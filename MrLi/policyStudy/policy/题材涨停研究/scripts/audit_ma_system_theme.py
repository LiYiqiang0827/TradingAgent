"""Frozen pre-pullback historical peer pools; evidence, not core-role truth."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from review_ma_system_pullback import ROOT, write_json, digest
from coreClient import data_provider as dp


# Auxiliary labels are deliberately separate from KPL's primary lu_desc.
# Jingzhida's pool is an inference from pre-pullback published industry reports.
PEER_LABELS = {"688137.SH": "CRO", "000725.SZ": "玻璃基板",
               "688627.SH": "存储|HBM"}


def main():
    root = ROOT / "outputs/ma_system_pullback_20261005_v2"
    out = root / "theme/audit_v2"
    out.mkdir(exist_ok=False)
    kpl = pd.read_parquet("/tmp/ma_system_kpl_2026.parquet")
    kpl["trade_date"] = kpl.trade_date.astype(str)
    kpl.to_parquet(out / "kpl_source.parquet", index=False)
    cal = dp.get_tradecal(start_date="20260101", end_date="20260930", source="database_only")
    calendar = sorted(cal.cal_date.astype(str).unique())
    raw_hits = pd.concat([pd.read_parquet(p) for p in (root / "theme").glob("*_raw_name_hits.parquet")], ignore_index=True)
    raw_hits["published_at"] = pd.to_datetime(raw_hits.datetime, format="mixed", errors="coerce")
    results = []
    peer_cache = {}
    for folder in sorted((root / "replay").glob("T*")):
        import json
        c = json.loads((folder / "candidate.json").read_text())
        code = c["case"].split("_")[0]
        cutoff = pd.Timestamp(c["episode_start"])
        entry = pd.Timestamp(c["time"])
        before = [d for d in calendar if d < cutoff.strftime("%Y%m%d")]
        days = before[-20:]
        assert days
        prior = kpl[kpl.trade_date.isin(days)].copy()
        prior = prior[~prior.name.fillna("").str.contains(r"ST|退", case=False, regex=True)]
        prior = prior[prior.ts_code.str.endswith((".SH", ".SZ"))]
        own = prior[prior.ts_code.eq(code)].sort_values("trade_date")
        pattern = PEER_LABELS[code]
        matching = prior[prior.theme.fillna("").str.contains(pattern, regex=True)]
        members = sorted(set(matching.ts_code) - {code})
        width = matching.groupby("trade_date").ts_code.nunique().reindex(days, fill_value=0)
        own_recent = kpl[kpl.ts_code.eq(code) & kpl.trade_date.lt(cutoff.strftime("%Y%m%d"))].sort_values("trade_date")
        primary = str(own_recent.iloc[-1].lu_desc) if len(own_recent) else None
        frozen = out / folder.name
        frozen.mkdir()
        prior.to_parquet(frozen / "prior_kpl.parquet", index=False)
        write_json(frozen / "frozen_pool.json", dict(frozen_before=str(cutoff),
                                                     source_days=days, auxiliary_pattern=pattern,
                                                     members=members, current_concept_members_used=False,
                                                     attribution_source="pre-pullback news inference" if code == "688627.SH" else "past KPL auxiliary tags"))
        cache_key = (tuple(members), days[-1], str(entry))
        if cache_key not in peer_cache:
            raw = dp.get_fifteenMin(ts_codes=members, start_date=days[-1], end_date=entry.strftime("%Y%m%d")) if members else pd.DataFrame()
            if len(raw):
                raw["datetime"] = pd.to_datetime(raw.datetime)
                raw = raw[raw.datetime.le(entry)].copy()
            peer_cache[cache_key] = raw
        raw = peer_cache[cache_key]
        raw.to_parquet(frozen / "peer_minutes_prefix.parquet", index=False)
        peers, normalized = [], {}
        for peer in members:
            data = raw[raw.ts_code.eq(peer)].sort_values("datetime") if len(raw) else pd.DataFrame()
            if data.empty:
                peers.append(dict(code=peer, known=False, reason="minute_missing")); continue
            data["p_close"] = data.close * data.adj_factor
            base = data[data.datetime.eq(pd.Timestamp(days[-1] + " 15:00"))]
            previous = data[data.datetime.dt.strftime("%Y%m%d").lt(entry.strftime("%Y%m%d")) &
                            data.datetime.dt.strftime("%H:%M").eq("15:00")].tail(1)
            current = data[data.datetime.eq(entry)]
            if base.empty or previous.empty or current.empty:
                peers.append(dict(code=peer, known=False, reason="reference_or_current_slot_missing")); continue
            prices = data.p_close.to_numpy()
            if not np.isfinite(prices).all() or (prices <= 0).any():
                peers.append(dict(code=peer, known=False, reason="invalid_price")); continue
            at = float(current.p_close.iloc[0])
            ret = 100 * (at / float(previous.p_close.iloc[0]) - 1)
            base_price = float(base.p_close.iloc[0])
            normalized[peer] = data.drop_duplicates("datetime").set_index("datetime").p_close / base_price - 1
            peers.append(dict(code=peer, known=True, day_return_pct=ret,
                              since_pool_freeze_pct=100 * (at / base_price - 1),
                              lowest_since_freeze_pct=100 * (data[data.datetime.gt(base.datetime.iloc[0])].p_close.min() / base_price - 1)))
        write_json(frozen / "peers.json", peers)
        known = [p for p in peers if p["known"]]
        positive = sum(p["day_return_pct"] > 0 for p in known)
        strong = sum(p["day_return_pct"] >= 2 for p in known)
        # Research proxy only. Unknown peers stay in the denominator.
        coverage = len(known) / len(members) if members else None
        fraction = positive / len(members) if members else None
        med = float(np.median([p["day_return_pct"] for p in known])) if known else None
        strength = (fraction >= .5 and strong >= 2 and med > 0) if coverage is not None and coverage >= .8 else None
        resonance = None
        group_trough = stock_trough = None
        if normalized:
            panel = pd.DataFrame(normalized)
            visible = panel.notna().sum(axis=1).ge(.8 * len(members))
            group = (panel.median(axis=1) * 100).where(visible)
            group.to_csv(frozen / "peer_median_since_freeze.csv", header=["return_pct"])
            armed = pd.Timestamp(c["armed_at"])
            pullback = group[(group.index >= cutoff) & (group.index <= armed)].dropna()
            own_m = pd.read_parquet(root / "execution_inputs" / c["case"] / "fifteen.parquet")
            own_m["datetime"] = pd.to_datetime(own_m.datetime)
            own_m["p_close"] = own_m.close * own_m.adj_factor
            own_base = own_m[own_m.datetime.eq(pd.Timestamp(days[-1] + " 15:00"))]
            own_span = own_m[own_m.datetime.between(cutoff, armed)].set_index("datetime").p_close
            if len(pullback) and len(own_base) and len(own_span):
                own_return = (own_span / float(own_base.p_close.iloc[0]) - 1) * 100
                group_trough = str(pullback.idxmin())
                stock_trough = str(own_return.idxmin())
                lo, hi = sorted([pd.Timestamp(group_trough).strftime("%Y%m%d"), pd.Timestamp(stock_trough).strftime("%Y%m%d")])
                session_gap = len([day for day in calendar if lo < day <= hi])
                resonance = bool(pullback.min() <= -3 and own_return.min() <= -3 and session_gap <= 1)
        # Article time is primary, model import time is not historical availability.
        name_pattern = "京东方" if code == "000725.SZ" else c["name"]
        text = raw_hits.title.fillna("") + " " + raw_hits.content.fillna("")
        news = raw_hits[text.str.contains(name_pattern, regex=False) & raw_hits.published_at.le(entry)]
        news = news[news.published_at.ge(cutoff - pd.Timedelta(days=60))].sort_values("published_at")
        news.to_parquet(frozen / "visible_news.parquet", index=False)
        archive_count = 0
        for p in (root / "theme").glob("*_archived_news.parquet"):
            if p.stem.startswith(name_pattern):
                a = pd.read_parquet(p)
                archive_count = len(a)
        record = dict(id=folder.name, name=c["name"], time=c["time"],
                      episode_start=c["episode_start"], primary_latest=primary,
                      own_kpl_events_prior20=len(own), auxiliary_peer_pattern=pattern,
                      prior20_peak_limitup_width=int(width.max()), historical_hot_proxy=bool(width.max() >= 5),
                      frozen_peer_count=len(members), known_peer_count=len(known), minute_coverage=coverage,
                      peer_positive_fraction=fraction, peer_strong_count=strong,
                      peer_median_day_return_pct=med, peer_strength_proxy=strength,
                      source_availability="next-session availability assumption; first-release archive absent",
                      core_role="unverified", visible_name_news_rows=len(news),
                      archived_semantic_rows=archive_count,
                      formal_theme_gate="unverified_core_role_and_catalyst_persistence",
                      sector_pullback_resonance_proxy=resonance,
                      peer_trough_time=group_trough, stock_trough_time=stock_trough,
                      note="KPL primary attribution is lu_desc; auxiliary theme tags are a different evidence channel")
        results.append(record)
        print(record["id"], record["name"], "pool",len(members),"coverage",coverage,"strength",strength,flush=True)
    write_json(out / "summary.json", results)
    pd.DataFrame(results).to_csv(out / "summary.csv", index=False)
    checks = []
    for earlier in results:
        for later in results:
            if (earlier["name"] == later["name"] and
                    earlier["episode_start"] == later["episode_start"] and
                    earlier["time"] < later["time"]):
                first = pd.read_parquet(out / earlier["id"] / "peer_minutes_prefix.parquet")
                second = pd.read_parquet(out / later["id"] / "peer_minutes_prefix.parquet")
                second = second[pd.to_datetime(second.datetime).le(pd.Timestamp(earlier["time"]))]
                cols = [c for c in first.columns if c != "snap_ts"]
                pd.testing.assert_frame_equal(first[cols].sort_values(["ts_code", "datetime"]).reset_index(drop=True),
                                              second[cols].sort_values(["ts_code", "datetime"]).reset_index(drop=True))
                checks.append(dict(earlier=earlier["id"], later=later["id"], status="prefix_equal"))
    write_json(out / "prefix_checks.json", checks)
    write_json(out / "MANIFEST.json", dict(causal_peers=True, st_excluded=True,
                                          scope="SH/SZ historical limit-up participants, not all concept members",
                                          source_sha256=digest(out / "kpl_source.parquet"),
                                          code_sha256=digest(__file__),
                                          prefix_checks=len(checks),
                                          formal_theme_gate_verified=False,
                                          source_publication_availability_verified=False))


if __name__ == "__main__":
    main()
