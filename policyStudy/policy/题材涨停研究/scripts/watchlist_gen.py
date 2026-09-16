"""
policyStudy/policy/题材涨停研究/watchlist_gen.py
==================================================

拉取指定日期范围 / 单日的涨停个股 watchlist。

数据源策略(2026-09-13 v2):
  - kpl_list 作为主源(来自 tushare,字段全)
  - kpl_limit_performance 作为增量补充(来自 kpl API,数据更及时)
  - 同 (trade_date, ts_code) 两表都有 → kpl_list 优先
  - 只在 kpl_limit_performance → 用它的 theme 字段映射成 lu_desc(列名以 kpl_list 为准)
  - lu_desc 列语义:涨停原因(题材)
  - status 列语义:连板标记 '首板'/'N连板'/'N天M板'(从 kpl_list)

输出 csv 列(6 列,以 kpl_list 列名为准):
  trade_date / ts_code / name / lu_time / lu_desc / status

用法:
  python watchlist_gen.py --start-date 20260901 --end-date 20260912
  python watchlist_gen.py --trade-date 20260912
  python watchlist_gen.py --start-date 20260901 --end-date 20260912 --output-dir /tmp/

设计原则:
  - 列名严格对齐 kpl_list(用户原话:列名以 kpl list 的为准)
  - 唯一差异:lu_desc 在 limit_performance 行的来源是它的 theme(主题一致,列名仍叫 lu_desc)
  - CSV 用 UTF-8 BOM(excel 友好),na_rep='' 缺失值留空
  - 主源优先 + 增量补充(去重逻辑稳定)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# ====== 路径配置:加 offlineDataManager/scripts 到 sys.path ======
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path("/Users/nickzhang/TradingAgent")  # scripts/ 移动后用绝对路径
OFFLINE_SCRIPTS = PROJECT_ROOT / "offlineDataManager" / "scripts"
if str(OFFLINE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(OFFLINE_SCRIPTS))

from core.offline_db_client import get_kpl_list, get_kpl_limit_performance  # noqa: E402


# ====== 默认输出目录(watchlist csv 在 scripts/ 的父级 watchlist/ 里) ======
DEFAULT_OUTPUT_DIR = SCRIPT_DIR.parent / "watchlist"

# ====== CSV 列定义(严格按 kpl_list 列名,顺序敏感) ======
COLUMNS_OUT = [
    "trade_date",
    "ts_code",
    "name",
    "lu_time",
    "lu_desc",
    "status",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="生成题材涨停研究的 watchlist CSV"
    )
    date_group = p.add_argument_group("日期(三选一)")
    date_group.add_argument("--start-date", help="起始日期 YYYYMMDD")
    date_group.add_argument("--end-date", help="结束日期 YYYYMMDD")
    date_group.add_argument(
        "--trade-date",
        help="单日查询 YYYYMMDD(优先级最高,设了就忽略 start/end_date)",
    )

    p.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"输出目录(默认 {DEFAULT_OUTPUT_DIR})",
    )
    p.add_argument(
        "--filename",
        help="自定义输出文件名,默认按日期范围生成",
    )
    p.add_argument(
        "--keep-csv-filename-chinese",
        action="store_true",
        help="保留中文文件名(默认文件名用 ASCII 避免编码问题)",
    )
    return p.parse_args()


def _validate_dates(args: argparse.Namespace) -> tuple[str, str]:
    """校验日期参数,返回 (start_date, end_date) 字符串 YYYYMMDD"""
    if args.trade_date:
        sd = ed = args.trade_date
    elif args.start_date and args.end_date:
        sd, ed = args.start_date, args.end_date
    elif args.start_date or args.end_date:
        raise SystemExit("❌ start_date 和 end_date 必须同时给(或用 --trade-date)")
    else:
        raise SystemExit("❌ 必须给日期参数: --trade-date 或 (--start-date + --end-date)")

    # 简单格式校验
    for d in (sd, ed):
        try:
            datetime.strptime(d, "%Y%m%d")
        except ValueError:
            raise SystemExit(f"❌ 日期格式错(要 YYYYMMDD): {d}")

    if sd > ed:
        raise SystemExit(f"❌ start_date({sd}) > end_date({ed})")
    return sd, ed


def _fetch_kpl_data(start_date: str, end_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """调 offlineDataManager 的两个方法,返回 (df_list, df_lp)"""
    print(f"  拉 get_kpl_list (主源)({start_date} ~ {end_date})...", flush=True)
    df_list = get_kpl_list(start_date=start_date, end_date=end_date)
    print(f"    → {len(df_list)} 行", flush=True)

    print(f"  拉 get_kpl_limit_performance (增量补充)({start_date} ~ {end_date})...", flush=True)
    df_lp = get_kpl_limit_performance(start_date=start_date, end_date=end_date)
    print(f"    → {len(df_lp)} 行", flush=True)

    return df_list, df_lp


def _merge_primary_with_increment(
    df_list: pd.DataFrame, df_lp: pd.DataFrame
) -> pd.DataFrame:
    """
    合并策略(2026-09-13 v2):
      1. 主源:kpl_list(有 lu_desc / status / lu_time)
      2. 增量:limit_performance(theme 字段映射到 lu_desc)
      3. 同 (trade_date, ts_code) 两表都有 → kpl_list 优先
      4. 只在 limit_performance → 把它的 theme 填到 lu_desc(列名以 kpl_list 为准)
    """
    # 1. 主源:用 kpl_list 的 6 列
    list_cols = ["trade_date", "ts_code", "name", "lu_time", "lu_desc", "status"]
    if len(df_list):
        df_primary = df_list[list_cols].copy()
    else:
        df_primary = pd.DataFrame(columns=list_cols)

    # 2. 增量:从 limit_performance 拿 (trade_date, ts_code, name, theme)
    #    theme 字段先暂存为 _theme 后面再重命名
    increment_cols = ["trade_date", "ts_code", "name", "theme"]
    if len(df_lp):
        df_increment = df_lp[increment_cols].copy()
        df_increment = df_increment.rename(columns={"theme": "_theme_from_lp"})
    else:
        df_increment = pd.DataFrame(columns=["trade_date", "ts_code", "name", "_theme_from_lp"])

    # 3. 主源 LEFT JOIN 增量(indicator 看哪些行只在主源/两表都有)
    merged = pd.merge(
        df_primary,
        df_increment,
        on=["trade_date", "ts_code"],
        how="left",
        suffixes=("_primary", "_increment"),
        indicator=True,
    )

    # 4. name 合并(以主源 name 为准,只在增量有时补)
    if "name_increment" in merged.columns:
        merged["name"] = merged["name_primary"].fillna(merged["name_increment"])
        merged = merged.drop(columns=["name_primary", "name_increment"])
    else:
        merged["name"] = merged["name_primary"]
        merged = merged.drop(columns=["name_primary"])

    # 5. _merge 语义(how='left'):
    #    - 'both':       两表都有(主源有 lu_desc,增量有 theme)
    #    - 'left_only':  只在主源有(增量没这行 → _theme_from_lp=NaN)
    #    - 'right_only': how='left' 下不会出现
    # 6. lu_desc 融合规则:
    #    - 主源 lu_desc 有值 → 保留主源
    #    - 主源 lu_desc 空 + 增量 theme 有值 → 用增量的 theme 补
    #    (2026-09-13 v2:*ST 股票 tushare 的 lu_desc 经常空,需要让 kpl_limit_performance 补)
    lu_desc_empty = merged["lu_desc"].isna()
    theme_available = merged["_theme_from_lp"].notna()
    should_use_increment_theme = lu_desc_empty & theme_available
    if should_use_increment_theme.any():
        merged.loc[should_use_increment_theme, "lu_desc"] = merged.loc[
            should_use_increment_theme, "_theme_from_lp"
        ].values

    # 7. status / lu_time 只在主源有(kpl_limit_performance 没这两个)
    #    增量补充的行 status / lu_time 留空(None → CSV 空字符串)

    # 8. 清理
    merged = merged.drop(columns=["_theme_from_lp", "_merge"])

    # 9. 排序:trade_date DESC, status 排序(连板多的优先,但 status 是文本,这里按 ts_code 兜底)
    merged = merged.sort_values(
        by=["trade_date", "ts_code"],
        ascending=[False, True],
    ).reset_index(drop=True)

    # 10. 补齐输出列
    for col in COLUMNS_OUT:
        if col not in merged.columns:
            merged[col] = None
    merged = merged[COLUMNS_OUT]

    return merged


def _save_csv(df: pd.DataFrame, output_path: Path) -> None:
    """保存为 CSV,UTF-8 BOM(excel 友好)"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",  # UTF-8 BOM,excel 中文不乱码
        na_rep="",  # NaN 留空
    )
    print(f"\n✅ 已保存: {output_path}", flush=True)
    print(f"   {len(df)} 行 × {len(df.columns)} 列", flush=True)


