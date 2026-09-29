"""Synthetic compact-packet checks; no private identities, outcomes or models."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

import build_core_blind_v2_packets as subject


@pytest.fixture
def source():
    daily = [[f"D_D{day}", f"D{day}", 100, 102, 98, 101, 1]
             for day in range(-60, 0)]
    minutes = []
    for day in range(-5, 1):
        clocks = subject.facts_module.M15_CLOCKS if day < 0 else subject.facts_module.M15_CLOCKS[:3]
        for clock in clocks:
            minutes.append([f"M_D{day}_{clock.replace(':', '')}", f"D{day}", clock,
                            100, 103, 99, 102, .05])
    return {
        "schema_version": "core_pullback_blind.v1", "sample_id": "S01",
        "decision": {"day": "D0", "bar_end": "10:15", "timezone": "Asia/Shanghai"},
        "conventions": deepcopy(subject.CONVENTION_VALUES),
        "anchor": {"id": "ANCHOR", "day": "D-3", "close": 100, "board_height_known_then": 3,
                   "leader_rank_known_then": 2, "theme_rank_known_then": 1, "known_at": "anchor_day_close"},
        "daily_columns": ["id", "day", "open", "high", "low", "close", "volume_ratio"],
        "daily_bars": daily,
        "m15_columns": ["id", "day", "bar_end", "open", "high", "low", "close", "volume_ratio"],
        "m15_bars": minutes,
        "prior_theme": {"id": "THEME_D-1", "day": "D-1", "theme_id": "T1",
                        "eligible_theme_rank": 2, "limit_up_count": 5, "failed_limit_up_count": None,
                        "max_board_height": None, "heat_score": 75, "core_rank_known_then": None,
                        "own_board_height_known_then": None},
        "data_quality": {"missing_past_fields": [], "warnings": [], "observed_daily_bars": 60,
                         "expected_daily_bars": 60, "observed_m15_bars": 83, "expected_m15_bars": 83,
                         "volume_unit_check": {"status": "consistent", "compared_days": 5, "max_relative_error": 0},
                         "future_fields_present": False},
    }


def test_object_bars_preserve_all_daily_and_only_last_two_minute_sessions(source):
    original = deepcopy(source)
    packet = subject.build_compact_packet(source)
    assert source == original
    assert len(packet["daily_bars"]) == 60 and len(packet["m15_bars"]) == 19
    assert all(isinstance(row, dict) for row in packet["daily_bars"] + packet["m15_bars"])
    assert {r["day"] for r in packet["m15_bars"]} == {"D-1", "D0"}
    assert max(r["bar_end"] for r in packet["m15_bars"] if r["day"] == "D0") == "10:15"
    for key in ("decision", "anchor", "prior_theme", "conventions", "data_quality"):
        assert packet[key] == source[key]


def test_facts_are_derived_before_shortening_and_hidden_sources_not_model_evidence(source):
    packet = subject.build_compact_packet(source)
    expected = subject.facts_module.derive(source)["facts"]
    assert {x["id"]: {k: v for k, v in x.items() if k != "id"} for x in packet["facts"]} == expected
    fact = next(x for x in packet["facts"] if x["id"] == "last_m15_volume_vs_five_prior_same_clock_median")
    assert fact["value"] == 1
    assert "M_D-5_1015" in fact["source_ids"]
    assert "M_D-5_1015" in packet["display_scope"]["hidden_source_ids"]
    allowed = subject.allowed_model_evidence_ids(packet)
    assert "M_D-5_1015" not in allowed
    assert fact["id"] in allowed and "M_D-1_1015" in allowed
    assert {"ANCHOR", "THEME_D-1"} <= allowed


def test_missing_current_first_bar_keeps_unknown_and_coverage_metadata(source):
    source["m15_bars"] = [r for r in source["m15_bars"] if r[0] != "M_D0_0945"]
    source["data_quality"]["missing_past_fields"] = ["m15:D0:2_of_3"]
    source["data_quality"]["observed_m15_bars"] = 82
    packet = subject.build_compact_packet(source)
    facts = {f["id"]: f for f in packet["facts"]}
    assert facts["current_first_m15"]["value"] is None
    assert facts["current_first_m15"]["status"] == "unknown"
    assert "low" in facts["current_first_m15"]["missing_fields"]
    assert facts["current_session_observation_coverage"]["value"]["missing_bar_ends"] == ["09:45"]
    assert packet["data_quality"]["missing_past_fields"] == ["m15:D0:2_of_3"]


@pytest.mark.parametrize("mutation", ["top_answer", "quality_answer", "convention_comment", "fact_answer",
                                      "fact_leaf_answer", "scope_answer", "real_code", "real_date",
                                      "daily_future", "minute_future", "hidden_future", "coverage_future"])
def test_disallowed_answers_identity_future_or_unlisted_fields_are_rejected(source, mutation):
    packet = subject.build_compact_packet(source)
    if mutation == "top_answer": packet["assessment"] = "avoid_now"
    elif mutation == "quality_answer": packet["data_quality"]["missing_past_fields"] = ["prior model says buy"]
    elif mutation == "convention_comment": packet["conventions"]["price_formula"] = "the previous reviewer liked it"
    elif mutation == "fact_answer": packet["facts"][0]["model_answer"] = "yes"
    elif mutation == "fact_leaf_answer": packet["facts"][0]["value"]["low"] = {"answer": "yes"}
    elif mutation == "scope_answer": packet["display_scope"]["assessment"] = "wait_more"
    elif mutation == "real_code": packet["facts"][0]["definition"] = "600000.SH"
    elif mutation == "real_date": packet["facts"][0]["definition"] = "2025-01-02"
    elif mutation == "daily_future": packet["daily_bars"][0].update(day="D0", id="D_D0")
    elif mutation == "minute_future": packet["m15_bars"][-1].update(bar_end="10:30", id="M_D0_1030")
    elif mutation == "hidden_future": packet["display_scope"]["hidden_source_ids"].append("M_D+1_0945")
    else:
        fact = next(x for x in packet["facts"] if x["id"] == "current_session_observation_coverage")
        fact["value"]["observed_bar_ends"].append("15:00")
    with pytest.raises(ValueError):
        subject.validate_compact_packet(packet)


def test_future_row_in_source_is_rejected_before_derivation(source):
    source["m15_bars"].append(["M_D0_1030", "D0", "10:30", 100, 105, 99, 104, .1])
    with pytest.raises(ValueError): subject.build_compact_packet(source)


def test_source_json_duplicate_keys_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        subject.strict_json('{"sample_id":"S01","sample_id":"S02"}')


def test_twelve_files_frozen_hashes_and_no_private_or_scoring_reads(tmp_path, monkeypatch, source):
    inputs = tmp_path / "v1/packets"; inputs.mkdir(parents=True)
    output = tmp_path / "v2"
    for sid in subject.SAMPLE_IDS:
        source["sample_id"] = sid
        (inputs / f"{sid}.json").write_bytes(subject.canonical(source))
    original_read = Path.read_bytes
    def guarded(path):
        assert not path.is_relative_to(tmp_path / "v1/private")
        assert not path.is_relative_to(tmp_path / "v1/scoring")
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", guarded)
    manifest = subject.run(inputs, output)
    assert manifest["samples"] == 12 and manifest["model_calls"] == 0
    assert manifest["independent_holdout"] is False
    assert subject.run(inputs, output) == manifest
    for item in manifest["packets"]:
        data = (output / item["packet_file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["input_sha256"]
        subject.validate_compact_packet(json.loads(data))
    path = output / "packets/S01.json"
    original_bytes = path.read_bytes()
    path.write_bytes(original_bytes + b" ")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        subject.run(inputs, output)
    assert path.read_bytes() == original_bytes + b" "
