"""Find long-trend-pool analogs of 近岸蛋白's Aug-Sep 2026 pattern."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[5]
DEFAULT_DAILY = ROOT / "outputs/ma60_ma120_top100_ppt/2026_through_20260930_qfq/daily_input_qfq.csv.gz"
DEFAULT_MA60 = ROOT / "outputs/ma60_ma120_top100_themes/2026_through_20260930_qfq/ma60_top100_with_ties_named.csv"
DEFAULT_MA120 = ROOT / "outputs/ma60_ma120_top100_themes/2026_through_20260930_qfq/ma120_top100_with_ties_named.csv"
DEFAULT_OUTPUT = ROOT / "outputs/nearshore_protein_pattern_analogs/2026_through_20260930_qfq"
REFERENCE_CODE = "688137.SH"
REFERENCE_NAME = "近岸蛋白"
REFERENCE_TOUCH_DATE = "20260911"
REFERENCE_WINDOW_START = "20260803"
REFERENCE_WINDOW_END = "20260930"
DATA_END = "20260930"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-file", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--ma60-file", type=Path, default=DEFAULT_MA60)
    parser.add_argument("--ma120-file", type=Path, default=DEFAULT_MA120)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data-end", default=DATA_END)
    return parser.parse_args(argv)


def _strict_rising_streak(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    flags = values.gt(values.shift(1)) & values.notna() & values.shift(1).notna()
    streak: list[int] = []
    current = 0
    for flag in flags:
        current = current + 1 if bool(flag) else 0
        streak.append(current)
    return pd.Series(streak, index=series.index, dtype=int)


def prepare_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute the scale-free price, volume and moving-average features."""
    result = frame.copy()
    result["trade_date"] = result["trade_date"].astype(str).str.replace("-", "", regex=False)
    result = result.sort_values("trade_date").drop_duplicates("trade_date", keep="last").reset_index(drop=True)
    close = pd.to_numeric(result["close"], errors="coerce")
    for period in (5, 10, 20, 30, 60, 120):
        result[f"ma{period}"] = close.rolling(period, min_periods=period).mean()
    short = result[["ma5", "ma10", "ma30"]]
    result["ma5_ma10_ma30_spread_pct"] = (
        short.max(axis=1) / short.min(axis=1) - 1.0
    ) * 100.0
    for period in (30, 60, 120):
        result[f"ma{period}_rising_streak"] = _strict_rising_streak(result[f"ma{period}"])
    result["low_vs_ma30_pct"] = (
        pd.to_numeric(result["low"], errors="coerce") / result["ma30"] - 1.0
    ) * 100.0
    result["close_vs_ma30_pct"] = (close / result["ma30"] - 1.0) * 100.0
    volume = pd.to_numeric(result["vol"], errors="coerce")
    result["volume_ratio_to_prior20_median"] = volume / volume.shift(1).rolling(
        20, min_periods=20
    ).median()
    return result


def locate_recent_wave(
    frame: pd.DataFrame,
    *,
    touch_position: int,
    peak_min_lag: int = 5,
    peak_max_lag: int = 20,
    wave_lookback: int = 30,
    wave_min_gap: int = 5,
) -> dict[str, int] | None:
    """Locate the recent close peak and the preceding close trough."""
    peak_start = touch_position - peak_max_lag
    peak_end = touch_position - peak_min_lag
    if peak_start < 0 or peak_end < peak_start:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce")
    peak_window = close.iloc[peak_start : peak_end + 1]
    if peak_window.dropna().empty:
        return None
    peak_position = int(peak_window.idxmax())
    start_from = max(0, peak_position - wave_lookback)
    start_to = peak_position - wave_min_gap
    if start_to < start_from:
        return None
    start_window = close.iloc[start_from : start_to + 1]
    if start_window.dropna().empty:
        return None
    wave_start_position = int(start_window.idxmin())
    return {
        "wave_start_position": wave_start_position,
        "peak_position": peak_position,
    }


def _future_close_return(
    close: pd.Series,
    position: int,
    horizon: int,
) -> float:
    future_position = position + horizon
    if future_position >= len(close):
        return float("nan")
    return float((close.iloc[future_position] / close.iloc[position] - 1.0) * 100.0)


