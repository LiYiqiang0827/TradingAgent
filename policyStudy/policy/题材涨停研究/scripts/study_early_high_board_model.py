"""用2025年训练、2026年复核首板后次日早盘的高板候选排序。

特征只取首板收盘及次日09:45可见的数据；未来四连板仅是训练标签。
10:00开盘低于涨停价才计入可执行候选。模型和交易阈值只在2025年确定。
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from coreClient.data_provider import get_day


ROOT = Path("outputs/fifteen_min_early_high_board_model")
SOURCE = {
    2025: (Path("outputs/fifteen_min_early_high_board_2025/events.csv"),
           Path("outputs/fifteen_min_core_reactivation_2026/db_theme_graph_2025.duckdb")),
    2026: (Path("outputs/fifteen_min_early_high_board_2026/events.csv"),
           Path("offlineDataManager/data/db_theme_graph.duckdb")),
}
FEATURES = [
    "prior_theme_width", "prior_theme_heat", "prior_theme_rank",
    "seal_minutes", "seal_ratio", "log_float", "log_board_amount",
    "close_to_ma20", "close_to_ma60", "close_to_prev20_high",
    "prior20_range", "return_5", "return_20", "volume_ratio_20",
    "first15_close_pct", "first15_open_gap_pct", "first15_body_pct",
    "first15_volume_ratio", "peer_observed", "peer_positive_share",
    "peer_at_least_2pct",
]


def _seal_minutes(value: object) -> float:
    if pd.isna(value):
        return np.nan
    parts = str(value).split(":")
    if len(parts) < 2:
        return np.nan
    return int(parts[0]) * 60 + int(parts[1])


def _daily_features(codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    history_start = (pd.Timestamp(start_date) - pd.DateOffset(months=6)).strftime("%Y%m%d")
    day = get_day(ts_codes=codes, start_date=history_start, end_date=end_date,
                  qfq=True, source="database_only")
    day.trade_date = day.trade_date.astype(str)
    day = day.sort_values(["ts_code", "trade_date"]).copy()
    g = day.groupby("ts_code", sort=False)
    day["ma20"] = g.close.transform(lambda x: x.rolling(20, min_periods=20).mean())
    day["ma60"] = g.close.transform(lambda x: x.rolling(60, min_periods=60).mean())
    day["prev20_high"] = g.high.transform(
        lambda x: x.shift().rolling(20, min_periods=20).max())
    day["prev20_low"] = g.low.transform(
        lambda x: x.shift().rolling(20, min_periods=20).min())
    day["prev20_vol"] = g.vol.transform(
        lambda x: x.shift().rolling(20, min_periods=20).median())
    day["close_to_ma20"] = day.close / day.ma20 - 1
    day["close_to_ma60"] = day.close / day.ma60 - 1
    day["close_to_prev20_high"] = day.close / day.prev20_high - 1
    day["prior20_range"] = day.prev20_high / day.prev20_low - 1
    day["return_5"] = day.close / g.close.shift(5) - 1
    day["return_20"] = day.close / g.close.shift(20) - 1
    day["volume_ratio_20"] = day.vol / day.prev20_vol
    keep = ["ts_code", "trade_date", "close_to_ma20", "close_to_ma60",
            "close_to_prev20_high", "prior20_range", "return_5", "return_20",
            "volume_ratio_20"]
    return day[keep].rename(columns={"trade_date": "dminus1"})


def _graph_features(path: Path, start_date: str, end_date: str) -> pd.DataFrame:
    con = duckdb.connect(str(path), read_only=True)
    try:
        kpl = con.execute(
            """SELECT trade_date AS dminus1,ts_code,lu_time,limit_order,free_float,
                      amount AS board_amount FROM fact_limit_event
               WHERE tag='涨停' AND status_raw='首板'
                 AND trade_date BETWEEN ? AND ?""", [start_date, end_date]).df()
    finally:
        con.close()
    kpl["seal_minutes"] = kpl.lu_time.map(_seal_minutes)
    kpl["seal_ratio"] = kpl.limit_order / kpl.free_float.where(kpl.free_float > 0)
    kpl["log_float"] = np.log10(kpl.free_float.where(kpl.free_float > 0))
    kpl["log_board_amount"] = np.log10(kpl.board_amount.where(kpl.board_amount > 0))
    return kpl.drop(columns=["lu_time", "limit_order", "free_float", "board_amount"])


def _load(year: int) -> pd.DataFrame:
    source, graph = SOURCE[year]
    events = pd.read_csv(source, dtype={"dminus1": str, "d0": str, "ts_code": str})
    events = events[events.quality_ok & events.buyable & events.next_rule_net.notna()].copy()
    events = events.merge(_graph_features(graph, events.dminus1.min(), events.dminus1.max()),
                          on=["dminus1", "ts_code"], how="left", validate="one_to_one")
    day = _daily_features(sorted(events.ts_code.unique()), events.dminus1.min(),
                          events.dminus1.max())
    events = events.merge(day, on=["dminus1", "ts_code"], how="left",
                          validate="many_to_one")
    events[FEATURES] = events[FEATURES].replace([np.inf, -np.inf], np.nan)
    return events


def _stats(frame: pd.DataFrame) -> dict:
    r = frame.next_rule_net
    return {"n": len(frame), "four_board": int(frame.future_four_board.sum()),
            "four_board_pct": round(100 * frame.future_four_board.mean(), 3) if len(frame) else None,
            "net_mean_pct": round(100 * r.mean(), 3) if len(r) else None,
            "net_median_pct": round(100 * r.median(), 3) if len(r) else None,
            "positive_pct": round(100 * r.gt(0).mean(), 2) if len(r) else None,
            "worst_pct": round(100 * r.min(), 3) if len(r) else None,
            "best_pct": round(100 * r.max(), 3) if len(r) else None,
            "blocked_exit": int(frame.next_rule_blocked_bars.gt(0).sum())}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    train = _load(2025)
    test = _load(2026)
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(C=0.1, class_weight="balanced",
                                             max_iter=1000))
    model.fit(train[FEATURES], train.future_four_board.astype(int))
    train["score"] = model.predict_proba(train[FEATURES])[:, 1]
    test["score"] = model.predict_proba(test[FEATURES])[:, 1]
    # 事前固定预算：2025可执行候选得分最高的0.2%，同日最多一只。
    threshold = float(train.score.quantile(.998))
    selected_train = train[train.score.ge(threshold)].sort_values(
        ["d0", "score", "ts_code"], ascending=[True, False, True]).drop_duplicates("d0")
    selected_test = test[test.score.ge(threshold)].sort_values(
        ["d0", "score", "ts_code"], ascending=[True, False, True]).drop_duplicates("d0")
    report = {
        "method": "2025训练L2逻辑回归，2025得分99.8百分位固定阈值，2026一次性复核；09:45特征，10:00涨停以下开盘才买",
        "features": FEATURES,
        "score_threshold": threshold,
        "2025_train": {"all_buyable": _stats(train), "selected": _stats(selected_train)},
        "2026_validation": {"all_buyable": _stats(test), "selected": _stats(selected_test)},
        "caveat": "2026此前已有探索性观察；这是时间外推检查，不能称完全盲法验证。10:00开盘成交、次日止盈止损仍是15分钟代理。",
    }
    train.to_csv(ROOT / "train_2025.csv", index=False)
    test.to_csv(ROOT / "validation_2026.csv", index=False)
    selected_test.to_csv(ROOT / "selected_2026.csv", index=False)
    (ROOT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
