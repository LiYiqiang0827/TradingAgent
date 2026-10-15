"""Inspectable daily/intraday trend-pullback replay gallery; submits no trades.

Reads frozen study outputs. All performance case charts explicitly disclose
future replay data; the observation-cohort charts stop at the selection date.
Run after the study: python trend_pullback_review.py --output OUTPUT_ROOT.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys
import os
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) /
                                         "tradingagent-trend-review-matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from plot.plot_daily_kline import MA_COLORS, _configure_chinese_font, _draw_candles


REASONS = {
    "pullback_restart_trial": "回调重启试仓",
    "confirmed_extension_add": "延续确认加仓",
    "frozen_stop_failed": "启动支撑失效",
    "sixteen_bars_no_progress_and_ma20_lost": "16根未推进且失守MA20",
    "ma30_control_exit": "MA30控制组退出",
    "falling_ma30_failed": "失守转弱MA30",
    "runner_ma60_failed": "尾仓失守MA60",
    "supported_failure_probability": "失败概率升高减仓",
    "acceleration_bear_volume_exhaustion": "加速后放量阴线减仓",
    "first_weak_ma20_loss": "首次弱化MA20减仓",
    "weakness_and_low_extension_probability": "弱化且延续概率下降",
    "late_runner_probability_trim": "尾仓延续概率下降",
    "study_end_censored": "研究结束仍持仓",
}


def _read_csv(path):
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"ts_code": str, "trade_date": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def _percent(value):
    return "未知" if value is None or pd.isna(value) else f"{100 * float(value):.2f}%"


def _truth(series):
    return series.map(lambda v: str(v).lower() in ("true", "1", "1.0"))


def _ticks(ax, times, minute=False):
    if len(times) == 0:
        return
    stamps = pd.to_datetime(times)
    if minute:
        days = stamps.dt.strftime("%Y-%m-%d")
        first = np.flatnonzero(days.ne(days.shift()).to_numpy())
        stride = max(1, int(np.ceil(len(first) / 10)))
        ticks = first[::stride]
        labels = [stamps.iloc[i].strftime("%m-%d %H:%M") for i in ticks]
    else:
        ticks = np.unique(np.linspace(0, len(times) - 1, min(10, len(times)), dtype=int))
        labels = [stamps.iloc[i].strftime("%Y-%m-%d") for i in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=25, ha="right", fontsize=8)


def _draw(ax, volax, candles, volume, mas, times, minute=False):
    candles = candles.reset_index(drop=True)
    colors = _draw_candles(ax, candles)
    for n, values in mas.items():
        ax.plot(np.arange(len(values)), np.asarray(values), linewidth=1.0,
                color=MA_COLORS.get(n, "#555555"), label=f"MA{n}", alpha=.9)
    ax.legend(loc="upper left", ncol=min(6, len(mas)), fontsize=8)
    ax.grid(alpha=.15)
    volax.bar(np.arange(len(candles)), np.asarray(volume), color=colors, width=.7, alpha=.68)
    volax.set_ylabel("成交量\n万股", fontsize=8)
    volax.grid(axis="y", alpha=.15)
    ax.tick_params(labelbottom=False)
    _ticks(volax, times, minute)


def _snapshot_frames(out, codes):
    """Reconstruct only a historical snapshot, even if full future files exist."""
    raw_path = out / "inputs/selection_daily.parquet"
    adj_path = out / "inputs/selection_adj.parquet"
    if raw_path.exists() and adj_path.exists():
        raw = pd.read_parquet(raw_path)
        raw = raw[raw.ts_code.isin(codes)].copy()
        adj = pd.read_parquet(adj_path)
        adj = adj[adj.ts_code.isin(codes)].copy()
        raw["trade_date"] = raw.trade_date.astype(str)
        adj["trade_date"] = adj.trade_date.astype(str)
        x = raw.drop(columns="adj_factor", errors="ignore").merge(
            adj[["ts_code", "trade_date", "adj_factor"]], on=["ts_code", "trade_date"],
            how="left", validate="one_to_one").sort_values(["ts_code", "trade_date"])
        frames = {}
        for code, f in x.groupby("ts_code"):
            f = f.copy()
            for field in ("open", "high", "low", "close"):
                f[field] *= f.adj_factor
            for n in (5, 10, 20, 30, 60, 120):
                f[f"ma{n}"] = f.close.rolling(n).mean()
            f["raw_vol"] = f.vol
            frames[code] = f.reset_index(drop=True)
        return frames
    frames = {}
    for code in codes:
        path = out / "daily_frames" / f"{code}.parquet"
        if path.exists():
            frames[code] = pd.read_parquet(path)
    return frames


def observation_view(out, union_pool, selection):
    """Choose only the first monthly cohort for pictures, never the union pool."""
    monthly = _read_csv(out / "MONTHLY_POOLS.csv")
    if monthly.empty:
        return union_pool, selection, pd.DataFrame(), 0
    required = {"month", "ts_code"}
    if required - set(monthly):
        raise ValueError("MONTHLY_POOLS.csv requires month and ts_code")
    monthly["month"] = monthly.month.astype(str).str.replace("-", "", regex=False)
    if "selection_as_of" not in monthly:
        if "as_of" not in monthly:
            raise ValueError("Monthly pool requires selection_as_of or as_of")
        monthly["selection_as_of"] = monthly.as_of
    monthly["selection_as_of"] = monthly.selection_as_of.astype(str)
    if monthly.duplicated(["month", "ts_code"]).any():
        raise ValueError("Repeated stock within a monthly observation cohort")
    counts = monthly.groupby("month").ts_code.nunique()
    if counts.gt(20).any():
        raise ValueError("A monthly observation cohort exceeds the declared 20-stock cap")
    rows = []
    previous = set()
    for month, group in monthly.groupby("month", sort=True):
        cutoffs = set(group.selection_as_of)
        if len(cutoffs) != 1:
            raise ValueError(f"Multiple selection cutoffs for {month}")
        cutoff = next(iter(cutoffs))
        if pd.Timestamp(cutoff) >= pd.Timestamp(month + "01"):
            raise ValueError(f"Selection cutoff {cutoff} is not earlier than active month {month}")
        codes = set(group.ts_code)
        themes = "/".join(sorted(set(group.ranking_theme.dropna()))) if "ranking_theme" in group else "未知"
        rows.append({"月份": month, "选股截面": cutoff, "观察股数": len(codes),
                     "新入池": len(codes - previous), "离开池": len(previous - codes),
                     "延续": len(codes & previous), "当月历史题材": themes})
        previous = codes
    first_month = monthly.month.min()
    example = monthly[monthly.month.eq(first_month)].copy()
    if "observation_rank" in example:
        example = example.sort_values("observation_rank")
    if "name" not in example:
        example = example.merge(union_pool[["ts_code", "name"]], on="ts_code", how="left", validate="one_to_one")
    first_selection = {**selection, "as_of": example.selection_as_of.iloc[0],
                       "example_month": first_month}
    return example.reset_index(drop=True), first_selection, pd.DataFrame(rows), monthly.ts_code.nunique()


def plot_cohort(out, pool, selection):
    as_of = str(selection.get("as_of", "20250630"))
    charts = out / "charts/cohort"
    charts.mkdir(parents=True, exist_ok=True)
    frames = _snapshot_frames(out, pool.ts_code.tolist())
    results = []
    for _, selected in pool.iterrows():
        code = selected.ts_code
        if code not in frames:
            continue
        f = frames[code].copy()
        f["trade_date"] = f.trade_date.astype(str)
        f = f[f.trade_date <= as_of].tail(140).reset_index(drop=True)
        if f.empty:
            continue
        anchor = float(f.adj_factor.iloc[-1])
        fig, (ax, volax) = plt.subplots(2, 1, figsize=(14, 8),
            gridspec_kw={"height_ratios": [4, 1]}, sharex=True)
        candles = f[["open", "high", "low", "close"]] / anchor
        mas = {n: f[f"ma{n}"] / anchor for n in (5, 10, 20, 30, 60, 120)
               if f"ma{n}" in f}
        _draw(ax, volax, candles, f.raw_vol / 100, mas,
              pd.to_datetime(f.trade_date, format="%Y%m%d"))
        name = str(selected.get("name", ""))
        run = max(float(selected.get(f"rise{n}", 0)) for n in (20, 30, 60))
        ax.set_title(f"{code} {name}｜历史观察池截面 {as_of}｜最长均线上升 {run:.0f}日")
        ax.set_ylabel("以截面复权因子归一的元价格")
        fig.text(.01, .008, "历史截面：不展示选择日之后的行情；选股未使用未来收益。", fontsize=9)
        fig.tight_layout(rect=(0, .025, 1, 1))
        path = charts / f"{code}.jpg"
        fig.savefig(path, dpi=120); plt.close(fig)
        results.append({"ts_code": code, "name": name, "path": path})
    if results:
        make_contact([r["path"] for r in results], charts / "cohort_contact.jpg", cols=4,
                     cell=(500, 310))
    return results


def make_contact(paths, target, cols=4, cell=(500, 370)):
    rows = int(np.ceil(len(paths) / cols))
    sheet = Image.new("RGB", (cols * cell[0], rows * cell[1]), "#f5f5f5")
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(paths):
        with Image.open(path) as source:
            picture = source.convert("RGB")
            picture.thumbnail((cell[0] - 12, cell[1] - 30))
            left = (i % cols) * cell[0] + (cell[0] - picture.width) // 2
            top = (i // cols) * cell[1] + 5
            sheet.paste(picture, (left, top))
        draw.text(((i % cols) * cell[0] + 8, (i // cols + 1) * cell[1] - 20),
                  Path(path).stem, fill="#333333")
    sheet.save(target, quality=90)


def plot_curve(out, curve, summary):
    target = out / "charts/holdout_fund_curve.jpg"
    if curve.empty:
        return None
    curve = curve.copy(); curve["datetime"] = pd.to_datetime(curve.datetime)
    initial = float(summary.get("initial_cash", curve.equity.iloc[0]))
    equity = curve.equity.astype(float)
    peak = equity.cummax().clip(lower=initial)
    drawdown = 100 * (equity / peak - 1)
    fig, axes = plt.subplots(3, 1, figsize=(15, 9), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1.2, 1.2]})
    axes[0].plot(curve.datetime, equity / 10000, color="#176d86", linewidth=1.6)
    axes[0].axhline(initial / 10000, color="#777777", linestyle="--", linewidth=.8)
    axes[0].set_ylabel("账户权益 / 万元")
    verified = bool(summary.get("cash_accounting_verified", False))
    qualifier = "现金权益" if verified else "含未建模公司行动：条件性权益参考"
    axes[0].set_title(f"后段留出期回放｜{qualifier}｜净收益 {_percent(summary.get('net_return'))}｜"
                      f"最大回撤 {_percent(summary.get('max_drawdown'))}")
    axes[1].fill_between(curve.datetime, drawdown, 0, color="#cd5f64", alpha=.5)
    axes[1].set_ylabel("回撤 / %")
    if "exposure" in curve:
        axes[2].fill_between(curve.datetime, 100 * curve.exposure / curve.equity,
                             color="#c59437", alpha=.5)
        axes[2].set_ylabel("股票仓位 / %")
    for ax in axes:
        ax.grid(alpha=.2)
    fig.text(.01, .01, "回放展示：下一根开盘成交代理、费用与逐批T+1；止损价不保证成交。", fontsize=9)
    fig.tight_layout(rect=(0, .03, 1, 1)); fig.savefig(target, dpi=130); plt.close(fig)
    return target


def compare_policies(out, selected_summary):
    rows = []
    fig, ax = plt.subplots(figsize=(14, 6))
    colors = {"adaptive": "#14768a", "structure": "#bc8532", "ma30": "#876bc4"}
    descriptions = {"adaptive": "概率分仓（半份试仓）", "structure": "结构分仓（半份试仓）",
                    "ma30": "MA30控制（整份入场）"}
    variants = [(out / "selected/holdout", selected_summary)]
    extra_folders = sorted((out / "controls").glob("*/holdout"))
    matched = out / "diagnostics/ma30_trial_only/holdout"
    if matched.exists():
        extra_folders.append(matched)
    for folder in extra_folders:
        summary = _read_json(folder / "SUMMARY.json")
        if summary:
            variants.append((folder, summary))
    for folder, summary in variants:
        curve = _read_csv(folder / "curve.csv")
        policy = str(summary.get("policy", folder.parent.name))
        if "ma30_trial_only" in str(folder):
            policy = "ma30_trial_only"
            descriptions[policy] = "MA30同份额对照（半份入场）"
            colors[policy] = "#596263"
        if not curve.empty:
            ax.plot(pd.to_datetime(curve.datetime), 100 * (curve.equity / summary["initial_cash"] - 1),
                    label=descriptions.get(policy, policy), color=colors.get(policy), linewidth=1.2)
        rows.append({"policy": descriptions.get(policy, policy), "net_return": summary.get("net_return"),
                     "max_drawdown": summary.get("max_drawdown"), "win_rate": summary.get("win_rate"),
                     "payoff_ratio": summary.get("payoff_ratio")})
    ax.axhline(0, color="#777777", linewidth=.8); ax.legend(loc="upper left")
    ax.grid(alpha=.2); ax.set_ylabel("账户净收益 / %")
    ax.set_title("同一入选规则的留出对照｜入场份额不同，不能单独归因于卖点或概率模型")
    fig.text(.01, .015, "回放展示：模型与规则在留出期未调整。概率/结构组半份试仓，MA30组整份入场；"
             "比较同时包含仓位与退出路径差异。", fontsize=9)
    fig.tight_layout(rect=(0, .05, 1, 1))
    target = out / "charts/policy_comparison.jpg"
    fig.savefig(target, dpi=130); plt.close(fig)
    return target, rows


def plot_probability_acceptance(out):
    """Audit frozen predictions, without refitting or altering past trade rules."""
    development = _read_csv(out / "MODEL_DEVELOPMENT.csv")
    temporal = _read_csv(out / "MODEL_TEMPORAL_TEST.csv")
    model = _read_json(out / "FROZEN_MODEL.json")
    if development.empty or temporal.empty or not model:
        return None, {"status": "unknown", "message": "概率验收材料未齐，不能解释为真实胜率"}
    selected = str(model.get("selected", "unknown"))
    validation = development[development.split.eq("validation")]
    chosen = validation[validation.model.eq(selected)]
    constant = validation[validation.model.eq("constant")]
    brier_ok = bool(len(chosen) and len(constant) and
                    chosen.brier.iloc[0] <= constant.brier.iloc[0] - .01)
    extend_error = abs(float(chosen.predicted_extend.iloc[0] - chosen.realized_extend.iloc[0])) if len(chosen) else np.nan
    error_ok = bool(np.isfinite(extend_error) and extend_error <= .10)
    deployment = model.get("variable_probability_deployed")
    status = "passed" if brier_ok and error_ok and deployment is not False else "failed"
    if deployment is False:
        message = "概率模块未通过开发验收，已禁用；常数预测仅用于诊断，不参与分仓"
    elif status == "failed":
        message = "概率模块未通过：原回放曾使用这些预测，但不能解释为真实胜率"
    else:
        message = "通过有限开发门槛；仍不能将标签概率解释为真实交易胜率"
    temporal = temporal.copy()
    probabilities = np.column_stack([temporal.p_fail, temporal.p_extend,
                                     1 - temporal.p_fail - temporal.p_extend])
    truth = np.eye(3)[temporal.label.astype(int)]
    prior = np.asarray(model.get("prior", [np.nan, np.nan, np.nan]), dtype=float)
    temporal_brier = float(np.mean(np.sum((probabilities - truth) ** 2, axis=1)))
    baseline_brier = float(np.mean(np.sum((prior - truth) ** 2, axis=1)))
    fig, axes = plt.subplots(2, 2, figsize=(15, 11))
    reliability_rows = []
    for ax, target, label in [(axes[0, 0], "p_fail", 0), (axes[0, 1], "p_extend", 1)]:
        x = temporal.copy(); x["truth"] = x.label.eq(label).astype(float)
        x["bin"] = np.minimum(9, (x[target] * 10).astype(int))
        bins = x.groupby("bin").agg(predicted=(target, "mean"), realized=("truth", "mean"),
                                     n=("truth", "size"))
        ax.plot([0, 100], [0, 100], color="#777777", linestyle="--", linewidth=.9,
                label="理想校准")
        ax.scatter(bins.predicted * 100, bins.realized * 100,
                   s=np.minimum(260, 20 + 15 * np.sqrt(bins.n)), color="#ba5356" if label == 0 else "#31799b")
        for _, row in bins.iterrows():
            ax.annotate(f"n={int(row.n)}", (row.predicted * 100, row.realized * 100),
                        xytext=(5, 5), textcoords="offset points", fontsize=8)
            reliability_rows.append(dict(target=target, prediction=float(row.predicted),
                                         actual=float(row.realized), n=int(row.n)))
        name = "先失败" if label == 0 else "先延伸"
        predicted_mean = float(x[target].mean())
        realized_mean = float(x.truth.mean())
        ax.set_title(f"后段留出 {name}｜预测均值 {predicted_mean:.2%}，实际 {realized_mean:.2%}")
        ax.set_xlabel("分箱预测概率 / %"); ax.set_ylabel("分箱实际标签频率 / %")
        ax.set_xlim(-2, 102); ax.set_ylim(-2, 102); ax.grid(alpha=.2)
    compare = axes[1, 0]
    chart_rows = validation[["model", "brier"]].to_dict("records")
    labels = ["开发 " + str(r["model"]) for r in chart_rows] + ["后段 " + selected, "后段 constant"]
    values = [float(r["brier"]) for r in chart_rows] + [temporal_brier, baseline_brier]
    compare.barh(np.arange(len(values)), values,
                 color=["#6f9296"] * len(chart_rows) + ["#bc565b", "#a7aaae"])
    compare.set_yticks(np.arange(len(values)), labels, fontsize=9)
    compare.invert_yaxis(); compare.set_xlabel("三分类 Brier：越低越好")
    compare.set_title("开发与后段：模型和常数基准对照")
    for i, val in enumerate(values):
        compare.text(val + .006, i, f"{val:.3f}", va="center", fontsize=9)
    compare.set_xlim(0, max(values) * 1.18); compare.grid(axis="x", alpha=.15)
    note = axes[1, 1]; note.axis("off")
    lines = [message, "", f"冻结模型：{selected}",
             f"Jan-Mar Brier 至少优于 constant 0.01：{'通过' if brier_ok else '失败'}",
             f"Jan-Mar 延伸均值误差 ≤ 10个百分点：{'通过' if error_ok else '失败'}",
             f"开发延伸均值误差：{100 * extend_error:.2f}个百分点", "",
             f"后段标签样本：{len(temporal)} 根，{temporal.time.str[:10].nunique()} 个日期",
             "四交易日标签相互重叠，行数不是独立样本量。",
             "先失败、先延伸、均未达到是标签分类。",
             "标签频率不等于执行后的净收益或胜率。",
             "此图只验收，不反向修改已冻结的历史交易。"]
    note.text(0, .98, "\n".join(lines), va="top", fontsize=11, linespacing=1.8)
    fig.suptitle("概率模块验收：先检查预测是否可信，再讨论按概率分仓", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, .96))
    target = out / "charts/probability_acceptance.jpg"
    fig.savefig(target, dpi=130); plt.close(fig)
    result = dict(status=status, message=message, selected_model=selected,
                  frozen_variable_probability_deployed=deployment,
                  validation_brier_gate=brier_ok, validation_extend_error=extend_error,
                  validation_extend_error_gate=error_ok, temporal_brier=temporal_brier,
                  temporal_constant_brier=baseline_brier,
                  temporal_predicted_fail=float(temporal.p_fail.mean()),
                  temporal_actual_fail=float(temporal.label.eq(0).mean()),
                  temporal_predicted_extend=float(temporal.p_extend.mean()),
                  temporal_actual_extend=float(temporal.label.eq(1).mean()),
                  n_temporal=len(temporal), temporal_dates=temporal.time.str[:10].nunique())
    (out / "PROBABILITY_ACCEPTANCE.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    pd.DataFrame(reliability_rows).to_csv(out / "probability_reliability_bins.csv", index=False)
    return target, result


def _chosen_cases(trades, count=20, seed=20261002):
    """Extreme chart audit + fixed-seed sample; never alters strategy denominators."""
    if len(trades) <= count:
        return trades.copy(), "展示全部成交案例"
    ordered = trades.sort_values(["net_return", "id"])
    ids = ordered.head(3).id.tolist() + ordered.tail(3).id.tolist()
    other = trades[~trades.id.isin(ids)].sample(n=count - len(ids), random_state=seed).id.tolist()
    selected = trades[trades.id.isin(ids + other)].sort_values(["entry_time", "ts_code"])
    return selected, f"图示审查：最好3笔、最差3笔，其余固定种子{seed}随机抽样；完整统计分母不变"


def _marks(ax, rows, orders, minute, anchor):
    if orders.empty:
        return
    span = max(.01, rows.high.max() - rows.low.min())
    dates = pd.to_datetime(rows.datetime)
    for i, (_, order) in enumerate(orders.iterrows()):
        if minute:
            found = np.flatnonzero(rows.original_index.to_numpy() == int(order.bar_index))
        else:
            day = pd.Timestamp(order.fill_time).normalize()
            found = np.flatnonzero(dates.dt.normalize().to_numpy() == day.to_datetime64())
        if not len(found):
            continue
        idx = int(found[0])
        side = str(order.side)
        row = rows.iloc[idx]
        factor = float(row["scale"] if "scale" in rows else row.adj_factor)
        price = float(order.price) * factor / anchor
        if side == "buy":
            is_add = str(order.reason) == "confirmed_extension_add"
            marker, color = ("D", "#ad3bc4") if is_add else ("^", "#d62728")
            label = "加仓" if is_add else "试仓"
        else:
            marker, color = "v", "#174c9a"
            remaining = float(order.get("shares_remaining", 0))
            fraction = float(order.qty) / (float(order.qty) + remaining)
            label = "清仓" if not remaining else f"减{100*fraction:.0f}%"
        ax.scatter(idx, price, marker=marker, color=color, s=70, zorder=8,
                   edgecolor="white", linewidth=.7)
        if not minute:
            # All fill markers remain, but same-day orders share one caption
            # rather than covering each other on a daily candle.
            same_day = pd.to_datetime(orders.fill_time).dt.normalize().eq(
                pd.Timestamp(order.fill_time).normalize()) & orders.side.eq(side)
            grouped = orders.loc[same_day]
            if len(grouped) > 1:
                if order.name != grouped.index[-1]:
                    continue
                label = f"买入{len(grouped)}次" if side == "buy" else (
                    f"卖出{len(grouped)}次/清仓" if not remaining else f"减仓{len(grouped)}次")
        offset = (.075 + .055 * (i % 3)) * span * (1 if side == "sell" else -1)
        text_x = idx - 9 if not minute and idx >= len(rows) - 15 else idx + 3
        ax.annotate(label, (idx, price), xytext=(text_x, price + offset), fontsize=8,
                    color=color, arrowprops={"arrowstyle": "-", "color": color, "lw": .7}, zorder=9)


def plot_case(out, trade, ledger, decisions, names):
    code = trade.ts_code
    intraday_path = out / "frames" / f"{code}.parquet"
    daily_path = out / "daily_frames" / f"{code}.parquet"
    if not intraday_path.exists() or not daily_path.exists():
        return None
    f = pd.read_parquet(intraday_path)
    f["datetime"] = pd.to_datetime(f.datetime)
    f["original_index"] = f.index
    d = pd.read_parquet(daily_path)
    d["trade_date"] = d.trade_date.astype(str)
    d["datetime"] = pd.to_datetime(d.trade_date, format="%Y%m%d")
    d["original_index"] = d.index
    entry = pd.Timestamp(trade.entry_time)
    end = pd.Timestamp(trade.exit_time) if pd.notna(trade.exit_time) else f.datetime.max()
    orders = ledger[ledger.id.eq(trade.id)].sort_values("fill_time") if len(ledger) else pd.DataFrame()
    held = decisions[decisions.id.eq(trade.id)].copy() if len(decisions) else pd.DataFrame()
    if not held.empty:
        held["datetime"] = pd.to_datetime(held.datetime)
    first = int(np.searchsorted(f.datetime.to_numpy(), entry.to_datetime64(), "left"))
    last = int(np.searchsorted(f.datetime.to_numpy(), end.to_datetime64(), "right"))
    f = f.iloc[max(0, first - 80): min(len(f), last + 16)].reset_index(drop=True)
    if f.empty:
        return None
    anchor = float(f.scale.iloc[min(80, len(f) - 1)])
    firstd = int(np.searchsorted(d.datetime.to_numpy(), entry.normalize().to_datetime64(), "left"))
    lastd = int(np.searchsorted(d.datetime.to_numpy(), end.normalize().to_datetime64(), "right"))
    d = d.iloc[max(0, firstd - 120): min(len(d), lastd + 2)].reset_index(drop=True)
    fig = plt.figure(figsize=(18, 15))
    gs = fig.add_gridspec(5, 1, height_ratios=[3, .8, 4, 1, 1.5], hspace=.28)
    daily_ax = fig.add_subplot(gs[0]); daily_vol = fig.add_subplot(gs[1], sharex=daily_ax)
    minute_ax = fig.add_subplot(gs[2]); minute_vol = fig.add_subplot(gs[3], sharex=minute_ax)
    prob_ax = fig.add_subplot(gs[4], sharex=minute_ax)
    daily_candles = d[["open", "high", "low", "close"]] / anchor
    daily_mas = {n: d[f"ma{n}"] / anchor for n in (5, 10, 20, 30, 60, 120) if f"ma{n}" in d}
    _draw(daily_ax, daily_vol, daily_candles, d.raw_vol / 100, daily_mas, d.datetime)
    daily_rows = d.copy()
    for col in ("open", "high", "low", "close"):
        daily_rows[col] /= anchor
    _marks(daily_ax, daily_rows, orders, False, anchor)
    daily_ax.set_title(f"{code} {names.get(code, '')}｜日线大趋势（回放展示，含买入之后行情）")
    minute_candles = f[[f"p_{col}" for col in ("open", "high", "low", "close")]].copy() / anchor
    minute_candles.columns = ["open", "high", "low", "close"]
    minute_mas = {n: f[f"ma{n}"] / anchor for n in (5, 10, 20, 30, 60, 120) if f"ma{n}" in f}
    _draw(minute_ax, minute_vol, minute_candles, f.vol / 10000, minute_mas, f.datetime, True)
    minute_rows = f.copy()
    for col in ("open", "high", "low", "close"):
        minute_rows[col] = f[f"p_{col}"] / anchor
    _marks(minute_ax, minute_rows, orders, True, anchor)
    stop = float(trade.stop_p) / anchor
    minute_ax.axhline(stop, color="#c7255e", linestyle="--", linewidth=1,
                      label=f"冻结失效价 {stop:.2f}")
    positions = np.flatnonzero((f.datetime >= entry) & (f.datetime <= end))
    if len(positions):
        for ax in (minute_ax, minute_vol, prob_ax):
            ax.axvspan(positions[0] - .5, positions[-1] + .5, color="#ffd65a", alpha=.10)
    minute_ax.legend(loc="upper left", ncol=7, fontsize=8)
    minute_ax.set_title(f"15分钟执行｜净收益 {_percent(trade.net_return)}｜买入{int(trade.buys)}次 / "
                         f"卖出{int(trade.sells)}次｜最终原因：{REASONS.get(trade.exit_reason, trade.exit_reason)}")
    minute_ax.set_ylabel("价格 / 元（因子归一）")
    # State-conditioned decisions are authoritative. Unsupported predictions
    # stay blank instead of being drawn as calibrated probabilities.
    probabilities = pd.DataFrame(index=range(len(f)))
    probabilities["p_fail"] = np.nan; probabilities["p_extend"] = np.nan
    if not held.empty and "p_fail" in held and "p_extend" in held:
        held = held.drop_duplicates("index", keep="last").set_index("index")
        for col in ("p_fail", "p_extend"):
            probabilities[col] = f.original_index.map(held[col])
        if "supported" in held:
            supported = f.original_index.map(_truth(held.supported)).eq(True).to_numpy(dtype=bool)
            probabilities.loc[~supported, ["p_fail", "p_extend"]] = np.nan
    elif "p_fail_pred" in f and "p_extend_pred" in f:
        probabilities["p_fail"] = f.p_fail_pred.to_numpy()
        probabilities["p_extend"] = f.p_extend_pred.to_numpy()
        if "model_supported" in f:
            probabilities.loc[~_truth(f.model_supported), ["p_fail", "p_extend"]] = np.nan
    model_meta = _read_json(out / "FROZEN_MODEL.json")
    probability_audit = _read_json(out / "PROBABILITY_ACCEPTANCE.json")
    disabled = model_meta.get("variable_probability_deployed") is False
    if disabled:
        probabilities[["p_fail", "p_extend"]] = np.nan
    has_probability = probabilities.notna().any().any()
    if has_probability:
        prob_ax.plot(probabilities.p_fail * 100, color="#be4146", label="未来失败条件概率")
        prob_ax.plot(probabilities.p_extend * 100, color="#198063", label="继续延伸条件概率")
        prob_ax.legend(loc="upper left", ncol=2, fontsize=8)
        if probability_audit.get("status") == "failed":
            prob_ax.text(.99, .94, "历史模型预测未通过验收，不能解释为真实胜率",
                         transform=prob_ax.transAxes, ha="right", va="top", fontsize=9,
                         color="#9b2525", bbox={"facecolor": "#fff4df", "alpha": .85, "edgecolor": "none"})
    else:
        explanation = "概率模块未过验收，未部署；本案例使用结构规则" if disabled else \
                      "本案例未使用受支持的概率；结构规则或未知处留空"
        prob_ax.text(.5, .5, explanation, transform=prob_ax.transAxes,
                     ha="center", fontsize=10, color="#555555")
    prob_ax.set_ylim(0, 100); prob_ax.set_ylabel("条件概率 / %", fontsize=9)
    prob_ax.grid(alpha=.2); _ticks(prob_ax, f.datetime, True)
    minute_vol.tick_params(labelbottom=False)
    issue = str(trade.get("issue_codes", ""))
    fig.text(.01, .011,
        "回放展示：此图含未来行情，仅用于审查已冻结信号。红▲试仓，紫◆加仓，蓝▼减仓/清仓；"
        "分仓标签是当次成交量占卖前剩余股数比例。\n"
        f"信号 {trade.signal_time}；首次买入 {trade.entry_time}；最终退出 {trade.exit_time}；"
        f"T+1/跌停延迟 {int(trade.sell_delay_bars)}根；异常 {issue}", fontsize=9)
    fig.subplots_adjust(left=.064, right=.987, top=.963, bottom=.087)
    path = out / "charts/cases" / f"{code}_{pd.Timestamp(trade.signal_time).strftime('%Y%m%d_%H%M')}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


def plot_decision(out, trade, names):
    """Pre-entry chart and exact flat-price MA scenario: no actual future OHLC."""
    code = trade.ts_code
    f = pd.read_parquet(out / "frames" / f"{code}.parquet")
    f["datetime"] = pd.to_datetime(f.datetime)
    signal = pd.Timestamp(trade.signal_time)
    f = f[f.datetime <= signal].copy()
    if f.empty:
        return None
    entire = f.copy()
    f = f.tail(120).reset_index(drop=True)
    anchor = float(f.scale.iloc[-1])
    d = pd.read_parquet(out / "daily_frames" / f"{code}.parquet")
    d["trade_date"] = d.trade_date.astype(str)
    d["datetime"] = pd.to_datetime(d.trade_date, format="%Y%m%d")
    d = d[d.datetime < signal.normalize()].tail(120).reset_index(drop=True)
    if d.empty:
        return None
    fig = plt.figure(figsize=(18, 12))
    gs = fig.add_gridspec(4, 1, height_ratios=[3, .8, 4, 1], hspace=.28)
    dax = fig.add_subplot(gs[0]); dv = fig.add_subplot(gs[1], sharex=dax)
    ax = fig.add_subplot(gs[2]); vv = fig.add_subplot(gs[3], sharex=ax)
    _draw(dax, dv, d[["open", "high", "low", "close"]] / anchor,
          d.raw_vol / 100, {n: d[f"ma{n}"] / anchor for n in (5, 10, 20, 30, 60, 120)
                            if f"ma{n}" in d}, d.datetime)
    dax.set_title(f"{code} {names.get(code, '')}｜买入决策材料：日线仅截至前一交易日")
    candles = f[[f"p_{c}" for c in ("open", "high", "low", "close")]].copy() / anchor
    candles.columns = ["open", "high", "low", "close"]
    _draw(ax, vv, candles, f.vol / 10000,
          {n: f[f"ma{n}"] / anchor for n in (5, 10, 20, 30, 60, 120, 250)
           if f"ma{n}" in f}, f.datetime, True)
    finalx = len(f) - 1
    close = float(entire.p_close.iloc[-1])
    ax.scatter(finalx, close / anchor, color="#d62728", marker="o", s=70, zorder=8)
    ax.axvline(finalx + .5, color="#777777", linewidth=.8)
    ax.axvspan(finalx + .5, finalx + 16, color="#e6eef3", alpha=.7)
    changes = []
    for n in (20, 60, 120, 250):
        history = entire.p_close.tail(n).to_numpy(dtype=float)
        if len(history) != n or not np.isfinite(history).all():
            continue
        initial = history.mean()
        projected = [initial] + [(history[h:].sum() + h * close) / n for h in range(1, 17)]
        ax.plot(np.arange(finalx, finalx + 17), np.asarray(projected) / anchor,
                color=MA_COLORS.get(n, "#555555"), linestyle="--", linewidth=1.5,
                label=f"MA{n}恒价抵扣")
        changes.append(f"MA{n}: {100*(projected[-1]/initial-1):+.2f}%")
    ax.plot([finalx, finalx + 16], [close / anchor] * 2,
            color="#555555", linewidth=.8, linestyle=":")
    ax.set_title(f"15分钟截至 {signal:%Y-%m-%d %H:%M}｜右侧16根：恒价情景，不是实际未来")
    ax.legend(loc="upper left", ncol=6, fontsize=8)
    ax.set_xlim(-1, finalx + 17)
    ax.set_ylabel("价格 / 元（截至信号因子归一）")
    fig.text(.01, .015, "不含未来材料：没有画实际成交价、卖点或真实未来K线。"
             "恒价情景同时抵扣每根被移出的历史收盘，只是假设；16根累计均线变化："
             + "；".join(changes), fontsize=9)
    fig.subplots_adjust(left=.064, right=.987, top=.963, bottom=.087)
    target = out / "charts/decisions" / f"{code}_{signal:%Y%m%d_%H%M}_asof.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=120); plt.close(fig)
    return target


def run(out, summary_only=False):
    out = Path(out).resolve()
    _configure_chinese_font()
    (out / "charts").mkdir(parents=True, exist_ok=True)
    pool = _read_csv(out / "observation_pool.csv")
    if pool.empty:
        raise FileNotFoundError("observation_pool.csv must exist before rendering")
    selection = _read_json(out / "OBSERVATION_SELECTION.json")
    base = out / "selected/holdout"
    summary = _read_json(base / "SUMMARY.json")
    if not summary:
        raise FileNotFoundError(f"Study replay summary not ready: {base / 'SUMMARY.json'}")
    trades = _read_csv(base / "trades.csv")
    ledger = _read_csv(base / "ledger.csv")
    decisions = _read_csv(base / "decisions.csv")
    curve = _read_csv(base / "curve.csv")
    example_pool, example_selection, monthly_table, union_count = observation_view(out, pool, selection)
    if len(monthly_table):
        monthly_table.to_csv(out / "MONTHLY_POOL_CHANGES.csv", index=False)
    zero_trades = trades.empty
    cohort = [] if summary_only or zero_trades else plot_cohort(out, example_pool, example_selection)
    fund = plot_curve(out, curve, summary)
    comparison_plot, comparisons = compare_policies(out, summary)
    probability_plot, probability_audit = plot_probability_acceptance(out)
    chosen, selection_text = _chosen_cases(trades) if len(trades) else (trades, "无成交案例")
    names = dict(zip(pool.ts_code, pool.name))
    cases = []
    decision_charts = []
    for _, trade in chosen.iterrows():
        path = plot_case(out, trade, ledger, decisions, names)
        if path:
            cases.append({"id": trade.id, "ts_code": trade.ts_code, "path": path,
                          "net_return": float(trade.net_return)})
            if len(decision_charts) < 5:
                decision_path = plot_decision(out, trade, names)
                if decision_path:
                    decision_charts.append({"ts_code": trade.ts_code, "path": decision_path})
    if cases:
        make_contact([r["path"] for r in cases], out / "charts/cases_contact.jpg", cols=4,
                     cell=(540, 490))
    verified = bool(summary.get("cash_accounting_verified", False))
    acceptance = bool(summary.get("risk_acceptance_failed", False))
    final = float(summary.get("final_equity", np.nan))
    traded_names = sorted(set(trades.ts_code)) if len(trades) else []
    added_trades = int(pd.to_numeric(trades.buys).gt(1).sum()) if len(trades) else 0
    phased_trades = int(pd.to_numeric(trades.sells).gt(1).sum()) if len(trades) else 0
    frozen_strategy = _read_json(out / "FROZEN_STRATEGY.json")
    targets_met = bool(frozen_strategy.get("development_targets_met", False))
    mode = frozen_strategy.get("selected", {}).get("context_mode", "未要求题材硬过滤")
    full_denominator = selection.get("full_denominator", {})
    qualified = selection.get("qualified", full_denominator.get("daily_eligible_with_limitup60", "未知"))
    method = "按照历史波动分层随机抽取，没有使用未来收益。" if "random draw" in selection.get("method", "") else \
             "依据截至选股日的KPL归属、题材热度与宽度、日线趋势与流动性排序，每行业最多3只；没有使用未来收益。"
    if len(monthly_table):
        introduction = (f"月度更新观察池：共 **{len(monthly_table)}个月**，每月最多20只，"
            f"累计去重 {union_count} 只。累计名单是历月并集，并非一次选入{union_count}只。"
            f"历史截面图只展示首月 {example_selection['example_month']} 的{len(example_pool)}只，"
            f"截止 {example_selection['as_of']}。每月根据上一月末可见题材与日线信息更新，"
            "不因未来表现替换名单；当前行业与存续名单仍有幸存偏差。")
    else:
        introduction = (f"观察池在 **{selection.get('as_of', '未知')}** 固定，共 {len(pool)} 只；"
            f"合格总体 {qualified} 只。" + method + "当前行业与存续名单仍有幸存偏差。")
    report = ["# 日线趋势与15分钟回调重启研究图册", "", introduction, "",
        f"入场题材约束模式：`{mode}`。历史KPL归属与原文匹配线索不等于公司受益已经证实。", "",
        "**" + probability_audit.get("message", "概率验收未知，不解释为真实胜率") + "。**", "",
        f"后段留出回放：候选 {summary.get('candidates', '未知')}，成交 {summary.get('trades', '未知')}，"
        f"平仓 {summary.get('closed_trades', '未知')}，未平仓 {summary.get('censored_trades', '未知')}。", "",
        f"实际覆盖 {len(traded_names)} 只：{'、'.join(traded_names) or '无'}。"
        f"加仓成交 {added_trades} 笔；分批卖出 {phased_trades} 笔。"
        f"开发期“小亏换大胜”目标{'达到' if targets_met else '未达到'}，不能只凭高胜率认为策略达标。", "",
        "|指标|留出期结果|", "|---|---|",
        f"|起始资金|{summary.get('initial_cash', 0):,.2f} 元|",
        f"|最终权益|{final:,.2f} 元|",
        f"|净收益|{_percent(summary.get('net_return'))}|",
        f"|最大回撤|{_percent(summary.get('max_drawdown'))}|",
        f"|平仓胜率|{_percent(summary.get('win_rate'))}|",
        f"|每笔均值 / 中位数|{_percent(summary.get('mean_trade_return'))} / {_percent(summary.get('median_trade_return'))}|",
        f"|平均盈利 / 平均亏损赔率|{summary.get('payoff_ratio', '未知')}（有限样本）|",
        f"|最差 / 最好一笔|{_percent(summary.get('worst_trade_return'))} / {_percent(summary.get('best_trade_return'))}|",
        f"|现金公司行动核验|{'通过' if verified else '未通过：禁止视为完整现金回测收益'}|",
        f"|单笔亏损超过10%|{'出现，风险验收失败' if acceptance else '本次未出现，不是未来损失上限保证'}|", "",
        "仓位：首次半份试仓，延续确认才加仓；每票名义风险预算0.5%账户权益，"
        "单票上限20%，总股票上限80%。技术失效优先退出，支持充分的条件概率用于减仓和保留尾仓。"
        "止损以完成K线判断、下一根开盘代理执行，逐批遵守T+1，不能保证亏损封顶。", "",
        "概率图中的空白代表未知或支持不足。模型区分未来64根内先失败、先延伸、两者均未出现三个互斥标签；"
        "图中两条概率之和不超过100%，余项为均未出现。这些是标签概率，不是直接可交易收益概率。"
        "一只历史固定观察池的后段检验不证明全市场稳定盈利。", "",
        f"图示抽样：{selection_text}。没有因图好坏更改统计分母。", "",
        "[打开完整图册](index.html) · [完整成交表](selected/holdout/trades.csv) · "
        "[逐笔订单](selected/holdout/ledger.csv) · [逐根决策](selected/holdout/decisions.csv)", "",
        "完整方法、预先选择与模型验证以研究运行器及独立方法文档为准；此文件只整理真实输出。"]
    if zero_trades:
        report += ["", "**后段零成交：胜率、盈亏赔率、买入成功率与卖点表现均不可评估。"
                   "账户收益为零不等于策略验证通过，也不能用开发期交易代替后段结果。**"]
    if len(monthly_table):
        report += ["", "## 每月观察池变化", "", "[完整月度名单与截面](MONTHLY_POOLS.csv)", "",
                   "|月份|选股截面|观察股数|新入池|离开池|延续|当月历史题材|",
                   "|---|---|---|---|---|---|---|"]
        for _, item in monthly_table.iterrows():
            report.append("|" + "|".join(str(item[c]) for c in monthly_table.columns) + "|")
        report += ["", "训练样本与入场候选应按其决策月份的真实名单归属筛选；"
                   "不能把后来新入池股票的更早数据当作当时已拥有的样本。"
                   "本轮Apr-Sep已有项目研究接触，不称从未看过的纯盲留出。"]
    report += ["", "## 留出对照", "", "|策略与入场份额|净收益|最大回撤|胜率|平均盈亏赔率|",
               "|---|---|---|---|---|"]
    for row in comparisons:
        payoff = row["payoff_ratio"]
        report.append(f"|{row['policy']}|{_percent(row['net_return'])}|"
                      f"{_percent(row['max_drawdown'])}|{_percent(row['win_rate'])}|"
                      f"{float(payoff):.2f}|" if payoff is not None else
                      f"|{row['policy']}|{_percent(row['net_return'])}|"
                      f"{_percent(row['max_drawdown'])}|{_percent(row['win_rate'])}|未知|")
    report += ["", "控制组入场份额不同，账户收益与回撤比较混有仓位差异；"
               "不能将较低回撤直接视为概率模型改善了卖点，更不能声称找到了最优卖点。"]
    if probability_audit.get("n_temporal"):
        report += ["", "## 概率验收", "",
            f"后段 {probability_audit['n_temporal']} 行 / {probability_audit['temporal_dates']} 日期。"
            f"延伸预测均值 {_percent(probability_audit['temporal_predicted_extend'])}，"
            f"实际标签频率 {_percent(probability_audit['temporal_actual_extend'])}；"
            f"Brier {probability_audit['temporal_brier']:.3f}，常数基准 {probability_audit['temporal_constant_brier']:.3f}。", "",
            "四交易日结果重叠，不能把行数当成独立样本量。失败的模型诊断保留，"
            "不得修改过去交易后重新声称通过。"]
    (out / "REPORT.md").write_text("\n".join(report) + "\n")
    def href(path):
        return html.escape(str(Path(path).relative_to(out)))
    cards = []
    if fund:
        cards.append(f'<section><h2>留出账户资金曲线</h2><img src="{href(fund)}"></section>')
    cards.append(f'<section><h2>同规则留出对照：仓位差异必须同时考虑</h2>'
                 f'<img src="{href(comparison_plot)}"></section>')
    if probability_plot:
        cards.append(f'<section><h2>概率验收：失败预测不能当成真实胜率</h2>'
                     f'<p>{html.escape(probability_audit["message"])}</p>'
                     f'<img src="{href(probability_plot)}"></section>')
    if len(monthly_table):
        cards.append(f'<section><h2>每月最多20只：{len(monthly_table)}个月观察池变化</h2>'
                     '<p><a href="MONTHLY_POOLS.csv">完整月度观察名单</a>；累计股票是历月并集，'
                     '不表示一次观察全部股票。此轮后段不是从未接触的纯盲留出。</p>'
                     + monthly_table.to_html(index=False, escape=True) + '</section>')
    cohort_title = f"首月历史池示例：截至{example_selection['as_of']}" if len(monthly_table) else "固定历史观察池：仅展示选择日前"
    cards.append(f'<section><h2>{html.escape(cohort_title)}</h2>')
    for item in cohort:
        cards.append(f'<details><summary>{html.escape(item["ts_code"] + " " + item["name"])}</summary>'
                     f'<img src="{href(item["path"])}"></details>')
    cards.append('</section><section><h2>买入与分批卖出案例：回放展示</h2>'
                 f'<p>{html.escape(selection_text)}</p>')
    for item in cases:
        cards.append(f'<details open><summary>{html.escape(item["ts_code"])} '
                     f'净收益 {_percent(item["net_return"])}</summary>'
                     f'<img src="{href(item["path"])}"></details>')
    cards.append('</section><section><h2>买入决策截面与抵扣情景：不含实际未来</h2>'
                 '<p>前5个展示案例，日线只截至前一交易日；右侧是恒价16根均线抵扣情景，'
                 '没有真实未来K线或卖点。</p>')
    for item in decision_charts:
        cards.append(f'<details><summary>{html.escape(item["ts_code"])} 决策截面</summary>'
                     f'<img src="{href(item["path"])}"></details>')
    cards.append('</section><section><h2>全部成交记录</h2><p>完整分母，包括未平仓与异常记录。</p>')
    showcols = [c for c in ("ts_code", "entry_time", "exit_time", "closed", "buys", "sells",
                           "net_return", "exit_reason", "issue_codes") if c in trades]
    table = trades[showcols].copy()
    if "net_return" in table:
        table["net_return"] = table.net_return.map(_percent)
    cards.append(table.to_html(index=False, escape=True) if not zero_trades else
                 '<p>后段无成交：胜率、赔率、成功率与卖点表现均不可评估。</p>')
    cards.append('</section>')
    warning = "现金公司行动核验通过。" if verified else "现金公司行动核验未通过，结果只能作为条件性参考。"
    warning += " " + probability_audit.get("message", "概率验收未知") + "。"
    page = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>日线趋势回调重启图册</title>' \
           '<style>body{max-width:1500px;margin:auto;padding:22px;background:#f5f7f8;color:#253540;' \
           'font:16px/1.65 system-ui}section{background:white;padding:20px;margin:20px 0;border-radius:9px}' \
           'img{max-width:100%;height:auto}summary{cursor:pointer;font-weight:600;padding:10px}' \
           'table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #ddd;padding:5px}' \
           '.warning{background:#fff4da;padding:15px}</style><h1>日线趋势 × 15分钟回调重启</h1>' \
           f'<p class="warning">{html.escape(warning)} 所有案例图均为回放展示，含买入后的未来行情。' \
           '条件概率不是保证，T+1可能使止损延迟。</p>' \
           '<p><a href="REPORT.md">结果报告</a> · <a href="selected/holdout/trades.csv">交易CSV</a> · ' \
           '<a href="selected/holdout/ledger.csv">分批订单CSV</a></p>' + ''.join(cards) + '</html>'
    (out / "index.html").write_text(page)
    manifest = {"cohort_charts": len(cohort), "case_charts": len(cases),
                "decision_charts": len(decision_charts),
                "case_selection": selection_text, "seed": 20261002,
                "all_trades": len(trades), "cash_accounting_verified": verified,
                "risk_acceptance_failed": acceptance,
                "probability_acceptance": probability_audit.get("status", "unknown"),
                "monthly_mode": bool(len(monthly_table)), "monthly_cohorts": len(monthly_table),
                "union_stock_count": union_count if len(monthly_table) else len(pool),
                "cohort_example_as_of": example_selection.get("as_of"),
                "cohort_example_month": example_selection.get("example_month"),
                "zero_trade_metrics_evaluable": not zero_trades}
    (out / "REVIEW_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output, summary_only=args.summary_only), ensure_ascii=False, indent=2))