def evaluate_touch_candidate(
    frame: pd.DataFrame,
    *,
    touch_position: int,
    touch_upper_pct: float = 2.0,
    close_floor_pct: float = -3.0,
    short_spread_max_pct: float = 8.0,
    ma30_min_rising_streak: int = 10,
    first_wave_min_gain_pct: float = 30.0,
    pullback_min_drawdown_pct: float = -40.0,
    pullback_max_drawdown_pct: float = -5.0,
    spread_contraction_min_pct: float = 30.0,
    require_ma30_above_wave_start: bool = True,
) -> dict[str, Any] | None:
    """Return metrics when a day matches the Nearshore-style structure."""
    if touch_position < 0 or touch_position >= len(frame):
        raise IndexError(touch_position)
    row = frame.iloc[touch_position]
    required = (
        "ma30",
        "ma30_rising_streak",
        "ma5_ma10_ma30_spread_pct",
        "low_vs_ma30_pct",
        "close_vs_ma30_pct",
    )
    if any(pd.isna(row.get(column)) for column in required):
        return None
    if int(row["ma30_rising_streak"]) < ma30_min_rising_streak:
        return None
    if float(row["low_vs_ma30_pct"]) > touch_upper_pct:
        return None
    if float(row["close_vs_ma30_pct"]) < close_floor_pct:
        return None
    if float(row["ma5_ma10_ma30_spread_pct"]) > short_spread_max_pct:
        return None

    wave = locate_recent_wave(frame, touch_position=touch_position)
    if wave is None:
        return None
    start_position = wave["wave_start_position"]
    peak_position = wave["peak_position"]
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["vol"], errors="coerce")
    start_close = float(close.iloc[start_position])
    peak_close = float(close.iloc[peak_position])
    touch_close = float(close.iloc[touch_position])
    if min(start_close, peak_close, touch_close) <= 0:
        return None
    first_wave_gain_pct = (peak_close / start_close - 1.0) * 100.0
    pullback_drawdown_pct = (touch_close / peak_close - 1.0) * 100.0
    if first_wave_gain_pct < first_wave_min_gain_pct:
        return None
    if not (
        pullback_min_drawdown_pct
        <= pullback_drawdown_pct
        <= pullback_max_drawdown_pct
    ):
        return None
    peak_spread = float(frame.iloc[peak_position]["ma5_ma10_ma30_spread_pct"])
    touch_spread = float(row["ma5_ma10_ma30_spread_pct"])
    if not np.isfinite(peak_spread) or peak_spread <= 0:
        return None
    spread_contraction_pct = (1.0 - touch_spread / peak_spread) * 100.0
    if spread_contraction_pct < spread_contraction_min_pct:
        return None
    start_ma30 = float(frame.iloc[start_position]["ma30"])
    touch_ma30 = float(row["ma30"])
    if not np.isfinite(start_ma30) or start_ma30 <= 0:
        return None
    if require_ma30_above_wave_start and touch_ma30 <= start_ma30:
        return None

    wave_volume = float(volume.iloc[start_position + 1 : peak_position + 1].mean())
    pullback_volume = float(volume.iloc[peak_position + 1 : touch_position + 1].mean())
    event: dict[str, Any] = {
        "wave_start_position": int(start_position),
        "peak_position": int(peak_position),
        "touch_position": int(touch_position),
        "wave_start_date": str(frame.iloc[start_position]["trade_date"]),
        "peak_date": str(frame.iloc[peak_position]["trade_date"]),
        "touch_date": str(row["trade_date"]),
        "wave_start_close": start_close,
        "peak_close": peak_close,
        "touch_close": touch_close,
        "first_wave_gain_pct": float(first_wave_gain_pct),
        "wave_days": int(peak_position - start_position),
        "pullback_drawdown_pct": float(pullback_drawdown_pct),
        "correction_days": int(touch_position - peak_position),
        "touch_low_vs_ma30_pct": float(row["low_vs_ma30_pct"]),
        "touch_close_vs_ma30_pct": float(row["close_vs_ma30_pct"]),
        "touch_short_ma_spread_pct": touch_spread,
        "peak_short_ma_spread_pct": peak_spread,
        "spread_contraction_pct": float(spread_contraction_pct),
        "ma30_rise_start_to_touch_pct": float((touch_ma30 / start_ma30 - 1.0) * 100.0),
        "pullback_volume_vs_wave_pct": float(pullback_volume / wave_volume * 100.0) if wave_volume > 0 else float("nan"),
        "ma30_rising_streak_at_touch": int(row["ma30_rising_streak"]),
        "ma60_rising_streak_at_touch": int(row.get("ma60_rising_streak", 0)),
        "ma120_rising_streak_at_touch": int(row.get("ma120_rising_streak", 0)),
    }
    for horizon in (1, 5, 10, 20):
        event[f"next_{horizon}d_return_pct"] = _future_close_return(
            close, touch_position, horizon
        )
    future5 = close.iloc[touch_position + 1 : touch_position + 6]
    event["next_5d_max_close_return_pct"] = (
        float((future5.max() / touch_close - 1.0) * 100.0)
        if len(future5)
        else float("nan")
    )
    return event


SIMILARITY_FEATURE_SCALES: dict[str, float] = {
    "first_wave_gain_pct": 60.0,
    "wave_days": 10.0,
    "pullback_drawdown_pct": 12.0,
    "correction_days": 6.0,
    "touch_low_vs_ma30_pct": 3.0,
    "touch_close_vs_ma30_pct": 3.0,
    "touch_short_ma_spread_pct": 4.0,
    "spread_contraction_pct": 30.0,
    "ma30_rise_start_to_touch_pct": 30.0,
    "pullback_volume_vs_wave_pct": 50.0,
}


def event_path_vector(
    frame: pd.DataFrame,
    event: dict[str, Any],
    *,
    points: int = 32,
) -> np.ndarray:
    """Resample and amplitude-normalize the pre-touch close path."""
    start = int(event["wave_start_position"])
    touch = int(event["touch_position"])
    close = pd.to_numeric(frame.iloc[start : touch + 1]["close"], errors="coerce").to_numpy(dtype=float)
    if len(close) < 2 or not np.isfinite(close).all() or close[0] <= 0:
        raise ValueError("invalid event close path")
    log_path = np.log(close / close[0])
    amplitude = float(np.max(log_path))
    if amplitude <= 0:
        raise ValueError("event path has no positive wave amplitude")
    source_x = np.linspace(0.0, 1.0, len(log_path))
    target_x = np.linspace(0.0, 1.0, points)
    return np.interp(target_x, source_x, log_path / amplitude)


