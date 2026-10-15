"""Freeze a pre-2026 theme-active daily-trend observation pool.

Selection uses only price/factor/KPL records ending 2025-12-31. The subsequent
2026 files are downloaded from LOCAL providers only AFTER the membership CSV
and selection manifest are frozen. This script never reads any trade outcome.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil

import numpy as np
import pandas as pd

from coreClient import data_provider as dp
from trend_pullback_features import universe_snapshot
from trend_pullback_theme_context import prepare_kpl, heat_score, board_height, prepare_news


ROOT = Path(__file__).resolve().parents[5]
AS_OF = "20251231"
DEFAULT = ROOT / "outputs/trend_pullback_theme_v2"
REUSE = ROOT / "outputs/trend_pullback_multiscale_v1/inputs"
WEIGHTS = dict(theme_heat=.55, theme_width=.20, daily_trend=.15, liquidity=.10)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+"\n")


def dates(x):
    x = x.copy()
    if "trade_date" in x:
        x["trade_date"] = x.trade_date.astype(str)
    if "cal_date" in x:
        x["cal_date"] = x.cal_date.astype(str)
    return x


def persist(path, loader):
    """A resumable local snapshot: existing input is reused, never refreshed."""
    path = Path(path)
    if path.exists():
        return pd.read_parquet(path)
    value = loader()
    value.to_parquet(path, index=False)
    print("saved", path.name, len(value), flush=True)
    return value


def select_pool(daily, adj, calendar, basic, kpl, *, as_of=AS_OF, count=20):
    """Pure prefix selector; return selected, full daily universe, scored pool.

    The daily gates are exactly the earlier ``universe_snapshot`` gates. Its
    random draw is discarded; only its auditable full-universe features/gates
    are reused. Ranking is new and declared before reading future prices.
    """
    calendar = sorted(set(str(day) for day in calendar if str(day) <= as_of))
    if as_of not in calendar:
        raise ValueError("Selection as_of must be an observed trading session")
    # Keep daily inputs entirely before the cutoff, not merely the final row.
    daily = dates(daily); adj = dates(adj); kpl = dates(kpl)
    daily = daily[daily.trade_date.le(as_of)].copy()
    adj = adj[adj.trade_date.le(as_of)].copy()
    kpl = kpl[kpl.trade_date.le(as_of)].copy()
    _, universe = universe_snapshot(daily, adj, calendar, basic,
                                    as_of=as_of, seed=20261003, count=20)
    limitups, market_available = prepare_kpl(kpl)
    prior60 = calendar[-60:]; recent20 = calendar[-20:]
    recent25 = calendar[-25:]
    membership = limitups[limitups.trade_date.isin(prior60)].copy()
    window = limitups[limitups.trade_date.isin(recent25)].copy()
    labels = sorted(membership.lu_desc.unique())
    daily_groups = {(day, theme): group for (day, theme), group in
                    window.groupby(["trade_date", "lu_desc"], sort=False)}
    theme_rows = []; daily_theme_rows = []
    for label in labels:
        history = []
        for day in recent25:
            group = daily_groups.get((day, label))
            known = day in market_available
            heights = [board_height(status) for status in group.status] if group is not None else []
            width = int(group.ts_code.nunique()) if group is not None else 0
            # Multiple identical primary labels/status rows must not inflate
            # ladder heat. Retain one literal stock per theme/day.
            if group is not None:
                unique = group.assign(_height=group.status.map(board_height)).sort_values(
                    ["_height", "status"]).drop_duplicates("ts_code", keep="last")
                heights = [board_height(status) for status in unique.status]
            current = dict(trade_date=day, theme=label, known=known,
                           width=width if known else None, height=max(heights, default=0) if known else None)
            history.append(current)
            active = sum(item["known"] and (item["width"] or 0) > 0 for item in history[-5:])
            current["heat"] = heat_score(heights, active) if known else None
            if day in recent20:
                daily_theme_rows.append(current)
        observations = [item for item in history if item["trade_date"] in recent20 and item["known"]]
        theme_rows.append(dict(theme=label,
                               heat_mean20=float(np.mean([item["heat"] for item in observations])) if observations else np.nan,
                               width_mean20=float(np.mean([item["width"] for item in observations])) if observations else np.nan,
                               heat_max20=max((item["heat"] for item in observations), default=np.nan),
                               width_max20=max((item["width"] for item in observations), default=np.nan),
                               active_sessions20=sum((item["width"] or 0) > 0 for item in observations),
                               observed_sessions20=len(observations)))
    themes = pd.DataFrame(theme_rows).set_index("theme")
    pool = universe[universe.eligible].copy()
    pool["historical_limitup60"] = pool.ts_code.isin(set(membership.ts_code))
    daily_eligible = len(pool)
    pool = pool[pool.historical_limitup60].copy()
    records = []
    for row in pool.itertuples():
        matches = membership[membership.ts_code.eq(row.ts_code)]
        literal_labels = sorted(matches.lu_desc.unique())
        ranking = sorted(literal_labels,
                         key=lambda label: (-themes.loc[label, "heat_mean20"],
                                            -themes.loc[label, "width_mean20"], label))
        theme = ranking[0]; facts = themes.loc[theme].to_dict()
        records.append(dict(ts_code=row.ts_code, ranking_theme=theme,
                            literal_themes60=json.dumps(literal_labels, ensure_ascii=False),
                            latest_kpl_date=matches.trade_date.max(),
                            last60_limitup_days=matches.trade_date.nunique(),
                            theme_heat_mean20=facts["heat_mean20"],
                            theme_width_mean20=facts["width_mean20"],
                            theme_heat_max20=facts["heat_max20"],
                            theme_width_max20=facts["width_max20"],
                            theme_active_sessions20=facts["active_sessions20"],
                            kpl_observed_sessions20=facts["observed_sessions20"]))
    pool = pool.merge(pd.DataFrame(records), on="ts_code", validate="one_to_one")
    pool["daily_trend_days"] = pool[["rise20", "rise30", "rise60"]].max(axis=1)
    columns = {"theme_heat": "theme_heat_mean20", "theme_width": "theme_width_mean20",
               "daily_trend": "daily_trend_days", "liquidity": "amount20"}
    for key, column in columns.items():
        pool[f"{key}_percentile"] = pool[column].rank(method="average", pct=True)
    pool["selection_score"] = sum(100*WEIGHTS[key]*pool[f"{key}_percentile"] for key in WEIGHTS)
    pool = pool.sort_values(["selection_score", "theme_heat_mean20", "theme_width_mean20", "amount20", "ts_code"],
                            ascending=[False, False, False, False, True]).reset_index(drop=True)
    pool["rank_before_industry_cap"] = np.arange(1, len(pool)+1)
    selected_indexes = []; industry_counts = Counter(); skip_reasons = {}
    for index, row in pool.iterrows():
        industry = str(row.industry)
        if len(selected_indexes) >= count:
            skip_reasons[index] = "after_fixed_count"
            continue
        if industry_counts[industry] >= 3:
            skip_reasons[index] = "industry_cap3"
            continue
        selected_indexes.append(index); industry_counts[industry] += 1
        skip_reasons[index] = "selected"
    pool["selection_outcome"] = [skip_reasons[i] for i in pool.index]
    selected = pool.loc[selected_indexes].copy().reset_index(drop=True)
    selected["observation_rank"] = np.arange(1, len(selected)+1)
    selected["selection_as_of"] = as_of
    if len(selected) < 10:
        raise ValueError(f"Only {len(selected)} pre-cutoff observations; do not relax using 2026 outcomes")
    statistics = dict(all_market_on_asof=len(universe), unchanged_daily_eligible=daily_eligible,
                      daily_eligible_with_limitup60=len(pool), selected=len(selected),
                      last60_market_sessions=prior60, last20_market_sessions=recent20,
                      kpl_missing_sessions20=sorted(set(recent20)-market_available),
                      industry_counts=dict(industry_counts),
                      ranking_weights=WEIGHTS,
                      selection_outcomes=pool.selection_outcome.value_counts().to_dict())
    return selected, universe, pool, pd.DataFrame(daily_theme_rows), membership, statistics


def factual_notes(selected, news, as_of=AS_OF):
    """Attach original dated factual title matches; never change pool ranking."""
    cutoff = pd.Timestamp(as_of)+pd.Timedelta(days=1)
    x = news.copy()
    stamp = pd.to_datetime(x.datetime, errors="coerce")
    x = x[stamp.ge(cutoff-pd.Timedelta(days=7)) & stamp.lt(cutoff)]
    x = prepare_news(x)
    notes = []
    for row in selected.itertuples():
        literal = json.loads(row.literal_themes60)
        terms = [str(row.name), row.ts_code[:6]]+literal
        pattern = "|".join(re.escape(term) for term in terms if len(term) >= 2)
        matched = x[x.title.str.contains(pattern, regex=True)].sort_values("source_time", ascending=False).head(3)
        for item in matched.to_dict("records"):
            notes.append(dict(ts_code=row.ts_code, name=row.name, **item,
                              causal_attribution=False, usage="dated_original_fact_lead_only"))
    return pd.DataFrame(notes)


def main(out=DEFAULT, reuse=REUSE):
    out = Path(out); reuse = Path(reuse); inp = out/"inputs"
    inp.mkdir(parents=True, exist_ok=True)
    # Freeze all selection inputs before fetching any selected stock's 2026 data.
    calendar = dates(persist(inp/"selection_calendar.parquet", lambda: dates(
        pd.read_parquet(reuse/"calendar.parquet")).query("cal_date <= @AS_OF")))
    if calendar.cal_date.max() > AS_OF:
        calendar = calendar[calendar.cal_date.le(AS_OF)].copy()
        calendar.to_parquet(inp/"selection_calendar.parquet", index=False)
    basic = persist(inp/"basic.parquet", lambda: pd.read_parquet(reuse/"basic.parquet"))
    selection_daily = dates(persist(inp/"selection_daily.parquet", lambda: dp.get_day(
        start_date="20250101", end_date=AS_OF, qfq=False, source="database_only")))
    selection_adj = dates(persist(inp/"selection_adj.parquet", lambda: dp.get_adj_factor(
        start_date="20250101", end_date=AS_OF, source="database_only")))
    selection_kpl = dates(persist(inp/"selection_kpl.parquet", lambda: dates(
        pd.read_parquet(reuse/"kpl.parquet")).query("trade_date <= @AS_OF")))
    selection_paths = [inp/name for name in ("selection_calendar.parquet", "basic.parquet",
                         "selection_daily.parquet", "selection_adj.parquet", "selection_kpl.parquet")]
    manifest = out/"OBSERVATION_SELECTION.json"
    if not manifest.exists():
        selected, universe, pool, themes, membership, stats = select_pool(
            selection_daily, selection_adj, calendar.cal_date.tolist(), basic, selection_kpl)
        universe.to_parquet(inp/"selection_features.parquet", index=False)
        pool.to_csv(out/"full_scored_candidates.csv", index=False)
        membership.to_parquet(inp/"selection_memberships60.parquet", index=False)
        themes.to_csv(out/"SELECTION_THEME_DAILY.csv", index=False)
        selected.to_csv(out/"observation_pool.csv", index=False)
        frozen = dict(version="pre2026_theme_trend_pool_v2", as_of=AS_OF,
                      selection_time="2025-12-31 after daily close, before 2026-01-01",
                      method="unchanged strict daily trend gates; literal prior60 limit-ups; declared last20 heat/width + trend/liquidity ranking; max3 per industry",
                      future_returns_used=False, future_2026_attribution_used=False,
                      codes=selected.ts_code.tolist(), full_denominator=stats,
                      files_sha256={str(path.relative_to(out)): digest(path) for path in
                                    selection_paths+[out/"observation_pool.csv", out/"full_scored_candidates.csv"]},
                      code_sha256=digest(__file__),
                      caveats=["This v2 change was motivated by failed v1 evaluation; 2026 is chronologically frozen but not a never-viewed pristine holdout.",
                               "Current surviving stock metadata and current industry/name are used only for conservative exclusions and diversification; historical metadata snapshots unavailable.",
                               "KPL database first-publication archive is not verified. Source values are treated as available after their dated close.",
                               "Historical literal KPL label membership is evidence of past trading attribution, not proven business benefit or catalyst causality.",
                               "Mean theme heat/width are descriptive scores, not probabilities or expected returns.",
                               "No future minute-data coverage or future return affects selected membership; missing future coverage must remain a data failure, not replacement."])
        write_json(manifest, frozen)
        print("Frozen observation pool", selected[["ts_code", "name", "industry", "ranking_theme", "selection_score"]].to_dict("records"), flush=True)
    else:
        frozen = json.loads(manifest.read_text())
        if frozen["as_of"] != AS_OF:
            raise ValueError("Frozen selection cutoff differs")
        for relative, expected in frozen["files_sha256"].items():
            if digest(out/relative) != expected:
                raise ValueError(f"Frozen selection input changed: {relative}")
        selected = pd.read_csv(out/"observation_pool.csv")
    codes = selected.ts_code.tolist()
    # Selection is now fixed. Native TDX lookup has no online fallback. Every
    # other provider request explicitly uses database_only; no service starts.
    persist(inp/"daily.parquet", lambda: dp.get_day(ts_codes=codes, start_date="20241001", end_date="20260930", qfq=False, source="database_only"))
    persist(inp/"adj.parquet", lambda: dp.get_adj_factor(ts_codes=codes, start_date="20241001", end_date="20260930", source="database_only"))
    persist(inp/"fifteen.parquet", lambda: dp.get_fifteenMin(ts_codes=codes, start_date="20241001", end_date="20260930"))
    persist(inp/"limits.parquet", lambda: dp.get_stk_limit(ts_codes=codes, start_date="20241001", end_date="20260930", source="database_only"))
    for name in ("calendar.parquet", "news.parquet", "kpl.parquet"):
        if not (inp/name).exists():
            shutil.copyfile(reuse/name, inp/name)
    notes = factual_notes(selected, pd.read_parquet(inp/"news.parquet"))
    notes.to_csv(out/"SELECTION_FACTUAL_NEWS_LEADS.csv", index=False)
    audit = []
    for name in ("daily", "adj", "fifteen", "limits"):
        data = pd.read_parquet(inp/f"{name}.parquet")
        for code in codes:
            matched = data[data.ts_code.eq(code)]
            datecol = "datetime" if name == "fifteen" else "trade_date"
            audit.append(dict(dataset=name, ts_code=code, rows=len(matched),
                              first=str(matched[datecol].min()) if len(matched) else None,
                              last=str(matched[datecol].max()) if len(matched) else None,
                              coverage_used_for_selection=False))
    pd.DataFrame(audit).to_csv(out/"INPUT_COVERAGE.csv", index=False)
    write_json(out/"INPUT_SNAPSHOT.json", dict(selection_manifest_sha256=digest(manifest),
                input_hashes={path.name:digest(path) for path in sorted(inp.glob("*.parquet"))},
                sources="coreClient.data_provider local database_only; native TDX offline fifteenMin; reused local v1 source snapshots",
                selected_codes=codes, selection_frozen_before_evaluation_fetch=True))
    print("Complete local v2 input snapshot", out, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--reuse", type=Path, default=REUSE)
    args = parser.parse_args()
    main(args.output, args.reuse)
