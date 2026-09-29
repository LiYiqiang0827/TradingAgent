"""Predeclared EOD invalidation comparisons on the same causal entries.

An entry-day closing break is known before the next eligible open.  When
confirmed, its fill matches the sticky pending-exit replay; otherwise the
baseline 15m trailing policy continues. This never changes entry selection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_fifteenMin, get_theme_daily
from study_fifteen_min_core_reactivation import _isolated_store


def metrics(frame: pd.DataFrame, col: str) -> dict:
    scored = frame[frame[col].notna()].copy()
    value = scored[col]
    sums = scored.groupby("entry_date")[col].agg(["sum", "count"]).to_numpy()
    rng = np.random.default_rng(20260929)
    draws = sums[rng.integers(0, len(sums), (4000, len(sums)))].sum(axis=1)
    return {"scored": len(scored), "unresolved": len(frame) - len(scored),
            "mean_net_pct": 100 * float(value.mean()), "worst_pct": 100 * float(value.min()),
            "win_pct": 100 * float(value.gt(0).mean()),
            "date_block_95_pct": (100 * np.quantile(draws[:, 0] / draws[:, 1], [.025, .975])).tolist()}


def run(source: Path, graph_db: Path | None, out: Path) -> dict:
    frame = pd.read_csv(source, dtype={"ts_code": str})
    # The underlying candidate audit retains unfilled entry plans. No outcome filter here.
    frame = frame[frame.old_buyable.eq(True)].copy()
    frame["entry_date"] = pd.to_datetime(frame.old_entry_execution_at).dt.strftime("%Y%m%d")
    first, last = frame.entry_date.min(), frame.entry_date.max()
    daily = (_isolated_store(graph_db).query_theme_daily(start_date=first, end_date=last)
             if graph_db else get_theme_daily(start_date=first, end_date=last))
    daily.trade_date = daily.trade_date.astype(str)
    daily = daily[(daily.level1_name != "ST与次新") &
                  ~daily.canonical_name.isin(["ST板块", "ST摘帽", "次新股"])].copy()
    daily["eligible"] = daily.limit_up_count.ge(3) & daily.heat_score.ge(50)
    daily = daily.sort_values(["trade_date", "eligible", "heat_score", "theme_id"],
                              ascending=[True, False, False, True])
    daily["rank"] = daily[daily.eligible].groupby("trade_date").cumcount().add(1).reindex(daily.index)
    themes = {(r.trade_date, r.theme_id): {"heat": float(r.heat_score),
               "width": int(r.limit_up_count), "strong": bool(r.eligible and r.rank <= 3)}
              for r in daily.itertuples()}
    bars = get_fifteenMin(ts_codes=sorted(frame.ts_code.unique()), start_date=first, end_date=last)
    bars.trade_date = bars.trade_date.astype(str)
    eod = bars[bars.datetime.dt.strftime("%H:%M").eq("15:00")].set_index(["trade_date", "ts_code"])
    rows = []
    for row in frame.to_dict("records"):
        key = (row["entry_date"], row["ts_code"])
        theme = themes.get((row["entry_date"], row["theme_id"]))
        row.update({"eod_price_known": key in eod.index, "eod_theme_known": theme is not None,
                    "eod_failed": False, "eod_theme_weak": False})
        if key in eod.index:
            factor = float(eod.loc[key, "adj_factor"])
            row["eod_price_known"] = bool(np.isfinite(factor) and abs(factor / row["signal_factor"] - 1) <= 1e-5)
            if row["eod_price_known"]:
                close = float(eod.loc[key, "close"])
                row["eod_close"] = close
                row["eod_failed"] = close <= max(row["old_entry_open"] * .97, row["neckline"] * .99)
        if theme is not None:
            row.update({"eod_theme_weak": not theme["strong"], "eod_theme_heat": theme["heat"],
                        "eod_theme_width": theme["width"]})
        if row["eod_failed"]:
            assert pd.notna(row["carry_entry_day_invalidation"]), "EOD failure must have an entry-day decision"
        row["close_confirmed_net"] = row["carry_net"] if row["eod_failed"] else row["old_net"]
        row["theme_confirmed_net"] = row["carry_net"] if (
            row["eod_failed"] and row["eod_theme_weak"]) else row["old_net"]
        rows.append(row)
    result = pd.DataFrame(rows)
    audit = {"source": str(source), "entry_count": len(result),
             "eod_failed": int(result.eod_failed.sum()),
             "eod_failed_and_weak_theme": int((result.eod_failed & result.eod_theme_weak).sum()),
             "unknown_eod_price": int((~result.eod_price_known).sum()),
             "unknown_eod_theme": int((~result.eod_theme_known).sum()),
             "unknown_policy": "no additional EOD exit without evidence; continue baseline; report missing counts",
             "rule_status": "exploratory comparison; same entries, no threshold optimization",
             "results": {col: metrics(result, col) for col in
                         ["old_net", "carry_net", "close_confirmed_net", "theme_confirmed_net"]}}
    result.to_csv(out / "trades.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--graph-db", type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    print(json.dumps(run(args.source, args.graph_db, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
