"""首板当天全市场09:35扫描的截至昨日题材身份与热度复核。"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from coreClient.data_provider import get_tradecal


EVENTS = Path("outputs/first_board_intraday_market_scan_2026/events.csv")
GRAPH = Path("offlineDataManager/data/db_theme_graph.duckdb")
OUT = Path("outputs/first_board_intraday_market_scan_2026")


def main() -> None:
    events = pd.read_csv(EVENTS, dtype={"d0": str, "ts_code": str})
    calendar = sorted(get_tradecal(start_date="20260501", end_date="20260924",
                                   source="database_only").cal_date.astype(str))
    index = {d: i for i, d in enumerate(calendar)}
    events["prior"] = events.d0.map(lambda d: calendar[index[d] - 1])
    events["older20"] = events.d0.map(lambda d: calendar[max(0, index[d] - 21)])
    con = duckdb.connect(str(GRAPH), read_only=True)
    try:
        history = con.execute(
            """SELECT e.ts_code,e.trade_date AS seen_date,r.theme_id
               FROM fact_limit_event e JOIN rel_limit_theme r USING(event_id)
               WHERE r.attribution_role='primary' AND e.tag='涨停'
                 AND e.trade_date BETWEEN '20260501' AND '20260917'""").df()
        daily = con.execute(
            """SELECT trade_date AS prior,theme_id,limit_up_count,heat_score
               FROM fact_theme_daily
               WHERE trade_date BETWEEN '20260501' AND '20260917'""").df()
    finally:
        con.close()
    links = events[["d0", "ts_code", "prior", "older20"]].merge(
        history, on="ts_code", how="left")
    links = links[links.seen_date.between(links.older20, links.prior)]
    links = links.merge(daily, on=["prior", "theme_id"], how="left")
    links["active"] = links.limit_up_count.ge(3) & links.heat_score.ge(50)
    known = links.groupby(["d0", "ts_code"]).agg(
        prior_active_theme=("active", "max"),
        prior_theme_heat_max=("heat_score", "max"),
        recent_primary_events=("seen_date", "size")).reset_index()
    events = events.merge(known, on=["d0", "ts_code"], how="left")
    events["prior_active_theme"] = events.prior_active_theme.eq(True)

    def stats(frame: pd.DataFrame) -> dict:
        scored = frame[frame.net.notna()]
        return {"n": len(frame), "d0_firstboard": int(frame.d0_kpl_firstboard.sum()),
                "future_four_board": int(frame.future_four_board.sum()),
                "scored": len(scored),
                "mean_net_pct": round(100 * scored.net.mean(), 3),
                "win_pct": round(100 * scored.net.gt(0).mean(), 2),
                "worst_pct": round(100 * scored.net.min(), 3)}

    report = {"all": stats(events),
              "known_theme_last20": stats(events[events.recent_primary_events.ge(1)]),
              "prior_active_theme": stats(events[events.prior_active_theme]),
              "rule": "同股最近20交易日已有KPL主归因题材，且该题材D-1至少3涨停、热度>=50；D0题材归因不进入特征"}
    OUT.mkdir(parents=True, exist_ok=True)
    events.to_csv(OUT / "prior_theme_context.csv", index=False)
    (OUT / "prior_theme_context.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
