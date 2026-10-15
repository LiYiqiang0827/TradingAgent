"""Independent prefix/definition tests; no historical outcomes are loaded."""
from __future__ import annotations

import pandas as pd
import pytest

import study_core_pullback_structure as subject
from study_core_reactivation_causal import schedule


@pytest.fixture
def structure():
    calendar = pd.bdate_range("2025-01-06", periods=8).strftime("%Y%m%d").tolist()
    stamps = schedule(calendar)
    stock = pd.DataFrame({
        "ts_code": "000001.SZ", "datetime": stamps,
        "trade_date": [int(t.strftime("%Y%m%d")) for t in stamps],
        "open": 10.0, "high": 10.2, "low": 9.0, "close": 10.0,
        "vol": 100.0, "adj_factor": 1.0,
    })
    # Signal = day 5, second bar. The immediately preceding 32 scheduled
    # bars contain two full, time-of-day-balanced volume windows.
    signal_index = 5 * 16 + 1
    stock.loc[signal_index - 16:signal_index - 1, "vol"] = 60.0
    stock.loc[68, "low"] = 8.5
    stock.loc[74, "low"] = 8.6
    stock.loc[50, "high"] = 14.0
    signal = dict(
        ts_code="000001.SZ", name="synthetic", theme_id="T", theme_name="test",
        anchor_date=calendar[1], signal_time=str(stamps[signal_index]),
        signal_close=10.0, signal_factor=1.0,
    )
    return stock, signal, calendar, signal_index


def test_reference_fixture_forms_all_three_structures(structure):
    stock, signal, calendar, _ = structure
    result = subject.features(stock, signal, calendar)
    assert result["data_status"] == "known"
    assert result["A"] and result["B"] and result["C"]
    assert result["volume_ratio"] == pytest.approx(.6)
    assert result["last_low"] == pytest.approx(8.6)


def test_a_excludes_signal_bar_even_at_median_boundary(structure):
    stock, signal, calendar, index = structure
    stock.loc[index - 16:index - 1, "vol"] = [100.0] * 8 + [60.0] * 8
    stock.loc[index, "vol"] = 0.0
    result = subject.features(stock, signal, calendar)
    assert result["volume_ratio"] == pytest.approx(.8)
    assert result["A"] is True
    stock.loc[index, "vol"] = 1e12
    assert subject.features(stock, signal, calendar)["volume_ratio"] == pytest.approx(.8)


@pytest.mark.parametrize("anchor_position, expected", [(2, True), (3, False)])
def test_a_requires_two_complete_intervening_sessions(structure, anchor_position, expected):
    stock, signal, calendar, _ = structure
    signal = {**signal, "anchor_date": calendar[anchor_position]}
    assert subject.features(stock, signal, calendar)["A"] is expected


@pytest.mark.parametrize("signal_offset, expected", [(4, []), (5, [2])])
def test_pivot_confirmation_must_be_strictly_before_signal(signal_offset, expected):
    stamps = pd.date_range("2025-01-06 09:45", periods=6, freq="15min")
    frame = pd.DataFrame({"datetime": stamps, "low": [11, 10, 9, 10, 11, 12]})
    assert subject.confirmed_lows(frame, stamps[signal_offset]) == expected


def test_equal_left_low_is_not_a_strict_pivot_but_equal_right_is_allowed():
    stamps = pd.date_range("2025-01-06 09:45", periods=6, freq="15min")
    frame = pd.DataFrame({"datetime": stamps, "low": [10, 9, 9, 10, 11, 12]})
    assert subject.confirmed_lows(frame, stamps[-1]) == []
    frame["low"] = [11, 10, 9, 9, 11, 12]
    assert subject.confirmed_lows(frame, stamps[-1]) == [2]


def test_b_invalidation_includes_signal_bar_low(structure):
    stock, signal, calendar, index = structure
    assert subject.features(stock, signal, calendar)["B"] is True
    stock.loc[index, "low"] = 8.59
    result = subject.features(stock, signal, calendar)
    assert result["last_low"] == pytest.approx(8.6)
    assert result["B"] is False


def test_b_cannot_choose_an_older_more_favorable_pair(structure):
    stock, signal, calendar, _ = structure
    stock.loc[78, "low"] = 8.55
    result = subject.features(stock, signal, calendar)
    assert result["last_low"] == pytest.approx(8.55)
    assert result["prior_low"] == pytest.approx(8.6)
    assert result["B"] is False


