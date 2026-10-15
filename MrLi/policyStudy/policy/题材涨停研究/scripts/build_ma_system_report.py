"""Publish verified run facts and outstanding research gaps without an alpha claim."""
from __future__ import annotations

from collections import Counter
import html
import json
from pathlib import Path

import pandas as pd

from review_ma_system_pullback import ROOT, write_json, digest


def md_table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"]*len(headers)) + " |"] +
                     ["| " + " | ".join(str(v) for v in row) + " |" for row in rows])


def htable(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{html.escape(str(v))}</th>' for v in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def main():
    root = ROOT / "outputs/ma_system_pullback_20261005_v2"
    coverage = pd.read_csv(root / "grid/coverage.csv")
    configs = pd.read_csv(root / "grid/config_summary.csv")
    summaries = json.loads((root / "replay/summaries.json").read_text())
    themes = {r["id"]: r for r in json.loads((root / "theme/audit_v2/summary.json").read_text())}
    summary = coverage.groupby(["case", "name"]).agg(watch=("active_watch_bars", "max"),
                                                    retest=("retest", "max"),
                                                    daily=("daily_confirm", "max"),
                                                    entry=("technical_candidate", "max")).reset_index()
    reasons = {"688627.SH_202604": "日线六条均线没有完整多头排列，候选资格未建立",
               "002810.SZ_202608": "日线六条均线没有完整多头排列；当月无完整15分钟空头排列",
               "600371.SH_202608": "日线六条均线没有完整多头排列；当月无完整15分钟空头排列",
               "300759.SZ_202608": "出现反转/回踩证据时，回调开始之前尚无有效日线资格"}
    case_rows = [[r["name"], r["case"][-6:], r["watch"], r["retest"],
                  "有" if r["entry"] else "无", reasons.get(r["case"], "存在技术候选，题材正式门槛未验证")]
                 for r in summary.to_dict("records")]
    candidate_rows = []
    events = Counter()
    for s in summaries:
        if not s["allow_rebuy"]:
            continue
        t = themes[s["id"]]
        no = next(r for r in summaries if r["id"] == s["id"] and not r["allow_rebuy"])
        candidate_rows.append([s["id"], s["name"], s["signal_time"][5:16],
                               s["episode_age_at_entry_bars"], s["armed_wait_bars"],
                               f"{s['return_on_initial_outlay_pct']:+.2f}%", f"{no['return_on_initial_outlay_pct']:+.2f}%",
                               "清仓" if s["status"] == "closed" else "未结束，含浮盈", s["rebuys"],
                               "强" if t["peer_strength_proxy"] else "弱", "是" if t["sector_pullback_resonance_proxy"] else "否"])
        for e in json.loads((root / "replay" / s["id"] / "rebuy_ledger.json").read_text()):
            events.update(e.get("events", []))
    ages = {s["id"]: s["episode_age_at_entry_bars"] for s in summaries}
    freshness = [{"max_episode_age_bars": age,
                  "retained_distinct_times": sum(v <= age for v in ages.values()),
                  "ids": [ident for ident, value in ages.items() if value <= age]}
                 for age in (64, 128, 256)]
    write_json(root / "episode_age_sensitivity.json", freshness)
    counts = dict(grid_scanner=len(json.loads((root / "grid/scanner_checks.json").read_text())),
                  execution_prefix=len(json.loads((root / "replay/prefix_checks.json").read_text())),
                  peer_prefix=len(json.loads((root / "theme/audit_v2/prefix_checks.json").read_text())))
    headers = ["编号", "股票", "候选时间", "回调起点距买点/根", "启动后等待/根", "允许买回", "禁止买回", "期末状态", "买回笔数", "原成员转强代理", "回调共振代理"]
    report = f'''# 日线与15分钟均线回调系统：开发样本与成交诊断 v2

截至2026年10月5日，本轮已完成规则程序、1,728组覆盖扫描、9个不同候选时点的18次条件成交回放、历史原成员题材核验和逐笔图表。**完整策略验证尚未完成：样本买点含义、题材核心身份和独立样本表现仍未解决。** 上一版只到规则诊断，不能作为完整研究交付。

## 数据与时点

用户确认样本年份为2026。近岸蛋白9月、精智达4月/6月、京东方6月、山东赫达/万向德农/康龙化成8月，共7个股票月份，合计2,352根有效样本月15分钟K线。雷科防务2026年12月在研究时点尚未发生，保留待确认，没有替换为2025年。

行情经本地 `coreClient.data_provider` 读取；接受 `source` 的接口固定 `database_only`，分钟接口本身只读本地。日线资格使用前一完整交易日，资格建立后在MA30保持上升的回调阶段保留；当天日线反转只用截至信号的15分钟聚合。指标价格为原始价格乘当日复权因子，成交现金使用原始价格。日线约10个月暖身覆盖MA120，这不是750根日线的长期结构细读。

## 覆盖扫描做了什么

扫描日线排列解释2种、MA30上升天数5/10/20、紧凑金叉4/8/16根、恒价预测2/4/6/8根、再回踩等待8/16/32/64根、回踩容差1%/2%、抵扣下降解释3种。每日支撑距离3%、日线反转确认必需、完整15分钟MA10/20/30/60/120空头排列等门槛保持固定。没有使用收益选择参数。

三种抵扣解释分别为：未来出窗口序列逐根不升且有下降（strict）；未来序列终点低于起点（end_lower）；最近三个已经完成的均线更新所用抵扣价逐根不升且有下降（observed_falling）。三者均额外要求恒价假设下MA10/20/30在预测窗口逐步上升并出现多头排列。它们是待对齐的规则解释，不能混为用户已经确认的定义。

所有设置的候选并集只覆盖3/7个股票月份，共9个不同时间。**任一统一设置最多覆盖2/7个股票月份**。observed_falling分支没有完整技术候选；strict分支覆盖精智达6月与京东方6月，近岸候选只出现在end_lower解释。G1291（当前日线排列、上升10天、金叉8根、预测4根、回踩等待64根、容差1%、end_lower）覆盖近岸与精智达；它只用于说明覆盖差异，没有被认定为最优收益参数。

下表列每个样本在扫描范围内的最大条件计数。这些列的最大值可能来自不同设置，不能拼成一套规则；回踩计数单位为K线，不能相加为交易数。

{md_table(['股票','月份','日线资格有效根数上限','回踩根数上限','技术候选','主要缺口'],case_rows)}

精智达4月、山东赫达8月、万向德农8月的当月日线六条均线完整多头排列天数均为0；即使把MA30上升天数放宽到5，也没有日线入选。因此不能只通过放宽15分钟买点去匹配这三例。康龙化成虽在8月后段建立日线资格，但已观察到的回调反转没有形成“回调开始前已有资格”的完整链条。

## 回调状态的时效

9个候选距其保留的空头回调起点121—252根，即约7.6—15.8个交易日。这未违反原文明确的超参数，但可能把原回调和后面的再次启动连在一起。仅增加“买点距回调起点不超过64/128/256根”的入场门槛，已观察候选分别剩0/2/9个；128根只保留京东方6月24日两种解释产生的时点。这是已有候选的因果门槛敏感性检查，没有重新搜索到期后新回调。**需要先对齐用户想表达的具体买点，再确定状态到期与重置规则。**

## 条件成交回放

以下每个时间属于不同参数解释下的候选，不是一套统一策略产生的九笔交易。每个候选使用独立30万元模拟账户、初次预算90%；账户重置，重叠持仓和结果不得相加为组合收益。报告的是相对首次实际现金投入的净变动；近岸四个候选截至9月30日仍持仓，含浮盈，不能当成完成交易胜率。全期均为用户提供的已知开发例，未进行收益优化、年化或样本外胜率声明。

{md_table(headers,candidate_rows)}

买回对精智达两种时点有利，却在京东方6月24日两种时点分别减少约10个百分点净变动；6月26日候选在16根内未达到期望而清仓。这些例子不支持“买回必然提高收益”。它们只证明状态机和现金账本已经实际走过相关路径。

执行口径：信号在已完成K线收盘形成，下一计划15分钟槽的开盘价加减5bp滑点模拟成交；09:45等标签是K线结束时间，物理开盘为标签减15分钟。相邻收盘与开盘同一钟表时间时，假设撮合发生在收盘决策之后，不代表零延迟真实成交。佣金3bp、每笔最低5元、过户费0.1bp、卖出印花税5bp是统一研究费用假设。涨停开盘买入和跌停开盘卖出保守阻塞；缺失/零成交量槽不成交；首次买入失败取消，清仓目标持续等待可卖数量。没有盘口排队、冲击或成交量参与率模型，OHLC结果不是实盘成交保证。

每批当天买回的股份重新受T+1约束，不能把恢复目标误记成已买回，也不会在先前减仓未成交时虚构买回。T+1依据[深交所交易规则第3.1.4条](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)。普通股票整百股申报；科创板买卖至少200股、可递增1股，不足200股的全部余股卖出例外依据[上交所科创板交易问答](https://edu.sse.com.cn/tib/qa/c/4866268.shtml)。精智达首次约500股，单独减四分之一不足200股时标记阻塞；后续多个减仓目标合并达到可申报数量才模拟成交，不补造不合法的小额订单。

暂定卖出定义仍包括：期望为MA5/10/20/30连续上升3根且多头排列；16根超时卖出；两档减仓分别按初始股份的1/4和1/2计算；“跌破MA120”解释为收盘价跌破，并在期望确认之前也作为保护性清仓条件；题材退潮没有指定独立减仓比例。这些选择尚需策略含义对齐。两种回放只对比允许/禁止技术买回，**没有把未核实的题材退潮代理直接接入卖出**。

允许买回的九次回放共观察到四分之一减仓意图{events['reduce_a_quarter_original']}次、二分之一减仓意图{events['reduce_b_half_original']}次、A买回意图{events['restore_a_quarter_original']}次、B买回意图{events['restore_b_half_original']}次、MA120清仓意图{events['exit_ma120']}次和期望超时清仓{events['exit_expectation_timeout']}次。意图次数不同于成交次数；相邻时点存在重复行情事件，不能视为独立统计样本。

## 题材证据与未确认项

每例在回调开始前冻结此前20个交易日的历史涨停参与者，沪深范围排除当时榜单名称带ST/退的记录；保持已掉出榜单的旧成员。没有使用当前概念全量成分，也没有使用买点当日收盘涨停榜。KPL的`lu_desc`是主归因，`theme`是辅助标签，两个证据渠道明确区分：近岸的原成员用历史CRO辅助标签，京东方用玻璃基板辅助标签；精智达用回调之前已发表的存储/测试设备关联报道推断存储/HBM比较池，不能伪称其自身已有涨停核心身份。

近岸、京东方、精智达的固定原成员池分别为8、13、42只，当前时点行情引用覆盖100%；此前20日同标签单日涨停宽度峰值分别6、5、11只。盘中转强代理要求至少80%行情覆盖、固定全池至少一半上涨、至少两只涨2%、中位数上涨；共振代理要求固定原成员中位价格和个股都回撤至少3%，收盘价低点所在交易日相差不超过1日。这些阈值未经验证，只是可观察证据，不能充当“热点”“核心”“共振”的真值。

京东方三个时点同时具备群体转强与共振代理；近岸仅9月22日13:15具备群体转强代理，四例均未通过上述共振代理；精智达两个时点具备群体转强，未通过共振代理。没有可靠方法据此确认核心身份、催化持续性或盘中带动关系，正式题材门槛全部保留未验证。既有模型归因被读取，但未复核或晚于决策时点的结果没有回填为历史事实。最终KPL的次日可用性仍是来源假设，缺乏首发归档时间证明。

## 验证与剩余工作

18个单元测试通过，覆盖恒价推演、日线状态、未来数据截断、16根边界、分档意图、T+1、最小申报量、未成交减仓的恢复、现金守恒和持仓复权因子变化报警。新增{counts['grid_scanner']}项快慢扫描等价检查、{counts['execution_prefix']}项成交回放前缀比较、{counts['peer_prefix']}项题材分钟前缀比较通过；已有v1的70项信号前缀检查保留。九个候选的持仓期未观察到复权因子变化，机械现金账本通过；尚无完整分红送转现金模型。

图册的日线最后一根只用信号时点已完成分钟聚合，15分钟竖线左侧为当时历史，右侧阴影为后续执行回放。全部9张图已实际查看；图上买卖编号与各模式逐笔成交表对应。题材`audit_v2`修复了早版同日不同时点错误复用早截断缓存的问题，旧`audit`标记作废并保留审计记录。

剩余三项：对齐样本具体买点及抵扣/日线长均线资格含义；建立当时可见的核心角色与催化持续性判断，并定义题材退潮如何改变操作；冻结最终参数后加入同时期非赢家对照、统一候选池、组合资金限制和真正隔离的时间验证。当前原成员行情比较不是策略非赢家对照试验。没有完成这些步骤，不能宣称策略有效或参数已经调优完成。

详细账本与图册：[v2回放页]({root}/review.html)。原始合同：[规则草案]({ROOT}/policyStudy/docs/ma_system_pullback_contract_20261005.md)。本轮未下单、未发布或提交代码。
'''
    doc = ROOT / "policyStudy/docs/ma_system_pullback_research_20261005.md"
    doc.write_text(report)
    case_headers = ["股票", "月份", "有效资格根数上限", "回踩根数上限", "技术候选", "主要缺口"]
    cards = []
    for row in candidate_rows:
        ident = row[0]
        folder = root / "replay" / ident
        details = []
        for mode, label in [("rebuy", "允许买回"), ("no_rebuy", "禁止买回对照")]:
            ledger = json.loads((folder / f"{mode}_ledger.json").read_text())
            fills = [e for e in ledger if e["kind"] == "fill"]
            fill_rows = [[i, e["time"], "买" if e["side"] == "buy" else "卖", e["qty"], f"{e['price']:.4f}", f"{e['fee']:.2f}", e["held_after"]] for i, e in enumerate(fills, 1)]
            details.append(f'<details><summary>{label}逐笔成交</summary>{htable(["图上编号","物理成交时间","方向","股数","原价+滑点","费用","成交后持股"],fill_rows)}<p><a href="replay/{ident}/{mode}_ledger.json">完整意图、阻塞和成交账本</a></p></details>')
        cards.append(f'<article id="{ident}"><h2>{ident} {html.escape(row[1])} {html.escape(row[2])}</h2><p>条件候选，正式题材门槛未验证；回调起点距买点{row[3]}根，启动后等待{row[4]}根。图上编号对应下方逐笔表。</p><a href="replay/{ident}/review.png"><img loading="lazy" src="replay/{ident}/review.png" alt="{ident}条件回放"></a>{"".join(details)}<p><a href="replay/{ident}/candidate.json">候选与日线证据</a> · <a href="theme/audit_v2/{ident}/frozen_pool.json">回调前固定题材原成员</a> · <a href="theme/audit_v2/{ident}/peers.json">决策时点原成员行情</a></p></article>')
    style = 'body{font-family:system-ui;background:#eef2f6;color:#243247;max-width:1400px;margin:auto;padding:32px}article,.panel{background:white;border-radius:12px;padding:24px;margin:24px 0}h1{font-size:30px}p{line-height:1.7}.notice{border-left:5px solid #d97706;padding:16px;background:#fffbeb}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;border-bottom:1px solid #e2e8f0;white-space:nowrap;text-align:left}th{background:#edf2f7}img{width:100%;display:block;margin:20px 0}summary{cursor:pointer;padding:12px;background:#f1f5f9;margin:8px 0}a{color:#1d4ed8}nav{display:flex;gap:14px;flex-wrap:wrap}'
    (root / "review.html").write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>均线回调系统研究 v2</title><style>' + style + '</style><h1>均线回调系统：参数覆盖与条件成交 v2</h1><p>2026-10-05 · 7个已知股票月份 · 1,728组覆盖设置 · 9个候选时点 · 18次条件回放</p><p class="notice"><b>研究尚未完成正式策略验证。</b>任一统一设置最多覆盖2/7个样本；候选并集覆盖3/7。题材核心身份与样本具体买点尚待对齐。各候选账户独立，参数不同、行情重叠，净变动不能相加；近岸期末仍持仓，含浮盈。</p><p><a href="'+str(doc)+'">完整研究报告</a> · <a href="grid/config_summary.csv">参数覆盖总表</a> · <a href="theme/audit_v2/summary.csv">题材证据表</a></p><nav>' + ''.join(f'<a href="#{row[0]}">{row[0]} {html.escape(row[1])}</a>' for row in candidate_rows) + '</nav><section class="panel"><h2>逐例规则缺口</h2>'+htable(case_headers,case_rows)+'</section><section class="panel"><h2>条件回放结果</h2><p>百分比为净现金变化及期末持仓市值相对初始实际投入；含费用。原成员转强与回调共振均为未校准代理。买回对精智达有利，却明显降低京东方这组样本结果。</p>'+htable(headers,candidate_rows)+'</section>'+''.join(cards)+'</html>')
    # Preserve erroneous early audit for provenance but prevent reuse as current evidence.
    write_json(root / "theme/audit/SUPERSEDED.json", dict(replacement="../audit_v2",
                                                         reason="same-day peer cache omitted intraday cutoff; later bars appeared missing",
                                                         corrected=True))
    write_json(root / "REPORT_MANIFEST.json", dict(status="development_and_conditional_execution_complete_validation_incomplete",
                                                  formal_entries_verified=False, checks=counts,
                                                  report_sha256=digest(doc), html_sha256=digest(root / "review.html"),
                                                  source_inputs_unchanged=True, reviewed_charts=9,
                                                  known_development_examples=True))
    print(doc)
    print(root / "review.html")


if __name__ == "__main__":
    main()
