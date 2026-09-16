"""
题材涨停研究 — 历史数据批量生成(v3,2026-09-14)

数据落地结构(单表 + ctrl 表,policyStudy v3):
  policy_minute.db:  tbl_minute       + tbl_minute_ctrl
  policy_ticks.db:   tbl_tick         + tbl_tick_ctrl
  policy_day.db:     tbl_day + tbl_day_nofuquan
                     tbl_day_ctrl + tbl_day_nofuquan_ctrl

用法:
  cd ~/TradingAgent/policyStudy/policy/题材涨停研究
  python3 data_gen.py watchlist_题材涨停研究_20260908_20260911
  python3 data_gen.py watchlist_题材涨停研究_20260908_20260911 --only minute
  python3 data_gen.py watchlist_题材涨停研究_20260908_20260911 --only ticks
  python3 data_gen.py watchlist_题材涨停研究_20260908_20260911 --only day
  python3 data_gen.py --help

流程:
  1. 从 <脚本目录>/../watchlist/<name>.csv 读 watchlist
  2. 提取 (ts_code, trade_date) 去重对
  3. ctrl 表过滤(只下载缺失的),--force 跳过过滤
  4a. tdx_client 拉 minute / ticks → upsert_rows 进单表
  4b. offline_db_client.get_day 拉日线 → upsert_day_pair 同时写 qfq + nofq
  5. upsert_rows 自动 mark_downloaded 进 ctrl 表
  6. 抽样验证
"""
import sys
import argparse
import time
from datetime import datetime
from pathlib import Path

ROOT = Path("/Users/nickzhang/TradingAgent")
sys.path.insert(0, str(ROOT / "coreClient"))
sys.path.insert(0, str(ROOT / "policyStudy" / "scripts"))
sys.path.insert(0, str(ROOT / "offlineDataManager" / "scripts"))

import pandas as pd
from tdx_client import TdxClient

from policy_db_client import (
    ensure_schema,
    filter_pending,
    mark_downloaded,
    upsert_rows,
    upsert_day_pair,
    count_rows,
)
from core.offline_db_client import get_day as offline_get_day


# ============================================================================
# 路径
# ============================================================================
SCRIPT_DIR = Path(__file__).parent.resolve()
WATCHLIST_DIR = SCRIPT_DIR.parent / "watchlist"
DATA_DIR = ROOT / "policyStudy" / "data"


# ============================================================================
# tdx_client 字段对齐(只保留数据库表里的列)
# ============================================================================
MINUTE_FIELDS = ["ts_code", "trade_date", "datetime", "time_idx", "price", "vol"]
TICKS_FIELDS = ["ts_code", "trade_date", "datetime", "time", "seqId", "price", "vol", "buyorsell"]
DAY_FIELDS = ["ts_code", "trade_date", "open", "high", "low", "close",
              "pre_close", "change", "pct_chg", "vol", "amount"]


def _shift_year(yyyymmdd: str, years: int) -> str:
    """YYYYMMDD 整体加减 N 年(natural year)

    例:'20260911' years=-1 → '20250911';years=+1 → '20270911'
    """
    y = int(yyyymmdd[:4]) + years
    return f"{y:04d}{yyyymmdd[4:]}"


