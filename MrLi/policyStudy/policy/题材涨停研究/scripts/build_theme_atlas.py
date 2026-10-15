"""Historical descriptive atlas. Outcomes here are hindsight, never prediction inputs.

Uses the immutable stage1 cache originally acquired via data_provider. Does not
change the v1 predictor, labels, weights, or results. All hypotheses are exploratory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "data/stage1_v1"
OUTPUT = BASE / "data/theme_atlas_v1"


def category(theme):
    if any(w in theme for w in ["季报", "年报", "中报", "业绩", "扭亏"]):
        return "业绩事件"
    if any(w in theme for w in ["重组", "收购", "实控人", "股权", "借壳", "资产注入"]):
        return "公司事件"
    return "行业或产业概念（待细分）"


def save_json(path, value):
    def default(v):
        if isinstance(v, (np.integer, np.floating, np.bool_)):
            return v.item()
        raise TypeError(type(v).__name__)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=default, allow_nan=False), encoding="utf-8")


def panel(records):
    dates = [r["date"] for r in records]
    themes = sorted({t["theme"] for r in records for t in r["themes"]})
    sparse = pd.DataFrame([dict(date=r["date"], **t) for r in records for t in r["themes"]])
    width = sparse.pivot(index="date", columns="theme", values="breadth").reindex(index=dates, columns=themes).fillna(0).astype(int)
    rank = width.rank(axis=1, method="min", ascending=False)
    ranks = rank.stack().rename("rank").reset_index()
    all_rows = width.stack().rename("breadth").reset_index().merge(ranks, on=["date", "theme"])
    all_rows["strong"] = (all_rows.breadth >= 5) & (all_rows["rank"] <= 3)
    all_rows["active"] = all_rows.breadth >= 5
    all_rows["category"] = all_rows.theme.map(category)
    return sparse, width, rank, all_rows


def stock_panel(records):
    stocks = pd.DataFrame([dict(date=r["date"], **l) for r in records for l in r["leaders"]])
    day = pd.read_pickle(SOURCE/"cache/day.pkl")
    limit = pd.read_pickle(SOURCE/"cache/limits.pkl")
    for f in [day, limit]:
        f["trade_date"] = f.trade_date.astype(str).str.replace("-", "")
    ref = day[["trade_date", "ts_code", "pre_close"]].merge(limit[["trade_date", "ts_code", "up_limit"]], on=["trade_date", "ts_code"])
    ref["regime"] = np.where(ref.up_limit/ref.pre_close-1 < .15, "10%", "20%")
    stocks = stocks.merge(ref[["trade_date", "ts_code", "regime"]], left_on=["date", "ts_code"], right_on=["trade_date", "ts_code"], how="left")
    assert len(stocks) == 11728 and stocks.regime.notna().all()
    return stocks


def representatives(stock, theme, dates):
    a = stock.loc[(stock.theme == theme) & stock.date.isin(dates)]
    result = []
    for regime, g in a.groupby("regime"):
        x = g.groupby(["ts_code", "name"]).agg(max_height=("height", "max"), up_days=("date", "nunique")).reset_index()
        x = x.loc[x.max_height >= 2].sort_values(["max_height", "up_days", "ts_code"], ascending=[False, False, True]).head(2)
        for row in x.to_dict("records"):
            result.append(dict(regime=regime, **row))
    return result


def intervals(width, rank, stocks):
    """Episodes start at width>=5, permit two weak sessions, end before third.

    The termination and representative stocks use later observations. Therefore
    these episodes are explicitly hindsight annotations, not causal state labels.
    """
    dates = width.index.tolist()
    episodes = []
    for theme in width:
        values = width[theme].to_numpy()
        active = np.flatnonzero(values >= 5)
        groups = []
        for i in active:
            if not groups or i-groups[-1][-1] > 3:
                groups.append([int(i)])
            else:
                groups[-1].append(int(i))
        for seq in groups:
            start, stop = seq[0], seq[-1]
            dates_here = dates[start:stop+1]
            strong = [i for i in seq if rank.iloc[i][theme] <= 3]
            peak = int(np.argmax(values[start:stop+1]))+start
            episodes.append({"theme": theme, "category": category(theme), "start": dates[start], "end": dates[stop],
                             "sessions": stop-start+1, "active_sessions": len(seq), "strong_sessions": len(strong),
                             "limit_up_stock_days": int(values[start:stop+1].sum()), "peak_width": int(values[peak]),
                             "peak_date": dates[peak], "left_censored": start < 3,
                             "right_censored": stop >= len(dates)-3,
                             "annotation": "持续强势段" if len(strong) >= 3 else "短强势/待确认段" if strong else "活跃未入前三段",
                             "height_representatives": representatives(stocks, theme, dates_here)})
    return sorted(episodes, key=lambda e: (e["start"], e["theme"]))


def exploratory(width, rank, records, stocks):
    dates = width.index.tolist()
    # Initiations must have three preceding sessions and five observable following
    # sessions, with no prior width>=5; do not choose only eventually successful waves.
    starts = []
    for theme in width:
        n = width[theme].to_numpy()
        for i in range(3, len(dates)-5):
            if n[i] >= 5 and max(n[i-3:i]) < 5:
                current = stocks.loc[(stocks.date == dates[i]) & (stocks.theme == theme)]
                strong_future = sum(n[j] >= 5 and rank.iloc[j][theme] <= 3 for j in range(i+1, i+6))
                starts.append({"date": dates[i], "theme": theme, "width": int(n[i]),
                               "current_top3": bool(rank.iloc[i][theme] <= 3),
                               "has_second_board": bool((current.height >= 2).any()),
                               "future5_strong_days": int(strong_future), "formed": bool(strong_future >= 2)})
    # One divergence per theme per three trading sessions. Core persistence means
    # yesterday's highest-board stock still closes at limit, not merely positive.
    divergences = []
    pools = {(date, theme): g for (date, theme), g in stocks.groupby(["date", "theme"])}
    for theme in width:
        n = width[theme].to_numpy(); last = -99
        for i in range(1, len(dates)-2):
            if n[i-1] >= 5 and n[i] <= .6*n[i-1] and i-last >= 3:
                prior = pools[(dates[i-1], theme)]
                old = set(prior.loc[prior.height == prior.height.max(), "ts_code"])
                now = pools.get((dates[i], theme), pd.DataFrame(columns=["ts_code"]))
                persists = bool(old & set(now.ts_code))
                recovered = max(n[i+1:i+3]) >= .8*n[i-1]
                divergences.append({"date": dates[i], "theme": theme, "prior_width": int(n[i-1]),
                                    "current_width": int(n[i]), "previous_height": int(prior.height.max()),
                                    "old_height_core_still_limit": persists,
                                    "future2_repaired": bool(recovered), "next2_peak_width": int(max(n[i+1:i+3]))})
                last = i
    market = pd.DataFrame([dict(date=r["date"], **r["market"]) for r in records]).set_index("date")
    market["active_theme_count"] = (width >= 5).sum(axis=1)
    shares = width.div(width.sum(axis=1), axis=0)
    market["hhi"] = shares.pow(2).sum(axis=1)
    market["liquidity_group"] = np.select([market.amount_ratio < .85, market.amount_ratio > 1.15], ["低于过去20日均值85%", "高于过去20日均值115%"], default="中间区间")
    # The first 20 research sessions have a partial baseline; exclude them here.
    liquidity = []
    for name, g in market.iloc[20:].groupby("liquidity_group"):
        liquidity.append({"group": name, "n": len(g), "median_active_themes": float(g.active_theme_count.median()),
                          "median_hhi": float(g.hhi.median()), "median_limit_ups": float(g.up_count.median())})
    def grouped(rows, feature, outcome):
        result = []
        for value in [False, True]:
            group = [r for r in rows if r[feature] == value]
            hit = sum(r[outcome] for r in group)
            result.append({feature: value, "n": len(group), "hits": hit, "rate": hit/len(group) if group else None})
        return result
    findings = {"scope": "Retrospective exploratory associations; not out-of-sample accuracy or causality.",
                "initiation_second_board": grouped(starts, "has_second_board", "formed"),
                "initiation_top3": grouped(starts, "current_top3", "formed"),
                "divergence_core": grouped(divergences, "old_height_core_still_limit", "future2_repaired"),
                "liquidity": liquidity}
    return starts, divergences, findings, market


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = json.loads((SOURCE/"features.json").read_text())
    sparse, width, rank, all_rows = panel(records)
    stocks = stock_panel(records)
    episodes = intervals(width, rank, stocks)
    starts, divergences, findings, market = exploratory(width, rank, records, stocks)
    monthly, blocks, daily = [], [], []
    dates = width.index.tolist()
    for month in sorted({d[:6] for d in dates}):
        subset = [d for d in dates if d.startswith(month)]
        total = int(width.loc[subset].to_numpy().sum())
        for theme, count in width.loc[subset].sum().sort_values(ascending=False).items():
            if count:
                monthly.append({"month": month, "theme": theme, "category": category(theme), "stock_days": int(count),
                                "sessions": len(subset), "daily_average": float(count/len(subset)),
                                "share": float(count/total), "strong_days": int(((rank.loc[subset, theme] <= 3)&(width.loc[subset, theme] >= 5)).sum()),
                                "height_representatives": representatives(stocks, theme, subset)})
    for i in range(0, len(dates), 5):
        subset = dates[i:i+5]
        top = width.loc[subset].sum().sort_values(ascending=False).head(3)
        blocks.append({"start": subset[0], "end": subset[-1], "sessions": len(subset),
                       "top3": [{"theme": t, "stock_days": int(n)} for t, n in top.items()]})
    for date in dates:
        w = width.loc[date]
        top = w.sort_values(ascending=False).head(3)
        r = next(r for r in records if r["date"] == date)
        daily.append({"date": date, "limit_up_count": int(w.sum()), "market_breadth": r["market"]["breadth"],
                      "amount_ratio": r["market"]["amount_ratio"], "top3": [{"theme": t, "width": int(n)} for t, n in top.items()],
                      "strong_themes": [t for t in width if width.loc[date,t] >= 5 and rank.loc[date,t] <= 3]})
    assert int(width.to_numpy().sum()) == len(stocks) == 11728
    assert sum(m["stock_days"] for m in monthly) == 11728
    assert len(daily) == 160 and len(blocks) == 32
    all_rows.to_csv(OUTPUT/"daily_theme_panel.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(starts).to_csv(OUTPUT/"all_initiation_events.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(divergences).to_csv(OUTPUT/"all_divergence_events.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(monthly).drop(columns="height_representatives").to_csv(OUTPUT/"monthly_summary.csv", index=False, encoding="utf-8-sig")
    save_json(OUTPUT/"episodes.json", episodes)
    save_json(OUTPUT/"monthly_summary.json", monthly)
    save_json(OUTPUT/"five_session_rotation.json", blocks)
    save_json(OUTPUT/"daily_map.json", daily)
    save_json(OUTPUT/"exploratory_findings.json", findings)
    save_json(OUTPUT/"manifest.json", {"source_features_sha256": hashlib.sha256((SOURCE/"features.json").read_bytes()).hexdigest(),
                                     "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                     "rows": len(all_rows), "themes": len(width.columns), "sessions": len(width),
                                     "stock_days": len(stocks), "episodes": len(episodes),
                                     "regime_split": stocks.groupby("regime").size().to_dict(),
                                     "interpretation": "KPL primary-attribution atlas; industry/event labels not yet semantically adjudicated; not true-mainline labels."})
    # Compact data for an inline exploration without exposing raw articles.
    top_themes = width.sum().sort_values(ascending=False).head(16).index.tolist()
    visual = {"months": sorted({d[:6] for d in dates}), "themes": top_themes,
              "monthly": [{k:v for k,v in m.items() if k!='height_representatives'} for m in monthly if m['theme'] in top_themes],
              "blocks": blocks, "daily": daily}
    save_json(OUTPUT/"visual_data.json", visual)
    report(monthly, blocks, episodes, starts, divergences, findings)
    print(json.dumps({"monthly_top3": [m for month in visual['months'] for m in [dict(month=month,top3=[r['theme'] for r in monthly if r['month']==month][:3])]], "findings": findings, "episode_count":len(episodes)}, ensure_ascii=False, indent=2))


def report(monthly, blocks, episodes, starts, divergences, findings):
    lines = ["# 2026年1—8月题材强势与轮动图谱：第一层事实梳理", "",
             "本图谱先回答发生了什么，再提出待验证假设。它是后视镜研究，不是新一轮预测准确率。原v1预测与成绩没有改动。", "",
             "覆盖160个交易日、229个KPL主归因题材、11728个非ST沪深合格涨停股日。只将芯片/机器人概念/智能电网统一为半导体/机器人/电网；没有把人工智能、AI应用、算力、通信事后合并成一个大主题。", "",
             "股日=同一股票每天涨停计一次，不是独立股票数。月度排名按累计股日，另报日均和份额，避免2月交易日较少造成误解。业绩/公司事件单列类型；宽行业仍须进一步细分到当期催化。", "",
             "## 月度强势方向", "", "| 月份 | 交易日 | 前三方向（涨停股日；占当月比例） |", "|---|---:|---|"]
    for month in sorted({m['month'] for m in monthly}):
        rows = [m for m in monthly if m['month']==month][:3]
        lines.append(f"| {month} | {rows[0]['sessions']} | "+"；".join(f"{r['theme']} {r['stock_days']}（{r['share']:.1%}）" for r in rows)+" |")
    lines += ["", "月度总量可能由数个不连续行情段构成，不能直接给整个月贴唯一主线标签。以下再拆成连续5交易日截面；同数值并列，展示次序不表示更强。", "",
              "## 每5个交易日的变化", "", "| 区间 | 第1宽度方向 | 第2 | 第3 |", "|---|---|---|---|"]
    for b in blocks:
        lines.append(f"| {b['start']}—{b['end']} | "+" | ".join(f"{r['theme']} {r['stock_days']}" for r in b['top3'])+" |")
    lines += ["", "## 阶段案例索引", "", "强势日暂定义为当日涨停>=5只且宽度排名前三，平手同时保留。活跃段由涨停>=5只的日期连接，允许中间最多2个弱日；超过2日则分段。这是可复核的描述规则，不是已经确认的题材生命周期。期初/期末截断保留标记。", "",
              "| 题材 | 活跃段 | 强势日/段内交易日 | 峰值宽度 | 高度代表股（事后统计，非龙头真值） |", "|---|---|---|---|---|"]
    for e in sorted(episodes,key=lambda e:e['limit_up_stock_days'],reverse=True)[:24]:
        reps="；".join(f"{r['name']} {r['ts_code']} {r['regime']}组/{r['max_height']}板" for r in e['height_representatives']) or "尚无连续2板代表"
        trunc="（边界截断）" if e['left_censored'] or e['right_censored'] else ""
        lines.append(f"| {e['theme']} | {e['start']}—{e['end']}{trunc} | {e['strong_sessions']}/{e['sessions']} | {e['peak_width']} | {reps} |")
    lines += ["", "宽行业的长活跃段可能包含多次催化、分支切换和核心更替。例如半导体4月7日—7月10日的65个交易日，不能据此认定为同一轮生命周期；需要结合当时新闻拆解。", "",
              "## 三个先行检验：保留支持和反例", "", "以下阈值只是本轮探索定义，未用独立时段验证；同一市场日事件相关，也未控制题材类型、初始宽度或市场环境，不能作因果或稳定胜率解释。", "",
              "### 启动后能否形成持续强势", "", "启动事件：今日宽度>=5、此前3日均<5；后续完整5日里至少2日成为强势日，记为形成持续强势。所有合格事件都入表，失败不删除。", "",
              "| 启动当日结构 | 后续形成/样本 | 比例 |", "|---|---|---|"]
    for r in findings['initiation_second_board']:
        label='有连续2板及以上' if r['has_second_board'] else '全部首板'
        lines.append(f"| {label} | {r['hits']}/{r['n']} | {r['rate']:.1%} |")
    lines += ["", "### 分歧后是否修复", "", "分歧事件：昨日宽度>=5，今日下降至少40%；同题材事件间隔至少3交易日。修复定义为未来2日宽度至少一次回到分歧前80%。核心保持只是昨日最高板候选仍封涨停，不能等同于盘中有承接；10%/20%混合的该项先作探索。", "",
              "| 昨日高度核心 | 两日修复/样本 | 比例 |", "|---|---|---|"]
    for r in findings['divergence_core']:
        lines.append(f"| {'至少一只仍涨停' if r['old_height_core_still_limit'] else '没有仍涨停'} | {r['hits']}/{r['n']} | {r['rate']:.1%} |")
    lines += ["", "### 成交活跃程度与题材共存", "", "跳过前20日不完整基准。活跃题材指当日至少5只涨停；集中度HHI越高，涨停份额越集中。比较只描述同时变化，不证明资金流向。", "",
              "| 成交额/过去20日均值 | 天数 | 活跃题材数中位数 | HHI中位数 | 涨停数中位数 |", "|---|---|---|---|---|"]
    for r in findings['liquidity']:
        lines.append(f"| {r['group']} | {r['n']} | {r['median_active_themes']:.1f} | {r['median_hhi']:.3f} | {r['median_limit_ups']:.1f} |")
    lines += ["", "## 如何继续学", "",
              "1. 在逐日和分段图谱上人工校正产业事件：原始题材名、催化、受益链、成员、主题层级都保留版本。新闻标题匹配只能提供候选证据，不能直接认定原因。",
              "2. 同时建立成功启动/失败启动、分歧修复/修复失败、二波/单纯反弹的对照案例。给每段补齐前3日、事件日及后5日，并标注哪条证据在当时已经可见。",
              "3. 龙头先标角色与带动性，再另列收益冠军；用日线确认阶段，用分钟顺序检查谁先转强及成员是否响应。",
              "4. 每条规律写成可证伪的命题，列全部样本、反例、适用情绪与题材范围，再按时间滚动遮住未来验证。八个月已用于学习，不能当作全新未见样本。", "",
              "## 下一层研究的固定问题", "",
              "每个行情段都按市场背景→具体催化与受益链→启动结构→首次分歧→修复或退潮→核心更替的顺序整理。情绪记录指数、市场涨跌分布、昨日涨停溢价与晋级、失败个股亏损强度，避免只看涨停数量。",
              "轮动交接先记录时间顺序和相对份额：旧题材变弱、新题材变强、旧核心与新核心先后变化、同日市场成交额和涨跌广度。价格与成交的共变不能直接证明资金从旧题材流入新题材。",
              "龙头标注至少区分高度核心、先启动核心、容量核心与补涨。10%和20%组分别比较连板高度，并用分钟数据检查带动时序；缺少完整题材非涨停成员和真实竞价数据时，保留无法确认。",
              "规律的记录格式固定为：适用环境、当时可见条件、预期观察窗口、确认现象、失效条件、支持案例、反例、待验证状态。没有反例检验的经验只进入假设库。",
              "预测环节分别评估当前状态识别、下一阶段方向、候选覆盖及龙头角色。先冻结判断再揭晓后续；完成1—8月研究后，另留真正未参与调规则的时间段作正式验证。", "",
              "## 文件", "",
              "- daily_theme_panel.csv：160日×229题材的完整宽度面板，缺席日为0涨停，不代表无成交。",
              "- monthly_summary.json/csv、five_session_rotation.json、daily_map.json：月度/5日/逐日三层事实。",
              "- episodes.json：全部活跃段，含边界截断与10%/20%高度代表。",
              "- all_initiation_events.csv、all_divergence_events.csv：所有满足定义的正反例。",
              "- exploratory_findings.json、manifest.json：探索结果与来源指纹。", ""]
    (OUTPUT/"题材轮动图谱与研究路线.md").write_text("\n".join(lines),encoding="utf-8")


if __name__ == '__main__':
    main()
