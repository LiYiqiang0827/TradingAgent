"""Draw every de-duplicated MA30 reactivation candidate as it looked on signal day.

The gallery table separately shows the retrospective ten-session open-to-open
simulation. Future prices never enter the charts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SCRIPTS = ROOT / "policyStudy" / "policy" / "题材涨停研究" / "scripts"
for path in (ROOT, RESEARCH_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from coreClient.data_provider import get_adj_factor, get_day, get_kpl_list  # noqa: E402
from backtest_rising_ma_reactivation_202605_202608 import _stock_frame  # noqa: E402
from study_rising_ma_reactivation import plot_template  # noqa: E402


DEFAULT_SOURCE = ROOT / "outputs" / "rising_ma_reactivation" / "may_aug_2026_final" / "first_episode_matches.csv"
DEFAULT_OUTPUT = DEFAULT_SOURCE.parent / "ma30_charts"


def _fmt_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:]}" if len(value) == 8 else ""


def _fmt_pct(value: float) -> str:
    return f"{value:+.2f}%" if np.isfinite(value) else "—"


def _name_lookup(kpl: pd.DataFrame) -> dict[tuple[str, str], str]:
    return {(str(row.ts_code), str(row.trade_date)): str(row.name)
            for row in kpl.itertuples()
            if pd.notna(row.name) and str(row.name).strip()}


def generate(source: Path, output: Path) -> dict:
    episodes = pd.read_csv(source, dtype={"ts_code": str, "trade_date": str,
                                          "first_wave_board_dates": str,
                                          "entry_date": str, "exit_date_10d": str})
    episodes = episodes.loc[episodes.template_observation.eq("ma30_coil_restart")]
    episodes = episodes.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    if episodes.empty:
        raise ValueError("没有 MA30 整理再启动候选")
    if episodes.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("同一股票和信号日有重复候选")

    output.mkdir(parents=True, exist_ok=True)
    codes = sorted(episodes.ts_code.unique())
    end = str(episodes.trade_date.max())
    raw = get_day(ts_codes=codes, start_date="20250101", end_date=end,
                  qfq=False, source="database_only")
    adj = get_adj_factor(ts_codes=codes, start_date="20250101", end_date=end,
                         source="database_only")
    raw.trade_date = raw.trade_date.astype(str)
    adj.trade_date = adj.trade_date.astype(str)
    raw_groups = dict(tuple(raw.groupby("ts_code", sort=False)))
    adj_groups = dict(tuple(adj.groupby("ts_code", sort=False)))
    first_board_dates = episodes.first_wave_board_dates.str.split("|").str[-1]
    kpl = get_kpl_list(start_date=str(first_board_dates.min()), end_date=end,
                       tags="涨停", source="database_only")
    kpl.trade_date = kpl.trade_date.astype(str)
    names = _name_lookup(kpl)

    records = []
    failures = []
    for code, group in episodes.groupby("ts_code", sort=False):
        if code not in raw_groups or code not in adj_groups:
            failures.extend({"ts_code": code, "trade_date": str(row.trade_date),
                             "error": "缺少日线或复权因子"} for row in group.itertuples())
            continue
        stock = _stock_frame(raw_groups[code], adj_groups[code])
        for row in group.itertuples():
            try:
                day = str(row.trade_date)
                sliced = stock.loc[stock.trade_date.le(day)].copy()
                if sliced.empty or str(sliced.iloc[-1].trade_date) != day:
                    raise ValueError("信号日缺少日线")
                factor = float(sliced.iloc[-1].adj_factor)
                if not np.isfinite(factor) or factor <= 0:
                    raise ValueError("信号日复权因子无效")
                # QFQ normalized at the decision-date close. No later factor is read.
                for column in ("open", "high", "low", "close", "ma5", "ma10",
                               "ma20", "ma30", "ma60", "ma120"):
                    sliced[column] = sliced[column] / factor
                board_day = str(row.first_wave_board_dates).split("|")[-1]
                name = names.get((code, board_day), "")
                target = output / f"ma30_{code}_{day}.jpg"
                result = row._asdict()
                result["name"] = name
                plot_template(sliced, result, target)
                net = float(row.net_10d_pct)
                records.append({
                    "ts_code": code, "name": name, "signal_date": day,
                    "entry_date": str(row.entry_date),
                    "exit_date": str(row.exit_date_10d),
                    "price_change_pct": net + .4,
                    "estimated_net_pct": net,
                    "theme": str(row.theme_tags).replace("|", "、"),
                    "chart_path": str(target.resolve()),
                })
            except Exception as exc:
                failures.append({"ts_code": code, "trade_date": str(row.trade_date),
                                 "error": str(exc)})

    records.sort(key=lambda item: (item["signal_date"], item["ts_code"]))
    pd.DataFrame(records).to_csv(output / "MA30候选交易表.csv", index=False,
                                 encoding="utf-8-sig")
    lines = [
        "# 2026 年 5–8 月 MA30 整理再启动候选日线图",
        "",
        f"共 {len(episodes)} 次去重候选、{len(records)} 张日线图。点击股票代码查看该次信号日的图。",
        "图只展示截至筛选日的日线、MA20/30/60/120、实际成交量、第一波自身涨停、第一波阶段高点与启动前均量基准；不展示后续行情。",
        "",
        "下表的买卖日期是**模拟**：筛选日次一交易日开盘买入，持有十个交易日后开盘卖出；跌停无法卖出则按原回放顺延。区间涨跌幅按复权等价买卖开盘价之比计算，估算净收益再扣买卖合计 0.4% 成本。开盘价只是成交代理，并非真实成交保证。题材为第一波最后一次涨停当日的开盘啦原始标签，可能含附加标签。",
        "",
        "| 股票代码 | 名字 | 模拟买入日期 | 模拟卖出日期 | 区间涨跌幅 | 估算净收益 | 题材 |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for item in records:
        chart = Path(item["chart_path"]).name
        label = item["name"].replace("|", "、")
        theme = item["theme"].replace("|", "、")
        lines.append(
            f"| [{item['ts_code']}]({chart}) | {label} | "
            f"{_fmt_date(item['entry_date'])} | {_fmt_date(item['exit_date'])} | "
            f"{_fmt_pct(item['price_change_pct'])} | "
            f"{_fmt_pct(item['estimated_net_pct'])} | {theme} |"
        )
    (output / "MA30候选交易表.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "errors.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    summary = {"candidates": len(episodes), "charts": len(records),
               "failed": len(failures), "output": str(output.resolve())}
    (output / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = generate(args.source, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
