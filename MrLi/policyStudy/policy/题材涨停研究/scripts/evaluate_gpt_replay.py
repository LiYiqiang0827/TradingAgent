"""Independent, evaluation-only scoring for frozen GPT historical replay.

No market provider, prediction model, training, or label construction is imported.
Only main() reads labels, after validating all frozen prediction bytes. Use
--validate-only to check a freeze without opening any future truth file.

Manifest contract: predictions_sha256 (raw file bytes), prediction_count (160),
dates (ordered YYYYMMDD strings), packet_sha256_by_date, frozen_at (ISO timestamp
with timezone). A matching hash establishes consistency, not proof that a human
or process had no earlier access to labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re


BASE = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BASE / "data" / "gpt_replay_v2"
EMOTIONS = ("cold", "neutral", "hot")
REQUIRED_FIELDS = {
    "date", "emotion_prediction", "mainline", "top3", "leader", "alternatives",
    "reasoning", "state_now", "phase", "confidence", "evidence_ids", "packet_sha256",
}
ROLES = ("height", "early", "capacity")
METRIC_DEFINITIONS = {
    "mainline_future3_top1": "All mature future-3 windows; ties accepted; abstention is a miss.",
    "mainline_future3_top3": "All mature future-3 windows; any of up to 3 choices wins; empty list is a miss.",
    "mainline_nextday_top1": "All mature next-session windows, separate from future-3 mainline.",
    "leader_selected_theme_return_multi": "Mature two-session windows with >=2 original candidates in the selected theme, whether the theme was correct or not. Missing price truth stays unknown and is a strict miss when candidate count is known.",
    "leader_when_future3_mainline_correct_return_multi": "Same return target and >=2 candidates, additionally requiring the selected theme to match the mature future-3 mainline.",
    "leader_selected_theme_return_singleton": "One original candidate only; reported separately from meaningful multi-candidate selection.",
    "leader_when_future3_mainline_correct_return_singleton": "Singleton return target conditioned on correct mature future-3 mainline.",
    "mainline_and_leader_end_to_end_strict": "Every date with complete future-3 and two-session windows. Correct future-3 theme AND original-pool two-session excess-return winner required. Abstention, out-of-pool choice and unknown price truth score zero; unknown truth and coverage are also reported.",
    "leader_height_secondary": "SECONDARY proxy: original-pool future maximum-height winners from the unchanged height_leaders label; >=2 candidates and nonempty height winners required. Not a return target or proof of a true market leader; underlying height can mix 10%/20% limit regimes.",
    "accuracy_and_coverage": "accuracy = correct / n, including unknowns as strict misses. coverage = known_n / n; known_accuracy is separately shown. n=0 produces null. Eligibility unknown because the selected pool size is missing is counted in diagnostics, never assumed singleton or multi.",
    "emotion": "Next-session cold/neutral/hot label unchanged. Mature sessions with missing truth stay unknown. Three-by-three confusion matrix is truth rows and prediction columns; refusals have a separate column so they remain in accuracy and per-class recall denominators. Balanced accuracy averages recalls of truth classes present.",
    "baselines": "Only frozen baselines are scored; no future fitting or reconstruction. Width theme is baselines.mainline_breadth. Stock roles are leader_height/leader_early/leader_capacity strings (width theme) or {theme,leader}. Optional leaders_by_theme[theme][height|early|capacity] permits same-selected-theme comparison. Missing fields are explicitly unknown.",
    "pool_and_news": "Original pool membership comes only from frozen original_candidate_themes/original_leader_pools; label omission cannot establish pool exclusion. news_gap is frozen metadata, not inferred from absence of news evidence IDs.",
}


def _choice(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("dates must be YYYYMMDD strings")
    datetime.strptime(value, "%Y%m%d")
    return value


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_frozen_predictions(raw: bytes, manifest: dict, expected_count: int = 160):
    """Validate in-memory raw bytes and manifest; do not inspect any labels."""
    if not isinstance(manifest, dict):
        raise ValueError("freeze manifest must be an object")
    digest = hashlib.sha256(raw).hexdigest()
    if manifest.get("predictions_sha256") != digest:
        raise ValueError("frozen prediction SHA256 mismatch")
    predictions = json.loads(raw)
    if not isinstance(predictions, list) or len(predictions) != expected_count:
        raise ValueError(f"exactly {expected_count} frozen predictions required")
    if manifest.get("prediction_count") != expected_count:
        raise ValueError("manifest prediction_count mismatch")
    dates = []
    for prediction in predictions:
        if not isinstance(prediction, dict) or REQUIRED_FIELDS - prediction.keys():
            raise ValueError("prediction missing required fields")
        dates.append(_date(prediction["date"]))
        if prediction["emotion_prediction"] not in (*EMOTIONS, None, ""):
            raise ValueError("emotion_prediction must be cold, neutral, hot or null/empty")
        for field in ("mainline", "leader"):
            if prediction[field] is not None and not isinstance(prediction[field], str):
                raise ValueError(f"{field} must be a string or null")
        top3 = prediction["top3"]
        if (not isinstance(top3, list) or len(top3) > 3
                or any(not isinstance(item, str) or not item.strip() for item in top3)
                or len(set(top3)) != len(top3)):
            raise ValueError("top3 must contain at most 3 distinct nonempty strings")
        if not isinstance(prediction["evidence_ids"], list):
            raise ValueError("evidence_ids must be a list")
        if not _sha(prediction["packet_sha256"]):
            raise ValueError("packet_sha256 must be a lowercase SHA256 digest")
        pool = prediction.get("original_leader_pools")
        if pool is not None and (not isinstance(pool, dict) or any(
            not isinstance(v, list) or any(not isinstance(c, str) for c in v)
            or len(v) != len(set(v)) for v in pool.values()
        )):
            raise ValueError("original_leader_pools must map themes to unique stock lists")
        themes = prediction.get("original_candidate_themes")
        if themes is not None and (not isinstance(themes, list)
                                   or any(not isinstance(t, str) for t in themes)):
            raise ValueError("original_candidate_themes must be a list of theme strings")
        if "news_gap" in prediction and prediction["news_gap"] is not None and not isinstance(prediction["news_gap"], bool):
            raise ValueError("news_gap must be a boolean or null")
    if len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError("prediction dates must be unique and sorted")
    if manifest.get("dates") != dates:
        raise ValueError("manifest ordered dates mismatch")
    packet_hashes = manifest.get("packet_sha256_by_date")
    if not isinstance(packet_hashes, dict) or set(packet_hashes) != set(dates):
        raise ValueError("manifest packet hash dates mismatch")
    if any(packet_hashes[p["date"]] != p["packet_sha256"] for p in predictions):
        raise ValueError("frozen packet SHA256 mismatch")
    try:
        frozen = datetime.fromisoformat(manifest["frozen_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("manifest requires ISO frozen_at") from exc
    if frozen.utcoffset() is None:
        raise ValueError("frozen_at requires an explicit timezone")
    return predictions


def proportion(correct, n, unknown=0):
    """Strict rate and observable-truth rate, with Wilson interval for strict rate."""
    known = n - unknown
    result = {"correct": correct, "n": n, "accuracy": correct / n if n else None,
              "unknown": unknown, "known_n": known, "coverage": known / n if n else None,
              "known_accuracy": correct / known if known else None, "wilson95": None}
    if n:
        p, z = correct / n, 1.95996398454
        center = (p + z*z/(2*n)) / (1 + z*z/n)
        half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
        result["wilson95"] = [max(0.0, center-half), min(1.0, center+half)]
    return result


def _measure(eligible, hit=None, reason=None):
    return {"eligible": bool(eligible), "hit": hit if eligible else None,
            "status": "ineligible" if not eligible else "unknown" if hit is None else "hit" if hit else "miss",
            "reason": reason}


def _pool_info(prediction, label, theme):
    pools = prediction.get("original_leader_pools")
    themes = prediction.get("original_candidate_themes")
    original = set(themes) if themes is not None else set(pools) if pools is not None else None
    in_pool = theme in original if theme and original is not None else None
    chosen = label.get("leaders", {}).get(theme) if theme else None
    n = len(pools[theme]) if theme and pools is not None and theme in pools else chosen.get("n") if chosen else None
    if chosen and n != chosen.get("n"):
        raise ValueError(f"{prediction['date']}: frozen original pool size differs from unchanged label")
    if chosen and in_pool is False:
        raise ValueError(f"{prediction['date']}: label theme conflicts with frozen original pool")
    if not theme:
        reason = "mainline_abstention"
    elif in_pool is False:
        reason = "theme_not_in_original_candidate_pool"
    elif chosen is None:
        reason = "price_truth_missing" if in_pool else "price_truth_or_original_pool_unknown"
    else:
        reason = None
    return {"theme_in_original_pool": in_pool, "candidate_count": n,
            "return_truth_available": chosen is not None, "reason": reason}, chosen


def _score_choices(prediction, label, windows, theme, top3, leader, *,
                   theme_missing=False, leader_missing=False, top3_missing=False):
    future3 = label.get("mainline", [])
    nextday = label.get("mainline_next_day", [])
    main_hit = theme in future3 if future3 and not theme_missing else None
    pool, chosen = _pool_info(prediction, label, theme)
    multi = pool["candidate_count"] is not None and pool["candidate_count"] >= 2
    singleton = pool["candidate_count"] == 1
    return_hit = leader in chosen["winners"] if chosen and not leader_missing else None
    height_winners = label.get("height_leaders", {}).get(theme, [])
    complete = windows["future3"] and windows["future2"]
    chain_reason = None
    if theme_missing or leader_missing:
        chain, chain_reason = None, "baseline_prediction_missing"
    elif not future3:
        chain, chain_reason = None, "mainline_truth_missing"
    elif not theme or not leader:
        chain, chain_reason = False, "prediction_abstention"
    elif pool["theme_in_original_pool"] is False:
        chain, chain_reason = False, "theme_not_in_original_candidate_pool"
    elif main_hit is False:
        chain, chain_reason = False, "mainline_wrong"
    elif chosen is None:
        chain, chain_reason = None, pool["reason"]
    else:
        chain = bool(main_hit and return_hit)
    measures = {
        "mainline_future3_top1": _measure(windows["future3"], main_hit,
                                         "baseline_prediction_missing" if theme_missing else "mainline_truth_missing" if not future3 else None),
        "mainline_future3_top3": _measure(windows["future3"],
                                         bool(set(top3) & set(future3)) if future3 and not top3_missing else None),
        "mainline_nextday_top1": _measure(windows["future1"], theme in nextday if nextday and not theme_missing else None),
        "leader_selected_theme_return_multi": _measure(windows["future2"] and multi, return_hit, pool["reason"]),
        "leader_when_future3_mainline_correct_return_multi": _measure(complete and multi and main_hit is True, return_hit, pool["reason"]),
        "leader_selected_theme_return_singleton": _measure(windows["future2"] and singleton, return_hit, pool["reason"]),
        "leader_when_future3_mainline_correct_return_singleton": _measure(complete and singleton and main_hit is True, return_hit, pool["reason"]),
        "mainline_and_leader_end_to_end_strict": _measure(complete, chain, chain_reason),
        "leader_height_secondary": _measure(windows["future2"] and multi and bool(height_winners),
                                            leader in height_winners if not leader_missing else None),
    }
    return {"mainline": theme, "leader": leader, "theme_prediction_missing": theme_missing,
            "leader_prediction_missing": leader_missing, "selected_pool": pool,
            "height_truth": height_winners, "measures": measures}


def score_day(prediction, label, windows):
    """Score one supplied prediction and label. windows are calendar maturity flags."""
    theme, leader = _choice(prediction.get("mainline")), _choice(prediction.get("leader"))
    core = _score_choices(prediction, label, windows, theme, prediction.get("top3", []), leader)
    emotion = _choice(prediction.get("emotion_prediction"))
    truth_emotion = label.get("emotion")
    original_themes = prediction.get("original_candidate_themes")
    if original_themes is None and prediction.get("original_leader_pools") is not None:
        original_themes = list(prediction["original_leader_pools"])
    mainline_absent = (not bool(set(label.get("mainline", [])) & set(original_themes))
                      if windows["future3"] and label.get("mainline") and original_themes is not None else None)
    if truth_emotion is not None and truth_emotion not in EMOTIONS:
        raise ValueError("unknown emotion truth class; label definitions must not change")
    core["measures"]["emotion"] = _measure(windows["future1"], emotion == truth_emotion if truth_emotion else None)
    baseline = prediction.get("baselines") or {}
    breadth = _choice(baseline.get("mainline_breadth"))
    breadth_missing = "mainline_breadth" not in baseline or baseline["mainline_breadth"] is None
    baselines = {"mainline_breadth": _score_choices(
        prediction, label, windows, breadth, [breadth] if breadth else [], None,
        theme_missing=breadth_missing, top3_missing=breadth_missing, leader_missing=True)}
    for role in ROLES:
        field = "leader_" + role
        value = baseline.get(field)
        if isinstance(value, dict):
            base_theme, base_leader = _choice(value.get("theme")), _choice(value.get("leader"))
            theme_missing = "theme" not in value or value["theme"] is None
            missing = "leader" not in value or value["leader"] is None
        else:
            base_theme, base_leader = breadth, _choice(value)
            theme_missing = breadth_missing
            missing = field not in baseline or value is None
        baselines["breadth_" + field] = _score_choices(
            prediction, label, windows, base_theme, [base_theme] if base_theme else [], base_leader,
            theme_missing=theme_missing, top3_missing=theme_missing, leader_missing=missing)
        by_theme = prediction.get("leaders_by_theme") or baseline.get("leaders_by_theme") or {}
        same_value = by_theme.get(theme, {}).get(role, by_theme.get(theme, {}).get(field))
        if isinstance(same_value, dict):
            same_value = same_value.get("leader")
        baselines["selected_theme_" + field] = _score_choices(
            prediction, label, windows, theme, prediction.get("top3", []), _choice(same_value),
            leader_missing=same_value is None)
    return {"date": prediction["date"], "prediction": prediction, "truth": label,
            "windows": windows, "emotion_prediction": emotion, "emotion_truth": truth_emotion,
            **core, "baselines": baselines,
            "diagnostics": {
                "mainline_abstention": theme is None,
                "leader_abstention": leader is None,
                "top3_abstention": not prediction.get("top3"),
                "emotion_abstention": emotion is None,
                "theme_outside_original_pool": core["selected_pool"]["theme_in_original_pool"] is False,
                "original_pool_membership_unknown": bool(theme) and core["selected_pool"]["theme_in_original_pool"] is None,
                "selected_candidate_count_unknown": bool(theme) and core["selected_pool"]["candidate_count"] is None,
                "selected_theme_price_truth_missing": windows["future2"] and bool(theme) and core["selected_pool"]["theme_in_original_pool"] is True and not core["selected_pool"]["return_truth_available"],
                "no_positive_future_height_winner": windows["future2"] and core["selected_pool"]["return_truth_available"] and not core["height_truth"],
                "height_secondary_tied_winners": windows["future2"] and len(core["height_truth"]) > 1,
                "true_mainline_absent_from_original_pool": mainline_absent,
                "news_gap": prediction.get("news_gap"),
            }}


def _aggregate_measures(rows):
    if not rows:
        return {}
    result = {}
    for name in rows[0]["measures"]:
        eligible = [r["measures"][name] for r in rows if r["measures"][name]["eligible"]]
        result[name] = proportion(sum(m["hit"] is True for m in eligible), len(eligible),
                                  sum(m["hit"] is None for m in eligible))
    return result


def summarize(ledger):
    """Aggregate a full ledger or a month, without recalculating window maturity."""
    result = {"days": len(ledger), **_aggregate_measures(ledger)}
    result["immature"] = {name: sum(not r["windows"][name] for r in ledger)
                          for name in ("future1", "future2", "future3")}
    result["diagnostics"] = dict(Counter(
        key for row in ledger for key, value in row["diagnostics"].items()
        if key != "news_gap" and value is True))
    for key in ("mainline_abstention", "leader_abstention", "top3_abstention", "emotion_abstention",
                "theme_outside_original_pool", "original_pool_membership_unknown",
                "selected_candidate_count_unknown", "selected_theme_price_truth_missing",
                "no_positive_future_height_winner", "height_secondary_tied_winners",
                "true_mainline_absent_from_original_pool"):
        result["diagnostics"].setdefault(key, 0)
    result["diagnostics"]["news_gap"] = sum(r["diagnostics"]["news_gap"] is True for r in ledger)
    result["diagnostics"]["news_gap_unknown"] = sum(r["diagnostics"]["news_gap"] is None for r in ledger)
    result["diagnostics"]["theme_outside_original_pool_mature_future3"] = sum(
        r["windows"]["future3"] and r["diagnostics"]["theme_outside_original_pool"] for r in ledger)
    if ledger:
        emotion = result["emotion"]
        matrix = [[0] * 3 for _ in EMOTIONS]
        abstention_column = [0] * 3
        for row in ledger:
            if not row["windows"]["future1"] or row["emotion_truth"] is None:
                continue
            i = EMOTIONS.index(row["emotion_truth"])
            if row["emotion_prediction"] in EMOTIONS:
                matrix[i][EMOTIONS.index(row["emotion_prediction"])] += 1
            else:
                abstention_column[i] += 1
        supports = [sum(row) + abstention_column[i] for i, row in enumerate(matrix)]
        recalls = [matrix[i][i] / n if n else None for i, n in enumerate(supports)]
        present = [r for r in recalls if r is not None]
        emotion.update({"confusion_labels": list(EMOTIONS), "confusion_matrix": matrix,
                        "confusion_orientation": "rows=true, columns=predicted",
                        "abstention_column": abstention_column, "support": supports,
                        "recall_by_class": dict(zip(EMOTIONS, recalls)),
                        "balanced_accuracy": sum(present) / len(present) if present else None,
                        "majority_baseline_known_truth": max(supports) / sum(supports) if sum(supports) else None})
    result["baselines"] = {}
    for name in ledger[0]["baselines"] if ledger else []:
        rows = [r["baselines"][name] for r in ledger]
        metrics = _aggregate_measures(rows)
        metrics["missing_theme_predictions"] = sum(r["theme_prediction_missing"] for r in rows)
        metrics["missing_leader_predictions"] = sum(r["leader_prediction_missing"] for r in rows)
        result["baselines"][name] = metrics
    return result


def score_replay(predictions, labels):
    """Pure scorer accepting memory objects; it neither reads nor alters labels.

    labels must include the original ordered source calendar (may cover more
    dates than predictions). Calendar maturity is independent of missing price
    labels and monthly reporting; source make_labels writes no explicit nextday
    maturity marker when emotion prices are missing.
    """
    dates = [_date(y["date"]) for y in labels]
    if dates != sorted(set(dates)):
        raise ValueError("label dates must be unique and sorted")
    prediction_dates = [p["date"] for p in predictions]
    if prediction_dates != sorted(set(prediction_dates)):
        raise ValueError("prediction dates must be unique and sorted")
    positions = {date: i for i, date in enumerate(dates)}
    missing = set(prediction_dates) - positions.keys()
    if missing:
        raise ValueError(f"missing label dates: {sorted(missing)}")
    ledger = []
    for prediction in predictions:
        i = positions[prediction["date"]]
        label = labels[i]
        windows = {f"future{n}": i + n < len(dates) for n in (1, 2, 3)}
        for task, n in (("mainline", 3), ("leaders", 2)):
            explicit = label.get("maturity", {}).get(task)
            if explicit is not None and (not windows[f"future{n}"] or explicit != dates[i+n]):
                raise ValueError(f"{prediction['date']}: {task} maturity disagrees with source calendar")
        ledger.append(score_day(prediction, label, windows))
    metrics = {"schema_version": "gpt_replay_score_v1", "label_definition": "unchanged stage1_v1 make_labels",
               "metric_definitions": METRIC_DEFINITIONS,
               "overall": summarize(ledger),
               "by_month": {month: summarize([r for r in ledger if r["date"].startswith(month)])
                            for month in sorted({r["date"][:6] for r in ledger})}}
    return metrics, ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_OUTPUT / "frozen_predictions.json")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT / "prediction_manifest.json")
    parser.add_argument("--labels", type=Path, default=BASE / "data" / "stage1_v1" / "labels.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validate-only", action="store_true", help="verify freeze without reading labels")
    args = parser.parse_args(argv)
    raw = args.predictions.read_bytes()
    manifest_raw = args.manifest.read_bytes()
    manifest = json.loads(manifest_raw)
    predictions = validate_frozen_predictions(raw, manifest)
    if args.validate_only:
        print(json.dumps({"freeze_valid": True, "prediction_count": len(predictions), "labels_read": False}))
        return 0
    # This is the only future-truth file read in this standalone module.
    labels_raw = args.labels.read_bytes()
    labels = json.loads(labels_raw)
    metrics, ledger = score_replay(predictions, labels)
    metrics["provenance"] = {
        "predictions_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "labels_sha256": hashlib.sha256(labels_raw).hexdigest(),
        "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "frozen_at": manifest["frozen_at"], "scored_at": datetime.now(timezone.utc).isoformat(),
        "prediction_count": len(predictions), "freeze_validated_before_labels_read": True,
        "freeze_limit": "Hash consistency does not independently prove prior information isolation.",
    }
    outputs = [(args.output / "metrics.json", metrics), (args.output / "daily_ledger.json", ledger)]
    inputs = {p.resolve() for p in (args.predictions, args.manifest, args.labels)}
    if any(path.resolve() in inputs for path, _ in outputs):
        raise ValueError("output must not overwrite a frozen input or source label")
    args.output.mkdir(parents=True, exist_ok=True)
    for path, content in outputs:
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"scored_days": len(ledger), "metrics": str(outputs[0][0]), "ledger": str(outputs[1][0])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
