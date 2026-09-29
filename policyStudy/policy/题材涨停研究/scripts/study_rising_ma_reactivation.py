"""Describe two point-in-time rising-MA reactivation chart templates.

This is an observational scanner, not a trading signal or fitted return model.
The input date is a *close-of-day* cutoff. All rolling windows and theme counts
end on that date; an actual entry would have to occur later.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_kpl_list


EXAMPLES = [
    {"ts_code": "300759.SZ", "trade_date": "20260731", "theme_query": "创新药"},
    {"ts_code": "301080.SZ", "trade_date": "20260914", "theme_query": "创新药"},
    {"ts_code": "688137.SH", "trade_date": "20260914", "theme_query": "创新药"},
]
MA_PERIODS = (5, 10, 20, 30, 60, 120)


def _num(value: object) -> float | None:
    value = float(value) if pd.notna(value) else float("nan")
    return round(value, 4) if np.isfinite(value) else None


def prepare_daily(qfq: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    """Use adjusted prices for geometry and original shares for volume."""
    if qfq.empty or raw.empty:
        return pd.DataFrame()
    raw_vol = raw[["trade_date", "vol"]].rename(columns={"vol": "raw_vol"}).copy()
    raw_vol.trade_date = raw_vol.trade_date.astype(str)
    out = qfq.copy()
    out.trade_date = out.trade_date.astype(str)
    out = out.merge(raw_vol, on="trade_date", how="left", validate="one_to_one")
    out = out.sort_values("trade_date").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "raw_vol"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    for period in MA_PERIODS:
        out[f"ma{period}"] = out.close.rolling(period, min_periods=period).mean()
    return out


def _spread(row: pd.Series) -> float:
    values = np.array([row[f"ma{n}"] for n in (5, 10, 20, 30)], dtype=float)
    return 100 * (values.max() / values.min() - 1) if np.all(np.isfinite(values)) else np.nan


def describe(frame: pd.DataFrame, trade_date: str) -> dict:
    """Extract only data available by the named close, even if frame has a tail."""
    frame = frame.loc[frame.trade_date.astype(str) <= str(trade_date)].copy()
    if frame.empty or str(frame.iloc[-1].trade_date) != str(trade_date):
        return {"trade_date": trade_date, "status": "missing_day"}
    if len(frame) < 145:
        return {"trade_date": trade_date, "status": "insufficient_history", "bars": len(frame)}
    i = len(frame) - 1
    bar = frame.iloc[i]
    # Reserve at least six sessions after the impulse peak: the current large
    # candle must not become its own historical impulse.
    search = frame.iloc[i - 35:i - 5]
    peak_i = int(search.high.idxmax())
    peak = frame.iloc[peak_i]
    before_peak = frame.iloc[max(0, peak_i - 60):peak_i]
    wave_start_i = int(frame.low.iloc[peak_i - 30:peak_i].idxmin())
    pre_wave_volume = frame.raw_vol.iloc[wave_start_i - 20:wave_start_i].mean()
    first_wave_volume = frame.raw_vol.iloc[wave_start_i:peak_i + 1].mean()
    pullback_volume = frame.raw_vol.iloc[peak_i + 1:i].mean()
    whole_wave_volume = frame.raw_vol.iloc[wave_start_i:i + 1].mean()
    peak_volume = frame.raw_vol.iloc[max(0, peak_i - 4):peak_i + 1].mean()
    before_peak_volume = frame.raw_vol.iloc[peak_i - 24:peak_i - 4].mean()
    pre_volume = frame.raw_vol.iloc[i - 3:i].mean()
    prior5_volume = frame.raw_vol.iloc[i - 5:i].mean()
    pre_low = before_peak.low.min()
    prior_high = before_peak.high.max()
    post_peak_low = frame.low.iloc[peak_i + 1:i + 1].min()
    peak_spread = _spread(peak)
    spread = _spread(bar)
    previous_close = float(frame.close.iloc[i - 1])
    previous3_high = float(frame.high.iloc[i - 3:i].max())
    slopes = {f"ma{n}_slope{lag}_pct": 100 * (bar[f"ma{n}"] /
              frame.iloc[i - lag][f"ma{n}"] - 1)
              for n, lag in ((20, 10), (30, 10), (60, 10), (120, 20))}
    persistence = {f"ma{n}_rising_days_last15": int(
        (frame[f"ma{n}"].diff().iloc[i - 14:i + 1] > 0).sum())
        for n in (30, 60, 120)}
    values = {
        "trade_date": trade_date, "status": "ok", "close": float(bar.close),
        "first_wave_start_date": str(frame.iloc[wave_start_i].trade_date),
        "peak_date": str(peak.trade_date), "peak_age_bars": i - peak_i,
        "peak_high": float(peak.high),
        "impulse_from_prior_60d_low_pct": 100 * (peak.high / pre_low - 1),
        "peak_vs_prior_60d_high_pct": 100 * (peak.high / prior_high - 1),
        "pullback_from_peak_pct": 100 * (post_peak_low / peak.high - 1),
        "ma5_30_spread_pct": spread,
        "ma5_30_spread_vs_peak": spread / peak_spread if peak_spread > 0 else np.nan,
        "pre3_volume_vs_peak5": pre_volume / peak_volume if peak_volume > 0 else np.nan,
        "impulse_volume_vs_pre20": peak_volume / before_peak_volume
        if before_peak_volume > 0 else np.nan,
        "first_wave_volume_vs_pre": first_wave_volume / pre_wave_volume
        if pre_wave_volume > 0 else np.nan,
        "pullback_volume_vs_pre": pullback_volume / pre_wave_volume
        if pre_wave_volume > 0 else np.nan,
        "whole_wave_volume_vs_pre": whole_wave_volume / pre_wave_volume
        if pre_wave_volume > 0 else np.nan,
        "day_volume_vs_prior5": bar.raw_vol / prior5_volume if prior5_volume > 0 else np.nan,
        "low_to_ma20_pct": 100 * (bar.low / bar.ma20 - 1),
        "close_to_ma20_pct": 100 * (bar.close / bar.ma20 - 1),
        "low_to_ma30_pct": 100 * (bar.low / bar.ma30 - 1),
        "close_to_ma30_pct": 100 * (bar.close / bar.ma30 - 1),
        "day_return_pct": 100 * (bar.close / previous_close - 1),
        "close_over_previous3_high": bool(bar.close > previous3_high),
        **slopes,
        **persistence,
    }
    # Separate two visually similar but temporally different structures.
    # Gates are descriptive pilot bounds chosen after viewing three examples;
    # they are not calibrated probabilities or tested profitability rules.
    early = {
        "impulse": 20 <= values["impulse_from_prior_60d_low_pct"] <= 200,
        "stage_new_high": values["peak_vs_prior_60d_high_pct"] >= 0,
        "impulse_volume_expanded": values["impulse_volume_vs_pre20"] >= 1.4,
        "first_wave_volume_sustained": values["first_wave_volume_vs_pre"] >= 1.5,
        "pullback_volume_sustained": values["pullback_volume_vs_pre"] >= 1.5,
        "pullback": -35 <= values["pullback_from_peak_pct"] <= -5,
        "ma20_retest": -8 <= values["low_to_ma20_pct"] <= 3 and values["close_to_ma20_pct"] >= -1,
        "mid_mas_rising": all(values[f"ma{n}_slope10_pct"] > 0 for n in (20, 30, 60)),
        "mid_ma_rise_persistent": all(values[f"ma{n}_rising_days_last15"] >= 12
                                       for n in (30, 60)),
        "long_ma_rising": values["ma120_slope20_pct"] > 0,
        "volume_eased": values["pre3_volume_vs_peak5"] <= 1.1,
    }
    coil = {
        "impulse": 20 <= values["impulse_from_prior_60d_low_pct"] <= 250,
        "stage_new_high": values["peak_vs_prior_60d_high_pct"] >= 0,
        "impulse_volume_expanded": values["impulse_volume_vs_pre20"] >= 1.4,
        "first_wave_volume_sustained": values["first_wave_volume_vs_pre"] >= 1.5,
        "pullback_volume_sustained": values["pullback_volume_vs_pre"] >= 1.5,
        "pullback": -45 <= values["pullback_from_peak_pct"] <= -8,
        "ma30_retest": -10 <= values["low_to_ma30_pct"] <= 5 and values["close_to_ma30_pct"] >= -5,
        "mid_and_long_mas_rising": all(values[f"ma{n}_slope{lag}_pct"] > 0
                                        for n, lag in ((30, 10), (60, 10), (120, 20))),
        "mid_ma_rise_persistent": all(values[f"ma{n}_rising_days_last15"] >= 12
                                       for n in (30, 60)),
        "ma_convergence": values["ma5_30_spread_vs_peak"] <= .5,
        "base_volume_not_above_impulse": values["pre3_volume_vs_peak5"] <= 1.15,
        "reversal_close": values["day_return_pct"] >= 5 and values["close_over_previous3_high"],
        "reversal_volume": values["day_volume_vs_prior5"] >= 1.15,
    }
    early = {key: bool(value) for key, value in early.items()}
    coil = {key: bool(value) for key, value in coil.items()}
    values["ma20_pullback_gates"] = early
    values["ma30_coil_restart_gates"] = coil
    values["ma20_pullback_hits"] = sum(early.values())
    values["ma30_coil_restart_hits"] = sum(coil.values())
    values["template_observation"] = (
        "ma30_coil_restart" if all(coil.values()) else
        "ma20_pullback_zone" if all(early.values()) else "unmatched"
    )
    return {key: _num(value) if isinstance(value, (float, np.floating)) else value
            for key, value in values.items()}


def theme_evidence(kpl: pd.DataFrame, trade_date: str, keyword: str) -> dict:
    """Count unique non-ST limit-up stocks mentioning an exact KPL theme token."""
    if kpl.empty or not keyword:
        return {"theme_query": keyword, "theme_status": "not_queried"}
    rows = kpl.copy()
    rows.trade_date = rows.trade_date.astype(str)
    rows = rows[rows.trade_date <= str(trade_date)]
    market_days = sorted(rows.trade_date.unique())[-20:]
    rows = rows[~rows.name.fillna("").str.contains("ST|退", case=False) &
                ~rows.ts_code.astype(str).str.endswith(".BJ") &
                ~rows.theme.fillna("").str.contains("ST板块|ST摘帽|次新")]
    rows = rows[rows.theme.fillna("").map(
        lambda s: keyword in {token.strip() for token in str(s).replace("、", ",").split(",")})]
    counts = rows.groupby("trade_date").ts_code.nunique()
    recent = counts.reindex(market_days, fill_value=0)
    return {"theme_query": keyword, "theme_status": "kpl_auxiliary_or_primary_tag",
            "theme_limitups_today": int(counts.get(str(trade_date), 0)),
            "theme_limitups_recent_max": int(recent.max()) if len(recent) else 0,
            "theme_last_limitup_date": str(counts.index[-1]) if len(counts) else None}


def plot_template(frame: pd.DataFrame, result: dict, path: Path) -> None:
    """Daily candles, actual volume, MAs, prior peak and decision-date marker."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    view = frame.tail(75).reset_index(drop=True)
    fig, (ax, volume_ax) = plt.subplots(
        2, 1, figsize=(14, 7.5), sharex=True,
        gridspec_kw={"height_ratios": [4, 1]}, layout="constrained")
    x = np.arange(len(view))
    for idx, row in view.iterrows():
        color = "#cc3333" if row.close >= row.open else "#219653"
        ax.vlines(idx, row.low, row.high, color=color, lw=.85)
        ax.add_patch(Rectangle((idx - .33, min(row.open, row.close)), .66,
                               max(abs(row.close - row.open), .015),
                               facecolor=color, edgecolor=color, alpha=.9))
        volume_ax.bar(idx, row.raw_vol, width=.7, color=color, alpha=.42)
    for period, color in ((20, "#3977bc"), (30, "#bb5ca9"),
                          (60, "#9a692e"), (120, "#62616e")):
        ax.plot(x, view[f"ma{period}"], color=color, lw=1.25, label=f"MA{period}")
    peak_pos = view.index[view.trade_date.eq(result["peak_date"])]
    if len(peak_pos):
        peak_x = int(peak_pos[0])
        ax.scatter([peak_x], [result["peak_high"]], marker="v", s=75,
                   color="#6d28d9", zorder=5)
        ax.annotate(f"prior peak {result['peak_date']}",
                    (peak_x, result["peak_high"]), xytext=(7, 10),
                    textcoords="offset points", color="#6d28d9", fontsize=8)
        ax.axvspan(peak_x, len(view) - 1, color="#f6dfec", alpha=.16)
    ax.axvline(len(view) - 1, color="#6d28d9", linestyle=":", lw=1)
    volume_ax.axvline(len(view) - 1, color="#6d28d9", linestyle=":", lw=1)
    ax.set_title(f"{result['ts_code']} through {result['trade_date']}  |  "
                 f"{result['template_observation']} (close-of-day)")
    ax.set_ylabel("QFQ price")
    volume_ax.set_ylabel("Actual vol")
    ax.grid(alpha=.15)
    ax.legend(loc="upper left", ncol=4, fontsize=8)
    ticks = sorted(set(list(range(0, len(view), 10)) + [len(view) - 1]))
    volume_ax.set_xticks(ticks, [view.trade_date.iloc[j] for j in ticks], rotation=35)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(items: list[dict], output: Path, *, charts_dir: Path | None = None) -> list[dict]:
    results = []
    for item in items:
        code, date = str(item["ts_code"]), str(item["trade_date"])
        if len(date) != 8 or not date.isdigit():
            raise ValueError(f"trade_date must be YYYYMMDD: {date}")
        start = (pd.Timestamp(date) - pd.DateOffset(months=13)).strftime("%Y%m%d")
        qfq = get_day(ts_code=code, start_date=start, end_date=date,
                      qfq=True, source="database_only")
        raw = get_day(ts_code=code, start_date=start, end_date=date,
                      qfq=False, source="database_only")
        frame = prepare_daily(qfq, raw)
        result = describe(frame, date)
        result["ts_code"] = code
        if charts_dir is not None and result.get("status") == "ok":
            chart = charts_dir / f"template_{code}_{date}.jpg"
            plot_template(frame, result, chart)
            result["chart"] = str(chart.resolve())
        keyword = str(item.get("theme_query", ""))
        if keyword:
            start_theme = (pd.Timestamp(date) - pd.Timedelta(days=55)).strftime("%Y%m%d")
            kpl = get_kpl_list(start_date=start_theme, end_date=date, tags="涨停",
                               source="database_only")
            result.update(theme_evidence(kpl, date, keyword))
        results.append(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="CSV: ts_code,trade_date,theme_query(optional)")
    parser.add_argument("--output", type=Path, default=Path("outputs/rising_ma_reactivation/examples.json"))
    parser.add_argument("--charts-dir", type=Path, help="Optional daily template drawings")
    args = parser.parse_args()
    items = pd.read_csv(args.input, dtype=str).fillna("").to_dict("records") if args.input else EXAMPLES
    print(json.dumps(run(items, args.output, charts_dir=args.charts_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
