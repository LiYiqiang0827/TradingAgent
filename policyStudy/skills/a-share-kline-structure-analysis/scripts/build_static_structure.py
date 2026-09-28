#!/usr/bin/env python3
"""Build future-isolated static K-line structure JSON and an optional chart."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[3]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from static_structure.engine import analyze_packet  # noqa: E402
from static_structure.render import (  # noqa: E402
    render_multitimeframe_structure,
    render_split_analysis_views,
    render_static_structure,
    render_timeframe_details,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--packet", type=Path)
    source.add_argument("--ts-code")
    result.add_argument("--as-of", help="Required with --ts-code, YYYYMMDD")
    result.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    result.add_argument("--bars", type=int, default=750)
    result.add_argument("--context-bars", type=int, default=1250)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--plot", type=Path)
    result.add_argument("--plot-multitimeframe", type=Path)
    result.add_argument("--plot-timeframes-dir", type=Path)
    result.add_argument("--plot-split-views-dir", type=Path)
    result.add_argument("--plot-bars", type=int, default=260)
    result.add_argument("--skip-official-limit-data", action="store_true")
    return result


def _load_builder(project_root: Path):
    path = project_root / "policyStudy" / "skills" / "a-share-kline-structure-analysis" / "scripts" / "build_kline_packet.py"
    spec = importlib.util.spec_from_file_location("build_kline_packet_for_static", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import packet builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_packet(args: argparse.Namespace) -> dict:
    if args.packet:
        return json.loads(args.packet.expanduser().read_text(encoding="utf-8"))
    if not args.as_of:
        raise ValueError("--as-of is required with --ts-code")
    module = _load_builder(args.project_root.resolve())
    build_args = SimpleNamespace(
        project_root=args.project_root.resolve(),
        ts_code=args.ts_code,
        as_of=str(args.as_of).replace("-", ""),
        bars=args.bars,
        context_bars=args.context_bars,
        swing_order=3,
    )
    return module.build_packet(build_args)


def official_limit_dates(packet: dict, project_root: Path) -> set[str] | None:
    try:
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        from coreClient.data_provider import get_day, get_stk_limit

        start = str(packet["daily"][0]["trade_date"])
        end = str(packet["as_of"])
        raw = get_day(ts_code=packet["ts_code"], start_date=start, end_date=end, qfq=False, source="database_only")
        limits = get_stk_limit(ts_code=packet["ts_code"], start_date=start, end_date=end, source="database_only")
        if raw is None or limits is None or raw.empty or limits.empty:
            return None
        raw = raw.copy()
        limits = limits.copy()
        raw["trade_date"] = raw["trade_date"].astype(str).str.replace("-", "", regex=False)
        limits["trade_date"] = limits["trade_date"].astype(str).str.replace("-", "", regex=False)
        merged = raw[["trade_date", "close"]].merge(limits[["trade_date", "up_limit"]], on="trade_date", how="inner")
        close = pd.to_numeric(merged["close"], errors="coerce")
        up_limit = pd.to_numeric(merged["up_limit"], errors="coerce")
        matched = merged[(close - up_limit).abs() <= 0.011]
        return set(matched["trade_date"].astype(str))
    except Exception:
        return None


def main() -> None:
    args = parser().parse_args()
    packet = load_packet(args)
    dates = None if args.skip_official_limit_data else official_limit_dates(packet, args.project_root.resolve())
    analysis = analyze_packet(packet, official_limit_dates=dates)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    rendered = None
    if args.plot:
        rendered = render_static_structure(packet, analysis, args.plot, bars=args.plot_bars)
    multitimeframe = None
    if args.plot_multitimeframe:
        multitimeframe = render_multitimeframe_structure(packet, analysis, args.plot_multitimeframe)
    timeframe_outputs = {}
    if args.plot_timeframes_dir:
        timeframe_outputs = render_timeframe_details(packet, analysis, args.plot_timeframes_dir)
    split_view_outputs = {}
    if args.plot_split_views_dir:
        split_view_outputs = render_split_analysis_views(packet, analysis, args.plot_split_views_dir)
    print(json.dumps({
        "output": str(output),
        "plot": None if rendered is None else str(rendered),
        "plot_multitimeframe": None if multitimeframe is None else str(multitimeframe),
        "plot_timeframes": {key: str(value) for key, value in timeframe_outputs.items()},
        "plot_split_views": {key: str(value) for key, value in split_view_outputs.items()},
        "summary": {
        "pivots": len(analysis["confirmed_pivots"]),
        "zones": len(analysis["horizontal_zones"]),
        "platforms": len(analysis["platforms"]),
        "swing_paths": len(analysis["swing_paths"]),
        "price_envelopes": len(analysis["price_envelopes"]),
        "envelope_trendlines": len(analysis["envelope_trendlines"]),
        "trendlines": len(analysis["trendlines"]),
        "trend_channels": len(analysis["trend_channels"]),
        "acceleration_legs": len(analysis["acceleration_legs"]),
        "patterns": len(analysis["chart_patterns"]),
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
