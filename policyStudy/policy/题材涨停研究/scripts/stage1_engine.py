"""Causal, interpretable features. This module never receives outcome labels."""
from __future__ import annotations

import re
import numpy as np
import pandas as pd

ALIASES = {"机器人概念": "机器人", "智能电网": "电网", "芯片": "半导体"}
KEYWORDS = {
    "半导体": ["半导体", "芯片", "集成电路"], "机器人": ["机器人", "人形机器人"],
    "算力": ["算力", "数据中心"], "商业航天": ["商业航天", "卫星", "火箭"],
    "电网": ["电网", "变压器"], "通信": ["通信", "光模块", "光纤"],
    "人工智能": ["人工智能", "大模型"], "AI应用": ["AI应用", "人工智能", "大模型"],
}
EMOTION_FEATURES = ["breadth", "median_ret", "up_count", "down_count", "seal_rate",
                    "promotion", "prev_up_excess", "index_ret", "amount_ratio", "height"]
THEME_FEATURES = ["breadth", "share", "height", "multi_share", "breadth_change",
                  "persistence", "seal_rate", "break_known", "order_float", "early_share",
                  "news_hits", "market_score", "amount_ratio"]
LEADER_FEATURES = ["height", "early", "order_float", "seal_decay", "momentum5",
                   "ma20_distance", "turnover", "log_free_mv", "reason_relevance",
                   "theme_breadth", "one_price"]


def canonical(value):
    value = str(value or "").strip()
    return ALIASES.get(value, value)


def number(value, default=0.0):
    try:
        n = float(value)
        return n if np.isfinite(n) else default
    except (TypeError, ValueError):
        return default


def parse_time(value):
    s = str(value)
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", s):
        return np.nan
    h, m, sec = map(int, s.split(":"))
    # Do not silently repair KPL LP UTC-looking times such as 01:25.
    return h * 60 + m + sec / 60 if 9 <= h <= 15 else np.nan


def eligible_market(day, limits):
    """Point-in-time price-limit regime, never today's listing-status snapshot."""
    frame = day.merge(limits[["ts_code", "up_limit", "down_limit"]], on="ts_code", how="left")
    ratio = frame.up_limit / frame.pre_close - 1
    ok = (ratio.between(.075, .125) | ratio.between(.175, .225))
    ok &= ~frame.ts_code.str.endswith(".BJ") & (frame.pre_close > 0)
    return frame.loc[ok].copy()


def chain_features(day):
    """Backward-only return index; avoids latest-date adjusted price anchoring."""
    x = day.sort_values(["ts_code", "trade_date"]).copy()
    x["return_index"] = (1 + x.pct_chg / 100).groupby(x.ts_code).cumprod()
    g = x.groupby("ts_code", sort=False).return_index
    x["momentum5"] = g.pct_change(5) * 100
    x["ma20_distance"] = (x.return_index / g.transform(lambda s: s.rolling(20, min_periods=10).mean()) - 1) * 100
    return x


def market_state(market, ups, breaks, previous_ups, previous_close, history, index):
    lookup = market.set_index("ts_code")
    prev = lookup.reindex(previous_ups).pct_chg.dropna()
    idx = index.set_index("ts_code")
    index_ret = number(idx.loc["000300.SH", "pct_chg"]) if "000300.SH" in idx.index else number(index.pct_chg.mean())
    amount = number(market.amount.sum()) * 1000  # Tushare daily amount is CNY thousands.
    past_amount = [r["amount"] for r in history[-20:]]
    amount_ratio = amount / np.mean(past_amount) if past_amount else 1.
    f = {
        "breadth": number((market.pct_chg > 0).mean()), "median_ret": number(market.pct_chg.median()),
        "up_count": len(ups), "down_count": int((market.close <= market.down_limit + .011).sum()),
        "seal_rate": len(ups) / max(1, len(ups) + len(breaks)),
        "promotion": len(set(ups.ts_code) & set(previous_ups)) / max(1, len(previous_ups)),
        "prev_up_excess": number(prev.median()) - index_ret if len(prev) else 0.,
        "index_ret": index_ret, "amount_ratio": amount_ratio,
        "height": number(ups.height.max()), "amount": amount,
        "prior_up_coverage": len(prev) / max(1, len(previous_ups)),
    }
    # Fixed diagnostic score, not a fitted probability.
    f["score"] = float(np.clip(35*f["breadth"] + 20*f["seal_rate"] + 20*f["promotion"]
                              + 10*np.clip(f["prev_up_excess"] / 3 + .5, 0, 1)
                              + 15*np.clip(f["up_count"] / 100, 0, 1)
                              - min(25, 2*f["down_count"]), 0, 100))
    if f["down_count"] >= 15 or (f["breadth"] < .25 and f["index_ret"] < -1):
        state = "退潮/恐慌"
    elif f["score"] >= 70 and f["breadth"] >= .65:
        state = "亢奋/高潮"
    elif f["score"] >= 55:
        state = "升温"
    elif f["score"] < 35:
        state = "低迷"
    else:
        state = "混沌/分歧"
    f["state"] = state
    f["baseline_prediction"] = "hot" if f["score"] >= 65 else "cold" if f["score"] < 45 else "neutral"
    return f


