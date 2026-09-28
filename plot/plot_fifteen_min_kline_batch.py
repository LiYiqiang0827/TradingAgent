#!/usr/bin/env python3
"""批量绘制TradingAgent离线15分钟K线图。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plot.plot_daily_kline_batch import (  # noqa: E402
    BatchSummary,
    DEFAULT_WATCHLIST,
    WATCHLIST_SAVE_DIR as DAILY_WATCHLIST_SAVE_DIR,
    _find_value,
    load_items_file,
    load_watchlist,
    merge_watchlist_same_tscode,
    normalize_batch_item,
    parse_items_json,
)
from plot.plot_daily_kline import load_stock_name_map  # noqa: E402
from plot.plot_fifteen_min_kline import (  # noqa: E402
    DEFAULT_LOOKAHEAD_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_SAVE_DIR,
    build_save_path,
    load_fifteen_min_data,
    plot_fifteen_min_kline,
    resolve_window,
)


WATCHLIST_SAVE_DIR = DAILY_WATCHLIST_SAVE_DIR / "15min"


def resolve_batch_save_dir(explicit: Path | None, *, watchlist_mode: bool) -> Path:
    if explicit is not None:
        return explicit
    return WATCHLIST_SAVE_DIR if watchlist_mode else DEFAULT_SAVE_DIR


def plot_batch(
    items: Sequence[Mapping[str, Any]],
    *,
    save_dir: Path = DEFAULT_SAVE_DIR,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    ma_warmup_bars: int = 250,
    to_latest: bool = False,
    qfq: bool = True,
    dpi: int = 160,
    overwrite: bool = False,
    fail_fast: bool = False,
    progress: bool = True,
) -> BatchSummary:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise ValueError("批量输入必须是字典列表")
    if not items:
        raise ValueError("批量输入中没有可绘制项目")

    output_dir = save_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = BatchSummary(total=len(items))
    normalized: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, raw_item in enumerate(items, start=1):
        try:
            row = normalize_batch_item(raw_item, index)
            key = (
                row["ts_code"], row["start_date"], row["end_date"],
                row["trade_date"], tuple(row["trade_dates"] or []),
            )
            if key in seen:
                summary.skipped += 1
                if progress:
                    print(f"[{index}/{summary.total}] 跳过（重复输入） {row['ts_code']}")
                continue
            seen.add(key)
            normalized.append((index, row))
        except Exception as exc:
            summary.failed += 1
            summary.errors.append({
                "index": index,
                "ts_code": _find_value(raw_item, "ts_code") if isinstance(raw_item, Mapping) else None,
                "error": str(exc),
                "item": dict(raw_item) if isinstance(raw_item, Mapping) else raw_item,
            })
            if progress:
                print(f"[{index}/{summary.total}] 输入失败: {exc}", file=sys.stderr)
            if fail_fast:
                raise

    stock_names = load_stock_name_map() if any(not item.get("name") for _, item in normalized) else {}
    for index, item in normalized:
        code = str(item["ts_code"])
        try:
            window = resolve_window(
                start_date=item.get("start_date"), end_date=item.get("end_date"),
                trade_date=item.get("trade_date"), trade_dates=item.get("trade_dates"),
                lookback_days=lookback_days, lookahead_days=lookahead_days,
                ma_warmup_bars=ma_warmup_bars, to_latest=to_latest,
            )
            target = build_save_path(code, window, output_dir).resolve()
            if target.exists() and not overwrite:
                summary.skipped += 1
                summary.outputs.append(target)
                if progress:
                    print(f"[{index}/{summary.total}] 跳过（已存在） {target.name}")
                continue
            df = load_fifteen_min_data(code, window, qfq=qfq)
            saved = plot_fifteen_min_kline(
                df, ts_code=code,
                stock_name=item.get("name") or stock_names.get(code),
                window=window, qfq=qfq, save_path=target, show=False, dpi=dpi,
            )
            if saved is None:
                raise RuntimeError("绘图函数没有返回保存路径")
            summary.success += 1
            summary.outputs.append(saved)
            if progress:
                print(f"[{index}/{summary.total}] 完成 {saved.name}")
        except Exception as exc:
            summary.failed += 1
            summary.errors.append({
                "index": index, "ts_code": code, "error": str(exc), "item": dict(item),
            })
            if progress:
                print(f"[{index}/{summary.total}] 失败 {code}: {exc}", file=sys.stderr)
            if fail_fast:
                raise
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从watchlist CSV或JSON字典列表批量保存15分钟K线图。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--watchlist", nargs="?", const=DEFAULT_WATCHLIST, type=Path,
        help="watchlist CSV；不写路径时使用policyStudy默认文件",
    )
    source.add_argument("--items-json", help="JSON字典列表字符串")
    source.add_argument("--items-file", type=Path, help="UTF-8 JSON字典列表文件")
    parser.add_argument("--save-dir", type=Path)
    merge = parser.add_mutually_exclusive_group()
    merge.add_argument(
        "--merge-same-tscode", dest="merge_same_tscode", action="store_true",
        default=True, help="watchlist合并相同股票的多个交易日",
    )
    merge.add_argument(
        "--no-merge-same-tscode", dest="merge_same_tscode", action="store_false",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--lookahead-days", type=int, default=DEFAULT_LOOKAHEAD_DAYS)
    parser.add_argument("--ma-warmup-bars", type=int, default=250)
    parser.add_argument("--to-latest", action="store_true")
    parser.add_argument("--no-qfq", action="store_true")
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
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
                print(f"watchlist合并相同ts_code：{raw_count}行 → {len(raw_items)}张图")
        elif args.items_file is not None:
            raw_items = load_items_file(args.items_file)
        else:
            raw_items = parse_items_json(args.items_json)
        if args.limit is not None:
            if args.limit <= 0:
                raise ValueError("--limit必须大于0")
            raw_items = raw_items[:args.limit]
        save_dir = resolve_batch_save_dir(args.save_dir, watchlist_mode=args.watchlist is not None)
        print(f"保存目录：{save_dir.expanduser().resolve()}")
        summary = plot_batch(
            raw_items, save_dir=save_dir,
            lookback_days=args.lookback_days, lookahead_days=args.lookahead_days,
            ma_warmup_bars=args.ma_warmup_bars, to_latest=args.to_latest,
            qfq=not args.no_qfq, dpi=args.dpi, overwrite=args.overwrite,
            fail_fast=args.fail_fast,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    print(
        f"批量完成：输入{summary.total}，成功{summary.success}，"
        f"跳过{summary.skipped}，失败{summary.failed}"
    )
    if summary.errors:
        for error in summary.errors[:20]:
            print(f"  #{error['index']} {error['ts_code']}: {error['error']}", file=sys.stderr)
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
