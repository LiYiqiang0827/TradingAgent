"""Evidence packs for retrospective, outcome-stratified theme case studies.

These are learning cases, not blind predictions or matched causal comparisons.
All new market reads use data_provider in database_only mode.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from coreClient import data_provider as dp
from stage1_engine import canonical

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "data/stage1_v1"
ATLAS = BASE / "data/theme_atlas_v1"
OUT = BASE / "data/theme_cases_v1"
WORDS = {
    "光伏": ["光伏", "太阳能", "钙钛矿", "太空", "马斯克"],
    "AI应用": ["AI应用", "Seedance", "seedance", "豆包", "即梦", "视频生成", "字节", "大模型", "人工智能"],
    "化工": ["化工", "化肥", "石化", "乙烯", "甲醇", "沙特", "伊朗", "霍尔木兹"],
    "机器人": ["机器人", "具身", "灵巧手", "减速器", "执行器", "Optimus", "特斯拉"],
    "商业航天": ["航天", "长征十号", "火箭", "星链", "卫星", "太空"],
    "半导体": ["半导体", "芯片", "晶圆", "存储", "光刻", "封装", "台积电", "英伟达"],
}


def clean(x):
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.integer, np.bool_)):
        return x.item()
    if isinstance(x, (float, np.floating)):
        return float(x) if np.isfinite(x) else None
    return x


def save(path, value):
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def cache(name, method, **kwargs):
    path = OUT / "cache" / (name + ".pkl")
    if not path.exists():
        frame = getattr(dp, method)(source="database_only", **kwargs)
        frame.to_pickle(path)
    else:
        frame = pd.read_pickle(path)
    return frame


def normalize(frame):
    frame = frame.copy()
    frame["trade_date"] = frame.trade_date.astype(str).str.replace("-", "")
    return frame


def selected_cases():
    starts = pd.read_csv(ATLAS / "all_initiation_events.csv", dtype={"date": str})
    cases = []
    for first, last in [("202601", "202603"), ("202604", "202606"), ("202607", "202608")]:
        for outcome in [False, True]:
            group = starts.loc[starts.date.str[:6].between(first, last) & (starts.formed == outcome)]
            row = group.sort_values(["width", "date", "theme"], ascending=[False, True, True]).iloc[0].to_dict()
            row["selection"] = "每个时段、每个后续持续性分组中，启动日涨停数最多的事件；非随机、非因果配对"
            cases.append(row)
    return cases


def news_candidates(frame, theme, stock_names, extra_keywords=()):
    """Retrieve evidence for human/LLM reading; relevance is NOT event truth."""
    candidates = []
    keywords = list(dict.fromkeys([*WORDS[theme], *extra_keywords]))
    for r in frame.fillna("").to_dict("records"):
        title = str(r["title"]).strip()
        body = re.sub(r"<[^>]*>", " ", str(r["content"]))
        text = title + " " + body
        matches = [w for w in keywords if w.lower() in text.lower()]
        companies = [w for w in stock_names if len(w) >= 3 and w in text]
        if not matches and not companies:
            continue
        # Exact textual duplicates keep all source IDs, not inflated evidence count.
        key = re.sub(r"\s+", "", title + body)
        candidates.append({"datetime": r["datetime"], "src": r["src"], "md5": r["md5"],
                           "title": title, "content": body, "matches": matches, "companies": companies,
                           "text_hash": hashlib.sha256(key.encode()).hexdigest(),
                           "retrieval_priority": sum(w.lower() in title.lower() for w in keywords)*3 + len(matches) + len(companies)*4,
                           "status": "待语义核验；关键词命中不等于催化"})
    grouped = {}
    for row in sorted(candidates, key=lambda r: r["datetime"]):
        identity = row.pop("text_hash")
        source = {k: row[k] for k in ["src", "datetime", "md5"]}
        if identity not in grouped:
            grouped[identity] = dict(row, source_records=[])
        grouped[identity]["source_records"].append(source)
    return sorted(grouped.values(), key=lambda r: (-r["retrieval_priority"], r["datetime"], r["md5"]))


def cohort_observation(pool, date, day, minutes, limits):
    """Pool frozen before the observation day; missing values stay in denominator."""
    d = day.loc[(day.trade_date == date) & day.ts_code.isin(pool)].set_index("ts_code")
    assert d.index.is_unique
    daily_valid = d.loc[d.pct_chg.notna()]
    date_prefix = datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d")
    minutes = minutes.loc[minutes.datetime.str.startswith(date_prefix)].copy()
    result = {"date": date, "expected": len(pool), "daily_covered": len(daily_valid),
              "daily_median_pct": d.pct_chg.median(), "daily_positive": int((d.pct_chg > 0).sum()),
              "daily_missing": sorted(set(pool)-set(daily_valid.index)), "snapshots": []}
    reference = limits.loc[(limits.trade_date == date) & limits.ts_code.isin(pool)].set_index("ts_code")
    joined = d.join(reference[["up_limit"]])
    result["close_at_limit"] = int(((joined.close-joined.up_limit).abs() < .005).sum())
    for cutoff in ["09:31:00", "09:45:00", "10:30:00", "14:00:00", "15:00:00"]:
        s = minutes.loc[minutes.ts_code.isin(pool) & minutes.datetime.str.endswith(cutoff)].copy()
        assert not s.duplicated("ts_code").any()
        s = s.merge(d[["pre_close"]], on="ts_code", how="inner")
        s = s.loc[(s.pre_close > 0) & (s.price > 0) & np.isfinite(s.price)]
        s["ret"] = (s.price / s.pre_close - 1) * 100
        result["snapshots"].append({"time": cutoff, "covered": len(s), "expected": len(pool),
                                     "median_pct": s.ret.median(), "positive": int((s.ret > 0).sum()),
                                     "negative": int((s.ret < 0).sum()),
                                     "missing": sorted(set(pool)-set(s.ts_code)),
                                     "stock_returns": s[["ts_code", "ret"]].to_dict("records")})
    return result


def main():
    (OUT / "cache").mkdir(parents=True, exist_ok=True)
    records = json.loads((SOURCE / "features.json").read_text())
    by_date = {r["date"]: r for r in records}
    dates = list(by_date)
    kpl = normalize(pd.read_pickle(SOURCE / "cache/kpl.pkl"))
    day = normalize(pd.read_pickle(SOURCE / "cache/day.pkl"))
    limits = normalize(pd.read_pickle(SOURCE / "cache/limits.pkl"))
    market_keys = ["breadth", "up_count", "down_count", "seal_rate", "promotion", "prev_up_excess", "index_ret", "amount_ratio", "height"]
    theme_keys = ["breadth", "share", "height", "multi_share", "seal_rate", "break_known", "break_count_assigned", "early_share", "order_float"]
    manifest = []
    for case in selected_cases():
        date, theme = case["date"], case["theme"]
        i = dates.index(date); case_id = date + "_" + theme
        folder = OUT / case_id; folder.mkdir(exist_ok=True)
        timeline = []
        for j in range(i-3, i+6):
            r = records[j]
            t = next((t for t in r["themes"] if t["theme"] == theme), None)
            leaders = [l for l in r["leaders"] if l["theme"] == theme]
            codes = [l["ts_code"] for l in leaders]
            stock = kpl.loc[(kpl.trade_date == r["date"]) & kpl.ts_code.isin(codes) & (kpl.tag == "涨停")].copy()
            stock = stock.merge(pd.DataFrame(leaders)[["ts_code", "height"]] if leaders else pd.DataFrame(columns=["ts_code", "height"]), on="ts_code")
            price = day.loc[day.trade_date == r["date"], ["ts_code", "pre_close", "pct_chg", "close", "amount"]].rename(columns={"amount":"daily_amount_thousand_yuan"})
            stock = stock.merge(price, on="ts_code", suffixes=("_kpl", ""))
            stock = stock.merge(limits.loc[limits.trade_date == r["date"], ["ts_code", "up_limit"]], on="ts_code")
            stock["limit_regime"] = np.where(stock.up_limit/stock.pre_close-1 < .15, "10%", "20%")
            stock = stock.sort_values(["limit_regime", "height", "lu_time", "ts_code"], ascending=[True, False, True, True])
            keep = ["ts_code", "name", "height", "limit_regime", "lu_time", "last_time", "theme", "status", "limit_order", "lu_limit_order", "free_float", "amount", "daily_amount_thousand_yuan"]
            timeline.append({"date": r["date"], "offset": j-i,
                             "market": {k: r["market"][k] for k in market_keys},
                             "theme": {k: t[k] for k in theme_keys} if t else {"breadth":0},
                             "competitors": [{k:x[k] for k in ["theme", "breadth", "height"]} for x in sorted(r["themes"],key=lambda x:-x["breadth"])[:5]],
                             "limit_stocks": stock[keep].to_dict("records")})
        current = timeline[3]["limit_stocks"]
        pool = [s["ts_code"] for s in current]
        start_time = datetime.strptime(dates[i-3], "%Y%m%d").strftime("%Y-%m-%d") + " 00:00:00"
        end_time = datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d") + " 18:00:00"
        news = cache("news_"+date, "get_news", start_datetime=start_time, end_datetime=end_time, limit=None)
        assert news.empty or (news.datetime.ge(start_time).all() and news.datetime.le(end_time).all())
        candidates = news_candidates(news, theme, [s["name"] for s in current])
        save(folder / "news_candidates.json", candidates)
        next_morning = datetime.strptime(dates[i+1], "%Y%m%d").strftime("%Y-%m-%d")
        overnight = cache("overnight_"+date, "get_news", start_datetime=end_time,
                          end_datetime=next_morning+" 09:30:00", limit=None)
        overnight = overnight.loc[overnight.datetime > end_time].copy()
        overnight_candidates = news_candidates(overnight, theme, [s["name"] for s in current])
        for item in overnight_candidates:
            item["time_bucket"] = "次日06:00前" if item["datetime"] <= next_morning+" 06:00:00" else "次日06:00—09:30"
        save(folder / "overnight_candidates.json", overnight_candidates)
        concept = cache("concept_"+date, "get_kpl_concept_cons", trade_date=date)
        own_concept = concept.loc[concept.con_code.isin(pool)].copy() if not concept.empty else concept
        own_concept.to_csv(folder / "historical_company_links.csv", index=False, encoding="utf-8-sig")
        lp = cache("performance_"+date, "get_kpl_limit_performance", trade_date=date)
        own_lp = lp.loc[lp.ts_code.isin(pool)] if not lp.empty else lp
        save(folder / "limit_reasons.json", own_lp.to_dict("records"))
        observations = []
        for later in dates[i+1:i+3]:
            minute = cache("minute_"+case_id+"_"+later, "get_minute", trade_date=later, ts_codes=pool)
            observations.append(cohort_observation(pool, later, day, minute, limits))
        case.update({"mode":"retrospective_learning", "future_was_visible": True,
                     "news_start": start_time, "news_cutoff": end_time,
                     "news_source_counts": news.groupby("src").size().to_dict(),
                     "news_raw_count": len(news), "news_candidate_count": len(candidates),
                     "overnight_raw_count": len(overnight), "overnight_candidate_count": len(overnight_candidates),
                     "overnight_source_counts": overnight.groupby("src").size().to_dict(),
                     "news_available_assumption": "按datetime重建；缺历史首发存档，snap_ts晚于事件日；不保证未回填",
                     "kpl_available_assumption": "D最终榜假设次交易日06:00可用；非D盘中名单",
                     "prior20_widths": [{"date":r["date"], "width": next((t["breadth"] for t in r["themes"] if t["theme"]==theme),0)} for r in records[max(0,i-20):i]],
                     "cohort": pool, "timeline": timeline, "cohort_followup": observations})
        save(folder / "evidence_pack.json", case)
        manifest.append({k:case[k] for k in ["date", "theme", "width", "formed", "future5_strong_days", "news_raw_count", "news_candidate_count", "news_source_counts"]})
        print(case_id, "news", len(news), "candidates", len(candidates), "pool", len(pool), flush=True)
    save(OUT / "manifest.json", {"cases":manifest,"selection":"3时段×2事后结果组；每组取初始宽度最大者", "source_features_sha256":hashlib.sha256((SOURCE/"features.json").read_bytes()).hexdigest(), "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


if __name__ == "__main__":
    main()
