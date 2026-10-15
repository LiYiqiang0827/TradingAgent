"""Tests for outcome labels that must not silently collapse ambiguous paths."""

from study_four_board_paths import classify_broken_path


def bars(*, deep_day=None, recovery_day=None):
    result = []
    for index in range(1, 11):
        result.append({
            "trade_date": f"D{index}",
            "low": 79.0 if index == deep_day else 91.0,
            "close": 101.0 if index == recovery_day else 92.0,
        })
    return result


def test_relimit_precedes_deep_drawdown():
    assert classify_broken_path(bars(deep_day=5), {"D3"}, 100) == (
        "relimit_before_deep_drawdown", "D3")


def test_deep_drawdown_precedes_relimit():
    assert classify_broken_path(bars(deep_day=2), {"D5"}, 100) == (
        "deep_drawdown_before_relimit", "D2")


def test_same_day_order_is_unknown_without_minutes():
    assert classify_broken_path(bars(deep_day=3), {"D3"}, 100) == (
        "same_day_order_unknown", "D3")


def test_incomplete_path_keeps_censoring():
    assert classify_broken_path(bars()[:9], set(), 100) == (
        "right_censored_or_missing", None)


def test_close_recovery_without_relimit_is_distinct():
    assert classify_broken_path(bars(recovery_day=4), set(), 100) == (
        "reclaimed_close_without_relimit", None)
