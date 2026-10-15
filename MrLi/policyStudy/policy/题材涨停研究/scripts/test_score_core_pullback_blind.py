"""Synthetic scorer tests. Never inspect real private identities or returns."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from build_core_pullback_blind_packets import SOURCE_COLUMNS, canonical_bytes, select_samples
from run_core_pullback_blind import canonical
import score_core_pullback_blind as subject
from test_run_core_pullback_blind import packet, answer  # Synthetic fixtures only.


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@pytest.fixture
def synthetic(tmp_path, packet, answer):
    directory = tmp_path / "synthetic_blind"
    source = tmp_path / "synthetic_trades.csv"
    records = []
    for i in range(12):
        row = {key: 1 for key in SOURCE_COLUMNS}
        row.update(ts_code=f"{i:06d}.SZ", signal_time=f"2025-03-{i + 1:02d} 10:00:00",
                   anchor_date="20250228", anchor_close=100.0, name=f"Synthetic {i}", theme_name="Synthetic Theme")
        records.append(row)
    pd.DataFrame(records).to_csv(source, index=False)
    pool, chosen = select_samples(source)
    keys = {row["ts_code"]: row for row in records}
    # Explicit independent fee arithmetic: baseline net is already after costs.
    expected_net = {1: 110 * .9982 / (100 * 1.0013) - 1,
                    2: 90 * .9982 / (100 * 1.0013) - 1}
    for i, selected in enumerate(chosen, 1):
        row = keys[selected["ts_code"]]
        for mode in ("old", "carry"):
            net = expected_net.get(i, .01) if i not in {3, 4, 5, 6} else None
            flag = None if i == 6 else i not in {4, 5}
            status = {3: "open_unresolved", 4: "entry_unfillable_proxy", 5: "entry_missing", 6: None}.get(i, "closed")
            row.update({f"{mode}_buyable": flag, f"{mode}_net": net, f"{mode}_status": status,
                        f"{mode}_blocked_bars": 3 if i == 3 else 0})
    pd.DataFrame(records).to_csv(source, index=False)
    mapping = {"samples": [{"sample_id": f"S{i:02d}", **row} for i, row in enumerate(chosen, 1)]}
    write(directory / "private/sample_mapping.json", mapping)
    protocol = {"packet_hashes": {}}
    packet_manifest = {"actual_samples": 12, "seed": 2026092901, "packets": [],
                       "candidate_whitelist_sha256": digest(pool.loc[:, list(SOURCE_COLUMNS)].to_dict("records"))}
    frozen = {"full_denominator": 12, "results": {}}
    for i in range(1, 13):
        sid = f"S{i:02d}"
        p = deepcopy(packet); p["sample_id"] = sid
        input_hash = digest(p)
        write(directory / "packets" / f"{sid}.json", p)
        protocol["packet_hashes"][sid] = input_hash
        packet_manifest["packets"].append({"sample_id": sid, "input_sha256": input_hash})
        target = directory / "runs/minimax" / sid
        if i == 12:
            item = {"status": "invalid_output", "assessment": None, "input_sha256": input_hash}
        else:
            result = deepcopy(answer); result["sample_id"] = sid
            result["assessment"] = "support_research" if i <= 6 else "wait_more"
            text = canonical(result)
            raw = canonical({"content": text.decode()})
            item = {"status": "valid", "assessment": result["assessment"], "input_sha256": input_hash,
                    "parsed_sha256": digest(result), "raw_output_sha256": hashlib.sha256(raw).hexdigest(), "attempt": 1}
            write(target / "parsed.json", result)
            attempt = target / "attempt_01"; attempt.mkdir()
            (attempt / "raw_text.txt").write_bytes(text)
            (attempt / "raw_response.json").write_bytes(raw)
            request = {"messages": [{"role": "user", "content": "synthetic prompt"}]}
            write(attempt / "request.json", request)
            write(attempt / "MANIFEST.json", {"input_sha256": input_hash, "request_sha256": digest(request),
                    "raw_text_sha256": hashlib.sha256(text).hexdigest(), "usage": {"input_tokens": 100, "output_tokens": 50}})
        frozen["results"][sid] = item
        write(target / "RESULT_MANIFEST.json", item)
    write(directory / "RUN_PROTOCOL.json", protocol)
    frozen["run_protocol_sha256"] = subject.sha(directory / "RUN_PROTOCOL.json")
    write(directory / "FROZEN_OUTPUTS.json", frozen)
    write(directory / "PACKET_MANIFEST.json", packet_manifest)
    return directory, source, chosen, expected_net


def run(synthetic):
    directory, source, _, _ = synthetic
    return subject.run(directory, source=source)


def test_full_twelve_denominator_includes_invalid_unfillable_unknown_and_unresolved(synthetic):
    report = run(synthetic)
    all_rows = report["groups"]["all"]["old"]
    assert report["full_denominator"] == 12
    assert report["valid_outputs"] == 11
    assert report["assessment_counts"] == {"support_research": 6, "wait_more": 5, "invalid_output": 1}
    assert all_rows["samples"] == 12 and all_rows["baseline_fillable"] == 9
    assert all_rows["baseline_unfillable"] == 1 and all_rows["entry_unresolved"] == 1
    assert all_rows["baseline_fillability_unknown"] == 1
    assert all_rows["closed"] == 8 and all_rows["unresolved"] == 1
    assert all_rows["blocked_exit_samples"] == 1
    assert report["groups"]["invalid_output"]["old"]["samples"] == 1
    assert report["groups"]["support_research"]["old"]["samples"] == 6
    assert report["groups"]["support_research"]["old"]["baseline_fillable"] == 3
    assert "they are not simulated model trades" in " ".join(report["limitations"])


def test_precomputed_net_is_multiplied_by_100_exactly_once_without_new_fee(synthetic):
    report = run(synthetic)
    expected = synthetic[3]
    group = report["groups"]["support_research"]["carry"]
    assert group["closed"] == 2
    assert group["mean_net_pct"] == pytest.approx((expected[1] + expected[2]) / 2 * 100)
    assert group["worst_net_pct"] == pytest.approx(expected[2] * 100)
    assert group["best_net_pct"] == pytest.approx(expected[1] * 100)
    assert group["positive"] == 1
    saved = pd.read_csv(synthetic[0] / "scoring/all_samples.csv")
    assert len(saved) == 12
    assert saved.loc[saved.sample_id.eq("S01"), "old_net"].iloc[0] == pytest.approx(expected[1])


@pytest.mark.parametrize("mutation", ["short_seal", "denominator", "protocol", "packet", "input_hash", "raw", "final_manifest", "parsed", "request"])
def test_bad_seal_stops_before_reading_private_or_any_outcomes(synthetic, monkeypatch, mutation):
    directory, source, _, _ = synthetic
    if mutation in {"short_seal", "denominator", "input_hash"}:
        p = directory / "FROZEN_OUTPUTS.json"; value = json.loads(p.read_text())
        if mutation == "short_seal": value["results"].pop("S12")
        elif mutation == "denominator": value["full_denominator"] = 11
        else: value["results"]["S01"]["input_sha256"] = "changed"
        write(p, value)
    else:
        target = {"protocol": directory / "RUN_PROTOCOL.json", "packet": directory / "packets/S01.json",
                  "raw": directory / "runs/minimax/S01/attempt_01/raw_response.json",
                  "final_manifest": directory / "runs/minimax/S01/RESULT_MANIFEST.json",
                  "parsed": directory / "runs/minimax/S01/parsed.json",
                  "request": directory / "runs/minimax/S01/attempt_01/request.json"}[mutation]
        target.write_text('{}')
    original = Path.read_text
    def guarded(path, *args, **kwargs):
        assert not path.is_relative_to(directory / "private"), "private mapping read before seal verified"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("outcome CSV read before seal verified"))
    with pytest.raises((ValueError, KeyError)):
        subject.run(directory, source=source)


def test_mapping_tamper_fails_before_reading_full_outcome_columns(synthetic, monkeypatch):
    directory, source, _, _ = synthetic
    p = directory / "private/sample_mapping.json"; value = json.loads(p.read_text())
    value["samples"][0]["ts_code"] = "999999.SZ"
    write(p, value)
    original = pd.read_csv
    def guarded(*args, **kwargs):
        assert kwargs.get("usecols") == list(SOURCE_COLUMNS)
        return original(*args, **kwargs)
    monkeypatch.setattr(pd, "read_csv", guarded)
    with pytest.raises(ValueError, match="private mapping"):
        subject.run(directory, source=source)


def test_candidate_input_mutation_is_integrity_failure(synthetic):
    _, source, _, _ = synthetic
    frame = pd.read_csv(source); frame.loc[0, "anchor_close"] = 999
    frame.to_csv(source, index=False)
    with pytest.raises(ValueError, match="candidate pool changed"):
        run(synthetic)


def test_missing_outcome_fields_remain_unknown_with_twelve_rows(synthetic):
    _, source, _, _ = synthetic
    frame = pd.read_csv(source).drop(columns=["old_buyable", "old_net", "carry_status"])
    frame.to_csv(source, index=False)
    report = run(synthetic)
    group = report["groups"]["all"]["old"]
    assert group["samples"] == 12 and group["baseline_fillability_unknown"] == 12
    assert group["closed"] == 0 and group["mean_net_pct"] is None
    assert sorted(report["missing_outcome_columns"]) == ["carry_status", "old_buyable", "old_net"]


@pytest.mark.parametrize("bad", [float("inf"), "not-a-number"])
def test_nonfinite_or_malformed_returns_remain_unresolved_not_zero(synthetic, bad):
    _, source, chosen, _ = synthetic
    frame = pd.read_csv(source)
    frame["old_net"] = frame.old_net.astype(object)
    frame.loc[frame.ts_code.eq(chosen[6]["ts_code"]), "old_net"] = bad
    frame.to_csv(source, index=False)
    report = run(synthetic)
    group = report["groups"]["all"]["old"]
    assert group["samples"] == 12 and group["closed"] == 7
    assert group["unresolved"] == 2 and group["nonfinite_or_malformed_net"] == 1


def test_empty_assessment_group_has_zero_count_and_no_invented_return(synthetic):
    group = run(synthetic)["groups"]["avoid_now"]["carry"]
    assert group["samples"] == 0 and group["closed"] == 0
    assert group["mean_net_pct"] is None and group["worst_net_pct"] is None


def test_finite_mark_like_return_with_unresolved_status_is_not_closed_profit(synthetic):
    _, source, chosen, _ = synthetic
    frame = pd.read_csv(source)
    frame.loc[frame.ts_code.eq(chosen[2]["ts_code"]), "old_net"] = .99
    frame.to_csv(source, index=False)
    group = run(synthetic)["groups"]["all"]["old"]
    assert group["closed"] == 8 and group["unresolved"] == 1
    assert group["net_without_closed_status"] == 1
    assert group["best_net_pct"] < 10


def test_frozen_v1_duplicate_keys_are_flagged_without_changing_original_choice(synthetic):
    directory = synthetic[0]
    target = directory / "runs/minimax/S04"
    attempt = target / "attempt_01"
    text = (attempt / "raw_text.txt").read_text()
    needle = '"higher_low_confirmed":'
    earlier = '"higher_low_confirmed":{"value":"no","evidence_ids":[],"reason":"first ambiguous value"},'
    text = text.replace(needle, earlier + needle, 1)
    # This synthetic input recreates what v1 actually sealed: json.loads keeps
    # the original final value, so parsed output/assessment stay unchanged.
    assert json.loads(text) == json.loads((target / "parsed.json").read_text())
    raw = canonical({"content": text})
    (attempt / "raw_text.txt").write_text(text)
    (attempt / "raw_response.json").write_bytes(raw)
    meta = json.loads((attempt / "MANIFEST.json").read_text())
    meta["raw_text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    write(attempt / "MANIFEST.json", meta)
    item = json.loads((target / "RESULT_MANIFEST.json").read_text())
    item["raw_output_sha256"] = hashlib.sha256(raw).hexdigest()
    write(target / "RESULT_MANIFEST.json", item)
    frozen_path = directory / "FROZEN_OUTPUTS.json"
    frozen = json.loads(frozen_path.read_text()); frozen["results"]["S04"] = item
    write(frozen_path, frozen)
    report = run(synthetic)
    assert report["valid_outputs"] == 11
    assert report["valid_outputs_without_duplicate_key_ambiguity"] == 10
    assert report["duplicate_json_keys"] == {"S04": ["higher_low_confirmed"]}
    assert report["assessment_counts"]["support_research"] == 6
