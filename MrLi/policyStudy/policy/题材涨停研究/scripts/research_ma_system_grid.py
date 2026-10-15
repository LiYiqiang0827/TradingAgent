"""Predeclared coverage grid for known examples, never a profitability optimizer.

Keep v1 inputs immutable. The observed-outgoing-price interpretation is a
separate semantic experiment, not a silent relaxation of the submitted rule.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from itertools import product
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ma_system_pullback import Rules, daily_facts, daily_evidence, flat_projection, intraday_facts, scan_entries
from review_ma_system_pullback import ROOT, CASES, write_json, digest


GRID = dict(daily_order_mode=["up_close_days", "current"],
            para_ma30up_period=[5, 10, 20], cross_window=[4, 8, 16],
            forecast_bars=[2, 4, 6, 8], retest_wait=[8, 16, 32, 64],
            retest_tolerance_pct=[1., 2.],
            deduction_mode=["strict", "end_lower", "observed_falling"])


def projection_flags(frame, horizon, mode):
    close = frame.p_close.to_numpy()
    flags = []
    for i in range(len(frame)):
        p = flat_projection(close[max(0, i - 29):i + 1], horizon,
                            mode if mode != "observed_falling" else "end_lower")
        if mode == "observed_falling":
            # Outgoing prices of the last three already completed MA updates.
            falling = all(i >= n + 2 and np.isfinite(close[i - n - 2:i - n + 1]).all()
                          and np.all(np.diff(close[i - n - 2:i - n + 1]) <= 0)
                          and np.any(np.diff(close[i - n - 2:i - n + 1]) < 0)
                          for n in (10, 20, 30))
            flags.append(bool(p["known"] and p["rising_all"] and
                              p["first_bull_bar"] is not None and falling))
        else:
            flags.append(p["ready"])
    return np.asarray(flags, dtype=bool)


def fast_scan(frame, daily, rules, projection, cache):
    """Array implementation equivalent to v1's scanner (bear_bars=1 only)."""
    assert rules.bear_bars == 1
    a = {key: frame[key].to_numpy() for key in
         ("valid", "bear", "ma5", "ma10", "ma20", "ma30", "ma120", "p_low", "p_close",
          "price_above120", "watch_active", "selected_at", "cross5_10", "cross5_20", "cross5_30")}
    stamps = frame.datetime.to_numpy()
    episode = armed = None
    crosses = {}
    prior_bear = False
    found = []
    counts = dict(stage3=0, retest=0, daily_confirm=0, technical_candidate=0)
    tol = rules.retest_tolerance_pct / 100
    for i in range(len(frame)):
        valid = a["valid"][i] and np.isfinite(a["ma120"][i])
        if not valid:
            episode = armed = None
            crosses = {}
            prior_bear = False
        elif a["bear"][i] and not prior_bear:
            episode, armed, crosses = i, None, {}
        if episode is not None:
            for n in (10, 20, 30):
                if a[f"cross5_{n}"][i]:
                    crosses[n] = i
        compact = (len(crosses) == 3 and
                   max(crosses.values()) - min(crosses.values()) < rules.cross_window and
                   i - min(crosses.values()) < rules.cross_window and
                   a["ma5"][i] > max(a["ma10"][i], a["ma20"][i], a["ma30"][i]))
        stage3 = bool(episode is not None and compact and a["price_above120"][i] and projection[i])
        in_month = bool(frame.attrs["month_mask"][i])
        counts["stage3"] += int(stage3 and in_month)
        if stage3 and armed is None:
            armed = i
        if armed is not None and (i - armed > rules.retest_wait or not a["price_above120"][i]):
            episode = armed = None
            crosses = {}
        retest = bool(armed is not None and i > armed and
                      a["ma30"][i] * (1 - tol) <= a["p_low"][i] <= a["ma30"][i] * (1 + tol)
                      and a["p_close"][i] >= a["ma30"][i])
        counts["retest"] += int(retest and in_month)
        if retest:
            key = (episode, i)
            if key not in cache:
                cache[key] = daily_evidence(daily, frame.iloc[:i + 1], pd.Timestamp(stamps[episode]), rules)
            e = cache[key]
            if e["confirm"]:
                selected = a["selected_at"][episode]
                daily_ok = bool(a["watch_active"][i] and pd.notna(selected))
                counts["daily_confirm"] += int(in_month)
                counts["technical_candidate"] += int(in_month and daily_ok)
                if in_month:
                    found.append(dict(time=str(pd.Timestamp(stamps[i])),
                                      episode_start=str(pd.Timestamp(stamps[episode])),
                                      armed_at=str(pd.Timestamp(stamps[armed])),
                                      selected_at=selected, daily_gate=daily_ok,
                                      technical_candidate=daily_ok,
                                      formal_entry="unknown_theme_evidence", daily_evidence=e))
                episode = armed = None
                crosses = {}
        prior_bear = bool(a["bear"][i]) if a["valid"][i] else False
    return counts, found


