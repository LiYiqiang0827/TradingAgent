"""Serial, resumable, no-tools LLM reading of frozen anonymous packets.

This runner never reads private mappings, price databases, or outcome columns.
It archives each exact credential-free request and original server response.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re


CHECKS = {"daily_trend_preserved", "retracement_stabilizing", "higher_low_confirmed",
          "m15_breakout_confirmed", "volume_supports_repair", "prior_theme_supports_core"}
PACKET_KEYS = {"schema_version", "sample_id", "decision", "conventions", "anchor",
               "daily_columns", "daily_bars", "m15_columns", "m15_bars", "prior_theme", "data_quality"}
NESTED_KEYS = {
    "decision": {"bar_end", "day", "timezone"},
    "anchor": {"id", "day", "close", "board_height_known_then", "leader_rank_known_then", "theme_rank_known_then", "known_at"},
    "prior_theme": {"id", "day", "theme_id", "eligible_theme_rank", "limit_up_count", "failed_limit_up_count", "max_board_height", "heat_score", "core_rank_known_then", "own_board_height_known_then"},
    "conventions": {"bar_time_is_end", "prices_normalized_to_anchor_close_100", "price_formula", "price_adjustment_uses_only_factors_known_at_cutoff", "factor_availability_assumption", "volume_is_ratio_to_prior_20_completed_daily_median", "volume_conversion", "execution_not_assessed"},
    "data_quality": {"missing_past_fields", "warnings", "observed_daily_bars", "expected_daily_bars", "observed_m15_bars", "expected_m15_bars", "volume_unit_check", "future_fields_present"},
}
RESULT_KEYS = {"schema_version", "sample_id", "as_of", "assessment", "structure_state", "checks",
               "levels", "supporting_evidence", "opposing_evidence", "next_confirmation",
               "invalidation", "missing_information", "evidence_sufficiency"}
ROOT = Path(__file__).resolve().parents[4]
PROMPT = Path(__file__).resolve().parents[1] / "prompts/core_pullback_blind_v1.txt"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    target = Path(path)
    temp = target.with_suffix(target.suffix+".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(target)


def packet_evidence(packet):
    evidence = {}
    for kind in ("daily", "m15"):
        columns = packet[f"{kind}_columns"]
        for values in packet[f"{kind}_bars"]:
            if len(values) != len(columns): raise ValueError("bar width mismatch")
            row = dict(zip(columns, values))
            if row["id"] in evidence: raise ValueError("duplicate evidence id")
            expected = f"D_{row['day']}" if kind == "daily" else f"M_{row['day']}_{row['bar_end'].replace(':', '')}"
            if row["id"] != expected: raise ValueError("bar evidence id does not match day/time")
            evidence[row["id"]] = row
    if packet["prior_theme"]["id"] in evidence or "ANCHOR" in evidence: raise ValueError("colliding metadata evidence id")
    if packet["prior_theme"]["id"] != "THEME_D-1" or packet["anchor"].get("id") != "ANCHOR":
        raise ValueError("invalid metadata evidence id")
    evidence[packet["prior_theme"]["id"]] = packet["prior_theme"]
    # Anchor is explicit evidence as well as a normalization convention.
    evidence["ANCHOR"] = packet["anchor"]
    return evidence


def validate_packet(packet):
    if set(packet) != PACKET_KEYS: raise ValueError("packet keys differ from whitelist")
    for key, allowed in NESTED_KEYS.items():
        if not isinstance(packet[key], dict) or set(packet[key])-allowed: raise ValueError("unapproved nested packet field: "+key)
    volume_check = packet["data_quality"].get("volume_unit_check", {})
    if not isinstance(volume_check, dict) or set(volume_check)-{"status", "compared_days", "max_relative_error"}:
        raise ValueError("unapproved volume metadata")
    for key in ("missing_past_fields", "warnings"):
        if any(not isinstance(x, str) for x in packet["data_quality"].get(key, [])):
            raise ValueError("quality notes must be strings")
    if packet["data_quality"].get("future_fields_present", False) is not False: raise ValueError("future data flag")
    daily_columns = ["id", "day", "open", "high", "low", "close", "volume_ratio"]
    if packet["daily_columns"] != daily_columns or packet["m15_columns"] != daily_columns[:2]+["bar_end"]+daily_columns[2:]:
        raise ValueError("bar columns differ from whitelist")
    text = canonical(packet).decode()
    if re.search(r"\b\d{6}\.(?:SH|SZ|BJ)\b|\b20\d{2}[-/]\d{2}[-/]\d{2}\b|\b20\d{6}\b", text):
        raise ValueError("absolute date or real stock code in packet")
    if not re.fullmatch(r"S\d{2}", packet["sample_id"]): raise ValueError("bad anonymous id")
    if packet["decision"]["day"] != "D0": raise ValueError("bad decision day")
    if packet["prior_theme"]["day"] != "D-1" or packet["prior_theme"]["theme_id"] != "T1": raise ValueError("theme not prior-day anonymous context")
    for values in packet["daily_bars"]:
        daily = dict(zip(packet["daily_columns"], values))
        if daily.get("day") == "D0": raise ValueError("D0 daily bar")
    for key, row in packet_evidence(packet).items():
        if "bar_end" in row:
            if row["day"] == "D0" and row["bar_end"] > packet["decision"]["bar_end"]:
                raise ValueError("future intraday bar")
        if key.startswith("D_") and row["day"] == "D0": raise ValueError("D0 daily bar")
        if "day" in row and not re.fullmatch(r"D(?:0|-\d+)", row["day"]):
            raise ValueError("non-relative day")


def validate_result(value, packet):
    if not isinstance(value, dict) or set(value) != RESULT_KEYS: raise ValueError("result keys differ from schema")
    if value["schema_version"] != "core_pullback_blind_result.v1": raise ValueError("schema version")
    if value["sample_id"] != packet["sample_id"]: raise ValueError("sample id mismatch")
    if value["as_of"] != {"day": "D0", "bar_end": packet["decision"]["bar_end"]}: raise ValueError("as-of mismatch")
    if value["assessment"] not in {"support_research", "wait_more", "avoid_now"}: raise ValueError("assessment enum")
    if value["structure_state"] not in {"intact_pullback", "repair_attempt", "failed_structure", "unclear"}: raise ValueError("structure enum")
    if value["evidence_sufficiency"] not in {"high", "medium", "low"}: raise ValueError("sufficiency enum")
    evidence = packet_evidence(packet)

    def refs(item, required=False):
        ids = item.get("evidence_ids")
        if not isinstance(ids, list) or any(not isinstance(x, str) or x not in evidence for x in ids):
            raise ValueError("unknown or malformed evidence reference")
        if required and not ids: raise ValueError("assertion requires evidence")
        return ids

    if set(value["checks"]) != CHECKS: raise ValueError("check keys")
    for check in value["checks"].values():
        if set(check) != {"value", "evidence_ids", "reason"}: raise ValueError("check fields")
        if check["value"] not in {"yes", "no", "unknown"}: raise ValueError("check enum")
        if not isinstance(check["reason"], str) or not check["reason"].strip(): raise ValueError("missing check reason")
        refs(check, required=check["value"] != "unknown")
    for key in ("levels", "supporting_evidence", "opposing_evidence", "next_confirmation", "invalidation", "missing_information"):
        if not isinstance(value[key], list): raise ValueError("expected array")
    for level in value["levels"]:
        if set(level) != {"kind", "lower", "upper", "evidence_ids", "reason"}: raise ValueError("level fields")
        if level["kind"] not in {"support", "pivot", "resistance"}: raise ValueError("level kind")
        lo, hi = level["lower"], level["upper"]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (lo, hi)):
            raise ValueError("nonfinite level")
        if not 0 < lo <= hi: raise ValueError("level order")
        ids = refs(level, required=True)
        bars = [evidence[x] for x in ids if "low" in evidence[x] and "high" in evidence[x]]
        if not bars or lo < min(b["low"] for b in bars)-.05 or hi > max(b["high"] for b in bars)+.05:
            raise ValueError("level outside cited price envelope")
    for key in ("supporting_evidence", "opposing_evidence"):
        for item in value[key]:
            if set(item) != {"statement", "evidence_ids"} or not isinstance(item["statement"], str): raise ValueError("evidence statement fields")
            refs(item, required=True)
    for key in ("next_confirmation", "invalidation"):
        for item in value[key]:
            if set(item) != {"observable_condition", "related_level_index", "evidence_ids"}: raise ValueError("condition fields")
            index = item["related_level_index"]
            if index is not None and (type(index) is not int or not 0 <= index < len(value["levels"])):
                raise ValueError("condition level index")
            refs(item)
    if any(not isinstance(x, str) for x in value["missing_information"]): raise ValueError("missing-information strings")


def parse_result(raw, packet):
    # Do not silently extract a convenient JSON object from a larger response.
    value = json.loads(raw)
    validate_result(value, packet)
    return value


def verify_raw(folder, expected_hash):
    meta = json.loads((folder / "MANIFEST.json").read_text())
    if sha((folder / "raw_response.json").read_bytes()) != expected_hash: raise ValueError("changed raw output")
    if sha((folder / "raw_text.txt").read_bytes()) != meta["raw_text_sha256"]: raise ValueError("changed raw text")


def run(directory: Path):
    import core_blind_model_transport as transport
    template = PROMPT.read_text(encoding="utf-8")
    packet_files = sorted((directory / "packets").glob("S*.json"))
    if not packet_files: raise ValueError("No frozen packets")
    packets = [json.loads(p.read_text()) for p in packet_files]
    for packet in packets: validate_packet(packet)
    manifest = {"version": "v1", "created_at": now(), "model": transport.configuration_public(),
                "prompt_sha256": sha(template.encode()), "script_sha256": sha(Path(__file__).read_bytes()),
                "transport_sha256": sha(Path(transport.__file__).read_bytes()),
                "packet_hashes": {x["sample_id"]: sha(canonical(x)) for x in packets},
                "max_tokens": 4096, "timeout": 180, "max_attempts": 2,
                "selection": "12 predeclared random unique-stock 2025 cases; seed 2026092901",
                "evaluation": "freeze all outputs before opening private mapping or outcomes",
                "news": "no Qwen requests; no news batch modification"}
    frozen = directory / "RUN_PROTOCOL.json"
    if frozen.exists():
        prior = json.loads(frozen.read_text())
        for key in ("model", "prompt_sha256", "packet_hashes", "max_tokens", "max_attempts", "script_sha256", "transport_sha256"):
            if prior[key] != manifest[key]: raise ValueError("Changed frozen run protocol: "+key)
    else: save(frozen, manifest)
    status = {"started_at": now(), "total": len(packets), "status": "running", "results": {}}
    save(directory / "batch_status.json", status)
    for packet in packets:
        sid = packet["sample_id"]
        target = directory / "runs/minimax" / sid
        target.mkdir(parents=True, exist_ok=True)
        final_path = target / "RESULT_MANIFEST.json"
        if final_path.exists():
            saved = json.loads(final_path.read_text())
            if saved["input_sha256"] != sha(canonical(packet)): raise ValueError("changed packet on resume")
            if saved["status"] == "valid":
                parsed = json.loads((target / "parsed.json").read_text())
                validate_result(parsed, packet)
                if sha(canonical(parsed)) != saved["parsed_sha256"]: raise ValueError("changed output on resume")
                verify_raw(target / f"attempt_{saved['attempt']:02d}", saved["raw_output_sha256"])
            status["results"][sid] = saved
            continue
        status.update({"current": sid, "updated_at": now(), "completed": len(status["results"])})
        save(directory / "batch_status.json", status)
        prompt = template.replace("{{PACKET_JSON}}", canonical(packet).decode())
        error = None
        final = {"input_sha256": sha(canonical(packet)), "status": "failed", "assessment": None}
        for attempt in range(1, 3):
            folder = target / f"attempt_{attempt:02d}"
            if folder.exists():
                # Never overwrite an original attempt after interruption/resumption.
                previous_path = folder / "MANIFEST.json"
                previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
                if previous.get("status") == "valid":
                    parsed = json.loads((folder / "parsed.json").read_text())
                    validate_result(parsed, packet)
                    if previous["input_sha256"] != sha(canonical(packet)): raise ValueError("changed retry input")
                    if previous["parsed_sha256"] != sha(canonical(parsed)): raise ValueError("changed retry output")
                    verify_raw(folder, previous["raw_output_sha256"])
                    save(target / "parsed.json", parsed)
                    final.update({"status": "valid", "assessment": parsed["assessment"], "attempt": attempt,
                                  "parsed_sha256": previous["parsed_sha256"],
                                  "raw_output_sha256": previous["raw_output_sha256"]})
                    break
                error = "prior attempt interrupted or invalid; return the original packet's schema only"
                continue
            folder.mkdir(exist_ok=True)
            current = prompt if not error else prompt + "\n上一尝试未通过校验，仅修复格式或引用问题；不得加入新事实。校验信息：" + error
            body = transport.request_payload(current, max_tokens=4096)
            request_bytes = canonical(body)
            (folder / "request.json").write_bytes(request_bytes)
            meta = {"attempt": attempt, "started_at": now(), "request_sha256": sha(request_bytes),
                    "prompt_sha256": sha(current.encode()), "input_sha256": sha(canonical(packet))}
            try:
                response = transport.call_model(current, timeout=180, max_tokens=4096)
                raw = response["raw_response_bytes"]
                (folder / "raw_response.json").write_bytes(raw)
                text = response["raw_text"]
                (folder / "raw_text.txt").write_text(text, encoding="utf-8")
                if response["request_payload"] != body: raise RuntimeError("actual request differs from frozen body")
                if response.get("request_body_bytes", request_bytes) != request_bytes: raise RuntimeError("actual request bytes differ")
                meta.update({"raw_output_sha256": sha(raw), "raw_text_sha256": sha(text.encode()),
                             "response_model": response.get("response_model"), "usage": response.get("usage"),
                             "elapsed_seconds": response.get("elapsed"), "stop_reason": response.get("stop_reason")})
                parsed = parse_result(text, packet)
                save(folder / "parsed.json", parsed)
                save(target / "parsed.json", parsed)
                meta.update({"status": "valid", "parsed_sha256": sha(canonical(parsed))})
                final.update({"status": "valid", "assessment": parsed["assessment"],
                              "attempt": attempt, "parsed_sha256": sha(canonical(parsed)),
                              "raw_output_sha256": sha(raw)})
                error = None
            except (ValueError, KeyError, TypeError) as exc:
                error = str(exc)[:180]
                meta.update({"status": "invalid_output", "error_type": type(exc).__name__, "validation_error": error})
                final["status"] = "invalid_output"
            except Exception as exc:
                # Do not print transport exceptions that might contain credentials or headers.
                error = "transport failed; repeat the same original packet"
                meta.update({"status": "transport_error", "error_type": type(exc).__name__})
                final["status"] = "transport_error"
            meta["finished_at"] = now()
            save(folder / "MANIFEST.json", meta)
            if error is None: break
        save(final_path, final)
        status["results"][sid] = final
        status.update({"updated_at": now(), "current": sid, "completed": len(status["results"]),
                       "counts": dict(Counter(x["status"] for x in status["results"].values()))})
        save(directory / "batch_status.json", status)
        print(json.dumps({k: status[k] for k in ("current", "completed", "counts")}), flush=True)
    status.update({"status": "finished", "updated_at": now(), "completed": len(status["results"]),
                   "counts": dict(Counter(x["status"] for x in status["results"].values()))})
    save(directory / "batch_status.json", status)
    save(directory / "FROZEN_OUTPUTS.json", {"frozen_at": now(), "run_protocol_sha256": sha(frozen.read_bytes()),
         "results": status["results"], "full_denominator": len(packets)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=ROOT / "outputs/core_pullback_blind_2025")
    args = parser.parse_args(); args.directory.mkdir(parents=True, exist_ok=True)
    with (args.directory / ".run.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.directory)


if __name__ == "__main__": main()
