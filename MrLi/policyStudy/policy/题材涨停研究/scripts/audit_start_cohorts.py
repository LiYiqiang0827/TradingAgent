"""Compare broad-theme persistence with the original stock cohort's outcomes.

Uses ALL 221 prespecified initiation events, never just the six showcase cases.
This is exploratory outcome auditing, not prediction accuracy or tradable returns.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
from build_case_dossiers import clean, normalize, save

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "data/stage1_v1"
OUT = BASE / "data/theme_cases_v1"


def compound_price_returns(first, second):
    result = ((first.close/first.pre_close)*(second.close/second.pre_close)-1)*100
    return result.mask(result.abs()<1e-8,0.0)


def main():
    records = json.loads((SOURCE/"features.json").read_text())
    dates = [r["date"] for r in records]
    daily = normalize(pd.read_pickle(SOURCE/"cache/day.pkl"))
    daily = daily.set_index(["trade_date", "ts_code"]).sort_index()
    limits = normalize(pd.read_pickle(SOURCE/"cache/limits.pkl")).set_index(["trade_date", "ts_code"])
    starts = pd.read_csv(BASE/"data/theme_atlas_v1/all_initiation_events.csv", dtype={"date":str})
    all_rows = []
    conflicts = []
    for date, events in starts.groupby("date", sort=True):
        i = dates.index(date)
        next_date, second_date = dates[i+1:i+3]
        # This cache was requested with D's limit-up pool; no D+1 winners added.
        minute = pd.read_pickle(SOURCE/f"cache/minute_{next_date}.pkl")
        minute = minute.loc[minute.trade_date.astype(str).str.replace("-", "")==next_date]
        for event in events.to_dict("records"):
            theme = event["theme"]
            pool = [l["ts_code"] for l in records[i]["leaders"] if l["theme"] == theme]
            expected = len(pool)
            d1 = daily.reindex(pd.MultiIndex.from_product([[next_date], pool], names=daily.index.names)).droplevel(0)
            d2 = daily.reindex(pd.MultiIndex.from_product([[second_date], pool], names=daily.index.names)).droplevel(0)
            # Avoid manufacturing tiny losses from already-rounded pct_chg.
            cumulative = compound_price_returns(d1,d2)
            row = dict(event, cohort_n=expected, d1_covered=int(d1.pct_chg.notna().sum()),
                       d2_covered=int(cumulative.notna().sum()), d1_median_pct=d1.pct_chg.median(),
                       d2_cumulative_median_pct=cumulative.median(),
                       d1_market_median_pct=daily.loc[next_date].pct_chg.median())
            next_all = {l["ts_code"]:l["theme"] for l in records[i+1]["leaders"]}
            next_theme = {s for s,t in next_all.items() if t==theme}
            up_prices = limits.reindex(pd.MultiIndex.from_product([[next_date],pool],names=limits.index.names)).droplevel(0).up_limit
            price_up = set(d1.index[(d1.close-up_prices).abs()<.005])
            kpl_up = set(pool)&set(next_all)
            for code in sorted(price_up^kpl_up):
                conflicts.append(dict(event_date=date,theme=theme,observation_date=next_date,ts_code=code,
                                      kpl_limit=code in kpl_up,price_limit=code in price_up,
                                      close=d1.loc[code,"close"],up_limit=up_prices.loc[code]))
            row.update(d1_original_pool_limit_count=len(set(pool)&set(next_all)),
                       d1_price_at_limit_count=len(price_up),d1_limit_status_conflicts=len(price_up^kpl_up),
                       d1_same_theme_retained_count=len(set(pool)&next_theme),
                       d1_new_primary_count=len(next_theme-set(pool)),
                       d1_same_theme_width=len(next_theme))
            for cutoff, prefix in [("09:45:00", "m0945"), ("10:30:00", "m1030"), ("14:00:00", "m1400")]:
                m = minute.loc[minute.ts_code.isin(pool) & minute.datetime.str.endswith(cutoff)].copy()
                assert not m.duplicated("ts_code").any()
                m = m.set_index("ts_code").join(d1[["pre_close"]])
                m = m.loc[(m.price>0)&(m.pre_close>0)&np.isfinite(m.price)]
                ret = (m.price/m.pre_close-1)*100
                row[prefix+"_covered"] = int(ret.notna().sum())
                row[prefix+"_median_pct"] = ret.median()
                row[prefix+"_positive_count"] = int((ret > 0).sum())
            all_rows.append(row)
    a = pd.DataFrame(all_rows)
    assert len(a)==221 and not a.duplicated(["date","theme"]).any()
    a.to_csv(OUT/"all_221_cohort_outcomes.csv", index=False, encoding="utf-8-sig")
    save(OUT/"limit_status_conflicts.json", conflicts)
    groups = []
    for formed, g in a.groupby("formed"):
        complete = g.loc[(g.d1_covered==g.cohort_n)&(g.d2_covered==g.cohort_n)]
        groups.append({"broad_theme_formed":bool(formed),"events":len(g),"complete_daily":len(complete),
                       "d1_negative_median_count":int((complete.d1_median_pct<0).sum()),
                       "d2_negative_cumulative_median_count":int((complete.d2_cumulative_median_pct<0).sum())})
    # A diagnostic, NOT a signal: positive at 10:30, negative at the close.
    complete = a.loc[(a.m1030_covered==a.cohort_n)&(a.d1_covered==a.cohort_n)]
    morning_positive = complete.loc[complete.m1030_median_pct>0]
    reversals = morning_positive.loc[morning_positive.d1_median_pct<0]
    result = {"scope":"all prespecified historical initiation events; no causal claim or strategy P&L",
              "groups":groups,"minute_complete_events":len(complete),"m1030_positive_events":len(morning_positive),
              "m1030_positive_close_negative_events":len(reversals),
              "limit_status_conflict_records":len(conflicts),
              "incomplete_daily_events":a.loc[(a.d1_covered<a.cohort_n)|(a.d2_covered<a.cohort_n),["date","theme","cohort_n","d1_covered","d2_covered"]].to_dict("records")}
    save(OUT/"cohort_outcome_audit.json",result)
    print(json.dumps(clean(result),ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
