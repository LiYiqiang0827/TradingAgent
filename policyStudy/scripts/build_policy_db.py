"""
build_policy_db — 把 ~/TradingAgent/data/{ticks,minute}/ 的 parquet 文件
                   导入到 policyStudy 单表结构 db(v3,2026-09-14)

2026-09-14 v3 改造:从 per-ticker 表 → 单表 + ctrl 表
  - policy_minute.db:  tbl_minute       (PK: ts_code, trade_date, time_idx)
                      tbl_minute_ctrl  (PK: ts_code, trade_date)
  - policy_ticks.db:   tbl_tick         (PK: ts_code, trade_date, seqId)
                      tbl_tick_ctrl    (PK: ts_code, trade_date)

源数据: ~/TradingAgent/data/{ticks,minute}/<tscode>/*.parquet
字段来源:
  parquet 已有:ts_code / date / time_idx / time / price / vol / buyorsell
  parquet 没有但 db 要存的:
    - trade_date: 从 date 整数 YYYYMMDD 转为 "YYYY-MM-DD"
    - datetime:   从 time_idx 或 time 推算
       minute: idx 0-119=09:31-11:30, 120-239=13:01-15:00
       ticks:  time "HH:MM" + trade_date 拼成 "YYYY-MM-DD HH:MM:00"
    - created_at: 写入时间戳

用法:
    python3 build_policy_db.py ticks
    python3 build_policy_db.py minute
    python3 build_policy_db.py all
"""
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
POLICY_STUDY_DIR = HERE.parent
TA_ROOT = POLICY_STUDY_DIR.parent
SRC_ROOT = TA_ROOT / "data"
DATA_DIR = POLICY_STUDY_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 让 policy_db_client 能 import
sys.path.insert(0, str(HERE))


# ============================================================================
# 时间推算(对齐 tdx_client._minute_index_to_datetime / _ticks_time_to_datetime)
# ============================================================================
def _date_int_to_str(date_int) -> str:
    """YYYYMMDD int/str → 'YYYY-MM-DD'"""
    s = str(int(date_int))
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _minute_index_to_datetime(time_idx: int, date_int) -> str:
    """minute idx → 'YYYY-MM-DD HH:MM:SS'

    索引规则(2026-09-12 用户确认):
        0-119   = 09:31-11:30(上午 120 根)
        120-239 = 13:01-15:00(下午 120 根)
    """
    date_str = _date_int_to_str(date_int)
    if time_idx < 120:
        minutes = 9 * 60 + 31 + time_idx
    else:
        minutes = 13 * 60 + 1 + (time_idx - 120)
    hour, minute = divmod(minutes, 60)
    return f"{date_str} {hour:02d}:{minute:02d}:00"


def _ticks_time_to_datetime(time_str: str, date_int) -> str:
    """ticks time "HH:MM" → 'YYYY-MM-DD HH:MM:00'

    pytdx tick time 只到分钟,秒钟补 00。
    """
    date_str = _date_int_to_str(date_int)
    h, m = time_str.split(":")
    return f"{date_str} {int(h):02d}:{int(m):02d}:00"


