"""Timing of the first MA30 price pullback with MA5/MA10/MA30 cohesion."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[5]
DEFAULT_DAILY = (
    ROOT
    / "outputs"
    / "ma60_ma120_top100_ppt"
    / "2026_through_20260930_qfq"
    / "daily_input_qfq.csv.gz"
)
DEFAULT_MA60 = (
    ROOT
    / "outputs"
    / "ma60_ma120_top100_themes"
    / "2026_through_20260930_qfq"
    / "ma60_top100_with_ties_named.csv"
)
DEFAULT_MA120 = (
    ROOT
    / "outputs"
    / "ma60_ma120_top100_themes"
    / "2026_through_20260930_qfq"
    / "ma120_top100_with_ties_named.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "outputs"
    / "first_ma30_pullback_cohesion_timing"
    / "2026_through_20260930_qfq"
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-file", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--ma60-file", type=Path, default=DEFAULT_MA60)
    parser.add_argument("--ma120-file", type=Path, default=DEFAULT_MA120)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cohesion-threshold-pct", type=float, default=3.0)
    parser.add_argument("--touch-upper-pct", type=float, default=1.0)
    parser.add_argument("--close-floor-pct", type=float, default=-2.0)
    parser.add_argument("--prior-extension-pct", type=float, default=5.0)
    parser.add_argument("--extension-lookback", type=int, default=10)
    return parser.parse_args(argv)


def add_pullback_features(
    frame: pd.DataFrame,
    *,
    cohesion_threshold_pct: float,
    touch_upper_pct: float,
    close_floor_pct: float,
    prior_extension_pct: float,
    extension_lookback: int,
) -> pd.DataFrame:
    """Add a no-lookahead MA30 touch after a prior extension from MA30."""
    if extension_lookback <= 0:
        raise ValueError("extension_lookback must be positive")
    result = frame.copy()
    ma_values = result[["ma5", "ma10", "ma30"]].apply(
        pd.to_numeric, errors="coerce"
    )
    mean = ma_values.mean(axis=1)
    result["ma5_ma10_ma30_spread_pct"] = 100.0 * (
        ma_values.max(axis=1) - ma_values.min(axis=1)
    ) / mean
    close = pd.to_numeric(result["close"], errors="coerce")
    low = pd.to_numeric(result["low"], errors="coerce")
    ma30 = pd.to_numeric(result["ma30"], errors="coerce")
    result["close_vs_ma30_pct"] = 100.0 * (close / ma30 - 1.0)
    result["low_vs_ma30_pct"] = 100.0 * (low / ma30 - 1.0)
    result["prior_max_close_vs_ma30_pct"] = result[
        "close_vs_ma30_pct"
    ].shift(1).rolling(extension_lookback, min_periods=extension_lookback).max()
    pullback = (
        result["low_vs_ma30_pct"].le(touch_upper_pct)
        & result["close_vs_ma30_pct"].ge(close_floor_pct)
        & result["prior_max_close_vs_ma30_pct"].ge(prior_extension_pct)
        & ma30.notna()
    )
    result["ma30_pullback_candidate"] = pullback.fillna(False)
    result["ma30_pullback_cohesion_candidate"] = (
        pullback
        & result["ma5_ma10_ma30_spread_pct"].le(cohesion_threshold_pct)
    ).fillna(False)
    return result


def compute_rising_streak(series: pd.Series) -> pd.Series:
    """Strict MA_t > MA_(t-1) streak, reset by flat/fall/missing."""
    values = pd.to_numeric(series, errors="coerce")
    rising = values.gt(values.shift(1)) & values.notna() & values.shift(1).notna()
    output: list[int] = []
    current = 0
    for flag in rising:
        current = current + 1 if bool(flag) else 0
        output.append(current)
    return pd.Series(output, index=series.index, dtype=int)


def rising_episode_status_at_position(
    rising: pd.Series,
    position: int,
) -> dict[str, Any]:
    """Describe the strict rising episode containing a position."""
    values = rising.fillna(False).astype(bool).to_numpy()
    if position < 0 or position >= len(values):
        raise IndexError(position)
    if not bool(values[position]):
        return {
            "streak_at_position": 0,
            "observed_episode_total": 0,
            "remaining_observed_days": 0,
            "episode_right_censored": False,
        }
    start = position
    while start > 0 and bool(values[start - 1]):
        start -= 1
    end = position
    while end + 1 < len(values) and bool(values[end + 1]):
        end += 1
    return {
        "streak_at_position": int(position - start + 1),
        "observed_episode_total": int(end - start + 1),
        "remaining_observed_days": int(end - position),
        "episode_right_censored": bool(end == len(values) - 1),
    }


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value) if not pd.isna(value) else False


def build_event_record(
    frame: pd.DataFrame,
    *,
    signal_position: int,
    anchor: pd.Series,
) -> dict[str, Any]:
    """Materialize the first pullback and both long-MA streak states."""
    row = frame.iloc[signal_position]
    dates = frame["trade_date"].map(_compact_date).tolist()
    start_date = _compact_date(anchor["start_date"])
    if start_date not in dates:
        raise ValueError(f"anchor start missing for {anchor['ts_code']}")
    start_position = dates.index(start_date)
    record: dict[str, Any] = {
        "ts_code": str(anchor["ts_code"]),
        "stock_name": str(anchor.get("stock_name", "")),
        "primary_theme": str(anchor.get("primary_theme", "")),
        "broad_category": str(anchor.get("broad_category", "")),
        "anchor_period": int(anchor["anchor_period"]),
        "anchor_rank": int(anchor.get("rank", 0)),
        "anchor_start_date": start_date,
        "anchor_end_date": _compact_date(anchor["end_date"]),
        "anchor_length": int(anchor.get("length", 0)),
        "anchor_right_censored": _as_bool(anchor.get("right_censored", False)),
        "pullback_date": _compact_date(row["trade_date"]),
        "pullback_position": int(signal_position),
        "anchor_observed_days_at_pullback": int(signal_position - start_position + 1),
        "close": float(row["close"]),
        "low": float(row["low"]),
        "ma5": float(row["ma5"]),
        "ma10": float(row["ma10"]),
        "ma30": float(row["ma30"]),
        "ma5_ma10_ma30_spread_pct": float(row["ma5_ma10_ma30_spread_pct"]),
        "prior_max_close_vs_ma30_pct": float(row["prior_max_close_vs_ma30_pct"]),
        "low_vs_ma30_pct": float(row["low_vs_ma30_pct"]),
        "close_vs_ma30_pct": float(row["close_vs_ma30_pct"]),
    }
    for period in (60, 120):
        value = row[f"ma{period}"]
        record[f"ma{period}"] = float(value) if pd.notna(value) else float("nan")
        status = rising_episode_status_at_position(
            frame[f"ma{period}_rising"], signal_position
        )
        for key, status_value in status.items():
            record[f"ma{period}_{key}"] = status_value
    return record


def _compact_date(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text.split(".")[0]


def select_first_pullback(
    frame: pd.DataFrame,
    *,
    anchor_start_date: object,
    anchor_end_date: object,
    signal_column: str = "ma30_pullback_cohesion_candidate",
) -> int | None:
    """Return the first signal position inside the inclusive long-MA anchor."""
    if signal_column not in frame:
        raise ValueError(f"missing signal column: {signal_column}")
    dates = frame["trade_date"].map(_compact_date)
    mask = (
        dates.ge(_compact_date(anchor_start_date))
        & dates.le(_compact_date(anchor_end_date))
        & frame[signal_column].fillna(False).astype(bool)
    )
    positions = np.flatnonzero(mask.to_numpy())
    return int(positions[0]) if len(positions) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def prepare_stock_features(
    daily: pd.DataFrame,
    *,
    cohesion_threshold_pct: float,
    touch_upper_pct: float,
    close_floor_pct: float,
    prior_extension_pct: float,
    extension_lookback: int,
) -> pd.DataFrame:
    frame = daily.copy()
    frame["trade_date"] = frame["trade_date"].map(_compact_date)
    frame = (
        frame.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )
    close = pd.to_numeric(frame["close"], errors="coerce")
    for period in (5, 10, 30, 60, 120):
        frame[f"ma{period}"] = close.rolling(period, min_periods=period).mean()
    for period in (60, 120):
        frame[f"ma{period}_rising"] = (
            frame[f"ma{period}"].gt(frame[f"ma{period}"].shift(1))
            & frame[f"ma{period}"].notna()
            & frame[f"ma{period}"].shift(1).notna()
        )
        frame[f"ma{period}_rising_streak"] = compute_rising_streak(
            frame[f"ma{period}"]
        )
    return add_pullback_features(
        frame,
        cohesion_threshold_pct=cohesion_threshold_pct,
        touch_upper_pct=touch_upper_pct,
        close_floor_pct=close_floor_pct,
        prior_extension_pct=prior_extension_pct,
        extension_lookback=extension_lookback,
    )


def summarize_streaks(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if events.empty:
        return pd.DataFrame(rows)
    for scope, group in events.groupby("scope", sort=True):
        for period in (60, 120):
            streak = pd.to_numeric(
                group[f"ma{period}_streak_at_position"], errors="coerce"
            )
            rising = streak.gt(0)
            positive = streak.loc[rising]
            total = pd.to_numeric(
                group.loc[rising, f"ma{period}_observed_episode_total"],
                errors="coerce",
            )
            remaining = pd.to_numeric(
                group.loc[rising, f"ma{period}_remaining_observed_days"],
                errors="coerce",
            )
            rows.append(
                {
                    "scope": scope,
                    "ma_period": period,
                    "event_count": int(len(group)),
                    "currently_rising_count": int(rising.sum()),
                    "currently_rising_pct": 100.0 * float(rising.mean()),
                    "streak_all_sample_median": float(streak.median()),
                    "streak_all_sample_p25": float(streak.quantile(0.25)),
                    "streak_all_sample_p75": float(streak.quantile(0.75)),
                    "streak_median": float(positive.median()) if len(positive) else float("nan"),
                    "streak_p25": float(positive.quantile(0.25)) if len(positive) else float("nan"),
                    "streak_p75": float(positive.quantile(0.75)) if len(positive) else float("nan"),
                    "streak_min": float(positive.min()) if len(positive) else float("nan"),
                    "streak_max": float(positive.max()) if len(positive) else float("nan"),
                    "observed_episode_total_median": float(total.median()) if len(total) else float("nan"),
                    "remaining_observed_days_median": float(remaining.median()) if len(remaining) else float("nan"),
                    "episode_right_censored_count": int(
                        group.loc[rising, f"ma{period}_episode_right_censored"].map(_as_bool).sum()
                    ),
                }
            )
    return pd.DataFrame(rows)


def load_anchors(args: argparse.Namespace) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for period, path in ((60, args.ma60_file), (120, args.ma120_file)):
        current = pd.read_csv(path, dtype={"ts_code": str})
        current["anchor_period"] = period
        current["start_date"] = current["start_date"].map(_compact_date)
        current["end_date"] = current["end_date"].map(_compact_date)
        current["left_censored"] = current["left_censored"].map(_as_bool)
        current["search_eligible"] = ~current["left_censored"]
        pieces.append(current)
    return pd.concat(pieces, ignore_index=True)


def run_sensitivity(
    *,
    anchors: pd.DataFrame,
    features_by_code: dict[str, pd.DataFrame],
    cohesion_thresholds: Sequence[float],
    touch_thresholds: Sequence[float],
    extension_thresholds: Sequence[float],
    close_floor_pct: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    eligible = anchors.loc[anchors["search_eligible"]]
    for cohesion in cohesion_thresholds:
        for touch in touch_thresholds:
            for extension in extension_thresholds:
                selected: list[dict[str, Any]] = []
                for anchor in eligible.to_dict("records"):
                    frame = features_by_code[str(anchor["ts_code"])]
                    pullback_flag = (
                        frame["low_vs_ma30_pct"].le(touch)
                        & frame["close_vs_ma30_pct"].ge(close_floor_pct)
                        & frame["prior_max_close_vs_ma30_pct"].ge(extension)
                    )
                    dates = frame["trade_date"].map(_compact_date)
                    positions = np.flatnonzero(
                        (
                            pullback_flag
                            & dates.ge(_compact_date(anchor["start_date"]))
                            & dates.le(_compact_date(anchor["end_date"]))
                        ).to_numpy()
                    )
                    if not len(positions):
                        continue
                    position = int(positions[0])
                    if not bool(
                        frame.iloc[position]["ma5_ma10_ma30_spread_pct"] <= cohesion
                    ):
                        continue
                    selected.append(
                        {
                            "anchor_period": int(anchor["anchor_period"]),
                            "ma60_streak": int(frame.iloc[position]["ma60_rising_streak"]),
                            "ma120_streak": int(frame.iloc[position]["ma120_rising_streak"]),
                        }
                    )
                selected_frame = pd.DataFrame(selected)
                for anchor_period in (60, 120):
                    group = (
                        selected_frame.loc[selected_frame["anchor_period"].eq(anchor_period)]
                        if len(selected_frame)
                        else pd.DataFrame()
                    )
                    denominator = int(eligible["anchor_period"].eq(anchor_period).sum())
                    row: dict[str, Any] = {
                        "anchor_period": anchor_period,
                        "cohesion_threshold_pct": float(cohesion),
                        "touch_upper_pct": float(touch),
                        "close_floor_pct": float(close_floor_pct),
                        "prior_extension_pct": float(extension),
                        "eligible_count": denominator,
                        "event_count": int(len(group)),
                        "coverage_pct": 100.0 * len(group) / denominator if denominator else float("nan"),
                    }
                    for ma_period in (60, 120):
                        values = (
                            pd.to_numeric(group[f"ma{ma_period}_streak"], errors="coerce")
                            if len(group)
                            else pd.Series(dtype=float)
                        )
                        positive = values.loc[values.gt(0)]
                        row[f"ma{ma_period}_rising_count"] = int(values.gt(0).sum())
                        row[f"ma{ma_period}_streak_median"] = float(positive.median()) if len(positive) else float("nan")
                        row[f"ma{ma_period}_streak_p25"] = float(positive.quantile(0.25)) if len(positive) else float("nan")
                        row[f"ma{ma_period}_streak_p75"] = float(positive.quantile(0.75)) if len(positive) else float("nan")
                    rows.append(row)
    return pd.DataFrame(rows)


def write_report(
    *,
    coverage: pd.DataFrame,
    streaks: pd.DataFrame,
    first_qualifying_streaks: pd.DataFrame,
    sensitivity: pd.DataFrame,
    output: Path,
) -> None:
    def one(source: pd.DataFrame, scope: str, period: int) -> pd.Series:
        selected = source.loc[
            source["scope"].eq(scope) & source["ma_period"].eq(period)
        ]
        if len(selected) != 1:
            raise ValueError((scope, period, len(selected)))
        return selected.iloc[0]

    lines = [
        "# MA60/MA120阶段内第一次回调到MA30且MA5/10/30粘合的时点",
        "",
        "## 主口径",
        "",
        "- 使用前复权日线，成交量不参与本问题；停牌/缺失只按实际交易日序列处理。",
        "- 在每个非左截断MA60/MA120连续上升锚点内，从锚点首日开始，先固定第一个回调触及MA30的交易日；这一步不看粘合条件。",
        "- 此前10个交易日中，至少有一天收盘价比当日自身MA30高5%以上，确认价格曾明显离开MA30。",
        "- 当前日最低价不高于MA30上方1%，收盘价不低于MA30下方2%，定义为回调触及但未深度跌破MA30。",
        "- 只在已经固定的第一次回调当天检查MA5、MA10、MA30的 `(最大值-最小值)/均值` 是否不超过3%；首次不粘合时不跳到后续回调。",
        "- MA60/MA120连续上涨严格使用 `MA_t > MA_(t-1)`；持平、下降或不可比较立即清零。",
        "",
        "## 事件覆盖",
        "",
        "| 锚定组 | 原锚点 | 排除左截断 | 可搜索 | 有第一次回调 | 第一次回调当日粘合 | 占可搜索 | 后续才首次合格（不得纳入主口径） |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in coverage.to_dict("records"):
        lines.append(
            f"| MA{int(row['anchor_period'])} | {int(row['anchor_count'])} | {int(row['left_censored_count'])} | "
            f"{int(row['eligible_count'])} | {int(row['first_pullback_count'])} | {int(row['first_pullback_cohesion_count'])} | "
            f"{row['first_pullback_cohesion_pct_of_eligible']:.1f}% | {int(row['later_qualifying_after_failed_first_count'])} |"
        )
    skipped_later = int(coverage["later_qualifying_after_failed_first_count"].sum())
    first_qualifying_total = int(coverage["first_qualifying_pullback_count"].sum())
    lines.extend(
        [
            "",
            f"次级口径‘第一个同时满足回调与粘合的点’共有{first_qualifying_total}个，其中{skipped_later}个跳过了更早但不粘合的第一次回调；这些后跳事件不进入主统计。",
            "",
            "## 回调发生时MA60/MA120已经连续上涨多少天",
            "",
            "主表仅含‘第一次回调当天即粘合’事件。‘含0’把当时未上涨的交叉均线记为0；活动样本中位数/IQR只在该均线仍严格上涨的样本中统计。",
            "",
            "| 事件口径 | 均线 | 事件数 | 当时正在上涨 | 含0连续上涨中位数 | 活动样本中位数 | 活动样本IQR |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for scope in ("MA60", "MA120", "UNION_UNIQUE"):
        for period in (60, 120):
            row = one(streaks, scope, period)
            lines.append(
                f"| {scope} | MA{period} | {int(row['event_count'])} | {int(row['currently_rising_count'])}（{row['currently_rising_pct']:.1f}%） | "
                f"{row['streak_all_sample_median']:.1f}日 | {row['streak_median']:.1f}日 | "
                f"{row['streak_p25']:.1f}–{row['streak_p75']:.1f}日 |"
            )
    lines.extend(
        [
            "",
            "## 次级口径：第一次满足粘合的回调（允许跳过更早的不粘合回调）",
            "",
            "此口径更接近寻找第一个可用粘合买点，但不应再称为字面意义上的‘第一次回调’。",
            "",
            "| 锚定组 | 均线 | 事件数 | 当时正在上涨 | 活动样本连续上涨中位数 | IQR |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for scope in ("MA60", "MA120"):
        for period in (60, 120):
            row = one(first_qualifying_streaks, scope, period)
            lines.append(
                f"| {scope} | MA{period} | {int(row['event_count'])} | {int(row['currently_rising_count'])}（{row['currently_rising_pct']:.1f}%） | "
                f"{row['streak_median']:.1f}日 | {row['streak_p25']:.1f}–{row['streak_p75']:.1f}日 |"
            )
    main = sensitivity.loc[
        sensitivity["cohesion_threshold_pct"].eq(3.0)
        & sensitivity["touch_upper_pct"].eq(1.0)
        & sensitivity["prior_extension_pct"].eq(5.0)
    ]
    lines.extend(
        [
            "",
            "## 阈值敏感性",
            "",
            f"重算粘合2%/3%/5%、触及MA30上方1%/2%/3%、此前离开MA30幅度3%/5%/8%，共{len(sensitivity)}个‘参数×锚定组’组合；主参数事件数为{int(main['event_count'].sum())}。",
        ]
    )
    for anchor_period in (60, 120):
        group = sensitivity.loc[sensitivity["anchor_period"].eq(anchor_period)]
        corresponding = f"ma{anchor_period}_streak_median"
        lines.append(
            f"- MA{anchor_period}锚定：覆盖率{group['coverage_pct'].min():.1f}%–{group['coverage_pct'].max():.1f}%，对应长均线连续上涨中位数{group[corresponding].min():.1f}–{group[corresponding].max():.1f}日。"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "这回答的是：在事后确认的长趋势强股样本里，第一次回调到MA30并形成短均线粘合，通常发生在长趋势第几天。它不是收益回测，也不能证明该形态本身是实时可交易买点。",
            "",
            "MA120锚点截至2026-09-30仍右截断，因此MA120整段总长度和事件后剩余天数只能视为截至样本日的下界。",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    daily = pd.read_csv(args.daily_file, compression="infer", dtype={"ts_code": str})
    daily["trade_date"] = daily["trade_date"].map(_compact_date)
    daily = daily.sort_values(["ts_code", "trade_date"], kind="mergesort")
    if daily.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("duplicate stock-date rows")

    features_by_code = {
        str(code): prepare_stock_features(
            group,
            cohesion_threshold_pct=args.cohesion_threshold_pct,
            touch_upper_pct=args.touch_upper_pct,
            close_floor_pct=args.close_floor_pct,
            prior_extension_pct=args.prior_extension_pct,
            extension_lookback=args.extension_lookback,
        )
        for code, group in daily.groupby("ts_code", sort=False)
    }
    anchors = load_anchors(args)
    missing_codes = sorted(set(anchors["ts_code"].astype(str)) - set(features_by_code))
    if missing_codes:
        raise ValueError(f"daily input missing anchor stocks: {missing_codes}")

    all_first_records: list[dict[str, Any]] = []
    strict_records: list[dict[str, Any]] = []
    first_qualifying_records: list[dict[str, Any]] = []
    eligibility: list[dict[str, Any]] = []
    for anchor_dict in anchors.to_dict("records"):
        frame = features_by_code[str(anchor_dict["ts_code"])]
        first_position = None
        first_qualifying_position = None
        if anchor_dict["search_eligible"]:
            first_position = select_first_pullback(
                frame,
                anchor_start_date=anchor_dict["start_date"],
                anchor_end_date=anchor_dict["end_date"],
                signal_column="ma30_pullback_candidate",
            )
            first_qualifying_position = select_first_pullback(
                frame,
                anchor_start_date=anchor_dict["start_date"],
                anchor_end_date=anchor_dict["end_date"],
                signal_column="ma30_pullback_cohesion_candidate",
            )
        first_cohesion_pass = bool(
            first_position is not None
            and frame.iloc[first_position]["ma30_pullback_cohesion_candidate"]
        )
        eligibility.append(
            {
                "ts_code": str(anchor_dict["ts_code"]),
                "stock_name": anchor_dict.get("stock_name", ""),
                "anchor_period": int(anchor_dict["anchor_period"]),
                "anchor_start_date": _compact_date(anchor_dict["start_date"]),
                "anchor_end_date": _compact_date(anchor_dict["end_date"]),
                "anchor_left_censored": bool(anchor_dict["left_censored"]),
                "search_eligible": bool(anchor_dict["search_eligible"]),
                "first_ma30_pullback_date": _compact_date(frame.iloc[first_position]["trade_date"]) if first_position is not None else "",
                "first_pullback_cohesion_pass": first_cohesion_pass,
                "first_qualifying_pullback_date": _compact_date(frame.iloc[first_qualifying_position]["trade_date"]) if first_qualifying_position is not None else "",
                "first_qualifying_skipped_earlier_pullback": bool(
                    first_position is not None
                    and first_qualifying_position is not None
                    and first_qualifying_position > first_position
                ),
            }
        )
        if first_position is not None:
            first_record = build_event_record(
                frame,
                signal_position=first_position,
                anchor=pd.Series(anchor_dict),
            )
            first_record["scope"] = f"MA{int(anchor_dict['anchor_period'])}"
            first_record["first_pullback_cohesion_pass"] = first_cohesion_pass
            all_first_records.append(first_record)
            if first_cohesion_pass:
                strict_records.append(first_record.copy())
        if first_qualifying_position is not None:
            qualifying_record = build_event_record(
                frame,
                signal_position=first_qualifying_position,
                anchor=pd.Series(anchor_dict),
            )
            qualifying_record["scope"] = f"MA{int(anchor_dict['anchor_period'])}"
            qualifying_record["same_as_first_pullback"] = bool(
                first_position == first_qualifying_position
            )
            first_qualifying_records.append(qualifying_record)

    all_first_pullbacks = pd.DataFrame(all_first_records)
    events = pd.DataFrame(strict_records)
    first_qualifying_events = pd.DataFrame(first_qualifying_records)
    if all_first_pullbacks.empty:
        raise ValueError("no MA30 pullbacks found")
    if events.empty:
        raise ValueError("no first-pullback cohesion events found")
    for anchor_period in (60, 120):
        subset = all_first_pullbacks.loc[
            all_first_pullbacks["anchor_period"].eq(anchor_period)
        ]
        if not (
            subset[f"ma{anchor_period}_streak_at_position"].astype(int)
            == subset["anchor_observed_days_at_pullback"].astype(int)
        ).all():
            raise ValueError(f"MA{anchor_period} streak does not match anchor timing")

    unique_first_pullbacks = (
        all_first_pullbacks.sort_values(
            ["pullback_date", "anchor_period"], kind="mergesort"
        )
        .drop_duplicates("ts_code", keep="first")
        .copy()
    )
    unique_events = unique_first_pullbacks.loc[
        unique_first_pullbacks["first_pullback_cohesion_pass"].map(_as_bool)
    ].copy()
    unique_events["scope"] = "UNION_UNIQUE"
    summary_events = pd.concat([events, unique_events], ignore_index=True)
    streaks = summarize_streaks(summary_events)
    first_qualifying_streaks = summarize_streaks(first_qualifying_events)

    coverage_rows: list[dict[str, Any]] = []
    for anchor_period in (60, 120):
        group = anchors.loc[anchors["anchor_period"].eq(anchor_period)]
        eligible_count = int(group["search_eligible"].sum())
        first_pullback_count = int(
            all_first_pullbacks["anchor_period"].eq(anchor_period).sum()
        )
        event_count = int(events["anchor_period"].eq(anchor_period).sum())
        first_qualifying_count = int(
            first_qualifying_events["anchor_period"].eq(anchor_period).sum()
        )
        coverage_rows.append(
            {
                "anchor_period": anchor_period,
                "anchor_count": int(len(group)),
                "left_censored_count": int(group["left_censored"].sum()),
                "eligible_count": eligible_count,
                "first_pullback_count": first_pullback_count,
                "first_pullback_cohesion_count": event_count,
                "first_pullback_cohesion_pct_of_eligible": 100.0 * event_count / eligible_count if eligible_count else float("nan"),
                "first_pullback_cohesion_pct_of_pullbacks": 100.0 * event_count / first_pullback_count if first_pullback_count else float("nan"),
                "first_qualifying_pullback_count": first_qualifying_count,
                "later_qualifying_after_failed_first_count": first_qualifying_count - event_count,
                "event_count": event_count,
                "coverage_pct": 100.0 * event_count / eligible_count if eligible_count else float("nan"),
            }
        )
    coverage = pd.DataFrame(coverage_rows)

    sensitivity = run_sensitivity(
        anchors=anchors,
        features_by_code=features_by_code,
        cohesion_thresholds=(2.0, 3.0, 5.0),
        touch_thresholds=(1.0, 2.0, 3.0),
        extension_thresholds=(3.0, 5.0, 8.0),
        close_floor_pct=args.close_floor_pct,
    )

    outputs: dict[str, pd.DataFrame] = {
        "anchor_eligibility.csv": pd.DataFrame(eligibility),
        "all_first_ma30_pullbacks.csv": all_first_pullbacks,
        "first_pullback_event_detail.csv": events,
        "first_qualifying_pullback_detail.csv": first_qualifying_events,
        "unique_stock_first_pullback_detail.csv": unique_events,
        "coverage_summary.csv": coverage,
        "ma60_ma120_streak_summary.csv": streaks,
        "first_qualifying_streak_summary.csv": first_qualifying_streaks,
        "threshold_sensitivity.csv": sensitivity,
    }
    for name, frame in outputs.items():
        frame.to_csv(output_dir / name, index=False)

    report_path = output_dir / "report.md"
    write_report(
        coverage=coverage,
        streaks=streaks,
        first_qualifying_streaks=first_qualifying_streaks,
        sensitivity=sensitivity,
        output=report_path,
    )
    artifacts = [output_dir / name for name in outputs] + [report_path]
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "study": "first_ma30_price_pullback_with_ma5_ma10_ma30_cohesion_timing",
        "target_window": "2026 YTD through 2026-09-30; anchor episodes use full observed intervals and may include 2025",
        "data_semantics": {
            "price_adjustment": "qfq=True",
            "daily_input": str(Path(args.daily_file).resolve()),
            "data_source_contract": "formal cache previously built through coreClient.data_provider with source=database_only",
            "trading_days_only": True,
        },
        "definitions": {
            "anchor": "selected non-left-censored MA60/MA120 strict rising episode",
            "prior_extension": f"maximum close/own-day-MA30-1 over prior {args.extension_lookback} trading days >= {args.prior_extension_pct}%",
            "ma30_touch": f"signal low/MA30-1 <= {args.touch_upper_pct}% and signal close/MA30-1 >= {args.close_floor_pct}%",
            "short_ma_cohesion": f"(max(MA5,MA10,MA30)-min)/mean <= {args.cohesion_threshold_pct}%",
            "first_pullback": "earliest MA30 touch inside the inclusive long-MA anchor, selected without using cohesion",
            "main_event": "the already-fixed first MA30 pullback also passes short-MA cohesion; a failed first pullback is never skipped",
            "secondary_first_qualifying_event": "earliest day jointly satisfying pullback and cohesion; reported separately because it may skip a failed earlier pullback",
            "unique_stock_rule": "first select each stock's earliest first-pullback event across MA60/MA120 anchors, then apply cohesion; do not deduplicate passed events",
            "long_ma_rising": "MA_t > MA_(t-1); flat, falling, or unavailable resets streak",
        },
        "counts": {
            "daily_rows": int(len(daily)),
            "unique_stocks": int(daily["ts_code"].nunique()),
            "anchor_rows": int(len(anchors)),
            "eligible_anchor_rows": int(anchors["search_eligible"].sum()),
            "first_pullback_rows": int(len(all_first_pullbacks)),
            "first_pullback_cohesion_event_rows": int(len(events)),
            "first_qualifying_event_rows": int(len(first_qualifying_events)),
            "event_unique_stocks": int(unique_events["ts_code"].nunique()),
        },
        "selection_bias_warning": "The pool is selected by future-observed MA60/MA120 duration ranking; this study describes timing in successful long-trend stocks and is not a live-strategy backtest.",
        "artifacts": {
            path.name: {"path": str(path.resolve()), "sha256": _sha256(path)}
            for path in artifacts
        },
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(_clean_json(metadata), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output_dir": str(output_dir.resolve()),
                "anchor_rows": int(len(anchors)),
                "eligible_anchor_rows": int(anchors["search_eligible"].sum()),
                "first_pullback_rows": int(len(all_first_pullbacks)),
                "first_pullback_cohesion_event_rows": int(len(events)),
                "first_qualifying_event_rows": int(len(first_qualifying_events)),
                "event_unique_stocks": int(unique_events["ts_code"].nunique()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
