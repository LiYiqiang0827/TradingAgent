"""Boundaries that make anonymous model packets causally reproducible."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from build_core_pullback_blind_packets import (
    BAR_TIMES, SOURCE_COLUMNS, build_packet, canonical_bytes, select_samples,
)
from run_core_pullback_blind import validate_packet


@pytest.fixture
def history():
    cal = pd.bdate_range("2025-01-02", periods=70).strftime("%Y%m%d").tolist()
    today, anchor = cal[60], cal[57]
    signal = {"ts_code": "600001.SH", "signal_time": f"{today} 10:15:00",
              "anchor_date": anchor, "anchor_close": 10.0,
              "anchor_board_height": 3, "anchor_leader_rank": 2,
              "anchor_theme_rank": 1, "prior_theme_heat": 75,
              "prior_theme_rank": 2, "prior_theme_width": 5}
    daily = pd.DataFrame([{"trade_date": d, "open": 10.0, "high": 11.0,
                           "low": 9.0, "close": 10.0, "vol": 1600} for d in cal])
    factors = pd.DataFrame([{"trade_date": d, "adj_factor": 1} for d in cal])
    minutes = pd.DataFrame([{"trade_date": d, "datetime": pd.Timestamp(f"{d} {t}"),
                             "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "vol": 10000,
                             "name": "secret name", "adj_factor": 1}
                            for d in cal[55:] for t in BAR_TIMES])
    return signal, cal, daily, factors, minutes


def make(history):
    return build_packet("S01", *history)


def test_all_future_bars_removed_or_poisoned_leave_packet_bytes_identical(history):
    original, _ = make(history)
    signal, cal, d, f, m = history
    cutoff = pd.Timestamp(signal["signal_time"])
    altered = m.copy()
    future = altered.datetime.gt(cutoff)
    altered.loc[future, ["open", "high", "low", "close", "vol", "adj_factor"]] = 999999999
    altered.loc[future, "name"] = "FUTURE_WINNER"
    a, _ = make((signal, cal, d, f, altered))
    b, _ = make((signal, cal, d, f, m[m.datetime.le(cutoff)]))
    assert canonical_bytes(original) == canonical_bytes(a) == canonical_bytes(b)


def test_future_daily_and_adjustment_changes_cannot_affect_packet(history):
    original, _ = make(history)
    signal, cal, d, f, m = history
    today = pd.Timestamp(signal["signal_time"]).strftime("%Y%m%d")
    d = d.copy(); f = f.copy()
    d.loc[d.trade_date.ge(today), ["open", "high", "low", "close", "vol"]] = 999999
    f.loc[f.trade_date.gt(today), "adj_factor"] = 999999
    altered, _ = make((signal, cal, d, f, m))
    assert canonical_bytes(original) == canonical_bytes(altered)


def test_volume_units_anchor_and_anonymization(history):
    packet, private = make(history)
    validate_packet(packet)
    assert packet["anchor"]["id"] == "ANCHOR"
    assert packet["anchor"]["close"] == 100
    assert packet["anchor"]["leader_rank_known_then"] == 2
    assert packet["prior_theme"]["core_rank_known_then"] is None
    assert len(packet["daily_bars"]) == 60
    assert len(packet["m15_bars"]) == 83
    assert all(row[-1] == 1.0 for row in packet["daily_bars"])
    assert all(row[-1] == .0625 for row in packet["m15_bars"])
    assert private["volume_denominator_shares"] == 160000
    assert packet["data_quality"]["volume_unit_check"]["status"] == "consistent"
    text = canonical_bytes(packet).decode()
    assert "600001.SH" not in text and "2025" not in text and "secret name" not in text
    assert all(row[1] != "D0" for row in packet["daily_bars"])


def test_missing_history_preserves_sample_and_reports_unknown(history):
    signal, cal, d, f, m = history
    day = cal[59]
    packet, _ = make((signal, cal, d[d.trade_date.ne(day)], f[f.trade_date.ne(day)], m))
    assert packet["sample_id"] == "S01"
    assert "daily:D-1" in packet["data_quality"]["missing_past_fields"]
    assert "factor:D-1" in packet["data_quality"]["missing_past_fields"]
    assert "prior_20_completed_daily_volume" in packet["data_quality"]["missing_past_fields"]
    assert all(row[-1] is None for row in packet["m15_bars"])
    assert all(row[3] is None for row in packet["m15_bars"] if row[1] == "D-1")


def test_volume_unit_failure_is_unknown_not_a_hidden_rescaling(history):
    signal, cal, d, f, m = history
    m = m.copy(); m["vol"] /= 100
    packet, _ = make((signal, cal, d, f, m))
    assert packet["data_quality"]["volume_unit_check"]["status"] == "mismatch"
    assert all(row[-1] is None for row in packet["daily_bars"])


def test_factor_adjustment_is_relative_to_anchor_at_that_time(history):
    signal, cal, d, f, m = history
    f = f.copy(); d = d.copy(); m = m.copy()
    split = cal[59]
    f.loc[f.trade_date.ge(split), "adj_factor"] = 2
    d.loc[d.trade_date.ge(split), ["open", "high", "low", "close"]] /= 2
    m.loc[m.trade_date.ge(split), ["open", "high", "low", "close"]] /= 2
    packet, _ = make((signal, cal, d, f, m))
    assert all(row[2] == 100 for row in packet["daily_bars"])
    assert all(row[3] == 100 for row in packet["m15_bars"])


def test_sampling_reads_only_whitelist_and_does_not_use_outcomes(tmp_path, monkeypatch):
    rows = []
    for i in range(30):
        row = {c: 1 for c in SOURCE_COLUMNS}
        row.update(ts_code=f"{i // 2:06}.SH", signal_time=f"2025-03-{i % 20 + 1:02} 13:30:00",
                   anchor_date="20250301", anchor_close=10, old_net=i, carry_net=-i,
                   outcome="winner" if i % 2 else "loser")
        rows.append(row)
    p = tmp_path / "signals.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    original = pd.read_csv
    calls = []
    def spy(*args, **kwargs):
        calls.append(kwargs.get("usecols"))
        return original(*args, **kwargs)
    monkeypatch.setattr(pd, "read_csv", spy)
    pool, chosen = select_samples(p)
    assert len(pool) == 30 and len(chosen) == 12
    assert len({x["ts_code"] for x in chosen}) == 12
    assert calls == [list(SOURCE_COLUMNS)]
    for row in rows:
        row["old_net"], row["carry_net"], row["outcome"] = -999999, 999999, "changed"
    pd.DataFrame(rows[::-1]).to_csv(p, index=False)
    _, chosen2 = select_samples(p)
    assert canonical_bytes(chosen) == canonical_bytes(chosen2)
    assert all(set(row) == set(SOURCE_COLUMNS) for row in chosen)


def test_packet_output_is_strict_json(history):
    packet, _ = make(history)
    assert json.loads(canonical_bytes(packet))["schema_version"] == "core_pullback_blind.v1"
