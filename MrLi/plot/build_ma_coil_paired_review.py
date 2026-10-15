"""Measure seven development templates and render separate 15m/day chart pairs.

Template anchors are descriptive, retrospectively located first-board candles.
They never replace the frozen replay's signals or enter a performance test.
The random set reuses the exact prior 20 identities, without resampling.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
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
from coreClient.data_provider import get_day
from plot.plot_daily_kline import (MA_COLORS, _configure_chinese_font, _draw_candles,
                                  _apply_date_ticks, load_daily_data, resolve_window)
from plot.plot_fifteen_min_ma_coil_random_review import digest, draw_panel

TARGET_FIRST_BOARDS = {
    "600371.SH": "20260825", "000978.SZ": "20260907", "600540.SH": "20260827",
    "000993.SZ": "20260909", "605577.SH": "20260831", "600865.SH": "20260903",
    "002790.SZ": "20260908",
}
GROUPS = {
    "短四线粘合%": ["ma5", "ma10", "ma20", "ma30"],
    "价格+MA30组%": ["p_close", "ma5", "ma10", "ma20", "ma30"],
    "价格+MA60组%": ["p_close", "ma5", "ma10", "ma20", "ma60"],
    "价格+MA5至60%": ["p_close", "ma5", "ma10", "ma20", "ma30", "ma60"],
    "价格+MA5至120%": ["p_close", "ma5", "ma10", "ma20", "ma30", "ma60", "ma120"],
    "价格+七线%": ["p_close", "ma5", "ma10", "ma20", "ma30", "ma60", "ma120", "ma250"],
}


def measure(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for name, cols in GROUPS.items():
        result[name] = 100 * (result[cols].max(axis=1) / result[cols].min(axis=1) - 1)
        result.loc[result[cols].isna().any(axis=1), name] = np.nan
    return result


def target_cases(raw: pd.DataFrame, manifest: dict, rules: Rules,
                 events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cases, checks, two_bars = [], [], []
    for code, day in TARGET_FIRST_BOARDS.items():
        ev = events[events.ts_code.eq(code) & events.trade_date.astype(str).eq(day)]
        assert (ev.board_height.eq(1) & ev.tag.eq("涨停")).any(), f"Unverified reference day {code} {day}"
        stock = raw[raw.ts_code.eq(code)].sort_values("datetime")
        f = features(stock, manifest["calendar"], rules)
        session = f[f.datetime.dt.strftime("%Y%m%d").eq(day)]
        # Already-known development examples: largest positive body on the chosen
        # first-board day is a reproducible chart anchor, not a causal buy signal.
        j = int(session.body_pct.idxmax())
        anchor = f.iloc[j]
        preceding = measure(f.iloc[j-2:j])
        preceding["selected_spread"] = preceding[["价格+MA30组%", "价格+MA60组%"]].min(axis=1)
        k = int(preceding.selected_spread.idxmin())
        coil = f.iloc[k]
        group = 30 if coil.coil30_pct <= coil.coil60_pct else 60
        factor_scale = float(coil.p_close / coil.close)
        # No fictional entries for retrospectively chosen template observations.
        case = {"ts_code": code, "name": stock.name.iloc[-1], "signal_time": str(anchor.datetime),
                "signal_close": float(anchor.close), "signal_factor": float(anchor.adj_factor),
                "coil_time": str(coil.datetime), "coil_branch": f"MA{group}",
                "coil_spread_pct": float(coil[f"coil{group}_pct"]),
                "coil_low_raw": float(coil[f"coil{group}_low"] / factor_scale),
                "coil_high_raw": float(coil[f"coil{group}_high"] / factor_scale),
                "origin": coil.origin, "impulse_time": None, "buyable_proxy": False,
                "entry_time": None, "entry_open": None, "limit_fill_assumption": False,
                "restart_volume_multiple8": float(anchor.volume_multiple8),
                "restart_volume_multiple_same_slot5": float(anchor.volume_multiple_same_slot5),
                "restart_body_pct": float(anchor.body_pct), "case_kind": "retrospective_template"}
        cases.append(case)
        metrics = measure(f.iloc[[k]]).iloc[0]
        checks.append({"股票代码": code, "股票名称": case["name"], "首板参照日": day,
                       "起涨参照时刻": case["signal_time"], "粘合测量时刻": case["coil_time"],
                       "距离起涨根数": j-k, "较紧分支": f"MA{group}",
                       "选定粘合度%": case["coil_spread_pct"],
                       **{name: float(metrics[name]) for name in GROUPS},
                       "起涨实体涨幅%": float(anchor.body_pct),
                       "一字板观察": bool(session.body_pct.abs().max() < 1e-8),
                       "方法": "已知首板日最大阳线前1–2根，取原MA30/MA60两组中较紧时点；属于事后模板测量"})
        for lag, (_, bar) in zip((2, 1), preceding.iterrows()):
            two_bars.append({"股票代码": code, "股票名称": case["name"],
                             "起涨参照时刻": case["signal_time"], "距离起涨根数": lag,
                             "测量时刻": str(bar.datetime),
                             **{name: float(bar[name]) for name in GROUPS}})
    return pd.DataFrame(cases), pd.DataFrame(checks), pd.DataFrame(two_bars)


def minute_chart(f: pd.DataFrame, row: pd.Series, path: Path, cutoff: str) -> dict:
    signal = pd.Timestamp(row.signal_time)
    first = signal.normalize() - pd.Timedelta(days=30)
    last = min(signal.normalize() + pd.Timedelta(days=7), pd.Timestamp(cutoff) + pd.Timedelta(hours=23))
    visible = f[f.datetime.between(first, last)].copy()
    base = float(f.adj_factor.dropna().iloc[0]); scale = base / float(row.signal_factor)
    template = row.case_kind == "retrospective_template"
    fig, (ax, vol) = plt.subplots(2, 1, figsize=(18, 10), sharex=True,
                                 gridspec_kw={"height_ratios": [4, 1], "hspace": .05})
    receipt = draw_panel(ax, vol, visible, row, scale, "", False,
                         signal_label="起涨参照（事后定位）" if template else "信号收完")
    ax.set_title(f"{row['name']} {row.ts_code}  15分钟K线 | MA按15分钟根数计算\n"
                 f"观察 {signal:%Y-%m-%d %H:%M} · 粘合 {pd.Timestamp(row.coil_time):%m-%d %H:%M}  {coil_description(row)}", fontsize=14)
    vol.set_xlabel("横轴取每个交易日第一根09:45；灰底为观察时点之后，仅供回看")
    intro = "首板日最大实体阳线前1–2根取较紧时点；事后模板测量，不是原策略信号。" if template else "原冻结信号与原随机名单均不变；星标仅表示一分钟成交估算。"
    definition = ("第一组=close+MA5/10/20/30；第二组=第一组+MA60；同根同时通过。"
                  if row.get("rule_coil_mode") == "dual" else f"原分支口径：close+MA5/10/20+{row.coil_branch}。")
    notes = [intro,
             f"{definition}100×(最大/最小-1)。量柱为真实成交量，未随价格复权。",
             f"前8根量比 {row.restart_volume_multiple8:.2f} / 前5日同时段 {row.restart_volume_multiple_same_slot5:.2f}。MA120/250展示长期位置，不要求一起粘合。"]
    if row.ts_code == "605577.SH":
        notes.append("9月9—11日停牌，保留空档；复牌后均线重新预热，是旧回放的覆盖限制。")
    fig.text(.07, .015, "\n".join(notes), fontsize=9, color="#334155")
    fig.subplots_adjust(left=.07, right=.985, top=.88, bottom=.16)
    fig.savefig(path, dpi=150, facecolor="white"); plt.close(fig)
    receipt.update(image=path.name, image_sha256=digest(path))
    return receipt


def daily_chart(row: pd.Series, path: Path, cutoff: str, data_dir: Path) -> dict:
    signal = pd.Timestamp(row.signal_time); day = signal.normalize()
    window = resolve_window(start_date=None, end_date=None, trade_date=day.strftime("%Y%m%d"),
                            lookback_months=12, lookahead_months=1, ma_warmup_days=420, to_latest=False)
    window = replace(window, display_end=min(window.display_end, pd.Timestamp(cutoff)))
    df = load_daily_data(row.ts_code, window, qfq=True)
    if df.date.duplicated().any():
        raise ValueError("Duplicate daily dates")
    observed = np.flatnonzero(df.date.eq(day).to_numpy())
    if len(observed) != 1:
        raise ValueError(f"Missing observation day in daily database: {row.ts_code} {day}")
    i = int(observed[0])
    source = get_day(ts_code=row.ts_code, trade_date=day.strftime("%Y%m%d"), qfq=False,
                     source="database_only")
    if len(source) != 1:
        raise ValueError("Missing unadjusted daily source for marker scaling")
    marker_scale = float(df.close.iloc[i]) / float(source.close.iloc[0])
    data_path = data_dir / f"{row.ts_code}_{day:%Y%m%d}_daily.parquet"
    df.to_parquet(data_path, index=False)
    fig, (ax, vol) = plt.subplots(2, 1, figsize=(18, 10), sharex=True,
                                 gridspec_kw={"height_ratios": [4, 1], "hspace": .05})
    for a in (ax, vol):
        a.grid(alpha=.2)
        # The complete signal-day daily candle was not yet known intraday.
        a.axvspan(i-.5, len(df), color="#e2e8f0", alpha=.7, zorder=0)
        a.axvspan(i-.5, i+.5, color="#7b2cbf", alpha=.12, zorder=1)
        a.axvline(i, color="#7b2cbf", ls="--", lw=1.2, zorder=5)
    colors = _draw_candles(ax, df); x = np.arange(len(df))
    for n in (5, 10, 20, 30, 60, 120, 250):
        ax.plot(x, df[f"ma{n}"], label=f"MA{n}", color=MA_COLORS[n], lw=1.2 if n<120 else 1.7)
    vol.bar(x, df.vol/10000, width=.68, color=colors, alpha=.85)
    y = float(row.signal_close) * marker_scale
    ax.scatter(i, y, color="#7b2cbf", marker="^", s=110, zorder=9)
    ax.annotate(f"观察日 {day:%m-%d}\n15分钟观察时刻 {signal:%H:%M}", (i,y),
                xytext=(-95, 35), textcoords="offset points", fontsize=10, color="#7b2cbf",
                arrowprops={"arrowstyle":"->","color":"#7b2cbf"},
                bbox={"facecolor":"white","edgecolor":"none","alpha":.9})
    if bool(row.buyable_proxy) and pd.notna(row.entry_time):
        entry = pd.Timestamp(row.entry_time)
        ei = np.flatnonzero(df.date.eq(entry.normalize()).to_numpy())
        if len(ei):
            ey = float(row.entry_open) * marker_scale
            ax.scatter(int(ei[0]), ey, marker="*", s=155, color="#d98200", edgecolor="black", zorder=10)
    ax.set_title(f"{row['name']} {row.ts_code}  日K线 | MA按交易日计算\n"
                 f"对应15分钟观察日 {day:%Y-%m-%d} · 前一年背景；实际显示至{df.date.iloc[-1]:%Y-%m-%d}", fontsize=14)
    ax.set_ylabel("前复权价格（元）");vol.set_ylabel("成交量（万手）")
    ax.legend(loc="upper left", ncol=7, fontsize=9)
    ax.set_xlim(-1,len(df)); _apply_date_ticks(vol,df.date)
    vol.set_xlabel("观察日完整日K与之后的灰底区域仅供事后对照，不能当作盘中已知信息")
    fig.text(.07,.025,"日线只用于展示大级别背景；15分钟粘合度没有替换成日线粘合度。量柱使用不复权实际成交量。",fontsize=10,color="#334155")
    fig.subplots_adjust(left=.07,right=.985,top=.88,bottom=.13)
    fig.savefig(path,dpi=150,facecolor="white");plt.close(fig)
    return {"first":str(df.date.iloc[0]),"last":str(df.date.iloc[-1]),"bars":len(df),
            "observation_day_in_posthoc_region":True,"image":path.name,"image_sha256":digest(path),
            "daily_input_sha256":digest(data_path),"volume_unit":"Tushare lots, displayed in 10000 lots"}


def render_set(cases: pd.DataFrame, raw: pd.DataFrame, m: dict, rules: Rules,
               out: Path, title: str) -> None:
    images=out/"charts"; data=out/"daily_inputs"
    images.mkdir(parents=True,exist_ok=True);data.mkdir(exist_ok=True)
    receipts=[]; cards=[];_configure_chinese_font()
    for number,(_,row) in enumerate(cases.iterrows(),1):
        stock=raw[raw.ts_code.eq(row.ts_code)].sort_values("datetime")
        f=features(stock,m["calendar"],rules)
        stamp=pd.Timestamp(row.signal_time).strftime("%Y%m%d_%H%M")
        stem=f"{number:02d}_{row.ts_code}_{stamp}"
        minute=minute_chart(f,row,images/f"{stem}_15分钟.jpg",m["last"])
        daily=daily_chart(row,images/f"{stem}_日线.jpg",m["last"],data)
        if row.case_kind=="frozen_signal":
            prefix=features(stock[stock.datetime.le(pd.Timestamp(row.signal_time))],m["calendar"],rules)
            ps=find_signals(prefix,row.ts_code,row['name'],rules,m["first"],m["last"])
            assert ps.signal_time.eq(row.signal_time).any()
        receipts.append({"number":number,"ts_code":row.ts_code,"name":row['name'],
                         "signal_time":row.signal_time,"case_kind":row.case_kind,"minute":minute,"daily":daily})
        caption=f"{number:02d}. {row['name']} {row.ts_code} · {row.signal_time}"
        cards.append(f'<section id="case{number}"><h2>{html.escape(caption)}</h2>'
                     f'<p>粘合 {html.escape(coil_description(row))} · 测量 {row.coil_time}</p>'
                     f'<h3>15分钟</h3><a target="_blank" href="charts/{minute["image"]}"><img loading="lazy" src="charts/{minute["image"]}"></a>'
                     f'<h3>日线</h3><a target="_blank" href="charts/{daily["image"]}"><img loading="lazy" src="charts/{daily["image"]}"></a></section>')
        print(f'[{number}/{len(cases)}] {row["name"]} 15分钟+日线',flush=True)
    nav=" ".join(f'<a href="#case{n}">{n:02d}</a>' for n in range(1,len(cases)+1))
    page='<!doctype html><meta charset="utf-8"><style>body{font-family:system-ui;background:#f1f5f9;margin:25px;color:#1e293b}section{background:white;padding:18px;margin:22px 0;border-radius:12px}img{width:100%}nav{position:sticky;top:0;z-index:5;background:white;padding:12px}nav a{margin-right:15px}</style>'
    page+=f'<h1>{html.escape(title)}</h1><p>每例两张独立图片，均含七条均线及真实成交量。灰底为观察之后的事后行情；日线从观察日整根开始变灰。全部显示至9月15日以内。</p><nav>{nav}</nav>'+"\n".join(cards)
    (out/"review.html").write_text(page)
    cases.to_csv(out/"case_index.csv",index=False,encoding="utf-8-sig")
    (out/"ACCEPTANCE.json").write_text(json.dumps({"cases":len(cases),"images":2*len(cases),"receipts":receipts},ensure_ascii=False,indent=2))
    with zipfile.ZipFile(out/"15分钟与日线图册.zip","w",zipfile.ZIP_DEFLATED) as z:
        for item in receipts:
            for kind in ("minute","daily"):
                path=images/item[kind]["image"];z.write(path,"charts/"+path.name)
        for name in ("review.html","case_index.csv"):
            z.write(out/name,name)
    build_overviews(out)


def build_overviews(out: Path) -> None:
    """One sheet: each stock occupies a tile, daily above its 15m chart."""
    acceptance=json.loads((out/"ACCEPTANCE.json").read_text())
    items=acceptance["receipts"]; count=len(items)
    columns=5 if count>=15 else 4; rows=(count+columns-1)//columns
    tile_w,tile_h,gap,header=1400,1680,20,140
    width=columns*tile_w+(columns+1)*gap
    height=header+rows*(tile_h+gap)+gap
    font_path=font_manager.findfont("Arial Unicode MS")
    big=ImageFont.truetype(font_path,48)
    label_font=ImageFont.truetype(font_path,34)
    sub=ImageFont.truetype(font_path,27)
    sheet=Image.new("RGB",(width,height),"#e9eef4")
    pen=ImageDraw.Draw(sheet)
    pen.text((gap,16),f"{count}例同图对照 | 每格同一股票：上方日线，下方15分钟",font=big,fill="#172b4d")
    pen.text((gap,86),"原随机名单保持不变；每图均有七条均线及实际成交量，紫线为观察日/时点；灰底为事后行情。",font=sub,fill="#334155")
    sources=[]
    for i,item in enumerate(items):
        x=gap+(i%columns)*(tile_w+gap); y=header+(i//columns)*(tile_h+gap)
        pen.rounded_rectangle((x,y,x+tile_w,y+tile_h),radius=9,fill="white")
        pen.text((x+12,y+8),f"{i+1:02d}  {item['name']}  {item['ts_code']}  {item['signal_time'][5:16]}",font=label_font,fill="#172b4d")
        source={"number":i+1,"ts_code":item["ts_code"],"signal_time":item["signal_time"],"daily_above_minute":True}
        for kind,label,offset in (("daily","日线",86),("minute","15分钟",896)):
            path=out/"charts"/item[kind]["image"]
            assert digest(path)==item[kind]["image_sha256"],f"Stale source image: {path}"
            pen.text((x+12,y+offset-32),label,font=sub,fill="#2457a7")
            with Image.open(path) as original:
                im=original.copy();im.thumbnail((tile_w-16,774),Image.Resampling.LANCZOS)
            sheet.paste(im,(x+(tile_w-im.width)//2,y+offset))
            source[kind]={"source_sha256":item[kind]["image_sha256"],"tile_offset_y":offset}
        sources.append(source)
    filename=f"{count}例_日线上_15分钟下_总览.jpg"
    sheet.save(out/filename,quality=95,subsampling=0)
    receipt={"image":filename,"size":[width,height],"cases":count,"panels":2*count,
             "columns":columns,"rows":rows,"daily_above_minute":True,
             "sha256":digest(out/filename),"source_order":sources}
    (out/"OVERVIEW_ACCEPTANCE.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    page='<!doctype html><meta charset="utf-8"><title>日线与15分钟总览</title><style>body{font:18px system-ui;margin:20px;background:#eef2f6}img{width:100%}a{color:#2457a7}</style>'
    page+=f'<h1>原随机{count}例：日线与15分钟放在同一张图</h1><p>同一股票同一格，上面日线，下面15分钟。点击图片打开高清原图。</p>'
    page+=f'<a href="{filename}" target="_blank"><img src="{filename}"></a>'
    page+='<p><a href="review.html">逐例查看日线与15分钟原图</a></p>'
    (out/"overview.html").write_text(page)
    print(f"Built one {count}-case daily-above-15m overview: {out/filename}",flush=True)


def build(replay: Path, case_mode: str) -> None:
    m=json.loads((replay/"FROZEN_MANIFEST.json").read_text())
    assert digest(replay/"signals_before_outcomes.csv")==m["signal_file_sha256"]
    rules=load_rules(replay/m.get("rules_file","rules_1.5.json"))
    random_dir=replay/"random_20_seed20260930"
    selected=pd.read_csv(random_dir/"随机20次信号.csv") if case_mode in ("both","random") else pd.DataFrame()
    codes=set(TARGET_FIRST_BOARDS) if case_mode in ("both","targets") else set()
    if len(selected): codes.update(selected.ts_code)
    raw=pd.read_parquet([str(x) for x in sorted((replay/"inputs").glob("bars_*.parquet"))],filters=[("ts_code","in",sorted(codes))])
    raw["datetime"]=pd.to_datetime(raw.datetime)
    if case_mode in ("both","targets"):
        events=pd.read_parquet(replay/"inputs/theme_events.parquet")
        cases,summary,two=target_cases(raw,m,rules,events)
        out=replay/"target_7_paired_views";out.mkdir(exist_ok=True)
        summary.to_csv(out/"目标7票_粘合度汇总.csv",index=False,encoding="utf-8-sig")
        two.to_csv(out/"目标7票_起涨前两根逐项.csv",index=False,encoding="utf-8-sig")
        render_set(cases,raw,m,rules,out,"7只目标票：15分钟粘合结构＋日线背景")
        report=["# 7只目标票的15分钟粘合测量","",
                "粘合度=100×(所选价格/均线最大值÷最小值−1)。数值越小越紧。", "",
                "MA30组=close+MA5/10/20/30；MA60组=close+MA5/10/20/60。另列全部短线与长期均线的宽度，避免只报最漂亮的一组。", "",
                "观察参照：8月24日至9月初这轮首板日内最大实体阳线，之前1–2根中原两组的较紧时点；新赛股份一字板按首根观察。该参照借助已知首板结果，是事后模板测量，不是隔离未来的入场信号，不改变原410次回放。", "",
                "| 股票 | 起涨参照 | 粘合测量 | 较紧分支 | 粘合% | 价格+MA30组% | 价格+MA60组% | 价格+MA5至120% |", "|---|---|---|---|---:|---:|---:|---:|"]
        for _,r in summary.iterrows():
            report.append(f"| {r['股票名称']} {r['股票代码']} | {r['起涨参照时刻']} | {r['粘合测量时刻']} | {r['较紧分支']} | {r['选定粘合度%']:.3f} | {r['价格+MA30组%']:.3f} | {r['价格+MA60组%']:.3f} | {r['价格+MA5至120%']:.3f} |")
        report += ["", "7只较紧分支均在1.5%以内，但不能推出所有均线都在1.5%内，也不能从已知赢家倒推出该阈值有预测优势。", "",
                   "对照原回放：桂林旅游8/31 14:45为1.393%，瑞尔特9/1 13:30为0.716%，闽东电力9/2 11:30为0.861%；这些与本表测量的是不同起涨参照时刻，不能混用。", "",
                   "每例两张独立JPG，一张15分钟、一张日线。15分钟默认前30日/后7日；日线前一年/后一月，但两者都以原研究9月15日截止。观察之后变灰；日线观察日整根未知，整日及以后变灰。", "",
                   "万向德农、新赛股份、龙版传媒的本轮首板起涨参照在8月，不能把它们的8月机会当作9月扫描漏掉的信号。其他工程前置条件是否符合原规则仍须分别检查。"]
        (out/"report.md").write_text("\n".join(report))
        print(summary[["股票名称","粘合测量时刻","较紧分支","选定粘合度%"]].to_string(index=False))
    if len(selected):
        sampled=json.loads((random_dir/"SAMPLING_MANIFEST.json").read_text())
        actual=selected[["ts_code","signal_time"]].to_dict(orient="records")
        assert actual==sampled["selected"],"Do not resample the existing twenty"
        selected["case_kind"]="frozen_signal"
        render_set(selected,raw,m,rules,random_dir/"paired_views","原随机20例：15分钟＋日线，一例两张")
    assert digest(replay/"signals_before_outcomes.csv")==m["signal_file_sha256"]


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir",type=Path,default=ROOT/"outputs/fifteen_min_ma_coil_20260901_20260915_v2")
    parser.add_argument("--case-mode",choices=("both","targets","random"),default="both")
    parser.add_argument("--overviews-only",action="store_true",help="Assemble existing verified chart pairs; do not redraw or resample")
    args=parser.parse_args()
    if args.overviews_only:
        if args.case_mode in ("both","targets"):build_overviews(args.replay_dir/"target_7_paired_views")
        if args.case_mode in ("both","random"):build_overviews(args.replay_dir/"random_20_seed20260930/paired_views")
    else:build(args.replay_dir,args.case_mode)
