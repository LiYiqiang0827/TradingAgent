from copy import deepcopy
import numpy as np
import pandas as pd
from stage1_engine import chain_features, intraday_rank, parse_time
from run_stage1 import train, proportion, snapshot, write_json, read_json


def test_return_features_are_prefix_invariant():
    frame = pd.DataFrame({"ts_code": ["A"]*30, "trade_date": [f"202601{i:02}" for i in range(1,31)], "pct_chg": range(30)})
    before = chain_features(frame.iloc[:20])
    changed = frame.copy()
    changed.loc[20:, "pct_chg"] = -90
    after = chain_features(changed).iloc[:20]
    pd.testing.assert_frame_equal(before, after)


def test_intraday_future_rows_do_not_change_ranking():
    themes = [{"theme": "A", "rule_score": 50, "phase": "分歧"}]
    leaders = [{"theme": "A", "ts_code": code, "rule_score": 50} for code in ["X", "Y"]]
    frame = pd.DataFrame([{"ts_code": code, "datetime": "2026-01-06 "+time, "price": price, "vol": 10}
                          for code in ["X", "Y"] for time, price in [("09:31:00", 10), ("09:45:00", 10.2), ("14:00:00", 12)]])
    original = intraday_rank(themes, leaders, frame, {"X": 10, "Y": 10}, "09:45")
    frame.loc[frame.datetime.str.contains("14:00"), "price"] = 1
    frame = pd.concat([frame, pd.DataFrame([{"ts_code": "FUTURE_WINNER", "datetime": "2026-01-06 09:45:00", "price": 1000, "vol": 1}])])
    assert original == intraday_rank(themes, leaders, frame, {"X": 10, "Y": 10}, "09:45")
    assert original[0]["eligible"]


def test_missing_or_stale_member_is_not_silently_removed():
    themes = [{"theme": "A", "rule_score": 50, "phase": "分歧"}]
    leaders = [{"theme": "A", "ts_code": code, "rule_score": 50} for code in ["X", "Y"]]
    frame = pd.DataFrame([{"ts_code": "X", "datetime": "2026-01-06 09:45:00", "price": 10.2},
                          {"ts_code": "Y", "datetime": "2026-01-06 09:44:00", "price": 10.2}])
    rank = intraday_rank(themes, leaders, frame, {"X": 10, "Y": 10}, "09:45")[0]
    assert rank["coverage"] == .5 and not rank["eligible"] and rank["leader"] is None


def test_matured_labels_only_enter_training():
    records = [{"date": "20260105", "market": {}, "themes": [], "leaders": []},
               {"date": "20260106", "market": {}, "themes": [], "leaders": []},
               {"date": "20260701", "market": {}, "themes": [], "leaders": []}]
    labels = [{"date": r["date"], "maturity": {"emotion": m}, "emotion": "cold"}
              for r, m in zip(records, ["20260106", "20260107", "20260702"])]
    _, meta = train(records, labels, "20260106")
    assert meta["sample_counts"]["emotion"] == 1
    changed = deepcopy(labels)
    changed[1]["emotion"] = "hot"
    changed[2]["emotion"] = "hot"
    assert train(records, changed, "20260106")[1] == meta


def test_missing_sample_and_timezone_are_explicit():
    assert proportion(0, 0)["accuracy"] is None
    assert proportion(10, 10)["wilson95"][0] < 1
    assert np.isnan(parse_time("01:25:00"))


def test_blind_snapshot_omits_future_and_prior_model_answers(tmp_path):
    write_json(tmp_path/"features.json", [
        {"date": "20260629", "market": {"state": "answer", "breadth": .5}},
        {"date": "20260630", "themes": [{"theme": "A", "rule_score": 99, "phase": "answer", "breadth": 3}]},
        {"date": "20260701", "future_fact": "forbidden"},
    ])
    # No predictions.json is provided: an independent packet must not need it.
    snapshot(tmp_path, "20260630", blind=True)
    packet = read_json(tmp_path/"snapshots/20260630/blind_decision_packet.json")
    assert packet["current"]["themes"] == [{"theme": "A", "breadth": 3}]
    assert packet["past_five_sessions"][0]["market"] == {"breadth": .5}
    assert "prediction" not in packet
    assert set(packet["reference_texts_without_results"]) == {"methodology.md", "blind-protocol.md", "feature-dictionary.md", "attribution-and-roles.md"}
    assert "forbidden" not in str(packet)
