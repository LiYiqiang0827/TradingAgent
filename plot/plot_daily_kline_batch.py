#!/usr/bin/env python3
"""批量绘制 TradingAgent 离线日 K 图。

输入支持：
1. policyStudy watchlist CSV；
2. JSON 字典列表；
3. Python 直接调用 ``plot_batch(list[dict])``。

每个字典可以是日期段模式：
``{"ts_code": "600519.SH", "start_date": "2026-03-05", "end_date": "2026-06-05"}``

也可以是单日或多交易日模式：
``{"ts_code": "000001.SZ", "trade_date": "2026-06-05"}``
``{"ts_code": "000001.SZ", "trade_dates": ["2026-06-05", "2026-07-10"]}``
两种字典可以出现在同一个列表中。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plot.plot_daily_kline import (  # noqa: E402
    DEFAULT_SAVE_DIR,
    build_save_path,
    load_daily_data,
    load_stock_name_map,
    normalize_ts_code,
    plot_daily_kline,
    resolve_window,
)


WATCHLIST_DIR = (
    PROJECT_ROOT
    / "policyStudy"
    / "policy"
    / "题材涨停研究"
    / "watchlist"
)
WATCHLIST_SAVE_DIR = WATCHLIST_DIR.parent / "savepic"


def discover_default_watchlist() -> Path:
    """优先使用 watchlist.csv，否则使用目录中名称排序最后的 watchlist CSV。"""

    conventional = WATCHLIST_DIR / "watchlist.csv"
    if conventional.is_file():
        return conventional
    candidates = sorted(WATCHLIST_DIR.glob("*watchlist*.csv"))
    if candidates:
        return candidates[-1]
    return conventional


DEFAULT_WATCHLIST = discover_default_watchlist()

FIELD_ALIASES = {
    "ts_code": ("ts_code", "tscode", "code", "symbol"),
    "start_date": ("start_date", "startdate"),
    "end_date": ("end_date", "enddate"),
    "trade_date": ("trade_date", "tradedate"),
    "trade_dates": ("trade_dates", "tradedates"),
    "name": ("name", "stock_name"),
}


@dataclass
class BatchSummary:
    total: int
    success: int = 0
    skipped: int = 0
    failed: int = 0
    outputs: list[Path] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


def _clean_value(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text if text else None


def _find_value(item: Mapping[str, Any], canonical: str) -> str | None:
    value = _find_raw_value(item, canonical)
    return _clean_value(value)


def _find_raw_value(item: Mapping[str, Any], canonical: str) -> Any:
    lowered = {str(key).strip().lower(): value for key, value in item.items()}
    for alias in FIELD_ALIASES[canonical]:
        if alias in lowered:
            return lowered[alias]
    return None


def _normalize_trade_dates(value: Any) -> list[str] | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for entry in values:
        result.extend(part.strip() for part in str(entry).split(",") if part.strip())
    return result or None


def normalize_batch_item(item: Mapping[str, Any], row_number: int | None = None) -> dict[str, Any]:
    """把别名字段统一成批量绘图所需的标准字典。"""

    where = f"第 {row_number} 项" if row_number is not None else "批量项"
    if not isinstance(item, Mapping):
        raise ValueError(f"{where}必须是字典，当前为 {type(item).__name__}")

    raw_code = _find_value(item, "ts_code")
    if raw_code is None:
        raise ValueError(f"{where}缺少 ts_code")
    ts_code = normalize_ts_code(raw_code)
    start_date = _find_value(item, "start_date")
    end_date = _find_value(item, "end_date")
    trade_date = _find_value(item, "trade_date")
    trade_dates = _normalize_trade_dates(_find_raw_value(item, "trade_dates"))

    # 复用单图脚本的完整日期模式校验。
    window = resolve_window(
        start_date=start_date,
        end_date=end_date,
        trade_date=trade_date,
        trade_dates=trade_dates,
        lookback_months=12,
        lookahead_months=1,
        ma_warmup_days=400,
        to_latest=False,
    )
    if trade_dates is not None:
        trade_dates = [value.strftime("%Y%m%d") for value in window.marked_dates]
    return {
        "ts_code": ts_code,
        "start_date": start_date,
        "end_date": end_date,
        "trade_date": trade_date,
        "trade_dates": trade_dates,
        "name": _find_value(item, "name"),
    }


def normalize_batch_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """校验、标准化并按代码与选择日期去重，保留首次出现顺序。"""

    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise ValueError("批量输入必须是字典列表")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, item in enumerate(items, start=1):
        row = normalize_batch_item(item, index)
        key = (
            row["ts_code"],
            row["start_date"],
            row["end_date"],
            row["trade_date"],
            tuple(row["trade_dates"] or []),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(row)
    if not normalized:
        raise ValueError("批量输入中没有可绘制项目")
    return normalized


def merge_watchlist_same_tscode(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把 watchlist 中同一 ts_code 的交易日合成一个单日/多日绘图项。"""

    grouped: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for index, raw_item in enumerate(items, start=1):
        row = normalize_batch_item(raw_item, index)
        dates: list[str] = []
        if row.get("trade_date"):
            dates.append(str(row["trade_date"]))
        if row.get("trade_dates"):
            dates.extend(str(value) for value in row["trade_dates"])
        if not dates:
            passthrough.append(row)
            continue

        code = str(row["ts_code"])
        if code not in grouped:
            grouped[code] = {"ts_code": code, "name": row.get("name"), "dates": set()}
        grouped[code]["dates"].update(dates)
        if not grouped[code].get("name") and row.get("name"):
            grouped[code]["name"] = row["name"]

    merged: list[dict[str, Any]] = []
    for group in grouped.values():
        dates = sorted(group.pop("dates"))
        if len(dates) == 1:
            group["trade_date"] = dates[0]
        else:
            group["trade_dates"] = dates
        merged.append(group)
    return merged + passthrough


