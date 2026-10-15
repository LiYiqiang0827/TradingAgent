"""Build MA60/MA120 nominal Top-100 observation lists and local theme evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

from study_ma60_top1pct_themes import build_theme_evidence_summary


EPISODE_COLUMNS = [
    "rank",
    "ts_code",
    "period",
    "start_date",
    "end_date",
    "length",
    "target_days",
    "left_censored",
    "carried_in",
    "right_censored",
]


def select_top_n_with_ties(
    episodes: pd.DataFrame,
    *,
    period: int,
    top_n: int = 100,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Select each stock's best in-window episode and retain cutoff ties."""
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    required = {"period", "ts_code", "target_days", "length", "end_date"}
    if missing := required.difference(episodes.columns):
        raise ValueError(f"missing required columns: {sorted(missing)}")
    candidates = episodes.loc[episodes["period"].eq(period)].copy()
    candidates = candidates.sort_values(
        ["target_days", "length", "end_date", "ts_code"],
        ascending=[False, False, False, True],
    ).drop_duplicates("ts_code", keep="first")
    if len(candidates) < top_n:
        raise ValueError(f"only {len(candidates)} candidates for period={period}; need {top_n}")
    cutoff = int(candidates.iloc[top_n - 1]["target_days"])
    selected = candidates.loc[candidates["target_days"].ge(cutoff)].copy().reset_index(drop=True)
    selected.insert(0, "rank", range(1, len(selected) + 1))
    metadata = {
        "requested_top_n": int(top_n),
        "cutoff_target_days": cutoff,
        "cutoff_tie_count": int((candidates["target_days"] == cutoff).sum()),
        "selected_with_ties": int(len(selected)),
    }
    return selected, metadata


def _prefix_ranking(frame: pd.DataFrame, period: int) -> pd.DataFrame:
    required = set(EPISODE_COLUMNS)
    if missing := required.difference(frame.columns):
        raise ValueError(f"period {period} ranking missing columns: {sorted(missing)}")
    prefix = f"ma{period}_"
    columns = [column for column in EPISODE_COLUMNS if column not in {"ts_code", "period"}]
    return frame[["ts_code", *columns]].rename(
        columns={column: f"{prefix}{column}" for column in columns}
    )


def build_observation_union(ma60: pd.DataFrame, ma120: pd.DataFrame) -> pd.DataFrame:
    """Outer-join two ranked lists and retain period-specific episode fields."""
    left = _prefix_ranking(ma60, 60)
    right = _prefix_ranking(ma120, 120)
    union = left.merge(right, on="ts_code", how="outer", validate="one_to_one")
    union["in_ma60_top100"] = union["ma60_rank"].notna()
    union["in_ma120_top100"] = union["ma120_rank"].notna()
    union["membership_count"] = (
        union["in_ma60_top100"].astype(int) + union["in_ma120_top100"].astype(int)
    )
    union["membership"] = ""
    union.loc[union["in_ma60_top100"], "membership"] = "MA60"
    union.loc[union["in_ma120_top100"], "membership"] = "MA120"
    union.loc[union["membership_count"].eq(2), "membership"] = "MA60+MA120"
    union["best_rank"] = union[["ma60_rank", "ma120_rank"]].min(axis=1)
    union["_ma60_bucket"] = (~union["in_ma60_top100"]).astype(int)
    union["_period_rank"] = union["ma60_rank"].fillna(union["ma120_rank"])
    union = union.sort_values(
        ["_ma60_bucket", "_period_rank", "ts_code"],
        ascending=[True, True, True],
    ).drop(columns=["_ma60_bucket", "_period_rank"]).reset_index(drop=True)
    union.insert(0, "observation_rank", range(1, len(union) + 1))
    for column in ("ma60_rank", "ma120_rank"):
        union[column] = union[column].astype("Int64")
    return union


def _python_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def build_evidence_input(union: pd.DataFrame) -> pd.DataFrame:
    """Create the single representative-episode schema used by evidence collection."""
    records: list[dict[str, Any]] = []
    for _, row in union.sort_values("observation_rank").iterrows():
        period = 60 if bool(row["in_ma60_top100"]) else 120
        prefix = f"ma{period}_"
        records.append(
            {
                "rank": int(row["observation_rank"]),
                "ts_code": str(row["ts_code"]),
                "period": period,
                "start_date": str(row[f"{prefix}start_date"]),
                "end_date": str(row[f"{prefix}end_date"]),
                "length": int(row[f"{prefix}length"]),
                "target_days": int(row[f"{prefix}target_days"]),
                "left_censored": bool(row[f"{prefix}left_censored"]),
                "carried_in": bool(row[f"{prefix}carried_in"]),
                "right_censored": bool(row[f"{prefix}right_censored"]),
            }
        )
    return pd.DataFrame(records)


