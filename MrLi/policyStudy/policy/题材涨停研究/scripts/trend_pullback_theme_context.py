"""Pure point-in-time KPL context for a daily/15-minute trend study.

No model annotations, current concept constituents, aliases, or outcome labels
are read. A KPL date is assumed available from the next market session; the
historical first publication of the stored KPL snapshot is NOT verified.
News matches identify dated factual leads, never establish causal attribution.
"""
from __future__ import annotations

from collections import defaultdict
import math
import re

import pandas as pd


MAX_ATTRIBUTION_AGE = 60
NEWS_LOOKBACK_DAYS = 7
CONTEXT_COLUMNS = [
    "ts_code", "trade_date", "context_as_of", "theme_name", "theme_known",
    "theme_recency_days", "theme_width", "theme_width5", "theme_height",
    "theme_heat", "theme_heat_change", "logic_evidence_known", "logic_title",
    "logic_source_time", "stock_theme_source_date", "kpl_day_known",
    "theme_conflict", "logic_source", "logic_match_kind", "logic_evidence_id",
    "logic_matches", "snapshot_first_publication_verified",
]

# Do not expand a theme with latest external members or hindsight synonyms.
EXCLUDED_THEME = re.compile(r"(?:ST|次新|新股)", re.I)
EXCLUDED_NAME = re.compile(r"(?:ST|退)", re.I)
PRICE_ONLY = re.compile(
    r"涨停|跌停|异动|快速(?:上涨|下跌|拉升)|(?:盘中|直线|集体|尾盘)"
    r"(?:拉升|跳水|大涨|走强|走低)|封板|连板|龙虎榜|领涨|领跌|"
    r"主力资金|资金流入|资金流出|换手率|创(?:阶段|历史)?新高|"
    r"(?:板块|概念|个股)(?:大涨|上涨|下跌|走强|走弱)")
FACT_EVENT = re.compile(
    r"政策|国务院|发改委|财政部|工信部|央行|证监会|关税|补贴|"
    r"减产|停产|复产|扩产|产能|供给|供应|产量|订单|合同|中标|"
    r"获批|批准|许可|注册|临床|试验|研发|技术|产品|上市申请|"
    r"业绩|净利润|营收|盈利|亏损|预告|报告|财报|融资|并购|收购|"
    r"投资|重组|合作|协议|采购|发布|需求|出口|进口|销售|价格|报价|"
    r"商业化|量产|投产|交付|制裁|禁令|规划|募资|债券|股权|"
    r"分红|增持|减持|回购|研发投入|利率|通胀|库存|税率|听证|"
    r"大会|会议|封关|发射|试飞|首飞|招标")


def _text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _date(value) -> str:
    text = _text(value)
    if re.fullmatch(r"\d{8}", text):
        return pd.to_datetime(text, format="%Y%m%d").strftime("%Y%m%d")
    return pd.Timestamp(text).strftime("%Y%m%d")


def board_height(status) -> int:
    """KPL reported board count: N连板=N; D天N板=N, not D."""
    status = _text(status)
    match = re.search(r"(\d+)连板", status)
    if not match:
        match = re.search(r"\d+天(\d+)板", status)
    if not match:
        match = re.fullmatch(r"(\d+)板", status)
    return max(1, int(match.group(1))) if match else 1


def _board_points(height: int) -> float:
    return {1: 1.0, 2: 2.5, 3: 5.0, 4: 8.0, 5: 12.0}.get(
        height, 12.0 + 4.0 * (height - 5))


def heat_score(heights: list[int], active_sessions5: int) -> float:
    """0..100 descriptive heat, NOT a probability or an economic target.

    Width 25 points, board ladder 35, maximum reported board count 15,
    five-session persistence 25. An empty current theme is zero, even if
    formerly active. Missing whole-market source dates are handled as unknown
    by the caller, rather than passed here as an empty theme.
    """
    if not heights:
        return 0.0
    return round(min(25.0, len(heights) * 2.5)
                 + min(35.0, sum(_board_points(h) for h in heights))
                 + min(15.0, max(heights) * 2.5)
                 + min(25.0, max(0, active_sessions5) * 5.0), 6)


