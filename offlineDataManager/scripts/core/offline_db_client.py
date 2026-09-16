"""
~/TradingAgent/offlineDataManager/scripts/core/offline_db_client.py
SQLite 多数据库管理 + 通用 upsert/ctrl 断点
支持 3 个 DB:
  - db_cn_basic.db (基础数据)
  - db_cn_kpl.db (开盘啦)
  - db_cn_news.db (新闻)
"""
import sqlite3
from datetime import date, datetime
from pathlib import Path
import re
from typing import Iterable, List, Optional, Union

import pandas as pd
from loguru import logger

# 复用项目配置
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from config.settings import (
    DB_PATH_BASIC, DB_PATH_KPL, DB_PATH_NEWS, DB_PATH_INDEX, DB_PATH,
    SCHEMA_SQL_BASIC, SCHEMA_SQL_KPL, SCHEMA_SQL_NEWS, SCHEMA_SQL_INDEX,
)


# ============================================================
# 多 DB 管理
# ============================================================
DB_PATHS = {
    "basic": DB_PATH_BASIC,
    "kpl":   DB_PATH_KPL,
    "news":  DB_PATH_NEWS,
    "index": DB_PATH_INDEX,  # 2026-09-15 新增
}

SCHEMA_SQLS = {
    "basic": SCHEMA_SQL_BASIC,
    "kpl":   SCHEMA_SQL_KPL,
    "news":  SCHEMA_SQL_NEWS,
    "index": SCHEMA_SQL_INDEX,  # 2026-09-15 新增
}


def snap_ts() -> str:
    """当前时间戳(写入字段用)"""
    return datetime.now().isoformat(timespec="seconds")


def init_db(db_name: str = "basic", verbose: bool = True) -> sqlite3.Connection:
    """初始化指定数据库(建表 + WAL 模式)

    Args:
        db_name: 'basic' / 'kpl' / 'news'
    """
    db_path = DB_PATHS[db_name]
    schema_sql = SCHEMA_SQLS[db_name]
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_sql)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    if verbose:
        logger.info(f"[init_db] {db_name} 初始化完成: {db_path}")
    return conn


def get_conn(db_name: str = "basic") -> sqlite3.Connection:
    """获取指定 DB 的连接(不建表)"""
    db_path = DB_PATHS[db_name]
    return sqlite3.connect(str(db_path))


# ============================================================
# 通用 upsert
# ============================================================
def upsert_df(conn: sqlite3.Connection, df, table: str, key_cols: List[str]):
    """
    通用 upsert(根据 key_cols 去重 + 插入)

    - 新行 INSERT
    - 已存在主键的行跳过(等价于 INSERT OR IGNORE)
    - 自动过滤 DataFrame 中不在表 schema 里的列
    """
    if df is None or len(df) == 0:
        return 0

    # 获取表的实际列(从 schema)
    cur = conn.execute(f"PRAGMA table_info({table})")
    table_cols = {row[1] for row in cur.fetchall()}

    # 过滤 df 的列(只保留表中存在的列)
    valid_cols = [c for c in df.columns if c in table_cols]
    if not valid_cols:
        return 0
    df = df[valid_cols]

    placeholders = ",".join(["?"] * len(valid_cols))
    # 给列名加反引号(SQLite 保留字如 limit / key / order 等需要)
    col_list = ",".join(f"`{c}`" for c in valid_cols)
    sql = f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"
    rows = []
    for row in df.itertuples(index=False, name=None):
        cleaned = tuple(
            ("" if v is None else
             (v.isoformat() if hasattr(v, "isoformat") else v))
            for v in row
        )
        rows.append(cleaned)
    cur = conn.executemany(sql, rows)
    conn.commit()
    return cur.rowcount


# ============================================================
# 覆盖更新(先清空再插入,用于 week/month)
# ============================================================
def replace_table(conn: sqlite3.Connection, df, table: str):
    """
    覆盖更新:DELETE 全表 → 重新 INSERT(整体包在 BEGIN/COMMIT 事务里)
    用于周 K / 月 K 这种"重新计算整个数据集"的场景

    Returns:
        插入行数
    """
    if df is None or len(df) == 0:
        return 0

    # 获取表的实际列
    cur = conn.execute(f"PRAGMA table_info({table})")
    table_cols = {row[1] for row in cur.fetchall()}

    # 过滤 df 的列
    valid_cols = [c for c in df.columns if c in table_cols]
    if not valid_cols:
        return 0
    df = df[valid_cols]

    # 事务化:DELETE + INSERT 必须在同一个事务里,中途崩了表才不会被清空
    try:
        conn.execute("BEGIN")
        conn.execute(f"DELETE FROM {table}")
        placeholders = ",".join(["?"] * len(valid_cols))
        col_list = ",".join(f"`{c}`" for c in valid_cols)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        rows = []
        for row in df.itertuples(index=False, name=None):
            cleaned = tuple(
                ("" if v is None else
                 (v.isoformat() if hasattr(v, "isoformat") else v))
                for v in row
            )
            rows.append(cleaned)
        cur = conn.executemany(sql, rows)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cur.rowcount


# ============================================================
# 通用 ctrl 断点管理
# ============================================================
def get_ctrl(conn: sqlite3.Connection, key: str) -> Optional[str]:
    """获取某张表已拉到的最大日期"""
    row = conn.execute(
        "SELECT max_date FROM tbl_basic_ctrl WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def update_ctrl(conn: sqlite3.Connection, key: str, max_date: str):
    """更新断点(已拉到 max_date)"""
    conn.execute(
        "INSERT OR REPLACE INTO tbl_basic_ctrl (key, max_date, updated_at) VALUES (?, ?, ?)",
        (key, max_date, snap_ts())
    )
    conn.commit()


def get_table_min_max(conn: sqlite3.Connection, table: str, date_col: str = "trade_date") -> tuple:
    """读表的最小/最大日期(用于智能增量)

    Returns:
        (min_date, max_date) — None 表示空表
    """
    try:
        row = conn.execute(
            f"SELECT MIN({date_col}), MAX({date_col}) FROM {table}"
        ).fetchone()
        return row
    except sqlite3.OperationalError:
        return (None, None)


def _get_news_ctrl_value(conn: sqlite3.Connection, src: str) -> Optional[str]:
    """获取某新闻源已拉到的最大 datetime(模块内部 helper)"""
    row = conn.execute(
        "SELECT max_date FROM tbl_news_ctrl WHERE src = ?", (src,)
    ).fetchone()
    return row[0] if row else None


def _update_news_ctrl_value(conn: sqlite3.Connection, src: str, max_date: str):
    """更新新闻源断点(模块内部 helper)"""
    conn.execute(
        "INSERT OR REPLACE INTO tbl_news_ctrl (src, max_date, updated_at) VALUES (?, ?, ?)",
        (src, max_date, snap_ts())
    )
    conn.commit()


# ============================================================
# 通用 ctrl helper(每个 DB 一个本地 ctrl 表,2026-09-15 强化设计)
# ============================================================
# 设计原则:每个 DB 一个 ctrl 表(每张数据表一行)
#   - db_cn_basic.db  → tbl_basic_ctrl    (cn_basic / cn_daily 等 ~23 keys)
#   - db_cn_kpl.db   → tbl_kpl_ctrl      (cn_kpl_list / cn_top_list 等 ~10 keys)
#   - db_cn_news.db  → tbl_news_ctrl     (按 src 不同,已有)
#   - db_cn_index.db → tbl_index_ctrl    (cn_index_basic / cn_index_daily 等,新增)
# 跨域的统一 key 前缀,见 get_ctrl_*() 接口

KPL_CTRL_TABLE = "tbl_kpl_ctrl"
INDEX_CTRL_TABLE = "tbl_index_ctrl"


def get_ctrl_kpl(
    key: Optional[Union[str, Iterable[str]]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[str]:
    """读 kpl 库的 tbl_cn_kpl_ctrl 断点(单值版)

    Args:
        key: 断点 key(例 'cn_kpl_list')
        conn: 可选外部 sqlite3 连接

    Returns:
        max_date 字符串 / None
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("kpl")
    try:
        if key is None:
            return None
        row = conn.execute(
            f"SELECT max_date FROM {KPL_CTRL_TABLE} WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        if own_conn:
            conn.close()


def update_ctrl_kpl(conn: sqlite3.Connection, key: str, max_date: str):
    """更新 kpl 库 tbl_cn_kpl_ctrl 断点"""
    conn.execute(
        f"INSERT OR REPLACE INTO {KPL_CTRL_TABLE} (key, max_date, updated_at) VALUES (?, ?, ?)",
        (key, max_date, snap_ts())
    )
    conn.commit()


def get_ctrl_index(
    key: Optional[Union[str, Iterable[str]]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[str]:
    """读 index 库的 tbl_index_ctrl 断点(单值版,2026-09-15 新增)

    Args:
        key: 断点 key(例 'cn_index_daily')
        conn: 可选外部 sqlite3 连接

    Returns:
        max_date 字符串 / None
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("index")
    try:
        if key is None:
            return None
        row = conn.execute(
            f"SELECT max_date FROM {INDEX_CTRL_TABLE} WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        if own_conn:
            conn.close()


def update_ctrl_index(conn: sqlite3.Connection, key: str, max_date: str):
    """更新 index 库 tbl_index_ctrl 断点(2026-09-15 新增)"""
    conn.execute(
        f"INSERT OR REPLACE INTO {INDEX_CTRL_TABLE} (key, max_date, updated_at) VALUES (?, ?, ?)",
        (key, max_date, snap_ts())
    )
    conn.commit()


# ============================================================
# 状态查询(跨 3 个 DB)
# ============================================================
def show_status(db_path: Path = None):
    """显示 DB 各表状态(默认所有 DB)"""
    if db_path is not None:
        # 单 DB 模式(兼容旧 API)
        _show_status_one(db_path)
        return

    print(f"\n{'='*60}")
    print(f"DB 状态总览")
    print(f"{'='*60}")
    for db_name in ["basic", "kpl", "news"]:
        _show_status_one(DB_PATHS[db_name], db_name)


def _show_status_one(db_path: Path, db_name: str = None):
    """显示单个 DB 状态"""
    if not db_path.exists():
        print(f"\n[{db_name}] {db_path.name}: 不存在")
        return
    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    print(f"\n[{db_name}] {db_path.name}")
    DATE_COLS = ["trade_date", "cal_date", "datetime"]
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        d_min = "?"
        for col in DATE_COLS:
            try:
                v = conn.execute(
                    f"SELECT MIN({col}) FROM {t} WHERE {col} IS NOT NULL AND {col} != ''"
                ).fetchone()[0]
                if v:
                    d_min = str(v)[:10]
                    break
            except Exception:
                continue
        print(f"  {t:<28} {n:>12,} 行  (日期: {d_min})")
    conn.close()


# ============================================================
# 读取接口
# ============================================================
DAY_TABLE = "tbl_cn_day"
ADJ_TABLE = "tbl_cn_adj_factor"
DAY_PRICE_COLS = ["open", "high", "low", "close"]
DAY_VOL_COL = "vol"

# 接受 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD,统一归一到 YYYYMMDD
def _norm_date_yyyymmdd(d: Optional[Union[str, datetime]]) -> Optional[str]:
    if d is None or d == "":
        return None
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    s = str(d).replace("-", "").replace("/", "")
    return s[:8] if len(s) >= 8 else s


def _norm_datetime_string(d: Optional[Union[str, datetime]]) -> Optional[str]:
    """把 datetime 归一为 'YYYY-MM-DD HH:MM:SS' 字符串(给 SQL 比较用)

    接受:datetime 对象 / 'YYYY-MM-DD HH:MM:SS' / 'YYYYMMDD HH:MM:SS' / 'YYYYMMDD' / 'YYYY-MM-DD'
    返回:'YYYY-MM-DD HH:MM:SS' 或 None
    """
    if d is None or d == "":
        return None
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d %H:%M:%S")
    s = str(d).strip()
    # YYYYMMDD (8 位) → 加默认时间
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]} 00:00:00"
    # YYYYMMDD HH:MM:SS(无横线)→ 加横线
    # 条件:s[8] 必须是空格(分隔日期和时间),s[:8] 是 8 位数字
    # 注意:不要用 s[9:].isdigit() 检查,因为时分秒含 ":" 不是纯数字
    if len(s) == 17 and s[:8].isdigit() and s[8] == " ":
        return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[9:]}"
    # YYYY-MM-DD(10 位)→ 加默认时间
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s} 00:00:00"
    # 已经是 YYYY-MM-DD HH:MM:SS(19 位)→ 原样返回
    return s