def score_event_similarity(
    reference_event: dict[str, Any],
    reference_path: np.ndarray,
    candidate_event: dict[str, Any],
    candidate_path: np.ndarray,
) -> float:
    """Return a 0-100 pre-touch structural similarity score."""
    deviations: list[float] = []
    for feature, scale in SIMILARITY_FEATURE_SCALES.items():
        reference_value = float(reference_event[feature])
        candidate_value = float(candidate_event[feature])
        if np.isfinite(reference_value) and np.isfinite(candidate_value):
            deviations.append(abs(candidate_value - reference_value) / scale)
    if not deviations:
        raise ValueError("no comparable event features")
    reference_array = np.asarray(reference_path, dtype=float)
    candidate_array = np.asarray(candidate_path, dtype=float)
    if reference_array.shape != candidate_array.shape:
        raise ValueError("path vectors must have the same shape")
    path_rmse = float(np.sqrt(np.mean((candidate_array - reference_array) ** 2)))
    distance = 0.7 * float(np.mean(deviations)) + 2.0 * path_rmse
    return float(100.0 * np.exp(-distance))


def scan_structural_events(
    frame: pd.DataFrame,
    *,
    active_periods_by_date: dict[str, tuple[int, ...]],
    ts_code: str,
    stock_name: str,
    cooldown_days: int = 10,
    require_ma30_above_wave_start: bool = True,
) -> list[dict[str, Any]]:
    """Scan active long-MA intervals and keep the first touch per cluster."""
    events: list[dict[str, Any]] = []
    last_touch_position: int | None = None
    for position, row in frame.reset_index(drop=True).iterrows():
        trade_date = str(row["trade_date"]).replace("-", "")
        periods = tuple(sorted(set(active_periods_by_date.get(trade_date, ()))))
        if not periods:
            continue
        event = evaluate_touch_candidate(
            frame,
            touch_position=int(position),
            require_ma30_above_wave_start=require_ma30_above_wave_start,
        )
        if event is None:
            continue
        if (
            last_touch_position is not None
            and int(position) - last_touch_position <= cooldown_days
        ):
            continue
        event["ts_code"] = str(ts_code)
        event["stock_name"] = str(stock_name)
        event["anchor_periods"] = "|".join(str(period) for period in periods)
        events.append(event)
        last_touch_position = int(position)
    return events


