import os
import sqlite3

import pytest


os.environ.setdefault("TRADING_AGENT_TUSHARE_TOKEN", "test-token-not-used")

from config.settings import SCHEMA_SQL_BASIC
from core.offline_db_client import get_ctrl, get_month, get_week, replace_tables, update_ctrl
from core.offline_downloader import CNDataDown


def build_downloader():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL_BASIC)
    day_rows = [
        ("000001.SZ", "20260105", 100, 110, 90, 105, 100, 1000),
        ("000001.SZ", "20260109", 106, 120, 100, 115, 200, 2000),
        ("000001.SZ", "20260112", 60, 65, 55, 62, 300, 3000),
        ("000001.SZ", "20260116", 63, 70, 60, 68, 400, 4000),
    ]
    conn.executemany(
        "INSERT INTO tbl_cn_day "
        "(ts_code,trade_date,open,high,low,close,vol,amount) "
        "VALUES (?,?,?,?,?,?,?,?)",
        day_rows,
    )
    conn.executemany(
        "INSERT INTO tbl_cn_adj_factor (ts_code,trade_date,adj_factor) VALUES (?,?,?)",
        [
            ("000001.SZ", "20260105", 1.0),
            ("000001.SZ", "20260109", 1.0),
            ("000001.SZ", "20260112", 2.0),
            ("000001.SZ", "20260116", 2.0),
        ],
    )
    conn.commit()
    downloader = CNDataDown.__new__(CNDataDown)
    downloader.conn_basic = conn
    return downloader, conn


def test_week_update_writes_qfq_and_origin_tables():
    downloader, conn = build_downloader()
    try:
        inserted = downloader.update_week("20260105", "20260116")

        adjusted = get_week(ts_code="000001.SZ", qfq=True, conn=conn)
        origin = get_week(ts_code="000001.SZ", qfq=False, conn=conn)

        assert inserted == 2
        assert len(adjusted) == len(origin) == 2
        assert origin.iloc[0]["open"] == pytest.approx(100)
        assert origin.iloc[0]["high"] == pytest.approx(120)
        assert origin.iloc[0]["close"] == pytest.approx(115)
        assert origin.iloc[0]["vol"] == pytest.approx(300)
        assert adjusted.iloc[0]["open"] == pytest.approx(50)
        assert adjusted.iloc[0]["high"] == pytest.approx(60)
        assert adjusted.iloc[0]["close"] == pytest.approx(57.5)
        assert adjusted.iloc[0]["vol"] == pytest.approx(600)
        assert origin.iloc[1]["pre_close"] == pytest.approx(115)
        assert adjusted.iloc[1]["pre_close"] == pytest.approx(57.5)
    finally:
        conn.close()


def test_month_update_writes_qfq_and_origin_tables():
    downloader, conn = build_downloader()
    try:
        inserted = downloader.update_month("20260105", "20260116")

        adjusted = get_month(ts_code="000001.SZ", qfq=True, conn=conn)
        origin = get_month(ts_code="000001.SZ", qfq=False, conn=conn)

        assert inserted == 1
        assert len(adjusted) == len(origin) == 1
        assert origin.iloc[0]["open"] == pytest.approx(100)
        assert origin.iloc[0]["high"] == pytest.approx(120)
        assert origin.iloc[0]["low"] == pytest.approx(55)
        assert origin.iloc[0]["close"] == pytest.approx(68)
        assert origin.iloc[0]["vol"] == pytest.approx(1000)
        assert adjusted.iloc[0]["open"] == pytest.approx(50)
        assert adjusted.iloc[0]["high"] == pytest.approx(70)
        assert adjusted.iloc[0]["low"] == pytest.approx(45)
        assert adjusted.iloc[0]["close"] == pytest.approx(68)
        assert adjusted.iloc[0]["vol"] == pytest.approx(1300)
    finally:
        conn.close()


def test_paired_replace_rolls_back_both_tables_on_failure():
    _, conn = build_downloader()
    try:
        conn.execute(
            "INSERT INTO tbl_cn_week (ts_code,trade_date,close) VALUES ('OLD','20260101',1)"
        )
        conn.execute(
            "INSERT INTO tbl_cn_week_origin (ts_code,trade_date,close) "
            "VALUES ('OLD','20260101',1)"
        )
        conn.commit()

        import pandas as pd

        good = pd.DataFrame([{"ts_code": "NEW", "trade_date": "20260102", "close": 2}])
        duplicate = pd.DataFrame(
            [
                {"ts_code": "NEW", "trade_date": "20260102", "close": 2},
                {"ts_code": "NEW", "trade_date": "20260102", "close": 3},
            ]
        )
        with pytest.raises(sqlite3.IntegrityError):
            replace_tables(
                conn,
                [("tbl_cn_week", good), ("tbl_cn_week_origin", duplicate)],
            )

        assert conn.execute("SELECT ts_code FROM tbl_cn_week").fetchone()[0] == "OLD"
        assert conn.execute("SELECT ts_code FROM tbl_cn_week_origin").fetchone()[0] == "OLD"
    finally:
        conn.close()


def test_basic_ctrl_never_moves_backwards():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE tbl_basic_ctrl (key TEXT PRIMARY KEY, max_date TEXT, updated_at TEXT)"
    )
    try:
        update_ctrl(conn, "cn_daily", "20260924")
        update_ctrl(conn, "cn_daily", "20260910")
        assert get_ctrl(conn, "cn_daily") == "20260924"
    finally:
        conn.close()
