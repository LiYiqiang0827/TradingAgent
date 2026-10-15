"""Render the frozen September coil replay and its seven development examples."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "policyStudy/policy/题材涨停研究/scripts"))
from study_fifteen_min_ma_coil import Rules, features, find_signals, load_rules, coil_description
from replay_fifteen_min_ma_coil import EXAMPLES
from plot.plot_daily_kline import MA_COLORS, _configure_chinese_font, _draw_candles
from plot.plot_fifteen_min_kline import _apply_datetime_ticks, _draw_session_guides


def raw_stock(out: Path, code: str) -> pd.DataFrame:
    files = sorted((out / "inputs").glob("bars_*.parquet"))
    return pd.read_parquet([str(p) for p in files], filters=[("ts_code", "=", code)]).sort_values("datetime")


def plot_stock(f: pd.DataFrame, trades: pd.DataFrame, code: str, name: str, first: str, last: str,
               path: Path, zoom: bool = False) -> None:
    selected = trades[trades.ts_code.eq(code)].sort_values("signal_time")
    visible = f[f.datetime.dt.strftime("%Y%m%d").between(first, last)].copy()
    if zoom and len(selected):
        t = pd.Timestamp(selected.iloc[0].signal_time)
        i = int(np.flatnonzero(visible.datetime.ge(t).to_numpy())[0])
        visible = visible.iloc[max(0, i - 40):i + 50].copy()
    if visible.empty: return
    # Chart-only QFQ normalization to the display cutoff. Signal ratios are invariant.
    factors = f.adj_factor.dropna()
    factor_scale = float(factors.iloc[0] / factors.iloc[-1])
    for c in ("open", "high", "low", "close"):
        visible[c] = visible[f"p_{c}"] * factor_scale
    for n in (5, 10, 20, 30, 60, 120, 250):
        visible[f"ma{n}"] *= factor_scale
    visible["date"] = visible.datetime.dt.normalize()
    visible = visible.reset_index(drop=True)
    _configure_chinese_font()
    fig, (ax, vol) = plt.subplots(2, 1, figsize=(17, 9), sharex=True,
                                 gridspec_kw={"height_ratios": [4, 1], "hspace": .06})
    colors = _draw_candles(ax, visible)
    x = np.arange(len(visible))
    for n in (5, 10, 20, 30, 60, 120, 250):
        ax.plot(x, visible[f"ma{n}"], label=f"MA{n}", color=MA_COLORS[n],
                lw=1.05 if n < 120 else 1.45)
    vol.bar(x, visible.vol / 10000, width=.67, color=colors, alpha=.8)
    for a in (ax, vol):
        a.grid(alpha=.2); _draw_session_guides(a, visible)
    index = {pd.Timestamp(t): i for i, t in enumerate(visible.datetime)}
    notes = []
    for j, r in enumerate(selected.itertuples()):
        signal, coil = pd.Timestamp(r.signal_time), pd.Timestamp(r.coil_time)
        if signal not in index: continue
        i = index[signal]
        if coil in index:
            k = index[coil]
            for a in (ax, vol): a.axvspan(k - .5, k + .5, color="#00b6b8", alpha=.25)
            ax.scatter([k], [visible.close.iloc[k]], s=58, color="#008d96", marker="o", zorder=8)
        ax.axvline(i, color="#7b2cbf", ls="--", lw=1.)
        ax.scatter([i], [visible.close.iloc[i]], s=90, color="#7b2cbf", marker="^", zorder=9)
        ax.annotate(f"确认{signal:%m-%d %H:%M}", (i, visible.close.iloc[i]),
                    xytext=(-50, 38 + 10 * (j % 2)), textcoords="offset points", fontsize=9,
                    arrowprops={"arrowstyle": "->", "color": "#7b2cbf"}, color="#7b2cbf")
        if bool(r.buyable_proxy) and pd.notna(r.entry_time):
            entry = pd.Timestamp(r.entry_time)
            position = int(np.searchsorted(visible.datetime.to_numpy(), np.datetime64(entry), side="left"))
            if position < len(visible):
                y = float(r.entry_open) * float(r.signal_factor) / float(factors.iloc[-1])
                ax.scatter([position], [y], marker="*", s=145, color="#d98200", edgecolor="black", lw=.4, zorder=10)
                ax.annotate(f"买入代理{entry:%H:%M}", (position, y), xytext=(18, -38),
                            textcoords="offset points", fontsize=9,
                            arrowprops={"arrowstyle": "->", "color": "#9c6200"}, color="#9c6200")
        notes.append(f"{signal:%m-%d %H:%M}  {coil_description(r)} / 前周波幅{r.pre_restart_range_pct:.2f}% / 量比{r.restart_volume_multiple8:.2f}")
    ax.set_title(f"{name} {code}  15分钟均线粘合再启动 | {first}—{last}\n"
                 f"青色：已确认粘合；紫色：信号收完；橙星：入场价格代理 | 匹配{len(selected)}次", fontsize=14)
    ax.set_ylabel("前复权价格（元）"); vol.set_ylabel("成交量（万股）")
    ax.legend(loc="upper left", ncol=7, fontsize=8)
    ax.set_xlim(-1, len(visible)); _apply_datetime_ticks(vol, visible)
    vol.set_xlabel("每天首根15分钟K标记时间09:45；分钟成交时刻另行注明")
    if not len(selected): notes = ["本期没有完整匹配；不能为了覆盖已知牛股放宽门槛。具体诊断见七例条件检查.csv。"]
    elif selected.coil_time.min()[:10] < str(visible.datetime.min().date()):
        notes.append(f"前置粘合在预热期：{selected.coil_time.min()}，未在本期图中展开。")
    observed = int(visible.valid_bar.sum())
    if observed < len(visible):
        if code == "605577.SH":
            notes.append("9/9—11已核实停牌：保留日历空档；本版复牌后长均线重新预热。")
        else:
            notes.append(f"离线源缺{len(visible) - observed}根预期K：保持空白，不将缺失时段压缩。")
    fig.text(.08, .018, "\n".join(notes[:3]), fontsize=9, color="#334155")
    fig.subplots_adjust(left=.07, right=.985, top=.89, bottom=.17 if len(notes) > 1 else .13)
    path.parent.mkdir(exist_ok=True, parents=True)
    fig.savefig(path, dpi=150, facecolor="white"); plt.close(fig)


def bootstrap_means(trades: pd.DataFrame, prefix: str = "") -> dict:
    result = {}
    for h in (1, 3, 5):
        col = f"{prefix}d{h}_net"
        b = trades[trades[col].notna()].copy()
        b["day"] = pd.to_datetime(b.entry_time).dt.strftime("%Y%m%d")
        groups = b.groupby("day")[col].agg(["sum", "count"])
        if len(groups) < 2: result[f"d{h}"] = None; continue
        rng = np.random.default_rng(20260930 + h)
        choices = rng.integers(0, len(groups), size=(4000, len(groups)))
        vals = groups["sum"].to_numpy()[choices].sum(axis=1) / groups["count"].to_numpy()[choices].sum(axis=1)
        result[f"d{h}"] = {"date_clusters": len(groups), "interval_pct": (100 * np.quantile(vals, [.025, .975])).tolist()}
    return result


def build(out: Path) -> None:
    m = json.loads((out / "FROZEN_MANIFEST.json").read_text())
    rules = load_rules(out / m.get("rules_file", "rules_1.5.json"))
    trades = pd.read_csv(out / "trades.csv")
    summary = json.loads((out / "summary.json").read_text())
    checks, prefix_count = [], 0
    image_dir = out / "charts"; image_dir.mkdir(exist_ok=True)
    for code in EXAMPLES:
        raw = raw_stock(out, code)
        f = features(raw, m["calendar"], rules)
        current = find_signals(f, code, str(raw.name.iloc[-1]), rules, m["first"], m["last"])
        actual = trades[trades.ts_code.eq(code)].sort_values("signal_time")
        assert (list(current.signal_time) if len(current) else []) == list(actual.signal_time)
        for t in actual.signal_time:
            cutoff = pd.Timestamp(t)
            pf = features(raw[raw.datetime.le(cutoff)], m["calendar"], rules)
            signals = find_signals(pf, code, str(raw.name.iloc[-1]), rules, m["first"], m["last"])
            expected = current[current.signal_time.le(t)]
            pd.testing.assert_frame_equal(signals.reset_index(drop=True), expected.reset_index(drop=True), check_dtype=False)
            prefix_count += 1
        for suffix, zoom in (("全期", False), ("局部", True)):
            plot_stock(f, trades, code, str(raw.name.iloc[-1]), m["first"], m["last"],
                       image_dir / f"{code}_{suffix}.jpg", zoom=zoom)
        s = f[f.datetime.dt.strftime("%Y%m%d").between(m["first"], m["last"])]
        diagnostics = {"股票代码": code, "股票名称": str(raw.name.iloc[-1]),
                       "有效K线": int(s.valid_bar.sum()), "预期K线": len(s),
                       "粘合K线数": int(s.cluster_only.sum()), "完整粘合候选数": int(s.coil_ready.sum()),
                       "确认信号数": len(current)}
        if len(current):
            row = current.iloc[0]; diagnostics.update({"首次确认": row.signal_time,
                "粘合幅度%": row.coil_spread_pct, "起涨前周波幅%": row.pre_restart_range_pct,
                "诊断": "本次定义匹配；不代表具备未来筛选优势"})
        elif not s.cluster_only.any():
            diagnostics["诊断"] = (f"没有两组同时≤{rules.coil_pct:g}%/{rules.coil_with_ma60_pct:g}%的粘合"
                                   if rules.coil_mode == "dual" else f"没有原分支≤{rules.coil_pct:g}%的粘合")
            diagnostics["诊断"] += "；涨停前结构可能在8月"
        elif not s.coil_ready.any():
            diagnostics["诊断"] = "存在粘合；叠加慢回调、长期趋势、前期多头及起涨来源后没有完整候选"
        else:
            diagnostics["诊断"] = "存在完整粘合候选；随后两根内未同时满足放量阳线、80根区间≤10%、六线严格多头且上升"
        if code == "605577.SH":
            diagnostics["诊断"] += "；9/9—11停牌已核实，复牌段长均线重新预热，覆盖有限"
        checks.append(diagnostics)
    pd.DataFrame(checks).to_csv(out / "七例条件检查.csv", index=False, encoding="utf-8-sig")
    eligible = trades[~trades.entry_status.str.startswith("excluded_")].copy()
    columns = {"ts_code": "股票代码", "name": "股票名称", "signal_time": "信号确认时间", "coil_time": "粘合时间",
               "entry_time": "买入代理时刻", "entry_open": "买入代理价格", "prior_theme_name": "前日已知题材",
               "coil_spread_pct": "粘合幅度%", "pre_restart_range_pct": "前周波幅%", "entry_status": "入场状态",
               "limit_fill_assumption": "涨停成交假设", "d1_net": "1日净收益%", "d3_net": "3日净收益%", "d5_net": "5日净收益%",
               "d1_status": "1日状态", "d3_status": "3日状态", "d5_status": "5日状态"}
    table = eligible[list(columns)].rename(columns=columns)
    for n in (1, 3, 5): table[f"{n}日净收益%"] *= 100
    table.to_csv(out / "9月1至15日_非ST信号与收益.csv", index=False, encoding="utf-8-sig")
    _configure_chinese_font()
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    for ax, h in zip(axes, (1, 3, 5)):
        v = trades[f"d{h}_net"].dropna() * 100
        ax.hist(v, bins=35, color="#3d7c9b", edgecolor="white")
        ax.axvline(0, color="black", lw=.8)
        ax.axvline(v.mean(), color="#b52b2b", ls="--", label=f"均值{v.mean():.2f}%")
        ax.set_title(f"入场后{h}个交易日 | 可评分{len(v)}例")
        ax.set_xlabel("净收益%（费用+双边滑点约0.31%）"); ax.set_ylabel("例数"); ax.legend(fontsize=9)
    fig.suptitle("9月1—15日15分钟粘合再启动 | 涨停可买按指定假设计入；未完成单列", fontsize=13)
    fig.tight_layout(); fig.savefig(image_dir / "净收益分布.jpg", dpi=150); plt.close(fig)
    ci = {"all": bootstrap_means(trades), "prior_limit_up": bootstrap_means(trades[trades.recent_limit_up_10sessions])}
    (out / "mean_uncertainty.json").write_text(json.dumps(ci, ensure_ascii=False, indent=2))
    (out / "prefix_validation.json").write_text(json.dumps({"real_examples": len(EXAMPLES),
        "real_prefixes_checked": prefix_count, "signal_sets_recomputed_equal": True,
        "unit_regressions": 15}, indent=2))
    cards = []
    for check in checks:
        code = check["股票代码"]; title = f"{check['股票名称']} {code} | 信号{check['确认信号数']}次"
        cards.append(f'<section><h2>{html.escape(title)}</h2><p>{html.escape(check["诊断"])}</p>'
                     f'<img src="charts/{code}_全期.jpg" alt="{html.escape(title)}"><details><summary>局部放大</summary>'
                     f'<img src="charts/{code}_局部.jpg"></details></section>')
    for code, note in (("001366.SZ", "播恩集团：前日农业强题材信号与次日涨停成交假设"),
                       ("000430.SZ", "张家界：符合形态，但涨停假设入场后仍下跌")):
        raw = raw_stock(out, code); f = features(raw, m["calendar"], rules)
        path = image_dir / f"{code}_对照.jpg"
        plot_stock(f, trades, code, str(raw.name.iloc[-1]), m["first"], m["last"], path)
        cards.append(f'<section><h2>对照案例：{html.escape(note)}</h2><p>供复核，未按对照案例调整信号参数。</p>'
                     f'<img src="charts/{path.name}"></section>')
    page = '<!doctype html><meta charset="utf-8"><title>9月15分钟粘合再启动回放</title><style>body{max-width:1450px;margin:30px auto;font:16px Arial;background:#f6f8fa;color:#223}section{background:white;padding:18px;margin:20px 0}img{width:100%}p{line-height:1.6}</style>'
    page += f'<h1>9月1—15日：15分钟均线粘合再启动</h1><p>合资格技术信号{len(eligible)}次（剔除ST与次新），入场估算{int(trades.buyable_proxy.sum())}次。样本来自已知牛股，不能当独立检验；完整表、分母与方法见报告。</p>'
    page += '<img src="charts/净收益分布.jpg">' + ''.join(cards)
    (out / "review.html").write_text(page)
    print(pd.DataFrame(checks).to_string(index=False))
    print(json.dumps(ci, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", default="outputs/fifteen_min_ma_coil_20260901_20260915_v2")
    build(Path(parser.parse_args().output))
