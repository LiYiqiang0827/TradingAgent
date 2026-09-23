"""Synthetic-only tests: never open historical labels, predictions, or reports."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from evaluate_gpt_replay import main, score_replay, validate_frozen_predictions


def prediction(day="20260105", **changes):
    result = {
        "date": day, "emotion_prediction": "hot", "mainline": "A", "top3": ["A", "B"],
        "leader": "X", "alternatives": ["Y"], "reasoning": "synthetic evidence only",
        "state_now": "test", "phase": "test", "confidence": "low", "evidence_ids": ["E1"],
        "packet_sha256": "a" * 64, "original_candidate_themes": ["A", "B", "S"],
        "original_leader_pools": {"A": ["X", "Y"], "B": ["U", "V"], "S": ["Z"]},
    }
    result.update(changes)
    return result


def frozen(predictions):
    raw = json.dumps(predictions, ensure_ascii=False).encode()
    return raw, {
        "predictions_sha256": hashlib.sha256(raw).hexdigest(), "prediction_count": len(predictions),
        "dates": [p["date"] for p in predictions],
        "packet_sha256_by_date": {p["date"]: p["packet_sha256"] for p in predictions},
        "frozen_at": "2026-09-21T00:00:00+00:00",
    }


def sample(days=7, start=date(2026, 1, 26)):
    dates = [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]
    predictions = [prediction(d) for d in dates]
    labels = []
    for i, d in enumerate(dates):
        y = {"date": d, "emotion": "hot" if i+1 < days else None,
             "mainline": ["A"] if i+3 < days else [],
             "mainline_next_day": ["B"] if i+1 < days else [],
             "leaders": {}, "height_leaders": {}, "maturity": {}}
        if i+1 < days:
            y["maturity"]["emotion"] = dates[i+1]
        if i+2 < days:
            y["maturity"]["leaders"] = dates[i+2]
            y["leaders"] = {
                "A": {"n": 2, "winners": ["X"], "returns": {"X": 2, "Y": 1}},
                "B": {"n": 2, "winners": ["U"], "returns": {"U": 3, "V": 0}},
                "S": {"n": 1, "winners": ["Z"], "returns": {"Z": 1}},
            }
            y["height_leaders"] = {"A": ["Y"], "B": ["V"], "S": ["Z"]}
        if i+3 < days:
            y["maturity"]["mainline"] = dates[i+3]
        labels.append(y)
    return predictions, labels


def test_freeze_enforces_160_and_matching_raw_bytes_dates_and_packet_hashes():
    predictions, _ = sample(160)
    raw, manifest = frozen(predictions)
    assert validate_frozen_predictions(raw, manifest) == predictions
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        validate_frozen_predictions(raw + b" ", manifest)
    with pytest.raises(ValueError, match="exactly 160"):
        short_raw, short_manifest = frozen(predictions[:-1])
        validate_frozen_predictions(short_raw, short_manifest)
    manifest["packet_sha256_by_date"][predictions[0]["date"]] = "b" * 64
    with pytest.raises(ValueError, match="packet SHA256 mismatch"):
        validate_frozen_predictions(raw, manifest)


@pytest.mark.parametrize("kind", ["duplicate", "unsorted", "manifest_dates", "missing_field", "top3", "timezone"])
def test_invalid_freezes_fail(kind):
    predictions, _ = sample(4)
    if kind == "duplicate":
        predictions[1]["date"] = predictions[0]["date"]
    elif kind == "unsorted":
        predictions.reverse()
    elif kind == "missing_field":
        del predictions[0]["reasoning"]
    elif kind == "top3":
        predictions[0]["top3"] = ["A", "B", "C", "D"]
    raw, manifest = frozen(predictions)
    if kind == "manifest_dates":
        manifest["dates"].reverse()
    elif kind == "timezone":
        manifest["frozen_at"] = "2026-09-21T00:00:00"
    with pytest.raises(ValueError):
        validate_frozen_predictions(raw, manifest, expected_count=4)


def test_complete_windows_abstentions_and_missing_price_stay_in_strict_denominator():
    predictions, labels = sample()
    predictions[0].update(mainline=None, leader=None, top3=[])
    del labels[1]["leaders"]["A"]
    del labels[1]["height_leaders"]["A"]
    predictions[2].update(mainline="OUTSIDE", leader="O", top3=["OUTSIDE"])
    before = deepcopy((predictions, labels))
    metrics, ledger = score_replay(predictions, labels)
    result = metrics["overall"]
    assert (predictions, labels) == before
    assert result["mainline_future3_top1"]["n"] == 4
    assert result["mainline_future3_top1"]["correct"] == 2
    assert result["mainline_future3_top3"]["n"] == 4
    chain = result["mainline_and_leader_end_to_end_strict"]
    assert chain["n"] == 4 and chain["correct"] == 1
    assert chain["unknown"] == 1 and chain["coverage"] == .75
    assert ledger[1]["measures"]["mainline_and_leader_end_to_end_strict"]["status"] == "unknown"
    assert ledger[1]["selected_pool"]["theme_in_original_pool"] is True
    assert ledger[2]["measures"]["mainline_and_leader_end_to_end_strict"]["hit"] is False
    assert result["diagnostics"]["theme_outside_original_pool"] == 1
    assert result["diagnostics"]["selected_theme_price_truth_missing"] == 1
    assert result["immature"] == {"future1": 1, "future2": 2, "future3": 3}


def test_return_within_selected_theme_and_correct_mainline_condition_are_distinct():
    predictions, labels = sample(5)
    predictions[0].update(mainline="B", top3=["B"], leader="U")
    metrics, _ = score_replay(predictions, labels)
    result = metrics["overall"]
    assert result["leader_selected_theme_return_multi"]["n"] == 3
    assert result["leader_selected_theme_return_multi"]["correct"] == 3
    assert result["leader_when_future3_mainline_correct_return_multi"]["n"] == 1
    assert result["mainline_and_leader_end_to_end_strict"]["n"] == 2
    assert result["mainline_and_leader_end_to_end_strict"]["correct"] == 1
    assert result["mainline_nextday_top1"]["correct"] == 1


def test_singleton_height_ties_and_top3_have_separate_targets():
    predictions, labels = sample(5)
    predictions[0].update(mainline="S", leader="Z", top3=["S", "A"])
    labels[1]["leaders"]["A"]["winners"] = ["X", "Y"]
    predictions[1]["leader"] = "Y"
    metrics, _ = score_replay(predictions, labels)
    result = metrics["overall"]
    assert result["leader_selected_theme_return_singleton"]["n"] == 1
    assert result["leader_selected_theme_return_singleton"]["correct"] == 1
    assert result["leader_selected_theme_return_multi"]["n"] == 2
    assert result["leader_height_secondary"]["n"] == 2
    assert result["leader_height_secondary"]["correct"] == 1
    assert result["mainline_future3_top1"]["correct"] == 1
    assert result["mainline_future3_top3"]["correct"] == 2


def test_emotion_refusal_is_in_confusion_support_and_balanced_accuracy():
    predictions, labels = sample(6)
    for prediction_row, truth_row, predicted, truth in zip(
        predictions, labels, [None, "cold", "hot", "hot", "neutral"],
        ["cold", "cold", "neutral", "hot", None]
    ):
        prediction_row["emotion_prediction"] = predicted
        truth_row["emotion"] = truth
    metrics, _ = score_replay(predictions, labels)
    emotion = metrics["overall"]["emotion"]
    assert emotion["n"] == 5 and emotion["correct"] == 2 and emotion["unknown"] == 1
    assert emotion["support"] == [2, 1, 1]
    assert emotion["abstention_column"] == [1, 0, 0]
    assert emotion["confusion_matrix"] == [[1, 0, 0], [0, 0, 1], [0, 0, 1]]
    assert emotion["balanced_accuracy"] == .5


def test_monthly_reporting_keeps_original_calendar_maturity():
    predictions, labels = sample(10, start=date(2026, 1, 29))
    metrics, _ = score_replay(predictions, labels)
    assert metrics["by_month"]["202601"]["mainline_future3_top1"]["n"] == 3
    assert sum(m["mainline_future3_top1"]["n"] for m in metrics["by_month"].values()) == 7


def test_frozen_baselines_and_selected_theme_comparison_never_fill_missing_fields():
    predictions, labels = sample(5)
    predictions[0]["baselines"] = {
        "mainline_breadth": "B", "leader_height": {"theme": "B", "leader": "V"},
        "leader_early": "U", "leader_capacity": {"theme": "B", "leader": "U"},
    }
    predictions[0]["leaders_by_theme"] = {"A": {"height": "Y", "early": "X", "capacity": "X"}}
    metrics, ledger = score_replay(predictions, labels)
    baseline = metrics["overall"]["baselines"]
    width = baseline["mainline_breadth"]["mainline_future3_top1"]
    assert width["n"] == 2 and width["correct"] == 0 and width["unknown"] == 1
    assert baseline["breadth_leader_early"]["leader_selected_theme_return_multi"]["correct"] == 1
    assert baseline["breadth_leader_height"]["leader_height_secondary"]["correct"] == 1
    assert baseline["selected_theme_leader_early"]["leader_selected_theme_return_multi"]["correct"] == 1
    assert baseline["selected_theme_leader_early"]["leader_selected_theme_return_multi"]["unknown"] == 2
    assert ledger[1]["baselines"]["mainline_breadth"]["theme_prediction_missing"] is True


def test_pool_omission_is_unknown_and_news_absence_is_not_zero():
    predictions, labels = sample(4)
    del predictions[0]["original_candidate_themes"]
    del predictions[0]["original_leader_pools"]
    del labels[0]["leaders"]["A"]
    predictions[1]["news_gap"] = True
    predictions[2]["news_gap"] = False
    metrics, ledger = score_replay(predictions, labels)
    result = metrics["overall"]["diagnostics"]
    assert ledger[0]["selected_pool"]["theme_in_original_pool"] is None
    assert result["theme_outside_original_pool"] == 0
    assert result["original_pool_membership_unknown"] == 1
    assert result["news_gap"] == 1 and result["news_gap_unknown"] == 2


def test_bad_label_calendar_and_pool_mismatch_fail():
    predictions, labels = sample(4)
    labels[0]["maturity"]["mainline"] = labels[1]["date"]
    with pytest.raises(ValueError, match="maturity disagrees"):
        score_replay(predictions, labels)
    predictions, labels = sample(4)
    predictions[0]["original_leader_pools"]["A"] = ["X"]
    with pytest.raises(ValueError, match="pool size differs"):
        score_replay(predictions, labels)
    with pytest.raises(ValueError, match="missing label dates"):
        score_replay(predictions, labels[1:])


def test_validate_only_and_invalid_hash_do_not_open_labels(tmp_path, monkeypatch):
    predictions, _ = sample(160)
    raw, manifest = frozen(predictions)
    prediction_path = tmp_path / "frozen_predictions.json"
    manifest_path = tmp_path / "prediction_manifest.json"
    label_path = tmp_path / "FORBIDDEN_labels.json"
    prediction_path.write_bytes(raw)
    manifest_path.write_text(json.dumps(manifest))
    original_read = Path.read_bytes

    def guarded_read(path):
        if path == label_path:
            raise AssertionError("future labels were read before validation")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    arguments = ["--predictions", str(prediction_path), "--manifest", str(manifest_path),
                 "--labels", str(label_path), "--output", str(tmp_path)]
    assert main(arguments + ["--validate-only"]) == 0
    prediction_path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        main(arguments)


def test_synthetic_cli_writes_only_requested_outputs(tmp_path):
    predictions, labels = sample(160)
    raw, manifest = frozen(predictions)
    prediction_path, manifest_path, label_path = [tmp_path / name for name in (
        "frozen_predictions.json", "prediction_manifest.json", "synthetic_truth.json")]
    prediction_path.write_bytes(raw)
    manifest_path.write_text(json.dumps(manifest))
    label_path.write_text(json.dumps(labels))
    output = tmp_path / "scored"
    main(["--predictions", str(prediction_path), "--manifest", str(manifest_path),
          "--labels", str(label_path), "--output", str(output)])
    metrics = json.loads((output / "metrics.json").read_text())
    ledger = json.loads((output / "daily_ledger.json").read_text())
    assert len(ledger) == 160
    assert metrics["overall"]["mainline_and_leader_end_to_end_strict"]["n"] == 157
    assert metrics["provenance"]["freeze_validated_before_labels_read"] is True
    assert prediction_path.read_bytes() == raw