def prepare_kpl(kpl: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    """Keep literal primary limit-up labels; remember coverage before exclusions."""
    if kpl is None or kpl.empty:
        return pd.DataFrame(columns=["trade_date", "ts_code", "name", "lu_desc", "status"]), set()
    missing = {"trade_date", "ts_code", "lu_desc"} - set(kpl.columns)
    if missing:
        raise ValueError(f"KPL missing columns: {sorted(missing)}")
    x = kpl.copy()
    x["trade_date"] = x.trade_date.map(_date)
    available = set(x.trade_date)
    for col in ("ts_code", "name", "lu_desc", "status"):
        if col not in x:
            x[col] = ""
        x[col] = x[col].map(_text)
    if "tag" in x:
        x = x[x.tag.map(_text).eq("涨停")]
    x = x[~x.name.str.contains(EXCLUDED_NAME)
          & ~x.lu_desc.str.contains(EXCLUDED_THEME)
          & x.lu_desc.ne("") & x.ts_code.ne("")
          & ~x.ts_code.str.endswith(".BJ")].copy()
    # Ambiguous same-day primary labels are retained for explicit conflict checks.
    x = x.drop_duplicates(["trade_date", "ts_code", "lu_desc", "status"])
    return x.sort_values(["trade_date", "ts_code", "lu_desc", "status"]), available


def prepare_news(news: pd.DataFrame | None) -> pd.DataFrame:
    """Accept only raw publication timestamps and factual, non-price-result leads."""
    columns = ["source_time", "title", "src", "md5", "content"]
    if news is None or news.empty:
        return pd.DataFrame(columns=columns)
    if "datetime" not in news or "title" not in news:
        raise ValueError("Raw news must supply datetime and title (no model event dates)")
    x = news.copy()
    # datetime in provider contract is Chinese local time. Convert explicit offsets
    # to that timezone; date-only rows cannot establish intraday availability.
    def timestamp(raw):
        text = _text(raw)
        if not re.search(r"\d{1,2}:\d{2}", text):
            return pd.NaT
        try:
            value = pd.Timestamp(text)
            if value.tzinfo is not None:
                value = value.tz_convert("Asia/Shanghai").tz_localize(None)
            return value
        except (ValueError, TypeError):
            return pd.NaT
    x["source_time"] = x.datetime.map(timestamp)
    for col in ("title", "src", "md5", "content"):
        if col not in x:
            x[col] = ""
        x[col] = x[col].map(_text)
    x = x[x.source_time.notna() & x.title.ne("")
          & ~x.title.str.contains(PRICE_ONLY)].copy()
    # A fact must occur in the title or early original body, not an LLM annotation.
    x = x[(x.title + " " + x.content.str.slice(0, 1500)).str.contains(FACT_EVENT)]
    x = x.drop_duplicates(["source_time", "title"]).sort_values(
        ["source_time", "title", "src"])
    return x[columns].reset_index(drop=True)


def _logic(news: pd.DataFrame, day: str, code: str, known_name: str,
           theme: str) -> dict:
    empty = {"logic_evidence_known": False, "logic_title": "",
             "logic_source_time": "", "logic_source": "", "logic_match_kind": "",
             "logic_evidence_id": "", "logic_matches": 0}
    if news.empty:
        return empty
    cutoff = pd.Timestamp(day + " 09:30:00")
    # prepare_news sorts timestamps. Binary bounds avoid rescanning a full-year
    # archive for every stock/day, while preserving an exclusive 09:30 cutoff.
    lo = news.source_time.searchsorted(cutoff - pd.Timedelta(days=NEWS_LOOKBACK_DAYS),
                                      side="left")
    hi = news.source_time.searchsorted(cutoff, side="left")
    x = news.iloc[lo:hi].copy()
    if x.empty:
        return empty
    # Code/name must be in original title to prevent loosely related full-body hits.
    stock = x.title.str.contains(re.escape(code[:6]), regex=True)
    if known_name and len(known_name) >= 2:
        stock |= x.title.str.contains(re.escape(known_name), regex=True)
    topic = x.title.str.contains(re.escape(theme), regex=True) if theme else False
    x["match_kind"] = "theme_literal"
    x.loc[stock, "match_kind"] = "stock_title"
    x = x[stock | topic]
    if x.empty:
        return empty
    # Stock-specific factual lead first, then most recent. Neither implies benefit.
    x["stock_priority"] = x.match_kind.eq("stock_title").astype(int)
    item = x.sort_values(["stock_priority", "source_time", "title"],
                         ascending=[False, False, True]).iloc[0]
    return {"logic_evidence_known": True, "logic_title": item.title,
            "logic_source_time": item.source_time.strftime("%Y-%m-%d %H:%M:%S"),
            "logic_source": item.src, "logic_match_kind": item.match_kind,
            "logic_evidence_id": item.md5, "logic_matches": len(x)}


def build_context(kpl: pd.DataFrame, calendar: list[str], codes: list[str],
                  news: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per selected stock/decision session; factual context never sees today.

    ``theme_known`` means a prior primary stock label exists within 60 market
    sessions; ``kpl_day_known`` independently marks prior-market-date coverage.
    Missing coverage sets numeric heat/breadth to NaN. Inactive themes on an
    observed source date get width/height/heat zero. Five-session distinct width
    and persistence-based heat require all five source dates to be observed.
    ``logic_evidence_known`` only means a dated factual raw-news match was found;
    this lexical screening cannot prove industrial exposure, sentiment, or cause.

    The calendar must include earlier sessions used to age stock attribution;
    records outside it are rejected to avoid silently treating age as zero.
    """
    days = [_date(d) for d in calendar]
    if days != sorted(set(days)):
        raise ValueError("calendar must be sorted, unique trading dates")
    selected = list(dict.fromkeys(str(c) for c in codes))
    if not days or not selected:
        return pd.DataFrame(columns=CONTEXT_COLUMNS)
    x, available = prepare_kpl(kpl)
    news_facts = prepare_news(news)
    day_index = {d: i for i, d in enumerate(days)}
    x = x[x.trade_date.isin(day_index)].copy()
    available &= set(day_index)
    grouped = {(d, theme): group for (d, theme), group
               in x.groupby(["trade_date", "lu_desc"], sort=False)}
    observations = defaultdict(dict)
    for (d, code), group in x[x.ts_code.isin(selected)].groupby(
            ["trade_date", "ts_code"], sort=False):
        labels = sorted(set(group.lu_desc))
        observations[d][code] = {"date": d, "name": group.name.iloc[0],
                                 "theme": labels[0] if len(labels) == 1 else "",
                                 "conflict": len(labels) > 1}
    latest = {}
    cache = {}

    def facts(theme: str, idx: int) -> dict:
        key = (theme, idx)
        if key in cache:
            return cache[key]
        unknown = {"theme_width": math.nan, "theme_width5": math.nan,
                   "theme_height": math.nan, "theme_heat": math.nan}
        if idx < 0 or days[idx] not in available:
            return unknown
        g = grouped.get((days[idx], theme))
        if g is None:
            heights = []
            width = 0
        else:
            # A stock contributes once even if duplicate/revised statuses coexist.
            per_stock = g.assign(height=g.status.map(board_height)).groupby("ts_code")
            heights = per_stock.height.max().astype(int).tolist()
            width = len(heights)
        window = days[max(0, idx - 4):idx + 1]
        complete5 = len(window) == 5 and all(d in available for d in window)
        members = set()
        active = 0
        for d in window:
            old = grouped.get((d, theme))
            if old is not None:
                members.update(old.ts_code)
                active += 1
        item = {"theme_width": width,
                "theme_width5": len(members) if complete5 else math.nan,
                "theme_height": max(heights, default=0),
                "theme_heat": heat_score(heights, active) if complete5 else math.nan}
        cache[key] = item
        return item

    output = []
    for i, day in enumerate(days):
        if i:
            latest.update(observations.get(days[i - 1], {}))
        prev = days[i - 1] if i else ""
        for code in selected:
            observed = latest.get(code)
            age = i - day_index[observed["date"]] if observed else None
            known = bool(observed and not observed["conflict"] and age <= MAX_ATTRIBUTION_AGE)
            theme = observed["theme"] if known else ""
            current = facts(theme, i - 1) if theme else {
                "theme_width": math.nan, "theme_width5": math.nan,
                "theme_height": math.nan, "theme_heat": math.nan}
            previous = facts(theme, i - 2) if theme else {"theme_heat": math.nan}
            change = current["theme_heat"] - previous["theme_heat"]
            output.append({"ts_code": code, "trade_date": day, "context_as_of": prev,
                "theme_name": theme, "theme_known": known,
                "theme_recency_days": age if observed else math.nan,
                **current, "theme_heat_change": change,
                "stock_theme_source_date": observed["date"] if observed else "",
                "kpl_day_known": prev in available,
                "theme_conflict": bool(observed and observed["conflict"]),
                "snapshot_first_publication_verified": False,
                **_logic(news_facts, day, code, observed["name"] if observed else "", theme)})
    result = pd.DataFrame(output, columns=CONTEXT_COLUMNS)
    result.attrs["provenance"] = {
        "KPL": "literal prior-session primary limit-up lu_desc, no future alias/member",
        "news": "raw original timestamps strictly before decision date 09:30 local",
        "logic": "factual lexical leads, not established causation or verified benefit",
        "heat": "25 width + 35 board ladder + 15 maximum boards + 25 persistence",
        "attribution_max_age_sessions": MAX_ATTRIBUTION_AGE,
        "news_lookback_calendar_days": NEWS_LOOKBACK_DAYS,
        "historical_first_publication_verified": False,
        "calendar_contract": "must contain prior sessions used for aging and rolling",
    }
    return result
