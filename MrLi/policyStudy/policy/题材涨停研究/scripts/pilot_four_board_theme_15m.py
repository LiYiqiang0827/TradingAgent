"""Deterministic 30-event four-board pilot with theme peers and 15m execution.

Exploratory rules were iterated while reviewing this development sample; this
is not a preregistered holdout or evidence of a deployable trading edge.
D0 KPL/graph facts, D1 peer opening prices, and completed 15m bars are inputs.
Future board status and close-to-close path labels are for audit only.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
import hashlib
import json
import random
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_fifteenMin, get_kpl_list, get_oneMin, get_stk_limit, get_theme_daily
from offlineDataManager.scripts.config.settings import ONE_MIN_CATALOG_PATH
from study_core_reactivation_causal import TIMES
from study_four_board_paths import GRAPH


SEED = 20260929
COST = .0031


def load_sample(source: Path, n: int = 30, seed: int = SEED) -> pd.DataFrame:
    whole = pd.read_csv(source, dtype={"ts_code": str, "d0": str, "d1": str})
    whole = whole.sort_values(["d0", "ts_code"]).reset_index(drop=True)
    if len(whole) < n:
        raise ValueError(f"Need {n} events, got {len(whole)}")
    chosen = sorted(random.Random(seed).sample(range(len(whole)), n))
    return whole.iloc[chosen].copy().reset_index(drop=True)


def load_graph_events(graph: Path, first: str, last: str) -> pd.DataFrame:
    con = duckdb.connect(str(graph), read_only=True)
    try:
        return con.execute(
            """SELECT e.trade_date,e.ts_code,e.name,e.tag,e.board_height,r.theme_id
               FROM fact_limit_event e JOIN rel_limit_theme r USING(event_id)
               WHERE e.trade_date BETWEEN ? AND ?
                 AND ((e.tag='涨停' AND r.attribution_role='primary')
                   OR (e.tag='炸板' AND r.attribution_role='auxiliary'))""", [first, last]
        ).df()
    finally:
        con.close()


def d0_ladder(graph_events: pd.DataFrame, event) -> tuple[pd.DataFrame, dict]:
    peers = graph_events[
        graph_events.trade_date.astype(str).eq(event.d0)
        & graph_events.theme_id.eq(event.theme_id)
        & ~graph_events.ts_code.eq(event.ts_code)
        & ~graph_events.name.fillna("").str.contains(r"ST|退", case=False)
        & ~graph_events.ts_code.str.endswith(".BJ")
    ]
    limit = peers[peers.tag.eq("涨停")].drop_duplicates("ts_code")
    heights = pd.to_numeric(limit.board_height, errors="coerce").fillna(1)
    facts = {
        "d0_theme_peer_limit": len(limit),
        "d0_theme_peer_first_board": int(heights.eq(1).sum()),
        "d0_theme_peer_second_board": int(heights.eq(2).sum()),
        "d0_theme_peer_third_plus": int(heights.ge(3).sum()),
        "d0_theme_peer_max_height": int(heights.max()) if len(heights) else 0,
        "d0_theme_peer_failed_limit": int(peers[peers.tag.eq("炸板")].ts_code.nunique()),
    }
    return limit, facts


def d1_peer_open(peers: pd.DataFrame, d0: str, d1: str,
                 daily: dict[tuple[str, str], dict]) -> dict:
    returns = []
    missing = 0
    for code in peers.ts_code:
        old, new = daily.get((code, d0)), daily.get((code, d1))
        if old is None or new is None or float(old["close"]) <= 0:
            missing += 1
        else:
            returns.append(100 * (float(new["open"]) / float(old["close"]) - 1))
    return {
        "d1_peer_open_observed": len(returns), "d1_peer_open_missing": missing,
        "d1_peer_open_positive": sum(value > 0 for value in returns),
        "d1_peer_open_positive_frac": sum(value > 0 for value in returns) / len(returns)
            if returns else np.nan,
        "d1_peer_open_median_pct": float(np.median(returns)) if returns else np.nan,
        "d1_peer_open_mean_pct": float(np.mean(returns)) if returns else np.nan,
    }


def first_break_context(event, break_date: str | None, daily: dict,
                        theme_width: dict) -> dict:
    """End-of-break-day observation, never used for an earlier D1 signal."""
    base = {"first_break_close_vs_d0_pct": np.nan,
            "first_break_theme_width": np.nan,
            "first_break_support_holds": None,
            "first_break_theme_continues": None,
            "first_break_risk_state": "unobserved"}
    if break_date is None:
        return base
    old = daily.get((event.ts_code, event.d0))
    new = daily.get((event.ts_code, break_date))
    width = theme_width.get((event.theme_id, break_date))
    if old is None or new is None or width is None or float(old["close"]) <= 0:
        return base
    close_pct = 100 * (float(new["close"]) / float(old["close"]) - 1)
    support = close_pct >= 0
    continued = width >= 2
    if support and continued:
        state = "support_and_theme"
    elif not support and not continued:
        state = "lost_support_and_theme"
    else:
        state = "mixed"
    return {"first_break_close_vs_d0_pct": close_pct,
            "first_break_theme_width": int(width),
            "first_break_support_holds": support,
            "first_break_theme_continues": continued,
            "first_break_risk_state": state}


def full_bars(stock: pd.DataFrame, calendar: list[str], d0: str,
              end_day: str) -> tuple[pd.DataFrame, str]:
    """Stop at the first missing or invalid bar; never fill later gaps."""
    dates = [d for d in calendar if d0 <= d <= end_day]
    if stock.empty:
        return stock.copy(), "no_15m_stock_bars"
    lookup = stock.set_index("datetime", drop=False)
    collected = []
    for date in dates:
        for clock in TIMES:
            stamp = pd.Timestamp(f"{date} {clock}")
            if stamp not in lookup.index:
                return pd.DataFrame(collected), f"bar_gap_at_{stamp}"
            row = lookup.loc[stamp]
            if isinstance(row, pd.DataFrame):
                return pd.DataFrame(collected), f"duplicate_at_{stamp}"
            if min(float(row.open), float(row.high), float(row.low), float(row.close)) <= 0:
                return pd.DataFrame(collected), f"invalid_ohlc_at_{stamp}"
            collected.append(row)
    return pd.DataFrame(collected).reset_index(drop=True), "complete"


def _ma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def find_signal(bars: pd.DataFrame, event, ladder: dict, peer_open: dict,
                calendar: list[str], statuses: dict, theme_width: dict) -> dict:
    """Return the first causal setup; D1 continuation and post-break pullback."""
    base = {"signal_kind": None, "signal_time": None, "signal_close": np.nan,
            "signal_low": np.nan, "signal_ma10": np.nan, "signal_factor": np.nan,
            "first_break_date": None, "reason": "no_signal"}
    if bars.empty or len(bars[bars.trade_date.astype(str).eq(event.d0)]) != 16:
        return {**base, "reason": "d0_15m_incomplete"}
    if not event.d1 or event.d1 not in calendar:
        return {**base, "reason": "d1_not_observed"}
    own_d0 = bars[bars.trade_date.astype(str).eq(event.d0)]
    anchor = float(own_d0.iloc[-1].close)
    anchor_factor = float(own_d0.iloc[-1].adj_factor)
    if not np.isfinite(anchor_factor) or anchor_factor <= 0:
        return {**base, "reason": "d0_factor_missing"}
    first = bars[bars.trade_date.astype(str).eq(event.d1)]
    ladder_ok = ladder["d0_theme_peer_limit"] >= 2 or ladder["d0_theme_peer_max_height"] >= 2
    peer_frac = peer_open["d1_peer_open_positive_frac"]
    if len(first) and first.iloc[0].datetime.strftime("%H:%M") == "09:45":
        bar = first.iloc[0]
        if abs(float(bar.adj_factor) / anchor_factor - 1) > 1e-5:
            return {**base, "reason": "factor_changed_before_d1_signal"}
        if (ladder_ok and np.isfinite(peer_frac) and peer_frac >= .5
                and bar.close > bar.open and bar.close >= anchor * 1.02):
            return {**base, "signal_kind": "continuation_0945",
                    "signal_time": str(bar.datetime), "signal_close": float(bar.close),
                    "signal_low": float(bar.low), "signal_ma10": float(_ma(bars.close, 10).loc[bar.name]),
                    "signal_factor": float(bar.adj_factor), "reason": "signal"}

    pos = calendar.index(event.d0)
    post_days = calendar[pos + 1:pos + 11]
    break_day = None
    for offset, date in enumerate(post_days, 1):
        if statuses.get((event.ts_code, date)) != f"{4 + offset}连板":
            break_day = date
            break
    base["first_break_date"] = break_day
    if break_day is None:
        return {**base, "reason": "no_break_yet"}
    if not ladder_ok or not np.isfinite(peer_frac) or peer_frac < 1 / 3:
        return {**base, "reason": "d0_ladder_or_d1_peer_open_weak"}
    break_pos = calendar.index(break_day)
    eligible_start = calendar[break_pos + 1] if break_pos + 1 < len(calendar) else None
    if eligible_start is None:
        return {**base, "reason": "no_day_after_break"}
    observed = bars.copy()
    observed["date"] = observed.trade_date.astype(str)
    observed["ma5"] = _ma(observed.close, 5)
    observed["ma10"] = _ma(observed.close, 10)
    observed["ma20"] = _ma(observed.close, 20)
    earliest = observed.index[observed.date.eq(eligible_start)].min()
    if pd.isna(earliest):
        return {**base, "reason": "post_break_15m_missing"}
    for i in range(int(earliest), len(observed)):
        row = observed.iloc[i]
        date = row.date
        if date not in post_days or date <= break_day:
            continue
        if abs(float(row.adj_factor) / anchor_factor - 1) > 1e-5:
            return {**base, "reason": "factor_changed_before_pullback_signal"}
        prior_day = calendar[calendar.index(date) - 1]
        if theme_width.get((event.theme_id, prior_day), 0) < 1:
            continue
        prior = observed.iloc[:i]
        post_anchor_prior = prior[prior.date.gt(event.d0)]
        trough = float(post_anchor_prior.low.min())
        if not np.isfinite(trough):
            continue
        if trough <= anchor * .80:
            return {**base, "reason": "deep_drawdown_before_setup"}
        if not anchor * .85 <= trough <= anchor * .97:
            continue
        if i < 24 or i - int(post_anchor_prior.low.idxmin()) < 2:
            continue
        if min(float(x) for x in observed.iloc[i - 2:i + 1].low) <= trough * 1.005:
            continue
        if float(row.close) > anchor * 1.12:
            continue
        ma5, ma10, ma20 = row.ma5, row.ma10, row.ma20
        if any(pd.isna(v) for v in (ma5, ma10, ma20)):
            continue
        if (row.close > row.open and row.close > max(prior.iloc[-4:].high) * 1.001
                and row.close > ma5 > ma10 and ma5 > observed.iloc[i - 1].ma5
                and row.vol >= prior.iloc[-4:].vol.median() * 1.2):
            return {**base, "signal_kind": "pullback_15m_reclaim",
                    "signal_time": str(row.datetime), "signal_close": float(row.close),
                    "signal_low": float(row.low), "signal_ma10": float(ma10),
                    "signal_factor": float(row.adj_factor),
                    "first_break_date": break_day, "reason": "signal"}
    return base


def minute_schedule(days: list[str]) -> list[pd.Timestamp]:
    return [stamp for day in days for first, last in (("09:31", "11:30"), ("13:01", "15:00"))
            for stamp in pd.date_range(f"{day} {first}", f"{day} {last}", freq="min")]


def execute_minutes(one: pd.DataFrame, fifteen: pd.DataFrame, signal: dict,
                    calendar: list[str], code: str, limits: dict) -> dict:
    """15m decisions; first 1m open at least 60 seconds after each decision."""
    base = {"entry_time": None, "entry_price": np.nan, "exit_time": None,
            "exit_price": np.nan, "exit_reason": None, "net_pct": np.nan,
            "blocked_exit_bars": 0, "status": "not_entered", "entry_day_invalid": False,
            "max_15m_1m_close_difference": 0.0}
    if signal["signal_time"] is None:
        return base
    stamp = pd.Timestamp(signal["signal_time"])
    slots = minute_schedule([d for d in calendar if d >= stamp.strftime("%Y%m%d")
                             and d <= str(fifteen.trade_date.max())])
    # A 1m bar ending at signal+2m opens at signal+1m.
    i = bisect_left(slots, stamp + pd.Timedelta(minutes=2))
    if i >= len(slots):
        return {**base, "status": "entry_beyond_minute_calendar"}
    one = one.copy()
    if one.empty:
        return {**base, "status": "one_minute_missing"}
    one.datetime = pd.to_datetime(one.datetime)
    one = one.set_index("datetime")
    fifteen = fifteen.set_index("datetime")
    entry_end = slots[i]
    if entry_end not in one.index:
        return {**base, "status": "entry_minute_missing"}
    entry_bar = one.loc[entry_end]
    if isinstance(entry_bar, pd.DataFrame):
        return {**base, "status": "entry_duplicate_minute"}
    entry_at = entry_end - pd.Timedelta(minutes=1)
    entry_day = entry_end.strftime("%Y%m%d")
    up_down = limits.get((code, entry_day))
    if up_down is None:
        return {**base, "status": "entry_limit_missing"}
    if not np.isfinite(float(entry_bar.adj_factor)) or abs(
            float(entry_bar.adj_factor) / float(signal["signal_factor"]) - 1) > 1e-5:
        return {**base, "status": "entry_factor_change"}
    entry = float(entry_bar.open)
    if (not np.isfinite(entry) or entry <= 0 or entry_bar.vol <= 0
            or entry >= up_down[0] - .005 or entry > float(signal["signal_close"]) * 1.03):
        return {**base, "status": "entry_unbuyable_proxy"}
    base.update({"entry_time": str(entry_at), "entry_price": entry,
                 "status": "open_unresolved"})
    entry_i = calendar.index(entry_day)
    deadline = calendar[min(entry_i + 5, len(calendar) - 1)]
    close_history = fifteen[fifteen.index < entry_end].close.astype(float).tolist()
    highest_close = entry
    pending_at = None
    pending_reason = None
    for end in slots[i:]:
        if end not in one.index:
            return {**base, "status": "holding_minute_gap", "exit_reason": pending_reason}
        bar = one.loc[end]
        if isinstance(bar, pd.DataFrame):
            return {**base, "status": "holding_duplicate_minute"}
        if not np.isfinite(float(bar.adj_factor)) or abs(
                float(bar.adj_factor) / float(signal["signal_factor"]) - 1) > 1e-5:
            return {**base, "status": "holding_factor_change"}
        now = end.strftime("%Y%m%d")
        open_at = end - pd.Timedelta(minutes=1)
        if now > entry_day and pending_at is None and end.strftime("%H:%M") == "09:31":
            previous_ma10 = np.mean(close_history[-10:]) if len(close_history) >= 10 else np.nan
            if bar.open < signal["signal_low"] or (
                    np.isfinite(previous_ma10) and bar.open < previous_ma10 * .98):
                pending_at, pending_reason = open_at, "overnight_structure_break"
        if pending_at is not None and now > entry_day and open_at >= pending_at + pd.Timedelta(minutes=1):
            lower = limits.get((code, now), (None, None))[1]
            if lower is None:
                return {**base, "status": "exit_limit_missing", "exit_reason": pending_reason}
            if bar.vol > 0 and bar.open > lower + .005:
                base.update({"exit_time": str(open_at), "exit_price": float(bar.open),
                             "exit_reason": pending_reason, "status": "closed",
                             "net_pct": 100 * (float(bar.open) / entry - 1 - COST)})
                return base
            base["blocked_exit_bars"] += 1
        if end not in fifteen.index:
            continue
        row = fifteen.loc[end]
        if isinstance(row, pd.DataFrame):
            return {**base, "status": "holding_duplicate_15m"}
        if abs(float(row.adj_factor) / float(signal["signal_factor"]) - 1) > 1e-5:
            return {**base, "status": "holding_15m_factor_change"}
        base["max_15m_1m_close_difference"] = max(
            base["max_15m_1m_close_difference"], abs(float(row.close) - float(bar.close)))
        close_history.append(float(row.close))
        highest_close = max(highest_close, float(row.close))
        if pending_at is not None:
            continue
        if len(close_history) >= 20:
            ma5 = np.mean(close_history[-5:])
            ma10 = np.mean(close_history[-10:])
            prev_ma5 = np.mean(close_history[-6:-1])
            prior = fifteen.loc[fifteen.index < end].tail(3)
            prev = prior.iloc[-1] if len(prior) else None
            two_red = bool(prev is not None and prev.close < prev.open and row.close < row.open)
            engulf = bool(prev is not None and prev.close > prev.open and row.close < row.open
                          and row.open >= prev.close and row.close <= prev.open)
            breakdown = bool(row.close < ma10 and (ma5 < prev_ma5 or
                             (two_red and len(prior) and row.close < prior.low.min())))
            trail = bool(highest_close >= entry * 1.05 and
                         row.close <= highest_close * .97 and two_red)
            if breakdown or engulf or trail:
                pending_reason = ("ma_break" if breakdown else
                                  "bearish_engulf" if engulf else "trail_two_red")
                pending_at = end
        if pending_at is None and row.close < signal["signal_low"]:
            pending_at, pending_reason = end, "signal_low_broken"
        if now == entry_day and pending_at is not None:
            base["entry_day_invalid"] = True
        if pending_at is None and now >= deadline and end.strftime("%H:%M") >= "14:45":
            pending_at, pending_reason = end, "five_day_safety_exit"
    return {**base, "exit_reason": pending_reason}


def summarize(rows: pd.DataFrame) -> dict:
    closed = rows[rows.status.eq("closed")]
    return {"sample": len(rows), "signal_count": int(rows.signal_kind.notna().sum()),
            "signal_kinds": rows.signal_kind.value_counts().to_dict(),
            "statuses": rows.status.value_counts().to_dict(),
            "closed": len(closed), "mean_net_pct": float(closed.net_pct.mean()) if len(closed) else None,
            "median_net_pct": float(closed.net_pct.median()) if len(closed) else None,
            "win_pct": float(100 * closed.net_pct.gt(0).mean()) if len(closed) else None,
            "worst_pct": float(closed.net_pct.min()) if len(closed) else None,
            "entry_day_invalid_count": int(rows.entry_day_invalid.sum()),
            "blocked_exit_bars": int(rows.blocked_exit_bars.sum())}


def write_case_table(frame: pd.DataFrame, output: Path) -> None:
    names = {
        "continuation_0945": "续板确认", "pullback_15m_reclaim": "回调突破",
    }
    break_names = {
        "support_and_theme": "守价、题材续", "lost_support_and_theme": "失价、题材弱",
        "mixed": "一强一弱", "unobserved": "未判定",
    }
    lines = ["# 固定随机种子 30 例逐轮审计", "",
             "同伴为四板当天同主归因题材涨停股，不含本股；开盘上涨比例只使用这些同伴次日开盘价。",
             "表中未来路径只作事后对照，不参与信号。空收益表示无信号或未满足买入代理。", "",
             "| 四板日 | 股票/题材 | 同伴涨停（2板以上）/炸板 | 次日同伴开涨 | 首次断板收盘 | 信号 | 执行结果 | 净收益 | 事后路径 |",
             "|---|---|---:|---:|---|---|---|---:|---|"]
    for r in frame.itertuples():
        higher = r.d0_theme_peer_second_board + r.d0_theme_peer_third_plus
        peer_open = (f"{r.d1_peer_open_positive}/{r.d1_peer_open_observed}"
                     if r.d1_peer_open_observed else "无同伴")
        signal = names.get(r.signal_kind, "无") if isinstance(r.signal_kind, str) else "无"
        result = (str(r.exit_reason) if r.status == "closed" else str(r.status))
        net = f"{r.net_pct:+.2f}%" if pd.notna(r.net_pct) else "—"
        lines.append(f"| {r.d0} | {r.name} / {r.theme} | "
                     f"{r.d0_theme_peer_limit}（{higher}）/{r.d0_theme_peer_failed_limit} | "
                     f"{peer_open} | {break_names[r.first_break_risk_state]} | "
                     f"{signal} | {result} | {net} | {r.hindsight_path} |")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_example_cases(frame: pd.DataFrame, stocks: dict[str, pd.DataFrame],
                       raw_daily: dict, output: Path) -> None:
    """Illustrative winners and failures selected after the fact, never score data."""
    plt.rcParams["font.family"] = "Arial Unicode MS"
    examples = [("20260112", "600637.SH"), ("20260113", "002342.SZ"),
                ("20260417", "002990.SZ"), ("20260702", "000566.SZ")]
    fig, axes = plt.subplots(4, 1, figsize=(16, 13))
    for ax, (date, code) in zip(axes, examples):
        case = frame[frame.d0.eq(date) & frame.ts_code.eq(code)].iloc[0]
        stock = stocks[code]
        last = pd.Timestamp(case.exit_time if pd.notna(case.exit_time) else case.signal_time)
        view = stock[(stock.datetime >= pd.Timestamp(date))
                     & (stock.datetime <= last + pd.Timedelta(days=1))].copy().reset_index(drop=True)
        xx = np.arange(len(view))
        ax.plot(xx, view.close, color="#273f67", lw=1.4, label="15m close")
        for size, color in [(5, "#d8963e"), (10, "#41a489"), (20, "#a96aa8")]:
            ax.plot(xx, view.close.rolling(size, min_periods=size).mean(),
                    color=color, lw=1.1, label=f"MA{size}")
        anchor = raw_daily.get((code, date))
        if anchor:
            ax.axhline(float(anchor["close"]), ls="--", color="#6b6b6b", lw=.8)
        for label, stamp, price, color in [
            ("signal", case.signal_time, case.signal_close, "#3272b8"),
            ("entry", case.entry_time, case.entry_price, "#19934a"),
            ("exit", case.exit_time, case.exit_price, "#c43639"),
        ]:
            if pd.notna(stamp) and pd.notna(price):
                place = int(view.datetime.searchsorted(pd.Timestamp(stamp)))
                if place < len(view):
                    ax.scatter(place, price, s=58, color=color, zorder=5)
                    ax.annotate(label, (place, price), xytext=(2, 8),
                                textcoords="offset points", fontsize=8, color=color)
        first_per_day = view.groupby("trade_date", sort=True).head(1)
        ax.set_xticks(first_per_day.index)
        ax.set_xticklabels(first_per_day.trade_date.astype(str), rotation=25, ha="right", fontsize=8)
        ax.set_title(f"{case.ts_code} | {case.theme} | peers {case.d0_theme_peer_limit}, "
                     f"D1 open-up {case.d1_peer_open_positive}/{case.d1_peer_open_observed} | "
                     f"net {case.net_pct:+.2f}%", fontsize=10)
        ax.grid(alpha=.2)
    axes[0].legend(loc="upper left", ncol=4, fontsize=8)
    fig.suptitle("Selected four-board pilot cases: 15m structure and 1m execution prices", fontsize=14)
    fig.tight_layout()
    fig.savefig(output, dpi=165)
    plt.close(fig)


def run(source: Path, out: Path, graph: Path = GRAPH,
        end_date: str = "20260928") -> dict:
    sample = load_sample(source)
    first = str(sample.d0.min())
    graph_events = load_graph_events(graph, first, str(sample.d0.max()))
    peer_sets = {}
    ladder = {}
    for event in sample.itertuples():
        peers, facts = d0_ladder(graph_events, event)
        peer_sets[(event.ts_code, event.d0)] = peers
        ladder[(event.ts_code, event.d0)] = facts
    peer_codes = sorted(set(sample.ts_code) | set().union(*(
        set(x.ts_code) for x in peer_sets.values())))
    raw_day = get_day(ts_codes=peer_codes, start_date=first, end_date=end_date,
                      qfq=False, source="database_only")
    raw_day.trade_date = raw_day.trade_date.astype(str)
    daily = raw_day.set_index(["ts_code", "trade_date"]).to_dict("index")
    limits_frame = get_stk_limit(ts_codes=sample.ts_code.unique().tolist(),
                                 start_date=first, end_date=end_date, source="database_only")
    limits = {(r.ts_code, str(r.trade_date)): (float(r.up_limit), float(r.down_limit))
              for r in limits_frame.itertuples()
              if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    cal = sorted(get_day(ts_code="000001.SZ", start_date=first, end_date=end_date,
                         qfq=False, source="database_only").trade_date.astype(str).unique())
    graph_daily = get_theme_daily(start_date=first, end_date=end_date)
    theme_width = {(r.theme_id, str(r.trade_date)): int(r.limit_up_count)
                   for r in graph_daily.itertuples()}
    minute = get_fifteenMin(ts_codes=sample.ts_code.unique().tolist(),
                            start_date=first, end_date=end_date)
    minute.trade_date = minute.trade_date.astype(str)
    minute.datetime = pd.to_datetime(minute.datetime)
    minute_by_stock = {code: x.sort_values("datetime").reset_index(drop=True)
                       for code, x in minute.groupby("ts_code")}
    kpl = get_kpl_list(start_date=first, end_date=end_date, tags="涨停", source="database_only")
    statuses = {(r.ts_code, str(r.trade_date)): str(r.status) for r in kpl.itertuples()}
    rows = []
    prefix_checks = 0
    for event in sample.itertuples():
        peers = peer_sets[(event.ts_code, event.d0)]
        d1_open = d1_peer_open(peers, event.d0, event.d1, daily)
        facts = ladder[(event.ts_code, event.d0)]
        stock = minute_by_stock.get(event.ts_code, pd.DataFrame())
        anchor_i = cal.index(event.d0)
        expected_last = cal[min(anchor_i + 15, len(cal) - 1)]
        available_last = min(expected_last, str(minute.trade_date.max()))
        valid, quality = full_bars(stock, cal, event.d0, available_last)
        if quality == "complete" and available_last < expected_last:
            quality = f"right_censored_15m_at_{available_last}"
        signal = find_signal(valid, event, facts, d1_open, cal, statuses, theme_width)
        break_facts = first_break_context(event, signal["first_break_date"],
                                          daily, theme_width)
        if signal["signal_time"]:
            prefix = valid[valid.datetime <= pd.Timestamp(signal["signal_time"])].copy()
            repeat = find_signal(prefix, event, facts, d1_open, cal, statuses, theme_width)
            if repeat != signal:
                raise AssertionError(f"Future bars altered signal for {event.ts_code} {event.d0}")
            prefix_checks += 1
        one = (get_oneMin(ts_code=event.ts_code,
                          start_date=pd.Timestamp(signal["signal_time"]).strftime("%Y%m%d"),
                          end_date=available_last)
               if signal["signal_time"] else pd.DataFrame())
        outcome = execute_minutes(one, valid, signal, cal, event.ts_code, limits)
        rows.append({"d0": event.d0, "d1": event.d1, "ts_code": event.ts_code,
                     "name": event.name, "theme": event.theme_name,
                     "hindsight_path": event.path, "bar_quality": quality,
                     **facts, **d1_open, **signal, **break_facts, **outcome})
    frame = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "pilot_30_cases.csv", index=False, encoding="utf-8-sig")
    sample[["d0", "ts_code", "name"]].to_csv(out / "sample_manifest.csv", index=False)
    write_case_table(frame, out / "pilot_30_cases.md")
    plot_example_cases(frame, minute_by_stock, daily, out / "four_contrasting_cases.png")
    execution_dates = sorted({pd.Timestamp(value).strftime("%Y%m%d")
                              for col in ("entry_time", "exit_time")
                              for value in frame[col].dropna()})
    source_counts = {}
    if execution_dates and ONE_MIN_CATALOG_PATH.exists():
        con = duckdb.connect(str(ONE_MIN_CATALOG_PATH), read_only=True)
        try:
            source_rows = con.execute(
                "SELECT trade_date,data_source FROM one_min_ingest_catalog WHERE trade_date IN ("
                + ",".join("?" for _ in execution_dates) + ") ORDER BY trade_date",
                execution_dates,
            ).df()
            source_counts = source_rows.data_source.value_counts().to_dict()
            source_rows.to_csv(out / "execution_date_source_catalog.csv", index=False)
        finally:
            con.close()
    audit = {"seed": SEED, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
             "sample_keys_sha256": hashlib.sha256(
                 sample[["d0", "ts_code"]].to_csv(index=False).encode()).hexdigest(),
             "data_last_15m": str(minute.trade_date.max()),
             "data_last_graph_daily": str(graph_daily.trade_date.max()),
             "signal_prefix_invariance_checks": prefix_checks,
             "one_min_execution_date_source_counts": source_counts,
             "mixed_partition_has_per_stock_source_proof": False,
             "rules": {"continuation": "D1 09:45 first 15m bullish and >=D0 close+2%; D0 peer ladder and >=50% peers gap up",
                       "pullback": "first confirmed break, 3-15% drawdown, 15m higher low + MA5>MA10 + four-bar breakout, D1 peer support and prior-day theme width",
                       "entry": "15m decision, then first 1m open >=60s later, below up limit with volume, <=signal close+3%",
                       "exit": "entry-day invalidation carried to D+1 first tradable 1m open; then MA/engulfing/trailing breakdown with >=60s delay; five-day cap",
                       "cost": "flat 0.31% round trip; no queue/slippage proof"},
             "all": summarize(frame),
             "continuation": summarize(frame[frame.signal_kind.eq("continuation_0945")]),
             "pullback": summarize(frame[frame.signal_kind.eq("pullback_15m_reclaim")]),
             "quality": frame.bar_quality.value_counts().to_dict(),
             "max_abs_15m_1m_close_difference": float(frame.max_15m_1m_close_difference.max()),
             "d0_peer_count_distribution": frame.d0_theme_peer_limit.value_counts().sort_index().to_dict()}
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path,
                   default=Path("outputs/four_board_paths_2026/four_board_paths.csv"))
    p.add_argument("--output", type=Path, default=Path("outputs/four_board_30_pilot_2026"))
    p.add_argument("--end-date", default="20260928")
    args = p.parse_args()
    run(args.source, args.output, end_date=args.end_date)


if __name__ == "__main__":
    main()
