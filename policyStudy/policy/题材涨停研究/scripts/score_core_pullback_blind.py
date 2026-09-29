"""Reveal outcome association only after every blind output has been frozen.

These groups do not simulate model-response latency or new execution rules.
Small pilot association is not an independent profitable-strategy validation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from run_core_pullback_blind import canonical, validate_packet, validate_result, verify_raw
from build_core_pullback_blind_packets import SOURCE_COLUMNS, canonical_bytes, select_samples


ROOT = Path(__file__).resolve().parents[4]
EXPECTED_IDS = {f"S{i:02d}" for i in range(1, 13)}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def duplicate_json_keys(text):
    """Audit ambiguity without changing frozen v1 json.loads last-value semantics."""
    duplicates = []
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result
    json.loads(text, object_pairs_hook=object_pairs)
    return sorted(set(duplicates))


def summarize(frame, mode):
    # CSV object columns containing missing flags must not turn unknown into False.
    flags = frame[f"{mode}_buyable"].map(
        lambda value: True if str(value).lower() in {"true", "1", "1.0"} else (
            False if str(value).lower() in {"false", "0", "0.0"} else None))
    buy = frame[flags.eq(True)]
    values = pd.to_numeric(buy[f"{mode}_net"], errors="coerce")
    finite = values.map(lambda x: pd.notna(x) and math.isfinite(x))
    closed_status = buy[f"{mode}_status"].eq("closed")
    ret = values[finite & closed_status]
    status = frame[f"{mode}_status"].fillna("scoring_label_missing")
    unfillable = flags.eq(False) & status.isin(["entry_unfillable_proxy", "excluded_limit_regime"])
    entry_unknown = flags.eq(False) & ~unfillable
    blocked = pd.to_numeric(buy[f"{mode}_blocked_bars"], errors="coerce")
    return {"samples": len(frame), "baseline_fillable": len(buy),
            "baseline_unfillable": int(unfillable.sum()),
            "entry_unresolved": int(entry_unknown.sum()),
            "baseline_fillability_unknown": int(flags.isna().sum()),
            "closed": len(ret), "unresolved": len(buy)-len(ret),
            "mean_net_pct": float(ret.mean()*100) if len(ret) else None,
            "median_net_pct": float(ret.median()*100) if len(ret) else None,
            "worst_net_pct": float(ret.min()*100) if len(ret) else None,
            "best_net_pct": float(ret.max()*100) if len(ret) else None,
            "positive": int(ret.gt(0).sum()),
            "blocked_exit_samples": int(blocked.gt(0).sum()),
            "blocked_exit_unknown": int(blocked.isna().sum()),
            "net_without_closed_status": int((finite & ~closed_status).sum()),
            "closed_without_finite_net": int((closed_status & ~finite).sum()),
            "status_counts": status.value_counts().to_dict(),
            "nonfinite_or_malformed_net": int((buy[f"{mode}_net"].notna() &
                ~finite).sum())}


def run(directory: Path, source: Path | None = None):
    sealed = directory / "FROZEN_OUTPUTS.json"
    frozen = json.loads(sealed.read_text())
    protocol = json.loads((directory / "RUN_PROTOCOL.json").read_text())
    if frozen["run_protocol_sha256"] != sha(directory / "RUN_PROTOCOL.json"):
        raise ValueError("run protocol modified after freeze")
    if (set(frozen["results"]) != EXPECTED_IDS or set(protocol["packet_hashes"]) != EXPECTED_IDS
            or frozen["full_denominator"] != 12):
        raise ValueError("all twelve declared samples must be frozen before reveal")
    packet_manifest = json.loads((directory / "PACKET_MANIFEST.json").read_text())
    manifest_items = {x["sample_id"]: x for x in packet_manifest["packets"]}
    if (set(manifest_items) != EXPECTED_IDS or len(packet_manifest["packets"]) != 12
            or packet_manifest["actual_samples"] != 12 or packet_manifest["seed"] != 2026092901):
        raise ValueError("packet sampling manifest differs from frozen twelve-sample plan")
    labels = []
    for sid, item in sorted(frozen["results"].items()):
        packet = json.loads((directory / "packets" / f"{sid}.json").read_text())
        validate_packet(packet)
        packet_hash = hashlib.sha256(canonical(packet)).hexdigest()
        if (packet_hash != protocol["packet_hashes"][sid] or packet_hash != item["input_sha256"]
                or packet_hash != manifest_items[sid]["input_sha256"]): raise ValueError("changed packet")
        target = directory / "runs/minimax" / sid
        if json.loads((target / "RESULT_MANIFEST.json").read_text()) != item:
            raise ValueError("result manifest modified after freeze")
        if item["status"] not in {"valid", "invalid_output", "transport_error", "failed"}:
            raise ValueError("sample does not have a frozen terminal model status")
        label = {"sample_id": sid, "llm_status": item["status"], "assessment": "invalid_output",
                 "evidence_sufficiency": None, "duplicate_json_keys": []}
        if item["status"] == "valid":
            result = json.loads((target / "parsed.json").read_text())
            validate_result(result, packet)
            if hashlib.sha256(canonical(result)).hexdigest() != item["parsed_sha256"]: raise ValueError("changed model result")
            if item["assessment"] != result["assessment"]: raise ValueError("frozen assessment differs from parsed result")
            attempt = target / f"attempt_{item['attempt']:02d}"
            verify_raw(attempt, item["raw_output_sha256"])
            raw_text = (attempt / "raw_text.txt").read_text()
            if canonical(json.loads(raw_text)) != canonical(result):
                raise ValueError("parsed result differs from frozen raw text")
            label["duplicate_json_keys"] = duplicate_json_keys(raw_text)
            attempt_meta = json.loads((attempt / "MANIFEST.json").read_text())
            if (attempt_meta["input_sha256"] != packet_hash
                    or sha(attempt / "request.json") != attempt_meta["request_sha256"]):
                raise ValueError("changed request for frozen result")
            label.update({"assessment": result["assessment"], "evidence_sufficiency": result["evidence_sufficiency"]})
        labels.append(label)
    # Private mapping and future labels are first accessed only after seal verification.
    mapping = json.loads((directory / "private/sample_mapping.json").read_text())
    source = source or ROOT / "outputs/core_reactivation_causal_2025/trades.csv"
    pool, expected_samples = select_samples(source, seed=packet_manifest["seed"], count=12)
    pool_hash = hashlib.sha256(canonical_bytes(pool.loc[:, list(SOURCE_COLUMNS)].to_dict("records"))).hexdigest()
    if pool_hash != packet_manifest["candidate_whitelist_sha256"]:
        raise ValueError("signal-only candidate pool changed before scoring")
    expected_mapping = [{"sample_id": f"S{i:02d}", **row} for i, row in enumerate(expected_samples, 1)]
    # Binding selected identities to the predeclared shuffle prevents relabeling
    # model choices to different stocks after their outcomes are known.
    if canonical_bytes(mapping["samples"]) != canonical_bytes(expected_mapping):
        raise ValueError("private mapping does not match predeclared seeded selection")
    keys = pd.DataFrame(mapping["samples"])[["sample_id", "ts_code", "signal_time"]]
    original = pd.read_csv(source)
    cols = ["ts_code", "signal_time"] + [c for c in ("name", "theme_name") if c in original]
    cols += [c for c in original if c.startswith(("old_", "carry_"))]
    joined = pd.DataFrame(labels).merge(keys, on="sample_id", validate="one_to_one").merge(
        original[cols], on=["ts_code", "signal_time"], how="left", validate="one_to_one")
    if len(joined) != frozen["full_denominator"]: raise ValueError("lost scoring denominator")
    missing_columns = []
    for mode in ("old", "carry"):
        for suffix in ("status", "buyable", "net", "blocked_bars"):
            column = f"{mode}_{suffix}"
            if column not in joined:
                joined[column] = None
                missing_columns.append(column)
    groups = {"all": joined, **{g: joined[joined.assessment.eq(g)]
              for g in ("support_research", "wait_more", "avoid_now", "invalid_output")}}
    usage = Counter()
    attempts = 0
    for path in (directory / "runs/minimax").glob("S*/attempt_*/MANIFEST.json"):
        meta = json.loads(path.read_text()); attempts += 1
        for key, value in (meta.get("usage") or {}).items():
            if isinstance(value, (int, float)): usage[key] += value
    report = {"full_denominator": len(joined), "valid_outputs": int(joined.llm_status.eq("valid").sum()),
              "valid_outputs_without_duplicate_key_ambiguity": int((joined.llm_status.eq("valid") &
                  joined.duplicate_json_keys.map(lambda x: not x)).sum()),
              "duplicate_json_keys": {r.sample_id: r.duplicate_json_keys for r in joined.itertuples()
                                      if r.duplicate_json_keys},
              "duplicate_key_audit_scope": "raw text of each v1-valid selected attempt; frozen last-value parsed choices unchanged",
              "assessment_counts": joined.assessment.value_counts().to_dict(), "attempts": attempts,
              "reported_usage_numeric_fields": dict(usage),
              "missing_outcome_columns": missing_columns,
              "net_return_convention": "precomputed baseline net fractions multiplied by 100 once; no extra fee subtraction",
              "groups": {g: {m: summarize(frame, m) for m in ("old", "carry")} for g, frame in groups.items()},
              "frozen_outputs_sha256": sha(sealed), "outcome_source_sha256": sha(source),
              "limitations": ["12 training-period random samples, not an untouched performance holdout",
                              "baseline bar-open execution has no additional LLM response latency",
                              "grouped outcomes describe association; they are not simulated model trades",
                              "no threshold tuning, calibrated probability or portfolio allocation",
                              "all invalid, unfillable and unknown samples retained in denominator",
                              "unresolved trades excluded from closed-trade mean, never counted as zero profit",
                              "v1 JSON parsing keeps the last duplicate key; affected valid outputs are ambiguous, not reselected or rerun",
                              "failed-output status is sealed but runner v1 does not seal every failed raw-attempt hash"]}
    (directory / "scoring").mkdir(exist_ok=True)
    joined.to_csv(directory / "scoring/all_samples.csv", index=False)
    (directory / "scoring/SCORE.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=Path, default=ROOT / "outputs/core_pullback_blind_2025")
    run(p.parse_args().directory)
