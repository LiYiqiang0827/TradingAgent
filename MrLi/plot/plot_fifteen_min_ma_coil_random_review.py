"""Random, outcome-independent visual audit of an already frozen 15m replay."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import sys
import zipfile

import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "policyStudy/policy/题材涨停研究/scripts"))
from study_fifteen_min_ma_coil import Rules, features, find_signals, load_rules, coil_description
from plot.plot_daily_kline import MA_COLORS, _configure_chinese_font, _draw_candles
from plot.plot_fifteen_min_kline import _apply_datetime_ticks, _draw_session_guides


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry_position(visible: pd.DataFrame, row: pd.Series) -> float | None:
    """Map a minute execution time into its actual 15m interval, not its end."""
    if not bool(row.buyable_proxy) or pd.isna(row.entry_time):
        return None
    entry = pd.Timestamp(row.entry_time)
    if bool(row.limit_fill_assumption):
        locations = np.flatnonzero(visible.datetime.eq(entry).to_numpy())
        return float(locations[0]) if len(locations) else None
    positions = np.flatnonzero(visible.datetime.gt(entry).to_numpy())
    if not len(positions):
        return None
    j = int(positions[0])
    end = visible.datetime.iloc[j]
    start = end - pd.Timedelta(minutes=15)
    if not start <= entry < end:
        return None
    return j - 1 + float((entry - start) / pd.Timedelta(minutes=15))


def draw_panel(ax, vol, frame: pd.DataFrame, row: pd.Series, scale: float,
               title: str, detail: bool, signal_label: str = "信号收完") -> dict:
    visible = frame.copy().reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        visible[col] = visible[f"p_{col}"] * scale
    for n in (5, 10, 20, 30, 60, 120, 250):
        visible[f"ma{n}"] *= scale
    visible["date"] = visible.datetime.dt.normalize()
    colors = _draw_candles(ax, visible)
    x = np.arange(len(visible))
    for n in (5, 10, 20, 30, 60, 120, 250):
        ax.plot(x, visible[f"ma{n}"], color=MA_COLORS[n], lw=1.3 if n < 120 else 1.8,
                label=f"MA{n}")
    # TDX vol is shares. Never adjust volume along with price.
    vol.bar(x, visible.vol / 10000, width=.68, color=colors, alpha=.85)
    index = {pd.Timestamp(t): i for i, t in enumerate(visible.datetime)}
    si, ci = index[pd.Timestamp(row.signal_time)], index[pd.Timestamp(row.coil_time)]
    for a in (ax, vol):
        a.grid(alpha=.16)
        _draw_session_guides(a, visible)
        a.axvspan(si + .08, len(visible), facecolor="#e2e8f0", alpha=.55, zorder=0)
        a.axvspan(ci - .5, ci + .5, facecolor="#00b6b8", alpha=.28, zorder=1)
        a.axvline(si, color="#7b2cbf", ls="--", lw=1.15, zorder=6)
    ax.scatter(ci, visible.close.iloc[ci], marker="o", color="#008b94", s=65, zorder=8)
    ax.scatter(si, visible.close.iloc[si], marker="^", color="#7b2cbf", s=115, zorder=9)
    vol.bar([si], [visible.vol.iloc[si] / 10000], width=.72, color="#7b2cbf", zorder=6)
    if pd.notna(row.impulse_time) and pd.Timestamp(row.impulse_time) in index:
        ii = index[pd.Timestamp(row.impulse_time)]
        ax.scatter(ii, visible.close.iloc[ii], marker="D", color="#d46919", s=55, zorder=8)
        if not detail:
            ax.annotate("前置放量阳线", (ii, visible.close.iloc[ii]), xytext=(-20, 35),
                        textcoords="offset points", fontsize=10, color="#a84f0d",
                        arrowprops={"arrowstyle": "->", "color": "#a84f0d"},
                        bbox={"facecolor": "white", "edgecolor": "none", "alpha": .8})
    ax.annotate(f"{signal_label} {pd.Timestamp(row.signal_time):%m-%d %H:%M}",
                (si, visible.close.iloc[si]), xytext=(12, 34), textcoords="offset points",
                fontsize=10, color="#7b2cbf", arrowprops={"arrowstyle": "->", "color": "#7b2cbf"},
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": .9})
    ep = entry_position(visible, row)
    if ep is not None:
        y = float(row.entry_open)  # QFQ chart is normalized at the signal date.
        ax.scatter(ep, y, marker="*", color="#d98200", edgecolor="black", s=165, lw=.4, zorder=10)
        label = "涨停假设买入" if bool(row.limit_fill_assumption) else "分钟入场代理"
        ax.annotate(f"{label} {pd.Timestamp(row.entry_time):%m-%d %H:%M}  {y:.2f}",
                    (ep, y), xytext=(15, -38), textcoords="offset points", fontsize=10,
                    color="#965b00", arrowprops={"arrowstyle": "->", "color": "#965b00"},
                    bbox={"facecolor": "white", "edgecolor": "none", "alpha": .9})
    ax.set_title(title, loc="left", fontsize=12, pad=9)
    ax.set_ylabel("信号日复权价格（元）", fontsize=10)
    vol.set_ylabel("成交量（万股）", fontsize=10)
    ax.legend(loc="upper left", ncol=7, fontsize=9)
    ax.set_xlim(-1, len(visible))
    ax.tick_params(labelbottom=False)
    _apply_datetime_ticks(vol, visible)
    vol.tick_params(axis="x", labelsize=9)
    return {"first": str(visible.datetime.iloc[0]), "last": str(visible.datetime.iloc[-1]),
            "bars": len(visible), "valid_bars": int(visible.valid_bar.sum()),
            "signal_position": si, "coil_position": ci, "entry_position": ep}


def chart(f: pd.DataFrame, row: pd.Series, number: int, total: int, path: Path,
          cutoff: str, lookback_days: int, lookahead_days: int) -> dict:
    signal, coil = pd.Timestamp(row.signal_time), pd.Timestamp(row.coil_time)
    display_start = signal.normalize() - pd.Timedelta(days=lookback_days)
    display_end = min(signal.normalize() + pd.Timedelta(days=lookahead_days),
                      pd.Timestamp(cutoff) + pd.Timedelta(hours=23, minutes=59))
    context = f[f.datetime.between(display_start, display_end)].copy()
    # Include both 250-bar background and a separate readable 80-before/32-after detail.
    locations = np.flatnonzero(f.datetime.eq(signal).to_numpy())
    si = int(locations[0])
    detail = f.iloc[max(0, si - 80):si + 33].copy()
    detail = detail[detail.datetime.le(display_end)]
    assert context.datetime.eq(signal).any() and context.datetime.eq(coil).any()
    assert detail.datetime.eq(signal).any() and detail.datetime.eq(coil).any()
    base = float(f.adj_factor.dropna().iloc[0])
    scale = base / float(row.signal_factor)
    fig = plt.figure(figsize=(21, 14))
    gs = fig.add_gridspec(5, 1, height_ratios=[3.6, 1.05, .4, 3.6, 1.05], hspace=.09)
    top = fig.add_subplot(gs[0]); top_vol = fig.add_subplot(gs[1], sharex=top)
    bottom = fig.add_subplot(gs[3]); bottom_vol = fig.add_subplot(gs[4], sharex=bottom)
    bg = draw_panel(top, top_vol, context, row, scale,
                    f"背景：信号前{lookback_days}个自然日，后{lookahead_days}日（全部不晚于9月15日）", False)
    local = draw_panel(bottom, bottom_vol, detail, row, scale,
                       "局部：前80根 / 后32根 | 青色粘合、紫色确认、橙星入场；右侧灰底为事后回看区", True)
    origin = "前置放量阳线" if row.origin == "volume_impulse" else "MA持续上升退化分支"
    fig.suptitle(f"随机样本 {number:02d}/{total}  ·  {row['name']} {row.ts_code}\n"
                 f"确认时刻 {signal:%Y-%m-%d %H:%M}  |  原始冻结规则，未为图形修改参数", fontsize=18, y=.98)
    same_slot = float(row.restart_volume_multiple_same_slot5)
    footer = [
        f"粘合 {coil:%m-%d %H:%M}  {coil_description(row)}  ·  前周高低波幅 {row.pre_restart_range_pct:.2f}%  ·  起涨来源：{origin}",
        f"回调 {row.pullback_pct:.2f}% / {int(row.pullback_bars)}根  ·  再启动实体 {row.restart_body_pct:.2f}%  ·  量比：前8根 {row.restart_volume_multiple8:.2f}倍 / 前5日同时段 {same_slot:.2f}倍（后者只作诊断）",
        "信号条件只读收完前数据；灰底行情用于人工复核。星标为成交估算/指定涨停假设，未制定动态卖点。横轴取每日首个可见K。",
    ]
    fig.text(.065, .018, "\n".join(footer), fontsize=10, color="#334155", linespacing=1.7)
    fig.subplots_adjust(left=.065, right=.988, top=.90, bottom=.13)
    fig.savefig(path, dpi=150, facecolor="white"); plt.close(fig)
    return {"number": number, "ts_code": row.ts_code, "name": row['name'],
            "signal_time": row.signal_time, "coil_time": row.coil_time,
            "context": bg, "detail": local, "image_sha256": digest(path), "image": path.name}


def build(replay: Path, seed: int, count: int, lookback: int, lookahead: int) -> Path:
    manifest = json.loads((replay / "FROZEN_MANIFEST.json").read_text())
    frozen = replay / "signals_before_outcomes.csv"
    assert digest(frozen) == manifest["signal_file_sha256"]
    rules = load_rules(replay / manifest.get("rules_file", "rules_1.5.json"))
    trades = pd.read_csv(replay / "trades.csv")
    # Eligibility was already established independently of future return. Selection
    # reads identities only; returns, winner labels and examples never enter RNG.
    eligible = trades[~trades.entry_status.str.startswith("excluded")].copy()
    pool = eligible[["ts_code", "signal_time"]].sort_values(["signal_time", "ts_code"]).reset_index(drop=True)
    if not 1 <= count <= len(pool) or lookback < 1 or lookahead < 0:
        raise ValueError("Sample size must fit the pool; lookback>=1 and lookahead>=0")
    chosen = pool.iloc[np.random.default_rng(seed).choice(len(pool), count, replace=False)]
    chosen = chosen.sort_values(["signal_time", "ts_code"])
    out = replay / f"random_{count}_seed{seed}"; images = out / "charts"
    images.mkdir(parents=True, exist_ok=True)
    sampling = {"seed": seed, "generator": "numpy.default_rng.PCG64", "sample_size": count,
                "pool_size": len(pool), "pool_sha256": hashlib.sha256(pool.to_csv(index=False).encode()).hexdigest(),
                "selection_uses_returns": False, "selection_uses_example_list": False,
                "signal_source_sha256": digest(frozen), "selected": chosen.to_dict(orient="records")}
    sampling_path = out / "SAMPLING_MANIFEST.json"
    if sampling_path.exists():
        assert json.loads(sampling_path.read_text()) == sampling, "Refusing to replace frozen sample"
    else:
        sampling_path.write_text(json.dumps(sampling, ensure_ascii=False, indent=2))
    selected = chosen.merge(trades, on=["ts_code", "signal_time"], validate="one_to_one")
    selected.to_csv(out / "随机20次信号.csv", index=False, encoding="utf-8-sig")
    raw = pd.read_parquet([str(p) for p in sorted((replay / "inputs").glob("bars_*.parquet"))],
                          filters=[("ts_code", "in", selected.ts_code.unique().tolist())])
    raw["datetime"] = pd.to_datetime(raw.datetime)
    _configure_chinese_font()
    accepted, cards = [], []
    for number, (_, row) in enumerate(selected.iterrows(), start=1):
        stock = raw[raw.ts_code.eq(row.ts_code)].sort_values("datetime")
        f = features(stock, manifest["calendar"], rules)
        prefix = features(stock[stock.datetime.le(pd.Timestamp(row.signal_time))], manifest["calendar"], rules)
        pf = find_signals(prefix, row.ts_code, row['name'], rules, manifest["first"], manifest["last"])
        assert pf.signal_time.eq(row.signal_time).any(), f"Signal failed prefix check: {row.ts_code}"
        filename = f"{number:02d}_{row.ts_code}_{pd.Timestamp(row.signal_time):%Y%m%d_%H%M}.jpg"
        receipt = chart(f, row, number, count, images / filename, manifest["last"], lookback, lookahead)
        accepted.append(receipt)
        caption = f"{number:02d}. {row['name']} {row.ts_code} · {row.signal_time}"
        cards.append(f'<article id="case{number}"><h2>{html.escape(caption)}</h2>'
                     f'<p>粘合 {row.coil_spread_pct:.2f}% · 回调 {row.pullback_pct:.2f}% · '
                     f'前8根量比 {row.restart_volume_multiple8:.2f} / 同时段量比 {row.restart_volume_multiple_same_slot5:.2f}</p>'
                     f'<a href="charts/{filename}" target="_blank"><img loading="lazy" src="charts/{filename}"></a>'
                     f'<a href="#top">回到目录</a></article>')
        print(f"[{number}/{count}] {row['name']} {row.ts_code} {row.signal_time}", flush=True)
    navigation = " ".join(f'<a href="#case{i}">{i:02d}</a>' for i in range(1, count + 1))
    page = '<!doctype html><meta charset="utf-8"><title>15分钟信号随机20例</title><style>'
    page += 'body{font-family:system-ui;margin:24px;background:#f1f5f9;color:#1e293b}article{background:white;padding:18px;margin:24px 0;border-radius:12px}img{width:100%;height:auto}nav{position:sticky;top:0;background:white;padding:14px;z-index:10}nav a{margin-right:14px}h2{font-size:20px}</style>'
    page += f'<h1 id="top">15分钟信号随机{count}例</h1><p>从410次非ST、非次新合资格信号等概率抽取；种子{seed}，不按收益挑图。每张图包含背景与局部，均有实际成交量及七条均线；灰底是信号之后真实行情，最多到9月15日。</p>'
    page += f'<nav>{navigation}</nav>' + "\n".join(cards)
    (out / "review.html").write_text(page)
    # A contact sheet is navigation only; all twenty full-resolution JPEGs remain.
    thumb_w, thumb_h = 900, 630
    columns = 4; rows = (count + columns - 1) // columns
    overview = Image.new("RGB", (columns * thumb_w, rows * thumb_h), "#f1f5f9")
    draw = ImageDraw.Draw(overview)
    font = ImageFont.truetype(font_manager.findfont("Arial Unicode MS"), 25)
    for i, item in enumerate(accepted):
        x, y = i % columns * thumb_w, i // columns * thumb_h
        im = Image.open(images / item["image"]); im.thumbnail((thumb_w - 16, thumb_h - 55))
        overview.paste(im, (x + (thumb_w - im.width) // 2, y + 50))
        draw.text((x + 12, y + 10), f"{i+1:02d} {item['name']} {item['signal_time'][5:16]}", fill="#1e293b", font=font)
    overview.save(out / "20例缩略总览.jpg", quality=92)
    readme = f"随机种子{seed}，从410次合资格信号等概率不放回抽{count}次，不参考收益。\n每张JPG包含背景和局部、成交量、MA5/10/20/30/60/120/250。\n青色=粘合；紫色=信号确认；橙星=成交代理/指定涨停假设；灰底=事后行情，截止9月15日。\n双击review.html逐张看图，点击图片打开原图。\n"
    (out / "说明.txt").write_text(readme)
    acceptance = {"images": len(accepted), "prefix_checks_passed": len(accepted),
                  "frozen_source_unchanged": digest(frozen) == manifest["signal_file_sha256"],
                  "latest_display_allowed": manifest["last"], "charts": accepted}
    (out / "ACCEPTANCE.json").write_text(json.dumps(acceptance, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(out / "随机20例_15分钟K线图.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for item in accepted:
            archive.write(images / item["image"], f"charts/{item['image']}")
        for name in ("review.html", "随机20次信号.csv", "SAMPLING_MANIFEST.json", "说明.txt"):
            archive.write(out / name, name)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", type=Path, default=ROOT / "outputs/fifteen_min_ma_coil_20260901_20260915_v2")
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--lookahead-days", type=int, default=7)
    args = parser.parse_args()
    print(build(args.replay_dir, args.seed, args.count, args.lookback_days, args.lookahead_days))
