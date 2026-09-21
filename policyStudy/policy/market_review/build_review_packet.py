from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coreClient.data_provider import (  # noqa: E402
    get_basic,
    get_daily_basic,
    get_day,
    get_index_daily,
    get_kpl_concept_cons,
    get_kpl_limit_performance,
    get_kpl_list,
    get_limit_list,
    get_stk_limit,
    get_tradecal,
)


INDEXES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}
RESTRICTED_NAME = re.compile(r"^(?:\*?ST|PT|退市)|退市|退$")
CONTINUOUS = re.compile(r"^(\d+)连板$")
NDAYMBOARD = re.compile(r"^(\d+)天(\d+)板$")


def ymd(value: str) -> str:
    text = str(value).strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid date: {value!r}")
    return text


def iso_date(value: str) -> str:
    text = ymd(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if not math.isfinite(float(value)):
            return None
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return clean_scalar(value)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    result = frame.copy()
    for column in ("trade_date", "cal_date", "pretrade_date"):
        if column in result:
            result[column] = result[column].astype(str).str.replace("-", "", regex=False)
    return result


@dataclass
class Snapshot:
    name: str
    frame: pd.DataFrame
    source: str
    path: Path
    error: str | None = None

    def evidence(self) -> dict[str, Any]:
        dates: list[str] = []
        for column in ("trade_date", "cal_date"):
            if column in self.frame and not self.frame.empty:
                dates = sorted(self.frame[column].dropna().astype(str).unique().tolist())
                break
        return {
            "name": self.name,
            "source": self.source,
            "rows": int(len(self.frame)),
            "columns": list(self.frame.columns),
            "dates": dates,
            "path": str(self.path),
            "sha256": sha256(self.path) if self.path.is_file() else None,
            "error": self.error,
        }


class SnapshotStore:
    def __init__(self, root: Path, refresh: bool) -> None:
        self.root = root
        self.refresh = refresh
        self.root.mkdir(parents=True, exist_ok=True)
        self.snapshots: dict[str, Snapshot] = {}

    def get(self, name: str, source: str, loader: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        path = self.root / f"{name}.csv"
        meta_path = self.root / f"{name}.meta.json"
        if path.is_file() and not self.refresh:
            try:
                frame = normalize_frame(pd.read_csv(path, dtype=str, keep_default_na=False))
            except pd.errors.EmptyDataError:
                frame = pd.DataFrame()
            snapshot = Snapshot(name, frame, "cache", path)
            self.snapshots[name] = snapshot
            return frame
        try:
            frame = normalize_frame(loader())
            frame.to_csv(path, index=False, encoding="utf-8-sig")
            snapshot = Snapshot(name, frame, source, path)
        except Exception as exc:  # fail visibly; optional sources may be empty
            frame = pd.DataFrame()
            frame.to_csv(path, index=False, encoding="utf-8-sig")
            snapshot = Snapshot(name, frame, source, path, f"{type(exc).__name__}: {exc}")
        self.snapshots[name] = snapshot
        write_json(meta_path, snapshot.evidence())
        return frame


def number_series(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def eligible_basic(basic: pd.DataFrame) -> pd.DataFrame:
    if basic.empty:
        return basic
    result = basic.copy()
    result["ts_code"] = result["ts_code"].astype(str).str.upper()
    result["name"] = result.get("name", "").astype(str).str.strip()
    suffix_ok = result["ts_code"].str.endswith((".SH", ".SZ"))
    name_ok = ~result["name"].str.upper().str.replace(" ", "", regex=False).str.match(RESTRICTED_NAME)
    if "list_status" in result:
        status_ok = result["list_status"].astype(str).isin(["", "L"])
    else:
        status_ok = True
    return result[suffix_ok & name_ok & status_ok].drop_duplicates("ts_code")


def parse_board(status: Any, fallback: Any = None) -> tuple[str, int | None, int | None]:
    text = str(status or "").strip()
    if text == "首板":
        return "continuous", 1, 1
    match = CONTINUOUS.match(text)
    if match:
        value = int(match.group(1))
        return "continuous", value, value
    match = NDAYMBOARD.match(text)
    if match:
        return "n_day_m_board", int(match.group(1)), int(match.group(2))
    try:
        value = int(float(fallback))
        if value >= 1:
            return "continuous_fallback", value, value
    except (TypeError, ValueError):
        pass
    return "unknown", None, None


def valid_time(value: Any) -> str | None:
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        epoch = 0.0
    if 946684800 <= epoch <= 4102444800:
        return datetime.fromtimestamp(epoch, ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")
    text = re.sub(r"\D", "", str(value or ""))
    if len(text) == 5:
        text = "0" + text
    if len(text) != 6:
        return None
    hh, mm, ss = int(text[:2]), int(text[2:4]), int(text[4:])
    if hh > 23 or mm > 59 or ss > 59:
        return None
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def event_table(
    limits: pd.DataFrame,
    kpl: pd.DataFrame,
    performance: pd.DataFrame,
    names: dict[str, str],
) -> pd.DataFrame:
    limits = limits.copy()
    if limits.empty:
        return pd.DataFrame(columns=["ts_code", "name", "limit_state", "board_kind", "board_days", "board_count"])
    limits["ts_code"] = limits["ts_code"].astype(str).str.upper()
    limits["limit_state"] = limits.get("limit", "")
    limits = number_series(limits, ["pct_chg", "amount", "board_count", "limit_times", "open_times"])

    kpl_by_code: dict[str, dict[str, Any]] = {}
    if not kpl.empty:
        for row in kpl.to_dict("records"):
            kpl_by_code[str(row.get("ts_code", "")).upper()] = row
    perf_by_code: dict[str, dict[str, Any]] = {}
    if not performance.empty:
        for row in performance.to_dict("records"):
            perf_by_code[str(row.get("ts_code", "")).upper()] = row

    rows: list[dict[str, Any]] = []
    for row in limits.to_dict("records"):
        code = str(row.get("ts_code", "")).upper()
        kr = kpl_by_code.get(code, {})
        pr = perf_by_code.get(code, {})
        fallback = kr.get("board_count") or pr.get("board_count") or row.get("board_count") or row.get("limit_times")
        kind, board_days, board_count = parse_board(kr.get("status"), fallback)
        first_time = valid_time(kr.get("lu_time") or pr.get("lu_time") or row.get("first_time"))
        last_time = valid_time(kr.get("last_time") or row.get("last_time"))
        open_times = row.get("open_times")
        if pd.isna(open_times):
            open_times = None
        try:
            perf_broken = int(float(pr.get("is_break", 0) or 0)) != 0
        except (TypeError, ValueError):
            perf_broken = False
        one_price = bool(
            first_time
            and first_time <= "09:31:00"
            and (open_times in (None, 0, 0.0))
            and not perf_broken
        )
        rows.append({
            "ts_code": code,
            "name": str(row.get("name") or kr.get("name") or pr.get("name") or names.get(code, "")),
            "limit_state": str(row.get("limit_state", "")),
            "limit_state_raw": str(row.get("limit_state", "")),
            "pct_chg": clean_scalar(row.get("pct_chg")),
            "amount": clean_scalar(row.get("amount") or kr.get("amount") or pr.get("amount")),
            "turnover_rate": clean_scalar(row.get("turnover_ratio") or kr.get("turnover_rate") or pr.get("turnover_rate")),
            "board_kind": kind,
            "board_days": board_days,
            "board_count": board_count,
            "status_raw": str(kr.get("status") or ""),
            "primary_theme": str(kr.get("lu_desc") or pr.get("theme") or "").strip(),
            "theme_text": str(kr.get("theme") or pr.get("limit_reason") or "").strip(),
            "first_limit_time": first_time,
            "last_limit_time": last_time,
            "open_times": clean_scalar(open_times),
            "one_price_proxy": one_price,
            "late_break": None,
            "late_break_evidence": "minute_not_requested",
        })
    return pd.DataFrame(rows)


def derive_price_limits_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Deterministic fallback for non-ST Shanghai/Shenzhen stocks.

    The official stk_limit table remains preferred. The fallback is retained
    with an explicit source marker so it cannot be mistaken for vendor data.
    """
    if daily.empty:
        return pd.DataFrame(columns=["trade_date", "ts_code", "up_limit", "down_limit", "limit_price_source"])
    rows = []
    for row in daily.to_dict("records"):
        code = str(row.get("ts_code", "")).upper()
        try:
            pre_close = Decimal(str(row.get("pre_close")))
        except Exception:
            continue
        symbol = code.split(".", 1)[0]
        rate = Decimal("0.20") if symbol.startswith(("300", "301", "688", "689")) else Decimal("0.10")
        tick = Decimal("0.01")
        rows.append({
            "trade_date": str(row.get("trade_date", "")),
            "ts_code": code,
            "up_limit": float((pre_close * (Decimal("1") + rate)).quantize(tick, rounding=ROUND_HALF_UP)),
            "down_limit": float((pre_close * (Decimal("1") - rate)).quantize(tick, rounding=ROUND_HALF_UP)),
            "limit_price_source": "derived_from_pre_close_and_board_rule",
        })
    return pd.DataFrame(rows)


def reconcile_limit_states(
    events: pd.DataFrame,
    daily: pd.DataFrame,
    price_limits: pd.DataFrame,
    names: dict[str, str],
) -> pd.DataFrame:
    """Use the closing price versus the official daily price limit as final state authority."""
    result = events.copy()
    if result.empty:
        result = pd.DataFrame(columns=[
            "ts_code", "name", "limit_state", "limit_state_raw", "board_kind",
            "board_days", "board_count", "status_raw", "primary_theme", "theme_text",
            "first_limit_time", "last_limit_time", "open_times", "one_price_proxy",
            "late_break", "late_break_evidence", "pct_chg", "amount", "turnover_rate",
        ])
    result["ts_code"] = result.get("ts_code", pd.Series(dtype=str)).astype(str).str.upper()
    if "limit_state_raw" not in result:
        result["limit_state_raw"] = result.get("limit_state", "")

    day = number_series(daily, ["close", "high", "low", "pct_chg", "amount"])
    limits = number_series(price_limits, ["up_limit", "down_limit"])
    for frame in (day, limits):
        if not frame.empty:
            frame["ts_code"] = frame["ts_code"].astype(str).str.upper()
    day_by_code = {str(row.get("ts_code")): row for row in day.to_dict("records")}
    limit_by_code = {str(row.get("ts_code")): row for row in limits.to_dict("records")}

    def same_tick(left: Any, right: Any) -> bool:
        try:
            return abs(float(left) - float(right)) <= 0.0051
        except (TypeError, ValueError):
            return False

    rows = {str(row.get("ts_code")): row for row in result.to_dict("records")}
    for code, day_row in day_by_code.items():
        price_row = limit_by_code.get(code, {})
        close = day_row.get("close")
        price_state = ""
        if same_tick(close, price_row.get("up_limit")):
            price_state = "U"
        elif same_tick(close, price_row.get("down_limit")):
            price_state = "D"
        if not price_state:
            continue
        if code not in rows:
            rows[code] = {
                "ts_code": code,
                "name": names.get(code, ""),
                "limit_state": price_state,
                "limit_state_raw": "missing",
                "pct_chg": clean_scalar(day_row.get("pct_chg")),
                "amount": clean_scalar(day_row.get("amount")),
                "turnover_rate": None,
                "board_kind": "unknown",
                "board_days": None,
                "board_count": None,
                "status_raw": "",
                "primary_theme": "",
                "theme_text": "",
                "first_limit_time": None,
                "last_limit_time": None,
                "open_times": None,
                "one_price_proxy": False,
                "late_break": None,
                "late_break_evidence": "minute_not_requested",
            }

    reconciled: list[dict[str, Any]] = []
    for code, row in rows.items():
        raw = str(row.get("limit_state_raw") or row.get("limit_state") or "")
        day_row = day_by_code.get(code, {})
        price_row = limit_by_code.get(code, {})
        close = day_row.get("close")
        high = day_row.get("high")
        checked = bool(day_row and price_row)
        price_state = ""
        if checked and same_tick(close, price_row.get("up_limit")):
            price_state = "U"
        elif checked and same_tick(close, price_row.get("down_limit")):
            price_state = "D"
        elif checked and raw == "U" and same_tick(high, price_row.get("up_limit")):
            price_state = "Z"

        final_state = price_state or str(row.get("limit_state") or "")
        row["limit_state"] = final_state
        row["limit_state_raw"] = raw
        row["limit_state_source"] = "daily_close_vs_stk_limit" if price_state else "limit_list"
        row["limit_state_checked"] = checked
        row["limit_state_conflict"] = bool(price_state and raw not in ("", "missing", price_state))
        row["close_price"] = clean_scalar(close)
        row["up_limit_price"] = clean_scalar(price_row.get("up_limit"))
        row["down_limit_price"] = clean_scalar(price_row.get("down_limit"))
        row["limit_price_source"] = str(price_row.get("limit_price_source") or "official_stk_limit") if checked else "unavailable"
        reconciled.append(row)
    return pd.DataFrame(reconciled)


def reclassify_boards(
    events: pd.DataFrame,
    limit_history: pd.DataFrame,
    trading_dates: list[str],
    trade_date: str,
) -> pd.DataFrame:
    """Derive continuity from dated limit-up history instead of trusting a label fallback."""
    if events.empty:
        return events
    result = events.copy()
    dates = [str(value) for value in trading_dates if str(value) <= trade_date]
    if trade_date not in dates:
        dates.append(trade_date)
        dates.sort()
    date_index = {value: idx for idx, value in enumerate(dates)}
    limit_dates: dict[str, set[str]] = defaultdict(set)
    if not limit_history.empty:
        history = limit_history.copy()
        history["ts_code"] = history["ts_code"].astype(str).str.upper()
        history["trade_date"] = history["trade_date"].astype(str)
        for row in history[(history.get("limit", "") == "U") & (history["trade_date"] <= trade_date)].to_dict("records"):
            limit_dates[str(row.get("ts_code"))].add(str(row.get("trade_date")))
    for row in result.to_dict("records"):
        code = str(row.get("ts_code"))
        if row.get("limit_state") == "U":
            limit_dates[code].add(trade_date)
        else:
            limit_dates[code].discard(trade_date)

    rows = []
    for row in result.to_dict("records"):
        if row.get("limit_state") != "U":
            rows.append(row)
            continue
        code = str(row.get("ts_code"))
        code_dates = limit_dates.get(code, set())
        cursor = date_index.get(trade_date, len(dates) - 1)
        streak = 0
        while cursor >= 0 and dates[cursor] in code_dates:
            streak += 1
            cursor -= 1
        source_kind = row.get("board_kind")
        source_days = row.get("board_days")
        source_count = row.get("board_count")
        try:
            source_count_int = int(source_count) if source_count is not None else None
        except (TypeError, ValueError):
            source_count_int = None
        recent = sorted((d for d in code_dates if d <= trade_date), key=date_index.get)
        conflict = False
        cluster = [trade_date]
        last = trade_date
        for prior in reversed([d for d in recent if d < trade_date]):
            if date_index[last] - date_index[prior] <= 3:
                cluster.append(prior)
                last = prior
            else:
                break
        cluster.sort(key=date_index.get)
        cluster_span = date_index[trade_date] - date_index[cluster[0]] + 1
        derived_cluster_nday = cluster_span > len(cluster) and (streak == 1 or len(cluster) >= 4)

        if source_kind == "n_day_m_board":
            kind, board_days, board_count = source_kind, source_days, source_count_int
            source = "explicit_kpl_status"
        elif derived_cluster_nday:
            kind, board_days, board_count = "n_day_m_board", cluster_span, len(cluster)
            source = "derived_recent_limit_cluster"
            conflict = source_kind == "continuous"
        elif streak >= 2:
            kind, board_days, board_count = "continuous", streak, streak
            source = "verified_daily_limit_history"
            conflict = bool(source_count_int and source_count_int != streak)
        elif source_count_int and source_count_int >= 2 and len(recent) >= source_count_int:
            selected = recent[-source_count_int:]
            span = date_index[trade_date] - date_index[selected[0]] + 1
            if span > source_count_int:
                kind, board_days, board_count = "n_day_m_board", span, source_count_int
            else:
                kind, board_days, board_count = "continuous", source_count_int, source_count_int
            source = "derived_from_daily_limit_history"
            conflict = source_kind == "continuous" and kind != "continuous"
        else:
            kind, board_days, board_count = "continuous", 1, 1
            source = "verified_daily_limit_history"
            conflict = bool(source_count_int and source_count_int > 1)

        row["board_kind"] = kind
        row["board_days"] = board_days
        row["board_count"] = board_count
        row["board_classification_source"] = source
        row["board_classification_conflict"] = conflict
        rows.append(row)
    return pd.DataFrame(rows)


def market_summary(
    day: pd.DataFrame,
    events: pd.DataFrame,
    index_rows: pd.DataFrame,
    previous_day: pd.DataFrame | None = None,
) -> dict[str, Any]:
    numeric = number_series(day, ["pct_chg", "amount"])
    advances = int((numeric.get("pct_chg", pd.Series(dtype=float)) > 0).sum())
    declines = int((numeric.get("pct_chg", pd.Series(dtype=float)) < 0).sum())
    unchanged = int((numeric.get("pct_chg", pd.Series(dtype=float)) == 0).sum())
    states = events.get("limit_state", pd.Series(dtype=str)).astype(str)
    closed = int((states == "U").sum())
    broken = int((states == "Z").sum())
    down = int((states == "D").sum())
    touch_seal = closed / (closed + broken) if closed + broken else None
    indexes = []
    if not index_rows.empty:
        idx = number_series(index_rows, ["open", "high", "low", "close", "pre_close", "pct_chg", "amount"])
        for row in idx.to_dict("records"):
            code = str(row.get("ts_code", ""))
            indexes.append({"ts_code": code, "name": INDEXES.get(code, code), **{k: clean_scalar(row.get(k)) for k in ("open", "high", "low", "close", "pre_close", "pct_chg", "amount")}})
    market_amount_raw = clean_scalar(numeric["amount"].sum()) if "amount" in numeric else None
    previous_amount_raw = None
    if previous_day is not None and not previous_day.empty and "amount" in previous_day:
        previous_amount_raw = clean_scalar(pd.to_numeric(previous_day["amount"], errors="coerce").sum())
    broken_rows = []
    if not events.empty:
        broken_events = events[events["limit_state"] == "Z"].copy()
        if not broken_events.empty:
            broken_events = broken_events.sort_values(["amount", "ts_code"], ascending=[False, True], na_position="last")
            broken_rows = [
                {key: clean_scalar(row.get(key)) for key in ("ts_code", "name", "pct_chg", "amount", "primary_theme")}
                for row in broken_events.to_dict("records")
            ]
    return {
        "eligible_stock_count": int(len(numeric)),
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advance_decline_ratio": advances / declines if declines else None,
        "market_amount_raw": market_amount_raw,
        "market_amount_raw_unit": "thousand_cny",
        "market_amount_trillion_cny": market_amount_raw / 1_000_000_000 if market_amount_raw is not None else None,
        "previous_market_amount_trillion_cny": previous_amount_raw / 1_000_000_000 if previous_amount_raw is not None else None,
        "market_amount_change_billion_cny": (market_amount_raw - previous_amount_raw) / 1_000_000 if market_amount_raw is not None and previous_amount_raw is not None else None,
        "closed_limit_up_count": closed,
        "broken_board_count": broken,
        "broken_board_representatives": broken_rows,
        "limit_down_count": down,
        "touch_seal_rate": touch_seal,
        "late_broken_count": None,
        "late_broken_status": "minute_not_validated",
        "indexes": indexes,
    }


def ladder_summary(events: pd.DataFrame, previous_events: pd.DataFrame, day: pd.DataFrame) -> dict[str, Any]:
    closed = events[events["limit_state"] == "U"].copy() if not events.empty else events
    continuous = closed[closed["board_kind"].isin(["continuous", "continuous_fallback"])].copy()
    nday = closed[closed["board_kind"] == "n_day_m_board"].copy()
    continuous = continuous.sort_values(["board_count", "first_limit_time", "ts_code"], ascending=[False, True, True], na_position="last")
    by_level: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in continuous.to_dict("records"):
        by_level[str(int(row["board_count"]))].append({k: clean_scalar(row.get(k)) for k in ("ts_code", "name", "board_count", "primary_theme", "first_limit_time", "one_price_proxy", "amount")})

    current_by_code = {row["ts_code"]: row for row in events.to_dict("records")}
    day_num = number_series(day, ["pct_chg"])
    day_pct = dict(zip(day_num.get("ts_code", []), day_num.get("pct_chg", [])))
    transitions: list[dict[str, Any]] = []
    promotion: dict[str, dict[str, int | float | None]] = {}
    if not previous_events.empty:
        prev_closed = previous_events[
            (previous_events["limit_state"] == "U")
            & previous_events["board_kind"].isin(["continuous", "continuous_fallback"])
        ]
        for level, group in prev_closed.groupby("board_count"):
            eligible = len(group)
            promoted = 0
            for prev in group.to_dict("records"):
                cur = current_by_code.get(prev["ts_code"])
                is_promoted = bool(cur and cur.get("limit_state") == "U" and cur.get("board_count") == int(level) + 1)
                promoted += int(is_promoted)
                transitions.append({
                    "ts_code": prev["ts_code"],
                    "name": prev["name"],
                    "from_board": int(level),
                    "today_state": cur.get("limit_state") if cur else "not_limit_event",
                    "today_board": cur.get("board_count") if cur else None,
                    "today_pct_chg": clean_scalar(day_pct.get(prev["ts_code"])),
                    "promoted": is_promoted,
                })
            promotion[f"{int(level)}进{int(level)+1}"] = {"promoted": promoted, "eligible": eligible, "rate": promoted / eligible if eligible else None}
    transitions.sort(key=lambda item: (-item["from_board"], item["ts_code"]))
    previous_limit_feedback: dict[str, Any] = {
        "sample_count": 0,
        "mean_pct_chg": None,
        "median_pct_chg": None,
        "positive_rate": None,
        "today_limit_up_count": 0,
        "today_broken_board_count": 0,
        "today_limit_down_count": 0,
    }
    if not previous_events.empty:
        previous_closed_codes = set(previous_events.loc[previous_events["limit_state"] == "U", "ts_code"].astype(str))
        feedback = day_num[day_num["ts_code"].astype(str).isin(previous_closed_codes)].copy()
        feedback_pct = pd.to_numeric(feedback.get("pct_chg"), errors="coerce").dropna()
        current_states = events[events["ts_code"].isin(previous_closed_codes)]["limit_state"] if not events.empty else pd.Series(dtype=str)
        previous_limit_feedback = {
            "sample_count": int(len(feedback_pct)),
            "mean_pct_chg": clean_scalar(feedback_pct.mean()) if len(feedback_pct) else None,
            "median_pct_chg": clean_scalar(feedback_pct.median()) if len(feedback_pct) else None,
            "positive_rate": clean_scalar((feedback_pct > 0).mean()) if len(feedback_pct) else None,
            "today_limit_up_count": int((current_states == "U").sum()),
            "today_broken_board_count": int((current_states == "Z").sum()),
            "today_limit_down_count": int((current_states == "D").sum()),
        }
    return {
        "highest_continuous_board": int(continuous["board_count"].max()) if not continuous.empty else 0,
        "continuous_by_level": dict(sorted(by_level.items(), key=lambda item: -int(item[0]))),
        "n_day_m_board": [
            {k: clean_scalar(row.get(k)) for k in (
                "ts_code", "name", "status_raw", "board_days", "board_count",
                "board_classification_source", "board_classification_conflict",
                "primary_theme", "first_limit_time", "one_price_proxy",
            )}
            for row in nday.to_dict("records")
        ],
        "promotion_rates": promotion,
        "previous_limit_up_feedback": previous_limit_feedback,
        "previous_high_board_outcomes": transitions,
    }


def percentile_rank(series: pd.Series) -> pd.Series:
    if len(series) <= 1:
        return pd.Series([1.0] * len(series), index=series.index)
    return series.rank(method="average", pct=True)


def theme_summary(
    members: pd.DataFrame,
    day: pd.DataFrame,
    daily_basic: pd.DataFrame,
    events: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    if members.empty:
        return [], {}
    membership = members.rename(columns={
        "ts_code": "theme_code",
        "name": "theme",
        "con_code": "ts_code",
        "con_name": "member_name",
    }).copy()
    membership["theme"] = membership["theme"].astype(str).str.strip()
    membership["ts_code"] = membership["ts_code"].astype(str).str.upper()
    membership = membership[(membership["theme"] != "") & membership["ts_code"].str.endswith((".SH", ".SZ"))]
    membership = membership.drop_duplicates(["theme", "ts_code"])
    overlaps = membership.groupby("ts_code")["theme"].apply(lambda x: sorted(set(x))).to_dict()

    d = number_series(day, ["pct_chg", "amount"])[[c for c in ("ts_code", "pct_chg", "amount") if c in day.columns]].copy()
    d["ts_code"] = d["ts_code"].astype(str).str.upper()
    db = number_series(daily_basic, ["turnover_rate", "turnover_rate_f", "volume_ratio", "circ_mv"])
    if not db.empty:
        db["ts_code"] = db["ts_code"].astype(str).str.upper()
        d = d.merge(db[[c for c in ("ts_code", "turnover_rate", "turnover_rate_f", "volume_ratio", "circ_mv") if c in db]], on="ts_code", how="left")
    panel = membership.merge(d, on="ts_code", how="inner")
    event_cols = ["ts_code", "name", "limit_state", "board_kind", "board_count", "first_limit_time", "one_price_proxy", "amount", "primary_theme"]
    ev = events[[c for c in event_cols if c in events]].copy()
    panel = panel.merge(ev, on="ts_code", how="left", suffixes=("", "_event"))
    total_amount = pd.to_numeric(d.get("amount"), errors="coerce").sum()
    rows: list[dict[str, Any]] = []
    for theme, group in panel.groupby("theme", sort=True):
        closed = group[group["limit_state"] == "U"]
        broken = group[group["limit_state"] == "Z"]
        member_count = int(group["ts_code"].nunique())
        levels = sorted({int(v) for v in closed["board_count"].dropna() if int(v) >= 1})
        max_level = max(levels, default=0)
        expected = set(range(1, min(max_level, 5) + 1))
        completeness = len(expected.intersection(levels)) / len(expected) if expected else 0.0
        first = closed.sort_values(["first_limit_time", "ts_code"], na_position="last").head(5)
        amount_sum = pd.to_numeric(group.get("amount"), errors="coerce").sum()
        touch = len(closed) + len(broken)
        rows.append({
            "theme": theme,
            "eligible_member_count": member_count,
            "advance_count": int((group["pct_chg"] > 0).sum()),
            "decline_count": int((group["pct_chg"] < 0).sum()),
            "equal_weight_pct_chg": clean_scalar(group["pct_chg"].mean()),
            "median_pct_chg": clean_scalar(group["pct_chg"].median()),
            "amount_sum_raw": clean_scalar(amount_sum),
            "market_amount_share": clean_scalar(amount_sum / total_amount) if total_amount else None,
            "median_volume_ratio": clean_scalar(pd.to_numeric(group.get("volume_ratio"), errors="coerce").median()) if "volume_ratio" in group else None,
            "closed_limit_up_count": int(len(closed)),
            "theme_close_limit_rate": len(closed) / member_count if member_count else None,
            "broken_board_count": int(len(broken)),
            "touch_seal_rate": len(closed) / touch if touch else None,
            "highest_continuous_board": int(closed.loc[closed["board_kind"].isin(["continuous", "continuous_fallback"]), "board_count"].max()) if not closed.loc[closed["board_kind"].isin(["continuous", "continuous_fallback"])].empty else 0,
            "continuous_board_levels": levels,
            "ladder_completeness": completeness,
            "n_day_m_board_count": int((closed["board_kind"] == "n_day_m_board").sum()),
            "first_limit_representatives": [
                {
                    "ts_code": row.get("ts_code"),
                    "name": row.get("name"),
                    "time": row.get("first_limit_time"),
                    "board_count": clean_scalar(row.get("board_count")),
                    "one_price_proxy": bool(row.get("one_price_proxy")),
                    "overlap_themes": overlaps.get(str(row.get("ts_code")), []),
                }
                for row in first.to_dict("records")
            ],
        })
    result = pd.DataFrame(rows)
    if result.empty:
        return [], overlaps
    breadth = percentile_rank(result["closed_limit_up_count"])
    depth_raw = result["closed_limit_up_count"] + result["highest_continuous_board"].clip(upper=5)
    depth = percentile_rank(depth_raw)
    ladder = percentile_rank(result["ladder_completeness"])
    quality = percentile_rank(result["touch_seal_rate"].fillna(0))
    activity = percentile_rank(result["market_amount_share"].fillna(0))
    result["evidence_score"] = 0.35 * breadth + 0.25 * depth + 0.15 * ladder + 0.10 * quality + 0.15 * activity
    result = result.sort_values(["evidence_score", "closed_limit_up_count", "market_amount_share", "theme"], ascending=[False, False, False, True])
    return [jsonable(row) for row in result.head(20).to_dict("records")], overlaps


def history_theme_panel(
    calendar: list[str],
    concept: pd.DataFrame,
    day: pd.DataFrame,
    limits: pd.DataFrame,
    performance: pd.DataFrame,
    basic: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = dict(zip(basic.get("ts_code", []), basic.get("name", [])))
    eligible_codes = set(str(code).upper() for code in basic.get("ts_code", []))
    daily_basic_empty = pd.DataFrame(columns=["ts_code"])
    for date in calendar:
        c = concept[concept["trade_date"] == date] if "trade_date" in concept else pd.DataFrame()
        d = day[day["trade_date"] == date] if "trade_date" in day else pd.DataFrame()
        l = limits[limits["trade_date"] == date] if "trade_date" in limits else pd.DataFrame()
        p = performance[performance["trade_date"] == date] if "trade_date" in performance else pd.DataFrame()
        if not c.empty and "con_code" in c:
            c = c[c["con_code"].astype(str).str.upper().isin(eligible_codes)]
        if not d.empty and "ts_code" in d:
            d = d[d["ts_code"].astype(str).str.upper().isin(eligible_codes)]
        if not l.empty and "ts_code" in l:
            l = l[l["ts_code"].astype(str).str.upper().isin(eligible_codes)]
        if not p.empty and "ts_code" in p:
            p = p[p["ts_code"].astype(str).str.upper().isin(eligible_codes)]
        ev = event_table(l, pd.DataFrame(), p, names)
        summary, _ = theme_summary(c, d, daily_basic_empty, ev)
        for rank, item in enumerate(summary[:8], 1):
            rows.append({"trade_date": date, "rank": rank, **item})
    return rows


def past_mainline_tracking(history: list[dict[str, Any]], today: list[dict[str, Any]], current_date: str) -> list[dict[str, Any]]:
    by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        by_theme[row["theme"]].append(row)
    today_by_theme = {row["theme"]: row for row in today}
    candidates = []
    for theme, rows in by_theme.items():
        past = [row for row in rows if row["trade_date"] != current_date]
        if not past:
            continue
        best_rank = min(int(row["rank"]) for row in past)
        active_days = sum(1 for row in past if row["closed_limit_up_count"] >= 3 or row["highest_continuous_board"] >= 2)
        if best_rank > 5 and active_days < 2:
            continue
        cur = today_by_theme.get(theme)
        candidates.append({
            "theme": theme,
            "past_15d_best_rank": best_rank,
            "past_15d_display_days": len(past),
            "past_15d_active_days": active_days,
            "past_15d_peak_limit_up_count": max(int(row["closed_limit_up_count"]) for row in past),
            "past_15d_peak_board": max(int(row["highest_continuous_board"]) for row in past),
            "last_seen_date": max(row["trade_date"] for row in past),
            "today": cur or {
                "closed_limit_up_count": 0,
                "broken_board_count": 0,
                "highest_continuous_board": 0,
                "equal_weight_pct_chg": None,
                "market_amount_share": None,
                "evidence_score": None,
            },
        })
    candidates.sort(key=lambda x: (x["past_15d_best_rank"], -x["past_15d_active_days"], x["theme"]))
    return candidates[:15]


def candidate_cards(
    events: pd.DataFrame,
    themes: list[dict[str, Any]],
    overlaps: dict[str, list[str]],
    recent_day: pd.DataFrame,
    recent_limits: pd.DataFrame,
    names: dict[str, str],
) -> list[dict[str, Any]]:
    top_theme_names = {row["theme"] for row in themes[:8]}
    recent = number_series(recent_day, ["open", "high", "low", "close", "pct_chg", "amount", "vol"])
    recent["ts_code"] = recent.get("ts_code", "").astype(str).str.upper()
    recent["trade_date"] = recent.get("trade_date", "").astype(str)
    limit_dates = defaultdict(list)
    limit_rows: dict[tuple[str, str], dict[str, Any]] = {}
    if not recent_limits.empty:
        for row in recent_limits[recent_limits.get("limit", "") == "U"].to_dict("records"):
            code = str(row.get("ts_code", "")).upper()
            date = str(row.get("trade_date", ""))
            limit_dates[code].append(date)
            limit_rows[(code, date)] = row
    current_date = str(recent["trade_date"].max()) if not recent.empty else ""
    event_by_code = {str(row.get("ts_code")): row for row in events.to_dict("records")}
    for code, event in event_by_code.items():
        dates = set(limit_dates.get(code, []))
        if event.get("limit_state") == "U":
            dates.add(current_date)
        else:
            dates.discard(current_date)
        limit_dates[code] = sorted(dates)
    rows = []
    for event in events[events["limit_state"].isin(["U", "Z"])].to_dict("records"):
        code = event["ts_code"]
        stock_themes = overlaps.get(code, [])
        if not top_theme_names.intersection(stock_themes) and (event.get("board_count") or 0) < 2:
            continue
        hist = recent[recent["ts_code"] == code].sort_values("trade_date")
        close = clean_scalar(hist["close"].iloc[-1]) if not hist.empty else None
        high20 = clean_scalar(hist.tail(20)["high"].max()) if not hist.empty else None
        low20 = clean_scalar(hist.tail(20)["low"].min()) if not hist.empty else None
        ret20 = None
        if len(hist) >= 2 and hist.tail(20)["close"].iloc[0]:
            ret20 = float(hist["close"].iloc[-1] / hist.tail(20)["close"].iloc[0] - 1)
        amount_ratio = None
        if len(hist) >= 6:
            base = hist.iloc[:-1].tail(20)["amount"].median()
            if base and math.isfinite(float(base)):
                amount_ratio = float(hist["amount"].iloc[-1] / base)
        flags = []
        if event.get("one_price_proxy"):
            flags.append("一字/早盘快速封板代理，默认存在不可成交风险")
        if event.get("limit_state") == "Z":
            flags.append("触板未封，不计入连板梯队")
        if event.get("board_kind") == "n_day_m_board" and int(event.get("board_count") or 0) == 2:
            pattern_stage = "restart_completed"
            pattern_shape = "首板后非连续交易日再次封板"
        elif event.get("board_kind") == "continuous" and int(event.get("board_count") or 0) >= 2:
            pattern_stage = "continuous_board"
            pattern_shape = "连续涨停，不属于首次回调再启"
        elif event.get("limit_state") == "U":
            pattern_stage = "initial_launch"
            pattern_shape = "首轮启动，等待后续回调观察"
        else:
            pattern_stage = "failed_touch"
            pattern_shape = "触板未封"
        rows.append({
            "ts_code": code,
            "name": event.get("name"),
            "pool_hint": "涨停接力" if event.get("limit_state") == "U" else "触板失败观察",
            "limit_state": event.get("limit_state"),
            "board_kind": event.get("board_kind"),
            "board_days": event.get("board_days"),
            "board_count": event.get("board_count"),
            "status_raw": event.get("status_raw"),
            "pattern_stage": pattern_stage,
            "pattern_shape": pattern_shape,
            "themes": stock_themes,
            "primary_theme": event.get("primary_theme"),
            "first_limit_time": event.get("first_limit_time"),
            "one_price_proxy": event.get("one_price_proxy"),
            "today_amount": event.get("amount"),
            "today_turnover_rate": event.get("turnover_rate"),
            "close": close,
            "high_20d": high20,
            "low_20d": low20,
            "return_20d": ret20,
            "amount_vs_prior_20d_median": amount_ratio,
            "recent_limit_dates": sorted(limit_dates.get(code, []))[-8:],
            "risk_flags": flags,
            "active_float": "not_in_core; optional web check only after final recommendation",
        })

    # Add stocks that are still in the first post-launch pullback. These are the
    # forward watch candidates; a stock that already re-boarded is a completed
    # sample, not retroactively a low-risk entry.
    market_dates = sorted(recent["trade_date"].dropna().astype(str).unique().tolist())
    date_index = {value: idx for idx, value in enumerate(market_dates)}
    for code, stock_themes in overlaps.items():
        if not top_theme_names.intersection(stock_themes):
            continue
        if code in event_by_code and event_by_code[code].get("limit_state") in ("U", "Z"):
            continue
        prior_limits = [date for date in limit_dates.get(code, []) if date < current_date and date in date_index]
        if not prior_limits or current_date not in date_index:
            continue
        launch_date = prior_limits[-1]
        gap = date_index[current_date] - date_index[launch_date]
        if gap not in (1, 2, 3):
            continue
        launch_meta = limit_rows.get((code, launch_date), {})
        try:
            launch_board_count = int(float(launch_meta.get("board_count") or launch_meta.get("limit_times") or 1))
        except (TypeError, ValueError):
            launch_board_count = 1
        if launch_board_count > 1:
            continue
        hist = recent[recent["ts_code"] == code].sort_values("trade_date")
        launch_rows = hist[hist["trade_date"] == launch_date]
        current_rows = hist[hist["trade_date"] == current_date]
        if launch_rows.empty or current_rows.empty:
            continue
        launch = launch_rows.iloc[-1]
        current = current_rows.iloc[-1]
        try:
            relative_close = float(current["close"] / launch["close"] - 1)
            amount_ratio_launch = float(current["amount"] / launch["amount"]) if float(launch["amount"]) else None
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if not (-0.08 <= relative_close <= 0.04):
            continue
        if amount_ratio_launch is not None and amount_ratio_launch > 1.25:
            continue
        shape = "tight_platform" if abs(relative_close) <= 0.03 else "shallow_pullback"
        flags = []
        if not launch_meta.get("board_count") and not launch_meta.get("limit_times"):
            flags.append("启动日首板属性仅由近端历史推断，需复核")
        rows.append({
            "ts_code": code,
            "name": names.get(code, ""),
            "pool_hint": "低位首板后首次回调跟踪",
            "limit_state": "not_limit_event",
            "board_kind": "pullback_watch",
            "board_days": 1,
            "board_count": 1,
            "status_raw": "",
            "pattern_stage": "pullback_in_progress",
            "pattern_shape": shape,
            "launch_date": launch_date,
            "pullback_sessions": gap,
            "close_vs_launch_close": relative_close,
            "amount_vs_launch_day": amount_ratio_launch,
            "themes": stock_themes,
            "primary_theme": stock_themes[0] if stock_themes else "",
            "first_limit_time": None,
            "one_price_proxy": False,
            "today_amount": clean_scalar(current.get("amount")),
            "today_turnover_rate": None,
            "close": clean_scalar(current.get("close")),
            "high_20d": clean_scalar(hist.tail(20)["high"].max()),
            "low_20d": clean_scalar(hist.tail(20)["low"].min()),
            "return_20d": None,
            "amount_vs_prior_20d_median": None,
            "recent_limit_dates": sorted(limit_dates.get(code, []))[-8:],
            "risk_flags": flags,
            "active_float": "not_in_core; optional web check only after final recommendation",
        })
    priority = {"pullback_in_progress": 0, "restart_completed": 1, "continuous_board": 2, "initial_launch": 3, "failed_touch": 4}
    rows.sort(key=lambda x: (priority.get(x.get("pattern_stage"), 9), -(x.get("board_count") or 0), -(x.get("today_amount") or 0), x["ts_code"]))
    return rows[:30]


def validate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    required = ["metadata", "data_quality", "market", "ladder", "themes", "past_mainline_tracking", "candidate_cards"]
    for key in required:
        if key not in packet:
            errors.append(f"missing key: {key}")
    market = packet.get("market", {})
    closed = market.get("closed_limit_up_count", 0)
    broken = market.get("broken_board_count", 0)
    expected = closed / (closed + broken) if closed + broken else None
    actual = market.get("touch_seal_rate")
    if expected is not None and (actual is None or abs(expected - actual) > 1e-12):
        errors.append("market touch_seal_rate is not reproducible")
    for theme in packet.get("themes", []):
        members = theme.get("eligible_member_count", 0)
        expected_close = theme.get("closed_limit_up_count", 0) / members if members else None
        if expected_close is not None and abs(expected_close - (theme.get("theme_close_limit_rate") or 0)) > 1e-12:
            errors.append(f"theme close-limit rate mismatch: {theme.get('theme')}")
        touches = theme.get("closed_limit_up_count", 0) + theme.get("broken_board_count", 0)
        expected_touch = theme.get("closed_limit_up_count", 0) / touches if touches else None
        if expected_touch is not None and abs(expected_touch - (theme.get("touch_seal_rate") or 0)) > 1e-12:
            errors.append(f"theme touch-seal rate mismatch: {theme.get('theme')}")
    if packet.get("data_quality", {}).get("required_missing"):
        errors.append("required current-day sources are missing")
    if not packet.get("data_quality", {}).get("late_break_minute_validated", False):
        warnings.append("late broken-board classification not minute-validated")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "warnings": warnings}


def collect(args: argparse.Namespace) -> tuple[dict[str, pd.DataFrame], SnapshotStore, list[str]]:
    trade_date = ymd(args.trade_date)
    out = Path(args.output_dir).resolve()
    store = SnapshotStore(out / "raw", args.refresh)
    start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=35)).strftime("%Y%m%d")

    frames: dict[str, pd.DataFrame] = {}
    frames["calendar_online"] = store.get("calendar_online", "online", lambda: get_tradecal(start_date=start, end_date=trade_date, source="online"))
    calendar = sorted(set(frames["calendar_online"].get("cal_date", pd.Series(dtype=str)).astype(str)))
    if trade_date not in calendar:
        calendar.append(trade_date)
        calendar.sort()
    previous = [date for date in calendar if date < trade_date]
    previous_date = previous[-1] if previous else (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
    history_dates = [date for date in calendar if date <= trade_date][-15:]
    history_start = history_dates[0] if history_dates else start

    frames["basic"] = store.get("basic", "online", lambda: get_basic(source="online"))
    frames["day_current"] = store.get("day_current", "online", lambda: get_day(trade_date=trade_date, qfq=True, source="online"))
    frames["daily_basic_current"] = store.get("daily_basic_current", "online", lambda: get_daily_basic(trade_date=trade_date, source="online"))
    frames["limit_current"] = store.get("limit_current", "online", lambda: get_limit_list(trade_date=trade_date, source="online"))

    def load_price_limits() -> pd.DataFrame:
        official = get_stk_limit(trade_date=trade_date, source="database")
        if not official.empty:
            official = official.copy()
            official["limit_price_source"] = "official_stk_limit_database"
            return official
        return derive_price_limits_from_daily(frames["day_current"])

    frames["price_limits_current"] = store.get(
        "price_limits_current",
        "database_or_derived",
        load_price_limits,
    )
    frames["kpl_current"] = store.get("kpl_current", "online", lambda: get_kpl_list(trade_date=trade_date, tags=["涨停", "炸板"], source="online"))
    frames["performance_current"] = store.get("performance_current", "online", lambda: get_kpl_limit_performance(trade_date=iso_date(trade_date), source="online"))
    frames["concept_current"] = store.get("concept_current", "online", lambda: get_kpl_concept_cons(trade_date=trade_date, source="online"))
    index_parts = []
    for code in INDEXES:
        part = store.get(f"index_current_{code.replace('.', '_')}", "online", lambda code=code: get_index_daily(ts_code=code, trade_date=trade_date, source="online"))
        if not part.empty:
            index_parts.append(part)
    frames["index_current"] = pd.concat(index_parts, ignore_index=True) if index_parts else pd.DataFrame()

    frames["day_history_db"] = store.get("day_history_db", "database", lambda: get_day(start_date=history_start, end_date=trade_date, qfq=True, source="database"))
    frames["limit_history_db"] = store.get("limit_history_db", "database", lambda: get_limit_list(start_date=history_start, end_date=trade_date, source="database"))
    frames["performance_history_db"] = store.get("performance_history_db", "database", lambda: get_kpl_limit_performance(start_date=iso_date(history_start), end_date=iso_date(trade_date), source="database"))
    frames["concept_history_db"] = store.get("concept_history_db", "database", lambda: get_kpl_concept_cons(start_date=history_start, end_date=trade_date, source="database"))

    missing_dates = [date for date in history_dates if date not in set(frames["day_history_db"].get("trade_date", pd.Series(dtype=str)).astype(str))]
    online_days = []
    online_limits = []
    online_perf = []
    online_concepts = []
    for date in missing_dates:
        d = store.get(f"day_online_{date}", "online", lambda date=date: get_day(trade_date=date, qfq=True, source="online"))
        l = store.get(f"limit_online_{date}", "online", lambda date=date: get_limit_list(trade_date=date, source="online"))
        p = store.get(f"performance_online_{date}", "online", lambda date=date: get_kpl_limit_performance(trade_date=iso_date(date), source="online"))
        c = store.get(f"concept_online_{date}", "online", lambda date=date: get_kpl_concept_cons(trade_date=date, source="online"))
        if not d.empty:
            online_days.append(d)
        if not l.empty:
            online_limits.append(l)
        if not p.empty:
            online_perf.append(p)
        if not c.empty:
            online_concepts.append(c)

    def combine(base: pd.DataFrame, additions: list[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
        parts = [part for part in [base, *additions] if part is not None and not part.empty]
        if not parts:
            return pd.DataFrame()
        return pd.concat(parts, ignore_index=True).drop_duplicates(keys, keep="last")

    frames["day_history"] = combine(frames["day_history_db"], online_days + [frames["day_current"]], ["trade_date", "ts_code"])
    frames["limit_history"] = combine(frames["limit_history_db"], online_limits + [frames["limit_current"]], ["trade_date", "ts_code", "limit"])
    frames["performance_history"] = combine(frames["performance_history_db"], online_perf + [frames["performance_current"]], ["trade_date", "ts_code"])
    frames["concept_history"] = combine(frames["concept_history_db"], online_concepts + [frames["concept_current"]], ["trade_date", "ts_code", "con_code"])
    return frames, store, history_dates


def build(args: argparse.Namespace) -> dict[str, Any]:
    trade_date = ymd(args.trade_date)
    out = Path(args.output_dir).resolve()
    frames, store, history_dates = collect(args)
    basic = eligible_basic(frames["basic"])
    eligible_codes = set(basic.get("ts_code", pd.Series(dtype=str)).astype(str))
    names = dict(zip(basic.get("ts_code", []), basic.get("name", [])))

    current_day = frames["day_current"].copy()
    if not current_day.empty:
        current_day["ts_code"] = current_day["ts_code"].astype(str).str.upper()
        current_day = current_day[current_day["ts_code"].isin(eligible_codes)]
    current_limits = frames["limit_current"].copy()
    if not current_limits.empty:
        current_limits["ts_code"] = current_limits["ts_code"].astype(str).str.upper()
        current_limits = current_limits[current_limits["ts_code"].isin(eligible_codes)]
    current_members = frames["concept_current"].copy()
    if not current_members.empty:
        current_members["con_code"] = current_members["con_code"].astype(str).str.upper()
        current_members = current_members[current_members["con_code"].isin(eligible_codes)]

    events = event_table(current_limits, frames["kpl_current"], frames["performance_current"], names)
    events = reconcile_limit_states(events, current_day, frames["price_limits_current"], names)
    events = reclassify_boards(events, frames["limit_history"], history_dates, trade_date)
    previous_dates = sorted(date for date in history_dates if date < trade_date)
    previous_date = previous_dates[-1] if previous_dates else ""
    prev_limits = frames["limit_history"]
    prev_perf = frames["performance_history"]
    previous_limits = prev_limits[prev_limits["trade_date"] == previous_date] if previous_date else pd.DataFrame()
    previous_perf = prev_perf[prev_perf["trade_date"] == previous_date] if previous_date else pd.DataFrame()
    if not previous_limits.empty:
        previous_limits = previous_limits[previous_limits["ts_code"].astype(str).str.upper().isin(eligible_codes)]
    if not previous_perf.empty:
        previous_perf = previous_perf[previous_perf["ts_code"].astype(str).str.upper().isin(eligible_codes)]
    previous_events = event_table(
        previous_limits,
        pd.DataFrame(),
        previous_perf,
        names,
    )
    if previous_date:
        previous_events = reclassify_boards(previous_events, frames["limit_history"], history_dates, previous_date)
    previous_day = frames["day_history"]
    if not previous_day.empty and previous_date:
        previous_day = previous_day[previous_day["trade_date"] == previous_date].copy()
        previous_day["ts_code"] = previous_day["ts_code"].astype(str).str.upper()
        previous_day = previous_day[previous_day["ts_code"].isin(eligible_codes)]
    else:
        previous_day = pd.DataFrame()
    themes, overlaps = theme_summary(current_members, current_day, frames["daily_basic_current"], events)
    history_rows = history_theme_panel(
        history_dates,
        frames["concept_history"],
        frames["day_history"],
        frames["limit_history"],
        frames["performance_history"],
        basic,
    )

    required_names = ["basic", "day_current", "daily_basic_current", "price_limits_current", "limit_current", "performance_current", "concept_current", "index_current"]
    required_missing = [name for name in required_names if frames.get(name, pd.DataFrame()).empty]
    packet = {
        "metadata": {
            "packet_version": "MR_PACKET_V1",
            "trade_date": trade_date,
            "as_of": datetime.now().astimezone().isoformat(),
            "review_mode": "end_of_day",
            "market_data_policy": "packet_is_authoritative; web must not recheck market facts",
            "web_allowed": ["policy catalyst", "company announcement", "industry event", "recommended-stock shareholder context"],
            "web_forbidden": ["index return", "market breadth", "limit counts", "ladder", "promotion rates", "stock price/amount already in packet"],
            "if_then_required": False,
            "active_float_core": False,
            "late_break_policy": "only report a post-14:30 broken board after minute-data validation; otherwise mark unavailable",
        },
        "definitions": {
            "universe": "SH/SZ A shares; exclude BJ, ST/*ST/PT, delisting and non-trading rows",
            "theme_source": "KPL daily concept membership and KPL labels",
            "theme_overlap": "a stock counts independently in every KPL theme; overlaps are explicit",
            "theme_close_limit_rate": "closed limit-ups / eligible theme members",
            "touch_seal_rate": "closed limit-ups / (closed limit-ups + broken boards)",
            "continuous_board": "首板 or N连板; N天M板 excluded from ladder",
            "money_flow_language": "observable price/breadth/amount proxies; no authoritative net-flow claim",
            "daily_amount_unit": "get_day amount is thousand CNY; packet market_amount_trillion_cny is the normalized display value",
            "event_amount_unit": "limit/KPL event amount is CNY and is not added to get_day amount without conversion",
        },
        "data_quality": {
            "required_missing": required_missing,
            "late_break_minute_validated": False,
            "history_dates": history_dates,
            "previous_trade_date": previous_date,
            "source_evidence": [snapshot.evidence() for snapshot in store.snapshots.values()],
            "limit_state_reconciliation": {
                "authority": "daily close versus official stk_limit; deterministic board-rule fallback if the official database row is unavailable",
                "checked_rows": int(events.get("limit_state_checked", pd.Series(dtype=bool)).eq(True).sum()),
                "corrected_rows": int(events.get("limit_state_conflict", pd.Series(dtype=bool)).eq(True).sum()),
                "corrected_codes": sorted(events.loc[events.get("limit_state_conflict", False) == True, "ts_code"].astype(str).tolist()) if not events.empty and "limit_state_conflict" in events else [],
                "price_limit_sources": events.get("limit_price_source", pd.Series(dtype=str)).value_counts().to_dict(),
            },
            "board_classification": {
                "authority": "dated limit-up history; KPL status is retained when explicit",
                "conflict_rows": int(events.get("board_classification_conflict", pd.Series(dtype=bool)).eq(True).sum()),
            },
        },
        "market": market_summary(current_day, events, frames["index_current"], previous_day),
        "ladder": ladder_summary(events, previous_events, current_day),
        "themes": themes,
        "past_mainline_tracking": past_mainline_tracking(history_rows, themes, trade_date),
        "candidate_cards": candidate_cards(events, themes, overlaps, frames["day_history"], frames["limit_history"], names),
        "agent_instructions": {
            "style": "Use MR_Example structure and narrative density; facts first, then connect the story.",
            "must": [
                "Do not produce If-Then sections.",
                "Do not browse or replace packet market facts.",
                "Separate continuous boards from N-day-M-board stocks.",
                "Mention one-price-board tradability risk.",
                "Treat money flow as a reasoned proxy unless a validated exact field is supplied.",
                "Keep past-three-week mainline tracking as a dedicated section.",
                "Recommend no more than five stocks and allow an empty list or no-trade conclusion.",
            ],
        },
    }
    validation = validate_packet(packet)
    write_json(out / "MR_PACKET.json", packet)
    write_json(out / "DATA_QUALITY.json", packet["data_quality"])
    write_json(out / "PACKET_VALIDATION.json", validation)
    if validation["status"] != "PASS":
        raise SystemExit("market-review packet validation failed; inspect PACKET_VALIDATION.json")
    return packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a compact deterministic A-share market-review packet")
    parser.add_argument("--trade-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    packet = build(args)
    print(json.dumps({
        "status": "PASS",
        "trade_date": packet["metadata"]["trade_date"],
        "themes": len(packet["themes"]),
        "candidates": len(packet["candidate_cards"]),
        "output": str(Path(args.output_dir).resolve() / "MR_PACKET.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
