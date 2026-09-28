"""题材周期图共用的配色、交易日坐标和保存工具。"""
from __future__ import annotations

from pathlib import Path
import re

import matplotlib.pyplot as plt
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_THEME_CHART_DIR = PROJECT_ROOT / "plot" / "tmp_savepic" / "theme_cycle"

STRUCTURE_COLORS = {
    "clear_single_mainline": "#d73027",
    "multiple_mainlines": "#fdae61",
    "mainline_replacement": "#8e44ad",
    "scattered_no_mainline": "#74add1",
    "cold_no_mainline": "#7f8c8d",
}
STRUCTURE_LABELS = {
    "clear_single_mainline": "主线明确",
    "multiple_mainlines": "多主线",
    "mainline_replacement": "主线替换",
    "scattered_no_mainline": "热点分散",
    "cold_no_mainline": "低迷无主线",
}
LIFECYCLE_COLORS = {
    "沉寂": "#d9d9d9",
    "试探": "#bdbdbd",
    "启动": "#80b1d3",
    "发酵": "#fdb462",
    "加速": "#fb8072",
    "高潮": "#d73027",
    "分歧": "#bc80bd",
    "延续": "#fccde5",
    "退潮": "#80cdc1",
}


def configure_style() -> None:
    plt.rcParams["font.sans-serif"] = [
        "Arial Unicode MS", "PingFang SC", "Microsoft YaHei", "SimHei", "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["axes.facecolor"] = "#fcfcfc"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.18


def safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", str(value)).strip("_") or "theme"


def trade_date_ticks(ax, labels: list[str], max_ticks: int = 14) -> None:
    if not labels:
        return
    step = max(1, int(np.ceil(len(labels) / max_ticks)))
    positions = list(range(0, len(labels), step))
    if positions[-1] != len(labels) - 1:
        positions.append(len(labels) - 1)
    ax.set_xticks(positions)
    ax.set_xticklabels([labels[index] for index in positions], rotation=35, ha="right", fontsize=8)


def contiguous_runs(values: list[str]) -> list[tuple[int, int, str]]:
    if not values:
        return []
    runs: list[tuple[int, int, str]] = []
    start = 0
    for index in range(1, len(values)):
        if values[index] != values[start]:
            runs.append((start, index - 1, values[start]))
            start = index
    runs.append((start, len(values) - 1, values[start]))
    return runs


def save_figure(fig, path: Path, dpi: int = 180) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path.resolve()