def _norm_to_list(
    val: Optional[Union[str, Iterable[str]]],
    valid_set: Optional[tuple] = None,
    param_name: str = "param",
) -> Optional[list]:
    """把单值/列表/None 归一为 list;None → []

    Args:
        val:        None / str / 可迭代
        valid_set:   白名单(校验每个元素),None = 不校验(list_status 这类不固定)
        param_name:  错误信息里用的参数名
    """
    if val is None:
        return []
    if isinstance(val, str):
        items = [val]
    else:
        items = list(val)
        if any(not isinstance(x, str) for x in items):
            raise ValueError(f"{param_name} 元素必须是 str,当前: {items}")
    if valid_set is not None:
        bad = [x for x in items if x not in valid_set]
        if bad:
            raise ValueError(f"{param_name} 非法值 {bad};允许: {valid_set}")
    if not items:
        return []
    return items


def get_day(
    ts_code: Optional[str] = None,
    ts_codes: Optional[Iterable[str]] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    qfq: bool = True,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读日 K(默认前复权)

    数据源:db_cn_basic.db / tbl_cn_day(存的是不复权原始数据)
    前复权实现:价 × (adj_factor / 该 ts_code 最新 adj_factor),量 ÷ 该系数
    算法参考 CNDataDown.update_week() (line 924-945)

    过滤条件(可组合;trade_date 优先于 start/end_date):
      - ts_code:    单只股票,例 "000001.SZ"
      - ts_codes:   多只股票,例 ["000001.SZ", "600000.SH"] 或 set/tuple 都行
      - start_date: 起始日期(包含),YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD
      - end_date:   结束日期(包含),同上
      - trade_date: 精确查某一天;传了就忽略 start/end_date
      - qfq:        是否前复权(默认 True);返回的 open/high/low/close/vol 已是复权后价格
                    amount(成交额)始终不变
      - columns:    指定返回列(默认 ["ts_code","trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount"])
      - conn:       可选外部 sqlite3 连接,方便复用事务;不传则内部 open + close basic 库

    Returns:
        pd.DataFrame,按 (ts_code, trade_date) 升序;空表返回空 DataFrame
    """
    # 1. 连接管理
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    try:
        # 2. 列与 WHERE 子句拼装
        all_cols = ["ts_code", "trade_date", "open", "high", "low", "close",
                    "pre_close", "change", "pct_chg", "vol", "amount"]
        if columns is None:
            select_cols = all_cols
        else:
            # 校验列名合法,过滤掉 qfq 相关中间列(我们不返回)
            bad = [c for c in columns if c not in all_cols]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {all_cols}")
            select_cols = columns

        wheres = []
        params: list = []

        if ts_code is not None and ts_codes is not None:
            raise ValueError("ts_code 和 ts_codes 互斥,只能传一个")

        if ts_code is not None:
            wheres.append("ts_code = ?")
            params.append(ts_code)
        elif ts_codes is not None:
            codes = list(ts_codes)
            if not codes:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(codes)

        # 日期条件:trade_date 优先
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT {','.join(select_cols)} FROM {DAY_TABLE}{where_sql} ORDER BY ts_code, trade_date"

        df = pd.read_sql_query(sql, conn, params=params)

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)

        # 3. 前复权计算(照搬 update_week 逻辑)
        if qfq:
            # 只对需要复权的列做 join,节省 IO
            ts_codes_in_df = df["ts_code"].unique().tolist()
            placeholders = ",".join(["?"] * len(ts_codes_in_df))
            df_adj = pd.read_sql_query(
                f"SELECT ts_code, trade_date, adj_factor FROM {ADJ_TABLE} "
                f"WHERE ts_code IN ({placeholders})",
                conn, params=ts_codes_in_df,
            )
            if df_adj is not None and len(df_adj) > 0:
                # 每只股票最新 adj_factor 作为基准
                latest_adj = df_adj.groupby("ts_code")["adj_factor"].last().reset_index()
                latest_adj.columns = ["ts_code", "adj_factor_latest"]

                df = df.merge(df_adj, on=["ts_code", "trade_date"], how="left")
                df = df.merge(latest_adj, on="ts_code", how="left")
                df["adj_factor"] = df["adj_factor"].fillna(1.0)
                df["adj_factor_latest"] = df["adj_factor_latest"].fillna(1.0)
                qfq_factor = df["adj_factor"] / df["adj_factor_latest"]

                for col in DAY_PRICE_COLS:
                    if col in df.columns:
                        df[col] = df[col] * qfq_factor
                if DAY_VOL_COL in df.columns:
                    df[DAY_VOL_COL] = df[DAY_VOL_COL] / qfq_factor
                # amount 不变

                # 丢掉 join 进来的中间列,保持 select_cols 干净
                df = df.drop(columns=["adj_factor", "adj_factor_latest"])
            else:
                logger.warning(f"[get_day] tbl_cn_adj_factor 无匹配数据,按不复权返回")

        # 保证列顺序稳定
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 周月 K 通用读取(week / month)
# ============================================================
WEEK_TABLE = "tbl_cn_week"
MONTH_TABLE = "tbl_cn_month"
FREQ_TABLE_COLS = ["ts_code", "trade_date", "open", "high", "low", "close",
                   "pre_close", "change", "pct_chg", "vol", "amount"]


def _iso_year_week(yyyymmdd: str) -> tuple:
    """YYYYMMDD -> (iso_year, iso_week)"""
    return datetime.strptime(yyyymmdd, "%Y%m%d").isocalendar()[:2]


def _ym(yyyymmdd: str) -> str:
    """YYYYMMDD -> YYYYMM(月份 bucket)"""
    return yyyymmdd[:6]


def _get_freq(
    table: str,
    ts_code: Optional[str] = None,
    ts_codes: Optional[Iterable[str]] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """周/月 K 通用读取,供 get_week / get_month 调用

    数据源:db_cn_basic.db / tbl_cn_week 或 tbl_cn_month
    重要:周月 K 入库时**已前复权**,接口不再提供不复权选项

    过滤条件(可组合;trade_date 优先于 start/end_date):
      - ts_code:    单只股票
      - ts_codes:   多只股票
      - start_date: 起始日期(包含),YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD
      - end_date:   结束日期(包含),同上
      - trade_date: 传入某一天 → 返回该天**所在周/月**的所有 K 行
                    (week = ISO 周,month = 自然月,通常各 1 行)
      - columns:    自定义返回列
      - conn:       可选外部连接
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    try:
        if columns is None:
            select_cols = FREQ_TABLE_COLS
        else:
            bad = [c for c in columns if c not in FREQ_TABLE_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {FREQ_TABLE_COLS}")
            select_cols = columns

        wheres = []
        params: list = []

        if ts_code is not None and ts_codes is not None:
            raise ValueError("ts_code 和 ts_codes 互斥,只能传一个")

        if ts_code is not None:
            wheres.append("ts_code = ?")
            params.append(ts_code)
        elif ts_codes is not None:
            codes = list(ts_codes)
            if not codes:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(codes)

        # 日期条件:trade_date 优先 — week 按 ISO 年周,month 按年月 bucket
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td and table == MONTH_TABLE:
                # month:用 YYYYMM bucket,SQL 直接过滤
                wheres.append("substr(trade_date, 1, 6) = ?")
                params.append(_ym(td))
            # week:trade_date 先不加 WHERE,后面 Python 端按 ISO 过滤
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""

        sql = f"SELECT {','.join(select_cols)} FROM {table}{where_sql} ORDER BY ts_code, trade_date"
        df = pd.read_sql_query(sql, conn, params=params)

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)

        # week + trade_date:Python 端按 ISO year/week 过滤
        if trade_date is not None and table == WEEK_TABLE:
            td = _norm_date_yyyymmdd(trade_date)
            target_iso = _iso_year_week(td)
            ts_series = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
            iso_cal = ts_series.dt.isocalendar()
            mask = (iso_cal["year"].astype(int) == target_iso[0]) & \
                   (iso_cal["week"].astype(int) == target_iso[1])
            df = df[mask.values].reset_index(drop=True)

        # 保证列顺序
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


def get_week(
    ts_code: Optional[str] = None,
    ts_codes: Optional[Iterable[str]] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读周 K(已是前复权)

    数据源:db_cn_basic.db / tbl_cn_week
    周聚合规则(对照 offline_downloader._aggregate_daily_to_freq freq='weekly'):
      - trade_date = 该周最后交易日(YYYYMMDD),ISO 周聚合
      - open = 周内第一日 open, close = 周内最后一日 close
      - high/low = max/min, vol/amount = sum

    过滤条件:
      - ts_code / ts_codes: 单股 / 多股
      - start_date + end_date: 区间(按 trade_date)
      - trade_date: 传入某天 → 返回该天**所在 ISO 周**的所有周 K 行(通常 1 行)
      - columns: 自定义返回列
      - conn: 可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 (ts_code, trade_date) 升序
    """
    return _get_freq(
        table=WEEK_TABLE,
        ts_code=ts_code, ts_codes=ts_codes,
        start_date=start_date, end_date=end_date, trade_date=trade_date,
        columns=columns, conn=conn,
    )


def get_month(
    ts_code: Optional[str] = None,
    ts_codes: Optional[Iterable[str]] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读月 K(已是前复权)

    数据源:db_cn_basic.db / tbl_cn_month
    月聚合规则(对照 offline_downloader._aggregate_daily_to_freq freq='monthly'):
      - trade_date = 该月最后交易日(YYYYMMDD),自然月聚合
      - open = 月内第一日 open, close = 月内最后一日 close
      - high/low = max/min, vol/amount = sum

    过滤条件:
      - ts_code / ts_codes: 单股 / 多股
      - start_date + end_date: 区间(按 trade_date)
      - trade_date: 传入某天 → 返回该天**所在自然月**的所有月 K 行(1 行)
      - columns: 自定义返回列
      - conn: 可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 (ts_code, trade_date) 升序
    """
    return _get_freq(
        table=MONTH_TABLE,
        ts_code=ts_code, ts_codes=ts_codes,
        start_date=start_date, end_date=end_date, trade_date=trade_date,
        columns=columns, conn=conn,
    )


# ============================================================
# 交易日历读取
# ============================================================
TRADECAL_TABLE = "tbl_cn_tradecal"
TRADECAL_COLS = ["cal_date", "exchange", "is_open", "pretrade_date", "snap_ts"]


def get_tradecal(
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    day_num: Optional[int] = None,
    market: Optional[str] = "SSE",
    is_open: Optional[bool] = True,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读交易日历

    数据源:db_cn_basic.db / tbl_cn_tradecal
    字段:cal_date (YYYYMMDD), exchange (SSE/SZSE/BSE), is_open (1=交易日),
          pretrade_date (上一交易日 YYYYMMDD)

    三种查询模式(互斥):
      A) 区间模式:给 start_date + end_date
         → 返回区间内所有日历日(含周末/节假日,按 is_open 过滤)
      B) 单日模式:只给 trade_date,不给 day_num
         → 返回 trade_date 那一行
      C) Offset 模式:给 trade_date + day_num
         → 从 trade_date(含)起的 day_num 个交易日
         → 如果 trade_date 不是交易日,从**后面第一个交易日**开始数
         → day_num <= 0 抛 ValueError

    附加过滤:
      - market:    'SSE' (沪市,默认) / 'SZSE' (深市) / None (无市场过滤,返回所有交易所)
      - is_open:   True=只返回开市日(默认), False=只返回非开市日, None=全部日历日
      - columns:   自定义返回列(默认全 5 列)
      - conn:      可选外部连接

    Returns:
        pd.DataFrame,按 (cal_date, exchange) 升序
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    try:
        if columns is None:
            select_cols = TRADECAL_COLS
        else:
            bad = [c for c in columns if c not in TRADECAL_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {TRADECAL_COLS}")
            select_cols = columns

        # 互斥校验:start+end vs trade_date+day_num
        has_range = (start_date is not None) or (end_date is not None)
        has_offset = (trade_date is not None) and (day_num is not None)
        if has_range and has_offset:
            raise ValueError("start_date/end_date 和 trade_date+day_num 互斥,不能同时用")
        if day_num is not None and day_num <= 0:
            raise ValueError(f"day_num 必须 > 0,当前: {day_num}")
        if market is not None and market not in ("SSE", "SZSE"):
            raise ValueError(f"market 必须为 None / 'SSE' / 'SZSE',当前: {market}")

        wheres = []
        params: list = []

        # market 过滤
        if market is not None:
            wheres.append("exchange = ?")
            params.append(market)

        # is_open 过滤
        if is_open is not None:
            wheres.append("is_open = ?")
            params.append(1 if is_open else 0)

        # === 模式 C: offset 模式 ===
        if has_offset:
            td = _norm_date_yyyymmdd(trade_date)
            # 找 trade_date(含)起的 day_num 个交易日
            # 思路:先数 trade_date 之前有多少个 is_open=1 的交易日,得到 offset
            # 注:COUNT 也要套用同一 market 过滤(保持语义一致)
            cnt_query = (
                f"SELECT COUNT(*) FROM {TRADECAL_TABLE} WHERE is_open=1 AND cal_date < ?"
            )
            cnt_params = [td]
            if market is not None:
                cnt_query += " AND exchange = ?"
                cnt_params.append(market)
            cnt_row = conn.execute(cnt_query, cnt_params).fetchone()
            offset = cnt_row[0]
            # 注意:offset 已经把 trade_date 之前的都排除了,trade_date 本身算第 offset+1 个
            # 所以 LIMIT day_num OFFSET offset 就是从 trade_date 开始数 day_num 个
            # 同样 offset 模式 SQL 也要应用 market / is_open 等过滤
            offset_wheres = ["is_open=1"]
            offset_params: list = []
            if market is not None:
                offset_wheres.append("exchange = ?")
                offset_params.append(market)
            sql = (
                f"SELECT {','.join(select_cols)} FROM {TRADECAL_TABLE} "
                f"WHERE {' AND '.join(offset_wheres)} "
                f"ORDER BY cal_date, exchange "
                f"LIMIT ? OFFSET ?"
            )
            # day_num 是"交易日个数"(按 cal_date),每个交易日可能 1 行(market 指定)或 2 行(双交易所)
            # 没传 market 时乘以 2,传了 market 时就是 1 行/天
            rows_per_day = 1 if market is not None else 2
            limit_n = day_num * rows_per_day
            offset_params.extend([limit_n, offset])
            df = pd.read_sql_query(sql, conn, params=offset_params)
            # 还要套上 market / columns 的处理
            if df is None or len(df) == 0:
                return pd.DataFrame(columns=select_cols)
            # 如果用户传了 market,SQL 里已经过滤了;这里再保一次序
            df = df.sort_values(["cal_date", "exchange"]).reset_index(drop=True)
            for col in select_cols:
                if col not in df.columns:
                    df[col] = None
            return df[select_cols]

        # === 模式 B: 单日 ===
        if trade_date is not None and not has_offset:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("cal_date = ?")
                params.append(td)
        # === 模式 A: 区间 ===
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("cal_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("cal_date <= ?")
                params.append(ed)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT {','.join(select_cols)} FROM {TRADECAL_TABLE}{where_sql} ORDER BY cal_date, exchange"
        df = pd.read_sql_query(sql, conn, params=params)

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)

        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 股票基本信息读取
# ============================================================
BASIC_TABLE = "tbl_cn_basic"
BASIC_COLS = ["ts_code", "symbol", "name", "industry", "fullname", "enname",
              "cnspell", "market", "exchange", "curr_type", "list_status",
              "list_date", "delist_date", "is_hs", "snap_ts"]
BASIC_VALID_EXCHANGES = ("SSE", "SZSE", "BSE")
BASIC_VALID_MARKETS = ("主板", "创业板", "科创板", "北交所")
BASIC_VALID_LIST_STATUS = ("L", "P")  # 不包含 D(退市):DB 不存退市股票


def get_basic(
    exchange: Optional[Union[str, Iterable[str]]] = None,
    market: Optional[Union[str, Iterable[str]]] = None,
    list_status: Optional[Union[str, Iterable[str]]] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读股票基本信息

    数据源:db_cn_basic.db / tbl_cn_basic
    字段(15 列):
      ts_code (主键), symbol, name, industry, fullname, enname, cnspell,
      market (主板/创业板/科创板/北交所), exchange (SSE/SZSE/BSE),
      curr_type, list_status (L=上市/D=退市/P=暂停), list_date, delist_date,
      is_hs (沪深港通标记), snap_ts

    过滤条件(全部可选,默认 None = 不过滤,返回 DB 内全部行):
      - exchange:    单值(str) 或 列表;取自 BASIC_VALID_EXCHANGES
                     None = 全部交易所
      - market:      单值(str) 或 列表;取自 BASIC_VALID_MARKETS
                     None = 全部板块
      - list_status: 单值或列表;取自 BASIC_VALID_LIST_STATUS ('L' / 'P')
                     None = DB 内所有状态(目前 DB 包含 L + P)
                     不接受 'D'(退市):DB 不存退市股票,如需退市数据需先扩 update_basic
      - columns:     自定义返回列(默认全 15 列)
      - conn:        可选外部 sqlite3 连接

    关键说明:
      - exchange 和 market 是两个独立维度(AND 关系),不是互斥关系
      - 主板跨 SSE+SZSE 两个交易所;创业板只在 SZSE;科创板只在 SSE;北交所只在 BSE
      - 支持单值或列表:exchange='SSE' 等价 exchange=['SSE']
      - 默认行为(None)等价 list_status=['L','P'](DB 内所有行)

    Returns:
        pd.DataFrame,按 (ts_code) 升序
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    try:
        if columns is None:
            select_cols = BASIC_COLS
        else:
            bad = [c for c in columns if c not in BASIC_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {BASIC_COLS}")
            select_cols = columns

        # 规范化 exchange / market / list_status 为列表
        exchanges = _norm_to_list(exchange, BASIC_VALID_EXCHANGES, "exchange")
        markets = _norm_to_list(market, BASIC_VALID_MARKETS, "market")
        statuses = _norm_to_list(list_status, BASIC_VALID_LIST_STATUS, "list_status")

        wheres = []
        params: list = []

        if exchanges:
            placeholders = ",".join(["?"] * len(exchanges))
            wheres.append(f"exchange IN ({placeholders})")
            params.extend(exchanges)

        if markets:
            placeholders = ",".join(["?"] * len(markets))
            wheres.append(f"market IN ({placeholders})")
            params.extend(markets)

        if statuses:
            placeholders = ",".join(["?"] * len(statuses))
            wheres.append(f"list_status IN ({placeholders})")
            params.extend(statuses)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT {','.join(select_cols)} FROM {BASIC_TABLE}{where_sql} ORDER BY ts_code"
        df = pd.read_sql_query(sql, conn, params=params)

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)

        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 通用 ctrl 断点查询(get_ctrl 已有底层函数,这里是批量版)
# ============================================================
CTRL_TABLE = "tbl_basic_ctrl"
CTRL_COLS = ["key", "max_date", "updated_at"]


def get_ctrl_basic(
    key: Optional[Union[str, Iterable[str]]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读 basic 库的 tbl_basic_ctrl 断点表

    数据源:db_cn_basic.db / tbl_basic_ctrl
    字段:key (主键,代表一张数据表的断点), max_date, updated_at

    Args:
        key:    None = 返回全部断点
                单值(str) = 精确匹配一个 key(例 'cn_basic')
                列表 = 精确匹配多个 key(例 ['cn_basic','cn_daily'])
        conn:   可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 key 升序
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    try:
        # key=None = 全部;key=[] = 0 行(显式空);key=str/Iterable = 精确 IN
        # 不能用 _norm_to_list 因为 None 和 [] 都归一成 [],会丢区分
        if key is None:
            sql = f"SELECT {','.join(CTRL_COLS)} FROM {CTRL_TABLE} ORDER BY key"
            params: list = []
        else:
            keys = _norm_to_list(key, None, "key")
            if not keys:
                # 显式空列表 → 0 行
                return pd.DataFrame(columns=CTRL_COLS)
            placeholders = ",".join(["?"] * len(keys))
            sql = f"SELECT {','.join(CTRL_COLS)} FROM {CTRL_TABLE} WHERE key IN ({placeholders}) ORDER BY key"
            params = keys

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=CTRL_COLS)
        df = df[CTRL_COLS]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 复权因子读取
# ============================================================
ADJ_FACTOR_COLS = ["ts_code", "trade_date", "adj_factor", "snap_ts"]


def get_adj_factor(
    ts_code: Optional[str] = None,
    ts_codes: Optional[Iterable[str]] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读复权因子

    数据源:db_cn_basic.db / tbl_cn_adj_factor
    字段:ts_code (主键之一), trade_date (主键之一, YYYYMMDD),
          adj_factor (浮点), snap_ts

    过滤条件(全部可选,默认 None = 不过滤;全 None = 全表):
      - ts_code:    单只股票,例 "000001.SZ"
      - ts_codes:   多只股票,例 ["000001.SZ","600000.SH"] / set / tuple 都行
                    (与 ts_code 互斥,只能传一个;None = 全部股票)
      - start_date: 起始日期(包含),YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD
      - end_date:   结束日期(包含),同上
      - trade_date: 精确查某一天(优先级最高;传了就忽略 start/end_date)
      - columns:    自定义返回列(默认 ["ts_code","trade_date","adj_factor","snap_ts"])
      - conn:       可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 (ts_code, trade_date) 升序;空表返回空 DataFrame
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    try:
        if columns is None:
            select_cols = ADJ_FACTOR_COLS
        else:
            bad = [c for c in columns if c not in ADJ_FACTOR_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {ADJ_FACTOR_COLS}")
            select_cols = columns

        wheres = []
        params: list = []

        if ts_code is not None and ts_codes is not None:
            raise ValueError("ts_code 和 ts_codes 互斥,只能传一个")

        if ts_code is not None:
            wheres.append("ts_code = ?")
            params.append(ts_code)
        elif ts_codes is not None:
            codes = list(ts_codes)
            if not codes:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(codes)

        # 日期条件:trade_date 优先
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT {','.join(select_cols)} FROM {ADJ_TABLE}{where_sql} ORDER BY ts_code, trade_date"

        df = pd.read_sql_query(sql, conn, params=params)

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)

        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 开盘啦涨停榜读取
# ============================================================
KPL_LIST_TABLE = "tbl_cn_kpl_list"
KPL_LIST_COLS = ["ts_code", "name", "trade_date", "lu_time", "ld_time",
                 "open_time", "last_time", "lu_desc", "tag", "theme",
                 "net_change", "bid_amount", "status", "bid_change",
                 "bid_turnover", "lu_bid_vol", "pct_chg", "bid_pct_chg",
                 "rt_pct_chg", "limit_order", "amount", "turnover_rate",
                 "free_float", "lu_limit_order", "snap_ts"]
# status 特殊关键字(扩展语法)
STATUS_NON_FIRST = "非首板"     # 排除 '首板'
STATUS_N_PLUS_RE = r"^(\d+)板以上$"  # N 连板及以上
STATUS_N_DAYS_RE = r"^\d+天$"        # N 天*板(LIKE 'N天%')
STATUS_DAYS_N_PLATES_RE = r"^天\d+板$"  # *天M板(LIKE '%天M板')


def get_kpl_list(
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    ts_codes: Optional[Union[str, Iterable[str]]] = None,
    themes: Optional[Union[str, Iterable[str]]] = None,
    lu_descs: Optional[Union[str, Iterable[str]]] = None,
    tags: Optional[Union[str, Iterable[str]]] = None,
    status: Optional[Union[str, Iterable[str]]] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读开盘啦涨停榜

    数据源:db_cn_kpl.db / tbl_cn_kpl_list
    字段(25 列):ts_code, name, trade_date, lu_time/ld_time/open_time/last_time(涨停时间),
          lu_desc(涨停原因,如 '印制电路板'), tag(涨停/炸板 等),
          theme(题材组合,用 '、'分隔,如 '军工、低空经济'),
          net_change, bid_amount, status(连板标记:'首板'/'2连板'/'3天2板' 等),
          bid_change, bid_turnover, lu_bid_vol, pct_chg, bid_pct_chg,
          rt_pct_chg, limit_order, amount, turnover_rate, free_float,
          lu_limit_order, snap_ts

    过滤条件(全部可选,默认 None = 不过滤):
      - start_date / end_date: trade_date 区间(YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD)
      - trade_date: 单日查询(优先级最高;传了就忽略 start/end_date)
      - ts_codes:   单值(str) 或 列表;None = 全部股票;精确 IN
      - themes:     单值或列表;None = 全部;**模糊包含匹配** LIKE '%theme%'
                    (因为 theme 是 '军工、低空经济' 这样的组合字符串)
      - lu_descs:   单值或列表;None = 全部;**模糊包含匹配** LIKE '%lu_desc%'
                    (涨停原因如 '印制电路板','新能源汽车' 等)
      - tags:       单值(str, '涨停' 或 '炸板') 或 列表;None = 不筛选 tag(返回涨停+炸板所有)
                    2026-09-15 加(用户要求支持按 tag 筛选:单独涨停 / 单独炸板 / 两者都有)
      - status:     单值或列表;None = 全部;精确匹配
                    特殊关键字(自动展开成精确 status 值列表的 IN 查询):
                      - '非首板' = 排除 '首板' 的所有连板记录
                      - 'N板以上' (N=1-9,如 '2板以上') = 连板数 ≥ N 的所有 status
                        例 '2板以上' 匹配 '2连板','3连板','3天2板','4连板' 等
                      - 'N天' (N=数字,如 '3天') = 所有 'N天*板'(LIKE '3天%')
                        例 '3天' 匹配 '3天1板','3天2板','3天3板'
                      - '天M板' (M=数字,如 '天2板') = 所有 '*天M板'(LIKE '%天2板')
                        例 '天2板' 匹配 '3天2板','4天2板','5天2板'
                      - 其他值(如 '2连板','3天2板') = 精确 IN
      - columns:    自定义返回列(默认全 25 列)
      - conn:       可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 (trade_date DESC, ts_code) 排序;空表返回空 DataFrame
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("kpl")

    try:
        if columns is None:
            select_cols = KPL_LIST_COLS
        else:
            bad = [c for c in columns if c not in KPL_LIST_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {KPL_LIST_COLS}")
            select_cols = columns

        # 规范化
        # 注意:None 和 [] 要区分语义(None=全部, []=显式空=0行)
        # _norm_to_list 把两者都归一成 [],所以 ts_codes 直接判断 None
        if ts_codes is None:
            codes: list = []
        else:
            codes = _norm_to_list(ts_codes, None, "ts_codes")
            if not codes:
                return pd.DataFrame(columns=select_cols)
        theme_list = _norm_to_list(themes, None, "themes")
        lu_desc_list = _norm_to_list(lu_descs, None, "lu_descs")
        tag_list = _norm_to_list(tags, None, "tags")
        status_list = _norm_to_list(status, None, "status")

        # === status 展开特殊关键字 ===
        if status_list:
            # 拿 DB 里所有 status 值做白名单(展开后必须落在这些值里)
            all_status_rows = conn.execute(
                f"SELECT DISTINCT status FROM {KPL_LIST_TABLE} WHERE status IS NOT NULL AND status != ''"
            ).fetchall()
            all_status_set = {row[0] for row in all_status_rows}

            expanded = []
            for s in status_list:
                if s == STATUS_NON_FIRST:
                    # 排除 '首板'
                    expanded.extend([x for x in all_status_set if x != "首板"])
                elif (m := re.match(STATUS_N_PLUS_RE, s)):
                    n = int(m.group(1))
                    if not (1 <= n <= 9):
                        raise ValueError(f"status 'N板以上' 的 N 必须在 1-9,当前: {s}")
                    # N=1: '首板'(数字=1) + '新上市未开板'(特殊状态,也算 1 板以上)
                    # 简单做法:直接用全 status 集合(N=1 相当于无过滤)
                    if n == 1:
                        expanded.extend(all_status_set)
                        continue
                    # N>=2: status 中"已封板数" ≥ N
                    # status 形式: 'N连板'(数字=N) / 'M天N板'(数字=N)
                    for st in all_status_set:
                        m1 = re.match(r"^(\d+)连板$", st)
                        if m1:
                            if int(m1.group(1)) >= n:
                                expanded.append(st)
                            continue
                        m2 = re.match(r"^\d+天(\d+)板$", st)
                        if m2:
                            if int(m2.group(1)) >= n:
                                expanded.append(st)
                            continue
                        # '首板' 不参与 N>=2
                        # '新上市未开板' 特殊,不参与 N>=2
                elif re.match(STATUS_N_DAYS_RE, s):
                    # 'N天' → 所有 'N天*板'(LIKE 'N天%')
                    n_days = s[:-1]  # 去掉末尾 '天'
                    for st in all_status_set:
                        if st.startswith(n_days + "天"):
                            expanded.append(st)
                elif re.match(STATUS_DAYS_N_PLATES_RE, s):
                    # '天M板' → 所有 '*天M板'(LIKE '%天M板')
                    suffix = s[1:]  # 去掉开头 '天',即 'M板'
                    for st in all_status_set:
                        if st.endswith("天" + suffix):
                            expanded.append(st)
                else:
                    # 普通精确值(2连板/3天2板/首板 等)
                    expanded.append(s)
            # 去重
            status_list = sorted(set(expanded))
            if not status_list:
                # 展开后为空,直接返回空
                return pd.DataFrame(columns=select_cols)

        wheres = []
        params: list = []

        # 日期:trade_date 优先
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        # ts_codes
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(codes)

        # themes:模糊 LIKE(每个主题 OR,多主题间是 OR 不是 AND)
        # 用户说"军工、低空经济"想匹配"军工"也算,所以主题间 OR
        theme_subwheres = []
        for t in theme_list:
            theme_subwheres.append("theme LIKE ?")
            params.append(f"%{t}%")
        if theme_subwheres:
            wheres.append("(" + " OR ".join(theme_subwheres) + ")")

        # lu_descs:模糊 LIKE,OR
        lu_desc_subwheres = []
        for d in lu_desc_list:
            lu_desc_subwheres.append("lu_desc LIKE ?")
            params.append(f"%{d}%")
        if lu_desc_subwheres:
            wheres.append("(" + " OR ".join(lu_desc_subwheres) + ")")

        # tags:精确 IN(2026-09-15 加,支持单独涨停/单独炸板/两者都有的筛选)
        if tag_list:
            placeholders = ",".join(["?"] * len(tag_list))
            wheres.append(f"tag IN ({placeholders})")
            params.extend(tag_list)

        # status:精确 IN
        if status_list:
            placeholders = ",".join(["?"] * len(status_list))
            wheres.append(f"status IN ({placeholders})")
            params.extend(status_list)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = (
            f"SELECT {','.join(select_cols)} FROM {KPL_LIST_TABLE}{where_sql} "
            f"ORDER BY trade_date DESC, ts_code"
        )

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 开盘啦题材成分读取
# ============================================================
KPL_CC_TABLE = "tbl_cn_kpl_concept_cons"
KPL_CC_COLS = ["ts_code", "name", "con_name", "con_code", "trade_date",
               "desc", "hot_num", "snap_ts"]


# 开盘啦涨停表现详情读取(tbl_cn_kpl_limit_performance)
# ============================================================
KPL_LP_TABLE = "tbl_cn_kpl_limit_performance"
KPL_LP_COLS = ["trade_date", "ts_code", "name", "board_type", "board_count",
               "lu_time", "theme", "limit_reason", "is_break", "amplitude",
               "turnover_rate", "limit_order", "lu_limit_order", "net_change",
               "main_in", "main_out", "amount", "free_float", "close_price",
               "pct_chg", "board_period", "theme_id", "sector_id", "snap_ts"]


def get_kpl_concept_cons(
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    ts_codes: Optional[Union[str, Iterable[str]]] = None,
    themes: Optional[Union[str, Iterable[str]]] = None,
    descs: Optional[Union[str, Iterable[str]]] = None,
    min_hot_num: Optional[int] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读开盘啦题材成分(题材与个股的关系)

    数据源:db_cn_kpl.db / tbl_cn_kpl_concept_cons
    字段(8 列):
      ts_code (主键之一, **题材代码**,如 '000009.KP' — 注意不是股票代码!)
      name (主键之一, 题材名称,如 '光刻机概念')
      con_name, con_code (主键之一, **成分股**,如 '上汽集团' / '600104.SH')
      trade_date (YYYYMMDD)
      desc (题材-公司关系描述,如 '公司持有芯上微装股权比例约2.13%...')
      hot_num (整数,题材热度)
      snap_ts

    ⚠️ 表里 ts_code 是**题材代码**不是股票代码!成分股代码在 con_code 字段。
    本接口为了查询方便,**参数名 ts_codes 实际查的是 con_code**(成分股代码)。
    如果想查"某题材的所有成分股",用 themes 参数(对应 name 字段)。

    过滤条件(全部可选,默认 None = 不过滤):
      - start_date / end_date: trade_date 区间(YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD)
      - trade_date: 单日查询(优先级最高;传了就忽略 start/end_date)
      - ts_codes:   单值(str) 或 列表;None = 全部成分股;**精确 IN con_code**
      - themes:     单值或列表;None = 全部题材;**精确 IN name**(题材名是规范值)
      - descs:      单值或列表;None = 全部;**模糊 LIKE '%desc%'** (OR)
                    例 descs='AI技术' 匹配所有 desc 字段含 'AI技术' 的
                    例 descs=['AI技术', '航天'] 任一含都返回
      - min_hot_num: 整数;None = 不限;只返回 hot_num >= min_hot_num 的
      - columns:    自定义返回列(默认全 8 列)
      - conn:       可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 (trade_date DESC, hot_num DESC, con_code) 排序
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("kpl")

    try:
        if columns is None:
            select_cols = KPL_CC_COLS
        else:
            bad = [c for c in columns if c not in KPL_CC_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {KPL_CC_COLS}")
            select_cols = columns

        # ts_codes:None=全部, []=显式空=0行
        if ts_codes is None:
            codes: list = []
        else:
            codes = _norm_to_list(ts_codes, None, "ts_codes")
            if not codes:
                return pd.DataFrame(columns=select_cols)
        theme_list = _norm_to_list(themes, None, "themes")
        desc_list = _norm_to_list(descs, None, "descs")

        wheres = []
        params: list = []

        # 日期:trade_date 优先
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        # ts_codes 实际查 con_code(成分股代码)
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"con_code IN ({placeholders})")
            params.extend(codes)

        # themes 查 name(题材名),精确 IN
        if theme_list:
            placeholders = ",".join(["?"] * len(theme_list))
            wheres.append(f"name IN ({placeholders})")
            params.extend(theme_list)

        # descs:模糊 LIKE,多个 OR
        desc_subwheres = []
        for d in desc_list:
            desc_subwheres.append("desc LIKE ?")
            params.append(f"%{d}%")
        if desc_subwheres:
            wheres.append("(" + " OR ".join(desc_subwheres) + ")")

        # min_hot_num
        if min_hot_num is not None:
            wheres.append("hot_num >= ?")
            params.append(min_hot_num)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = (
            f"SELECT {','.join(select_cols)} FROM {KPL_CC_TABLE}{where_sql} "
            f"ORDER BY trade_date DESC, hot_num DESC, con_code"
        )

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


def get_kpl_limit_performance(
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    ts_codes: Optional[Union[str, Iterable[str]]] = None,
    board_counts: Optional[Union[int, Iterable[int]]] = None,
    themes: Optional[Union[str, Iterable[str]]] = None,
    min_amplitude: Optional[float] = None,
    only_broken: Optional[bool] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读开盘啦涨停表现详情(每只涨停股的完整字段)

    数据源:db_cn_kpl.db / tbl_cn_kpl_limit_performance
    主键: (trade_date, ts_code)
    字段(24 列):完整涨停数据,见 KPL_LP_COLS

    与 get_kpl_list 的区别:
      - get_kpl_list: 来自 tushare kpl_list(第二天早上才更新)
      - 本接口:     来自 kpl API DailyLimitPerformance(实时拉取)
      - 字段差异:  本表含 limit_reason/is_break/amplitude/board_period/close_price 等更细粒字段
      - 主键:      (trade_date, ts_code)

    过滤条件(全部可选,默认 None = 不过滤):
      - start_date / end_date: trade_date 区间
      - trade_date:            单日查询(优先级最高;传了就忽略 start/end_date)
      - ts_codes:              单值(str) 或 列表;None = 全部;精确 IN
      - board_counts:          单值(int) 或 列表;None = 全部;精确 IN 板数(1-5)
      - themes:                单值或列表;None = 全部;**精确 IN theme**
      - min_amplitude:         浮点;None = 不限;只返回 amplitude >= min_amplitude 的
      - only_broken:           bool;None = 不限;True = 只返回 is_break=1(曾炸板);
                                          False = 只返回 is_break=0(封死)
      - columns:               自定义返回列(默认全 24 列)
      - conn:                  可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 (trade_date DESC, board_count DESC, lu_time ASC) 排序
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("kpl")

    try:
        if columns is None:
            select_cols = KPL_LP_COLS
        else:
            bad = [c for c in columns if c not in KPL_LP_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {KPL_LP_COLS}")
            select_cols = columns

        # 日期规范化(函数在本模块)
        sd = _norm_date_yyyymmdd(start_date) if start_date is not None else None
        ed = _norm_date_yyyymmdd(end_date) if end_date is not None else None
        td = _norm_date_yyyymmdd(trade_date) if trade_date is not None else None

        wheres = []
        params = []

        # 日期过滤
        if td:
            wheres.append("trade_date = ?")
            params.append(td)
        else:
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        # ts_codes
        if ts_codes is not None:
            if isinstance(ts_codes, str):
                ts_codes = [ts_codes]
            if len(ts_codes) == 0:
                return pd.DataFrame(columns=select_cols)  # 显式空 = 0 行
            placeholders = ",".join(["?"] * len(ts_codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(ts_codes)

        # board_counts
        if board_counts is not None:
            if isinstance(board_counts, int):
                board_counts = [board_counts]
            if len(board_counts) == 0:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(board_counts))
            wheres.append(f"board_count IN ({placeholders})")
            params.extend(board_counts)

        # themes(精确 IN;theme 是单题材简称,不是 limit_reason 多题材)
        if themes is not None:
            if isinstance(themes, str):
                themes = [themes]
            if len(themes) == 0:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(themes))
            wheres.append(f"theme IN ({placeholders})")
            params.extend(themes)

        # min_amplitude
        if min_amplitude is not None:
            wheres.append("amplitude >= ?")
            params.append(min_amplitude)

        # only_broken
        if only_broken is not None:
            wheres.append("is_break = ?")
            params.append(1 if only_broken else 0)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = (
            f"SELECT {','.join(select_cols)} FROM {KPL_LP_TABLE}{where_sql} "
            f"ORDER BY trade_date DESC, board_count DESC, lu_time ASC"
        )

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 新闻快讯读取(tbl_news)
# ============================================================
NEWS_TABLE = "tbl_news"
NEWS_COLS = ["datetime", "src", "title", "content", "channels", "score",
             "md5", "snap_ts"]


def get_news(
    start_date: Optional[Union[str, datetime, date]] = None,
    end_date: Optional[Union[str, datetime, date]] = None,
    trade_date: Optional[Union[str, datetime, date]] = None,
    start_datetime: Optional[Union[str, datetime]] = None,
    end_datetime: Optional[Union[str, datetime]] = None,
    title: Optional[str] = None,
    content: Optional[str] = None,
    src: Optional[Union[str, Iterable[str]]] = None,
    limit: Optional[int] = 5000,
    offset: int = 0,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读新闻快讯(tbl_news,9 个源的高频快讯)

    数据源:db_cn_news.db / tbl_news
    字段(8 列):
      datetime (YYYY-MM-DD HH:MM:SS,**带时间戳**;主键之一)
      src (主键之一;sina/wallstreetcn/10jqka/eastmoney/jinrongjie/cls/yicai 等)
      title, content, channels, score, md5, snap_ts

    ⚠️ tbl_news 的 datetime 是 'YYYY-MM-DD HH:MM:SS' 格式(不是纯日期!)
    不像 cctv_news 是 'YYYYMMDD'。

    ⚠️ tbl_news 现有 940 万+ 行,全表查询会很慢。**默认 limit=5000**(最近 5000 条),
    分页:limit=5000 offset=5000 取下一页。

    日期过滤(多种模式,见优先级):
      - trade_date: 单日查询,自动扩展为 [当天 00:00:00, 23:59:59]
                    (优先级最高;传了就忽略 start_*/end_* 系列)
      - start_datetime / end_datetime: 精确时间(含时分秒),datetime 直接比较
      - start_date / end_date: 自动补 00:00:00 / 23:59:59
                                (start_date=end_date=同一日期等价 trade_date)

    互斥校验:
      - trade_date 不能和 start_datetime / end_datetime 同时用
        (语义冲突:一个是"当天整天",一个是"精确时间")

    其他过滤:
      - title:    str;模糊 LIKE '%title%'
      - content:  str;模糊 LIKE '%content%'
      - src:      单值(str) 或 列表;None = 全部源;精确 IN
      - limit:    int;最大返回行数;None=不限;默认 5000
      - offset:   int;跳过前 N 行(分页用);默认 0

    Returns:
        pd.DataFrame,按 datetime DESC 排序,limit/offset 应用于排序后
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("news")

    try:
        if columns is None:
            select_cols = NEWS_COLS
        else:
            bad = [c for c in columns if c not in NEWS_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {NEWS_COLS}")
            select_cols = columns

        # 规范化 src
        src_list = _norm_to_list(src, None, "src")

        # === 日期范围解析 ===
        # 优先级:trade_date > start_datetime/end_datetime > start_date/end_date
        has_trade = trade_date is not None
        has_dt = (start_datetime is not None) or (end_datetime is not None)
        has_date = (start_date is not None) or (end_date is not None)
        if has_trade and has_dt:
            raise ValueError("trade_date 和 start_datetime/end_datetime 互斥,不能同时用")

        # 把所有可能的时间范围归一到 (dt_start, dt_end) 两个字符串
        dt_start: Optional[str] = None
        dt_end: Optional[str] = None

        if has_trade:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                # YYYYMMDD → 'YYYY-MM-DD 00:00:00' 和 'YYYY-MM-DD 23:59:59'
                date_part = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
                dt_start = f"{date_part} 00:00:00"
                dt_end = f"{date_part} 23:59:59"
        elif has_dt:
            dt_start = _norm_datetime_string(start_datetime)
            dt_end = _norm_datetime_string(end_datetime)
        elif has_date:
            sd = _norm_date_yyyymmdd(start_date) if start_date is not None else None
            ed = _norm_date_yyyymmdd(end_date) if end_date is not None else None
            if sd:
                date_part = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}"
                dt_start = f"{date_part} 00:00:00"
            if ed:
                date_part = f"{ed[:4]}-{ed[4:6]}-{ed[6:8]}"
                dt_end = f"{date_part} 23:59:59"

        wheres = []
        params: list = []

        # datetime 范围
        if dt_start is not None:
            wheres.append("datetime >= ?")
            params.append(dt_start)
        if dt_end is not None:
            wheres.append("datetime <= ?")
            params.append(dt_end)

        # src
        if src_list:
            placeholders = ",".join(["?"] * len(src_list))
            wheres.append(f"src IN ({placeholders})")
            params.extend(src_list)

        # title / content 模糊
        if title is not None and title != "":
            wheres.append("title LIKE ?")
            params.append(f"%{title}%")
        if content is not None and content != "":
            wheres.append("content LIKE ?")
            params.append(f"%{content}%")

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = (
            f"SELECT {','.join(select_cols)} FROM {NEWS_TABLE}{where_sql} "
            f"ORDER BY datetime DESC"
        )
        # LIMIT/OFFSET 分页(默认 5000,None=不限)
        params_for_pagination = []
        if limit is not None:
            sql += " LIMIT ?"
            params_for_pagination.append(int(limit))
        if offset and offset > 0:
            sql += " OFFSET ?"
            params_for_pagination.append(int(offset))
        params = params + params_for_pagination

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# CCTV 新闻联播读取
# ============================================================
CCTV_NEWS_TABLE = "tbl_cctv_news"
CCTV_NEWS_COLS = ["datetime", "title", "content", "src", "md5", "snap_ts"]


def get_cctv_news(
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    content: Optional[str] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读 CCTV 新闻联播

    数据源:db_cn_news.db / tbl_cctv_news
    字段(6 列):datetime (YYYYMMDD,**cctv 接口没有时间字段**),
          title, content, src (基本固定 'cctv'),
          md5 (内容哈希), snap_ts

    ⚠️ cctv_news 接口一次只返回一天的新闻联播,datetime 是 YYYYMMDD 字符串
    (不像 tbl_news/tbl_major_news 是 YYYY-MM-DD HH:MM:SS)。
    一天可能多条(联播通常一次,但偶尔有重播/补录),
    主键 (datetime, md5) 自动去重。

    过滤条件(全部可选,默认 None = 不过滤):
      - start_date / end_date: datetime 区间(YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD)
      - trade_date: 单日查询(优先级最高;传了就忽略 start/end_date)
      - content:    str;None = 不过滤;**模糊 LIKE '%content%'**
                    例 content='习近平' 匹配所有 content 字段含 '习近平' 的联播
      - columns:    自定义返回列(默认全 6 列)
      - conn:       可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 datetime DESC 排序
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("news")

    try:
        if columns is None:
            select_cols = CCTV_NEWS_COLS
        else:
            bad = [c for c in columns if c not in CCTV_NEWS_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {CCTV_NEWS_COLS}")
            select_cols = columns

        wheres = []
        params: list = []

        # 日期:trade_date 优先
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("datetime = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("datetime >= ?")
                params.append(sd)
            if ed:
                wheres.append("datetime <= ?")
                params.append(ed)

        # content:模糊 LIKE
        if content is not None and content != "":
            wheres.append("content LIKE ?")
            params.append(f"%{content}%")

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = (
            f"SELECT {','.join(select_cols)} FROM {CCTV_NEWS_TABLE}{where_sql} "
            f"ORDER BY datetime DESC"
        )

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 新闻源断点读取(tbl_news_ctrl)
# ============================================================
NEWS_CTRL_TABLE = "tbl_news_ctrl"
NEWS_CTRL_COLS = ["src", "max_date", "updated_at"]


def get_news_ctrl(
    src: Optional[Union[str, Iterable[str]]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读 news 库的 tbl_news_ctrl 断点表

    数据源:db_cn_news.db / tbl_news_ctrl
    字段:src (主键,代表一个新闻源), max_date, updated_at

    Args:
        src:    None = 返回全部断点(11 行:9 个 news 源 + cctv_news + major_news)
                单值(str) = 精确匹配一个 src(例 'sina')
                列表 = 精确匹配多个 src(例 ['sina','yicai'])
        conn:   可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 src 升序
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("news")

    try:
        # src=None = 全部; src=[] = 显式空 = 0 行
        if src is None:
            sql = f"SELECT {','.join(NEWS_CTRL_COLS)} FROM {NEWS_CTRL_TABLE} ORDER BY src"
            params: list = []
        else:
            srcs = _norm_to_list(src, None, "src")
            if not srcs:
                # 显式空列表 → 0 行
                return pd.DataFrame(columns=NEWS_CTRL_COLS)
            placeholders = ",".join(["?"] * len(srcs))
            sql = f"SELECT {','.join(NEWS_CTRL_COLS)} FROM {NEWS_CTRL_TABLE} WHERE src IN ({placeholders}) ORDER BY src"
            params = srcs

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=NEWS_CTRL_COLS)
        df = df[NEWS_CTRL_COLS]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 长新闻读取(tbl_major_news)
# ============================================================
MAJOR_NEWS_TABLE = "tbl_major_news"
MAJOR_NEWS_COLS = ["datetime", "src", "title", "content", "channels", "score",
                   "md5", "snap_ts"]


# ============================================================
# ============================================================
# 每日停复牌信息读取(tbl_cn_suspend)
# ============================================================
SUSPEND_TABLE = "tbl_cn_suspend"
SUSPEND_COLS = ["trade_date", "ts_code", "suspend_timing", "suspend_type", "snap_ts"]


def get_suspend(
    ts_code=None,
    ts_codes=None,
    start_date=None,
    end_date=None,
    trade_date=None,
    suspend_type=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读每日停复牌信息

    数据源:db_cn_basic.db / tbl_cn_suspend
    主键:(trade_date, ts_code)
    字段(5):trade_date, ts_code, suspend_timing, suspend_type, snap_ts

    重要:停牌期间每天都有一行(覆盖从停牌第一天到复牌前一天)

    过滤条件(全部可选):
      - ts_code / ts_codes: 单只 / 多只
      - start_date / end_date: 区间
      - trade_date: 单日(优先级最高)
      - suspend_type: S=停牌 / R=复牌
      - columns: 自定义列
      - conn: 可选外部连接

    Returns:
        pd.DataFrame,按 (trade_date DESC, ts_code) 升序
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")
    try:
        all_cols = SUSPEND_COLS
        if columns is None:
            select_cols = all_cols
        else:
            bad = [c for c in columns if c not in all_cols]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {all_cols}")
            select_cols = columns

        wheres = []
        params: list = []

        if ts_code is not None and ts_codes is not None:
            raise ValueError("ts_code 和 ts_codes 互斥")

        if ts_code is not None:
            wheres.append("ts_code = ?")
            params.append(ts_code)
        elif ts_codes is not None:
            codes = list(ts_codes)
            if not codes:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(codes)

        # 日期条件
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        # 类型条件
        if suspend_type is not None:
            wheres.append("suspend_type = ?")
            params.append(suspend_type)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT {','.join(select_cols)} FROM {SUSPEND_TABLE}{where_sql} ORDER BY trade_date DESC, ts_code"

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 龙虎榜每日活跃读取(tbl_cn_top_list)
# ============================================================
TOP_LIST_TABLE = "tbl_cn_top_list"
TOP_LIST_COLS = ["trade_date", "ts_code", "name", "close", "pct_change", "turnover_rate",
                 "amount", "l_sell", "l_buy", "l_amount", "net_amount", "net_rate",
                 "amount_rate", "float_values", "reason", "snap_ts"]


def get_top_list(
    ts_code=None,
    ts_codes=None,
    start_date=None,
    end_date=None,
    trade_date=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读龙虎榜每日活跃

    数据源:db_cn_kpl.db / tbl_cn_top_list
    主键:(trade_date, ts_code)
    字段(16):见 TOP_LIST_COLS
    """
    return _get_top(TOP_LIST_TABLE, TOP_LIST_COLS,
                    ts_code, ts_codes, start_date, end_date, trade_date,
                    columns, conn)


# ============================================================
# 龙虎榜机构买卖明细读取(tbl_cn_top_inst)
# ============================================================
TOP_INST_TABLE = "tbl_cn_top_inst"
TOP_INST_COLS = ["trade_date", "ts_code", "exalter", "buy", "buy_rate", "sell",
                 "sell_rate", "net_buy", "side", "reason", "snap_ts"]


def get_top_inst(
    ts_code=None,
    ts_codes=None,
    start_date=None,
    end_date=None,
    trade_date=None,
    exalter=None,
    side=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读龙虎榜机构买卖明细

    数据源:db_cn_kpl.db / tbl_cn_top_inst
    主键:(trade_date, ts_code, exalter, side)
    字段(11):见 TOP_INST_COLS
    """
    return _get_top(TOP_INST_TABLE, TOP_INST_COLS,
                    ts_code, ts_codes, start_date, end_date, trade_date,
                    columns, conn, exalter=exalter, side=side)


def _get_top(table, all_cols, ts_code, ts_codes, start_date, end_date,
              trade_date, columns, conn, exalter=None, side=None) -> pd.DataFrame:
    """top_list / top_inst 共用读取实现(放在 kpl DB)"""
    own_conn = conn is None
    if own_conn:
        conn = get_conn("kpl")  # ← kpl DB(用户要求)
    try:
        if columns is None:
            select_cols = all_cols
        else:
            bad = [c for c in columns if c not in all_cols]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {all_cols}")
            select_cols = columns

        wheres = []
        params: list = []

        if ts_code is not None and ts_codes is not None:
            raise ValueError("ts_code 和 ts_codes 互斥")

        if ts_code is not None:
            wheres.append("ts_code = ?")
            params.append(ts_code)
        elif ts_codes is not None:
            codes = list(ts_codes)
            if not codes:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(codes)

        # 日期条件
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        # top_inst 专属过滤
        if exalter is not None:
            wheres.append("exalter = ?")
            params.append(exalter)
        if side is not None:
            wheres.append("side = ?")
            params.append(side)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT {','.join(select_cols)} FROM {table}{where_sql} ORDER BY trade_date DESC, ts_code"

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 大宗交易读取(tbl_cn_block_trade)
# ============================================================
BLOCK_TRADE_TABLE = "tbl_cn_block_trade"
BLOCK_TRADE_COLS = ["trade_date", "ts_code", "price", "vol", "amount", "buyer", "seller", "snap_ts"]


def get_block_trade(
    ts_code=None,
    start_date=None,
    end_date=None,
    trade_date=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读大宗交易

    数据源:db_cn_kpl.db / tbl_cn_block_trade
    主键:(trade_date, ts_code, buyer, seller)
    """
    return _get_paged_top(BLOCK_TRADE_TABLE, BLOCK_TRADE_COLS,
                           ts_code, start_date, end_date, trade_date, columns, conn)


# ============================================================
# 港股通每日成交读取(tbl_cn_ggt_daily)
# ============================================================
GGT_DAILY_TABLE = "tbl_cn_ggt_daily"
GGT_DAILY_COLS = ["trade_date", "buy_amount", "buy_volume", "sell_amount", "sell_volume", "snap_ts"]


def get_ggt_daily(
    start_date=None,
    end_date=None,
    trade_date=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读港股通每日成交

    数据源:db_cn_kpl.db / tbl_cn_ggt_daily
    主键:trade_date(单 PK)
    """
    return _get_paged_top(GGT_DAILY_TABLE, GGT_DAILY_COLS,
                           None, start_date, end_date, trade_date, columns, conn)


# ============================================================
# 沪深股通十大成交股读取(tbl_cn_hsgt_top10)
# ============================================================
HSGT_TOP10_TABLE = "tbl_cn_hsgt_top10"
HSGT_TOP10_COLS = ["trade_date", "ts_code", "name", "close", "change", "rank",
                    "market_type", "amount", "net_amount", "buy", "sell", "snap_ts"]


def get_hsgt_top10(
    ts_code=None,
    start_date=None,
    end_date=None,
    trade_date=None,
    market_type=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读沪深股通十大成交股

    数据源:db_cn_kpl.db / tbl_cn_hsgt_top10
    主键:(trade_date, ts_code, market_type)
    """
    return _get_paged_top(HSGT_TOP10_TABLE, HSGT_TOP10_COLS,
                           ts_code, start_date, end_date, trade_date,
                           columns, conn, market_type=market_type)


# ============================================================
# 每日涨跌停列表读取(tbl_cn_limit_list)
# ============================================================
LIMIT_LIST_TABLE = "tbl_cn_limit_list"
LIMIT_LIST_COLS = ["trade_date", "ts_code", "industry", "name", "close", "pct_chg",
                    "amount", "limit_amount", "float_mv", "total_mv", "turnover_ratio",
                    "fd_amount", "first_time", "last_time", "open_times", "up_stat",
                    "limit_times", "limit", "snap_ts"]


def get_limit_list(
    ts_code=None,
    start_date=None,
    end_date=None,
    trade_date=None,
    limit=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读每日涨跌停列表

    数据源:db_cn_kpl.db / tbl_cn_limit_list
    主键:(trade_date, ts_code, limit)
    limit: U=涨停 / D=跌停 / Z=炸板
    """
    return _get_paged_top(LIMIT_LIST_TABLE, LIMIT_LIST_COLS,
                           ts_code, start_date, end_date, trade_date,
                           columns, conn, limit=limit)


def _get_paged_top(table, all_cols, ts_code, start_date, end_date,
                    trade_date, columns, conn, market_type=None, limit=None) -> pd.DataFrame:
    """block_trade / ggt_daily / hsgt_top10 / limit_list 共用读取(放在 kpl DB)"""
    own_conn = conn is None
    if own_conn:
        conn = get_conn("kpl")
    try:
        if columns is None:
            select_cols = all_cols
        else:
            bad = [c for c in columns if c not in all_cols]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {all_cols}")
            select_cols = columns

        wheres = []
        params: list = []

        if ts_code is not None:
            wheres.append("ts_code = ?")
            params.append(ts_code)

        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        if market_type is not None:
            wheres.append("market_type = ?")
            params.append(market_type)

        if limit is not None:
            wheres.append('`limit` = ?')
            params.append(limit)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        # 给列名加反引号(SQLite 保留字 limit 等需要)
        quoted_cols = ",".join(f"`{c}`" for c in select_cols)
        sql = f"SELECT {quoted_cols} FROM {table}{where_sql} ORDER BY trade_date DESC"
        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 每日涨跌停价格读取(tbl_cn_stk_limit)
# ============================================================
STK_LIMIT_TABLE = "tbl_cn_stk_limit"
STK_LIMIT_COLS = ["trade_date", "ts_code", "up_limit", "down_limit", "snap_ts"]


def get_stk_limit(
    ts_code: Optional[str] = None,
    ts_codes: Optional[Iterable[str]] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    trade_date: Optional[Union[str, datetime]] = None,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读每日涨跌停价格

    数据源:db_cn_basic.db / tbl_cn_stk_limit
    主键:(trade_date, ts_code)
    字段(5):trade_date, ts_code, up_limit, down_limit, snap_ts

    过滤条件(全部可选,默认 None = 不过滤):
      - ts_code: 单只股票,例 "000001.SZ"
      - ts_codes: 多只股票,例 ["000001.SZ", "600000.SH"]
      - start_date: 起始日期(包含),YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD
      - end_date: 结束日期(包含),同上
      - trade_date: 精确查某一天;传了就忽略 start/end_date
      - columns: 自定义返回列(默认全 5 列)
      - conn: 可选外部 sqlite3 连接

    Returns:
        pd.DataFrame,按 (trade_date DESC, ts_code) 升序;空表返回空 DataFrame
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    try:
        if columns is None:
            select_cols = STK_LIMIT_COLS
        else:
            bad = [c for c in columns if c not in STK_LIMIT_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {STK_LIMIT_COLS}")
            select_cols = columns

        wheres = []
        params: list = []

        if ts_code is not None and ts_codes is not None:
            raise ValueError("ts_code 和 ts_codes 互斥,只能传一个")

        if ts_code is not None:
            wheres.append("ts_code = ?")
            params.append(ts_code)
        elif ts_codes is not None:
            codes = list(ts_codes)
            if not codes:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(codes)

        # 日期条件:trade_date 优先
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT {','.join(select_cols)} FROM {STK_LIMIT_TABLE}{where_sql} ORDER BY trade_date DESC, ts_code"

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 个股资金流向读取(tbl_cn_moneyflow)
# ============================================================
MONEYFLOW_TABLE = "tbl_cn_moneyflow"
MONEYFLOW_COLS = [
    "trade_date", "ts_code",
    "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
    "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
    "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
    "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
    "net_mf_vol", "net_mf_amount", "snap_ts",
]


def get_moneyflow(
    ts_code=None,
    ts_codes=None,
    start_date=None,
    end_date=None,
    trade_date=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读个股资金流向(放在 basic DB)

    数据源:db_cn_basic.db / tbl_cn_moneyflow
    主键:(trade_date, ts_code)
    字段(20):见 MONEYFLOW_COLS
    含义:小/中/大/特大单买卖(数量+金额)+ 净流入(手/万元)
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")
    try:
        if columns is None:
            select_cols = MONEYFLOW_COLS
        else:
            bad = [c for c in columns if c not in MONEYFLOW_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {MONEYFLOW_COLS}")
            select_cols = columns

        wheres = []
        params: list = []

        if ts_code is not None and ts_codes is not None:
            raise ValueError("ts_code 和 ts_codes 互斥")

        if ts_code is not None:
            wheres.append("ts_code = ?")
            params.append(ts_code)
        elif ts_codes is not None:
            codes = list(ts_codes)
            if not codes:
                return pd.DataFrame(columns=select_cols)
            placeholders = ",".join(["?"] * len(codes))
            wheres.append(f"ts_code IN ({placeholders})")
            params.extend(codes)

        # 日期条件
        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        # 给列名加反引号(SQLite 保留字 limit 等需要,虽然 moneyflow 没有保留字但保持一致)
        quoted_cols = ",".join(f"`{c}`" for c in select_cols)
        sql = f"SELECT {quoted_cols} FROM {MONEYFLOW_TABLE}{where_sql} ORDER BY trade_date DESC, ts_code"
        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 融资融券交易汇总读取(tbl_cn_margin)
# ============================================================
MARGIN_TABLE = "tbl_cn_margin"
MARGIN_COLS = ["trade_date", "exchange_id", "rzye", "rzmre", "rzche",
                "rqye", "rqmcl", "rzrqye", "rqyl", "snap_ts"]


def get_margin(
    exchange_id=None,
    start_date=None,
    end_date=None,
    trade_date=None,
    columns=None,
    conn=None,
) -> pd.DataFrame:
    """读融资融券交易汇总(放在 basic DB)

    数据源:db_cn_basic.db / tbl_cn_margin
    主键:(trade_date, exchange_id)
    字段(10):见 MARGIN_COLS
    含义:融资余额/买入/偿还 + 融券余额/卖出量/余量 + 融资融券余额
    exchange_id: SSE / SZSE / BSE
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")
    try:
        if columns is None:
            select_cols = MARGIN_COLS
        else:
            bad = [c for c in columns if c not in MARGIN_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {MARGIN_COLS}")
            select_cols = columns

        wheres = []
        params: list = []

        if exchange_id is not None:
            wheres.append("exchange_id = ?")
            params.append(exchange_id)

        if trade_date is not None:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                wheres.append("trade_date = ?")
                params.append(td)
        else:
            sd = _norm_date_yyyymmdd(start_date)
            ed = _norm_date_yyyymmdd(end_date)
            if sd:
                wheres.append("trade_date >= ?")
                params.append(sd)
            if ed:
                wheres.append("trade_date <= ?")
                params.append(ed)

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT {','.join(select_cols)} FROM {MARGIN_TABLE}{where_sql} ORDER BY trade_date DESC, exchange_id"
        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 融资融券交易明细读取(tbl_cn_margin_detail)
# ============================================================
def get_margin_detail(
    ts_code: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, datetime, date]] = None,
    end_date: Optional[Union[str, datetime, date]] = None,
    trade_date: Optional[Union[str, datetime, date]] = None,
    columns: Optional[List[str]] = None,
    conn=None,
) -> pd.DataFrame:
    """读取融资融券交易明细(2026-09-15 新增,basic DB)

    Args:
        ts_code: 单只或列表(000001.SZ / ['000001.SZ', '600519.SH'])
        start_date / end_date / trade_date: 日期过滤
        columns: 指定返回列(默认全部)
        conn: 可选,传入已有 basic 连接避免重开

    Returns:
        DataFrame, 11 列(含 snap_ts)
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    # 默认列
    if columns is None:
        columns = [
            "trade_date", "ts_code",
            "rzye", "rqye", "rzmre", "rqyl", "rzche", "rqchl", "rqmcl", "rzrqye",
            "snap_ts",
        ]

    where, params = [], []
    if ts_code is not None:
        codes = _norm_to_list(ts_code)
        if len(codes) == 1:
            where.append("ts_code = ?")
            params.append(codes[0])
        else:
            placeholders = ",".join("?" for _ in codes)
            where.append(f"ts_code IN ({placeholders})")
            params.extend(codes)
    if start_date:
        where.append("trade_date >= ?")
        params.append(_norm_date_yyyymmdd(start_date))
    if end_date:
        where.append("trade_date <= ?")
        params.append(_norm_date_yyyymmdd(end_date))
    if trade_date:
        td = _norm_date_yyyymmdd(trade_date)
        where.append("trade_date = ?")
        params.append(td)

    sql = f'SELECT {",".join(columns)} FROM tbl_cn_margin_detail'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY trade_date DESC, ts_code"

    df = pd.read_sql_query(sql, conn, params=tuple(params))

    if own_conn:
        conn.close()
    return df


# ============================================================
# 每日筹码及胜率读取(tbl_cn_cyq_perf)
# ============================================================
def get_cyq_perf(
    ts_code: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, datetime, date]] = None,
    end_date: Optional[Union[str, datetime, date]] = None,
    trade_date: Optional[Union[str, datetime, date]] = None,
    min_winner_rate: Optional[float] = None,
    max_winner_rate: Optional[float] = None,
    columns: Optional[List[str]] = None,
    conn=None,
) -> pd.DataFrame:
    """读取每日筹码及胜率(2026-09-15 新增,basic DB)

    Args:
        ts_code: 单只或列表
        start_date / end_date / trade_date: 日期过滤
        min_winner_rate / max_winner_rate: 胜率范围过滤(%)
        columns: 指定返回列(默认全部)
        conn: 可选,传入已有 basic 连接避免重开

    Returns:
        DataFrame, 12 列(含 snap_ts)
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    # 默认列
    if columns is None:
        columns = [
            "trade_date", "ts_code",
            "his_low", "his_high",
            "cost_5pct", "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct",
            "weight_avg", "winner_rate",
            "snap_ts",
        ]

    where, params = [], []
    if ts_code is not None:
        codes = _norm_to_list(ts_code)
        if len(codes) == 1:
            where.append("ts_code = ?")
            params.append(codes[0])
        else:
            placeholders = ",".join("?" for _ in codes)
            where.append(f"ts_code IN ({placeholders})")
            params.extend(codes)
    if start_date:
        where.append("trade_date >= ?")
        params.append(_norm_date_yyyymmdd(start_date))
    if end_date:
        where.append("trade_date <= ?")
        params.append(_norm_date_yyyymmdd(end_date))
    if trade_date:
        td = _norm_date_yyyymmdd(trade_date)
        where.append("trade_date = ?")
        params.append(td)
    if min_winner_rate is not None:
        where.append("winner_rate >= ?")
        params.append(min_winner_rate)
    if max_winner_rate is not None:
        where.append("winner_rate <= ?")
        params.append(max_winner_rate)

    sql = f'SELECT {",".join(columns)} FROM tbl_cn_cyq_perf'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY trade_date DESC, ts_code"

    df = pd.read_sql_query(sql, conn, params=tuple(params))

    if own_conn:
        conn.close()
    return df


# ============================================================
# 每日指标读取(tbl_cn_daily_basic)
# ============================================================
def get_daily_basic(
    ts_code: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, datetime, date]] = None,
    end_date: Optional[Union[str, datetime, date]] = None,
    trade_date: Optional[Union[str, datetime, date]] = None,
    columns: Optional[List[str]] = None,
    conn=None,
) -> pd.DataFrame:
    """读取每日指标(2026-09-15 新增,basic DB)

    Args:
        ts_code: 单只或列表
        start_date / end_date / trade_date: 日期过滤
        columns: 指定返回列(默认全部)
        conn: 可选,传入已有 basic 连接避免重开

    Returns:
        DataFrame, 19 列(含 snap_ts)
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("basic")

    # 默认列
    if columns is None:
        columns = [
            "trade_date", "ts_code", "close",
            "turnover_rate", "turnover_rate_f", "volume_ratio",
            "pe", "pe_ttm", "pb", "ps", "ps_ttm",
            "dv_ratio", "dv_ttm",
            "total_share", "float_share", "free_share",
            "total_mv", "circ_mv",
            "snap_ts",
        ]

    where, params = [], []
    if ts_code is not None:
        codes = _norm_to_list(ts_code)
        if len(codes) == 1:
            where.append("ts_code = ?")
            params.append(codes[0])
        else:
            placeholders = ",".join("?" for _ in codes)
            where.append(f"ts_code IN ({placeholders})")
            params.extend(codes)
    if start_date:
        where.append("trade_date >= ?")
        params.append(_norm_date_yyyymmdd(start_date))
    if end_date:
        where.append("trade_date <= ?")
        params.append(_norm_date_yyyymmdd(end_date))
    if trade_date:
        td = _norm_date_yyyymmdd(trade_date)
        where.append("trade_date = ?")
        params.append(td)

    sql = f'SELECT {",".join(columns)} FROM tbl_cn_daily_basic'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY trade_date DESC, ts_code"

    df = pd.read_sql_query(sql, conn, params=tuple(params))

    if own_conn:
        conn.close()
    return df


# ============================================================
# 指数基本信息读取(tbl_cn_index_basic)
# ============================================================
def get_index_basic(
    ts_code: Optional[Union[str, List[str]]] = None,
    market: Optional[Union[str, List[str]]] = None,
    publisher: Optional[Union[str, List[str]]] = None,
    columns: Optional[List[str]] = None,
    conn=None,
) -> pd.DataFrame:
    """读取指数基本信息(2026-09-15 新增,index DB)

    Args:
        ts_code: 单个指数代码或列表
        market: 市场过滤('SW' / 'CSI' / 'MSCI' / 'SSE' / 'SZSE')
        publisher: 发布方过滤
        columns: 指定返回列
        conn: 可选,传入已有 index 连接避免重开

    Returns:
        DataFrame, 9 列(含 snap_ts)
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("index")

    # 默认列
    if columns is None:
        columns = [
            "ts_code", "name", "market", "publisher", "category",
            "base_date", "base_point", "list_date", "snap_ts",
        ]

    where, params = [], []
    if ts_code is not None:
        codes = _norm_to_list(ts_code)
        if len(codes) == 1:
            where.append("ts_code = ?")
            params.append(codes[0])
        else:
            placeholders = ",".join("?" for _ in codes)
            where.append(f"ts_code IN ({placeholders})")
            params.extend(codes)
    if market is not None:
        markets = _norm_to_list(market)
        if len(markets) == 1:
            where.append("market = ?")
            params.append(markets[0])
        else:
            placeholders = ",".join("?" for _ in markets)
            where.append(f"market IN ({placeholders})")
            params.extend(markets)
    if publisher is not None:
        pubs = _norm_to_list(publisher)
        if len(pubs) == 1:
            where.append("publisher = ?")
            params.append(pubs[0])
        else:
            placeholders = ",".join("?" for _ in pubs)
            where.append(f"publisher IN ({placeholders})")
            params.extend(pubs)

    sql = f'SELECT {",".join(columns)} FROM tbl_cn_index_basic'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts_code"

    df = pd.read_sql_query(sql, conn, params=tuple(params))

    if own_conn:
        conn.close()
    return df


# ============================================================
# 指数日线行情读取(tbl_cn_index_daily)
# ============================================================
def get_index_daily(
    ts_code: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, datetime, date]] = None,
    end_date: Optional[Union[str, datetime, date]] = None,
    trade_date: Optional[Union[str, datetime, date]] = None,
    columns: Optional[List[str]] = None,
    conn=None,
) -> pd.DataFrame:
    """读取指数日线行情(2026-09-15 新增,index DB)

    Args:
        ts_code: 单个指数代码或列表(如 '000001.SH')
        start_date / end_date / trade_date: 日期过滤
        columns: 指定返回列
        conn: 可选,传入已有 index 连接避免重开

    Returns:
        DataFrame, 12 列(含 snap_ts)
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("index")

    # 默认列
    if columns is None:
        columns = [
            "trade_date", "ts_code",
            "open", "high", "low", "close", "pre_close",
            "change", "pct_chg", "vol", "amount",
            "snap_ts",
        ]

    where, params = [], []
    if ts_code is not None:
        codes = _norm_to_list(ts_code)
        if len(codes) == 1:
            where.append("ts_code = ?")
            params.append(codes[0])
        else:
            placeholders = ",".join("?" for _ in codes)
            where.append(f"ts_code IN ({placeholders})")
            params.extend(codes)
    if start_date:
        where.append("trade_date >= ?")
        params.append(_norm_date_yyyymmdd(start_date))
    if end_date:
        where.append("trade_date <= ?")
        params.append(_norm_date_yyyymmdd(end_date))
    if trade_date:
        td = _norm_date_yyyymmdd(trade_date)
        where.append("trade_date = ?")
        params.append(td)

    sql = f'SELECT {",".join(columns)} FROM tbl_cn_index_daily'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY trade_date DESC, ts_code"

    df = pd.read_sql_query(sql, conn, params=tuple(params))

    if own_conn:
        conn.close()
    return df


def get_major_news(
    start_date: Optional[Union[str, datetime, date]] = None,
    end_date: Optional[Union[str, datetime, date]] = None,
    trade_date: Optional[Union[str, datetime, date]] = None,
    start_datetime: Optional[Union[str, datetime]] = None,
    end_datetime: Optional[Union[str, datetime]] = None,
    title: Optional[str] = None,
    content: Optional[str] = None,
    src: Optional[Union[str, Iterable[str]]] = None,
    limit: Optional[int] = 5000,
    offset: int = 0,
    columns: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> pd.DataFrame:
    """读长新闻(tbl_major_news,Tushare 'major_news' 源)

    数据源:db_cn_news.db / tbl_major_news
    字段(8 列,跟 tbl_news 一模一样):
      datetime (YYYY-MM-DD HH:MM:SS,**带时间戳**;主键之一)
      src (主键之一;基本固定 'major_news')
      title, content, channels, score, md5, snap_ts

    ⚠️ tbl_major_news 现有 145 万+ 行,**默认 limit=5000**(最近 5000 条),
    分页:limit=5000 offset=5000 取下一页。

    接口参数、行为、优先级与 get_news 完全一致,只是表不同:
      - 日期过滤:trade_date(单日) / start_datetime+end_datetime(精确) /
                  start_date+end_date(自动补 00:00:00 / 23:59:59)
      - 模糊:title / content
      - 精确:src(单值/列表,None=全部)
      - 分页:limit / offset
      - 互斥:trade_date 不能和 *_datetime 同时用
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn("news")

    try:
        if columns is None:
            select_cols = MAJOR_NEWS_COLS
        else:
            bad = [c for c in columns if c not in MAJOR_NEWS_COLS]
            if bad:
                raise ValueError(f"非法列名: {bad};允许: {MAJOR_NEWS_COLS}")
            select_cols = columns

        # 规范化 src
        src_list = _norm_to_list(src, None, "src")

        # === 日期范围解析(同 get_news) ===
        has_trade = trade_date is not None
        has_dt = (start_datetime is not None) or (end_datetime is not None)
        has_date = (start_date is not None) or (end_date is not None)
        if has_trade and has_dt:
            raise ValueError("trade_date 和 start_datetime/end_datetime 互斥,不能同时用")

        dt_start: Optional[str] = None
        dt_end: Optional[str] = None

        if has_trade:
            td = _norm_date_yyyymmdd(trade_date)
            if td:
                date_part = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
                dt_start = f"{date_part} 00:00:00"
                dt_end = f"{date_part} 23:59:59"
        elif has_dt:
            dt_start = _norm_datetime_string(start_datetime)
            dt_end = _norm_datetime_string(end_datetime)
        elif has_date:
            sd = _norm_date_yyyymmdd(start_date) if start_date is not None else None
            ed = _norm_date_yyyymmdd(end_date) if end_date is not None else None
            if sd:
                date_part = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}"
                dt_start = f"{date_part} 00:00:00"
            if ed:
                date_part = f"{ed[:4]}-{ed[4:6]}-{ed[6:8]}"
                dt_end = f"{date_part} 23:59:59"

        wheres = []
        params: list = []

        if dt_start is not None:
            wheres.append("datetime >= ?")
            params.append(dt_start)
        if dt_end is not None:
            wheres.append("datetime <= ?")
            params.append(dt_end)

        if src_list:
            placeholders = ",".join(["?"] * len(src_list))
            wheres.append(f"src IN ({placeholders})")
            params.extend(src_list)

        if title is not None and title != "":
            wheres.append("title LIKE ?")
            params.append(f"%{title}%")
        if content is not None and content != "":
            wheres.append("content LIKE ?")
            params.append(f"%{content}%")

        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = (
            f"SELECT {','.join(select_cols)} FROM {MAJOR_NEWS_TABLE}{where_sql} "
            f"ORDER BY datetime DESC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        if offset and offset > 0:
            sql += " OFFSET ?"
            params.append(int(offset))

        df = pd.read_sql_query(sql, conn, params=params)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=select_cols)
        for col in select_cols:
            if col not in df.columns:
                df[col] = None
        df = df[select_cols]
        return df
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 测试
# ============================================================
if __name__ == "__main__":
    print("=== 初始化 3 个 DB ===")
    for db_name in ["basic", "kpl", "news"]:
        init_db(db_name)
    show_status()

    print("\n=== get_day smoke test ===")
    # 单只 + 单日 + qfq
    d1 = get_day(ts_code="000001.SZ", trade_date="20240901")
    print(f"1) 单股单日 qfq: {len(d1)} 行")
    print(d1.head().to_string(index=False))

    # 单只 + 区间 + 不复权
    d2 = get_day(ts_code="000001.SZ", start_date="20240901", end_date="20240910", qfq=False)
    print(f"\n2) 单股区间不复权: {len(d2)} 行")

    # 多只 + 区间 + qfq
    d3 = get_day(ts_codes=["000001.SZ", "600000.SH"], start_date="2024-09-01", end_date="2024-09-05")
    print(f"\n3) 多股区间 qfq: {len(d3)} 行")
    print(d3.head().to_string(index=False))
