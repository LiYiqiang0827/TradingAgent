"""首板后次日09:35可见特征：2025训练，2026统一TDX一分钟数据复核。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from study_early_high_board_model import _daily_features, _graph_features, SOURCE


OUT = Path("outputs/one_min_early_high_board_model")
FILES = {
    2025: Path("outputs/one_min_early_high_board_2025/events.csv"),
    2026: Path("outputs/one_min_early_high_board_2026/events.csv"),
}
FEATURES = [
    "prior_theme_width", "prior_theme_heat", "prior_theme_rank",
    "seal_minutes", "seal_ratio", "log_float", "log_board_amount",
    "close_to_ma20", "close_to_ma60", "close_to_prev20_high",
    "prior20_range", "return_5", "return_20", "volume_ratio_20",
    "close_935_pct", "open_gap_pct", "first5_body_pct", "last3_gain_pct",
    "close_location",
]


def _load(year: int) -> pd.DataFrame:
    frame = pd.read_csv(FILES[year], dtype={"dminus1": str, "d0": str,
                                            "ts_code": str})
    frame = frame[frame.quality_ok & frame.buyable_936 & frame.net.notna()].copy()
    original = pd.read_csv(SOURCE[year][0],
                           usecols=["dminus1", "d0", "ts_code", "prior_theme_rank"],
                           dtype={"dminus1": str, "d0": str, "ts_code": str})
    frame = frame.merge(original, on=["dminus1", "d0", "ts_code"],
                        how="left", validate="one_to_one")
    graph = _graph_features(SOURCE[year][1], frame.dminus1.min(), frame.dminus1.max())
    frame = frame.merge(graph, on=["dminus1", "ts_code"], how="left",
                        validate="one_to_one")
    daily = _daily_features(sorted(frame.ts_code.unique()), frame.dminus1.min(),
                            frame.dminus1.max())
    frame = frame.merge(daily, on=["dminus1", "ts_code"], how="left",
                        validate="many_to_one")
    frame[FEATURES] = frame[FEATURES].replace([np.inf, -np.inf], np.nan)
    return frame


def _stats(frame: pd.DataFrame) -> dict:
    return {"n": len(frame), "future_four_board": int(frame.future_four_board.sum()),
            "future_four_rate_pct": round(100 * frame.future_four_board.mean(), 3)
            if len(frame) else None,
            "mean_net_pct": round(100 * frame.net.mean(), 3) if len(frame) else None,
            "median_net_pct": round(100 * frame.net.median(), 3) if len(frame) else None,
            "positive_pct": round(100 * frame.net.gt(0).mean(), 2) if len(frame) else None,
            "worst_pct": round(100 * frame.net.min(), 3) if len(frame) else None,
            "blocked_exit": int(frame.blocked_bars.gt(0).sum())}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    train = _load(2025)
    test = _load(2026)
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(C=0.1, class_weight="balanced",
                                             max_iter=1000))
    model.fit(train[FEATURES], train.future_four_board.astype(int))
    train["score"] = model.predict_proba(train[FEATURES])[:, 1]
    test["score"] = model.predict_proba(test[FEATURES])[:, 1]
    threshold = float(train.score.quantile(.998))
    selected_train = train[train.score.ge(threshold)].sort_values(
        ["d0", "score", "ts_code"], ascending=[True, False, True]).drop_duplicates("d0")
    selected_test = test[test.score.ge(threshold)].sort_values(
        ["d0", "score", "ts_code"], ascending=[True, False, True]).drop_duplicates("d0")
    result = {"method": "2025数据训练L2逻辑回归，2025得分99.8百分位固定阈值，同日最多一只，2026年6—9月复核",
              "features": FEATURES, "threshold": threshold,
              "2025_train": {"all_buyable": _stats(train), "selected": _stats(selected_train)},
              "2026_validation": {"all_buyable": _stats(test), "selected": _stats(selected_test)},
              "limitations": "2025一分钟多来自CSV，2026年6—9月为TDX，来源变化与年份变化耦合；09:36和次日一分钟开盘是成交代理。"}
    train.to_csv(OUT / "train_2025.csv", index=False)
    test.to_csv(OUT / "validation_2026.csv", index=False)
    selected_test.to_csv(OUT / "selected_2026.csv", index=False)
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
