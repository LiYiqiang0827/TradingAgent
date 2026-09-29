"""Synthetic isolation/resumption tests; no private packets or models are read."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import run_core_pullback_blind as subject


@pytest.fixture
def packet():
    return {
        "schema_version": "core_pullback_blind.v1", "sample_id": "S01",
        "decision": {"day": "D0", "bar_end": "10:00", "timezone": "Asia/Shanghai"},
        "conventions": {
            "bar_time_is_end": True, "prices_normalized_to_anchor_close_100": True,
            "volume_is_ratio_to_prior_20_completed_daily_median": True,
            "execution_not_assessed": True,
            "price_adjustment_uses_only_factors_known_at_cutoff": True,
        },
        "anchor": {"id": "ANCHOR", "day": "D-3", "close": 100,
                   "board_height_known_then": 3},
        "daily_columns": ["id", "day", "open", "high", "low", "close", "volume_ratio"],
        "daily_bars": [["D_D-2", "D-2", 100, 103, 98, 101, 1.1],
                       ["D_D-1", "D-1", 101, 104, 98, 102, 1.0]],
        "m15_columns": ["id", "day", "bar_end", "open", "high", "low", "close", "volume_ratio"],
        "m15_bars": [["M_D0_0945", "D0", "09:45", 101, 102, 100, 101, .1],
                     ["M_D0_1000", "D0", "10:00", 101, 103, 100, 102, .2]],
        "prior_theme": {"id": "THEME_D-1", "day": "D-1", "theme_id": "T1",
                        "eligible_theme_rank": 1, "limit_up_count": 5,
                        "failed_limit_up_count": 2, "max_board_height": 4,
                        "heat_score": 80, "core_rank_known_then": 2,
                        "own_board_height_known_then": 3},
        "data_quality": {"missing_past_fields": [], "future_fields_present": False},
    }


@pytest.fixture
def answer():
    return {
        "schema_version": "core_pullback_blind_result.v1", "sample_id": "S01",
        "as_of": {"day": "D0", "bar_end": "10:00"},
        "assessment": "wait_more", "structure_state": "unclear",
        "checks": {name: {"value": "unknown", "evidence_ids": [],
                          "reason": "只见有限历史，暂不确认结构。"} for name in subject.CHECKS},
        "levels": [{"kind": "support", "lower": 98, "upper": 100,
                    "evidence_ids": ["D_D-1"], "reason": "既有日线范围。"}],
        "supporting_evidence": [], "opposing_evidence": [],
        "next_confirmation": [], "invalidation": [],
        "missing_information": [], "evidence_sufficiency": "low",
    }


def test_minimal_whitelisted_packet_and_result_are_valid(packet, answer):
    subject.validate_packet(packet)
    assert subject.parse_result(json.dumps(answer), packet) == answer


@pytest.mark.parametrize("text", ["000001.SZ", "600000.SH", "830001.BJ",
                                  "2025-01-02", "2025/01/02", "20250102"])
def test_real_codes_and_absolute_dates_are_rejected_anywhere(packet, text):
    packet["data_quality"]["missing_past_fields"] = [text]
    with pytest.raises(ValueError):
        subject.validate_packet(packet)


@pytest.mark.parametrize("container, field, value", [
    ("prior_theme", "future_return", .2),
    ("data_quality", "real_stock_name", "not allowed"),
    ("anchor", "ts_code", "000001"),
])
def test_nested_packet_fields_cannot_smuggle_unlisted_information(packet, container, field, value):
    packet[container][field] = value
    with pytest.raises(ValueError):
        subject.validate_packet(packet)


def test_future_minute_bar_is_rejected(packet):
    packet["m15_bars"].append(["M_D0_1015", "D0", "10:15", 102, 104, 101, 103, .1])
    with pytest.raises(ValueError):
        subject.validate_packet(packet)


def test_future_day_is_rejected(packet):
    packet["m15_bars"].append(["M_D+1_0945", "D+1", "09:45", 102, 104, 101, 103, .1])
    with pytest.raises(ValueError):
        subject.validate_packet(packet)


def test_d0_daily_bar_cannot_bypass_rejection_by_using_an_unusual_id(packet):
    packet["daily_bars"].append(["MISLABELLED_DAILY", "D0", 102, 104, 101, 103, 1.0])
    with pytest.raises(ValueError):
        subject.validate_packet(packet)


def test_future_label_cannot_be_attached_to_a_past_bar(packet):
    packet["m15_bars"][0][0] = "M_D+1_0945"
    with pytest.raises(ValueError):
        subject.validate_packet(packet)


def test_theme_evidence_id_cannot_overwrite_a_bar(packet):
    packet["prior_theme"]["id"] = "D_D-1"
    with pytest.raises(ValueError):
        subject.validate_packet(packet)


@pytest.mark.parametrize("eid", ["NONEXISTENT", "M_D0_1015", "M_D+1_0945"])
def test_result_cannot_cite_unknown_or_future_evidence(packet, answer, eid):
    answer["checks"]["daily_trend_preserved"].update(value="yes", evidence_ids=[eid])
    with pytest.raises(ValueError):
        subject.parse_result(json.dumps(answer), packet)


def test_price_level_outside_cited_range_is_rejected(packet, answer):
    answer["levels"][0]["upper"] = 1000
    with pytest.raises(ValueError):
        subject.parse_result(json.dumps(answer), packet)


@pytest.mark.parametrize("raw", ["not json", "{", "[]", "{}", "```json\n{}\n```"])
def test_malformed_or_non_schema_json_is_not_silently_repaired(packet, raw):
    with pytest.raises((ValueError, TypeError)):
        subject.parse_result(raw, packet)


@pytest.mark.parametrize("key", ["assessment", "structure_state", "evidence_sufficiency"])
def test_unknown_top_level_enum_is_rejected(packet, answer, key):
    answer[key] = "unregistered"
    with pytest.raises(ValueError):
        subject.parse_result(json.dumps(answer), packet)


def test_unknown_check_enum_is_rejected(packet, answer):
    answer["checks"]["higher_low_confirmed"]["value"] = "probably"
    with pytest.raises(ValueError):
        subject.parse_result(json.dumps(answer), packet)


@pytest.fixture
def fake_run(tmp_path, monkeypatch, packet, answer):
    directory = tmp_path / "blind"
    (directory / "packets").mkdir(parents=True)
    subject.save(directory / "packets/S01.json", packet)
    prompt = tmp_path / "frozen_prompt.txt"
    prompt.write_text("Read only this anonymous packet:\n{{PACKET_JSON}}", encoding="utf-8")
    monkeypatch.setattr(subject, "PROMPT", prompt)
    transport_file = tmp_path / "synthetic_transport.py"
    transport_file.write_text("# stable fake transport; no network\n", encoding="utf-8")
    calls = []
    responses = []

    def payload(prompt_text, max_tokens=4096):
        return {"model": "MiniMax-Synthetic", "messages": [{"role": "user", "content": prompt_text}],
                "max_tokens": max_tokens, "stream": False}

    def call_model(prompt_text, timeout=180, max_tokens=4096):
        body = payload(prompt_text, max_tokens)
        assert "tools" not in body and "tool_choice" not in body
        assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "user"
        calls.append(deepcopy(body))
        text = responses.pop(0) if responses else json.dumps(answer, ensure_ascii=False)
        if isinstance(text, Exception):
            raise text
        raw = json.dumps({"model": "MiniMax-Synthetic", "content": [{"type": "text", "text": text}]}).encode()
        return {"raw_response_bytes": raw, "raw_text": text, "request_payload": body,
                "request_body_bytes": subject.canonical(body), "stop_reason": "end_turn",
                "response_model": "MiniMax-Synthetic", "usage": None, "elapsed": .01}

    fake = SimpleNamespace(__file__=str(transport_file),
                           configuration_public=lambda: {"model": "MiniMax-Synthetic", "tools_enabled": False},
                           request_payload=payload, call_model=call_model)
    monkeypatch.setitem(sys.modules, "core_blind_model_transport", fake)
    return directory, calls, responses


def test_fake_transport_succeeds_with_no_tools_and_archives_exact_request(fake_run):
    directory, calls, _ = fake_run
    subject.run(directory)
    assert len(calls) == 1
    target = directory / "runs/minimax/S01"
    manifest = json.loads((target / "RESULT_MANIFEST.json").read_text())
    archived = json.loads((target / "attempt_01/request.json").read_text())
    assert manifest["status"] == "valid"
    assert archived == calls[0]
    assert "tools" not in archived
    assert (directory / "FROZEN_OUTPUTS.json").exists()


@pytest.mark.parametrize("failure", ["not json", TimeoutError("synthetic transport failure")])
def test_at_most_two_total_attempts_even_when_every_call_fails(fake_run, failure):
    directory, calls, responses = fake_run
    responses.extend([failure, failure, failure])
    subject.run(directory)
    assert len(calls) == 2
    assert not (directory / "runs/minimax/S01/attempt_03").exists()
    # A sealed unsuccessful result remains in the denominator, not an unbounded retry loop.
    subject.run(directory)
    assert len(calls) == 2


def test_one_invalid_json_then_valid_answer_uses_only_one_repair(fake_run):
    directory, calls, responses = fake_run
    responses.append("not json")
    subject.run(directory)
    assert len(calls) == 2
    target = directory / "runs/minimax/S01"
    assert json.loads((target / "RESULT_MANIFEST.json").read_text())["status"] == "valid"
    assert (target / "attempt_01/raw_text.txt").read_text() == "not json"


def test_completed_sample_is_not_resent(fake_run):
    directory, calls, _ = fake_run
    subject.run(directory)
    original = (directory / "runs/minimax/S01/attempt_01/raw_response.json").read_bytes()
    subject.run(directory)
    assert len(calls) == 1
    assert (directory / "runs/minimax/S01/attempt_01/raw_response.json").read_bytes() == original


def test_changed_packet_is_rejected_before_resume_call(fake_run):
    directory, calls, _ = fake_run
    subject.run(directory)
    path = directory / "packets/S01.json"
    changed = json.loads(path.read_text())
    changed["prior_theme"]["heat_score"] = 79
    subject.save(path, changed)
    with pytest.raises(ValueError):
        subject.run(directory)
    assert len(calls) == 1


def test_changed_valid_parsed_output_is_rejected_on_resume(fake_run):
    directory, calls, _ = fake_run
    subject.run(directory)
    path = directory / "runs/minimax/S01/parsed.json"
    changed = json.loads(path.read_text())
    changed["assessment"] = "avoid_now"
    subject.save(path, changed)
    with pytest.raises(ValueError):
        subject.run(directory)
    assert len(calls) == 1


@pytest.mark.parametrize("filename", ["raw_response.json", "raw_text.txt"])
def test_changed_raw_output_is_rejected_on_resume(fake_run, filename):
    directory, calls, _ = fake_run
    subject.run(directory)
    raw = directory / "runs/minimax/S01/attempt_01" / filename
    raw.write_bytes(b'{"changed":true}')
    with pytest.raises(ValueError):
        subject.run(directory)
    assert len(calls) == 1


def test_interrupted_attempt_original_bytes_are_never_overwritten(fake_run):
    directory, calls, _ = fake_run
    first = directory / "runs/minimax/S01/attempt_01"
    first.mkdir(parents=True)
    (first / "raw_response.json").write_bytes(b"original partial response")
    (first / "raw_text.txt").write_bytes(b"original partial text")
    subject.run(directory)
    assert len(calls) == 1
    assert (first / "raw_response.json").read_bytes() == b"original partial response"
    assert (first / "raw_text.txt").read_bytes() == b"original partial text"
    assert (first.parent / "attempt_02/MANIFEST.json").exists()


def test_valid_attempt_can_be_recovered_after_final_manifest_interruption(fake_run):
    directory, calls, _ = fake_run
    subject.run(directory)
    target = directory / "runs/minimax/S01"
    (target / "RESULT_MANIFEST.json").unlink()
    (target / "parsed.json").unlink()
    subject.run(directory)
    assert len(calls) == 1
    assert json.loads((target / "RESULT_MANIFEST.json").read_text())["status"] == "valid"