def test_first_post_anchor_pivot_can_use_known_anchor_day_left_neighbors(structure):
    stock, signal, calendar, _ = structure
    stock["low"] = 9.0
    stock.loc[32, "low"] = 8.5  # first bar after anchor close, left bars already known
    stock.loc[40, "low"] = 8.6
    result = subject.features(stock, signal, calendar)
    assert result["confirmed_low_count"] == 2
    assert result["prior_low"] == pytest.approx(8.5)
    assert result["B"] is True


def test_c_pressure_uses_exactly_three_previous_complete_sessions(structure):
    stock, signal, calendar, index = structure
    stock.loc[16, "high"] = 999.0  # D-4, outside pressure window
    stock.loc[index - 1:index, "high"] = 888.0  # D partial session, outside
    result = subject.features(stock, signal, calendar)
    assert result["pressure"] == pytest.approx(14.0)
    stock.loc[32, "high"] = 16.0  # D-3, must be included
    assert subject.features(stock, signal, calendar)["pressure"] == pytest.approx(16.0)


@pytest.mark.parametrize("missing_index", [30, 33, 65])
def test_missing_planned_bar_is_unknown_not_skipped(structure, missing_index):
    stock, signal, calendar, _ = structure
    stock = stock.drop(missing_index)
    result = subject.features(stock, signal, calendar)
    assert result["data_status"] == "observed_prefix_gap_or_duplicate"
    assert all(result[f"{term}_status"].startswith("unknown") for term in "ABC")
    assert not any(result[term] for term in "ABC")


def test_complete_data_without_pivots_is_known_nonselection(structure):
    stock, signal, calendar, _ = structure
    stock["low"] = 9.0
    result = subject.features(stock, signal, calendar)
    assert result["B_status"] == "known_no_confirmed_support"
    assert result["C_status"] == "known_no_confirmed_support"
    assert result["B"] is False and result["C"] is False


def test_future_corruption_duplication_and_removal_cannot_change_features(structure):
    stock, signal, calendar, index = structure
    expected = subject.features(stock, signal, calendar)
    altered = stock.copy()
    altered.loc[index + 1:, ["open", "high", "low", "close", "vol", "adj_factor"]] = -999.0
    altered = pd.concat([altered, altered.iloc[[index + 2]]], ignore_index=True)
    assert subject.features(altered, signal, calendar) == expected
    assert subject.features(stock.iloc[:index + 1], signal, calendar) == expected


def test_outcome_fields_cannot_influence_features(structure):
    stock, signal, calendar, _ = structure
    expected = subject.features(stock, signal, calendar)
    contaminated = {**signal, "old_net": 1000000.0, "carry_net": -1000000.0,
                    "old_buyable": False, "carry_status": "unresolved",
                    "future_high": 99999999.0}
    assert subject.features(stock, contaminated, calendar) == expected
    assert not set(contaminated).difference(subject.INPUT_COLUMNS).intersection(expected)


def test_extract_known_false_dominates_unknown_in_conjunctions(structure, tmp_path, monkeypatch):
    stock, signal, calendar, _ = structure
    states = {
        "unknown_A_false_B": (False, False, True, "unknown", "known", "known"),
        "unknown_A_true_B": (False, True, True, "unknown", "known", "known"),
        "false_A_unknown_B": (False, False, True, "known", "unknown", "known"),
        "true_AB_unknown_C": (True, True, False, "known", "known", "unknown"),
    }
    rows = [{**signal, "name": name, "old_net": -999.0} for name in states]
    source = tmp_path / "synthetic_signals.csv"
    pd.DataFrame(rows).to_csv(source, index=False)
    out = tmp_path / "features"
    out.mkdir()
    monkeypatch.setattr(subject, "get_tradecal", lambda **kwargs: pd.DataFrame({"cal_date": calendar}))
    monkeypatch.setattr(subject, "get_fifteenMin", lambda **kwargs: stock)

    def controlled_features(frame, row, dates):
        assert set(row) == set(subject.INPUT_COLUMNS)
        a, b, c, sa, sb, sc = states[row["name"]]
        return {**row, "data_status": "synthetic", "A": a, "B": b, "C": c,
                "A_status": sa, "B_status": sb, "C_status": sc}

    monkeypatch.setattr(subject, "features", controlled_features)
    subject.extract(source, out)
    result = pd.read_csv(out / "features.csv").set_index("name")
    assert not bool(result.loc["unknown_A_false_B", "unknown_A_B"])
    assert bool(result.loc["unknown_A_true_B", "unknown_A_B"])
    assert not bool(result.loc["false_A_unknown_B", "unknown_A_B"])
    assert bool(result.loc["true_AB_unknown_C", "unknown_A_B_C"])
    assert not result.selected_A_B_C.any()
