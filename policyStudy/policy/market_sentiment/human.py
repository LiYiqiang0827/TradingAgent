"""Independent blind-label packets and per-dimension human agreement checks."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import random
import re
from typing import Any

import numpy as np
import pandas as pd

from .readings import SPEC

WEATHER_ORDER = tuple(SPEC["weather_order"])
WEATHER_LABELS = dict(SPEC["weather_labels"])
DIMENSIONS = ("hit", "cont", "act")
BAND_LABELS = {"hit": ("轻", "中", "重"), "cont": ("差", "一般", "好"), "act": ("低", "中", "高")}
LABEL_COLUMNS = ["trade_date", "hit", "cont", "act", "weather", "notes"]
# This is a whitelist, never a prefix selection: scores and machine explanations
# also begin with mkt_, and must not leak into a packet.
NEUTRAL_FIELDS = {
    "mkt_eligible_n": ("合格股票数", "只"),
    "mkt_up_n": ("涨停数", "只"), "mkt_down_n": ("跌停数", "只"),
    "mkt_broken_n": ("炸板数", "只"), "mkt_max_height": ("最高连板", "板"),
    "mkt_turnover_cny": ("全市场成交额", "元"),
    "mkt_ma20_cny": ("此前20交易日成交均额（不含当日）", "元"),
    "mkt_ratio20": ("成交额/此前20日均额", "倍"),
    "mkt_index_sh_pct": ("上证指数涨跌幅", "%（百分点）"),
    "mkt_index_sz_pct": ("深证成指涨跌幅", "%（百分点）"),
    "mkt_index_cyb_pct": ("创业板指涨跌幅", "%（百分点）"),
    "mkt_advance_n": ("上涨家数（平盘不计）", "只"),
    "mkt_advance_pct": ("上涨家数占比", "0–1"),
    **{f"mkt_height_{h}_n": (f"当日{h}板人数", "只") for h in range(1, 6)},
    "mkt_height_6plus_n": ("当日6板及以上人数", "只"),
    "mkt_ladder": ("有成员的连板档数/6", "0–1"),
    **{f"mkt_{g}_{q}": (f"昨日{label}{description}", unit)
       for g, label in (("all", "全部涨停群体"), ("chain", "≥2板群体"))
       for q, description, unit in (
           ("original_n", "原始人数", "只"), ("observed_n", "今日可观测人数", "只"),
           ("paused_n", "今日停牌人数", "只"), ("missing_n", "今日缺失人数", "只"),
           ("unobservable_n", "今日有记录但不可观测人数", "只"),
           ("drop_k", "今日大跌人数", "只"), ("drop_rate", "今日大跌比例（分母为可观测人数）", "0–1"))},
    **{f"mkt_promo_h{h}_{q}": (f"昨日{'≥4' if h == 4 else h}板{'今日晋级数' if q == 'k' else '可观测晋级分母'}", "只")
       for h in range(1, 5) for q in ("k", "n")},
}


def _date(value: Any) -> str:
    if pd.isna(value):
        raise ValueError("Missing trade_date")
    if isinstance(value, (date, datetime, pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).date().isoformat()
    text = str(value).strip()
    if re.fullmatch(r"\d{8}(?:\.0+)?", text):
        return datetime.strptime(text.split(".")[0], "%Y%m%d").date().isoformat()
    return date.fromisoformat(text).isoformat()


def _daily(daily: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "mkt_weather", *(f"mkt_{d}" for d in DIMENSIONS)}
    if not required.issubset(daily.columns):
        raise ValueError(f"daily missing required columns: {sorted(required - set(daily.columns))}")
    result = daily.copy(deep=True)
    result["trade_date"] = result.trade_date.map(_date)
    if result.trade_date.duplicated().any():
        raise ValueError("daily has duplicate dates")
    invalid = result.mkt_weather.notna() & ~result.mkt_weather.isin(WEATHER_ORDER) & result.mkt_weather.ne("")
    if invalid.any():
        raise ValueError("daily has unknown weather codes")
    return result.sort_values("trade_date").reset_index(drop=True)


def machine_band(value: Any, dimension: str) -> str | None:
    """Use the current frozen configuration's bands; missing has no label."""
    if pd.isna(value) or not np.isfinite(float(value)):
        return None
    score = float(value)
    if not 0 <= score <= 100:
        raise ValueError("A machine reading must lie in 0..100")
    lower, upper = SPEC["human_bins"]
    return BAND_LABELS[dimension][0 if score < lower else 1 if score < upper else 2]


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8")