# ============================================================================
# 单股票导入 → 转成 DataFrame(交给 policy_db_client.upsert_rows 写)
# ============================================================================
def load_one_ticker_to_df(ts_code: str, src_dir: Path, kind: str) -> pd.DataFrame:
    """读单只股票的所有 parquet 文件,合并成一张符合 schema 的 DataFrame

    Returns:
        kind='minute' → 列:ts_code, trade_date, datetime, time_idx, price, vol
        kind='ticks'  → 列:ts_code, trade_date, datetime, time, seqId, price, vol, buyorsell
    """
    is_ticks = (kind == "ticks")

    files = sorted(src_dir.glob("*.parquet"))
    if not files:
        return pd.DataFrame()

    all_dfs = []
    for f in files:
        try:
            df_one = pd.read_parquet(f)
        except Exception as e:
            print(f"  [WARN] 读 {f.name} 失败: {e}")
            continue
        if df_one.empty:
            continue
        df_one["ts_code"] = ts_code
        all_dfs.append(df_one)

    if not all_dfs:
        return pd.DataFrame()

    df = pd.concat(all_dfs, ignore_index=True)

    if is_ticks:
        # ====== ticks:加 trade_date + datetime + seqId(0-based,按时间正序) ======
        rows = []
        seqId_counter: dict = {}
        # 按 (date, time) 升序排
        df = df.sort_values(["date", "time"], kind="stable")

        for date_val, group in df.groupby("date", sort=False):
            group = group.sort_values("time", kind="stable")
            trade_date = _date_int_to_str(int(date_val))
            start = seqId_counter.get(int(date_val), -1)
            for offset, r in enumerate(group.itertuples(index=False)):
                time_str = str(r.time)
                datetime_str = _ticks_time_to_datetime(time_str, int(date_val))
                rows.append({
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "datetime": datetime_str,
                    "time": time_str,
                    "seqId": start + 1 + offset,
                    "price": float(r.price),
                    "vol": int(r.vol),
                    "buyorsell": int(r.buyorsell) if pd.notna(r.buyorsell) else None,
                })
            seqId_counter[int(date_val)] = start + len(group)

        return pd.DataFrame(rows)
    else:
        # ====== minute:加 trade_date + datetime ======
        rows = []
        for r in df.itertuples(index=False):
            date_val = int(r.date)
            time_idx = int(r.time_idx)
            trade_date = _date_int_to_str(date_val)
            datetime_str = _minute_index_to_datetime(time_idx, date_val)
            rows.append({
                "ts_code": ts_code,
                "trade_date": trade_date,
                "datetime": datetime_str,
                "time_idx": time_idx,
                "price": float(r.price),
                "vol": int(r.vol),
            })
        return pd.DataFrame(rows)


# ============================================================================
# 批量导入
# ============================================================================
def import_all(kind: str):
    """kind: 'ticks' / 'minute' / 'all'"""
    if kind == "ticks":
        kinds = ["ticks"]
    elif kind == "minute":
        kinds = ["minute"]
    elif kind == "all":
        kinds = ["ticks", "minute"]
    else:
        print(f"[ERR] unknown kind: {kind}")
        return

    # 延迟导入 policy_db_client(确保 sys.path 已设好)
    from policy_db_client import upsert_rows, ensure_schema

    for k in kinds:
        db_kind = k  # 'ticks' or 'minute'
        src_root = SRC_ROOT / k

        print(f"\n=== 导入 {k} ===")
        print(f"  db: ~/TradingAgent/policyStudy/data/policy_{k}.db")
        print(f"  src: {src_root}")

        if not src_root.exists():
            print(f"  [WARN] 源目录不存在,跳过")
            continue

        # 先建表
        ensure_schema(db_kind=db_kind, verbose=True)

        # 找所有 ts_code 目录
        ts_codes = sorted([d.name for d in src_root.iterdir() if d.is_dir()])
        print(f"  ts_codes: {len(ts_codes)}")

        t0 = time.time()
        total_inserted = 0
        total_files = 0

        for i, ts_code in enumerate(ts_codes, 1):
            src_dir = src_root / ts_code
            df = load_one_ticker_to_df(ts_code, src_dir, k)

            if df.empty:
                file_count = len(list(src_dir.glob("*.parquet")))
                total_files += file_count
                continue

            file_count = len(list(src_dir.glob("*.parquet")))
            total_files += file_count

            try:
                inserted = upsert_rows(db_kind, df)
                total_inserted += inserted if inserted >= 0 else 0
            except Exception as e:
                print(f"  [ERR] {ts_code}: {e}")
                continue

            if i % 100 == 0 or i == len(ts_codes):
                elapsed = time.time() - t0
                speed = i / elapsed if elapsed > 0 else 0
                eta = (len(ts_codes) - i) / speed if speed > 0 else 0
                print(
                    f"  [{i}/{len(ts_codes)}] {ts_code}: {file_count} 文件 / "
                    f"{len(df)} 行  速度 {speed:.1f} ts/s / "
                    f"已用 {elapsed:.0f}s / 预计剩余 {eta:.0f}s",
                    flush=True,
                )

        print(
            f"\n=== {k} 完成:{(time.time()-t0)/60:.1f} 分钟,"
            f"{total_inserted} 新行 / {total_files} 文件 ===\n"
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 build_policy_db.py [ticks|minute|all]")
        sys.exit(1)
    import_all(sys.argv[1])
