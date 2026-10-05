"""Synthetic labels exist only in pytest's tests/tmp output directory."""
import json

import numpy as np
import pandas as pd
import pytest

from policyStudy.policy.market_sentiment.human import (
    BAND_LABELS, LABEL_COLUMNS, NEUTRAL_FIELDS, WEATHER_ORDER,
    create_packets, evaluate_labels, machine_band,
)


def daily(n=25, codes=None):
    weather = codes or [code for code in WEATHER_ORDER for _ in range(5)]
    dates = pd.date_range("2025-01-02", periods=n).strftime("%Y-%m-%d")
    result = pd.DataFrame({"trade_date": dates, "mkt_weather": weather[:n],
                           "mkt_hit": [0.] * n, "mkt_cont": [0.] * n, "mkt_act": [0.] * n,
                           "mkt_relay_score": [99.] * n, "mkt_summary": ["SECRET_MACHINE_DESCRIPTION"] * n})
    for field in NEUTRAL_FIELDS:
        result[field] = 1.0
    return result


def labels(dates, **changes):
    frame = pd.DataFrame({"trade_date": dates, "hit": "", "cont": "", "act": "", "weather": "", "notes": ""})
    for column, values in changes.items():
        frame[column] = values
    return frame


def evaluate(tmp_path, source, harry, li, revision=0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    h, l = tmp_path / "Harry.csv", tmp_path / "Li.csv"
    harry.to_csv(h, index=False)
    li.to_csv(l, index=False)
    return evaluate_labels(source, h, l, tmp_path / "evaluation", revision)


@pytest.mark.parametrize("dimension", ["hit", "cont", "act"])
def test_fixed_35_65_edges(dimension):
    low, middle, high = BAND_LABELS[dimension]
    assert [machine_band(value, dimension) for value in (0, 34.999999, 35, 64.999999, 65, 100)] == [low, low, middle, middle, high, high]
    assert machine_band(np.nan, dimension) is None
    with pytest.raises(ValueError, match="0..100"):
        machine_band(100.1, dimension)


def test_packets_reproducible_sorted_neutral_and_independently_blank(tmp_path):
    source = daily()
    snapshot = source.copy(deep=True)
    a = create_packets(source, tmp_path / "a")
    b = create_packets(source.iloc[::-1], tmp_path / "b")
    assert a["status"] == "random_part_ready"
    assert a["counts"] == {"total": 15, "random": 15, "nominated": 0}
    assert all(stratum["final_random_n"] == 3 for stratum in a["strata"].values())
    key_a = pd.read_csv(a["paths"]["selection_manifest"])
    key_b = pd.read_csv(b["paths"]["selection_manifest"])
    pd.testing.assert_frame_equal(key_a, key_b)
    assert key_a.trade_date.is_monotonic_increasing and key_a.trade_date.is_unique
    facts = pd.read_csv(a["paths"]["facts_csv"])
    assert facts.columns.tolist() == ["trade_date", *NEUTRAL_FIELDS]
    assert not {"mkt_hit", "mkt_cont", "mkt_act", "mkt_relay_score", "mkt_weather", "mkt_forecast_n", "mkt_summary"} & set(facts.columns)
    text = (tmp_path / "a/packets/facts.md").read_text(encoding="utf-8")
    assert "SECRET_MACHINE_DESCRIPTION" not in text
    assert not any(code in text for code in WEATHER_ORDER)
    for name in ("Harry", "Li"):
        empty = pd.read_csv(a["paths"][f"{name}_labels"], keep_default_na=False)
        assert empty.columns.tolist() == LABEL_COLUMNS
        assert empty[LABEL_COLUMNS[1:]].eq("").to_numpy().all()
    pd.testing.assert_frame_equal(source, snapshot)


def test_nomination_overlap_replaced_within_same_class_and_deduplicated(tmp_path):
    source = daily()
    initial = create_packets(source, tmp_path / "initial")
    selected = pd.read_csv(initial["paths"]["selection_manifest"])
    overlap = selected.loc[selected.machine_weather.eq("sunny"), "trade_date"].iloc[0]
    nominated = pd.DataFrame({"trade_date": [overlap, overlap], "nominated_by": ["Harry", "Li"]})
    result = create_packets(source, tmp_path / "with_nominations", nominated)
    manifest = pd.read_csv(result["paths"]["selection_manifest"])
    assert result["counts"] == {"total": 16, "random": 15, "nominated": 1}
    assert result["duplicate_nominations_n"] == 1
    assert manifest.trade_date.is_unique
    assert manifest.loc[manifest.trade_date.eq(overlap), "selection"].item() == "nominated"
    replacements = manifest[manifest.selection.eq("replacement")]
    assert len(replacements) == 1 and replacements.machine_weather.item() == "sunny"
    assert result["strata"]["sunny"]["overlap_with_nominations_n"] == 1


def test_small_class_and_exhausted_replacement_pool_are_disclosed(tmp_path):
    source = daily(n=9, codes=["sunny"] * 2 + ["cloudy"] * 4 + ["storm"] * 3)
    initial = create_packets(source, tmp_path / "initial")
    assert initial["counts"]["random"] == 8
    assert initial["strata"]["sunny"]["random_shortfall_n"] == 1
    assert initial["strata"]["thunder"]["candidate_n"] == 0
    assert initial["strata"]["thunder"]["random_shortfall_n"] == 3
    manifest = pd.read_csv(initial["paths"]["selection_manifest"])
    overlap = manifest.loc[manifest.machine_weather.eq("sunny"), "trade_date"].tolist()
    result = create_packets(source, tmp_path / "all_sunny_nominated", pd.DataFrame({"trade_date": overlap}))
    assert result["counts"] == {"total": 8, "random": 6, "nominated": 2}
    assert result["strata"]["sunny"]["replacement_n"] == 0
    assert result["strata"]["sunny"]["random_shortfall_n"] == 3


def test_empty_labels_awaiting_and_zero_common_denominator_never_pass(tmp_path):
    source = daily(n=2, codes=["sunny", "cloudy"])
    empty = labels(source.trade_date)
    result = evaluate(tmp_path / "blank", source, empty, empty)
    assert result["status"] == "awaiting_labels" and result["threshold_revision_count"] == 0
    assert all(result["dimensions"][dimension]["primary"]["n"] == 0 for dimension in BAND_LABELS)
    assert not result["all_dimensions_pass"]
    assert pd.read_csv(result["paths"]["differences"]).empty
    # One person's labels are not a common truth or a reason to consume revision.
    h = labels(source.trade_date, hit="轻", cont="差", act="低")
    result = evaluate(tmp_path / "single", source, h, empty)
    assert result["status"] == "needs_review"
    assert result["dimensions"]["hit"]["machine_vs_harry"]["n"] == 2
    assert result["dimensions"]["hit"]["primary"]["n"] == 0


def test_primary_exact_70_and_failure_cannot_hide_in_overall_average(tmp_path):
    source = daily(n=10, codes=["sunny"] * 10)
    common = labels(source.trade_date, hit=["轻"] * 7 + ["重"] * 3, cont="差", act="低", weather="阴")
    result = evaluate(tmp_path / "70", source, common, common)
    assert result["status"] == "pass"
    assert result["dimensions"]["hit"]["primary"] == {"n": 10, "hits": 7, "accuracy": .7, "machine_unavailable_n": 0, "pass": True}
    assert result["weather_supplement"]["machine_vs_harry"]["accuracy"] == 0
    failed = common.copy()
    failed.loc[6, "hit"] = "重"
    result = evaluate(tmp_path / "failed", source, failed, failed)
    assert result["status"] == "needs_review"
    assert result["dimensions"]["hit"]["primary"]["accuracy"] == .6
    assert result["dimensions"]["cont"]["primary"]["accuracy"] == result["dimensions"]["act"]["primary"]["accuracy"] == 1
    result = evaluate(tmp_path / "last_revision", source, failed, failed, revision=1)
    assert result["status"] == "failed_after_allowed_revision" and result["threshold_revision_count"] == 1
    with pytest.raises(ValueError, match="0 or 1"):
        evaluate(tmp_path / "over_revision", source, failed, failed, revision=2)


def test_dimension_specific_missing_disagreement_and_small_denominator(tmp_path):
    source = daily(n=3, codes=["sunny"] * 3)
    source["mkt_cont"] = 100
    source["mkt_act"] = 50
    h = labels(source.trade_date, hit=["轻", "", "轻"], cont=["好", "一般", ""], act=["中", "中", ""])
    l = labels(source.trade_date, hit=["轻", "重", "重"], cont=["差", "", "好"], act=["中", "", "中"])
    result = evaluate(tmp_path / "missing", source, h, l)
    assert result["dimensions"]["hit"]["primary"]["n"] == 1
    assert result["dimensions"]["hit"]["harry_vs_li"]["n"] == 2
    assert result["dimensions"]["hit"]["harry_vs_li"]["hits"] == 1
    assert result["dimensions"]["cont"]["primary"]["n"] == 0
    assert result["dimensions"]["act"]["primary"]["n"] == 1
    assert result["status"] == "needs_review"
    differences = pd.read_csv(result["paths"]["differences"])
    assert set(NEUTRAL_FIELDS).issubset(differences.columns)
    assert differences.reason.str.contains("Harry_vs_Li").any()
    # A small but positive common denominator remains explicit and can pass.
    one = labels(source.trade_date.iloc[:1], hit="轻", cont="好", act="中")
    result = evaluate(tmp_path / "small", source, one, one)
    assert result["status"] == "pass"
    assert all(result["dimensions"][dimension]["primary"]["n"] == 1 for dimension in BAND_LABELS)
    source.loc[0, "mkt_hit"] = np.nan
    result = evaluate(tmp_path / "machine_missing", source, one, one)
    assert result["dimensions"]["hit"]["primary"]["n"] == 1
    assert result["dimensions"]["hit"]["primary"]["machine_unavailable_n"] == 1
    assert result["status"] == "needs_review"


def test_real_annotation_never_overwritten_and_invalid_labels_rejected(tmp_path):
    source = daily()
    result = create_packets(source, tmp_path / "pack")
    path = result["paths"]["Harry_labels"]
    actual = pd.read_csv(path, keep_default_na=False)
    actual.loc[0, "notes"] = "Remembered independently"
    actual.to_csv(path, index=False)
    before = open(path, "rb").read()
    with pytest.raises(FileExistsError, match="Preserve"):
        create_packets(source, tmp_path / "pack")
    assert open(path, "rb").read() == before
    bad = labels(source.trade_date, hit="invented")
    with pytest.raises(ValueError, match="invalid hit"):
        evaluate(tmp_path / "bad", source, bad, labels(source.trade_date))
    duplicate = pd.concat([actual, actual.iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate dates"):
        evaluate(tmp_path / "duplicate", source, duplicate, labels(source.trade_date))