def resolve_batch_save_dir(explicit: Path | None, *, watchlist_mode: bool) -> Path:
    if explicit is not None:
        return explicit
    return WATCHLIST_SAVE_DIR if watchlist_mode else DEFAULT_SAVE_DIR


def load_watchlist(path: Path) -> list[dict[str, str]]:
    """读取 watchlist CSV；每行可为 trade_date 或 start/end 日期模式。"""

    csv_path = path.expanduser().resolve()
    if not csv_path.is_file():
        raise ValueError(f"watchlist 文件不存在: {csv_path}")
    try:
        frame = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    except Exception as exc:
        raise ValueError(f"读取 watchlist 失败: {csv_path}: {exc}") from exc
    if frame.empty:
        raise ValueError(f"watchlist 没有数据: {csv_path}")
    return frame.to_dict("records")


def parse_items_json(text: str) -> list[dict[str, Any]]:
    """解析 JSON 字典列表，也接受 {"items": [...]} 包装。"""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误: {exc}") from exc
    if isinstance(payload, dict) and "items" in payload:
        payload = payload["items"]
    if not isinstance(payload, list):
        raise ValueError("JSON 顶层必须是字典列表，或包含 items 列表")
    return payload


def load_items_file(path: Path) -> list[dict[str, Any]]:
    json_path = path.expanduser().resolve()
    if not json_path.is_file():
        raise ValueError(f"JSON 文件不存在: {json_path}")
    try:
        return parse_items_json(json_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"读取 JSON 文件失败: {json_path}: {exc}") from exc