def main() -> None:
    args = _parse_args()
    start_date, end_date = _validate_dates(args)

    print(f"=== 题材涨停研究 watchlist 生成 ===")
    print(f"  日期范围: {start_date} ~ {end_date}", flush=True)
    if args.trade_date:
        print(f"  (单日查询模式: {args.trade_date})", flush=True)

    # 1. 拉数据
    df_list, df_lp = _fetch_kpl_data(start_date, end_date)

    if len(df_list) == 0 and len(df_lp) == 0:
        print(f"\n⚠️  {start_date} ~ {end_date} 无任何涨停数据,跳过保存", flush=True)
        return

    # 2. 合并(主源 + 增量)
    print("\n  合并(主源 + 增量)...", flush=True)
    df_merged = _merge_primary_with_increment(df_list, df_lp)
    print(f"    → 合并后: {len(df_merged)} 行", flush=True)

    # 3. 数据覆盖统计
    from_primary = df_merged["lu_desc"].notna().sum()
    print(f"    → 来自主源 kpl_list(有 lu_desc): {from_primary} 行", flush=True)
    only_inc = len(df_merged) - from_primary
    if only_inc:
        print(
            f"    → 来自增量 kpl_limit_performance(theme → lu_desc): {only_inc} 行",
            flush=True,
        )

    # 4. 保存 CSV
    output_dir = Path(args.output_dir)
    if args.filename:
        filename = args.filename
    else:
        if args.keep_csv_filename_chinese:
            filename = f"watchlist_题材涨停研究_{start_date}_{end_date}.csv"
        else:
            filename = f"watchlist_tczt_{start_date}_{end_date}.csv"
    output_path = output_dir / filename
    _save_csv(df_merged, output_path)

    # 5. 简单预览
    print(f"\n=== 前 10 行预览 ===", flush=True)
    with pd.option_context("display.max_columns", None, "display.width", 200, "display.max_colwidth", 30):
        print(df_merged.head(10).to_string(), flush=True)


if __name__ == "__main__":
    main()