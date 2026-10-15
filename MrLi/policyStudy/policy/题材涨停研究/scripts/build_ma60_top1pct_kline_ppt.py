"""Build a themed PowerPoint atlas for 2026 MA60 Top-1% stocks."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


DAILY_QFQ = False

ALLOWED_BROAD_CATEGORIES = {
    "半导体/芯片",
    "AI算力/数据中心",
    "PCB/通信硬件",
    "机器人/高端装备",
    "医药医疗",
    "新能源/电力储能",
    "军工/航空航天",
    "有色金属/稀土/资源",
    "化工/新材料",
    "汽车/智能驾驶",
    "消费",
    "地产/基建",
    "金融",
    "其他科技电子",
    "并购重组/ST/事件驱动",
}


MANUAL_CLASSIFICATIONS: dict[str, dict] = {
    "603261.SH": {"primary_theme": "军工", "broad_category": "军工/航空航天", "secondary_themes": ["大飞机", "无人机", "ST摘帽"]},
    "300604.SZ": {"primary_theme": "半导体测试设备", "broad_category": "半导体/芯片", "secondary_themes": ["先进封装", "存储"]},
    "300209.SZ": {"primary_theme": "算力租赁", "broad_category": "AI算力/数据中心", "secondary_themes": ["算力", "词元概念"]},
    "688200.SH": {"primary_theme": "半导体测试设备", "broad_category": "半导体/芯片", "secondary_themes": ["先进封装"]},
    "301526.SZ": {"primary_theme": "电子布/玻纤", "broad_category": "PCB/通信硬件", "secondary_themes": ["AI硬件", "通信", "PEEK轻量化材料"]},
    "603186.SH": {"primary_theme": "覆铜板", "broad_category": "PCB/通信硬件", "secondary_themes": ["IC载板", "半导体材料", "5G-A"]},
    "002821.SZ": {"primary_theme": "CRO/CDMO", "broad_category": "医药医疗", "secondary_themes": ["创新药", "减肥药", "合成生物"]},
    "000608.SZ": {"primary_theme": "房地产/地产链", "broad_category": "地产/基建", "secondary_themes": ["并购重组预期概念", "ST板块", "人工智能"]},
    "688409.SH": {"primary_theme": "半导体设备精密零部件", "broad_category": "半导体/芯片", "secondary_themes": ["光刻机", "芯片"]},
    "688630.SH": {"primary_theme": "PCB设备", "broad_category": "PCB/通信硬件", "secondary_themes": ["光刻机概念", "半导体设备", "通信"]},
    "301273.SZ": {"primary_theme": "建筑节能", "broad_category": "地产/基建", "secondary_themes": ["基础建设", "稀土永磁"]},
    "301377.SZ": {"primary_theme": "PCB设备/刀具", "broad_category": "PCB/通信硬件", "secondary_themes": ["印制电路板", "AI硬件"]},
    "600367.SH": {"primary_theme": "金属锰", "broad_category": "有色金属/稀土/资源", "secondary_themes": ["小金属", "锂电池材料", "玻璃基板"]},
    "003004.SZ": {"primary_theme": "安防", "broad_category": "其他科技电子", "secondary_themes": ["数字经济", "物理AI", "ST板块"]},
    "688519.SH": {"primary_theme": "覆铜板", "broad_category": "PCB/通信硬件", "secondary_themes": ["涨价概念"]},
    "000636.SZ": {"primary_theme": "MLCC/电阻电容", "broad_category": "其他科技电子", "secondary_themes": ["一体化电感", "AI硬件"]},
    "300666.SZ": {"primary_theme": "半导体靶材", "broad_category": "半导体/芯片", "secondary_themes": ["半导体材料", "长江存储概念"]},
    "301200.SZ": {"primary_theme": "PCB设备", "broad_category": "PCB/通信硬件", "secondary_themes": ["玻璃基板", "AI硬件"]},
    "688300.SH": {"primary_theme": "先进封装材料", "broad_category": "半导体/芯片", "secondary_themes": ["HBM存储", "半导体材料", "覆铜板"]},
    "688141.SH": {"primary_theme": "模拟芯片", "broad_category": "半导体/芯片", "secondary_themes": ["汽车芯片", "AI硬件"]},
    "002832.SZ": {"primary_theme": "高端运动服饰", "broad_category": "消费", "secondary_themes": ["体育产业", "复苏消费概念", "一季报增长"]},
    "300835.SZ": {"primary_theme": "稀土永磁", "broad_category": "有色金属/稀土/资源", "secondary_themes": ["一体化电感", "元器件"]},
    "603115.SH": {"primary_theme": "服务器电容器链", "broad_category": "AI算力/数据中心", "secondary_themes": ["电阻电容", "算力", "元器件"]},
    "688432.SH": {"primary_theme": "半导体硅片", "broad_category": "半导体/芯片", "secondary_themes": ["存储", "国产芯片概念", "硅"]},
    "603938.SH": {"primary_theme": "电子气体", "broad_category": "半导体/芯片", "secondary_themes": ["光刻机概念", "光纤", "有机硅"]},
    "600350.SH": {"primary_theme": "公路运营", "broad_category": "地产/基建", "secondary_themes": ["高股息", "股权转让"]},
    "688766.SH": {"primary_theme": "存储芯片", "broad_category": "半导体/芯片", "secondary_themes": ["半导体存储器", "涨价概念"]},
    "300179.SZ": {"primary_theme": "金刚石/超硬材料", "broad_category": "化工/新材料", "secondary_themes": ["深地探索概念", "出口管制概念"]},
    "002636.SZ": {"primary_theme": "覆铜板", "broad_category": "PCB/通信硬件", "secondary_themes": ["电子布", "AI硬件", "通信"]},
    "605376.SH": {"primary_theme": "MLCC镍粉", "broad_category": "其他科技电子", "secondary_themes": ["有色金属", "光伏新技术", "AI硬件"]},
    "688328.SH": {"primary_theme": "面板/显示设备", "broad_category": "其他科技电子", "secondary_themes": ["半导体设备", "存储", "AI眼镜概念"]},
    "300054.SZ": {"primary_theme": "半导体材料", "broad_category": "半导体/芯片", "secondary_themes": ["先进封装", "HBM存储", "国产芯片概念"]},
    "002281.SZ": {"primary_theme": "光模块/光芯片", "broad_category": "PCB/通信硬件", "secondary_themes": ["硅光技术概念", "高速连接", "AI硬件"]},
    "688498.SH": {"primary_theme": "光芯片", "broad_category": "PCB/通信硬件", "secondary_themes": ["光通信设备", "硅光技术概念", "激光雷达"]},
    "300903.SZ": {"primary_theme": "PCB/印制电路板", "broad_category": "PCB/通信硬件", "secondary_themes": ["端侧AI", "AI硬件"]},
    "603061.SH": {"primary_theme": "半导体测试设备", "broad_category": "半导体/芯片", "secondary_themes": ["芯片"]},
    "688625.SH": {"primary_theme": "覆铜板", "broad_category": "PCB/通信硬件", "secondary_themes": ["通信"]},
    "603256.SH": {"primary_theme": "电子布/玻纤", "broad_category": "PCB/通信硬件", "secondary_themes": ["AI硬件", "通信"]},
    "688702.SH": {"primary_theme": "以太网交换芯片", "broad_category": "半导体/芯片", "secondary_themes": ["算力", "大基金持股"]},
    "688545.SH": {"primary_theme": "湿电子化学品", "broad_category": "半导体/芯片", "secondary_themes": ["光刻胶", "长鑫存储概念", "半导体材料"]},
    "301115.SZ": {"primary_theme": "机器人概念", "broad_category": "机器人/高端装备", "secondary_themes": ["地产链", "房屋养老金概念"]},
    "600176.SH": {"primary_theme": "电子布/玻纤", "broad_category": "PCB/通信硬件", "secondary_themes": ["AI硬件", "风电", "PEEK轻量化材料"]},
    "603629.SH": {"primary_theme": "算力租赁", "broad_category": "AI算力/数据中心", "secondary_themes": ["东数西算", "腾讯AI概念", "英伟达概念"]},
    "688090.SH": {"primary_theme": "工业机器人", "broad_category": "机器人/高端装备", "secondary_themes": ["芯片"]},
    "688629.SH": {"primary_theme": "高速铜缆互联", "broad_category": "PCB/通信硬件", "secondary_themes": ["商业航天", "华为昇腾", "国企人工智能"]},
    "603268.SH": {"primary_theme": "造船", "broad_category": "机器人/高端装备", "secondary_themes": ["并购重组", "ST板块", "锂电池"]},
    "688257.SH": {"primary_theme": "机械基件（行业兜底）", "broad_category": "机器人/高端装备", "secondary_themes": []},
    "603303.SH": {"primary_theme": "LED照明", "broad_category": "其他科技电子", "secondary_themes": ["低空经济", "智慧城市", "股权转让"]},
    "603045.SH": {"primary_theme": "数据中心/算力", "broad_category": "AI算力/数据中心", "secondary_themes": ["智能电网", "电气设备", "白银"]},
    "002859.SZ": {"primary_theme": "MLCC载带/离型膜", "broad_category": "其他科技电子", "secondary_themes": ["半导体材料", "复合集流体", "并购重组"]},
    "300398.SZ": {"primary_theme": "半导体材料", "broad_category": "半导体/芯片", "secondary_themes": ["先进封装", "HBM存储", "国产芯片概念"]},
    "603211.SH": {"primary_theme": "机器人概念", "broad_category": "机器人/高端装备", "secondary_themes": ["人形机器人", "宇树机器人", "商业航天"]},
    "002937.SZ": {"primary_theme": "液冷", "broad_category": "AI算力/数据中心", "secondary_themes": ["PEEK", "算力"]},
    "688479.SH": {"primary_theme": "AI智能体", "broad_category": "其他科技电子", "secondary_themes": ["国产软件", "人工智能"]},
    "000593.SZ": {"primary_theme": "天然气", "broad_category": "有色金属/稀土/资源", "secondary_themes": ["实控人变更", "举牌", "并购重组"]},
    "002491.SZ": {"primary_theme": "光纤/光通信", "broad_category": "PCB/通信硬件", "secondary_themes": ["通信", "AI硬件"]},
    "001389.SZ": {"primary_theme": "PCB/印制电路板", "broad_category": "PCB/通信硬件", "secondary_themes": ["CPU", "AI硬件"]},
    "301588.SZ": {"primary_theme": "地产链", "broad_category": "地产/基建", "secondary_themes": ["装修家具", "商业航天"]},
}


CATEGORY_COLORS = {
    "半导体/芯片": "#5B6CFF",
    "PCB/通信硬件": "#00A6A6",
    "AI算力/数据中心": "#8B5CF6",
    "其他科技电子": "#D97706",
    "机器人/高端装备": "#2563EB",
    "有色金属/稀土/资源": "#B7791F",
    "地产/基建": "#64748B",
    "化工/新材料": "#0D9488",
    "消费": "#DB2777",
    "医药医疗": "#E11D48",
    "军工/航空航天": "#334155",
    "新能源/电力储能": "#16A34A",
    "汽车/智能驾驶": "#EA580C",
    "金融": "#0369A1",
    "并购重组/ST/事件驱动": "#7C3AED",
}


CATEGORY_ORDER = [
    "半导体/芯片",
    "PCB/通信硬件",
    "AI算力/数据中心",
    "其他科技电子",
    "机器人/高端装备",
    "有色金属/稀土/资源",
    "地产/基建",
    "化工/新材料",
    "消费",
    "医药医疗",
    "军工/航空航天",
    "新能源/电力储能",
    "汽车/智能驾驶",
    "金融",
    "并购重组/ST/事件驱动",
]


def materialize_classifications(
    evidence: Sequence[dict],
    manual_mapping: dict[str, dict],
) -> list[dict]:
    """Combine analyst mapping with verbatim local KPL evidence labels."""
    evidence_codes = {str(item["ts_code"]) for item in evidence}
    mapping_codes = set(manual_mapping)
    if evidence_codes != mapping_codes:
        raise ValueError(
            "manual classification coverage mismatch: "
            f"missing={sorted(evidence_codes - mapping_codes)}, "
            f"extra={sorted(mapping_codes - evidence_codes)}"
        )
    results: list[dict] = []
    for item in sorted(evidence, key=lambda row: int(row["rank"])):
        ts_code = str(item["ts_code"])
        mapping = manual_mapping[ts_code]
        labels: list[str] = []
        for source_name in ("kpl_list_themes", "concepts", "limit_performance_themes"):
            for record in item.get(source_name, []):
                label = str(record.get("theme", "")).strip()
                if label and label not in labels:
                    labels.append(label)
        kpl = item.get("kpl_list_themes", [])
        concepts = item.get("concepts", [])
        strong_kpl = any(int(record.get("count", 0)) >= 2 for record in kpl)
        strong_concept = any(int(record.get("observed_days", 0)) >= 60 for record in concepts)
        any_evidence = bool(labels)
        confidence = "high" if strong_kpl or strong_concept else ("medium" if any_evidence else "low")
        kpl_text = "、".join(
            f"{record['theme']}×{int(record.get('count', 0))}" for record in kpl[:4]
        ) or "无"
        concept_text = "、".join(
            f"{record['theme']}（{int(record.get('observed_days', 0))}日）"
            for record in concepts[:4]
        ) or "无"
        results.append(
            {
                "ts_code": ts_code,
                "primary_theme": mapping["primary_theme"],
                "broad_category": mapping["broad_category"],
                "secondary_themes": list(mapping.get("secondary_themes", [])),
                "evidence_labels": labels[:8],
                "confidence": confidence,
                "evidence_note": (
                    f"KPL：{kpl_text}；概念：{concept_text}；"
                    f"行业：{item.get('industry') or '未标注'}"
                ),
            }
        )
    return results


def add_moving_averages(
    daily: pd.DataFrame,
    periods: Iterable[int] = (20, 30, 60, 120),
) -> pd.DataFrame:
    """Sort daily bars by stock/date and add simple moving averages."""
    required = {"ts_code", "trade_date", "close", "vol"}
    missing = required.difference(daily.columns)
    if missing:
        raise ValueError(f"missing required daily columns: {sorted(missing)}")
    out = daily.copy()
    out["trade_date"] = out["trade_date"].astype(str)
    out = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    for period in periods:
        out[f"ma{period}"] = out.groupby("ts_code", sort=False)["close"].transform(
            lambda values: values.rolling(period, min_periods=period).mean()
        )
    return out


def display_start_for_episode(start_date: str) -> pd.Timestamp:
    """Show 90 calendar days before long episodes, with at least Oct-2025 context."""
    episode_context = pd.Timestamp(str(start_date)) - timedelta(days=90)
    minimum_context = pd.Timestamp("2025-10-01")
    return min(episode_context, minimum_context)


def render_kline_chart(
    daily: pd.DataFrame,
    output: Path,
    *,
    stock_name: str,
    ts_code: str,
    episode_start: str,
    episode_end: str,
    display_start: pd.Timestamp,
    display_end: pd.Timestamp,
) -> dict[str, object]:
    """Render front-adjusted daily candles, MA20/30/60/120 and actual volume."""
    required = {
        "trade_date", "open", "high", "low", "close", "vol",
        "ma20", "ma30", "ma60", "ma120",
    }
    if missing := required.difference(daily.columns):
        raise ValueError(f"chart data missing columns: {sorted(missing)}")
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d")
    frame = frame.loc[frame["date"].between(display_start, display_end)].copy()
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if frame.empty:
        raise ValueError(f"no chart bars for {ts_code}")
    if frame[["open", "high", "low", "close", "vol"]].isna().any().any():
        raise ValueError(f"missing OHLCV values for {ts_code}")

    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC", "PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"
    ]
    plt.rcParams["axes.unicode_minus"] = False
    figure, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(14.2, 7.2),
        sharex=True,
        gridspec_kw={"height_ratios": [4.25, 1.15], "hspace": 0.03},
        facecolor="white",
    )
    for axis in (price_ax, volume_ax):
        axis.set_facecolor("#FBFCFE")
        axis.grid(True, color="#CBD5E1", alpha=0.38, linewidth=0.65)
        axis.spines[["top", "right"]].set_visible(False)
        axis.tick_params(labelsize=9, colors="#334155")

    x = np.arange(len(frame))
    up = frame["close"].gt(frame["open"])
    down = frame["close"].lt(frame["open"])
    colors = np.where(up, "#D9363E", np.where(down, "#159D72", "#64748B"))
    price_ax.vlines(x, frame["low"], frame["high"], color=colors, linewidth=0.75, zorder=2)
    body_bottom = np.minimum(frame["open"], frame["close"])
    body_height = np.abs(frame["close"] - frame["open"])
    min_body = max(float(frame["close"].median()) * 0.0008, 1e-6)
    for idx, (bottom, height, color) in enumerate(zip(body_bottom, body_height, colors)):
        price_ax.add_patch(
            Rectangle(
                (idx - 0.31, float(bottom)),
                0.62,
                max(float(height), min_body),
                facecolor=color,
                edgecolor=color,
                linewidth=0.5,
                zorder=3,
            )
        )

    ma_colors = {20: "#F59E0B", 30: "#8B5CF6", 60: "#2563EB", 120: "#111827"}
    for period, color in ma_colors.items():
        price_ax.plot(
            x,
            frame[f"ma{period}"],
            label=f"MA{period}",
            color=color,
            linewidth=1.45 if period == 60 else 1.05,
            alpha=0.95,
            zorder=4,
        )
    volume_ax.bar(x, frame["vol"] / 10000.0, width=0.68, color=colors, alpha=0.82)

    start = pd.Timestamp(str(episode_start))
    end = pd.Timestamp(str(episode_end))
    mask = frame["date"].between(start, end)
    if mask.any():
        indexes = np.flatnonzero(mask.to_numpy())
        left, right = int(indexes[0]) - 0.5, int(indexes[-1]) + 0.5
        for axis in (price_ax, volume_ax):
            axis.axvspan(left, right, color="#DBEAFE", alpha=0.32, zorder=0)
            axis.axvline(left + 0.5, color="#2563EB", linestyle="--", linewidth=0.9, alpha=0.75)
            axis.axvline(right - 0.5, color="#2563EB", linestyle="--", linewidth=0.9, alpha=0.75)

    price_ax.set_ylabel("未复权价格（元）", fontsize=10, color="#334155")
    volume_ax.set_ylabel("成交量（万手）", fontsize=10, color="#334155")
    price_ax.legend(
        loc="upper left", ncol=4, fontsize=9, frameon=True,
        facecolor="white", edgecolor="#E2E8F0", framealpha=0.92,
    )
    price_ax.set_title(
        f"{stock_name}  {ts_code}｜日K + 成交量",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color="#0F172A",
        pad=10,
    )
    tick_count = min(9, len(frame))
    ticks = np.unique(np.linspace(0, len(frame) - 1, tick_count, dtype=int))
    volume_ax.set_xticks(ticks)
    volume_ax.set_xticklabels(
        [frame.iloc[index]["date"].strftime("%Y-%m") for index in ticks],
        rotation=0,
        fontsize=9,
    )
    price_ax.set_xlim(-1, len(frame))
    figure.text(
        0.995,
        0.012,
        "红涨绿跌｜蓝色区间为入选的MA60连续上升段｜价格与成交量均未复权",
        ha="right",
        va="bottom",
        fontsize=8.5,
        color="#64748B",
    )
    figure.subplots_adjust(left=0.065, right=0.985, top=0.91, bottom=0.105)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(figure)
    return {
        "ts_code": ts_code,
        "image": output.name,
        "bars": int(len(frame)),
        "first_date": frame.iloc[0]["date"].strftime("%Y%m%d"),
        "last_date": frame.iloc[-1]["date"].strftime("%Y%m%d"),
    }


def validate_classifications(
    top_stocks: pd.DataFrame,
    classifications: Sequence[dict],
) -> pd.DataFrame:
    """Validate one classification per selected stock and return rank order."""
    required_top = {"rank", "ts_code"}
    if missing := required_top.difference(top_stocks.columns):
        raise ValueError(f"top stock data missing columns: {sorted(missing)}")
    frame = pd.DataFrame(classifications)
    required_classification = {
        "ts_code", "primary_theme", "broad_category", "secondary_themes"
    }
    if missing := required_classification.difference(frame.columns):
        raise ValueError(f"classification missing columns: {sorted(missing)}")
    if frame["ts_code"].duplicated().any():
        raise ValueError("classification coverage contains duplicate ts_code")
    expected = set(top_stocks["ts_code"].astype(str))
    actual = set(frame["ts_code"].astype(str))
    if expected != actual:
        raise ValueError(
            "classification coverage mismatch: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    invalid = sorted(set(frame["broad_category"]) - ALLOWED_BROAD_CATEGORIES)
    if invalid:
        raise ValueError(f"invalid broad categories: {invalid}")
    return top_stocks.merge(frame, on="ts_code", how="left", validate="one_to_one").sort_values("rank")


def _rgb(hex_color: str) -> RGBColor:
    value = hex_color.lstrip("#")
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _set_slide_background(slide, color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(color)


def _add_text(
    slide,
    text: str,
    left: float,
    top: float,
    width: float,
    height: float,
    *,
    size: float = 18,
    color: str = "#0F172A",
    bold: bool = False,
    align=PP_ALIGN.LEFT,
    valign=MSO_ANCHOR.TOP,
    font: str = "Noto Sans CJK SC",
    margin: float = 0.03,
):
    shape = slide.shapes.add_textbox(
        Inches(left), Inches(top), Inches(width), Inches(height)
    )
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Inches(margin)
    frame.margin_right = Inches(margin)
    frame.margin_top = Inches(margin)
    frame.margin_bottom = Inches(margin)
    frame.vertical_anchor = valign
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    paragraph.space_after = Pt(0)
    run = paragraph.add_run()
    run.text = str(text)
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = _rgb(color)
    return shape


def _add_rect(
    slide,
    left: float,
    top: float,
    width: float,
    height: float,
    *,
    fill: str,
    radius: bool = False,
    line: str | None = None,
):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(
        kind, Inches(left), Inches(top), Inches(width), Inches(height)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(fill)
    if line:
        shape.line.color.rgb = _rgb(line)
        shape.line.width = Pt(0.75)
    else:
        shape.line.fill.background()
    return shape


def _add_pill(slide, text: str, left: float, top: float, width: float, color: str) -> None:
    _add_rect(slide, left, top, width, 0.34, fill=color, radius=True)
    _add_text(
        slide, text, left + 0.04, top + 0.01, width - 0.08, 0.28,
        size=10.5, color="#FFFFFF", bold=True, align=PP_ALIGN.CENTER,
        valign=MSO_ANCHOR.MIDDLE, margin=0,
    )


def _add_slide_number(slide, number: int, *, dark: bool = False) -> None:
    color = "#94A3B8" if dark else "#64748B"
    _add_text(
        slide,
        f"MA60 TOP 1% · 2026 YTD                                      {number:02d}",
        0.38,
        7.16,
        12.55,
        0.22,
        size=8,
        color=color,
        align=PP_ALIGN.RIGHT,
        margin=0,
    )


def _category_sequence(stocks: pd.DataFrame) -> list[str]:
    present = set(stocks["broad_category"])
    ordered = [category for category in CATEGORY_ORDER if category in present]
    return ordered + sorted(present - set(ordered))


def build_presentation(
    stocks: pd.DataFrame,
    chart_paths: dict[str, Path],
    output: Path,
    *,
    data_end: str,
    valid_universe: int,
    nominal_top_n: int,
    cutoff_days: int,
) -> dict[str, int]:
    """Create the full themed classification and one-chart-per-stock deck."""
    required = {
        "rank", "ts_code", "stock_name", "industry", "start_date", "end_date",
        "target_days", "length", "carried_in", "right_censored", "primary_theme",
        "broad_category", "secondary_themes", "evidence_labels", "confidence",
        "evidence_note",
    }
    if missing := required.difference(stocks.columns):
        raise ValueError(f"presentation data missing columns: {sorted(missing)}")
    missing_charts = sorted(set(stocks["ts_code"]) - set(chart_paths))
    if missing_charts:
        raise ValueError(f"missing chart images: {missing_charts}")
    for path in chart_paths.values():
        if not Path(path).exists():
            raise FileNotFoundError(path)

    ordered = stocks.sort_values("rank").reset_index(drop=True)
    categories = _category_sequence(ordered)
    category_counts = ordered["broad_category"].value_counts().to_dict()
    deck = Presentation()
    deck.slide_width = Inches(13.333)
    deck.slide_height = Inches(7.5)
    blank = deck.slide_layouts[6]

    # Cover
    slide = deck.slides.add_slide(blank)
    _set_slide_background(slide, "#0B1220")
    _add_rect(slide, 0, 0, 0.16, 7.5, fill="#5B6CFF")
    _add_text(slide, "MA60 上升持续性\nTOP 1% 股票图册", 0.72, 1.25, 8.9, 1.7,
              size=34, color="#F8FAFC", bold=True, valign=MSO_ANCHOR.MIDDLE)
    _add_text(slide, "58 只股票 · 题材归类 · 日K与成交量", 0.78, 3.16, 8.5, 0.48,
              size=19, color="#A5B4FC", bold=True)
    _add_text(slide, f"数据截至 {data_end[:4]}-{data_end[4:6]}-{data_end[6:]}  |  本地日线数据库  |  未复权价格",
              0.78, 3.78, 9.0, 0.38, size=12.5, color="#CBD5E1")
    _add_rect(slide, 9.86, 1.05, 2.55, 4.92, fill="#111C32", radius=True, line="#263653")
    _add_text(slide, "58", 10.15, 1.62, 1.95, 0.95, size=52, color="#FFFFFF", bold=True,
              align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
    _add_text(slide, "只入选股票", 10.15, 2.54, 1.95, 0.35, size=13, color="#94A3B8",
              align=PP_ALIGN.CENTER)
    _add_text(slide, str(cutoff_days), 10.15, 3.28, 1.95, 0.65, size=34, color="#5EEAD4", bold=True,
              align=PP_ALIGN.CENTER)
    _add_text(slide, "交易日入围线", 10.15, 3.92, 1.95, 0.35, size=13, color="#94A3B8",
              align=PP_ALIGN.CENTER)
    _add_text(slide, str(len(categories)), 10.15, 4.55, 1.95, 0.62, size=32, color="#FBBF24", bold=True,
              align=PP_ALIGN.CENTER)
    _add_text(slide, "个主归类", 10.15, 5.16, 1.95, 0.35, size=13, color="#94A3B8",
              align=PP_ALIGN.CENTER)
    _add_slide_number(slide, 1, dark=True)

    # Methodology
    slide = deck.slides.add_slide(blank)
    _set_slide_background(slide, "#F8FAFC")
    _add_text(slide, "筛选口径与阅读方式", 0.58, 0.42, 7.4, 0.58, size=25, bold=True)
    stats = [
        ("有效股票池", f"{valid_universe:,}", "#5B6CFF"),
        ("名义 Top 1%", str(nominal_top_n), "#8B5CF6"),
        ("并列后入选", str(len(ordered)), "#00A6A6"),
        ("第56名阈值", f"{cutoff_days} 天", "#D97706"),
    ]
    for index, (label, value, color) in enumerate(stats):
        left = 0.58 + index * 3.13
        _add_rect(slide, left, 1.27, 2.78, 1.15, fill="#FFFFFF", radius=True, line="#E2E8F0")
        _add_rect(slide, left, 1.27, 0.08, 1.15, fill=color)
        _add_text(slide, value, left + 0.24, 1.46, 2.25, 0.48, size=24, color=color, bold=True)
        _add_text(slide, label, left + 0.24, 1.96, 2.25, 0.28, size=10.5, color="#64748B")
    method_text = (
        "排名指标\n每只股票与2026目标窗口相交的 MA60 连续上升段中，取窗口内连续上升交易日数 target_days 的最大值。\n\n"
        "并列规则\n有效股票池 Top 1% 名义为56只；第56名为131天，阈值同分全部纳入，因此最终为58只。\n\n"
        "图表口径\n日K价格与成交量均采用未复权数据；叠加 MA20 / MA30 / MA60 / MA120。蓝色底纹是入选连续段。"
    )
    _add_rect(slide, 0.58, 2.78, 7.72, 3.67, fill="#FFFFFF", radius=True, line="#E2E8F0")
    _add_text(slide, method_text, 0.88, 3.03, 7.08, 3.15, size=14, color="#334155")
    caveat = (
        f"截止日\n{data_end[:4]}-{data_end[4:6]}-{data_end[6:]}\n\n"
        f"跨年延续\n{int(ordered['carried_in'].sum())} 只\n\n"
        f"截止日仍上升\n{int(ordered['right_censored'].sum())} 只\n\n"
        "注意\n这是 2026 YTD，非全年终值；仍在延续的段长度是当前下限。"
    )
    _add_rect(slide, 8.62, 2.78, 4.12, 3.67, fill="#0F172A", radius=True)
    _add_text(slide, caveat, 8.94, 3.04, 3.48, 3.12, size=14, color="#E2E8F0")
    _add_slide_number(slide, 2)

    # Category distribution
    slide = deck.slides.add_slide(blank)
    _set_slide_background(slide, "#F8FAFC")
    _add_text(slide, "题材结构总览", 0.58, 0.42, 7.2, 0.58, size=25, bold=True)
    _add_text(slide, "先按主归类分组；个股页保留具体主/次题材与原始证据标签。", 0.6, 0.99, 8.7, 0.34,
              size=11.5, color="#64748B")
    max_count = max(category_counts.values())
    for index, category in enumerate(categories):
        count = int(category_counts[category])
        top = 1.48 + index * 0.47
        color = CATEGORY_COLORS.get(category, "#64748B")
        _add_text(slide, category, 0.62, top, 2.35, 0.29, size=11.5, color="#334155", bold=True,
                  valign=MSO_ANCHOR.MIDDLE)
        _add_rect(slide, 3.03, top + 0.02, 7.65 * count / max_count, 0.27, fill=color, radius=True)
        _add_text(slide, str(count), 10.86, top - 0.01, 0.6, 0.32, size=12, color=color, bold=True,
                  align=PP_ALIGN.RIGHT)
    top_two = sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))[:2]
    top_share = sum(value for _, value in top_two) / len(ordered) * 100
    _add_rect(slide, 11.55, 1.48, 1.15, 4.95, fill="#FFFFFF", radius=True, line="#E2E8F0")
    _add_text(slide, f"{top_share:.0f}%", 11.62, 2.12, 1.0, 0.7, size=26, color="#5B6CFF", bold=True,
              align=PP_ALIGN.CENTER)
    _add_text(slide, "集中于\n前两大类", 11.66, 2.82, 0.92, 0.7, size=11, color="#64748B",
              align=PP_ALIGN.CENTER)
    _add_text(slide, f"{top_two[0][0]}\n+\n{top_two[1][0]}", 11.65, 3.75, 0.95, 1.25,
              size=9.5, color="#334155", bold=True, align=PP_ALIGN.CENTER,
              valign=MSO_ANCHOR.MIDDLE)
    _add_slide_number(slide, 3)

    # Full classification inventory, 24 stocks per slide.
    inventory_pages = math.ceil(len(ordered) / 24)
    for page in range(inventory_pages):
        slide = deck.slides.add_slide(blank)
        _set_slide_background(slide, "#F8FAFC")
        _add_text(slide, f"58只股票题材归类 · {page + 1}/{inventory_pages}", 0.58, 0.4, 8.0, 0.56,
                  size=24, bold=True)
        subset = ordered.iloc[page * 24:(page + 1) * 24]
        for position, (_, row) in enumerate(subset.iterrows()):
            column = position // 12
            row_index = position % 12
            left = 0.58 + column * 6.27
            top = 1.19 + row_index * 0.47
            color = CATEGORY_COLORS.get(row["broad_category"], "#64748B")
            _add_rect(slide, left, top + 0.04, 0.09, 0.33, fill=color, radius=True)
            _add_text(slide, f"{int(row['rank']):02d}", left + 0.15, top, 0.42, 0.4,
                      size=10.5, color="#64748B", bold=True, valign=MSO_ANCHOR.MIDDLE)
            _add_text(slide, f"{row['stock_name']}  {row['ts_code']}", left + 0.6, top, 2.55, 0.4,
                      size=11, bold=True, valign=MSO_ANCHOR.MIDDLE)
            _add_text(slide, str(row["primary_theme"]), left + 3.08, top, 2.35, 0.4,
                      size=10.5, color=color, bold=True, valign=MSO_ANCHOR.MIDDLE)
            _add_text(slide, f"{int(row['target_days'])}d", left + 5.42, top, 0.55, 0.4,
                      size=10.5, color="#334155", align=PP_ALIGN.RIGHT, valign=MSO_ANCHOR.MIDDLE)
        _add_slide_number(slide, len(deck.slides))

    # Category dividers and stock slides.
    category_slide_count = 0
    stock_slide_count = 0
    for category_index, category in enumerate(categories, start=1):
        group = ordered.loc[ordered["broad_category"].eq(category)].copy()
        color = CATEGORY_COLORS.get(category, "#64748B")
        slide = deck.slides.add_slide(blank)
        _set_slide_background(slide, "#0B1220")
        _add_rect(slide, 0, 0, 0.18, 7.5, fill=color)
        _add_text(slide, f"{category_index:02d}", 0.72, 0.84, 1.3, 0.7, size=32, color=color, bold=True)
        _add_text(slide, category, 0.72, 1.55, 8.7, 0.82, size=32, color="#FFFFFF", bold=True)
        _add_text(slide, f"{len(group)} 只股票", 0.76, 2.5, 3.0, 0.4, size=15, color="#CBD5E1")
        midpoint = math.ceil(len(group) / 2)
        for column, subset in enumerate((group.iloc[:midpoint], group.iloc[midpoint:])):
            lines = [
                f"{int(row['rank']):02d}  {row['stock_name']} · {row['primary_theme']} · {int(row['target_days'])}d"
                for _, row in subset.iterrows()
            ]
            _add_text(slide, "\n".join(lines), 0.8 + column * 6.15, 3.1, 5.72, 3.4,
                      size=13, color="#E2E8F0")
        _add_slide_number(slide, len(deck.slides), dark=True)
        category_slide_count += 1

        for _, row in group.iterrows():
            slide = deck.slides.add_slide(blank)
            _set_slide_background(slide, "#F1F5F9")
            _add_rect(slide, 0, 0, 0.13, 7.5, fill=color)
            _add_rect(slide, 0.36, 0.28, 0.58, 0.58, fill=color, radius=True)
            _add_text(slide, f"{int(row['rank']):02d}", 0.37, 0.29, 0.56, 0.56, size=18,
                      color="#FFFFFF", bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE,
                      margin=0)
            _add_text(slide, f"{row['stock_name']}  {row['ts_code']}", 1.08, 0.28, 6.2, 0.47,
                      size=23, bold=True, valign=MSO_ANCHOR.MIDDLE)
            _add_pill(slide, category, 8.85, 0.35, 2.25, color)
            status = "截至期末仍在延续" if bool(row["right_censored"]) else "已结束"
            carry = " · 跨年延续" if bool(row["carried_in"]) else ""
            _add_text(
                slide,
                f"MA60窗口内连续上升 {int(row['target_days'])} 个交易日 · "
                f"{str(row['start_date'])[:4]}-{str(row['start_date'])[4:6]}-{str(row['start_date'])[6:]} → "
                f"{str(row['end_date'])[:4]}-{str(row['end_date'])[4:6]}-{str(row['end_date'])[6:]} · {status}{carry}",
                1.1, 0.78, 10.95, 0.32, size=10.5, color="#64748B",
            )
            chart_path = Path(chart_paths[str(row["ts_code"])])
            slide.shapes.add_picture(
                str(chart_path), Inches(0.36), Inches(1.22), width=Inches(10.25), height=Inches(5.42)
            )
            _add_rect(slide, 10.79, 1.22, 2.2, 5.42, fill="#FFFFFF", radius=True, line="#DDE5EF")
            _add_text(slide, "主归类", 11.06, 1.5, 1.65, 0.25, size=9.5, color="#94A3B8", bold=True)
            _add_text(slide, str(row["primary_theme"]), 11.03, 1.78, 1.7, 0.72,
                      size=15, color=color, bold=True)
            _add_text(slide, "次题材", 11.06, 2.66, 1.65, 0.25, size=9.5, color="#94A3B8", bold=True)
            secondary = " / ".join(row["secondary_themes"]) or "—"
            _add_text(slide, secondary, 11.03, 2.95, 1.7, 0.88, size=11, color="#334155")
            _add_text(slide, "证据标签", 11.06, 3.95, 1.65, 0.25, size=9.5, color="#94A3B8", bold=True)
            labels = " / ".join(row["evidence_labels"][:5]) or "行业兜底"
            _add_text(slide, labels, 11.03, 4.24, 1.7, 0.85, size=10.5, color="#475569")
            _add_text(slide, f"行业  {row['industry'] or '未标注'}", 11.04, 5.2, 1.72, 0.3,
                      size=10, color="#64748B", bold=True)
            confidence_map = {"high": "高", "medium": "中", "low": "低"}
            _add_text(slide, f"归类置信度  {confidence_map.get(row['confidence'], row['confidence'])}",
                      11.04, 5.56, 1.72, 0.3, size=10, color="#64748B", bold=True)
            _add_text(slide, "来源：本地日线 + 开盘啦历史题材；不构成投资建议。", 10.98, 6.06, 1.82, 0.45,
                      size=8.2, color="#94A3B8")
            _add_slide_number(slide, len(deck.slides))
            stock_slide_count += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    deck.save(output)
    return {
        "slides": len(deck.slides),
        "stock_slides": stock_slide_count,
        "category_slides": category_slide_count,
        "inventory_slides": inventory_pages,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report(stocks: pd.DataFrame, output: Path, metadata: dict) -> None:
    lines = [
        "# 2026 YTD MA60 连续上升 Top 1%：题材归类与日K图册",
        "",
        f"- 数据截止：`{metadata['actual_target_end']}`",
        f"- 有效股票池：{metadata['valid_universe']:,} 只",
        f"- 名义 Top 1%：{metadata['nominal_top_n']} 只；并列后：{len(stocks)} 只",
        f"- 入围线：窗口内连续上升 `{metadata['cutoff_target_days']}` 个交易日",
        "- 排名单位：股票；每只股票取 2026 目标窗口内最长 MA60 连续上升段。",
        "- 题材证据：本地开盘啦 2026 历史涨停题材、概念成分与行业兜底。",
        "",
    ]
    for category in _category_sequence(stocks):
        group = stocks.loc[stocks["broad_category"].eq(category)].sort_values("rank")
        lines.extend([f"## {category}（{len(group)}只）", "", "| 排名 | 代码 | 名称 | 主归类 | 次题材 | MA60天数 |", "|---:|---|---|---|---|---:|"])
        for _, row in group.iterrows():
            secondary = "、".join(row["secondary_themes"]) or "—"
            lines.append(
                f"| {int(row['rank'])} | {row['ts_code']} | {row['stock_name']} | "
                f"{row['primary_theme']} | {secondary} | {int(row['target_days'])} |"
            )
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=Path("outputs/ma60_top1pct_themes/2026_through_20260930"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/ma60_top1pct_ppt/2026_through_20260930"),
    )
    parser.add_argument("--daily-start", default="20240101")
    return parser.parse_args()


def main() -> None:
    root = Path(__file__).resolve().parents[5]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from coreClient.data_provider import get_day

    args = _parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = args.output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    selection_metadata = json.loads((args.analysis_dir / "metadata.json").read_text())
    top = pd.read_csv(args.analysis_dir / "top_ma60_stocks.csv", dtype={"ts_code": str})
    evidence = json.loads((args.analysis_dir / "theme_evidence_summary.json").read_text())
    evidence_details = pd.DataFrame(
        [
            {
                "ts_code": item["ts_code"],
                "stock_name": item["stock_name"],
                "industry": item["industry"],
            }
            for item in evidence
        ]
    )
    top = top.merge(evidence_details, on="ts_code", how="left", validate="one_to_one")
    classifications = materialize_classifications(evidence, MANUAL_CLASSIFICATIONS)
    stocks = validate_classifications(top, classifications).reset_index(drop=True)
    if len(stocks) != 58:
        raise ValueError(f"expected 58 selected stocks, got {len(stocks)}")
    if stocks[["stock_name", "industry"]].isna().any().any():
        raise ValueError("missing stock name or industry")

    classification_records = json.loads(
        stocks.to_json(orient="records", force_ascii=False)
    )
    (args.output_dir / "classifications.json").write_text(
        json.dumps(classification_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    classification_csv = stocks.copy()
    for column in ("secondary_themes", "evidence_labels"):
        classification_csv[column] = classification_csv[column].map(lambda values: " | ".join(values))
    classification_csv.to_csv(args.output_dir / "classifications.csv", index=False, encoding="utf-8-sig")

    codes = stocks["ts_code"].tolist()
    data_end = str(selection_metadata["actual_target_end"])
    daily = get_day(
        ts_codes=codes,
        start_date=args.daily_start,
        end_date=data_end,
        qfq=DAILY_QFQ,
        source="database_only",
    )
    if daily.empty:
        raise ValueError("local daily database returned no rows")
    daily["ts_code"] = daily["ts_code"].astype(str)
    actual_codes = set(daily["ts_code"])
    if set(codes) != actual_codes:
        raise ValueError(
            f"daily coverage mismatch: missing={sorted(set(codes) - actual_codes)}, "
            f"extra={sorted(actual_codes - set(codes))}"
        )
    duplicates = daily.duplicated(["ts_code", "trade_date"])
    if duplicates.any():
        raise ValueError(f"duplicate daily rows: {int(duplicates.sum())}")
    daily = add_moving_averages(daily)
    daily.to_csv(
        args.output_dir / "daily_input_unadjusted.csv.gz",
        index=False,
        compression="gzip",
    )

    chart_paths: dict[str, Path] = {}
    chart_receipts: list[dict[str, object]] = []
    for _, row in stocks.sort_values("rank").iterrows():
        ts_code = str(row["ts_code"])
        image_path = charts_dir / f"{int(row['rank']):02d}_{ts_code.replace('.', '_')}.png"
        stock_daily = daily.loc[daily["ts_code"].eq(ts_code)].copy()
        receipt = render_kline_chart(
            stock_daily,
            image_path,
            stock_name=str(row["stock_name"]),
            ts_code=ts_code,
            episode_start=str(row["start_date"]),
            episode_end=str(row["end_date"]),
            display_start=display_start_for_episode(str(row["start_date"])),
            display_end=pd.Timestamp(data_end),
        )
        receipt["rank"] = int(row["rank"])
        receipt["sha256"] = _sha256(image_path)
        chart_receipts.append(receipt)
        chart_paths[ts_code] = image_path
        print(f"[{int(row['rank']):02d}/58] {row['stock_name']} {ts_code}", flush=True)

    deck_path = args.output_dir / "MA60_Top1pct_58stocks_2026YTD_Kline_Atlas.pptx"
    deck_receipt = build_presentation(
        stocks,
        chart_paths,
        deck_path,
        data_end=data_end,
        valid_universe=int(selection_metadata["valid_universe"]),
        nominal_top_n=int(selection_metadata["nominal_top_n"]),
        cutoff_days=int(selection_metadata["cutoff_target_days"]),
    )
    reopened = Presentation(deck_path)
    embedded_images = sum(
        1
        for slide in reopened.slides
        for shape in slide.shapes
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
    )
    if len(reopened.slides) != deck_receipt["slides"]:
        raise ValueError("saved deck slide count changed after reopen")
    if embedded_images != 58:
        raise ValueError(f"expected 58 embedded chart images, got {embedded_images}")

    output_metadata = {
        **selection_metadata,
        "generated_at": datetime.now().astimezone().isoformat(),
        "daily_access": f"coreClient.data_provider.get_day(source='database_only', qfq={DAILY_QFQ})",
        "daily_start": args.daily_start,
        "daily_end": data_end,
        "daily_rows": int(len(daily)),
        "daily_codes": int(daily["ts_code"].nunique()),
        "categories": {
            key: int(value)
            for key, value in stocks["broad_category"].value_counts().to_dict().items()
        },
        "charts": len(chart_receipts),
        "deck": deck_receipt,
        "embedded_images": embedded_images,
        "pptx_sha256": _sha256(deck_path),
        "pptx_bytes": deck_path.stat().st_size,
    }
    (args.output_dir / "chart_receipts.json").write_text(
        json.dumps(chart_receipts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "metadata.json").write_text(
        json.dumps(output_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_report(stocks, args.output_dir / "report.md", output_metadata)
    print(json.dumps(output_metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