def same_window_similarity(
    reference: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
) -> dict[str, float | int]:
    """Compare normalized close paths on the same calendar window."""
    def select(frame: pd.DataFrame, value_name: str) -> pd.DataFrame:
        result = frame[["trade_date", "close"]].copy()
        result["trade_date"] = result["trade_date"].astype(str).str.replace("-", "", regex=False)
        result = result.loc[
            result["trade_date"].ge(str(start_date).replace("-", ""))
            & result["trade_date"].le(str(end_date).replace("-", ""))
        ]
        return result.rename(columns={"close": value_name})

    merged = select(reference, "reference_close").merge(
        select(candidate, "candidate_close"), on="trade_date", how="inner"
    )
    if len(merged) < 5:
        return {
            "same_window_similarity_score": float("nan"),
            "path_correlation": float("nan"),
            "path_rmse": float("nan"),
            "amplitude_ratio": float("nan"),
            "common_trading_days": int(len(merged)),
        }
    reference_close = pd.to_numeric(merged["reference_close"], errors="coerce").to_numpy(dtype=float)
    candidate_close = pd.to_numeric(merged["candidate_close"], errors="coerce").to_numpy(dtype=float)
    if (
        not np.isfinite(reference_close).all()
        or not np.isfinite(candidate_close).all()
        or reference_close[0] <= 0
        or candidate_close[0] <= 0
    ):
        raise ValueError("invalid close path")
    reference_path = np.log(reference_close / reference_close[0])
    candidate_path = np.log(candidate_close / candidate_close[0])
    reference_amplitude = float(reference_path.max() - reference_path.min())
    candidate_amplitude = float(candidate_path.max() - candidate_path.min())
    if reference_amplitude <= 0 or candidate_amplitude <= 0:
        score = 0.0
        correlation = float("nan")
        rmse = float("nan")
        amplitude_ratio = float("nan")
    else:
        reference_shape = (reference_path - reference_path.min()) / reference_amplitude
        candidate_shape = (candidate_path - candidate_path.min()) / candidate_amplitude
        correlation = float(np.corrcoef(reference_shape, candidate_shape)[0, 1])
        rmse = float(np.sqrt(np.mean((reference_shape - candidate_shape) ** 2)))
        amplitude_ratio = float(candidate_amplitude / reference_amplitude)
        correlation_component = max(0.0, (correlation + 1.0) / 2.0)
        score = float(
            100.0
            * np.exp(-2.0 * rmse - 0.5 * abs(np.log(amplitude_ratio)))
            * np.sqrt(correlation_component)
        )
    return {
        "same_window_similarity_score": score,
        "path_correlation": correlation,
        "path_rmse": rmse,
        "amplitude_ratio": amplitude_ratio,
        "common_trading_days": int(len(merged)),
        "candidate_window_return_pct": float((candidate_close[-1] / candidate_close[0] - 1.0) * 100.0),
        "reference_window_return_pct": float((reference_close[-1] / reference_close[0] - 1.0) * 100.0),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _load_reference(data_end: str) -> pd.DataFrame:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from coreClient.data_provider import get_day

    daily = get_day(
        ts_code=REFERENCE_CODE,
        start_date="20220101",
        end_date=data_end,
        qfq=True,
        source="database_only",
    )
    if daily.empty:
        raise ValueError(f"no daily data for {REFERENCE_CODE}")
    return prepare_features(daily)


def _build_anchor_membership(
    frame: pd.DataFrame,
    stock_anchors: pd.DataFrame,
) -> dict[str, tuple[int, ...]]:
    dates = frame["trade_date"].astype(str)
    memberships: dict[str, set[int]] = {}
    for anchor in stock_anchors.to_dict("records"):
        start = str(anchor["start_date"]).replace("-", "").split(".")[0]
        end = str(anchor["end_date"]).replace("-", "").split(".")[0]
        for date in dates.loc[dates.ge(start) & dates.le(end)]:
            memberships.setdefault(str(date), set()).add(int(anchor["anchor_period"]))
    return {
        date: tuple(sorted(periods))
        for date, periods in memberships.items()
    }


def _add_stage_fields(event: dict[str, Any], frame: pd.DataFrame) -> None:
    touch = int(event["touch_position"])
    close = pd.to_numeric(frame["close"], errors="coerce")
    touch_close = float(close.iloc[touch])
    tail = close.iloc[touch + 1 :]
    event["days_from_touch_to_cutoff"] = int(len(frame) - 1 - touch)
    event["return_touch_to_cutoff_pct"] = float(
        (close.iloc[-1] / touch_close - 1.0) * 100.0
    )
    event["max_close_return_after_touch_pct"] = (
        float((tail.max() / touch_close - 1.0) * 100.0)
        if len(tail)
        else float("nan")
    )
    days = int(event["days_from_touch_to_cutoff"])
    latest_return = float(event["return_touch_to_cutoff_pct"])
    max_return = float(event["max_close_return_after_touch_pct"])
    if days <= 2:
        stage = "正在回踩MA30"
    elif np.isfinite(max_return) and max_return >= 20.0:
        stage = "已出现明显二次上升"
    elif latest_return >= 5.0:
        stage = "初步反弹"
    elif latest_return <= -5.0:
        stage = "回踩后继续走弱"
    else:
        stage = "仍在整理"
    event["stage_as_of_cutoff"] = stage


def _configure_matplotlib() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "PingFang SC",
        "Arial Unicode MS",
        "Heiti SC",
        "SimHei",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _plot_event_analogs(
    *,
    reference_event: dict[str, Any],
    top_events: pd.DataFrame,
    frames_by_code: dict[str, pd.DataFrame],
    output: Path,
) -> None:
    plt = _configure_matplotlib()
    rows: list[dict[str, Any]] = [reference_event]
    rows.extend(top_events.head(8).to_dict("records"))
    figure, axes = plt.subplots(3, 3, figsize=(16, 12), constrained_layout=True)
    for axis, event in zip(axes.flat, rows):
        code = str(event["ts_code"])
        frame = frames_by_code[code]
        start = int(event["wave_start_position"])
        touch = int(event["touch_position"])
        end = min(len(frame) - 1, touch + 10)
        window = frame.iloc[start : end + 1]
        base = float(window.iloc[0]["close"])
        x = np.arange(start, end + 1) - touch
        axis.plot(x, window["close"] / base * 100.0, color="#D84A3A", linewidth=2.0, label="收盘价")
        axis.plot(x, window["ma30"] / base * 100.0, color="#2E6FBA", linewidth=1.6, label="MA30")
        axis.axvline(0, color="#333333", linestyle="--", linewidth=1.0)
        score = event.get("structural_similarity_score", 100.0)
        title = f"{event['stock_name']} {code}\n触及 {event['touch_date']} · 相似度 {float(score):.1f}"
        axis.set_title(title, fontsize=11)
        axis.grid(alpha=0.2)
        axis.set_xlabel("相对MA30回踩日（交易日）")
        axis.set_ylabel("波段起点=100")
    for axis in axes.flat[len(rows) :]:
        axis.axis("off")
    axes.flat[0].legend(loc="best", fontsize=9)
    figure.suptitle("近岸蛋白模板与候选池结构相似事件（评分仅使用回踩日及以前数据）", fontsize=16)
    figure.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(figure)


def _plot_same_window(
    *,
    reference: pd.DataFrame,
    ranking: pd.DataFrame,
    frames_by_code: dict[str, pd.DataFrame],
    output: Path,
) -> None:
    plt = _configure_matplotlib()
    top = ranking.head(9)
    figure, axes = plt.subplots(3, 3, figsize=(16, 12), constrained_layout=True)
    reference_window = reference.loc[
        reference["trade_date"].ge(REFERENCE_WINDOW_START)
        & reference["trade_date"].le(REFERENCE_WINDOW_END),
        ["trade_date", "close"],
    ].copy()
    reference_window["reference_normalized"] = (
        reference_window["close"] / reference_window.iloc[0]["close"] * 100.0
    )
    for axis, row in zip(axes.flat, top.to_dict("records")):
        code = str(row["ts_code"])
        candidate = frames_by_code[code]
        current = candidate.loc[
            candidate["trade_date"].ge(REFERENCE_WINDOW_START)
            & candidate["trade_date"].le(REFERENCE_WINDOW_END),
            ["trade_date", "close"],
        ].copy()
        merged = reference_window.merge(current, on="trade_date", how="inner")
        merged["candidate_normalized"] = merged["close_y"] / merged.iloc[0]["close_y"] * 100.0
        x = np.arange(len(merged))
        axis.plot(x, merged["reference_normalized"], color="#777777", linestyle="--", linewidth=1.6, label="近岸蛋白")
        axis.plot(x, merged["candidate_normalized"], color="#D84A3A", linewidth=2.0, label=str(row["stock_name"]))
        axis.set_title(f"{row['stock_name']} {code}\n同期相似度 {float(row['same_window_similarity_score']):.1f}", fontsize=11)
        axis.grid(alpha=0.2)
        axis.set_xlabel("2026-08-03至09-30交易日序号")
        axis.set_ylabel("窗口首日=100")
    axes.flat[0].legend(loc="best", fontsize=9)
    figure.suptitle("候选池与近岸蛋白2026年8—9月同期走势对比", fontsize=16)
    figure.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(figure)


def _markdown_table(frame: pd.DataFrame, columns: list[tuple[str, str]], limit: int = 10) -> list[str]:
    rows = frame.head(limit)
    lines = [
        "| " + " | ".join(label for _, label in columns) + " |",
        "|" + "|".join("---" if index < 2 else "---:" for index in range(len(columns))) + "|",
    ]
    for row in rows.to_dict("records"):
        cells: list[str] = []
        for key, _ in columns:
            value = row.get(key, "")
            if isinstance(value, float):
                cells.append("" if not np.isfinite(value) else f"{value:.1f}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _write_report(
    *,
    reference_event: dict[str, Any],
    all_events: pd.DataFrame,
    recent_events: pd.DataFrame,
    current_watchlist: pd.DataFrame,
    relaxed_all_events: pd.DataFrame,
    relaxed_recent_events: pd.DataFrame,
    relaxed_current_watchlist: pd.DataFrame,
    best_events: pd.DataFrame,
    same_window: pd.DataFrame,
    pool_size: int,
    output: Path,
) -> None:
    lines = [
        "# 近岸蛋白2026年8—9月走势的候选池相似股扫描",
        "",
        "## 先说结论",
        "",
        f"- 近岸蛋白（{REFERENCE_CODE}）本身不在MA60/MA120长趋势候选池{pool_size}只股票中；本报告把它作为外部模板。",
        f"- 模板回踩日为{reference_event['touch_date']}：第一波涨幅{reference_event['first_wave_gain_pct']:.1f}%，回撤{reference_event['pullback_drawdown_pct']:.1f}%，MA5/10/30最大离差{reference_event['touch_short_ma_spread_pct']:.1f}%，MA60/MA120已连续上涨{int(reference_event['ma60_rising_streak_at_touch'])}/{int(reference_event['ma120_rising_streak_at_touch'])}个交易日。",
        "- 一个重要修正：近岸蛋白在关键回踩日的三线离差是6.5%，会被此前3%粘合阈值漏掉。因此本次相似扫描把‘收敛’放宽到8%，同时要求离差较波峰至少收缩30%。",
        f"- 严格口径在候选池长均线锚点内共找到{len(all_events)}个结构事件，涉及{all_events['ts_code'].nunique() if len(all_events) else 0}只股票；2026年8—9月事件{len(recent_events)}个。",
        f"- 敏感性：若不要求‘回踩日MA30高于波段起点MA30’，事件增至{len(relaxed_all_events)}个、2026年8—9月增至{len(relaxed_recent_events)}个，近期观察名单由{len(current_watchlist)}只增至{len(relaxed_current_watchlist)}只。",
        "",
        "## 结构定义",
        "",
        "1. 近5—20个交易日前出现波峰；波峰前30个交易日内的低点到波峰至少上涨30%。",
        "2. 波峰后回撤5%—40%，当前最低价到MA30上方不超过2%，收盘不低于MA30下方3%。",
        "3. MA30已严格连续上涨至少10日；MA5/10/30最大离差不超过8%，且较波峰收缩至少30%。",
        "4. 为保证整轮第一波—回踩期间的中期趋势没有倒退，回踩日MA30必须高于波段起点MA30。",
        "5. 事件必须位于该股票入选的MA60或MA120长均线上升锚点内；10个交易日内的连续触及只保留第一天。",
        "6. 结构相似度只使用回踩日及以前的数据；回踩后的收益仅作事后描述，不参与排名。",
        "",
        "## 2026年8—9月出现的相似结构",
        "",
    ]
    if recent_events.empty:
        lines.append("无满足完整结构的事件。")
    else:
        lines.extend(
            _markdown_table(
                recent_events,
                [
                    ("stock_name", "股票"),
                    ("ts_code", "代码"),
                    ("touch_date", "回踩日"),
                    ("structural_similarity_score", "结构相似度"),
                    ("first_wave_gain_pct", "第一波%"),
                    ("pullback_drawdown_pct", "回撤%"),
                    ("touch_short_ma_spread_pct", "三线离差%"),
                    ("return_touch_to_cutoff_pct", "截至09-30%"),
                    ("stage_as_of_cutoff", "阶段"),
                ],
            )
        )
    lines.extend(["", "## 截至2026-09-30仍值得继续观察的近期回踩", ""])
    lines.append("筛选规则：回踩发生在最近10个交易日内，且截至样本末日尚未比回踩收盘价跌逾5%；这是观察名单，不是买入信号。")
    lines.append("")
    if current_watchlist.empty:
        lines.append("无。")
    else:
        lines.extend(
            _markdown_table(
                current_watchlist,
                [
                    ("stock_name", "股票"),
                    ("ts_code", "代码"),
                    ("touch_date", "回踩日"),
                    ("structural_similarity_score", "结构相似度"),
                    ("ma60_rising_streak_at_touch", "MA60天数"),
                    ("ma120_rising_streak_at_touch", "MA120天数"),
                    ("return_touch_to_cutoff_pct", "回踩后至09-30%"),
                    ("stage_as_of_cutoff", "阶段"),
                ],
            )
        )
    strict_watch_codes = set(current_watchlist["ts_code"].astype(str)) if len(current_watchlist) else set()
    relaxed_extras = relaxed_current_watchlist.loc[
        ~relaxed_current_watchlist["ts_code"].astype(str).isin(strict_watch_codes)
    ]
    lines.extend(["", "### 放宽整轮MA30上升条件后的补充名单", ""])
    if relaxed_extras.empty:
        lines.append("无新增。")
    else:
        lines.extend(
            _markdown_table(
                relaxed_extras,
                [
                    ("stock_name", "股票"),
                    ("ts_code", "代码"),
                    ("touch_date", "回踩日"),
                    ("ma30_rise_start_to_touch_pct", "起点至回踩MA30变化%"),
                    ("return_touch_to_cutoff_pct", "回踩后至09-30%"),
                ],
            )
        )
    lines.extend(["", "## 全部锚点历史中每只股票的最佳结构近似", ""])
    lines.extend(
        _markdown_table(
            best_events,
            [
                ("stock_name", "股票"),
                ("ts_code", "代码"),
                ("touch_date", "回踩日"),
                ("structural_similarity_score", "结构相似度"),
                ("first_wave_gain_pct", "第一波%"),
                ("pullback_drawdown_pct", "回撤%"),
                ("next_5d_return_pct", "后5日%"),
            ],
        )
    )
    lines.extend(["", "## 与近岸蛋白同一自然窗口（2026-08-03至09-30）的走势相似度", ""])
    lines.extend(
        _markdown_table(
            same_window,
            [
                ("stock_name", "股票"),
                ("ts_code", "代码"),
                ("same_window_similarity_score", "同期相似度"),
                ("path_correlation", "路径相关"),
                ("amplitude_ratio", "振幅比"),
                ("candidate_window_return_pct", "窗口收益%"),
            ],
        )
    )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 结构相似不等于未来会复制近岸蛋白后续涨幅；它只说明第一波、回撤到上升MA30、短均线收敛这几部分相似。",
            "- 股票池本身按事后观察到的超长MA60/MA120上升段筛选，存在选择偏差，不能把命中率当作实时策略绩效。",
            "- 同期相似度要求同一日历窗口同涨同跌，结构事件相似度允许发生在不同日期；二者回答的问题不同。",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    reference = _load_reference(str(args.data_end))
    reference_positions = np.flatnonzero(reference["trade_date"].eq(REFERENCE_TOUCH_DATE).to_numpy())
    if len(reference_positions) != 1:
        raise ValueError("reference touch date missing or duplicated")
    reference_event = evaluate_touch_candidate(
        reference,
        touch_position=int(reference_positions[0]),
    )
    if reference_event is None:
        raise ValueError("reference no longer passes structural definition")
    reference_event.update(
        {
            "ts_code": REFERENCE_CODE,
            "stock_name": REFERENCE_NAME,
            "anchor_periods": "template",
            "structural_similarity_score": 100.0,
        }
    )
    _add_stage_fields(reference_event, reference)
    reference_path = event_path_vector(reference, reference_event)

    daily = pd.read_csv(args.daily_file, compression="infer", dtype={"ts_code": str})
    daily["trade_date"] = daily["trade_date"].astype(str).str.replace("-", "", regex=False)
    ma60 = pd.read_csv(args.ma60_file, dtype={"ts_code": str})
    ma120 = pd.read_csv(args.ma120_file, dtype={"ts_code": str})
    ma60["anchor_period"] = 60
    ma120["anchor_period"] = 120
    anchors = pd.concat([ma60, ma120], ignore_index=True)
    if REFERENCE_CODE in set(daily["ts_code"]):
        raise ValueError("reference unexpectedly belongs to candidate pool")
    name_map = (
        anchors[["ts_code", "stock_name"]]
        .dropna(subset=["ts_code"])
        .drop_duplicates("ts_code")
        .set_index("ts_code")["stock_name"]
        .astype(str)
        .to_dict()
    )
    theme_map = (
        anchors[["ts_code", "primary_theme"]]
        .dropna(subset=["ts_code"])
        .drop_duplicates("ts_code")
        .set_index("ts_code")["primary_theme"]
        .fillna("")
        .astype(str)
        .to_dict()
        if "primary_theme" in anchors.columns
        else {}
    )

    frames_by_code: dict[str, pd.DataFrame] = {REFERENCE_CODE: reference}
    event_records: list[dict[str, Any]] = []
    relaxed_event_records: list[dict[str, Any]] = []
    same_window_records: list[dict[str, Any]] = []
    for code, group in daily.groupby("ts_code", sort=True):
        code = str(code)
        frame = prepare_features(group)
        frames_by_code[code] = frame
        stock_anchors = anchors.loc[anchors["ts_code"].astype(str).eq(code)]
        membership = _build_anchor_membership(frame, stock_anchors)
        events = scan_structural_events(
            frame,
            active_periods_by_date=membership,
            ts_code=code,
            stock_name=name_map.get(code, code),
        )
        for event in events:
            candidate_path = event_path_vector(frame, event)
            event["structural_similarity_score"] = score_event_similarity(
                reference_event,
                reference_path,
                event,
                candidate_path,
            )
            event["primary_theme"] = theme_map.get(code, "")
            _add_stage_fields(event, frame)
            event_records.append(event)
        relaxed_events = scan_structural_events(
            frame,
            active_periods_by_date=membership,
            ts_code=code,
            stock_name=name_map.get(code, code),
            require_ma30_above_wave_start=False,
        )
        for event in relaxed_events:
            candidate_path = event_path_vector(frame, event)
            event["structural_similarity_score"] = score_event_similarity(
                reference_event,
                reference_path,
                event,
                candidate_path,
            )
            event["primary_theme"] = theme_map.get(code, "")
            _add_stage_fields(event, frame)
            relaxed_event_records.append(event)
        same = same_window_similarity(
            reference,
            frame,
            start_date=REFERENCE_WINDOW_START,
            end_date=REFERENCE_WINDOW_END,
        )
        same.update(
            {
                "ts_code": code,
                "stock_name": name_map.get(code, code),
                "primary_theme": theme_map.get(code, ""),
            }
        )
        same_window_records.append(same)

    all_events = pd.DataFrame(event_records)
    if len(all_events):
        all_events = all_events.sort_values(
            ["structural_similarity_score", "touch_date", "ts_code"],
            ascending=[False, False, True],
            kind="mergesort",
        ).reset_index(drop=True)
        all_events.insert(0, "rank", np.arange(1, len(all_events) + 1))
        best_events = (
            all_events.sort_values(
                ["structural_similarity_score", "touch_date", "ts_code"],
                ascending=[False, False, True],
                kind="mergesort",
            )
            .drop_duplicates("ts_code", keep="first")
            .reset_index(drop=True)
        )
        best_events["rank"] = np.arange(1, len(best_events) + 1)
        recent_events = all_events.loc[all_events["touch_date"].ge("20260801")].copy()
        recent_events["recent_rank"] = np.arange(1, len(recent_events) + 1)
    else:
        best_events = all_events.copy()
        recent_events = all_events.copy()

    current_watchlist = (
        recent_events.loc[
            recent_events["days_from_touch_to_cutoff"].le(10)
            & recent_events["return_touch_to_cutoff_pct"].ge(-5.0)
        ]
        .sort_values(
            ["structural_similarity_score", "touch_date", "ts_code"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
        if len(recent_events)
        else recent_events.copy()
    )
    if len(current_watchlist):
        current_watchlist.insert(0, "watch_rank", np.arange(1, len(current_watchlist) + 1))

    relaxed_all_events = pd.DataFrame(relaxed_event_records).sort_values(
        ["structural_similarity_score", "touch_date", "ts_code"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    relaxed_all_events.insert(0, "rank", np.arange(1, len(relaxed_all_events) + 1))
    relaxed_recent_events = relaxed_all_events.loc[
        relaxed_all_events["touch_date"].ge("20260801")
    ].copy()
    relaxed_current_watchlist = (
        relaxed_recent_events.loc[
            relaxed_recent_events["days_from_touch_to_cutoff"].le(10)
            & relaxed_recent_events["return_touch_to_cutoff_pct"].ge(-5.0)
        ]
        .sort_values(
            ["structural_similarity_score", "touch_date", "ts_code"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )
    relaxed_current_watchlist.insert(
        0, "watch_rank", np.arange(1, len(relaxed_current_watchlist) + 1)
    )

    same_window = pd.DataFrame(same_window_records).sort_values(
        ["same_window_similarity_score", "ts_code"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    same_window.insert(0, "rank", np.arange(1, len(same_window) + 1))

    reference_daily_path = output_dir / "reference_688137_qfq_daily.csv.gz"
    reference.to_csv(reference_daily_path, index=False, compression="gzip")
    reference_metrics = pd.DataFrame([reference_event])
    outputs: dict[str, pd.DataFrame] = {
        "reference_metrics.csv": reference_metrics,
        "all_structural_events.csv": all_events,
        "best_structural_analog_per_stock.csv": best_events,
        "recent_aug_sep_structural_analogs.csv": recent_events,
        "current_watchlist_asof_20260930.csv": current_watchlist,
        "sensitivity_without_full_wave_ma30_rise_events.csv": relaxed_all_events,
        "sensitivity_without_full_wave_ma30_rise_watchlist.csv": relaxed_current_watchlist,
        "same_window_similarity.csv": same_window,
    }
    for name, frame in outputs.items():
        frame.to_csv(output_dir / name, index=False)

    event_chart = output_dir / "nearshore_vs_top_structural_analogs.png"
    if len(best_events):
        _plot_event_analogs(
            reference_event=reference_event,
            top_events=(current_watchlist if len(current_watchlist) else recent_events if len(recent_events) else best_events),
            frames_by_code=frames_by_code,
            output=event_chart,
        )
    same_window_chart = output_dir / "nearshore_vs_same_window_top9.png"
    _plot_same_window(
        reference=reference,
        ranking=same_window,
        frames_by_code=frames_by_code,
        output=same_window_chart,
    )
    report_path = output_dir / "report.md"
    _write_report(
        reference_event=reference_event,
        all_events=all_events,
        recent_events=recent_events,
        current_watchlist=current_watchlist,
        relaxed_all_events=relaxed_all_events,
        relaxed_recent_events=relaxed_recent_events,
        relaxed_current_watchlist=relaxed_current_watchlist,
        best_events=best_events,
        same_window=same_window,
        pool_size=int(daily["ts_code"].nunique()),
        output=report_path,
    )

    artifact_paths = [reference_daily_path, report_path, same_window_chart]
    if event_chart.exists():
        artifact_paths.append(event_chart)
    artifact_paths.extend(output_dir / name for name in outputs)
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "study": "nearshore_protein_aug_sep_pattern_analogs_in_long_trend_pool",
        "window": "through 2026-09-30",
        "reference": {
            "ts_code": REFERENCE_CODE,
            "stock_name": REFERENCE_NAME,
            "touch_date": REFERENCE_TOUCH_DATE,
            "same_window": [REFERENCE_WINDOW_START, REFERENCE_WINDOW_END],
            "is_in_candidate_pool": False,
        },
        "data_contract": {
            "price_adjustment": "qfq=True",
            "reference_source": "coreClient.data_provider.get_day(source=database_only)",
            "candidate_source": str(Path(args.daily_file).resolve()),
            "candidate_cache_contract": "formal qfq cache previously built through coreClient.data_provider source=database_only",
            "volume": "raw volume",
            "trading_days_only": True,
        },
        "structure_definition": {
            "recent_peak_lag_trading_days": [5, 20],
            "wave_start_lookback_trading_days": 30,
            "first_wave_min_gain_pct": 30.0,
            "pullback_drawdown_pct": [-40.0, -5.0],
            "touch_low_vs_ma30_max_pct": 2.0,
            "touch_close_vs_ma30_min_pct": -3.0,
            "ma30_min_rising_streak": 10,
            "ma30_at_touch_must_exceed_ma30_at_wave_start": True,
            "ma5_ma10_ma30_max_spread_pct": 8.0,
            "spread_contraction_min_pct": 30.0,
            "event_cooldown_trading_days": 10,
            "must_be_inside_selected_ma60_or_ma120_anchor": True,
        },
        "similarity_no_lookahead": "structural similarity uses only data through the touch date; post-touch fields are descriptive outcomes",
        "counts": {
            "candidate_stocks": int(daily["ts_code"].nunique()),
            "all_structural_events": int(len(all_events)),
            "stocks_with_structural_event": int(all_events["ts_code"].nunique()) if len(all_events) else 0,
            "recent_aug_sep_events": int(len(recent_events)),
            "current_watchlist_asof_20260930": int(len(current_watchlist)),
            "sensitivity_without_full_wave_ma30_rise_events": int(len(relaxed_all_events)),
            "sensitivity_without_full_wave_ma30_rise_recent_events": int(len(relaxed_recent_events)),
            "sensitivity_without_full_wave_ma30_rise_watchlist": int(len(relaxed_current_watchlist)),
            "same_window_ranked_stocks": int(len(same_window)),
        },
        "selection_bias_warning": "The 178-stock pool was selected by future-observed long MA duration; analogs are descriptive, not live-strategy evidence.",
        "artifacts": {
            path.name: {"path": str(path.resolve()), "sha256": _sha256(path)}
            for path in artifact_paths
        },
    }
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(_json_clean(metadata), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output_dir": str(output_dir.resolve()),
                "candidate_stocks": int(daily["ts_code"].nunique()),
                "all_structural_events": int(len(all_events)),
                "stocks_with_events": int(all_events["ts_code"].nunique()) if len(all_events) else 0,
                "recent_events": int(len(recent_events)),
                "current_watchlist": current_watchlist.head(10)[["stock_name", "ts_code", "touch_date", "structural_similarity_score"]].to_dict("records") if len(current_watchlist) else [],
                "relaxed_watchlist": relaxed_current_watchlist.head(15)[["stock_name", "ts_code", "touch_date", "structural_similarity_score"]].to_dict("records"),
                "top_recent": recent_events.head(5)[["stock_name", "ts_code", "touch_date", "structural_similarity_score"]].to_dict("records") if len(recent_events) else [],
                "top_same_window": same_window.head(5)[["stock_name", "ts_code", "same_window_similarity_score"]].to_dict("records"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
