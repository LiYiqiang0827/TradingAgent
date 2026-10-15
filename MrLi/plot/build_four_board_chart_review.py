"""Plot the frozen four-board cohort with one daily and one 15-minute chart.

Uses the existing offline plotting tools. Keeps every source episode, including
recent episodes whose requested future window is not yet available. Never
selects cases using the subsequent price path. Re-running reuses only images
whose data hash, requested window and rendering settings are unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plot import plot_daily_kline as daily
from plot import plot_fifteen_min_kline as minute

SOURCE = ROOT / "outputs/four_board_paths_2026/four_board_paths.csv"
OUTPUT = ROOT / "outputs/four_board_chart_review_2026"


def read_cohort(source: Path) -> pd.DataFrame:
    """Use only identity, D0 and contemporaneous raw theme from the frozen CSV."""
    frame = pd.read_csv(source, dtype=str, usecols=["ts_code", "name", "d0", "d0_theme_raw"])
    if frame[["ts_code", "name", "d0"]].isna().any().any():
        raise ValueError("四板清单的股票或日期缺失")
    if frame.duplicated(["ts_code", "d0"]).any():
        raise ValueError("四板清单包含重复的股票/四板日期")
    pd.to_datetime(frame.d0, format="%Y%m%d", errors="raise")
    frame["d0_theme_raw"] = frame.d0_theme_raw.fillna("未标注")
    frame = frame.sort_values(["d0", "ts_code"]).reset_index(drop=True)
    frame.insert(0, "case_number", range(1, len(frame) + 1))
    return frame


def windows(d0: str):
    return (
        daily.resolve_window(start_date=None, end_date=None, trade_date=d0,
                             lookback_months=12, lookahead_months=1,
                             ma_warmup_days=400, to_latest=False),
        minute.resolve_window(start_date=None, end_date=None, trade_date=d0,
                              lookback_months=1, lookahead_days=14,
                              ma_warmup_bars=250, to_latest=False),
    )


def _digest(frame: pd.DataFrame, window, dpi: int, kind: str) -> str:
    # Includes MA warmup effects, volumes and adjustment effects in loaded data.
    h = hashlib.sha256(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
    h.update(json.dumps({"kind": kind, "dpi": dpi, "plot_version": 1,
                         "start": str(window.display_start), "end": str(window.display_end),
                         "d0": str(window.selected_start)}, sort_keys=True).encode())
    return h.hexdigest()


def _coverage(frame: pd.DataFrame, window, kind: str) -> dict:
    anchor = frame.loc[frame.date.eq(window.selected_start)]
    expected = 1 if kind == "daily" else 16
    if len(anchor) != expected:
        raise ValueError(f"四板当天{kind}根数为{len(anchor)}，应为{expected}")
    if kind == "minute":
        expected_times = [f"{h:02d}:{m:02d}" for h, m in
                          [(9,45),(10,0),(10,15),(10,30),(10,45),(11,0),(11,15),(11,30),
                           (13,15),(13,30),(13,45),(14,0),(14,15),(14,30),(14,45),(15,0)]]
        if anchor.datetime.dt.strftime("%H:%M").tolist() != expected_times:
            raise ValueError("四板当天15分钟时间戳不完整或不符合09:45至15:00口径")
    by_day = frame.groupby("date").size()
    return {
        "requested_start": window.display_start.strftime("%Y%m%d"),
        "requested_end": window.display_end.strftime("%Y%m%d"),
        "actual_start": frame.date.min().strftime("%Y%m%d"),
        "actual_end": frame.date.max().strftime("%Y%m%d"),
        "bars": len(frame), "trading_days": len(by_day), "anchor_bars": len(anchor),
        "anchor_first": (anchor.datetime.iloc[0].isoformat() if kind == "minute" else str(anchor.date.iloc[0].date())),
        "anchor_last": (anchor.datetime.iloc[-1].isoformat() if kind == "minute" else str(anchor.date.iloc[-1].date())),
        "incomplete_days": [d.strftime("%Y%m%d") for d,n in by_day.items()
                            if kind == "minute" and n != 16],
    }


def generate(source: Path, output: Path, *, dpi: int = 160, limit: int | None = None) -> dict:
    cohort = read_cohort(source)
    if limit is not None:
        cohort = cohort.head(limit).copy()
    output.mkdir(parents=True, exist_ok=True)
    old = {}
    manifest_path = output / "chart_manifest.json"
    if manifest_path.exists():
        old = { (r["ts_code"],r["d0"]):r for r in json.loads(manifest_path.read_text())["cases"] }
    records = []
    for row in cohort.to_dict("records"):
        dwindow, mwindow = windows(row["d0"])
        dframe = daily.load_daily_data(row["ts_code"], dwindow, qfq=True)
        mframe = minute.load_fifteen_min_data(row["ts_code"], mwindow, qfq=True)
        record = dict(row)
        previous = old.get((row["ts_code"],row["d0"]),{})
        for kind, frame, window, module in (("daily",dframe,dwindow,daily),
                                            ("minute",mframe,mwindow,minute)):
            info = _coverage(frame, window, kind)
            info["data_sha256"] = _digest(frame, window, dpi, kind)
            path = module.build_save_path(row["ts_code"], window, output / f"{kind}_charts").resolve()
            if not path.exists() or previous.get(kind,{}).get("data_sha256") != info["data_sha256"]:
                plot = module.plot_daily_kline if kind == "daily" else module.plot_fifteen_min_kline
                plot(frame, ts_code=row["ts_code"], stock_name=row["name"], window=window,
                     qfq=True, save_path=path, show=False, dpi=dpi)
            info["image"] = str(path)
            info["image_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            record[kind] = info
        # Daily bars establish actual tradability, avoiding false suspension gaps.
        expected = set(dframe.loc[(dframe.date >= mwindow.display_start) &
                                  (dframe.date <= mframe.date.max()),"date"])
        record["minute"]["missing_daily_trading_dates"] = sorted(
            d.strftime("%Y%m%d") for d in expected - set(mframe.date))
        records.append(record)
        manifest_path.write_text(json.dumps({"cases":records},ensure_ascii=False,indent=2))
        print(f"图表 {row['case_number']}/{len(cohort)} {row['name']} {row['ts_code']} 四板 {row['d0']}", flush=True)

    # Last observed dates are global source limits across this actual chart cohort.
    latest = {kind:max(r[kind]["actual_end"] for r in records) for kind in ("daily","minute")}
    for row in records:
        for kind in ("daily","minute"):
            info = row[kind]
            info["pending_future_window"] = info["requested_end"] > latest[kind]
    summary = {
        "cases": len(records), "stocks": cohort.ts_code.nunique(), "charts": len(records)*2,
        "d0_first": cohort.d0.min(), "d0_last": cohort.d0.max(),
        "monthly_cases": cohort.groupby(cohort.d0.str[:6]).size().to_dict(),
        "source": str(source.resolve()), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "latest_display_data": latest,
        "future_window_pending": {k:sum(r[k]["pending_future_window"] for r in records) for k in latest},
        "minute_incomplete_days": sum(len(r["minute"]["incomplete_days"]) for r in records),
        "minute_missing_stock_days": sum(len(r["minute"]["missing_daily_trading_dates"]) for r in records),
        "daily_window": "D0-12 calendar months through D0+1 calendar month",
        "minute_window": "D0-1 calendar month through D0+14 calendar days",
        "marker": "D0 daily candle and all 16 D0 intraday bars (09:30 opening through 15:00 close)",
    }
    payload = {"summary": summary, "cases": records, "source": str(output.resolve())}
    manifest_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    (output / "deck_data.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    flat = []
    for row in records:
        entry={k:v for k,v in row.items() if k not in ("daily","minute")}
        for kind in ("daily","minute"):
            entry.update({f"{kind}_{k}":v for k,v in row[kind].items()})
        flat.append(entry)
    pd.DataFrame(flat).to_csv(output / "四板175次_图表索引.csv",index=False,encoding="utf-8-sig")
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return payload


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=SOURCE)
    parser.add_argument("--output",type=Path,default=OUTPUT)
    parser.add_argument("--dpi",type=int,default=160)
    parser.add_argument("--limit",type=int)
    args=parser.parse_args()
    generate(args.source,args.output,dpi=args.dpi,limit=args.limit)
