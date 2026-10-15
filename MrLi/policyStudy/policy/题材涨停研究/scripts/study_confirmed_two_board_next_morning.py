"""D0 confirmed second board -> D+1 09:35 observation, 09:36 entry.

The prior evening's KPL two-board and D0 close are usable at D+1.  D+1
close and D+2 minute bars are outcomes only.  This measures the price of
waiting for confirmation without imputing a D0 09:36 fill retroactively.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coreClient.data_provider import get_kpl_list, get_oneMin, get_stk_limit, get_tradecal
from study_early_high_board_r_paths import _simulate


def run(source_path: Path, label_end: str) -> tuple[pd.DataFrame, dict]:
    source = pd.read_csv(source_path, dtype={"d0": str, "ts_code": str})
    source = source[source.quality_ok.eq(True) & source.d0_closed_limit.eq(True)].copy()
    source = source.drop_duplicates(["d0", "ts_code"])
    start = str(source.d0.min())
    cal = sorted(get_tradecal(start_date=start, end_date=label_end,
                              source="database_only").cal_date.astype(str))
    plus1 = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    plus2 = {cal[i]: cal[i + 2] for i in range(len(cal) - 2)}
    kpl = get_kpl_list(start_date=start, end_date=str(source.d0.max()),
                       tags=["涨停"], source="database_only")
    kpl.trade_date = kpl.trade_date.astype(str)
    confirmed = {(r.trade_date, r.ts_code) for r in kpl.itertuples()
                 if r.status == "2连板"}
    limits = get_stk_limit(start_date=start, end_date=label_end,
                           source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    limit_map = {(r.trade_date, r.ts_code): (float(r.up_limit), float(r.down_limit))
                 for r in limits.itertuples()
                 if pd.notna(r.up_limit) and pd.notna(r.down_limit)}

    rows = []
    for d0, group in source.groupby("d0", sort=True):
        d1, d2 = plus1.get(d0), plus2.get(d0)
        group = group[group.ts_code.map(lambda code: (d0, code) in confirmed)]
        if group.empty or not d1:
            continue
        morning = get_oneMin(ts_codes=sorted(group.ts_code.unique()), trade_date=d1)
        parts = {code: x.sort_values("datetime") for code, x in morning.groupby("ts_code")}
        buys = []
        for r in group.itertuples():
            bars = parts.get(r.ts_code, pd.DataFrame())
            item = {"d0": d0, "d1": d1, "d2": d2, "ts_code": r.ts_code,
                    "name": r.name, "theme_name": r.theme_name,
                    "prior_theme_heat": r.prior_theme_heat,
                    "future_four_board": bool(r.future_four_board),
                    "buyable": False, "full_d1": len(bars) == 240}
            band = limit_map.get((d1, r.ts_code))
            if len(bars) != 240 or band is None:
                rows.append(item)
                continue
            times = [t.strftime("%H:%M") for t in bars.iloc[:6].datetime]
            if times != ["09:31", "09:32", "09:33", "09:34", "09:35", "09:36"]:
                rows.append(item)
                continue
            up, _ = band
            first, entry = bars.iloc[:5], bars.iloc[5]
            item.update({"sealed_at_935": bool(first.iloc[-1].close >= up - .005),
                         "d1_early_pct": 100 * (first.iloc[-1].close / bars.iloc[0].open - 1),
                         "d1_closed_limit": bool(bars.iloc[-1].close >= up - .005)})
            buyable = bool(entry.vol > 0 and 0 < entry.open < up - .005 and
                           entry.open <= first.iloc[-1].close * 1.03)
            item["buyable"] = buyable
            if buyable:
                item["entry_price"] = float(entry.open)
                buys.append(item)
            rows.append(item)
        if not buys or not d2:
            continue
        outcome = get_oneMin(ts_codes=[b["ts_code"] for b in buys], trade_date=d2)
        future_parts = {code: x.sort_values("datetime") for code, x in outcome.groupby("ts_code")}
        for item in buys:
            _, down = limit_map.get((d2, item["ts_code"]), (None, float("nan")))
            for take, tag in [(.03, "r1"), (.06, "r2"), (.09, "r3")]:
                result = _simulate(future_parts.get(item["ts_code"], pd.DataFrame()),
                                   item["entry_price"], down, take)
                item.update({f"{tag}_{key}": value for key, value in result.items()})
    frame = pd.DataFrame(rows)
    buy = frame[frame.buyable.eq(True)]
    audit = {"source": str(source_path), "d0_closed_limit_candidates": len(source),
             "d0_kpl_confirmed_two_board": len(frame),
             "sealed_at_d1_935": int(frame.sealed_at_935.eq(True).sum()),
             "buyable_d1_936": len(buy),
             "buyable_future_four_board": int(buy.future_four_board.sum()),
             "d1_close_board_in_buyable_outcome_only": int(buy.d1_closed_limit.eq(True).sum()),
             "targets": {},
             "lookahead_policy": "D0 KPL and closing board known before D1; D1 09:35 bars only for selection; D1 close and D2 bars are outcomes"}
    for tag in ("r1", "r2", "r3"):
        scored = buy[buy[f"{tag}_net"].notna()]
        x = scored[f"{tag}_net"]
        audit["targets"][tag] = {
            "scored": len(scored), "unresolved": len(buy) - len(scored),
            "mean_net_pct": float(100 * x.mean()),
            "median_net_pct": float(100 * x.median()),
            "win_pct": float(100 * x.gt(0).mean()),
            "worst_pct": float(100 * x.min()),
            "take_count": int(scored[f"{tag}_reason"].eq("take").sum()),
            "cut_count": int(scored[f"{tag}_reason"].eq("cut").sum()),
        }
    return frame, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--label-end", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    frame, audit = run(Path(args.source), args.label_end)
    frame.to_csv(out / "events.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