def news_evidence(news, theme):
    words = KEYWORDS.get(theme, [theme])
    text = news.title.fillna("") + " " + news.content.fillna("")
    matches = news.loc[text.str.contains("|".join(map(re.escape, words)), regex=True)].copy()
    # Repeated releases and price recap headlines are not independent catalysts.
    matches = matches.drop_duplicates(subset=["content"])
    recap = (matches.title.fillna("") + matches.content.fillna("")).str.contains("涨停|复盘|涨幅居前|板块拉升|板块异动")
    matches = matches.loc[~recap]
    evidence = []
    for r in matches.sort_values("datetime").head(3).to_dict("records"):
        evidence.append({"datetime": r["datetime"], "src": r["src"], "md5": r["md5"],
                         "excerpt": str(r.get("title") or r.get("content") or "")[:160],
                         "type": "keyword_match_requires_semantic_review"})
    return min(len(matches), 10), evidence


def assign_breaks(breaks, themes, prior_memberships, ordinal):
    result = {theme: [] for theme in themes}
    unknown = []
    for r in breaks.to_dict("records"):
        tokens = {canonical(t) for t in re.split("[、,+，]", str(r.get("theme", "")))}
        matched = tokens & set(themes)
        provenance = "same_day_concept_exact"
        if len(matched) != 1:
            prior = prior_memberships.get(r["ts_code"])
            if prior and ordinal-prior[1] <= 20 and prior[0] in themes:
                matched = {prior[0]}
                provenance = "prior_primary_within_20_sessions"
        if len(matched) == 1:
            result[next(iter(matched))].append({"ts_code": r["ts_code"], "basis": provenance})
        else:
            unknown.append(r["ts_code"])
    return result, unknown


def theme_and_leader_features(ups, breaks, basic, daily, news, market, past_themes, memberships, ordinal):
    themes = sorted(ups.primary.unique())
    assigned, unknown = assign_breaks(breaks, themes, memberships, ordinal)
    b = basic.set_index("ts_code")
    d = daily.set_index("ts_code")
    result, leaders = [], []
    for theme, group in ups.groupby("primary"):
        past = past_themes.get(theme, [])
        last = past[-1] if past and past[-1]["ordinal"] == ordinal-1 else None
        recent = [r for r in past if ordinal-r["ordinal"] <= 5]
        older = [r for r in past if 2 <= ordinal-r["ordinal"] <= 20]
        width = len(group)
        times = group.lu_time.map(parse_time)
        order = pd.to_numeric(group.limit_order, errors="coerce")
        free = pd.to_numeric(group.free_float, errors="coerce").replace(0, np.nan)
        news_hits, evidence = news_evidence(news, theme)
        own_breaks = len(assigned[theme])
        f = {
            "theme": theme, "breadth": width, "share": width/max(1, len(ups)),
            "height": int(group.height.max()), "multi_share": number((group.height >= 2).mean()),
            "breadth_change": (width - (last["breadth"] if last else 0))/max(2, last["breadth"] if last else 2),
            "persistence": len(recent)/5, "seal_rate": width/(width+own_breaks),
            "break_known": 1 - len(unknown)/max(1, len(breaks)),
            "order_float": number((order/free).clip(0, 1).median()),
            "early_share": number((times <= 600).mean()), "news_hits": news_hits,
            "market_score": market["score"], "amount_ratio": market["amount_ratio"],
            "break_count_assigned": own_breaks, "break_evidence": assigned[theme],
            "news_evidence": evidence, "ordinal": ordinal,
        }
        # A category is not a confirmed mainline just because it has a high-board stock.
        f["rule_score"] = float(25*min(width/10, 1) + 15*min(f["height"]/5, 1)
                                + 15*min(max(f["breadth_change"], 0)/2, 1) + 15*f["persistence"]
                                + 10*f["seal_rate"] + 10*min(f["order_float"]/.02, 1)
                                + 5*f["early_share"] + 5*min(news_hits/3, 1))
        old_peak = max([r["breadth"] for r in older], default=0)
        if width <= 2 and old_peak >= 6:
            phase = "退潮"
        elif width >= 8 and f["share"] >= .18 and f["early_share"] >= .6:
            phase = "高潮候选"
        elif last and (width < last["breadth"]*.65 or own_breaks/(width+own_breaks) >= .35):
            phase = "分歧"
        elif last and last["phase"] in ["分歧", "退潮"] and width >= max(3, last["breadth"]*1.5) and f["seal_rate"] >= .7:
            phase = "分歧转一致"
        elif old_peak >= 4 and width >= 3 and (not last or last["breadth"] <= old_peak*.5):
            phase = "二波候选"
        elif width >= 3 and (not recent or max(r["breadth"] for r in recent) <= 2):
            phase = "启动候选"
        elif width >= 3 and f["persistence"] >= .4:
            phase = "延续"
        else:
            phase = "萌芽/零散"
        f["phase"] = phase
        result.append(f)
        for row in group.to_dict("records"):
            code = row["ts_code"]
            br = b.loc[code] if code in b.index else {}
            dr = d.loc[code] if code in d.index else {}
            t = parse_time(row["lu_time"])
            free_mv = number(br.get("free_share"), np.nan) * 10000 * number(dr.get("close"), np.nan)
            cap = free_mv if np.isfinite(free_mv) and free_mv > 0 else number(row["free_float"], np.nan)
            maxorder = number(row["lu_limit_order"])
            lf = {
                "ts_code": code, "name": row["name"], "theme": theme, "height": row["height"],
                "early": max(0, min(1, (900-t)/330)) if np.isfinite(t) else np.nan,
                "order_float": min(number(row["limit_order"])/cap, 1) if cap > 0 else np.nan,
                "seal_decay": number(row["limit_order"])/maxorder if maxorder > 0 else np.nan,
                "momentum5": number(dr.get("momentum5"), np.nan),
                "ma20_distance": number(dr.get("ma20_distance"), np.nan),
                "turnover": number(br.get("turnover_rate_f"), np.nan),
                "log_free_mv": np.log10(cap) if cap > 0 else np.nan,
                "reason_relevance": float(any(w in str(row["theme"]) for w in KEYWORDS.get(theme, [theme]))),
                "theme_breadth": width, "one_price": float(number(dr.get("high")) == number(dr.get("low"))),
            }
            lf["rule_score"] = (35*min(lf["height"]/5, 1) + 20*number(lf["early"])
                                + 20*min(number(lf["order_float"])/.03, 1)
                                + 10*number(lf["seal_decay"]) + 10*lf["reason_relevance"]
                                + 5*min(max(number(lf["momentum5"]), 0)/30, 1))
            leaders.append(lf)
    return result, leaders, unknown


