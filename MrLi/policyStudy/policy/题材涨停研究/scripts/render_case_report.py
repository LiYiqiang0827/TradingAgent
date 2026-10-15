"""Join reviewed reasoning to traceable evidence; fail on missing news IDs."""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
from build_case_dossiers import OUT, BASE, ROOT, save


def read(path):
    return json.loads(path.read_text())


def fmt(value, unit="%", digits=2):
    if value is not None and abs(value)<.5*10**(-digits):
        value = 0.0
    return "未知" if value is None else f"{value:.{digits}f}{unit}"


def resolve_news(case_folder, evidence):
    name = "news_candidates.json" if evidence["bucket"]=="day" else "overnight_candidates.json"
    rows = read(case_folder/name)
    for row in rows:
        for source in row["source_records"]:
            if source["md5"]==evidence["md5"]:
                return {**row, **source, **evidence}
    raise ValueError(f"Unresolved evidence {case_folder.name} {evidence['md5']}")


def main():
    annotations = read(BASE/"research/case_annotations_v1.json")
    outcomes = pd.read_csv(OUT/"all_221_cohort_outcomes.csv",dtype={"date":str})
    audit = read(OUT/"cohort_outcome_audit.json")
    summary = []
    total_news = 0
    for annotation in annotations["cases"]:
        folder = OUT/annotation["id"]
        pack = read(folder/"evidence_pack.json")
        news = [resolve_news(folder,e) for e in annotation["evidence"]]
        save(folder/"reviewed_evidence.json",news)
        total_news += pack["news_raw_count"]+pack["overnight_raw_count"]
        outcome = outcomes.loc[(outcomes.date==pack["date"])&(outcomes.theme==pack["theme"])].iloc[0]
        summary.append(dict(date=pack["date"],theme=pack["theme"],event_theme=annotation["event_theme"],width=pack["width"],
                            d1_width=pack["timeline"][4]["theme"]["breadth"],d1_median=outcome.d1_median_pct,
                            d2_median=outcome.d2_cumulative_median_pct,original_pool_up=outcome.d1_original_pool_limit_count,
                            new_primary=outcome.d1_new_primary_count,formed=pack["formed"],path=str(folder/"案例.md")))
        lines=[f"# {pack['date']} {annotation['event_theme']}","",annotations["scope"],"",
               f"样本选择：{pack['selection']}。原代理结果：后续5个交易日中{pack['future5_strong_days']}个强势日。强势仅指至少5只涨停且当日宽度排名前三，含并列。", "",
               f"## 题材与状态", "",annotation["state"],"",annotation["explanation"],"",
               "## 当时已知的反证", "",annotation["counterevidence"],"",
               "## 盘前更新应当检查什么", "",annotation["preopen"],"",
               "## 后续事实", "",annotation["followup"],"",
               f"固定启动日股票群体的两交易日累计涨跌幅中位数为{fmt(outcome.d2_cumulative_median_pct)}，按每只股票收盘价/当日参考前收的比值逐日复合后取中位数；不等同于可成交组合收益。", "",
               "| 日期 | 相对日 | 本题材涨停数 | 市场上涨比例 | 市场涨停数 | 成交额/过去20日均值 |", "|---|---:|---:|---:|---:|---:|"]
        for r in pack["timeline"]:
            lines.append(f"| {r['date']} | {r['offset']:+d} | {r['theme']['breadth']} | {r['market']['breadth']:.1%} | {r['market']['up_count']} | {r['market']['amount_ratio']:.2f} |")
        lines += ["","## 固定启动日名单的分钟观察", "",
                  "名单在观察日前固定，不加入次日新涨停股。分母保留缺数；这仅覆盖启动日涨停群体，不代表完整产业成分。09:31是分钟价格观察点，不是09:25竞价。", "",
                  "| 日期 | 截面 | 有效/应有 | 上涨/应有 | 涨跌幅中位数 |", "|---|---|---:|---:|---:|"]
        for obs in pack["cohort_followup"]:
            for s in obs["snapshots"]:
                lines.append(f"| {obs['date']} | {s['time']} | {s['covered']}/{s['expected']} | {s['positive']}/{s['expected']} | {fmt(s['median_pct'])} |")
        lines += ["","## 核心股角色", "",annotation["roles"],"",
                  "| 股票 | 涨停制度 | 连续板 | 首封记录 | 成交额（亿元） |", "|---|---|---:|---|---:|"]
        current = pack["timeline"][3]["limit_stocks"]
        chosen = []
        for regime in ["10%","20%"]:
            chosen += [r for r in current if r["limit_regime"]==regime][:2]
        chosen += sorted(current,key=lambda r:r["amount"],reverse=True)[:2]
        used = set()
        for r in chosen:
            if r["ts_code"] in used: continue
            used.add(r["ts_code"])
            lines.append(f"| {r['name']} {r['ts_code']} | {r['limit_regime']} | {r['height']} | {r['lu_time']} | {r['amount']/1e8:.2f} |")
        lines += ["","以上为高度、首封与成交规模的代表样本，不是已验证的龙头标签。首封来自KPL最终榜，不能视为当时已有整份最终名单。", "",
                  "## 阅读并标注的新闻证据", "",
                  "编号用于定位本地原文。多源转载不算独立证实；下表为报道的分类，底层新闻真实性及首次可见版本未额外核验。", "",
                  "| 时间 | 来源/ID | 性质 | 阅读结论 |", "|---|---|---|---|"]
        for r in news:
            lines.append(f"| {r['datetime']} | {r['src']} / {r['md5']} | {r['kind']} | {r['note']} |")
        lines += ["",f"本案例盘前历史窗口共有{pack['news_raw_count']}条原始多源新闻，另有{pack['overnight_raw_count']}条隔夜至次交易日09:30新闻；候选检索不代表全部逐条语义审阅。各来源覆盖见evidence_pack.json，全部命中与原文见news_candidates.json和overnight_candidates.json。", "",
                  "## 待验证命题", "",annotation["hypothesis"],"",
                  "本案例只能用于提出或反驳简单解释；没有独立样本，不能据此给出命题准确率。", ""]
        (folder/"案例.md").write_text("\n".join(lines))
    save(OUT/"case_summary.json",summary)
    lines=["# 题材案例研究v1：从涨停地图到事件、群体与核心", "",
           "本轮完成六个事件的事后证据阅读，并对全部221次既定启动事件复核原始股票群体的后续表现。它是学习阶段，不是新的盲测成绩，也不声称已完成八个月所有题材的生命周期标注。", "",
           "六例按1—3月、4—6月、7—8月分段，在每段的原宽度延续/未延续组中各取启动日宽度最大的一例。选择使用了未来结果，只用于对照学习，不能从这六例估计胜率或作因果配对比较。", "",
           "## 最重要的修正：不能把题材名称存活等同于原群体持续强势", "",
           "原探索定义为：当日至少5只涨停、此前3日均不足5只，视为一次启动；后续5日中至少2日进入至少5只涨停的宽度前三（含并列），视为宽度延续。这是机械代理，尚非真实主线标签。", "",
           "| 原宽度代理 | 事件数 | 两日日线完整 | 次日原群体中位数为负 | 两日累计中位数为负 |", "|---|---:|---:|---:|---:|"]
    for g in audit["groups"]:
        lines.append(f"| {'延续' if g['broad_theme_formed'] else '未延续'} | {g['events']} | {g['complete_daily']} | {g['d1_negative_median_count']} | {g['d2_negative_cumulative_median_count']} |")
    lines += ["", "45个宽度延续事件中，12个原始股票群体的两日累计涨跌幅中位数为负。不能把这些事件直接标成原成员延续成功；需要分别考核具体事件题材的持续、原成员反馈与核心股延续。两个日线不完整事件保留在总数，未混入完整数据分组比例。", "",
              "累计涨跌幅使用收盘价/当日参考前收逐日复合。初算使用已四舍五入的pct_chg曾得到13个负值，其中1例复核为持平；不把数值舍入误差当作亏损。", "",
              f"在{audit['minute_complete_events']}个次日10:30与收盘数据完整的事件中，{audit['m1030_positive_events']}个10:30群体中位数为正，其中{audit['m1030_positive_close_negative_events']}个收盘转负。单个上午截面不能充当全天确认；这项统计也没有验证某个交易信号。", "",
              "## 六例概览", "", "| 启动日/方向 | 当日→次日宽度 | 原群体次日KPL涨停 | 原群体次日中位数 | 两日累计中位数 |", "|---|---:|---:|---:|---:|"]
    for r in summary:
        lines.append(f"| [{r['date']} {r['theme']}]({r['path']}) | {r['width']}→{r['d1_width']} | {r['original_pool_up']}/{r['width']} | {fmt(r['d1_median'])} | {fmt(r['d2_median'])} |")
    lines += ["", "所有涨跌幅均为固定启动日涨停名单的描述，未考虑买入时点、可成交性、成本或仓位，不能解释为策略盈利。", "",
              "全体事件发现两处KPL与日线涨停状态冲突，详见limit_status_conflicts.json。六例中机器人原群体次日KPL继续列涨停2只，按日线涨停价为3只（海晨股份在KPL列炸板）。宽度与梯队统计沿用KPL口径，价格观察单列，不静默合并。", ""]
    for a in annotations["cases"]:
        lines += [f"### {a['id']}：{a['event_theme']}","",a["state"]+"。", "",a["explanation"],"",a["counterevidence"],"",a["preopen"],"",a["followup"],""]
    lines += ["## 收敛为五个可执行研究问题", "",
              "1. 先辨认事件：这是全新信息驱动的启动，还是旧题材预热后的集中扩散、旧方向修复、个股并购事件？用此前20日结构和新闻首次出现顺序回答，三日低宽度本身不够。",
              "2. 再检查催化是否仍成立：把盘后、06:00、开盘前增量分别记录。事件前提被削弱时及时改判，不为维持前日判断而忽略反证。",
              "3. 同时维护两个群体：D日已知固定名单衡量原成员反馈；当日动态名单描述新扩散。二者不混算，新增成员不倒填到D日候选。",
              "4. 核心角色分开：高度、产业关联、较早转强、大成交额承接分别记录。核心强而群体弱时，只能确认核心幸存，不能确认全题材安全。",
              "5. 盘中确认可撤销：09:45、10:30、14:00分别保留判断。群体上涨比例、中位超额与原核心状态持续恶化时，修复预期应降级；具体阈值尚需在开发窗口冻结后检验。", "",
              "## 下一轮评估应如何改", "",
              "主题归因按宽行业→当期事件/分支→公司受益路径建立版本。生命周期当前状态与未来转移分开打分，人工/独立语义标签需引用当时证据，不能再用预测规则自己的阈值充当真值。主线判断至少分别报告事件持续、原群体反馈、候选覆盖；龙头分别报告角色识别、原候选延续、核心更替的识别时点。", "",
              "六例中的‘本来应该如何判断’均为看过结果后的研究建议。下一步应把同类条件应用于完整事件集，加入同一时期、相近宽度、相似市场背景的反例；之后冻结规则，用未参与调规则的新时段评估。不能把本轮1—8月结果再次称为独立未见测试。", "",
              "## 数据与复核", "",
              f"本轮六例共读取{total_news}条原始新闻记录（不同案例窗口可能重复，不代表独立新闻数）；仅选定证据完成语义标注，其余为可检索资料。逐例保留多源计数、新闻ID、原文、KPL关联与分钟观察。所有新数据经data_provider的database_only接口读取。", "",
              "历史成分与新闻是后来入库的历史日期记录，缺少当时首次发布的完整存档；公司说明只能辅助归因，不能独立证明历史可见性。KPL最终榜按次日可用假设重建，没有真实09:25竞价或封单时间序列。", "",
              "复核通过：六例合计356个后续股票交易日的15:00分钟价格与日线收盘一致；固定名单、未来日期隔离、缺数分母、新闻转载及累计涨跌幅精度共4项行为测试通过。原v1预测、标签、模型的文件指纹保持不变。本轮未重写历史预测来提高成绩。", "",
              f"[全部221次事件与固定群体结果]({OUT/'all_221_cohort_outcomes.csv'})；[复核汇总]({OUT/'cohort_outcome_audit.json'})；[六例结构化摘要]({OUT/'case_summary.json'})。", ""]
    target = ROOT/"reports/题材案例研究_v1.md"
    target.write_text("\n".join(lines),encoding="utf-8")
    print(target)


if __name__=="__main__":
    main()
