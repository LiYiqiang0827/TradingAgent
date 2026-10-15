"""Freeze and chart a user-selected daily-wave / intraday-coil template.

This is a descriptive development case, not a discovered trade or a return
backtest. Daily input ends at the prior close; each intraday fact is prefix-tested.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT), str(ROOT / "offlineDataManager/scripts")]

from coreClient.data_provider import (
    get_day,
    get_adj_factor,
    get_tradecal,
    get_kpl_list,
    get_stk_limit,
    get_fifteenMin,
    get_oneMin,
)
from study_daily_intraday_pullback import (
    prepare_daily,
    revised_rules,
    relaxed_restart_alignment,
    daily_pool_snapshot,
)
from replay_daily_intraday_pullback import board_events
from study_fifteen_min_ma_coil import Rules, features
from replay_fifteen_min_ma_coil import digest
from plot.plot_daily_kline import (
    MA_COLORS,
    _configure_chinese_font,
    _draw_candles,
    _apply_date_ticks,
)


def spread(values) -> float:
    values = np.asarray(values, dtype=float)
    return (
        float(100 * (values.max() / values.min() - 1))
        if np.isfinite(values).all()
        else np.nan
    )


def wave_metrics(d: pd.DataFrame, launch: str, pullback: str) -> dict:
    """A supplied launch is explicit, never secretly optimized for a gain cap."""
    x = d[d.trade_date.le(pullback)].reset_index(drop=True)
    locations = np.flatnonzero(x.trade_date.eq(launch))
    if len(locations) != 1 or locations[0] < 20:
        raise ValueError("Launch or its 20-day volume baseline is missing")
    i = int(locations[0])
    phase = x.iloc[i:]
    if not x.iloc[i - 20 :].valid_day.all():
        raise ValueError("Gap in the reviewed wave or volume baseline")
    peak_i = int(phase.high.idxmax())
    peak, start, last = x.iloc[peak_i], x.iloc[i], x.iloc[-1]
    base = float(x.raw_vol.iloc[i - 20 : i].mean())
    advance = x.raw_vol.iloc[i : peak_i + 1]
    correction = x.raw_vol.iloc[peak_i + 1 :]
    return dict(
        launch_date=launch,
        launch_close=float(start.raw_close),
        peak_date=str(peak.trade_date),
        peak_raw_high=float(peak.raw_high),
        gain_from_launch_close_pct=float(100 * (peak.high / start.close - 1)),
        gain_from_launch_low_pct=float(100 * (peak.high / start.low - 1)),
        prelaunch_volume_first=str(x.iloc[i - 20].trade_date),
        prelaunch_volume_last=str(x.iloc[i - 1].trade_date),
        baseline_volume=float(base),
        advance_volume_mean=float(advance.mean()),
        correction_volume_mean=float(correction.mean()),
        advance_volume_ratio=float(advance.mean() / base),
        correction_volume_ratio=float(correction.mean() / base),
        correction_min_volume_ratio=float(correction.min() / base),
        correction_vs_advance_ratio=float(correction.mean() / advance.mean()),
        whole_phase_volume_ratio=float(phase.raw_vol.mean() / base),
        pullback_date=pullback,
        pullback_low=float(last.raw_low),
        pullback_close=float(last.raw_close),
        pullback_ma30=float(last.ma30 / last.adj_factor),
        pullback_ma30_rising_days=int(last.rise30),
        pullback_daily_short_spread_pct=spread([last.ma5, last.ma10, last.ma20]),
        atr14_at_peak=float(peak.atr14_pct),
        atr14_at_pullback=float(last.atr14_pct),
    )


def draw_daily(ax, vol, d, w, name):
    shown = d.tail(80).copy().reset_index(drop=True)
    shown["date"] = pd.to_datetime(shown.trade_date)
    factor = float(d.adj_factor.iloc[-1])
    for column in ("open", "high", "low", "close"):
        shown[column] /= factor
    colors = _draw_candles(ax, shown)
    positions = np.arange(len(shown))
    for n in (5, 10, 20, 30, 60, 120):
        ax.plot(
            positions,
            shown[f"ma{n}"] / factor,
            color=MA_COLORS[n],
            label=f"MA{n}",
            lw=1.25,
        )
    vol.bar(positions, shown.raw_vol / 10000, color=colors, width=0.65, alpha=0.85)
    lookup = {day: i for i, day in enumerate(shown.trade_date)}
    for first, last, color in (
        (w["launch_date"], w["peak_date"], "#f59e0b"),
        (w["peak_date"], w["pullback_date"], "#0ea5e9"),
    ):
        for a in (ax, vol):
            a.axvspan(lookup[first] - 0.5, lookup[last] + 0.5, color=color, alpha=0.08)
    short_date = lambda day: pd.Timestamp(day).strftime("%m/%d")
    notes = [
        (
            w["launch_date"],
            w["launch_close"],
            f"{short_date(w['launch_date'])}启动涨停\n启动收盘{w['launch_close']:.2f}",
            (-75, 40),
        ),
        (
            w["peak_date"],
            w["peak_raw_high"],
            f"首波最高：{short_date(w['peak_date'])} {w['peak_raw_high']:.2f}\n按启动收盘计 +{w['gain_from_launch_close_pct']:.2f}%",
            (-110, 28),
        ),
        (
            w["pullback_date"],
            w["pullback_low"],
            f"{short_date(w['pullback_date'])}低{w['pullback_low']:.2f} / MA30 {w['pullback_ma30']:.2f}\n"
            f"MA30连升{w['pullback_ma30_rising_days']}日；短组{w['pullback_daily_short_spread_pct']:.2f}%",
            (-120, -63),
        ),
    ]
    reversals = shown[
        shown.trade_date.gt(w["pullback_date"])
        & shown.close.ge(shown.open * 1.01)
        & shown.raw_vol.ge(w["baseline_volume"] * 1.5)
    ]
    if len(reversals):
        b = reversals.iloc[0]
        notes.append(
            (
                str(b.trade_date),
                float(b.close),
                f"{short_date(b.trade_date)}放量阳线\n回踩之后才可见的转强",
                (25, 28),
            )
        )
    for day, price, label, offset in notes:
        if day not in lookup:
            continue
        ax.scatter(lookup[day], price, s=60, color="#7c3aed", zorder=8)
        ax.annotate(
            label,
            (lookup[day], price),
            xytext=offset,
            textcoords="offset points",
            fontsize=9,
            arrowprops=dict(arrowstyle="->", color="#7c3aed"),
            bbox=dict(facecolor="white", alpha=0.9, edgecolor="none"),
        )
    vol.axhline(
        w["baseline_volume"] / 10000,
        color="#64748b",
        ls="--",
        lw=1.3,
        label=f"启动前20日均量；首波{w['advance_volume_ratio']:.2f}倍 / 回调{w['correction_volume_ratio']:.2f}倍",
    )
    vol.legend(loc="upper left", fontsize=8)
    ax.set_title(
        f"{name} 日线：截至{short_date(d.trade_date.iloc[-1])}收盘，未使用下一交易日完整日K",
        fontsize=13,
    )
    ax.set_ylabel("前复权价格（元）")
    vol.set_ylabel("成交量（万手）")
    ax.legend(loc="upper left", ncol=6, fontsize=8)
    ax.set_xlim(-1, len(shown) + 6)
    vol.set_xlim(ax.get_xlim())
    ax.tick_params(axis="x", labelbottom=False)
    _apply_date_ticks(vol, shown.date)
    return len(shown)


def draw_minutes(ax, vol, f, name):
    shown = f.tail(54).copy().reset_index(drop=True)
    factor = float(f.adj_factor.dropna().iloc[0]) / float(
        f.adj_factor.dropna().iloc[-1]
    )
    for column in ("open", "high", "low", "close"):
        shown[column] = shown[f"p_{column}"] * factor
    colors = _draw_candles(ax, shown)
    positions = np.arange(len(shown))
    for n in (5, 10, 20, 30, 60, 120):
        ax.plot(
            positions,
            shown[f"ma{n}"] * factor,
            color=MA_COLORS[n],
            label=f"MA{n}",
            lw=1.3,
        )
    vol.bar(positions, shown.vol / 10000, color=colors, width=0.65, alpha=0.85)
    firsts = shown.groupby(shown.datetime.dt.normalize(), sort=True).head(1)
    vol.set_xticks(firsts.index)
    vol.set_xticklabels(
        firsts.datetime.dt.strftime("%m/%d 09:45"), rotation=20, ha="right"
    )
    notes = []
    last_day = f.datetime.iloc[-1].normalize()
    for clock, offset in (
        ("09:45", (-190, -65)),
        ("10:15", (-170, 50)),
        ("10:45", (30, -65)),
    ):
        stamp = pd.Timestamp(f"{last_day:%Y-%m-%d} {clock}")
        row = f[f.datetime.eq(stamp)]
        if row.empty:
            continue
        b = row.iloc[0]
        if clock == "09:45":
            label = f"{clock}短组{b.coil30_pct:.2f}%：核对点\nMA60组{b.coil_with_ma60_pct:.2f}%，新分支不约束"
        else:
            state = (
                "短四线排列且上升"
                if relaxed_restart_alignment(b)
                else "短四线尚未排列且上升"
            )
            label = (
                f"{clock}实体{b.body_pct:+.2f}% / 量比{b.volume_multiple8:.2f}\n{state}"
            )
        notes.append((str(stamp), label, offset))
    for stamp, label, offset in notes:
        matches = shown.index[shown.datetime.eq(pd.Timestamp(stamp))]
        if len(matches) != 1:
            continue
        i = int(matches[0])
        price = float(shown.close.iloc[i])
        ax.axvline(i, color="#7c3aed", ls="--", alpha=0.6, lw=0.9)
        ax.scatter(i, price, s=60, color="#7c3aed", zorder=8)
        ax.annotate(
            label,
            (i, price),
            xytext=offset,
            textcoords="offset points",
            fontsize=9,
            arrowprops=dict(arrowstyle="->", color="#7c3aed"),
            bbox=dict(facecolor="white", alpha=0.92, edgecolor="none"),
        )
    for a in (ax, vol):
        for i in firsts.index:
            a.axvline(i - 0.5, color="#94a3b8", alpha=0.25, lw=0.7)
    ax.legend(loc="upper left", ncol=6, fontsize=8)
    ax.set_title(
        f"{name} 15分钟：逐根核对至{f.datetime.iloc[-1]:%m/%d %H:%M}；标记均为观察点",
        fontsize=13,
    )
    ax.set_ylabel("前复权价格（元）")
    vol.set_ylabel("成交量（万股）")
    ax.set_xlim(-1, len(shown) + 8)
    vol.set_xlim(ax.get_xlim())
    ax.tick_params(axis="x", labelbottom=False)
    return len(shown)


def run(args):
    out = Path(args.output)
    if (out / "MANIFEST.json").exists():
        raise ValueError(
            "Choose a fresh output directory; frozen case cannot be overwritten"
        )
    out.mkdir(parents=True, exist_ok=True)
    inputs = out / "inputs"
    inputs.mkdir(exist_ok=True)
    cutoff = pd.Timestamp(args.cutoff)
    cal_raw = get_tradecal(
        start_date="20250601",
        end_date=cutoff.strftime("%Y%m%d"),
        source="database_only",
    )
    calendar = sorted(cal_raw.cal_date.astype(str).unique())
    previous = max(day for day in calendar if day < cutoff.strftime("%Y%m%d"))
    raw = get_day(
        ts_code=args.ts_code,
        start_date="20250601",
        end_date=previous,
        qfq=False,
        source="database_only",
    )
    adj = get_adj_factor(
        ts_code=args.ts_code,
        start_date="20250601",
        end_date=previous,
        source="database_only",
    )
    kpl = get_kpl_list(
        start_date="20260601", end_date=previous, tags="涨停", source="database_only"
    )
    kpl = kpl[kpl.ts_code.eq(args.ts_code)].copy()
    caps = get_stk_limit(
        ts_code=args.ts_code,
        start_date="20260601",
        end_date=previous,
        source="database_only",
    )
    minute_raw = get_fifteenMin(
        ts_code=args.ts_code, start_date="20260601", end_date=cutoff.strftime("%Y%m%d")
    )
    minute_raw.datetime = pd.to_datetime(minute_raw.datetime)
    minute_raw = minute_raw[minute_raw.datetime.le(cutoff)].copy()
    one = get_oneMin(ts_code=args.ts_code, trade_date=cutoff.strftime("%Y%m%d"))
    one.datetime = pd.to_datetime(one.datetime)
    one = one[one.datetime.le(cutoff)].copy()
    for key, table in {
        "calendar": cal_raw,
        "daily_raw": raw,
        "daily_factors": adj,
        "kpl": kpl,
        "limits": caps,
        "fifteen_prefix": minute_raw,
        "one_prefix": one,
    }.items():
        table.to_parquet(inputs / f"{key}.parquet", index=False)
    d = prepare_daily(raw, adj, [day for day in calendar if day <= previous])
    w = wave_metrics(d, args.launch_date, args.pullback_date)
    events = board_events(kpl, revised_rules(), raw, caps, calendar)
    f = features(minute_raw, [day for day in calendar if day >= "20260601"], Rules())
    f = f[f.datetime.le(cutoff)].copy()
    observations = []
    checks = []
    prefix_checks = 0
    factor = float(f.adj_factor.dropna().iloc[0]) / float(
        f.adj_factor.dropna().iloc[-1]
    )
    for _, bar in f[f.datetime.dt.normalize().eq(cutoff.normalize())].iterrows():
        stamp = bar.datetime
        prefix = features(
            minute_raw[minute_raw.datetime.le(stamp)],
            [day for day in calendar if day >= "20260601"],
            Rules(),
        )
        pd.testing.assert_frame_equal(
            f[f.datetime.le(stamp)].reset_index(drop=True),
            prefix[prefix.datetime.le(stamp)].reset_index(drop=True),
        )
        prefix_checks += 1
        observations.append(
            dict(
                datetime=str(stamp),
                close=float(bar.close),
                short_group_pct=float(bar.coil30_pct),
                extended_ma60_pct=float(bar.coil_with_ma60_pct),
                relaxed_observation=bool(bar.coil30_pct <= 2),
                relaxed_restart_alignment=relaxed_restart_alignment(bar),
                body_pct=float(bar.body_pct),
                volume_multiple8=float(bar.volume_multiple8),
                compression_previous80_pct=float(bar.pre_restart_range_pct),
                **{
                    f"ma{n}": float(bar[f"ma{n}"] * factor)
                    for n in (5, 10, 20, 30, 60, 120)
                },
            )
        )
        sample = one[
            one.datetime.gt(stamp - pd.Timedelta(minutes=15)) & one.datetime.le(stamp)
        ]
        aggregated = [
            sample.open.iloc[0],
            sample.high.max(),
            sample.low.min(),
            sample.close.iloc[-1],
            sample.vol.sum(),
        ]
        native = [bar.open, bar.high, bar.low, bar.close, bar.vol]
        checks.append(
            dict(
                datetime=str(stamp),
                one_minute_bars=len(sample),
                ohlcv_equal=bool(
                    len(sample) == 15
                    and np.allclose(aggregated, native, atol=1e-8, rtol=0)
                ),
            )
        )
    prior_bar = d[d.trade_date.eq(previous)].iloc[0]
    w["prior_close_date"] = previous
    w["prior_daily_short_spread_pct"] = spread(
        [prior_bar.ma5, prior_bar.ma10, prior_bar.ma20]
    )
    w["source_kpl_native_status"] = str(
        kpl.loc[kpl.trade_date.astype(str).eq(args.launch_date), "status"].iloc[0]
    )
    w["launch_twenty_pct_limit_verified"] = bool(
        events.loc[
            events.trade_date.eq(args.launch_date), "twenty_pct_limit_verified"
        ].iloc[0]
    )
    pool = daily_pool_snapshot(d, events, previous, revised_rules())

    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))

    write_json(out / "wave_metrics.json", w)
    write_json(out / "daily_pool_snapshot.json", pool)
    pd.DataFrame(observations).to_csv(
        out / "minute_observations.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(checks).to_csv(
        out / "minute_aggregation_checks.csv", index=False, encoding="utf-8-sig"
    )
    events.to_csv(out / "board_event_audit.csv", index=False, encoding="utf-8-sig")
    _configure_chinese_font()
    for kind in ("daily", "minute", "paired"):
        count = 4 if kind == "paired" else 2
        fig, axes = plt.subplots(
            count,
            1,
            figsize=(18, 18 if count == 4 else 9),
            gridspec_kw=dict(
                height_ratios=[4, 1] * (count // 2), hspace=0.5 if count == 4 else 0.23
            ),
        )
        for axis in axes:
            axis.grid(alpha=0.2)
        if kind in ("daily", "paired"):
            draw_daily(axes[0], axes[1], d, w, args.name)
        if kind == "minute":
            draw_minutes(axes[0], axes[1], f, args.name)
        elif kind == "paired":
            draw_minutes(axes[2], axes[3], f, args.name)
        fig.suptitle(
            f"{args.name} {args.ts_code}｜启动、回踩、粘合与确认的区分", fontsize=17
        )
        fig.subplots_adjust(left=0.075, right=0.97, top=0.89, bottom=0.10)
        fig.text(
            0.075,
            0.02,
            "开发样本；启动日期由用户指定。日线至前一日，分钟逐根截断；没有画模拟买卖或使用截止时点之后行情。\n"
            "放宽分支：日MA5/10/20≤7%，15分钟close+MA5/10/20/30≤2%；MA60不参与粘合约束。观察不等于买入。",
            fontsize=9,
        )
        fig.savefig(out / f"{kind}.jpg", dpi=140, facecolor="white")
        plt.close(fig)
    manifest = dict(
        ts_code=args.ts_code,
        cutoff=str(cutoff),
        daily_cutoff=previous,
        explicitly_selected_development_template=True,
        launch_date=args.launch_date,
        rules=asdict(revised_rules()),
        models_called=False,
        outcomes_scored=False,
        prefix_checks=prefix_checks,
        aggregation_checks=checks,
        daily_pool_eligible=bool(pool["eligible"]),
        input_sha256={p.name: digest(p) for p in inputs.glob("*.parquet")},
        image_sha256={p.name: digest(p) for p in out.glob("*.jpg")},
    )
    write_json(out / "MANIFEST.json", manifest)
    print(
        json.dumps(
            dict(wave=w, daily_pool=pool, observations=observations, checks=checks),
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ts-code", default="688137.SH")
    p.add_argument("--name", default="近岸蛋白")
    p.add_argument("--launch-date", default="20260807")
    p.add_argument("--pullback-date", default="20260911")
    p.add_argument("--cutoff", default="2026-09-16 11:00:00")
    p.add_argument("--output", default="outputs/ma_pullback_nearshore_case_20260916_v2")
    run(p.parse_args())