def intraday_rank(previous_themes, previous_leaders, minutes, previous_close, cutoff):
    """Only yesterday's explicitly frozen basket and observations <= cutoff."""
    visible = minutes.loc[minutes.datetime.astype(str).str[11:16] <= cutoff].copy()
    visible = visible.sort_values("datetime")
    rows = []
    for t in previous_themes:
        pool = [r for r in previous_leaders if r["theme"] == t["theme"]]
        codes = [r["ts_code"] for r in pool]
        prices = visible.loc[visible.ts_code.isin(codes)]
        # The last point must actually be at the requested minute; no stale quote fill.
        first = prices.groupby("ts_code").first()
        last = prices.groupby("ts_code").last()
        last = last.loc[last.datetime.astype(str).str[11:16] == cutoff]
        valid = [c for c in last.index if number(previous_close.get(c)) > 0]
        coverage = len(valid)/max(1, len(codes))
        ret = pd.Series({c: (last.loc[c, "price"]/previous_close[c]-1)*100 for c in valid}, dtype=float)
        strengthening = pd.Series({c: (last.loc[c,"price"]/first.loc[c,"price"]-1)*100 for c in valid}, dtype=float)
        good = coverage >= .8 and len(valid) >= 2
        r = {"theme": t["theme"], "coverage": coverage, "eligible": bool(good), "expected": len(codes),
             "observed": len(valid), "median_ret": number(ret.median(), np.nan),
             "positive_share": number((ret > 0).mean(), np.nan),
             "median_from_first_point": number(strengthening.median(), np.nan),
             "score": None, "leader": None, "signal": "数据不足"}
        if good:
            r["score"] = .25*t["rule_score"] + 5*r["median_ret"] + 10*r["positive_share"] + 2*r["median_from_first_point"]
            # Only observed price strengthening; no queue/real fill inference.
            ls = {p["ts_code"]: .2*p["rule_score"] + 5*ret[p["ts_code"]] + 2*strengthening[p["ts_code"]]
                  for p in pool if p["ts_code"] in valid}
            r["leader"] = max(ls, key=lambda code: (ls[code], code))
            weak_to_strong = r["median_from_first_point"] > 1 and r["positive_share"] >= .65
            r["signal"] = "分歧修复候选" if weak_to_strong and t["phase"] in ["分歧", "退潮"] else "集体走强" if weak_to_strong else "走弱" if r["median_ret"] < -1 else "待确认"
        rows.append(r)
    return sorted(rows, key=lambda r: r["score"] if r["score"] is not None else -1e9, reverse=True)
