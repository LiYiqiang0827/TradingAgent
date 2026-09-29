"""以可实现的次日净收益为目标，而非四连板标签的单次时间外推检验。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline

from study_early_high_board_one_min_model import FEATURES


SOURCE = Path("outputs/one_min_early_high_board_model")
OUT = Path("outputs/one_min_early_return_model")


def _stats(frame: pd.DataFrame) -> dict:
    r = frame.net
    return {"n": len(frame), "four_board": int(frame.future_four_board.sum()),
            "net_mean_pct": round(100 * r.mean(), 3) if len(r) else None,
            "net_median_pct": round(100 * r.median(), 3) if len(r) else None,
            "win_pct": round(100 * r.gt(0).mean(), 2) if len(r) else None,
            "worst_pct": round(100 * r.min(), 3) if len(r) else None,
            "best_pct": round(100 * r.max(), 3) if len(r) else None}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prior = pd.read_csv(SOURCE / "train_2025.csv", dtype={"d0": str, "ts_code": str})
    future = pd.read_csv(SOURCE / "validation_2026.csv", dtype={"d0": str,
                                                                "ts_code": str})
    train = prior[prior.d0.lt("20250901")].copy()
    calibrate = prior[prior.d0.ge("20250901")].copy()
    model = make_pipeline(SimpleImputer(strategy="median"),
                          HistGradientBoostingRegressor(max_iter=100, learning_rate=.05,
                                                        max_leaf_nodes=10, min_samples_leaf=150,
                                                        l2_regularization=1, random_state=2026))
    model.fit(train[FEATURES], train.net.clip(-.15, .20))
    calibrate["forecast_net"] = model.predict(calibrate[FEATURES])
    future["forecast_net"] = model.predict(future[FEATURES])
    threshold = max(0.0, float(calibrate.forecast_net.quantile(.995)))

    def select(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[frame.forecast_net.ge(threshold)].sort_values(
            ["d0", "forecast_net", "ts_code"], ascending=[True, False, True]
        ).drop_duplicates("d0")

    selected_calibrate = select(calibrate)
    selected_future = select(future)
    report = {"method": "2025年1—8月训练固定复杂度回归，9—12月得分99.5百分位且预测净收益>0做校准，2026年6—9月用相同阈值，同日最多一只",
              "features": FEATURES, "threshold_predicted_net": threshold,
              "2025_calibration": {"all": _stats(calibrate),
                                   "selected": _stats(selected_calibrate)},
              "2026_validation": {"all": _stats(future),
                                   "selected": _stats(selected_future)},
              "caveat": "本模型在其他2026探索后才提出，2026数据并非严格封存；2025与2026一分钟数据来源不同。"}
    calibrate.to_csv(OUT / "calibration_2025.csv", index=False)
    future.to_csv(OUT / "validation_2026.csv", index=False)
    selected_future.to_csv(OUT / "selected_2026.csv", index=False)
    (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
