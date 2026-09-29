"""August 2026 diagnostic: point-in-time first-board relay and four-board paths.

This reads previously frozen, audited replay cohorts. Future-four-board and
future-path fields are *outcomes only*; they never select a trading candidate.
The groups are descriptive development checks, not independent validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[4]
FIRST = ROOT / "outputs/one_min_early_high_board_2026/events.csv"
RANK = ROOT / "outputs/early_high_board_research/leader_rank_2026.csv"
FOURTH = ROOT / "outputs/four_board_paths_2026/four_board_paths.csv"
ONE_MIN_CATALOG = ROOT / "offlineDataManager/data/oneMinute/catalog.duckdb"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(rows: pd.DataFrame) -> dict:
    scored = rows.loc[rows.buyable_936 & rows.net.notna()]
    net = 100 * scored.net
    return {
        "candidates": int(len(rows)),
        "later_four_board": int(rows.future_four_board.sum()),
        "buyable_proxy": int(rows.buyable_936.sum()),
        "scored": int(len(scored)),
        "unresolved_or_missing_exit": int(rows.buyable_936.sum() - len(scored)),
        "buyable_later_four_board": int((rows.buyable_936 & rows.future_four_board).sum()),
        "mean_net_pct": float(net.mean()) if len(net) else None,
        "median_net_pct": float(net.median()) if len(net) else None,
        "net_win_rate_pct": float(100 * net.gt(0).mean()) if len(net) else None,
        "worst_net_pct": float(net.min()) if len(net) else None,
    }


def _day_bootstrap(rows: pd.DataFrame, seed: int = 202608, trials: int = 5000) -> list[float]:
    scored = rows.loc[rows.buyable_936 & rows.net.notna()]
    day = scored.groupby("d0").net.agg(["sum", "count"])
    if day.empty:
        return [float("nan"), float("nan")]
    rng = np.random.default_rng(seed)
    sample = rng.integers(0, len(day), size=(trials, len(day)))
    values = (day["sum"].to_numpy()[sample].sum(axis=1)
              / day["count"].to_numpy()[sample].sum(axis=1))
    return [float(v) for v in (100 * np.quantile(values, [.025, .975]))]


def run(month: str = "202608") -> dict:
    first = pd.read_csv(FIRST, dtype={"dminus1": str, "d0": str, "ts_code": str})
    rank = pd.read_csv(RANK, dtype={"dminus1": str, "d0": str, "ts_code": str})
    fourth = pd.read_csv(FOURTH, dtype={"d0": str, "ts_code": str})
    first = first.loc[first.d0.str.startswith(month)].copy()
    rank = rank.loc[rank.d0.str.startswith(month),
                    ["d0", "ts_code", "theme_id", "first_seal", "theme_firstboard_count",
                     "prior_top3_strong"]].copy()
    fourth = fourth.loc[fourth.d0.str.startswith(month)].copy()
    if first.empty or fourth.empty:
        raise ValueError(f"No August cohort for {month}")
    if first.duplicated(["d0", "ts_code"]).any() or rank.duplicated(["d0", "ts_code"]).any():
        raise ValueError("Candidate keys are not unique")
    if first.d0.nunique() != 21 and month == "202608":
        raise ValueError("August first-board cohort lacks a trading day")
    first = first.merge(rank, on=["d0", "ts_code"], how="left",
                        validate="one_to_one", indicator=True)
    if not first._merge.eq("both").all():
        raise ValueError("A candidate is missing its prior-day leader-rank record")
    first = first.drop(columns="_merge")
    if first.first_seal.isna().any():
        raise ValueError("A candidate is missing prior-day leader rank")
    multi = first.theme_firstboard_count.ge(2)
    # An intentionally simple, executable hypothesis: within each prior-day
    # top-3 strong theme, select the buyable candidate with the highest 09:35
    # return. Select before examining future labels or exit availability.
    strongest = (first.loc[first.prior_top3_strong & first.buyable_936]
                 .sort_values(["d0", "theme_id", "close_935_pct", "ts_code"],
                              ascending=[True, True, False, True])
                 .drop_duplicates(["d0", "theme_id"]))
    groups = {
        "all": _summary(first),
        "prior_top3_strong_theme": _summary(first.loc[first.prior_top3_strong]),
        "prior_other_theme": _summary(first.loc[~first.prior_top3_strong]),
        "multi_theme_first_seal": _summary(first.loc[multi & first.first_seal]),
        "multi_theme_other": _summary(first.loc[multi & ~first.first_seal]),
        "first_five_minutes_up_3pct": _summary(first.loc[first.close_935_pct.ge(3)]),
        "top3_theme_strongest_0935": _summary(strongest),
    }
    winners = first.loc[first.future_four_board & first.buyable_936].copy()
    invalidation = first.loc[first.buyable_936 & first.invalidation_net.notna(),
                             "invalidation_net"]
    matched = []
    for row in winners.itertuples():
        peers = first.loc[first.d0.eq(row.d0) & first.theme_name.eq(row.theme_name)
                          & first.buyable_936 & ~first.future_four_board]
        matched.append({"d0": row.d0, "ts_code": row.ts_code, "name": row.name,
                        "theme": row.theme_name, "net_pct": float(100 * row.net),
                        "same_day_theme_buyable_nonwinners": int(len(peers)),
                        "nonwinner_mean_net_pct": float(100 * peers.net.mean()) if len(peers) else None,
                        "early_strength_rank_among_buyable": int(1 + (
                            first.loc[first.d0.eq(row.d0) & first.theme_name.eq(row.theme_name)
                                      & first.buyable_936, "close_935_pct"] > row.close_935_pct
                        ).sum())})
    direct = fourth.d1_direct_five.fillna(False).astype(bool)
    wide = fourth.theme_width_d0.ge(3)
    catalog_counts: dict[str, int] = {}
    if ONE_MIN_CATALOG.is_file():
        dates = sorted(set(first.d0) | set(pd.to_datetime(
            first.exit_time.dropna()).dt.strftime("%Y%m%d")))
        conn = duckdb.connect(str(ONE_MIN_CATALOG), read_only=True)
        try:
            catalog = conn.execute(
                "SELECT trade_date,data_source FROM one_min_ingest_catalog WHERE trade_date IN ("
                + ",".join("?" for _ in dates) + ")", dates,
            ).df()
        finally:
            conn.close()
        if set(catalog.trade_date.astype(str)) != set(dates):
            raise ValueError("One-minute source catalog lacks a decision or exit date")
        catalog_counts = {str(k): int(v) for k, v in
                          catalog.data_source.value_counts().items()}
    result = {
        "month": month,
        "status": "exploratory_development_sample; no strategy selected",
        "source_sha256": {p.name: _sha(p) for p in (FIRST, RANK, FOURTH)},
        "one_min_partition_source_counts": catalog_counts,
        "first_board_definition": "D-1 KPL first board; D0 09:35 observe, 09:36 open-price buy proxy",
        "four_board_label": "D+2 consecutive fourth board; outcome only",
        "later_four_board_sealed_at_0935": int(first.loc[first.future_four_board, "sealed_935"].sum()),
        "buyable_winner_mean_net_pct_hindsight_only": float(100 * winners.net.mean()),
        "exit_definition": "D+1 one-minute +3%/-3%/14:45 decision, next tradable minute open, 0.31% round-trip cost proxy",
        "groups": groups,
        "d0_close_based_next_day_exit": {
            "rule": "If D0 failed second board, sell first tradable D+1 minute; otherwise use base exit",
            "scored": int(len(invalidation)),
            "unresolved_or_missing": int(first.buyable_936.sum() - len(invalidation)),
            "mean_net_pct": float(100 * invalidation.mean()),
            "worst_net_pct": float(100 * invalidation.min()),
        },
        "selected_top3_theme_cases": [
            {"d0": str(r.d0), "ts_code": str(r.ts_code), "name": str(r.name),
             "theme": str(r.theme_name), "close_935_pct": float(r.close_935_pct),
             "future_four_board_outcome": bool(r.future_four_board),
             "net_pct_outcome": float(100 * r.net) if pd.notna(r.net) else None,
             "exit_reason_outcome": str(r.exit_reason) if pd.notna(r.exit_reason) else None}
            for r in strongest.itertuples()
        ],
        "all_mean_net_day_bootstrap_95pct": _day_bootstrap(first),
        "strong_mean_net_day_bootstrap_95pct": _day_bootstrap(first.loc[first.prior_top3_strong]),
        "matched_winners": matched,
        "fourth_board_close_cohort": {
            "events": int(len(fourth)),
            "direct_fifth_board_next_day": int(direct.sum()),
            "path_counts": {str(k): int(v) for k, v in fourth.path.value_counts().items()},
            "width_ge_3_direct_fifth": int((wide & direct).sum()),
            "width_ge_3_total": int(wide.sum()),
            "width_lt_3_direct_fifth": int((~wide & direct).sum()),
            "width_lt_3_total": int((~wide).sum()),
        },
        "limitations": [
            "D-1 KPL publication time is not archived; D0 pre-open availability is an assumption.",
            "One-minute archive has CSV/TDX source mixing without complete per-stock lineage.",
            "One-minute open/volume is a fill proxy, not queue, depth or capacity evidence.",
            "Unresolved exits are excluded from realized means, making them optimistic.",
            "August is already known to the researcher and cannot be an independent holdout.",
        ],
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="202608")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "outputs/august_board_mechanism_2026/audit.json")
    args = parser.parse_args()
    result = run(args.month)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
