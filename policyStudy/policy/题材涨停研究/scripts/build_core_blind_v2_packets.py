"""Build development-only v2 inputs from public frozen v1 packets.

No private mapping, model answer, scoring output, market database or network is
read. Facts use the full v1 past prefix, then displayed minutes are shortened.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

import derive_core_blind_facts as facts_module

ROOT = Path(__file__).resolve().parents[4]
SCHEMA = "core_pullback_blind_compact.v2"
SAMPLE_IDS = tuple(f"S{i:02}" for i in range(1, 13))
DAILY_FIELDS = {"id", "day", "open", "high", "low", "close", "volume_ratio"}
MINUTE_FIELDS = DAILY_FIELDS | {"bar_end"}
TOP_FIELDS = {"schema_version", "sample_id", "decision", "conventions", "anchor", "prior_theme",
              "daily_bars", "m15_bars", "facts", "data_quality", "display_scope"}
NESTED_FIELDS = {
    "decision": {"day", "bar_end", "timezone"},
    "anchor": {"id", "day", "close", "board_height_known_then", "leader_rank_known_then", "theme_rank_known_then", "known_at"},
    "prior_theme": {"id", "day", "theme_id", "eligible_theme_rank", "limit_up_count", "failed_limit_up_count", "max_board_height", "heat_score", "core_rank_known_then", "own_board_height_known_then"},
    "conventions": {"bar_time_is_end", "prices_normalized_to_anchor_close_100", "price_formula", "price_adjustment_uses_only_factors_known_at_cutoff", "factor_availability_assumption", "volume_is_ratio_to_prior_20_completed_daily_median", "volume_conversion", "execution_not_assessed"},
    "data_quality": {"missing_past_fields", "warnings", "observed_daily_bars", "expected_daily_bars", "observed_m15_bars", "expected_m15_bars", "volume_unit_check", "future_fields_present"},
}
SCOPE_FIELDS = {"daily_source_bars", "minute_display_days", "minute_display_bars", "minute_source_bars",
                "minute_source_window", "minute_history_shortened", "facts_derived_before_display_reduction",
                "hidden_source_ids", "allowed_model_evidence_policy", "sample_role"}
FACT_FIELDS = {"id", "value", "source_ids", "definition", "status", "missing_fields", "observation_window", "clock_alignment"}
VALUE_FIELDS = {
    "previous_completed_day": {"open", "high", "low", "close", "volume_ratio"},
    "current_first_m15": {"open", "high", "low", "close", "volume_ratio"},
    "current_last_m15": {"open", "high", "low", "close", "volume_ratio"},
    "current_session_observation_coverage": {"expected_bar_ends", "observed_bar_ends", "missing_bar_ends", "complete", "last_observed_bar_end", "decision_bar_present", "first_scheduled_bar_present"},
    "session_so_far": {"observed_bars", "through", "last_observed_bar_end", "scheduled_slots_complete", "low", "high"},
    "first_m15_low_vs_previous_day_low": {"observed_low", "previous_day_low", "relation"},
    "session_low_vs_previous_day_low": {"observed_low", "previous_day_low", "relation"},
    **{f"close_vs_prior_{n}_observed_m15_high": {"prior_high", "signal_close", "close_above_prior_high"} for n in (4, 8, 16)},
    "last_m15_volume_vs_five_prior_same_clock_median": None,
    "matched_clock_cumulative_volume_ratio": None,
    "prior_theme_core_identity": {"prior_theme_rank", "prior_theme_heat", "prior_theme_limit_up_count", "prior_core_rank_explicit", "prior_board_height_explicit", "anchor_day", "anchor_core_rank", "anchor_board_height", "anchor_is_prior_day"},
}
OPTIONAL_FACT_FIELDS = {
    "observation_window": {"required_observed_bars", "available_observed_bars", "first_source_id", "last_source_id", "scheduled_slots_between_endpoints", "missing_scheduled_slots_between_endpoints"},
    "clock_alignment": {"compared_bar_ends", "scheduled_slots_complete", "historical_days_with_all_compared_slots_and_volume"},
}
MODEL_EVIDENCE_POLICY = "fact.id, visible bar.id, ANCHOR, THEME_D-1 only; hidden source_ids are provenance"
CONVENTION_VALUES = {
    "bar_time_is_end": True, "prices_normalized_to_anchor_close_100": True,
    "price_formula": "100 * raw_price * factor_at_bar_date / (anchor_raw_close * factor_at_anchor_date)",
    "price_adjustment_uses_only_factors_known_at_cutoff": True,
    "factor_availability_assumption": "effective_date_is_known_by_session_open; historical_publication_time_not_archived",
    "volume_is_ratio_to_prior_20_completed_daily_median": True,
    "volume_conversion": "daily_lots_x100_and_tdx15_shares_use_same_denominator", "execution_not_assessed": True,
}
DEFINITIONS = {
    **{key: "Literal bar fields; daily and m15 are different durations. First m15 means scheduled 09:45, never a later observed substitute."
       for key in ("previous_completed_day", "current_first_m15", "current_last_m15")},
    "current_session_observation_coverage": "Coverage of scheduled bar slots through the decision, not proof all field values are available.",
    "session_so_far": "Observed completed D0 m15 bars only; never full-day OHLC.",
    **{key: "Low versus low only. This is not confirmation of a swing higher-low pattern."
       for key in ("first_m15_low_vs_previous_day_low", "session_low_vs_previous_day_low")},
    **{f"close_vs_prior_{n}_observed_m15_high": "Excludes current bar from prior high. Missing scheduled bars are not imputed; window is observed bars. One close above is not proof of a sustained breakout." for n in (4, 8, 16)},
    "last_m15_volume_vs_five_prior_same_clock_median": "Current m15 volume / median of same-clock m15 in D-5..D-1.",
    "matched_clock_cumulative_volume_ratio": "Sum of observed D0 clock slots / median sum of exactly the same slots in each of D-5..D-1. This does not fill missing clock slots or estimate full-day volume.",
    "prior_theme_core_identity": "Null prior fields do not erase anchor facts; an older anchor is not current core proof.",
}


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate input JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


def allowed_model_evidence_ids(packet) -> set[str]:
    """Hidden historical source IDs are provenance, never direct model evidence."""
    return ({row["id"] for row in packet["daily_bars"] + packet["m15_bars"] + packet["facts"]}
            | {"ANCHOR", "THEME_D-1"})


def validate_compact_packet(packet) -> None:
    if not isinstance(packet, dict) or set(packet) != TOP_FIELDS:
        raise ValueError("compact packet top fields differ from whitelist")
    if packet["schema_version"] != SCHEMA or packet["sample_id"] not in SAMPLE_IDS:
        raise ValueError("invalid compact version or sample ID")
    for key, allowed in NESTED_FIELDS.items():
        if not isinstance(packet[key], dict) or set(packet[key]) - allowed:
            raise ValueError("unapproved nested field: " + key)
    text = canonical(packet).decode()
    if re.search(r"\b\d{6}\.(?:SH|SZ|BJ)\b|\b20\d{2}[-/]\d{2}[-/]\d{2}\b|\b20\d{6}\b", text):
        raise ValueError("absolute date or stock identity in compact packet")
    decision = packet["decision"]
    if (set(decision) != NESTED_FIELDS["decision"] or decision["day"] != "D0"
            or decision["bar_end"] not in facts_module.M15_CLOCKS
            or decision["timezone"] != "Asia/Shanghai"):
        raise ValueError("invalid compact decision boundary")
    if packet["anchor"].get("id") != "ANCHOR" or packet["prior_theme"].get("id") != "THEME_D-1":
        raise ValueError("metadata evidence IDs changed")
    if packet["prior_theme"].get("day") != "D-1" or packet["prior_theme"].get("theme_id") != "T1":
        raise ValueError("theme is not anonymous prior-day information")
    if not re.fullmatch(r"D-[1-9]\d*", packet["anchor"].get("day", "")):
        raise ValueError("anchor must be a past relative day")
    def finite_or_null(value):
        return value is None or (type(value) in {int, float} and math.isfinite(value))
    for container in ("anchor", "prior_theme"):
        for key, value in packet[container].items():
            if key not in {"id", "day", "theme_id", "known_at"} and not finite_or_null(value):
                raise ValueError("metadata numeric field contains nonnumeric information")
    if packet["anchor"].get("known_at", "anchor_day_close") != "anchor_day_close":
        raise ValueError("anchor availability changed")
    for key, value in packet["conventions"].items():
        if type(value) not in {str, bool} or value != CONVENTION_VALUES[key]:
            raise ValueError("invalid convention value")
    ids = set()
    for kind, allowed in [("daily_bars", DAILY_FIELDS), ("m15_bars", MINUTE_FIELDS)]:
        if not isinstance(packet[kind], list):
            raise ValueError("bars must be objects in an array")
        for row in packet[kind]:
            if not isinstance(row, dict) or set(row) != allowed:
                raise ValueError("bar object differs from whitelist")
            if kind == "daily_bars":
                if not re.fullmatch(r"D-(?:[1-9]|[1-5]\d|60)", row["day"]):
                    raise ValueError("daily bar outside past 60-day window")
                expected = f"D_{row['day']}"
            else:
                if row["day"] not in {"D-1", "D0"} or row["bar_end"] not in facts_module.M15_CLOCKS:
                    raise ValueError("minute bar outside displayed window")
                if row["day"] == "D0" and row["bar_end"] > decision["bar_end"]:
                    raise ValueError("future minute bar")
                expected = f"M_{row['day']}_{row['bar_end'].replace(':', '')}"
            if row["id"] != expected or row["id"] in ids:
                raise ValueError("invalid or duplicate visible evidence ID")
            ids.add(row["id"])
            for key in facts_module.BAR_FIELDS:
                value = row[key]
                if value is not None and (type(value) not in {int, float} or not math.isfinite(value)
                        or value < 0 or (key != "volume_ratio" and value == 0)):
                    raise ValueError("invalid numeric bar field")
    if len(packet["daily_bars"]) > 60 or len(packet["m15_bars"]) > 32:
        raise ValueError("excessive displayed bars")
    quality = packet["data_quality"]
    if quality.get("future_fields_present") is not False:
        raise ValueError("future-data flag")
    for key in ("missing_past_fields", "warnings"):
        if not isinstance(quality.get(key, []), list) or any(not isinstance(x, str) for x in quality.get(key, [])):
            raise ValueError("invalid data-quality notes")
    note_pattern = (r"calendar_history_less_than_60_days|daily:D-[1-9]\d*|anchor_price_or_factor|"
                    r"factor:D(?:0|-[1-9]\d*)|volume_unit_crosscheck|prior_20_completed_daily_volume|"
                    r"m15:D(?:0|-[1-9]\d*):\d+_of_\d+")
    if any(not re.fullmatch(note_pattern, note) for note in quality.get("missing_past_fields", [])):
        raise ValueError("unapproved free-text quality commentary")
    if any(note != "daily_lots_vs_tdx15_shares_mismatch" for note in quality.get("warnings", [])):
        raise ValueError("unapproved warning commentary")
    for key in ("observed_daily_bars", "expected_daily_bars", "observed_m15_bars", "expected_m15_bars"):
        if key in quality and (type(quality[key]) is not int or quality[key] < 0):
            raise ValueError("invalid quality count")
    unit = quality.get("volume_unit_check", {})
    if not isinstance(unit, dict) or set(unit) - {"status", "compared_days", "max_relative_error"}:
        raise ValueError("invalid volume coverage metadata")
    if unit and (unit.get("status") not in {"consistent", "mismatch", "unknown"}
            or type(unit.get("compared_days")) is not int or not finite_or_null(unit.get("max_relative_error"))):
        raise ValueError("invalid volume coverage values")
    scope = packet["display_scope"]
    if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
        raise ValueError("invalid display scope fields")
    if (scope["minute_display_days"] != ["D-1", "D0"] or scope["minute_history_shortened"] is not True
            or scope["facts_derived_before_display_reduction"] is not True
            or scope["sample_role"] != "same_12_revealed_v1_samples_development_only"
            or scope["minute_source_window"] != "D-5..D0 through decision"
            or scope["allowed_model_evidence_policy"] != MODEL_EVIDENCE_POLICY):
        raise ValueError("display/development scope changed")
    if scope["daily_source_bars"] != len(packet["daily_bars"]) or scope["minute_display_bars"] != len(packet["m15_bars"]):
        raise ValueError("display bar count mismatch")
    if type(scope["minute_source_bars"]) is not int or not len(packet["m15_bars"]) <= scope["minute_source_bars"] <= 96:
        raise ValueError("invalid original minute-window count")
    hidden = scope["hidden_source_ids"]
    if (not isinstance(hidden, list) or len(set(hidden)) != len(hidden)
            or any(not re.fullmatch(r"M_D-[2-5]_(?:0945|1000|1015|1030|1045|1100|1115|1130|1315|1330|1345|1400|1415|1430|1445|1500)", x) for x in hidden)):
        raise ValueError("invalid hidden provenance IDs")
    available_source_ids = ids | set(hidden) | {"ANCHOR", "THEME_D-1"}
    if not isinstance(packet["facts"], list) or {x.get("id") for x in packet["facts"]} != set(VALUE_FIELDS) or len(packet["facts"]) != len(VALUE_FIELDS):
        raise ValueError("derived fact IDs differ from whitelist")
    for fact in packet["facts"]:
        if not isinstance(fact, dict) or set(fact) - FACT_FIELDS or not {"id", "value", "source_ids", "definition", "status"} <= set(fact):
            raise ValueError("invalid derived fact fields")
        if fact["status"] not in {"known", "partial", "unknown"} or fact["definition"] != DEFINITIONS[fact["id"]]:
            raise ValueError("invalid derived fact status/definition")
        if not isinstance(fact["source_ids"], list) or any(x not in available_source_ids for x in fact["source_ids"]):
            raise ValueError("derived fact source outside frozen prefix")
        value, allowed = fact["value"], VALUE_FIELDS[fact["id"]]
        if value is not None:
            if allowed is None:
                if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                    raise ValueError("invalid scalar derived value")
            elif not isinstance(value, dict) or set(value) - allowed:
                raise ValueError("derived value contains unapproved fields")
            else:
                for key, child in value.items():
                    if key in {"expected_bar_ends", "observed_bar_ends", "missing_bar_ends"}:
                        if not isinstance(child, list) or any(t not in facts_module.M15_CLOCKS or t > decision["bar_end"] for t in child):
                            raise ValueError("future or invalid derived coverage clock")
                    elif key in {"through", "last_observed_bar_end"}:
                        if child is not None and (child not in facts_module.M15_CLOCKS or child > decision["bar_end"]):
                            raise ValueError("future or invalid derived observation time")
                    elif key == "relation":
                        if child not in {"above", "below", "equal"}: raise ValueError("invalid low relation")
                    elif key == "anchor_day":
                        if not isinstance(child, str) or not re.fullmatch(r"D-[1-9]\d*", child): raise ValueError("invalid fact anchor day")
                    elif key in {"complete", "decision_bar_present", "first_scheduled_bar_present", "scheduled_slots_complete", "close_above_prior_high", "anchor_is_prior_day"}:
                        if type(child) is not bool: raise ValueError("invalid fact boolean")
                    elif not finite_or_null(child):
                        raise ValueError("fact numeric leaf contains unapproved information")
        for key, allowed in OPTIONAL_FACT_FIELDS.items():
            if key in fact and (not isinstance(fact[key], dict) or set(fact[key]) != allowed):
                raise ValueError("invalid derived coverage metadata")
        if "observation_window" in fact:
            for key, value in fact["observation_window"].items():
                if key in {"first_source_id", "last_source_id"}:
                    if value is not None and value not in available_source_ids: raise ValueError("invalid observation-window source")
                elif type(value) is not int or value < 0:
                    raise ValueError("invalid observation-window count")
        if "clock_alignment" in fact:
            alignment = fact["clock_alignment"]
            if (not isinstance(alignment["compared_bar_ends"], list)
                    or any(x not in facts_module.M15_CLOCKS or x > decision["bar_end"] for x in alignment["compared_bar_ends"])
                    or type(alignment["scheduled_slots_complete"]) is not bool
                    or type(alignment["historical_days_with_all_compared_slots_and_volume"]) is not int):
                raise ValueError("invalid matched-clock metadata")
        if "missing_fields" in fact and (not isinstance(fact["missing_fields"], list)
                or any(x not in facts_module.BAR_FIELDS for x in fact["missing_fields"])):
            raise ValueError("invalid missing-field metadata")
    if set(hidden) & allowed_model_evidence_ids(packet):
        raise ValueError("hidden provenance incorrectly exposed as model evidence")


def build_compact_packet(source):
    derived = facts_module.derive(source)
    daily = [dict(zip(source["daily_columns"], row)) for row in source["daily_bars"]]
    original_minutes = [dict(zip(source["m15_columns"], row)) for row in source["m15_bars"]]
    minutes = [row for row in original_minutes if row["day"] in {"D-1", "D0"}]
    visible_ids = {row["id"] for row in daily + minutes} | {"ANCHOR", "THEME_D-1"}
    facts = [{"id": key, **deepcopy(value)} for key, value in derived["facts"].items()]
    hidden = sorted({eid for fact in facts for eid in fact["source_ids"]} - visible_ids)
    packet = {"schema_version": SCHEMA, "sample_id": source["sample_id"],
              **{key: deepcopy(source[key]) for key in ("decision", "conventions", "anchor", "prior_theme", "data_quality")},
              "daily_bars": daily, "m15_bars": minutes, "facts": facts,
              "display_scope": {
                  "daily_source_bars": len(daily), "minute_display_days": ["D-1", "D0"],
                  "minute_display_bars": len(minutes), "minute_source_bars": len(original_minutes),
                  "minute_source_window": "D-5..D0 through decision",
                  "minute_history_shortened": True, "facts_derived_before_display_reduction": True,
                  "hidden_source_ids": hidden,
                  "allowed_model_evidence_policy": MODEL_EVIDENCE_POLICY,
                  "sample_role": "same_12_revealed_v1_samples_development_only",
              }}
    validate_compact_packet(packet)
    return packet


def _write_frozen(path, value):
    content = canonical(value)
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError("refusing to overwrite different frozen artifact: " + str(path))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def run(source_directory, output_directory):
    if source_directory.resolve() == (output_directory / "packets").resolve():
        raise ValueError("v2 output must not overwrite v1 source directory")
    entries = []
    for sid in SAMPLE_IDS:
        path = source_directory / f"{sid}.json"
        raw = path.read_bytes()
        source = strict_json(raw.decode())
        if source.get("sample_id") != sid:
            raise ValueError("source filename and anonymous sample ID differ")
        packet = build_compact_packet(source)
        packet_hash = _write_frozen(output_directory / "packets" / f"{sid}.json", packet)
        entries.append({"sample_id": sid, "packet_file": f"packets/{sid}.json", "input_sha256": packet_hash,
                        "source_packet_sha256": digest(source), "source_file_sha256": hashlib.sha256(raw).hexdigest(),
                        "daily_bars": len(packet["daily_bars"]), "displayed_m15_bars": len(packet["m15_bars"]),
                        "source_m15_bars": len(source["m15_bars"]), "facts": len(packet["facts"]),
                        "input_bytes": len(canonical(packet))})
    manifest = {"schema_version": SCHEMA, "samples": len(entries), "packets": entries,
                "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "derive_code_sha256": hashlib.sha256(Path(facts_module.__file__).read_bytes()).hexdigest(),
                "cohort": "same 12 public anonymous v1 samples; outcomes previously revealed; development retest only",
                "independent_holdout": False, "model_calls": 0, "read_private_or_scoring": False,
                "minute_display": "D-1 and D0 completed bars only; deterministic facts retain original D-5..D0 prefix"}
    _write_frozen(output_directory / "PACKET_MANIFEST.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "outputs/core_pullback_blind_2025/packets")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/core_pullback_blind_v2_2025")
    args = parser.parse_args()
    result = run(args.source, args.output)
    print(json.dumps({"samples": result["samples"], "independent_holdout": False, "model_calls": 0}))
