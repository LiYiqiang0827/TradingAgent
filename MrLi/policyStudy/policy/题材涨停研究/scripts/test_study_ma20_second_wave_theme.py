"""Cutoff and old-cohort checks for the theme overlay."""

from __future__ import annotations

import pandas as pd

import study_ma20_second_wave_theme as study


def test_restart_requires_two_market_days_without_a_board():
    calendar = ["20260810", "20260811", "20260812", "20260813",
                "20260814", "20260817", "20260818"]
    kpl = pd.DataFrame([
        ("A", "20260810"), ("A", "20260811"), ("A", "20260814"),
        ("B", "20260810"), ("B", "20260813"),
        ("C", "20260810"), ("C", "20260812"),
    ], columns=["ts_code", "trade_date"])
    assert study._restart_codes(kpl, {"A", "B", "C"}, "20260811",
                                "20260813", calendar) == {"B"}
    assert study._restart_codes(kpl, {"A", "B", "C"}, "20260811",
                                "20260814", calendar) == {"A", "B"}


def test_touch_features_freeze_peak_cohort_and_use_as_of_cutoff(monkeypatch):
    calendar = ["20260810", "20260811", "20260812", "20260813",
                "20260814", "20260817"]
    members = pd.DataFrame([
        ("A", "20260810"), ("X", "20260811"),
        ("B", "20260812"), ("C", "20260814"),
    ], columns=["ts_code", "trade_date"])
    theme_daily = pd.DataFrame([
        ("20260810", 1, 1.0, 1, "E"),
        ("20260811", 1, 2.0, 2, "E"),
        ("20260812", 1, 3.0, 2, "E"),
        ("20260814", 1, 4.0, 1, "E"),
    ], columns=["trade_date", "limit_up_count", "heat_score",
                "max_board_height", "episode_id"])

    def fake_members(theme_id, *, as_of, historical):
        assert (theme_id, as_of, historical) == ("T", "20260814", True)
        return members.copy()

    def fake_daily(*, theme_id, start_date, end_date):
        assert (theme_id, start_date, end_date) == (
            "T", "20260810", "20260814")
        return theme_daily.copy()

    def fake_day(*, ts_codes, trade_date, qfq, source):
        assert (ts_codes, trade_date, qfq, source) == (
            ["A", "B"], "20260814", False, "database_only")
        return pd.DataFrame({"ts_code": ["A", "B"], "pct_chg": [7.5, 2.0]})

    monkeypatch.setattr(study, "get_theme_members", fake_members)
    monkeypatch.setattr(study, "get_theme_daily", fake_daily)
    monkeypatch.setattr(study, "get_day", fake_day)
    result = study._prefix_theme_features(
        "T", "X", "20260811", "20260812", "20260814", calendar)
    assert result["frozen_old_peer_codes"] == "A,B"
    assert result["theme_width"] == 1
    assert result["old_peer_limit"] == 0
    assert result["nonfrozen_peer_limit"] == 1
    assert result["old_peer_strong7_codes"] == "A"
    assert result["member_width_matches_daily"]
