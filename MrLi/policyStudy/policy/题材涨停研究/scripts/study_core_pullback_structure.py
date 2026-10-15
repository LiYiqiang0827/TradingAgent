"""Frozen, prefix-only structure filters on the existing causal signal universe.

Feature extraction cannot read outcome columns. Scoring is a separate command.
No parameter search, live orders, or inferred historical news catalysts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_fifteenMin, get_tradecal
from study_core_reactivation_causal import schedule


INPUT_COLUMNS = ["ts_code", "name", "theme_id", "theme_name", "anchor_date",
                 "signal_time", "signal_close", "signal_factor"]
GROUPS = {"A": ["A"], "B": ["B"], "C": ["C"],
          "A_B": ["A", "B"], "A_B_C": ["A", "B", "C"]}
PROTOCOL = {
    "version": "1", "universe": "all frozen causal signals, no fill or outcome filtering",
    "A": "signal day index - anchor day index >=3; median volume of last 16 planned bars before signal / preceding 16 <=0.8",
    "B": "last two confirmed lows strictly after anchor close; low < both left2 and <= both right2; confirmation strictly before signal; separation>=4 bars; last>=prior*1.005; no later low through signal below last",
    "C": "pressure=max high of previous 3 complete trading days; reference_stop=latest confirmed low*0.995; (pressure-signal_close)/(signal_close-reference_stop)>=2 with positive numerator and denominator",
    "groups": GROUPS, "data_unknown": "retained separately from known non-selection; never removed from original denominator",
    "primary_exit": "old", "secondary_exit": "carry, no promotion based on this exit",
    "training_gate": "2025 old n>=15 and 99% two-sided date-block bootstrap mean lower bound>0; at most one candidate, highest lower bound then group name; no threshold retuning",
    "next_step": "if none passes gate, promote none; otherwise freeze candidate before 2026-early and late exploratory checks",
    "limitations": "five predeclared subsets; bootstrap sampling uncertainty only; entire upstream pool previously explored; a passed training gate is not trading validation",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def register(out: Path):
    target = out / "PROTOCOL.json"
    if target.exists():
        saved = json.loads(target.read_text())
        if saved["protocol"] != PROTOCOL:
            raise ValueError("Frozen protocol differs; use a new version/output directory")
        return
    write_json(target, {"registered_at": datetime.now(timezone.utc).isoformat(),
                        "protocol": PROTOCOL, "script_sha256": digest(__file__)})


def confirmed_lows(frame: pd.DataFrame, signal_at: pd.Timestamp) -> list[int]:
    """A pivot is available only after both right-hand bars closed before signal."""
    lows = frame.low.to_numpy(float)
    return [i for i in range(2, len(frame)-2)
            if frame.iloc[i+2].datetime < signal_at
            and lows[i] < min(lows[i-2:i]) and lows[i] <= min(lows[i+1:i+3])]


def features(stock: pd.DataFrame, signal: dict, calendar: list[str]) -> dict:
    result = {k: signal[k] for k in INPUT_COLUMNS}
    result.update({"data_status": "known", "A_status": "known", "B_status": "known", "C_status": "known",
                   "A": False, "B": False, "C": False, "volume_ratio": None,
                   "confirmed_low_count": 0, "last_low": None, "last_low_at": None,
                   "last_low_confirmed_at": None, "prior_low": None,
                   "pressure": None, "reference_stop": None, "space_risk_ratio": None})
    stamp = pd.Timestamp(signal["signal_time"])
    anchor = pd.Timestamp(str(signal["anchor_date"]) + " 15:00")
    day = stamp.strftime("%Y%m%d")
    day_pos = calendar.index(day)
    age = day_pos-calendar.index(str(signal["anchor_date"]))
    result["anchor_age_sessions"] = age
    slots = schedule(calendar)
    if stamp not in slots or day_pos < 3 or slots.index(stamp) < 32:
        return {**result, "data_status": "calendar_history_missing",
                **{f"{c}_status": "unknown" for c in "ABC"}}
    index = slots.index(stamp)
    volume_slots = slots[index-32:index]
    pressure_slots = schedule(calendar[day_pos-3:day_pos])
    post_slots = [x for x in slots if anchor < x <= stamp]
    # The first post-anchor low can use the two already-known anchor tail bars.
    anchor_index = slots.index(anchor)
    pivot_context = slots[anchor_index-1:anchor_index+1]
    required = sorted(set(volume_slots + pressure_slots + pivot_context + post_slots + [stamp]))
    # Slice before any extrema, factor or missing-value calculation.
    visible = stock[stock.datetime.le(stamp) & stock.datetime.isin(required)].sort_values("datetime")
    if visible.datetime.duplicated().any() or len(visible) != len(required):
        return {**result, "data_status": "observed_prefix_gap_or_duplicate",
                **{f"{c}_status": "unknown" for c in "ABC"}}
    values = visible[["open", "high", "low", "close", "vol", "adj_factor"]].to_numpy(float)
    if (not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any()
            or not (abs(values[:, 5]/float(signal["signal_factor"])-1) <= 1e-5).all()):
        return {**result, "data_status": "observed_invalid_or_factor_change",
                **{f"{c}_status": "unknown" for c in "ABC"}}
    lookup = visible.set_index("datetime")
    if abs(float(lookup.loc[stamp, "close"])-float(signal["signal_close"])) > .011:
        return {**result, "data_status": "signal_close_mismatch",
                **{f"{c}_status": "unknown" for c in "ABC"}}
    volumes = lookup.loc[volume_slots, "vol"].to_numpy(float)
    denominator = float(np.median(volumes[:16]))
    if denominator > 0:
        result["volume_ratio"] = float(np.median(volumes[16:])/denominator)
        result["A"] = bool(age >= 3 and result["volume_ratio"] <= .8)
    else:
        result["A_status"] = "unknown_zero_reference_volume"
    # Exactly two context bars: every pivot index>=2 is strictly after anchor close.
    post = lookup.loc[pivot_context + post_slots].reset_index()
    pivots = confirmed_lows(post, stamp)
    result["confirmed_low_count"] = len(pivots)
    result["pressure"] = float(lookup.loc[pressure_slots, "high"].max())
    if not pivots:
        result["B_status"] = result["C_status"] = "known_no_confirmed_support"
        return result
    last = pivots[-1]
    support = float(post.iloc[last].low)
    result.update({"last_low": support, "last_low_at": str(post.iloc[last].datetime),
                   "last_low_confirmed_at": str(post.iloc[last+2].datetime),
                   "reference_stop": support*.995})
    if len(pivots) >= 2:
        previous = pivots[-2]
        result["prior_low"] = float(post.iloc[previous].low)
        result["B"] = bool(last-previous >= 4 and support >= result["prior_low"]*1.005
                           and float(post.iloc[last+1:].low.min()) >= support)
    else:
        result["B_status"] = "known_only_one_confirmed_low"
    reward = result["pressure"] - float(signal["signal_close"])
    risk = float(signal["signal_close"]) - result["reference_stop"]
    if risk > 0:
        result["space_risk_ratio"] = reward/risk
        result["C"] = bool(reward > 0 and result["space_risk_ratio"] >= 2)
    else:
        result["C_status"] = "known_nonpositive_reference_risk"
    return result


def extract(source: Path, out: Path):
    register(out)
    # Only decision-time fields can enter the extractor, even if CSV contains outcomes.
    signals = pd.read_csv(source, usecols=INPUT_COLUMNS, dtype={"anchor_date": str})
    first = (pd.to_datetime(signals.anchor_date.min())-pd.Timedelta(days=25)).strftime("%Y%m%d")
    last = pd.to_datetime(signals.signal_time).max().strftime("%Y%m%d")
    calendar = sorted(get_tradecal(start_date=first, end_date=last, source="database_only").cal_date.astype(str))
    bars = get_fifteenMin(ts_codes=sorted(signals.ts_code.unique()), start_date=first, end_date=last)
    bars.datetime = pd.to_datetime(bars.datetime)
    by_stock = {code: part for code, part in bars.groupby("ts_code")}
    records = []
    for row in signals.to_dict("records"):
        stock = by_stock.get(row["ts_code"], bars.iloc[:0])
        value = features(stock, row, calendar)
        truncated = features(stock[stock.datetime.le(pd.Timestamp(row["signal_time"]))], row, calendar)
        assert value == truncated, "Future removal changed structure features"
        records.append(value)
    result = pd.DataFrame(records)
    for group, terms in GROUPS.items():
        result[f"selected_{group}"] = result[terms].all(axis=1)
        unknown = pd.DataFrame({c: result[f"{c}_status"].str.startswith("unknown") for c in terms})
        known_false = ((~result[terms]) & (~unknown)).any(axis=1)
        result[f"unknown_{group}"] = unknown.any(axis=1) & ~known_false
    result.to_csv(out / "features.csv", index=False)
    write_json(out / "FEATURE_MANIFEST.json", {
        "source": str(source), "source_sha256": digest(source),
        "created_at": datetime.now(timezone.utc).isoformat(), "allowed_input_columns": INPUT_COLUMNS,
        "protocol_sha256": digest(out / "PROTOCOL.json"), "script_sha256": digest(__file__),
        "features_sha256": digest(out / "features.csv"), "signals": len(result),
        "prefix_invariance_checks": len(result), "data_status": result.data_status.value_counts().to_dict(),
        "groups": {g: {"selected": int(result[f"selected_{g}"].sum()),
                       "unknown": int(result[f"unknown_{g}"].sum())} for g in GROUPS}})


def metrics(frame, mode):
    buy = frame[frame[f"{mode}_buyable"].eq(True)]
    scored = buy[buy[f"{mode}_net"].notna()]
    values = scored[f"{mode}_net"]
    result = {"selected_signals": len(frame), "filled": len(buy), "unfilled": len(frame)-len(buy),
              "closed": len(scored), "unresolved": len(buy)-len(scored),
              "mean_net_pct": None, "median_net_pct": None, "win_pct": None,
              "worst_pct": None, "best_pct": None, "mean_95_pct": None, "mean_99_pct": None,
              "remove_best3_mean_pct": None, "blocked_exit_count": int(buy[f"{mode}_blocked_bars"].gt(0).sum())}
    if len(scored):
        result.update({"mean_net_pct": float(values.mean()*100), "median_net_pct": float(values.median()*100),
                       "win_pct": float(values.gt(0).mean()*100), "worst_pct": float(values.min()*100),
                       "best_pct": float(values.max()*100),
                       "remove_best3_mean_pct": float(values.sort_values().iloc[:-3].mean()*100) if len(values)>3 else None})
        blocks = scored.groupby(scored.signal_time.str[:10])[f"{mode}_net"].agg(["sum", "count"]).to_numpy()
        if len(blocks)>1:
            rng = np.random.default_rng(20260929)
            sampled = blocks[rng.integers(0, len(blocks), (10000, len(blocks)))].sum(axis=1)
            boot = sampled[:, 0]/sampled[:, 1]*100
            result["mean_95_pct"] = np.quantile(boot, [.025, .975]).tolist()
            result["mean_99_pct"] = np.quantile(boot, [.005, .995]).tolist()
    return result


def score(source: Path, out: Path):
    manifest = json.loads((out / "FEATURE_MANIFEST.json").read_text())
    assert manifest["features_sha256"] == digest(out / "features.csv")
    assert manifest["protocol_sha256"] == digest(out / "PROTOCOL.json")
    assert manifest["source_sha256"] == digest(source)
    feature = pd.read_csv(out / "features.csv")
    source_frame = pd.read_csv(source)
    outcomes = [c for c in source_frame if c.startswith(("old_", "carry_"))]
    joined = feature.merge(source_frame[["ts_code", "signal_time", *outcomes]], on=["ts_code", "signal_time"], validate="one_to_one")
    groups = {"baseline": joined, **{g: joined[joined[f"selected_{g}"].eq(True)] for g in GROUPS}}
    report = {"period": [str(joined.signal_time.min()), str(joined.signal_time.max())],
              "full_signal_denominator": len(joined), "data_status": manifest["data_status"],
              "groups": {g: {m: metrics(f, m) for m in ("old", "carry")} for g, f in groups.items()},
              "feature_manifest_sha256": digest(out / "FEATURE_MANIFEST.json"),
              "evaluated_at": datetime.now(timezone.utc).isoformat(), "training_gate_passed": []}
    if joined.signal_time.str.startswith("2025").all():
        for group in GROUPS:
            m = report["groups"][group]["old"]
            if m["closed"] >= 15 and m["mean_99_pct"] and m["mean_99_pct"][0] > 0 and m["unresolved"] == 0:
                report["training_gate_passed"].append(group)
        report["training_gate_passed"].sort(key=lambda g: (-report["groups"][g]["old"]["mean_99_pct"][0], g))
        report["promoted_rule"] = next(iter(report["training_gate_passed"]), None)
    joined.to_csv(out / "scored.csv", index=False)
    write_json(out / "SCORE.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["register", "extract", "score"])
    p.add_argument("--source", type=Path, default=Path("outputs/core_reactivation_causal_2025/trades.csv"))
    p.add_argument("--output", type=Path, default=Path("outputs/core_pullback_structure_2025"))
    args = p.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "register": register(args.output)
    elif args.command == "extract": extract(args.source, args.output)
    else: score(args.source, args.output)


if __name__ == "__main__": main()