def plot_batch(
    items: Sequence[Mapping[str, Any]],
    *,
    save_dir: Path = DEFAULT_SAVE_DIR,
    lookback_months: int = 12,
    lookahead_months: int = 1,
    ma_warmup_days: int = 400,
    to_latest: bool = False,
    qfq: bool = True,
    dpi: int = 160,
    overwrite: bool = False,
    fail_fast: bool = False,
    progress: bool = True,
) -> BatchSummary:
    """逐项保存 K 线图；单项失败默认记录后继续。"""

    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise ValueError("批量输入必须是字典列表")
    if not items:
        raise ValueError("批量输入中没有可绘制项目")

    output_dir = save_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = BatchSummary(total=len(items))
    normalized: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[Any, ...]] = set()
    for original_index, raw_item in enumerate(items, start=1):
        try:
            row = normalize_batch_item(raw_item, original_index)
            key = (
                row["ts_code"],
                row["start_date"],
                row["end_date"],
                row["trade_date"],
                tuple(row["trade_dates"] or []),
            )
            if key in seen:
                summary.skipped += 1
                if progress:
                    print(f"[{original_index}/{summary.total}] 跳过（重复输入） {row['ts_code']}")
                continue
            seen.add(key)
            normalized.append((original_index, row))
        except Exception as exc:
            summary.failed += 1
            summary.errors.append(
                {
                    "index": original_index,
                    "ts_code": _find_value(raw_item, "ts_code") if isinstance(raw_item, Mapping) else None,
                    "error": str(exc),
                    "item": dict(raw_item) if isinstance(raw_item, Mapping) else raw_item,
                }
            )
            if progress:
                print(f"[{original_index}/{summary.total}] 输入失败: {exc}", file=sys.stderr)
            if fail_fast:
                raise

    missing_names = any(not item.get("name") for _, item in normalized)
    stock_names = load_stock_name_map() if missing_names else {}

    for index, item in normalized:
        code = str(item["ts_code"])
        try:
            window = resolve_window(
                start_date=item.get("start_date"),
                end_date=item.get("end_date"),
                trade_date=item.get("trade_date"),
                trade_dates=item.get("trade_dates"),
                lookback_months=lookback_months,
                lookahead_months=lookahead_months,
                ma_warmup_days=ma_warmup_days,
                to_latest=to_latest,
            )
            target = build_save_path(code, window, output_dir).resolve()
            if target.exists() and not overwrite:
                summary.skipped += 1
                summary.outputs.append(target)
                if progress:
                    print(f"[{index}/{summary.total}] 跳过（已存在） {target.name}")
                continue

            df = load_daily_data(code, window, qfq=qfq)
            saved = plot_daily_kline(
                df,
                ts_code=code,
                stock_name=item.get("name") or stock_names.get(code),
                window=window,
                qfq=qfq,
                save_path=target,
                show=False,
                dpi=dpi,
            )
            if saved is None:
                raise RuntimeError("绘图函数没有返回保存路径")
            summary.success += 1
            summary.outputs.append(saved)
            if progress:
                print(f"[{index}/{summary.total}] 完成 {saved.name}")
        except Exception as exc:
            summary.failed += 1
            summary.errors.append(
                {"index": index, "ts_code": code, "error": str(exc), "item": dict(item)}
            )
            if progress:
                print(f"[{index}/{summary.total}] 失败 {code}: {exc}", file=sys.stderr)
            if fail_fast:
                raise
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从 watchlist CSV 或 JSON 字典列表批量保存日 K 图。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--watchlist",
        nargs="?",
        const=DEFAULT_WATCHLIST,
        type=Path,
        help="watchlist CSV；不写路径时使用 policyStudy 默认文件",
    )
    source.add_argument("--items-json", help="JSON 字典列表字符串，支持日期段和单日混合")
    source.add_argument("--items-file", type=Path, help="保存了 JSON 字典列表的 UTF-8 文件")
    parser.add_argument(
        "--save-dir",
        type=Path,
        help=(
            "JPG 保存目录；watchlist 模式默认使用题材涨停研究/savepic，"
            "其他模式默认使用 plot/tmp_savepic"
        ),
    )
    merge_mode = parser.add_mutually_exclusive_group()
    merge_mode.add_argument(
        "--merge-same-tscode",
        dest="merge_same_tscode",
        action="store_true",
        default=True,
        help="watchlist 模式合并相同 ts_code 的多个 trade_date（默认开启）",
    )
    merge_mode.add_argument(
        "--no-merge-same-tscode",
        dest="merge_same_tscode",
        action="store_false",
        help="watchlist 模式每一行单独画图",
    )
    parser.add_argument("--limit", type=int, help="只绘制输入中的前 N 项，便于试跑")
    parser.add_argument("--lookback-months", type=int, default=12, help="选择起点前展示月数")
    parser.add_argument("--lookahead-months", type=int, default=1, help="选择终点后展示月数")
    parser.add_argument("--ma-warmup-days", type=int, default=400, help="MA250 预热自然日数")
    parser.add_argument("--to-latest", action="store_true", help="所有图片都展示到当前最新数据")
    parser.add_argument("--no-qfq", action="store_true", help="所有图片均使用不复权价格")
    parser.add_argument("--dpi", type=int, default=160, help="JPG 输出 DPI")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已经存在的同名图片")
    parser.add_argument("--fail-fast", action="store_true", help="遇到第一项错误立即停止")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.watchlist is not None:
            raw_items = load_watchlist(args.watchlist)
            raw_count = len(raw_items)
            if args.merge_same_tscode:
                raw_items = merge_watchlist_same_tscode(raw_items)
                print(
                    f"watchlist 合并相同 ts_code：{raw_count} 行 → {len(raw_items)} 张图"
                )
        elif args.items_file is not None:
            raw_items = load_items_file(args.items_file)
        else:
            raw_items = parse_items_json(args.items_json)

        if args.limit is not None:
            if args.limit <= 0:
                raise ValueError("--limit 必须大于 0")
            raw_items = raw_items[: args.limit]

        save_dir = resolve_batch_save_dir(
            args.save_dir,
            watchlist_mode=args.watchlist is not None,
        )
        print(f"保存目录：{save_dir.expanduser().resolve()}")
        summary = plot_batch(
            raw_items,
            save_dir=save_dir,
            lookback_months=args.lookback_months,
            lookahead_months=args.lookahead_months,
            ma_warmup_days=args.ma_warmup_days,
            to_latest=args.to_latest,
            qfq=not args.no_qfq,
            dpi=args.dpi,
            overwrite=args.overwrite,
            fail_fast=args.fail_fast,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    print(
        f"批量完成：输入 {summary.total}，成功 {summary.success}，"
        f"跳过 {summary.skipped}，失败 {summary.failed}"
    )
    if summary.errors:
        print("失败明细：", file=sys.stderr)
        for error in summary.errors[:20]:
            print(
                f"  #{error['index']} {error['ts_code']}: {error['error']}",
                file=sys.stderr,
            )
        if len(summary.errors) > 20:
            print(f"  其余 {len(summary.errors) - 20} 项未展开", file=sys.stderr)
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
