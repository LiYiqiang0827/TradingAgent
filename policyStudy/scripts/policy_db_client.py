"""
policy_db_client — policyStudy 数据库客户端(v3,2026-09-14)

路径:~/TradingAgent/policyStudy/scripts/policy_db_client.py

数据库(~/TradingAgent/policyStudy/data/):
  - policy_minute.db
      tbl_minute       (PK: ts_code, trade_date, time_idx)
      tbl_minute_ctrl  (PK: ts_code, trade_date)
  - policy_ticks.db
      tbl_tick         (PK: ts_code, trade_date, seqId)
      tbl_tick_ctrl    (PK: ts_code, trade_date)
  - policy_day.db
      tbl_day              (PK: ts_code, trade_date) 前复权
      tbl_day_nofuquan     (PK: ts_code, trade_date) 不复权
      tbl_day_ctrl         (PK: ts_code, trade_date) qfq 是否已落盘
      tbl_day_nofuquan_ctrl(PK: ts_code, trade_date) nofq 是否已落盘

设计目的(2026-09-14 用户原话):
  - 三个 db 都改成"单表 + ctrl 表"结构,横向切片(同一天所有股票)
    直接 `WHERE trade_date = ?` 即可,不需要 union 一堆 ticker_xxx 表
  - ctrl 表(ts_code, trade_date)快速判断"某只股票某一天有没有数据"
  - 各 kind 的 ctrl 表 INSERT OR IGNORE,幂等

交易日历源:offlineDataManager.scripts.core.offline_db_client.get_tradecal
  (tbl_cn_tradecal / cal_date YYYYMMDD / is_open=1 是交易日)

forward / backward 语义(按用户原话):
  forward=N   → 往前(历史方向)找 N 个交易日
  backward=N  → 往后(未来方向)找 N 个交易日
  默认都是 0(就是 trade_date 当天)

使用示例:
  from policy_db_client import PolicyDBClient
  client = PolicyDBClient()

  # 单只单日
  df = client.get_minute('000006.SZ', trade_date='20260911')

  # 多只 + 日期范围
  df = client.get_minute(['000006.SZ', '000636.SZ'],
                         start_date='20260908', end_date='20260911')

  # 单只单日 + 前后扩展
  df = client.get_minute('000006.SZ',
                         trade_date='20260911', forward=2, backward=1)

  # day(qfq / 不复权)
  df = client.get_day('000636.SZ', trade_date='20260911')
  df = client.get_day('000636.SZ', trade_date='20260911', qfq=False)

  # 横向切片:9/11 涨停所有股票的分钟 K
  df = client.get_minute(trade_date='20260911')   # 不传 ts_code = 所有股票

  # 快速判断某天某只是否有数据
  ok = client.has_data('minute', '000006.SZ', '20260911')
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple, Union

import pandas as pd


# ============================================================================
# 路径配置
# ============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
POLICY_STUDY_DIR = SCRIPT_DIR.parent
DATA_DIR = POLICY_STUDY_DIR / "data"

MINUTE_DB = DATA_DIR / "policy_minute.db"
TICKS_DB = DATA_DIR / "policy_ticks.db"
DAY_DB = DATA_DIR / "policy_day.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# 每个 kind 的 schema 元数据(字典驱动)
# ============================================================================
KIND_SCHEMAS = {
    "minute": {
        "db_path": MINUTE_DB,
        "data_table": "tbl_minute",
        "ctrl_table": "tbl_minute_ctrl",
        "data_columns": ["ts_code", "trade_date", "datetime", "time_idx", "price", "vol", "created_at"],
        "pk_columns": ["ts_code", "trade_date", "time_idx"],
        # 数据列(不带 created_at),用于 INSERT
        "insert_columns": ["ts_code", "trade_date", "datetime", "time_idx", "price", "vol"],
    },
    "ticks": {
        "db_path": TICKS_DB,
        "data_table": "tbl_tick",
        "ctrl_table": "tbl_tick_ctrl",
        "data_columns": ["ts_code", "trade_date", "datetime", "time", "seqId", "price", "vol", "buyorsell", "created_at"],
        "pk_columns": ["ts_code", "trade_date", "seqId"],
        "insert_columns": ["ts_code", "trade_date", "datetime", "time", "seqId", "price", "vol", "buyorsell"],
    },
    "day": {
        "db_path": DAY_DB,
        "data_table": "tbl_day",
        "ctrl_table": "tbl_day_ctrl",
        "data_columns": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount", "created_at"],
        "pk_columns": ["ts_code", "trade_date"],
        "insert_columns": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"],
    },
    "day_nofuquan": {
        "db_path": DAY_DB,
        "data_table": "tbl_day_nofuquan",
        "ctrl_table": "tbl_day_nofuquan_ctrl",
        "data_columns": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount", "created_at"],
        "pk_columns": ["ts_code", "trade_date"],
        "insert_columns": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"],
    },
}

# 兼容: db_kind 'ticks' ↔ 'ticks'(用于 PolicyDBClient 接口)
DB_KIND_ALIAS = {
    "minute": "minute",
    "ticks": "ticks",
    "tick": "ticks",
    "day": "day",
    "day_qfq": "day",
    "day_nofuquan": "day_nofuquan",
    "day_nofq": "day_nofuquan",
}


def _resolve_kind(db_kind: str) -> str:
    """db_kind 别名 → 标准名"""
    if db_kind in KIND_SCHEMAS:
        return db_kind
    if db_kind in DB_KIND_ALIAS:
        return DB_KIND_ALIAS[db_kind]
    raise ValueError(
        f"未知 db_kind {db_kind!r},可选: {list(KIND_SCHEMAS.keys())}"
    )


# ============================================================================
# 工具:日期格式
# ============================================================================
def _ymd_compact_to_dash(d: str) -> str:
    """'20260911' → '2026-09-11';已是 dash 格式则原样返回"""
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def _ymd_dash_to_compact(d: str) -> str:
    """'2026-09-11' → '20260911';已是 compact 格式则原样返回"""
    if len(d) == 10 and d[4] == "-":
        return d.replace("-", "")
    return d


def _norm_ts_codes(ts_codes: Optional[Union[str, Iterable[str]]]) -> Optional[List[str]]:
    """统一 ts_codes 成 list[str] 或 None(代表'全部')"""
    if ts_codes is None:
        return None
    if isinstance(ts_codes, str):
        return [ts_codes]
    return list(ts_codes)


# ============================================================================
# offline_db_client 路径(交易日历源)
# ============================================================================
TRADECAL_CLIENT_PATH = Path("/Users/nickzhang/TradingAgent/offlineDataManager/scripts")
if str(TRADECAL_CLIENT_PATH) not in sys.path:
    sys.path.insert(0, str(TRADECAL_CLIENT_PATH))


def _get_tradecal_client():
    """延迟导入 offline_db_client"""
    from core.offline_db_client import get_tradecal
    return get_tradecal


# ============================================================================
# 交易日历
# ============================================================================
def get_trading_calendar(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    market: str = "SSE",
) -> List[str]:
    """从 tradecal 读交易日历(YYYYMMDD)

    Args:
        start_date / end_date: 可选过滤,接受 YYYYMMDD 或 YYYY-MM-DD
        market: SSE(沪市,默认) / SZSE(深市) / None(全部)

    Returns:
        升序的交易日 YYYYMMDD 字符串列表
    """
    get_tradecal = _get_tradecal_client()
    sd = _ymd_dash_to_compact(start_date) if start_date else None
    ed = _ymd_dash_to_compact(end_date) if end_date else None
    df = get_tradecal(start_date=sd, end_date=ed, market=market, is_open=True)
    if df is None or len(df) == 0:
        return []
    return sorted(df["cal_date"].astype(str).tolist())


def expand_trade_dates(
    trade_date: str,
    forward: int = 0,
    backward: int = 0,
    market: str = "SSE",
) -> List[str]:
    """围绕某天向**前**(forward) / 向**后**(backward)扩展 N 个交易日

    语义(按用户原话):
      forward=N   → 往前(历史方向)N 个交易日
      backward=N  → 往后(未来方向)N 个交易日
      默认都是 0

    Args:
        trade_date: 中心日期,接受 YYYYMMDD 或 YYYY-MM-DD
        forward: 往前(历史方向)N 个交易日
        backward: 往后(未来方向)N 个交易日
        market: SSE(沪市,默认) / SZSE / None

    Returns:
        升序的 YYYYMMDD 字符串列表,包含 trade_date 本身

    Examples:
        trade_date='20260911', forward=2, backward=1
        → ['20260909', '20260910', '20260911', '20260914']  (9/12, 9/13 周末)
    """
    if forward < 0 or backward < 0:
        raise ValueError(f"forward/backward 必须 >= 0,当前 forward={forward}, backward={backward}")

    td = _ymd_dash_to_compact(trade_date)
    get_tradecal = _get_tradecal_client()

    # 1) 往前 N 天(包括 trade_date):自己 SQL 倒序取 N+1 天升序
    if forward > 0:
        from core.offline_db_client import get_conn
        conn = get_conn("basic")
        try:
            market_filter = "AND exchange = ?" if market is not None else ""
            params = [td]
            if market is not None:
                params.append(market)
            sql = (
                f"SELECT cal_date FROM tbl_cn_tradecal "
                f"WHERE is_open=1 AND cal_date <= ? {market_filter} "
                f"ORDER BY cal_date DESC LIMIT ?"
            )
            params.append(forward + 1)
            cur = conn.execute(sql, params)
            forward_dates = sorted([r[0] for r in cur.fetchall()])
        finally:
            conn.close()
    else:
        forward_dates = [td]

    # 2) 往后 N 天:用 get_tradecal offset 模式(包含 trade_date)
    if backward > 0:
        df = get_tradecal(trade_date=td, day_num=backward + 1, market=market, is_open=True)
        if df is None or len(df) == 0:
            backward_dates = [td]
        else:
            backward_dates = sorted(df["cal_date"].astype(str).tolist())
    else:
        backward_dates = [td]

    # 合并
    if forward > 0:
        result = forward_dates + [d for d in backward_dates if d > forward_dates[-1]]
    else:
        result = backward_dates

    return result


def _resolve_dates(
    trade_date: Optional[str],
    forward: int,
    backward: int,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Optional[List[str]]:
    """把 get_*(...) 的日期参数解析为 dash 格式日期列表

    两套模式(互斥):
      A) trade_date(+forward/backward):单日 + 前后扩展
      B) start_date..end_date:区间

    Returns:
        None:无任何日期参数(由调用方决定要不要全表扫描)
        []:参数冲突或解析失败
        [str]:dash 格式日期列表
    """
    if trade_date is not None:
        if start_date is not None or end_date is not None:
            raise ValueError("trade_date 模式与 start_date/end_date 互斥")
        compact = expand_trade_dates(
            trade_date, forward=forward, backward=backward, market="SSE"
        )
        return [_ymd_compact_to_dash(d) for d in compact]

    if start_date is not None or end_date is not None:
        compact = get_trading_calendar(start_date=start_date, end_date=end_date, market="SSE")
        return [_ymd_compact_to_dash(d) for d in compact]

    return None


# ============================================================================
# DB 连接 + Schema 建表
# ============================================================================
def _conn(db_kind: str) -> sqlite3.Connection:
    kind = _resolve_kind(db_kind)
    db_path = KIND_SCHEMAS[kind]["db_path"]
    return sqlite3.connect(str(db_path))


TEXT_COLS = ("ts_code", "trade_date", "datetime", "time")
INT_COLS = ("time_idx", "seqId", "vol", "buyorsell")  # minute/ticks 的 vol 是 INTEGER
REAL_COLS = ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "amount")
# day 的 vol 是 REAL(tushare 给的是 float,单位是"手")— 这里按 tushare 原始类型


def _create_table_sql(kind: str) -> str:
    """生成 CREATE TABLE SQL(含索引,脚本,可用 executescript)"""
    schema = KIND_SCHEMAS[kind]
    data_table = schema["data_table"]
    data_cols = schema["data_columns"]

    cols_def = []
    for col in data_cols:
        if col in TEXT_COLS:
            cols_def.append(f"{col} TEXT NOT NULL")
        elif col == "created_at":
            cols_def.append(f"{col} TEXT NOT NULL")
        elif col in INT_COLS:
            cols_def.append(f"{col} INTEGER")
        elif col in REAL_COLS:
            cols_def.append(f"{col} REAL")
        else:
            # 兜底
            cols_def.append(f"{col} REAL")

    # day 的 vol 用 REAL(覆盖 INT 默认)
    if kind in ("day", "day_nofuquan"):
        cols_def = [c.replace("INTEGER", "REAL") if "vol INTEGER" in c else c for c in cols_def]

    pk_cols = schema["pk_columns"]
    pk_str = ", ".join(pk_cols)
    cols_str = ",\n    ".join(cols_def)

    return (
        f"CREATE TABLE IF NOT EXISTS {data_table} (\n"
        f"    {cols_str},\n"
        f"    PRIMARY KEY ({pk_str})\n"
        f");\n"
        f"CREATE INDEX IF NOT EXISTS idx_{data_table}_date ON {data_table}(trade_date);\n"
        f"CREATE INDEX IF NOT EXISTS idx_{data_table}_code ON {data_table}(ts_code);\n"
    )


def _create_ctrl_sql(kind: str) -> str:
    schema = KIND_SCHEMAS[kind]
    ctrl_table = schema["ctrl_table"]
    return (
        f"CREATE TABLE IF NOT EXISTS {ctrl_table} (\n"
        f"    ts_code TEXT NOT NULL,\n"
        f"    trade_date TEXT NOT NULL,\n"
        f"    PRIMARY KEY (ts_code, trade_date)\n"
        f");\n"
    )


def ensure_schema(db_kind: Optional[str] = None, verbose: bool = False) -> None:
    """建数据表 + ctrl 表

    db_kind: None=全部,或 'minute'/'ticks'/'day'/'day_nofuquan'
    """
    if db_kind is None:
        kinds = list(KIND_SCHEMAS.keys())
    else:
        kinds = [_resolve_kind(db_kind)]

    # 按 db_path 去重(同 db 多 kind 共享连接)
    for kind in kinds:
        schema = KIND_SCHEMAS[kind]
        db_path = schema["db_path"]
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(_create_table_sql(kind))
            conn.executescript(_create_ctrl_sql(kind))
            conn.commit()
            if verbose:
                cur = conn.execute(f"SELECT COUNT(*) FROM {schema['data_table']}")
                d_rows = cur.fetchone()[0]
                cur = conn.execute(f"SELECT COUNT(*) FROM {schema['ctrl_table']}")
                c_rows = cur.fetchone()[0]
                print(f"  [{kind}] {db_path.name}: {schema['data_table']}={d_rows} 行 / {schema['ctrl_table']}={c_rows} 行")
        finally:
            conn.close()


# ============================================================================
# ctrl CRUD
# ============================================================================
def get_downloaded_pairs(db_kind: str) -> Set[Tuple[str, str]]:
    """读 ctrl 表,返回已下载 (ts_code, trade_date) 的集合(dash 格式)"""
    kind = _resolve_kind(db_kind)
    ctrl_table = KIND_SCHEMAS[kind]["ctrl_table"]
    conn = sqlite3.connect(str(KIND_SCHEMAS[kind]["db_path"]))
    try:
        cur = conn.execute(f"SELECT ts_code, trade_date FROM {ctrl_table}")
        return {(row[0], row[1]) for row in cur.fetchall()}
    finally:
        conn.close()


def mark_downloaded(db_kind: str, pairs: Iterable[Tuple[str, str]]) -> int:
    """把 (ts_code, trade_date) 对 INSERT OR IGNORE 到 ctrl 表

    trade_date 接受紧凑或 dash,内部统一转 dash 后写入
    """
    kind = _resolve_kind(db_kind)
    ctrl_table = KIND_SCHEMAS[kind]["ctrl_table"]
    conn = sqlite3.connect(str(KIND_SCHEMAS[kind]["db_path"]))
    try:
        sql = f"INSERT OR IGNORE INTO {ctrl_table} (ts_code, trade_date) VALUES (?, ?)"
        inserted = 0
        for ts_code, trade_date in pairs:
            td = _ymd_compact_to_dash(trade_date)
            cur = conn.execute(sql, (ts_code, td))
            inserted += cur.rowcount
        conn.commit()
        return inserted
    finally:
        conn.close()


def filter_pending(db_kind: str, pairs: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """过滤掉已下载的对,只返回未下载的"""
    downloaded = get_downloaded_pairs(db_kind)
    pending = []
    for ts_code, trade_date in pairs:
        td = _ymd_compact_to_dash(trade_date)
        if (ts_code, td) not in downloaded:
            pending.append((ts_code, trade_date))
    return pending


def has_data(db_kind: str, ts_code: str, trade_date: str) -> bool:
    """快速判断某只股票某一天是否有数据(查 ctrl 表)"""
    kind = _resolve_kind(db_kind)
    ctrl_table = KIND_SCHEMAS[kind]["ctrl_table"]
    conn = sqlite3.connect(str(KIND_SCHEMAS[kind]["db_path"]))
    try:
        td = _ymd_compact_to_dash(trade_date)
        cur = conn.execute(
            f"SELECT 1 FROM {ctrl_table} WHERE ts_code=? AND trade_date=? LIMIT 1",
            (ts_code, td),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


# ============================================================================
# 数据写入(INSERT OR IGNORE)
# ============================================================================
def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def upsert_rows(db_kind: str, df: pd.DataFrame) -> int:
    """把 DataFrame 的行 INSERT OR IGNORE 进对应 db_kind 的数据表

    df 必须包含 KIND_SCHEMAS[kind]['insert_columns'] 这些列
    自动补 created_at

    Returns:
        新插入的行数(rowcount,sqlite 报告)
    """
    kind = _resolve_kind(db_kind)
    schema = KIND_SCHEMAS[kind]
    data_table = schema["data_table"]
    ctrl_table = schema["ctrl_table"]
    insert_cols = schema["insert_columns"]

    if df is None or df.empty:
        return 0

    # 校验列
    missing = [c for c in insert_cols if c not in df.columns]
    if missing:
        raise ValueError(f"df 缺少列 {missing},需要 {insert_cols}")

    # trade_date 统一成 dash
    if "trade_date" in df.columns:
        df = df.copy()
        df["trade_date"] = df["trade_date"].astype(str).map(_ymd_compact_to_dash)

    # 补 created_at(用于新行,但 INSERT OR IGNORE 不会更新已存在的)
    df = df.copy()
    df["created_at"] = _now_iso()

    conn = sqlite3.connect(str(schema["db_path"]))
    try:
        # 数据表 INSERT
        placeholders = ",".join(["?"] * (len(insert_cols) + 1))  # +1 for created_at
        all_cols = insert_cols + ["created_at"]
        col_str = ", ".join(all_cols)
        sql_data = f"INSERT OR IGNORE INTO {data_table} ({col_str}) VALUES ({placeholders})"
        rows = [tuple(r) for r in df[all_cols].itertuples(index=False, name=None)]
        cur = conn.executemany(sql_data, rows)
        inserted = cur.rowcount if cur.rowcount >= 0 else 0
        # rowcount 在 executemany 可能是 -1(SQLite 不支持精确统计 multi-row),改用 SUM
        # 这里改用单独 INSERT + sum
        conn.commit()

        # ctrl 表 INSERT OR IGNORE(独立于数据表 INSERT 结果,单独按 (ts_code, trade_date) 去重)
        if {"ts_code", "trade_date"}.issubset(df.columns):
            ctrl_pairs = list(set(zip(df["ts_code"].astype(str), df["trade_date"].astype(str))))
            sql_ctrl = f"INSERT OR IGNORE INTO {ctrl_table} (ts_code, trade_date) VALUES (?, ?)"
            for pair in ctrl_pairs:
                conn.execute(sql_ctrl, pair)
            conn.commit()

        return inserted
    finally:
        conn.close()


def upsert_day_pair(ts_code: str, df_qfq: pd.DataFrame, df_nofq: pd.DataFrame) -> Tuple[int, int]:
    """专门给 day 用的快捷:同时写 qfq + nofq + 各自的 ctrl

    Returns:
        (qfq_inserted, nofq_inserted)
    """
    qfq_n = upsert_rows("day", df_qfq)
    nofq_n = upsert_rows("day_nofuquan", df_nofq)
    return (qfq_n, nofq_n)


# ============================================================================
# 数据读取(通用 _read)
# ============================================================================
def _read(
    db_kind: str,
    ts_codes: Optional[Union[str, Iterable[str]]],
    trade_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    forward: int = 0,
    backward: int = 0,
    order_by: Optional[str] = None,
) -> pd.DataFrame:
    """通用读取(单表结构)

    Args:
        db_kind: 'minute'/'ticks'/'day'/'day_nofuquan'
        ts_codes: None = 全部;str = 单只;list = 多只
        trade_date / start_date / end_date / forward / backward:
            互斥两套模式,见 _resolve_dates

    Returns:
        DataFrame,按 (ts_code, trade_date, ...) 升序
    """
    kind = _resolve_kind(db_kind)
    schema = KIND_SCHEMAS[kind]
    data_table = schema["data_table"]
    ts_list = _norm_ts_codes(ts_codes)

    dates = _resolve_dates(trade_date, forward, backward, start_date, end_date)
    if dates is not None and len(dates) == 0:
        return pd.DataFrame()

    where_clauses = []
    params: list = []
    if ts_list is not None and len(ts_list) > 0:
        placeholders = ",".join(["?"] * len(ts_list))
        where_clauses.append(f"ts_code IN ({placeholders})")
        params.extend(ts_list)
    if dates is not None and len(dates) > 0:
        placeholders = ",".join(["?"] * len(dates))
        where_clauses.append(f"trade_date IN ({placeholders})")
        params.extend(dates)

    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    # order_by 默认
    if order_by is None:
        pk_cols = schema["pk_columns"]
        order_by = ", ".join(pk_cols) + " ASC"

    sql = f"SELECT * FROM {data_table}{where_sql} ORDER BY {order_by}"

    conn = sqlite3.connect(str(schema["db_path"]))
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


# ============================================================================
# 元信息
# ============================================================================
def list_tickers(db_kind: str) -> List[str]:
    """列出 db_kind db 里所有有数据的 ts_code(从 ctrl 表读)"""
    kind = _resolve_kind(db_kind)
    ctrl_table = KIND_SCHEMAS[kind]["ctrl_table"]
    conn = sqlite3.connect(str(KIND_SCHEMAS[kind]["db_path"]))
    try:
        cur = conn.execute(f"SELECT DISTINCT ts_code FROM {ctrl_table} ORDER BY ts_code ASC")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def list_ticker_dates(db_kind: str, ts_code: str) -> List[str]:
    """列出某只股票在 db_kind db 里所有有数据的 trade_date(YYYY-MM-DD 升序,从 ctrl 表读)"""
    kind = _resolve_kind(db_kind)
    ctrl_table = KIND_SCHEMAS[kind]["ctrl_table"]
    conn = sqlite3.connect(str(KIND_SCHEMAS[kind]["db_path"]))
    try:
        cur = conn.execute(
            f"SELECT DISTINCT trade_date FROM {ctrl_table} WHERE ts_code=? ORDER BY trade_date ASC",
            (ts_code,),
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def count_rows(db_kind: str) -> dict:
    """统计 db_kind 的数据表 + ctrl 表行数"""
    kind = _resolve_kind(db_kind)
    schema = KIND_SCHEMAS[kind]
    conn = sqlite3.connect(str(schema["db_path"]))
    try:
        d_count = conn.execute(f"SELECT COUNT(*) FROM {schema['data_table']}").fetchone()[0]
        c_count = conn.execute(f"SELECT COUNT(*) FROM {schema['ctrl_table']}").fetchone()[0]
        return {
            "data_table": schema["data_table"],
            "data_rows": d_count,
            "ctrl_table": schema["ctrl_table"],
            "ctrl_rows": c_count,
        }
    finally:
        conn.close()


# ============================================================================
# PolicyDBClient(类,高级 API)
# ============================================================================
class PolicyDBClient:
    """policyStudy 数据库客户端(v3,单表 + ctrl 表)"""

    # ========== 数据读取 ==========
    def get_minute(
        self,
        ts_codes: Optional[Union[str, Iterable[str]]] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        forward: int = 0,
        backward: int = 0,
    ) -> pd.DataFrame:
        """读 minute 数据(1分钟 K 线)

        Args:
            ts_codes: None=全部,或单只/多只
            trade_date: 中心日期(YYYYMMDD 或 YYYY-MM-DD)
            start_date / end_date: 区间范围(可单独给一个,自动以对方为同值)
            forward: 中心日期往前 N 个交易日
            backward: 中心日期往后 N 个交易日

        Returns:
            DataFrame,按 (ts_code, trade_date, time_idx) 升序
        """
        return _read(
            "minute", ts_codes,
            trade_date=trade_date,
            start_date=start_date, end_date=end_date,
            forward=forward, backward=backward,
            order_by="ts_code, trade_date, time_idx",
        )

    def get_ticks(
        self,
        ts_codes: Optional[Union[str, Iterable[str]]] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        forward: int = 0,
        backward: int = 0,
    ) -> pd.DataFrame:
        """读 ticks 数据(分笔)

        参数同 get_minute
        Returns:
            DataFrame,按 (ts_code, trade_date, seqId) 升序
        """
        return _read(
            "ticks", ts_codes,
            trade_date=trade_date,
            start_date=start_date, end_date=end_date,
            forward=forward, backward=backward,
            order_by="ts_code, trade_date, seqId",
        )

    def get_day(
        self,
        ts_codes: Optional[Union[str, Iterable[str]]] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        forward: int = 0,
        backward: int = 0,
        qfq: bool = True,
    ) -> pd.DataFrame:
        """读日线数据

        Args:
            qfq: True=前复权(tbl_day),False=不复权(tbl_day_nofuquan)

        Returns:
            DataFrame,按 (ts_code, trade_date) 升序
        """
        kind = "day" if qfq else "day_nofuquan"
        return _read(
            kind, ts_codes,
            trade_date=trade_date,
            start_date=start_date, end_date=end_date,
            forward=forward, backward=backward,
            order_by="ts_code, trade_date",
        )

    # ========== 单只单日快捷 ==========
    def get_minute_one(self, ts_code: str, trade_date: str) -> pd.DataFrame:
        """单只单日 minute(简化版)"""
        return self.get_minute(ts_code, trade_date=trade_date)

    def get_ticks_one(self, ts_code: str, trade_date: str) -> pd.DataFrame:
        """单只单日 ticks(简化版)"""
        return self.get_ticks(ts_code, trade_date=trade_date)

    def get_day_one(self, ts_code: str, trade_date: str, qfq: bool = True) -> pd.DataFrame:
        """单只单日 day(简化版)"""
        return self.get_day(ts_code, trade_date=trade_date, qfq=qfq)

    # ========== 横向切片 ==========
    def get_minute_on_date(self, trade_date: str) -> pd.DataFrame:
        """某一天所有股票的 minute K(横向切片)"""
        return self.get_minute(trade_date=trade_date)

    def get_ticks_on_date(self, trade_date: str) -> pd.DataFrame:
        """某一天所有股票的 ticks(横向切片)"""
        return self.get_ticks(trade_date=trade_date)

    def get_day_on_date(self, trade_date: str, qfq: bool = True) -> pd.DataFrame:
        """某一天所有股票的 day(横向切片)"""
        return self.get_day(trade_date=trade_date, qfq=qfq)

    # ========== 元信息 ==========
    def list_tickers(self, db_kind: str) -> List[str]:
        return list_tickers(db_kind)

    def list_ticker_dates(self, db_kind: str, ts_code: str) -> List[str]:
        return list_ticker_dates(db_kind, ts_code)

    def count_rows(self, db_kind: str) -> dict:
        return count_rows(db_kind)

    def has_data(self, db_kind: str, ts_code: str, trade_date: str) -> bool:
        return has_data(db_kind, ts_code, trade_date)

    # ========== ctrl ==========
    def ensure_schema(self, db_kind: Optional[str] = None, verbose: bool = False):
        return ensure_schema(db_kind=db_kind, verbose=verbose)

    def get_downloaded(self, db_kind: str) -> Set[Tuple[str, str]]:
        return get_downloaded_pairs(db_kind)

    def mark_downloaded(self, db_kind: str, pairs: Iterable[Tuple[str, str]]) -> int:
        return mark_downloaded(db_kind, pairs)

    def filter_pending(self, db_kind: str, pairs: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
        return filter_pending(db_kind, pairs)


# ============================================================================
# CLI 入口(只做建表,数据导入走 build_policy_db.py / data_gen.py)
# ============================================================================
def _cli():
    p = argparse.ArgumentParser(description="policy_db_client - 建表/统计工具")
    p.add_argument("--init", action="store_true", help="建表(tbl_*)")
    p.add_argument("--count", action="store_true", help="统计各 db 行数")
    p.add_argument("--kind", choices=list(KIND_SCHEMAS.keys()), help="指定 kind(默认全部)")
    args = p.parse_args()

    if args.init:
        ensure_schema(db_kind=args.kind, verbose=True)
    elif args.count:
        kinds = [args.kind] if args.kind else list(KIND_SCHEMAS.keys())
        for kind in kinds:
            info = count_rows(kind)
            print(f"  [{kind}] {info['data_table']}={info['data_rows']} 行 / {info['ctrl_table']}={info['ctrl_rows']} 行")
    else:
        p.print_help()


if __name__ == "__main__":
    _cli()
