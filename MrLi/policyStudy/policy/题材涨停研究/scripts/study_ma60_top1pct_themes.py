"""Rank 2026 MA60 persistence leaders and collect local KPL theme evidence."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

import pandas as pd


_THEME_SPLIT_RE = re.compile(r"[,，、;；/|]+")


def split_theme_tokens(value: Any) -> list[str]:
    """Split KPL theme strings without inventing or normalizing labels."""
    if value is None or pd.isna(value):
        return []
    return [token.strip() for token in _THEME_SPLIT_RE.split(str(value)) if token.strip()]


def aggregate_theme_counts(
    rows: pd.DataFrame,
    ts_code: str,
    *,
    code_col: str = "ts_code",
    theme_col: str = "theme",
) -> list[dict[str, Any]]:
    """Count verbatim theme labels for one stock."""
    if rows.empty or code_col not in rows or theme_col not in rows:
        return []
    counter: Counter[str] = Counter()
    for value in rows.loc[rows[code_col].eq(ts_code), theme_col]:
        counter.update(split_theme_tokens(value))
    return [
        {"theme": theme, "count": int(count)}
        for theme, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def select_top_percent_stocks(
    episodes: pd.DataFrame,
    *,
    period: int,
    valid_universe: int,
    top_pct: float = 0.01,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Select stocks by their longest in-window rising episode, including cutoff ties."""
    if valid_universe <= 0:
        raise ValueError("valid_universe must be positive")
    if not 0 < top_pct <= 1:
        raise ValueError("top_pct must be in (0, 1]")
    required = {"period", "ts_code", "target_days", "length", "end_date"}
    missing = required.difference(episodes.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")

    candidates = episodes[episodes["period"].eq(period)].copy()
    candidates = candidates.sort_values(
        ["target_days", "length", "end_date", "ts_code"],
        ascending=[False, False, False, True],
    ).drop_duplicates("ts_code", keep="first")
    nominal_top_n = math.ceil(valid_universe * top_pct)
    if len(candidates) < nominal_top_n:
        raise ValueError(
            f"only {len(candidates)} stocks have qualifying episodes; need {nominal_top_n}"
        )
    cutoff = int(candidates.iloc[nominal_top_n - 1]["target_days"])
    selected = candidates[candidates["target_days"].ge(cutoff)].copy().reset_index(drop=True)
    selected.insert(0, "rank", range(1, len(selected) + 1))
    metadata = {
        "nominal_top_n": nominal_top_n,
        "cutoff_target_days": cutoff,
        "selected_with_ties": int(len(selected)),
    }
    return selected, metadata


def summarize_concepts(
    rows: pd.DataFrame,
    ts_code: str,
) -> list[dict[str, Any]]:
    """Summarize local KPL concept membership evidence for one stock."""
    if rows.empty or "con_code" not in rows or "name" not in rows:
        return []
    stock_rows = rows.loc[rows["con_code"].eq(ts_code)].copy()
    stock_rows = stock_rows[stock_rows["name"].notna()]
    if stock_rows.empty:
        return []
    stock_rows["_hot_num"] = pd.to_numeric(stock_rows.get("hot_num"), errors="coerce")
    summaries: list[dict[str, Any]] = []
    for name, group in stock_rows.groupby("name", sort=False):
        ordered = group.sort_values("trade_date")
        descriptions = ordered.loc[ordered.get("desc", pd.Series(index=ordered.index)).notna(), "desc"]
        max_hot = group["_hot_num"].max()
        summaries.append(
            {
                "theme": str(name),
                "observed_days": int(group["trade_date"].nunique()),
                "latest_date": str(group["trade_date"].max()),
                "max_hot_num": None if pd.isna(max_hot) else int(max_hot),
                "description": "" if descriptions.empty else str(descriptions.iloc[-1]),
            }
        )
    return sorted(
        summaries,
        key=lambda item: (
            -item["observed_days"],
            -(item["max_hot_num"] or -1),
            item["theme"],
        ),
    )


def build_theme_evidence_summary(
    top_stocks: pd.DataFrame,
    basic: pd.DataFrame,
    concepts: pd.DataFrame,
    kpl_list: pd.DataFrame,
    limit_performance: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Build per-stock evidence objects while keeping provider labels verbatim."""
    basic_by_code = (
        basic.drop_duplicates("ts_code", keep="last").set_index("ts_code")
        if not basic.empty and "ts_code" in basic
        else pd.DataFrame()
    )
    result: list[dict[str, Any]] = []
    for _, row in top_stocks.iterrows():
        ts_code = str(row["ts_code"])
        stock_name = ""
        industry = ""
        if not basic_by_code.empty and ts_code in basic_by_code.index:
            basic_row = basic_by_code.loc[ts_code]
            stock_name = "" if pd.isna(basic_row.get("name")) else str(basic_row.get("name"))
            industry = "" if pd.isna(basic_row.get("industry")) else str(basic_row.get("industry"))
        if not stock_name and not concepts.empty:
            names = concepts.loc[concepts["con_code"].eq(ts_code), "con_name"].dropna()
            if not names.empty:
                stock_name = str(names.iloc[-1])
        result.append(
            {
                "rank": int(row["rank"]),
                "ts_code": ts_code,
                "stock_name": stock_name,
                "industry": industry,
                "start_date": str(row["start_date"]),
                "end_date": str(row["end_date"]),
                "target_days": int(row["target_days"]),
                "full_episode_length": int(row["length"]),
                "carried_in": bool(row.get("carried_in", False)),
                "right_censored": bool(row.get("right_censored", False)),
                "kpl_list_themes": aggregate_theme_counts(kpl_list, ts_code),
                "limit_performance_themes": aggregate_theme_counts(limit_performance, ts_code),
                "concepts": summarize_concepts(concepts, ts_code),
            }
        )
    return result


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
        default=Path("outputs/ma60_top1pct_themes/2026_through_20260930"),
    )
    parser.add_argument("--period", type=int, default=60)
    parser.add_argument("--top-pct", type=float, default=0.01)
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
    analysis_metadata = json.loads((args.analysis_dir / "metadata.json").read_text())
    episodes = pd.read_csv(args.analysis_dir / "episodes.csv", dtype={"ts_code": str})
    period_summary = pd.read_csv(args.analysis_dir / "summary.csv")
    period_row = period_summary.loc[period_summary["period"].eq(args.period)]
    if len(period_row) != 1:
        raise ValueError(f"expected one summary row for period={args.period}")
    valid_universe = int(period_row.iloc[0]["valid_stocks"])
    top_stocks, selection_metadata = select_top_percent_stocks(
        episodes,
        period=args.period,
        valid_universe=valid_universe,
        top_pct=args.top_pct,
    )
    codes = top_stocks["ts_code"].astype(str).tolist()
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
        kpl_list = kpl_list[kpl_list["ts_code"].isin(codes)].copy()
    limit_performance = get_kpl_limit_performance(
        start_date=target_start,
        end_date=actual_end,
        source="database_only",
    )
    if not limit_performance.empty:
        limit_performance = limit_performance[
            limit_performance["ts_code"].isin(codes)
        ].copy()

    evidence = build_theme_evidence_summary(
        top_stocks,
        basic,
        concepts,
        kpl_list,
        limit_performance,
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

    args.output_dir.mkdir(parents=True, exist_ok=True)
    top_columns = [
        "rank", "ts_code", "period", "start_date", "end_date", "length",
        "target_days", "left_censored", "carried_in", "right_censored",
    ]
    top_stocks[top_columns].to_csv(args.output_dir / "top_ma60_stocks.csv", index=False)
    concepts.to_csv(args.output_dir / "concept_evidence.csv", index=False)
    kpl_list.to_csv(args.output_dir / "kpl_list_evidence.csv", index=False)
    limit_performance.to_csv(
        args.output_dir / "kpl_limit_performance_evidence.csv", index=False
    )
    evidence_frame.to_csv(args.output_dir / "theme_evidence_summary.csv", index=False)
    (args.output_dir / "theme_evidence_summary.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_metadata = {
        **selection_metadata,
        "period": args.period,
        "top_pct": args.top_pct,
        "valid_universe": valid_universe,
        "ranking_metric": (
            "每只股票与目标窗口相交的MA上升段中，窗口内连续上升交易日"
            "target_days的最大值"
        ),
        "tie_rule": "第56名阈值以上全部纳入",
        "requested_target_start": target_start,
        "actual_target_end": actual_end,
        "concept_rows": int(len(concepts)),
        "kpl_list_rows": int(len(kpl_list)),
        "kpl_limit_performance_rows": int(len(limit_performance)),
        "data_sources": [
            "coreClient.data_provider.get_basic(source='database_only')",
            "coreClient.data_provider.get_kpl_concept_cons(source='database_only')",
            "coreClient.data_provider.get_kpl_list(source='database_only')",
            "coreClient.data_provider.get_kpl_limit_performance(source='database_only')",
        ],
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(output_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(output_metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
