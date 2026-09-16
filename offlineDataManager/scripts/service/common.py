"""
~/TradingAgent/offlineDataManager/scripts/service/common.py
service 通用工具:日期范围解析(根据 MyATM 思路)
"""
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))

from loguru import logger


def resolve_date_range(conn, ctrl_key: str, args, default_start: str = "20150101") -> tuple:
    """根据 args + ctrl 决定实际拉取日期范围

    优先级:
    1. --trade-date(单日,start_date = end_date = trade_date)
    2. --start-date + --end-date(日期段)
    3. 增量(默认 start_date=2015-01-01, end_date=今天)

    start_date 必须 ≥ tbl_ctrl 里的日期 + 1(避免重拉已有数据)

    Args:
        conn: SQLite 连接(用来读 tbl_ctrl)
        ctrl_key: tbl_ctrl 里对应的 key
        args: argparse 参数,需要含 trade_date / start_date / end_date
        default_start: 默认 start_date(YYYYMMDD)

    Returns:
        (start_date, end_date, desc) 三元组
    """
    from core.offline_db_client import get_ctrl

    today = datetime.now().strftime("%Y%m%d")

    # 1. --trade-date 优先(单日)
    if getattr(args, "trade_date", None):
        td = args.trade_date
        return td, td, f"单日 {td}"

    # 2. --start-date + --end-date(日期段)
    # 兼容老的 --start / --end(week / month 用)
    sd = (getattr(args, "start_date", None) or
          getattr(args, "start", None) or
          default_start)
    ed = (getattr(args, "end_date", None) or
          getattr(args, "end", None) or
          today)

    # 3. start_date 必须 ≥ ctrl(允许 ctrl 当天重拉,主键去重保证幂等)
    last_ctrl = get_ctrl(conn, ctrl_key)
    if last_ctrl:
        # 用户最新规则:start_date < ctrl_day → 用 ctrl_day 替代(而不是 ctrl_day + 1)
        # 考虑 ctrl 可能不全,重跑 ctrl_day 一次更安全
        if sd < last_ctrl:
            logger.warning(f"[{ctrl_key}] start_date {sd} < ctrl {last_ctrl},自动调整为 {last_ctrl}")
            sd = last_ctrl

    return sd, ed, f"日期段 {sd} ~ {ed}"