def _nominations(value: Any) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame(columns=["trade_date", "nominated_by", "notes"])
    if isinstance(value, (str, Path)):
        frame = pd.read_csv(value, dtype=str, keep_default_na=False)
    elif isinstance(value, pd.DataFrame):
        frame = value.copy(deep=True)
    else:
        frame = pd.DataFrame(value)
    if "trade_date" not in frame:
        raise ValueError("nominations require trade_date")
    frame = frame.loc[frame.trade_date.notna() & frame.trade_date.astype(str).str.strip().ne("")].copy()
    frame["trade_date"] = frame.trade_date.map(_date)
    return frame


def _format(value: Any, field: str) -> str:
    if pd.isna(value):
        return "缺失"
    if field.endswith("_pct") and field not in ("mkt_index_sh_pct", "mkt_index_sz_pct", "mkt_index_cyb_pct"):
        return f"{float(value):.2%}"
    if field.endswith("drop_rate") or field == "mkt_ladder":
        return f"{float(value):.2%}"
    if field.endswith("_cny"):
        return f"{float(value) / 1e12:.4f} 万亿元"
    if field.endswith("_n") or field.endswith("_k") or field == "mkt_max_height":
        return str(int(value))
    return f"{float(value):.4f}"


def create_packets(daily: pd.DataFrame, output_dir: str | Path, nominations: Any = None, seed: int = 20261005) -> dict:
    """Write date-sorted neutral facts, independent blank labels and private key."""
    source = _daily(daily)
    nominees = _nominations(nominations)
    duplicate_nominations_n = int(nominees.trade_date.duplicated().sum())
    nominees = nominees.drop_duplicates("trade_date", keep="first").sort_values("trade_date")
    unknown = sorted(set(nominees.trade_date) - set(source.trade_date))
    if unknown:
        raise ValueError(f"Nominated dates absent from daily: {unknown}")
    nominated = set(nominees.trade_date)
    rng = random.Random(seed)
    pools = {code: source.loc[source.mkt_weather.eq(code), "trade_date"].tolist() for code in WEATHER_ORDER}
    initial = {code: rng.sample(pools[code], min(3, len(pools[code]))) for code in WEATHER_ORDER}
    draws, replacements, strata = {}, {}, {}
    for code in WEATHER_ORDER:
        kept = [day for day in initial[code] if day not in nominated]
        overlaps = [day for day in initial[code] if day in nominated]
        available = [day for day in pools[code] if day not in nominated and day not in kept]
        replacements[code] = rng.sample(available, min(len(overlaps), len(available)))
        draws[code] = kept + replacements[code]
        strata[code] = {"candidate_n": len(pools[code]), "requested_random_n": 3,
                       "initial_random_n": len(initial[code]), "overlap_with_nominations_n": len(overlaps),
                       "replacement_n": len(replacements[code]), "final_random_n": len(draws[code]),
                       "random_shortfall_n": 3 - len(draws[code])}
    selected_dates = sorted(nominated | {day for group in draws.values() for day in group})
    selected = source[source.trade_date.isin(selected_dates)].copy()
    facts = selected.reindex(columns=["trade_date", *NEUTRAL_FIELDS])
    packets = Path(output_dir) / "packets"
    private = Path(output_dir) / "private_key"
    existing_selection = private / "selection_manifest.csv"
    if nominations is None and existing_selection.exists():
        frozen_dates = pd.read_csv(existing_selection, dtype=str).trade_date.tolist()
        if frozen_dates != selected_dates:
            raise ValueError("Preserve existing selected dates; use a new packet directory for another sample")
    label_paths = {name: packets / f"{name}_labels.csv" for name in ("Harry", "Li")}
    # Rebuilding a blank packet is safe; overwriting a person's work is not.
    for path in label_paths.values():
        if path.exists():
            existing = pd.read_csv(path, dtype=str, keep_default_na=False)
            content = existing.drop(columns="trade_date", errors="ignore")
            if content.astype(str).apply(lambda column: column.str.strip().ne("")).to_numpy().any():
                raise FileExistsError(f"Preserve existing completed/annotated labels: {path}")
    packets.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    _csv(private / "audit_raw_facts.csv", facts)
    blanks = pd.DataFrame({column: selected_dates if column == "trade_date" else [""] * len(selected_dates) for column in LABEL_COLUMNS})
    for path in label_paths.values():
        _csv(path, blanks)
    nomination_template = packets / "nominations_template.csv"
    if not nomination_template.exists():
        _csv(nomination_template, pd.DataFrame(columns=["trade_date", "nominated_by", "notes"]))
    from .cards import write_cards
    card_paths = write_cards(source, selected_dates, packets)
    # Old wide blind materials become private audit evidence. Paths are fixed
    # children of this explicit output directory, never computed glob moves.
    for filename in ("facts.csv", "facts.md"):
        old = packets / filename
        if old.exists():
            old.replace(private / f"archived_{filename}")
    glossary = ["# Harry 与 Li 独立标注说明", "", "请各自阅读 cards.pdf（每日一张）或 cards.html，在自己的标签 CSV 中填写；先独立标注，再讨论。", "",
                "- hit（挨打）：轻 / 中 / 重。", "- cont（延续）：差 / 一般 / 好。", "- act（活跃）：低 / 中 / 高。",
                "- weather（最接近的天气）：晴 / 多云 / 阴 / 雷阵雨 / 暴雨。", "- notes：可选备注；不确定的标签可以留空。", "",
                "昨日群体按昨日合格涨停身份固定；连板群体包括昨日全部 ≥2 板。今日大跌为收盘相对有效前收跌 5% 及以上或收于跌停，每只只计一次。比例的分母是今日可观测人数。", "",
                "每张卡片只有10项事实。右列按 P10 / 中位数 / P90 显示2025全年可观测日值的常见范围，采用线性分位。参考范围仅帮助理解数值，不替人划分类别。", "",
                "数、分母和比例分别计算分位；所有比例不平滑，以百分比显示。指数1.2%表示+1.2%，金额单位为万亿元。晋级 ≥4 板组包括更高板。缺失比例表示分母为零或无法观测。", "",
                "nominations_template.csv 用于补充约15个记得清楚的日期，填写 YYYY-MM-DD；允许两人重复提名，程序会去重。", "",
                "reference_2025.csv 是小型参考表，记录各子项单位、有效日数及三个分位。原始审计细节留在独立目录，不需要标注者逐项阅读。"]
    (packets / "README.md").write_text("\n".join(glossary) + "\n", encoding="utf-8")
    key = selected.reindex(columns=["trade_date", "mkt_hit", "mkt_cont", "mkt_act", "mkt_weather"])
    for dimension in DIMENSIONS:
        key[f"machine_{dimension}"] = key[f"mkt_{dimension}"].map(lambda value: machine_band(value, dimension))
    key["machine_weather_label"] = key.mkt_weather.map(WEATHER_LABELS)
    _csv(private / "machine_key.csv", key)
    sample_rows = []
    for day in selected_dates:
        code = source.loc[source.trade_date.eq(day), "mkt_weather"].iloc[0]
        sample_rows.append({"trade_date": day, "machine_weather": code,
                            "selection": "nominated" if day in nominated else "replacement" if any(day in group for group in replacements.values()) else "random",
                            "overlapped_initial_random": any(day in group for group in initial.values()) and day in nominated})
    _csv(private / "selection_manifest.csv", pd.DataFrame(sample_rows, columns=["trade_date", "machine_weather", "selection", "overlapped_initial_random"]))
    _csv(private / "nominations_used.csv", nominees)
    paths = {**card_paths, "facts_csv": str(private / "audit_raw_facts.csv"),
             "Harry_labels": str(label_paths["Harry"]), "Li_labels": str(label_paths["Li"]),
             "nominations_template": str(nomination_template), "private_key": str(private / "machine_key.csv"),
             "selection_manifest": str(private / "selection_manifest.csv"), "sampling_summary": str(private / "sampling_summary.json")}
    summary = {"status": "random_part_ready" if not nominated else "complete", "count": len(selected_dates),
               "counts": {"total": len(selected_dates), "random": sum(len(group) for group in draws.values()), "nominated": len(nominated)},
               "seed": seed, "target_total_approx": 30, "duplicate_nominations_n": duplicate_nominations_n,
               "strata": strata, "missing_neutral_fields": [field for field in NEUTRAL_FIELDS if field not in source],
               "paths": paths, "human_labels_filled": False}
    _json(private / "sampling_summary.json", summary)
    (private / "README.md").write_text("# 机器答案与抽样依据：暂勿揭盲\n\n此目录含机器三读数、三档和天气，以及分层抽样依据。Harry 与 Li 完成各自独立标签前，请勿打开或转发；仅把 ../packets/ 交给标注者。评估结果也可能包含答案，须同样保留在此目录。\n", encoding="utf-8")
    (Path(output_dir) / "README.md").write_text(f"# 大盘天气预报：独立人工标注\n\n给标注者的材料在 packets/：cards.pdf 每日一张、cards.html 可离线打印，卡片仅10项中性事实及2025常见范围；另附 Harry 和 Li 各自的空标签、提名模板、小型参考表。请先分别标注，再比较。private_key/ 是机器答案、抽样依据和审计细节；两人独立标注完成前请勿打开或转发。\n\n当前共{len(selected_dates)}个唯一日期，已收到{len(nominated)}个唯一提名。没有收到提名时仅为随机部分，仍需补充约15个记得清楚的日期。补齐入口：`python -m policyStudy.policy.market_sentiment.run human-pack --daily <日表> --output <新的human目录> --nominations <提名CSV>`。填过标签的目录不会被重建覆盖。\n", encoding="utf-8")
    return summary