def run(source, out):
    out.mkdir(parents=True, exist_ok=False)
    configs = [dict(zip(GRID, values)) for values in product(*GRID.values())]
    write_json(out / "GRID.json", dict(grid=GRID, configurations=len(configs),
                                      purpose="case coverage only; no returns used for selection",
                                      known_examples=True, daily_near_pct=3., require_daily_reversal=True,
                                      bear_bars=1, invalidation="close below MA120 cancels armed setup"))
    rows, all_candidates, validations = [], [], []
    for name, code, month in CASES:
        folder = source / f"{code}_{month}"
        raw, adj, cal, minute = [pd.read_parquet(folder / f"{n}.parquet")
                                for n in ("daily", "factors", "calendar", "fifteen")]
        calendar = sorted(cal.cal_date.astype(str).unique())
        minute_calendar = [d for d in calendar if d >= pd.to_datetime(minute.datetime).min().strftime("%Y%m%d")]
        base = daily_facts(raw, adj, calendar)
        f = intraday_facts(minute, minute_calendar, base)
        mask = f.trade_date.str.startswith(month).to_numpy()
        f.attrs["month_mask"] = mask
        projections = {(h, m): projection_flags(f, h, m) for h, m in
                       product(GRID["forecast_bars"], GRID["deduction_mode"])}
        daily_cache = {}
        evidence_cache = {}  # MA/candles don't depend on qualification interpretation.
        for ci, conf in enumerate(configs):
            legal = {k: v for k, v in conf.items() if k != "deduction_mode"}
            rules = replace(Rules(), **legal,
                            deduction_mode=conf["deduction_mode"] if conf["deduction_mode"] != "observed_falling" else "end_lower")
            key = (rules.daily_order_mode, rules.para_ma30up_period)
            if key not in daily_cache:
                d = daily_facts(raw, adj, calendar, rules)
                lookup = d.set_index("trade_date")
                daily_cache[key] = (d, f.daily_as_of.map(lookup.watch_active).eq(True).to_numpy(),
                                    f.daily_as_of.map(lookup.selected_at).to_numpy())
            d, watch, selected = daily_cache[key]
            f["watch_active"], f["selected_at"] = watch, selected
            projection = projections[(rules.forecast_bars, conf["deduction_mode"])]
            counts, found = fast_scan(f, d, rules, projection, evidence_cache)
            rows.append(dict(case=f"{code}_{month}", name=name, config_id=f"G{ci:04}", **conf,
                             active_watch_bars=int((watch & mask).sum()), **counts))
            all_candidates.extend(dict(case=f"{code}_{month}", name=name,
                                       config_id=f"G{ci:04}", **c) for c in found)
            # Validate independent scanner implementation on spread-out literal configs.
            if ci in (0, 48, 192, 384, 864, 1200) and conf["deduction_mode"] != "observed_falling":
                f["projection_ready"] = projection
                di, slow = scan_entries(f, d, rules)
                expected = {key: int(di.loc[mask, key].sum()) for key in counts}
                assert counts == expected, (ci, counts, expected)
                slow = [c for c in slow if pd.Timestamp(c["time"]).strftime("%Y%m") == month]
                assert found == slow
                validations.append(dict(case=f"{code}_{month}", config_id=f"G{ci:04}", status="scanner_equivalent"))
        print(name, month, "grid complete", flush=True)
    pd.DataFrame(rows).to_csv(out / "coverage.csv", index=False)
    write_json(out / "candidates.json", all_candidates)
    write_json(out / "scanner_checks.json", validations)
    unique = {}
    for c in all_candidates:
        if c["technical_candidate"]:
            key = (c["case"], c["time"])
            record = unique.setdefault(key, dict(case=c["case"], name=c["name"], time=c["time"],
                                                 configs=[], evidence=c["daily_evidence"]))
            record["configs"].append(c["config_id"])
    write_json(out / "unique_technical_candidates.json", list(unique.values()))
    table = pd.DataFrame(rows)
    summary = table.groupby("config_id").agg(case_coverage=("technical_candidate", lambda x: int(x.gt(0).sum())),
                                             candidates=("technical_candidate", "sum"), retests=("retest", "sum")).reset_index()
    summary = summary.merge(table.drop_duplicates("config_id")[["config_id", *GRID]], on="config_id")
    summary.to_csv(out / "config_summary.csv", index=False)
    write_json(out / "MANIFEST.json", dict(source=str(source), grid=GRID, as_of="20261005",
                                          scanner_equivalence_checks=len(validations),
                                          input_hashes={str(p.relative_to(source)): digest(p) for p in source.glob("*/*.parquet")},
                                          code_sha256=digest(__file__),
                                          unique_technical_candidates=len(unique), formal_entries_validated=False))
    print(summary.sort_values(["case_coverage", "candidates"], ascending=False).head(12).to_string(index=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, default=ROOT / "outputs/ma_system_pullback_20261005_v1")
    p.add_argument("--output", type=Path, default=ROOT / "outputs/ma_system_pullback_20261005_v2/grid")
    args = p.parse_args()
    run(args.source, args.output)