def compute_day_window(df_watch: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """按 ts_code 分组,算出每只股票的日线下载窗口(min/max 各 ±1 年)

    Args:
        df_watch: watchlist DataFrame,必有 ts_code / trade_date (YYYYMMDD)

    Returns:
        {ts_code: (start_yyyymmdd, end_yyyymmdd), ...}
    """
    out: dict[str, tuple[str, str]] = {}
    grouped = df_watch.groupby("ts_code")["trade_date"]
    for ts_code, dates in grouped:
        min_d = dates.min()
        max_d = dates.max()
        out[ts_code] = (_shift_year(min_d, -1), _shift_year(max_d, +1))
    return out


# ============================================================================
# 1) 读 watchlist
# ============================================================================
def read_watchlist(watchlist_dir: Path, name: str) -> pd.DataFrame:
    """从 <watchlist_dir>/<name>.csv 读 watchlist"""
    csv_path = watchlist_dir / f"{name}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"watchlist 不存在: {csv_path}")

    df = pd.read_csv(csv_path, dtype={"trade_date": str, "ts_code": str})
    required = {"trade_date", "ts_code"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"watchlist 缺少必要列 {missing}: {csv_path}")

    df = df.drop_duplicates(subset=["trade_date", "ts_code"]).reset_index(drop=True)
    return df


# ============================================================================
# 2) 拉数据(tdx_client)
# ============================================================================
def fetch_one_minute(client: TdxClient, ts_code: str, date_int: int) -> list[dict]:
    try:
        return client.get_history_minute(ts_code, date_int)
    except Exception as e:
        print(f"      ⚠️ {ts_code} {date_int} minute 失败: {e}")
        return []


def fetch_one_ticks(client: TdxClient, ts_code: str, date_int: int) -> list[dict]:
    try:
        return client.get_history_ticks(ts_code, date_int)
    except Exception as e:
        print(f"      ⚠️ {ts_code} {date_int} ticks 失败: {e}")
        return []


# ============================================================================
# 主流程
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="题材涨停研究 — 历史数据批量生成(v3)")
    parser.add_argument("watchlist_name", nargs="?",
                        help="watchlist 名字(不带 .csv 后缀)")
    parser.add_argument("--only", choices=["minute", "ticks", "day"], default=None,
                        help="只跑一种数据,默认都跑")
    parser.add_argument("--watchlist-dir", default=str(WATCHLIST_DIR),
                        help=f"watchlist 目录(默认 {WATCHLIST_DIR})")
    parser.add_argument("--force", action="store_true",
                        help="强制覆盖(忽略 ctrl 表过滤,重新下载)")
    parser.add_argument("--rate", type=float, default=0.15,
                        help="每只股票间隔秒数(pytdx ~70 req/s,默认 0.15s)")
    args = parser.parse_args()

    if not args.watchlist_name:
        parser.error("watchlist_name 是必填")

    watchlist_dir = Path(args.watchlist_dir)

    print(f"=====[ data_gen v3 ]=====")
    print(f"watchlist_dir: {watchlist_dir}")
    print(f"only:          {args.only or 'minute+ticks+day'}")
    print(f"force:         {args.force}")
    print()

    # 0) 确保表 + ctrl 都建好
    print("[0/4] ensure_schema ...")
    kinds = ["minute", "ticks", "day"] if args.only is None else [args.only]
    if args.only == "day":
        kinds = ["day", "day_nofuquan"]
    for kind in kinds:
        ensure_schema(db_kind=kind, verbose=False)

    # 1) 读 watchlist
    df = read_watchlist(watchlist_dir, args.watchlist_name)
    pairs = list(zip(df["ts_code"].tolist(), df["trade_date"].tolist()))
    print(f"[1/4] watchlist: {len(pairs)} 个 (ts_code, trade_date) 对(去重后)")

    # 2) ctrl 过滤
    if args.force:
        pending_minute = pairs if args.only in (None, "minute") else []
        pending_ticks = pairs if args.only in (None, "ticks") else []
        pending_day = pairs if args.only in (None, "day") else []  # day 用一对过滤
        print(f"[2/4] --force 模式:跳过 ctrl 过滤,全量下载")
    else:
        pending_minute = filter_pending("minute", pairs) if args.only in (None, "minute") else []
        pending_ticks = filter_pending("ticks", pairs) if args.only in (None, "ticks") else []
        # day:两个 ctrl 表任一为空都视为 pending
        if args.only in (None, "day"):
            already_qfq = filter_pending("day", pairs)  # 这返回未下载的
            already_nofq = filter_pending("day_nofuquan", pairs)
            # 如果任一未下载,就算 pending
            pending_day = list(set(already_qfq) | set(already_nofq))
        else:
            pending_day = []
    print(
        f"[2/4] ctrl 过滤后: minute={len(pending_minute)} | ticks={len(pending_ticks)} | day={len(pending_day)}"
    )

    # 3) 拉 + 写
    client = TdxClient()
    minute_total = 0
    ticks_total = 0
    day_qfq_total = 0
    day_nofq_total = 0
    t0 = datetime.now()

    try:
        # ---- minute ----
        if pending_minute:
            print(f"\n[3a/4] 拉 minute ({len(pending_minute)} 对)...")
            for i, (ts_code, trade_date) in enumerate(pending_minute, 1):
                rows_m = fetch_one_minute(client, ts_code, int(trade_date))
                if not rows_m:
                    print(f"  [{i:3d}/{len(pending_minute)}] {trade_date} {ts_code} minute: ❌ 拉取为空")
                    continue
                # tdx_client 给的 trade_date 可能是 YYYYMMDD,转 dash
                for r in rows_m:
                    if "trade_date" in r:
                        td = str(r["trade_date"])
                        if len(td) == 8 and td.isdigit():
                            r["trade_date"] = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
                df_m = pd.DataFrame(rows_m)[MINUTE_FIELDS]
                inserted = upsert_rows("minute", df_m)
                minute_total += inserted if inserted and inserted > 0 else 0
                print(f"  [{i:3d}/{len(pending_minute)}] {trade_date} {ts_code} minute: {len(rows_m)} 根 (新插 {inserted})")
                if i < len(pending_minute):
                    time.sleep(args.rate)
            # 全量标记
            mark_downloaded("minute", pending_minute)

        # ---- ticks ----
        if pending_ticks:
            print(f"\n[3b/4] 拉 ticks ({len(pending_ticks)} 对)...")
            for i, (ts_code, trade_date) in enumerate(pending_ticks, 1):
                rows_t = fetch_one_ticks(client, ts_code, int(trade_date))
                if not rows_t:
                    print(f"  [{i:3d}/{len(pending_ticks)}] {trade_date} {ts_code} ticks: ❌ 拉取为空")
                    continue
                for r in rows_t:
                    if "trade_date" in r:
                        td = str(r["trade_date"])
                        if len(td) == 8 and td.isdigit():
                            r["trade_date"] = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
                df_t = pd.DataFrame(rows_t)[TICKS_FIELDS]
                inserted = upsert_rows("ticks", df_t)
                ticks_total += inserted if inserted and inserted > 0 else 0
                print(f"  [{i:3d}/{len(pending_ticks)}] {trade_date} {ts_code} ticks:  {len(rows_t)} 笔 (新插 {inserted})")
                if i < len(pending_ticks):
                    time.sleep(args.rate)
            mark_downloaded("ticks", pending_ticks)

        # ---- day(从 offline 库拉) ----
        if pending_day:
            print(f"\n[3c/4] 拉 day ({len(df['ts_code'].unique())} 只 × ±1 年窗口,pending {len(pending_day)} 对)...")
            day_window = compute_day_window(df)
            # 按 ts_code 去重,只看需要拉 day 的 ts_code
            pending_ts_codes = sorted(set(ts for ts, _ in pending_day))
            t_day = datetime.now()
            for i, ts_code in enumerate(pending_ts_codes, 1):
                sd, ed = day_window[ts_code]
                try:
                    df_qfq = offline_get_day(ts_codes=[ts_code], start_date=sd, end_date=ed, qfq=True)
                    df_nofq = offline_get_day(ts_codes=[ts_code], start_date=sd, end_date=ed, qfq=False)
                except Exception as e:
                    print(f"  [{i:3d}/{len(pending_ts_codes)}] {ts_code} day: ⚠️ 拉取异常: {e}")
                    continue
                if df_qfq is None or len(df_qfq) == 0:
                    print(f"  [{i:3d}/{len(pending_ts_codes)}] {ts_code} day: ❌ 拉取为空 ({sd}~{ed})")
                    continue
                # 列对齐
                df_qfq = df_qfq[DAY_FIELDS].copy()
                if df_nofq is not None:
                    df_nofq = df_nofq[DAY_FIELDS].copy()
                # offline 给 YYYYMMDD,policyStudy 内部用 YYYY-MM-DD
                df_qfq["trade_date"] = df_qfq["trade_date"].astype(str).map(
                    lambda d: f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
                )
                if df_nofq is not None:
                    df_nofq["trade_date"] = df_nofq["trade_date"].astype(str).map(
                        lambda d: f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
                    )
                qfq_ins, nofq_ins = upsert_day_pair(ts_code, df_qfq, df_nofq)
                day_qfq_total += qfq_ins if qfq_ins and qfq_ins > 0 else 0
                day_nofq_total += nofq_ins if nofq_ins and nofq_ins > 0 else 0
                print(
                    f"  [{i:3d}/{len(pending_ts_codes)}] {ts_code} day: "
                    f"{len(df_qfq)} 日 ({sd}~{ed}),新插 qfq={qfq_ins} | nofq={nofq_ins}"
                )
            day_elapsed = (datetime.now() - t_day).total_seconds()
            print(f"  → day 总耗时 {day_elapsed:.1f}s,新插 qfq={day_qfq_total} + nofq={day_nofq_total}")
    finally:
        try:
            client.api.disconnect()
        except Exception:
            pass

    elapsed = (datetime.now() - t0).total_seconds()
    print()
    print(f"[3/4] 完成,耗时 {elapsed:.1f}s")
    print(f"  minute: 新插 {minute_total} 行")
    print(f"  ticks:  新插 {ticks_total} 行")
    print(f"  day:    新插 qfq={day_qfq_total} + nofq={day_nofq_total} 行")

    # 4) 抽样验证
    print()
    print(f"[4/4] 抽样验证(前 3 对):")
    from policy_db_client import _ymd_compact_to_dash
    sample_pairs = pairs[:3]
    for ts_code, trade_date in sample_pairs:
        db_date = _ymd_compact_to_dash(trade_date)
        if args.only in (None, "minute"):
            from policy_db_client import PolicyDBClient
            client_v = PolicyDBClient()
            df_m = client_v.get_minute(ts_code, trade_date=db_date)
            cnt = len(df_m)
            if cnt > 0:
                mn = int(df_m["time_idx"].min())
                mx = int(df_m["time_idx"].max())
            else:
                mn = mx = None
            print(f"  {ts_code} {db_date} minute: {cnt} 行 (time_idx {mn}..{mx})")
        if args.only in (None, "ticks"):
            from policy_db_client import PolicyDBClient
            client_v = PolicyDBClient()
            df_t = client_v.get_ticks(ts_code, trade_date=db_date)
            cnt = len(df_t)
            if cnt > 0:
                mn = int(df_t["seqId"].min())
                mx = int(df_t["seqId"].max())
            else:
                mn = mx = None
            print(f"  {ts_code} {db_date} ticks:  {cnt} 行 (seqId {mn}..{mx})")
        if args.only in (None, "day"):
            from policy_db_client import PolicyDBClient
            client_v = PolicyDBClient()
            for qfq_label, qfq in (("qfq", True), ("nofq", False)):
                df_d = client_v.get_day(ts_code, qfq=qfq)
                cnt = len(df_d)
                if cnt > 0:
                    mn = df_d["trade_date"].min()
                    mx = df_d["trade_date"].max()
                else:
                    mn = mx = None
                print(f"  {ts_code} day({qfq_label}): {cnt} 行 ({mn}..{mx})")


if __name__ == "__main__":
    main()
