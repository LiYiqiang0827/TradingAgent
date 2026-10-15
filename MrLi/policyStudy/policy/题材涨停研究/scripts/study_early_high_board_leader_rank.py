"""首板题材内先锋排名：比较全体与次日可买集合中的四板富集程度。"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd


SOURCE = {
    2025: ("outputs/fifteen_min_early_high_board_2025/events.csv",
           "outputs/fifteen_min_core_reactivation_2026/db_theme_graph_2025.duckdb"),
    2026: ("outputs/fifteen_min_early_high_board_2026/events.csv",
           "offlineDataManager/data/db_theme_graph.duckdb"),
}
OUT = Path("outputs/early_high_board_research")


def _stats(frame: pd.DataFrame) -> dict:
    early = frame.first_seal
    return {"n": len(frame), "four_board": int(frame.future_four_board.sum()),
            "early_seal_n": int(early.sum()),
            "early_seal_four_board_rate_pct": round(100 * frame.loc[
                early, "future_four_board"].mean(), 3),
            "other_four_board_rate_pct": round(100 * frame.loc[
                ~early, "future_four_board"].mean(), 3),
            "early_seal_net_pct": round(100 * frame.loc[
                early, "next_rule_net"].mean(), 3)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result = {}
    for year, (event_file, graph_file) in SOURCE.items():
        event = pd.read_csv(event_file, dtype={"dminus1": str, "d0": str})
        con = duckdb.connect(graph_file, read_only=True)
        try:
            kpl = con.execute(
                """SELECT trade_date AS dminus1,ts_code,lu_time,
                          limit_order/NULLIF(free_float,0) AS seal_ratio
                   FROM fact_limit_event
                   WHERE tag='涨停' AND status_raw='首板'""").df()
        finally:
            con.close()
        event = event.merge(kpl, on=["dminus1", "ts_code"], how="left",
                            validate="one_to_one")
        groups = event.groupby(["dminus1", "theme_id"])
        event["theme_firstboard_count"] = groups.ts_code.transform("size")
        event["first_seal"] = groups.lu_time.rank(method="first").eq(1)
        event["largest_order_float"] = groups.seal_ratio.rank(
            method="first", ascending=False).eq(1)
        multi = event[event.theme_firstboard_count.ge(2)]
        result[str(year)] = {"all_multi_stock_themes": _stats(multi),
                             "buyable_at_10": _stats(multi[multi.buyable.eq(True)])}
        event.to_csv(OUT / f"leader_rank_{year}.csv", index=False)
    (OUT / "leader_rank.json").write_text(json.dumps(result, ensure_ascii=False,
                                                       indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