def augment_evidence_with_membership(
    evidence: list[dict[str, Any]],
    union: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Attach both ranked-list memberships and period-specific episode data."""
    indexed = union.set_index("ts_code", drop=False)
    if set(indexed.index.astype(str)) != {str(item["ts_code"]) for item in evidence}:
        raise ValueError("evidence and observation-union coverage differ")
    fields = [
        "observation_rank", "membership", "in_ma60_top100", "in_ma120_top100",
        "ma60_rank", "ma60_start_date", "ma60_end_date", "ma60_length",
        "ma60_target_days", "ma60_left_censored", "ma60_carried_in",
        "ma60_right_censored", "ma120_rank", "ma120_start_date",
        "ma120_end_date", "ma120_length", "ma120_target_days",
        "ma120_left_censored", "ma120_carried_in", "ma120_right_censored",
    ]
    output: list[dict[str, Any]] = []
    for item in evidence:
        row = indexed.loc[str(item["ts_code"])]
        record = dict(item)
        record.update({field: _python_value(row[field]) for field in fields})
        output.append(record)
    return sorted(output, key=lambda item: int(item["observation_rank"]))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=Path("outputs/ma_rising_streak_distribution/2026_through_20260930"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/ma60_ma120_top100_themes/2026_through_20260930"),
    )
    parser.add_argument("--top-n", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    root = Path(__file__).resolve().parents[5]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from coreClient.data_provider import (
        get_basic,
        get_kpl_concept_cons,
        get_kpl_limit_performance,
        get_kpl_list,
    )

    args = _parse_args()
    analysis_metadata = json.loads(
        (args.analysis_dir / "metadata.json").read_text(encoding="utf-8")
    )
    episodes = pd.read_csv(
        args.analysis_dir / "episodes.csv", dtype={"ts_code": str}
    )
    ma60, ma60_metadata = select_top_n_with_ties(
        episodes, period=60, top_n=args.top_n
    )
    ma120, ma120_metadata = select_top_n_with_ties(
        episodes, period=120, top_n=args.top_n
    )
    union = build_observation_union(ma60, ma120)
    codes = union["ts_code"].astype(str).tolist()
    target_start = str(analysis_metadata["requested_target_start"])
    actual_end = str(analysis_metadata["actual_target_end"])

    basic = get_basic(source="database_only")
    concepts = get_kpl_concept_cons(
        ts_codes=codes,
        start_date=target_start,
        end_date=actual_end,
        source="database_only",
    )
    kpl_list = get_kpl_list(
        start_date=target_start,
        end_date=actual_end,
        tags=None,
        source="database_only",
    )
    if not kpl_list.empty:
        kpl_list = kpl_list.loc[kpl_list["ts_code"].isin(codes)].copy()
    limit_performance = get_kpl_limit_performance(
        start_date=target_start,
        end_date=actual_end,
        source="database_only",
    )
    if not limit_performance.empty:
        limit_performance = limit_performance.loc[
            limit_performance["ts_code"].isin(codes)
        ].copy()

    evidence_input = build_evidence_input(union)
    evidence = build_theme_evidence_summary(
        evidence_input,
        basic,
        concepts,
        kpl_list,
        limit_performance,
    )
    evidence = augment_evidence_with_membership(evidence, union)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ma60[EPISODE_COLUMNS].to_csv(args.output_dir / "ma60_top100_with_ties.csv", index=False)
    ma120[EPISODE_COLUMNS].to_csv(args.output_dir / "ma120_top100_with_ties.csv", index=False)
    union.to_csv(args.output_dir / "observation_union.csv", index=False)
    concepts.to_csv(args.output_dir / "concept_evidence.csv", index=False)
    kpl_list.to_csv(args.output_dir / "kpl_list_evidence.csv", index=False)
    limit_performance.to_csv(
        args.output_dir / "kpl_limit_performance_evidence.csv", index=False
    )
    (args.output_dir / "theme_evidence_summary.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    evidence_frame = pd.DataFrame(
        [
            {
                **{key: value for key, value in item.items() if not isinstance(value, list)},
                "kpl_list_themes": json.dumps(item["kpl_list_themes"], ensure_ascii=False),
                "limit_performance_themes": json.dumps(
                    item["limit_performance_themes"], ensure_ascii=False
                ),
                "concepts": json.dumps(item["concepts"], ensure_ascii=False),
            }
            for item in evidence
        ]
    )
    evidence_frame.to_csv(args.output_dir / "theme_evidence_summary.csv", index=False)

    overlap = int(
        len(set(ma60["ts_code"].astype(str)) & set(ma120["ts_code"].astype(str)))
    )
    output_metadata = {
        "requested_top_n_each": int(args.top_n),
        "ma60": ma60_metadata,
        "ma120": ma120_metadata,
        "overlap": overlap,
        "union": int(len(union)),
        "ranking_metric": (
            "每只股票与目标窗口相交的对应均线连续上升段中，"
            "窗口内连续上升交易日target_days的最大值"
        ),
        "tie_rule": "第100名target_days阈值同分全部纳入",
        "ranking_price_basis": str(analysis_metadata.get("price_basis", "")),
        "requested_target_start": target_start,
        "actual_target_end": actual_end,
        "concept_rows": int(len(concepts)),
        "kpl_list_rows": int(len(kpl_list)),
        "kpl_limit_performance_rows": int(len(limit_performance)),
        "data_sources": [
            "outputs/ma_rising_streak_distribution/.../episodes.csv",
            "coreClient.data_provider.get_basic(source='database_only')",
            "coreClient.data_provider.get_kpl_concept_cons(source='database_only')",
            "coreClient.data_provider.get_kpl_list(source='database_only')",
            "coreClient.data_provider.get_kpl_limit_performance(source='database_only')",
        ],
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(output_metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(output_metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