def _labels(path: str | Path, name: str, available_dates: set[str]) -> pd.DataFrame:
    labels = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = set(LABEL_COLUMNS) - {"notes"}
    if not required.issubset(labels.columns):
        raise ValueError(f"{name} labels missing columns: {sorted(required - set(labels.columns))}")
    if "notes" not in labels:
        labels["notes"] = ""
    labels = labels[LABEL_COLUMNS].copy()
    for column in LABEL_COLUMNS:
        labels[column] = labels[column].str.strip()
    blank_date = labels.trade_date.eq("")
    if labels.loc[blank_date, LABEL_COLUMNS[1:]].ne("").to_numpy().any():
        raise ValueError(f"{name} has annotations without a date")
    labels = labels.loc[~blank_date].copy()
    labels["trade_date"] = labels.trade_date.map(_date)
    if labels.trade_date.duplicated().any():
        raise ValueError(f"{name} labels have duplicate dates")
    unknown = sorted(set(labels.trade_date) - available_dates)
    if unknown:
        raise ValueError(f"{name} label dates absent from daily: {unknown}")
    for dimension in DIMENSIONS:
        invalid = labels[dimension].ne("") & ~labels[dimension].isin(BAND_LABELS[dimension])
        if invalid.any():
            raise ValueError(f"{name} invalid {dimension} label: {labels.loc[invalid, dimension].tolist()}")
    weather_aliases = {**{code: code for code in WEATHER_ORDER}, **{label: code for code, label in WEATHER_LABELS.items()}, "": ""}
    if not labels.weather.isin(weather_aliases).all():
        raise ValueError(f"{name} invalid weather label")
    labels["weather"] = labels.weather.map(weather_aliases)
    return labels.set_index("trade_date")


def _agreement(left: pd.Series, right: pd.Series) -> dict:
    available = left.notna() & left.ne("") & right.notna() & right.ne("")
    n = int(available.sum())
    hits = int(left.loc[available].eq(right.loc[available]).sum())
    return {"n": n, "hits": hits, "accuracy": hits / n if n else None}


def evaluate_labels(daily: pd.DataFrame, harry_path: str | Path, li_path: str | Path,
                    output_dir: str | Path, threshold_revision_count: int = 0) -> dict:
    """Evaluate each dimension independently; never revise any thresholds."""
    if threshold_revision_count not in (0, 1):
        raise ValueError("threshold_revision_count must be 0 or 1")
    source = _daily(daily).set_index("trade_date")
    available_dates = set(source.index)
    harry = _labels(harry_path, "Harry", available_dates).reindex(source.index).fillna("")
    li = _labels(li_path, "Li", available_dates).reindex(source.index).fillna("")
    machine = pd.DataFrame(index=source.index)
    for dimension in DIMENSIONS:
        machine[dimension] = source[f"mkt_{dimension}"].map(lambda value: machine_band(value, dimension))
    machine["weather"] = source.mkt_weather.where(source.mkt_weather.isin(WEATHER_ORDER), None)
    dimensions = {}
    metric_rows, differences = [], []
    for dimension in (*DIMENSIONS, "weather"):
        result = {"machine_vs_harry": _agreement(machine[dimension], harry[dimension]),
                  "machine_vs_li": _agreement(machine[dimension], li[dimension]),
                  "harry_vs_li": _agreement(harry[dimension], li[dimension])}
        if dimension in DIMENSIONS:
            common = harry[dimension].ne("") & li[dimension].ne("") & harry[dimension].eq(li[dimension])
            n = int(common.sum())
            hits = int(machine.loc[common, dimension].eq(harry.loc[common, dimension]).sum())
            result["primary"] = {"n": n, "hits": hits, "accuracy": hits / n if n else None,
                                 "machine_unavailable_n": int(machine.loc[common, dimension].isna().sum()),
                                 "pass": n > 0 and hits / n >= 0.7}
            dimensions[dimension] = result
        for comparison, metric in result.items():
            metric_rows.append({"dimension": dimension, "comparison": comparison, **metric})
        has_harry, has_li = harry[dimension].ne(""), li[dimension].ne("")
        mismatch_harry = has_harry & machine[dimension].ne(harry[dimension])
        mismatch_li = has_li & machine[dimension].ne(li[dimension])
        mismatch_humans = has_harry & has_li & harry[dimension].ne(li[dimension])
        for day in source.index[mismatch_harry | mismatch_li | mismatch_humans]:
            reasons = []
            if pd.isna(machine.at[day, dimension]):
                reasons.append("machine_unavailable")
            if mismatch_harry.at[day]:
                reasons.append("machine_vs_Harry")
            if mismatch_li.at[day]:
                reasons.append("machine_vs_Li")
            if mismatch_humans.at[day]:
                reasons.append("Harry_vs_Li")
            entry = {"trade_date": day, "dimension": dimension, "machine": machine.at[day, dimension],
                     "Harry": harry.at[day, dimension], "Li": li.at[day, dimension], "reason": ";".join(reasons),
                     "Harry_notes": harry.at[day, "notes"], "Li_notes": li.at[day, "notes"]}
            entry.update(source.loc[day].reindex(NEUTRAL_FIELDS).to_dict())
            differences.append(entry)
        if dimension == "weather":
            weather = result
    rating_count = int(harry[[*DIMENSIONS, "weather"]].ne("").sum().sum() + li[[*DIMENSIONS, "weather"]].ne("").sum().sum())
    all_pass = all(result["primary"]["pass"] for result in dimensions.values())
    status = "awaiting_labels" if rating_count == 0 else "pass" if all_pass else "needs_review" if threshold_revision_count == 0 else "failed_after_allowed_revision"
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {"summary": str(output / "human_evaluation.json"), "metrics": str(output / "agreement_metrics.csv"), "differences": str(output / "neutral_disagreements.csv")}
    lower, upper = SPEC["human_bins"]
    summary = {"status": status, "threshold_revision_count": threshold_revision_count,
               "machine_bands": {"low": f"[0,{lower})", "middle": f"[{lower},{upper})", "high": f"[{upper},100]"},
               "primary_threshold": 0.7, "primary_denominator": "per dimension: both humans labeled and agreed; unavailable machine stays in n and is disclosed",
               "dimensions": dimensions, "weather_supplement": weather, "all_dimensions_pass": all_pass,
               "counts": {"daily": len(source), "human_ratings": rating_count, "disagreement_rows": len(differences),
                          "harry_dates_with_ratings": int(harry[[*DIMENSIONS, "weather"]].ne("").any(axis=1).sum()),
                          "li_dates_with_ratings": int(li[[*DIMENSIONS, "weather"]].ne("").any(axis=1).sum())},
               "paths": paths}
    _json(output / "human_evaluation.json", summary)
    _csv(output / "agreement_metrics.csv", pd.DataFrame(metric_rows))
    difference_columns = ["trade_date", "dimension", "machine", "Harry", "Li", "reason", "Harry_notes", "Li_notes", *NEUTRAL_FIELDS]
    _csv(output / "neutral_disagreements.csv", pd.DataFrame(differences, columns=difference_columns).sort_values(["trade_date", "dimension"]))
    (output / "README.md").write_text("# 人工一致率评估\n\n本目录可能包含机器答案和人的标签，仅在独立标注完成后讨论。主标准按挨打、延续、活跃分别计算双方都标注且意见一致的日期；每项均达到70%且各自分母大于零才通过。天气一致率只作补充，不能替代三维主标准。空标签不消耗修订次数；程序只记录传入的 threshold_revision_count，不修改阈值。\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    packet = commands.add_parser("human-pack")
    packet.add_argument("--daily", required=True)
    packet.add_argument("--output", required=True)
    packet.add_argument("--nominations")
    packet.add_argument("--seed", type=int, default=20261005)
    evaluate = commands.add_parser("evaluate-human")
    evaluate.add_argument("--daily", required=True)
    evaluate.add_argument("--harry", required=True)
    evaluate.add_argument("--li", required=True)
    evaluate.add_argument("--output", required=True)
    evaluate.add_argument("--threshold-revision-count", type=int, default=0)
    args = parser.parse_args()
    daily = pd.read_csv(args.daily, float_precision="round_trip")
    result = create_packets(daily, args.output, args.nominations, args.seed) if args.command == "human-pack" else evaluate_labels(daily, args.harry, args.li, args.output, args.threshold_revision_count)
    print(json.dumps(_safe(result), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
