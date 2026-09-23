"""Render reviewed interpretation beside reproducible facts and source IDs."""
import json
from pathlib import Path

from study_divergence import ROOT, BASE, OUT
from build_case_dossiers import save


def pct(value):
    return "缺失" if value is None else f"{value:+.2f}%"


def link(label,path):
    return f"[{label}]({path.resolve()})"


def main():
    annotations=json.loads((BASE/"research/divergence_case_annotations_v1.json").read_text())
    summary=json.loads((OUT/"summary.json").read_text())
    verification=json.loads((OUT/"verification.json").read_text())
    # This narrative is a reviewed v1 artifact. Fail if recomputed facts drift.
    assert (summary["events"],summary["actual_loss_events"],summary["daily_complete"],
            summary["width_drop_without_negative_median"],summary["no_height_core"])==(338,167,330,168,126)
    assert {(x["width_repaired"],x["n"],x["old_pool_restored"]) for x in summary["cross_tab"]}=={(False,132,32),(True,34,22)}
    diagnostic=[x for x in summary["diagnostics"] if x["time"]=="1030"]
    assert [(x["n"],x["remaining_negative"]) for x in diagnostic]==[(38,18),(25,9),(15,4)]
    assert not verification["mismatches"] and all(x["unchanged"] for x in verification["v1_hashes"].values())
    short=[]; reviewed_count=0; raw_count=0
    for note in annotations["cases"]:
        folder=OUT/(note["date"]+"_"+note["theme"])
        detail=json.loads((folder/"evidence_pack.json").read_text()); row=detail["summary"]
        news=json.loads((folder/"news_candidates.json").read_text())
        by_id={source["md5"]:r for r in news for source in r["source_records"]}
        reviewed=[]
        for selected in note["reviewed_news"]:
            assert selected["id"] in by_id,selected
            r=by_id[selected["id"]]
            assert r["datetime"]<=detail["news"]["end"]
            reviewed.append(dict(r,selected_id=selected["id"],review_note=selected["use"]))
        save(folder/"reviewed_news.json",reviewed)
        reviewed_count+=len(reviewed);raw_count+=detail["news"]["raw_count"]
        lines=[f'# {note["date"]} {note["theme"]}：{note["title"]}',
               '', '**性质：事后对照学习，不是当日预测。** P为分歧前交易日，D为分歧日，T为下一交易日，U为T的下一交易日。',
               '',note["interpretation"],'', '## 固定名单和市场背景','',
               f'P={row["p_date"]}；D={row["date"]}；T={row["t_date"]}；U={row["u_date"]}。原成员{row["pool_n"]}只；'
               f'D全市场上涨比例{row["market_d_breadth"]:.1%}，T为{row["market_t_breadth"]:.1%}。',
               '', '高度候选：'+('；'.join(f'{c["name"]}（{c["ts_code"]}，{c["height"]}连板，{c["regime"]}制度）' for c in detail["cores"]) or '无符合条件的连板高度候选')+'。高度身份不自动等于产业龙头。',
               '', '|日期|同主归因涨停数|其中原成员数|', '|---|---:|---:|']
        for x in detail["timeline"]:lines.append(f'|{x["date"]}|{x["width"]}|{x["original_pool_in_theme"]}|')
        lines+=['','## 新闻逻辑与归因核对','',note["logic"],'',annotations["source_limit"],'',
                '|新闻时间|来源/记录ID|所属时段|阅读后的用途|','|---|---|---|---|']
        for r in reviewed:lines.append(f'|{r["datetime"]}|{r["src"]} / {r["selected_id"]}|{r["time_bucket"]}|{r["review_note"]}|')
        lines+=['',link('已阅读新闻原文及来源记录',folder/"reviewed_news.json"),'',
                '|日期|高度候选|榜单状态|KPL主归因|KPL多标签|','|---|---|---|---|---|']
        for r in sorted(detail["core_kpl_history"],key=lambda r:(r["trade_date"],r["ts_code"])):
            lines.append(f'|{r["trade_date"]}|{r["name"]}|{r["tag"]}/{r["status"]}|{r["lu_desc"]}|{r["theme"]}|')
        lines+=['','主归因空缺的股票日不会以当前概念成员补入。KPL标签变化用于事后核验，不能成为更早的预测输入。',
                '', '## 次日固定名单的盘中截面','',note["intraday"],'',
                '|T时点|覆盖/原池|上涨数|原群体当日中位涨跌幅|原群体相对P基准|高度候选当日中位涨跌幅|',
                '|---|---:|---:|---:|---:|---:|']
        for r in detail["snapshots"]:
            lines.append(f'|{r["cutoff"]}|{r["covered"]}/{r["expected"]}|{r["positive_count"]}|{pct(r["median_pct"])}|{pct(r["since_p_median_pct"])}|{pct(r["core_median_pct"])}|')
        seq=detail["first_three_minute_run"]
        lines+=['','首次连续3分钟确认，记录的是第3分钟完成时点；跨午休不连续，缺数不算转强：',
                '',f'- 高度候选中位数转正：{seq["core_positive"] or "未出现"}。',
                f'- 原群体多数上涨：{seq["pool_majority_positive"] or "未出现"}。',
                f'- 两者同时成立：{seq["core_and_group"] or "未出现"}。',
                f'- 原群体累计中位数收复P基准：{seq["pool_back_to_p"] or "未出现"}。',
                '',link('全部240个分钟截面',folder/"fixed_pool_minute_path.csv"),
                '', '## 原成员逐股结果','',
                '|股票|P高度|D当日|T当日|P至T累计|P至U累计|','|---|---:|---:|---:|---:|---:|']
        for r in detail["stock_outcomes"]:
            lines.append(f'|{r["name"]} {r["ts_code"]}|{r["p_height"]}|{pct(r["d_pct"])}|{pct(r["t_pct"])}|{pct(r["t_since_p_pct"])}|{pct(r["u_since_p_pct"])}|')
        loss=detail["supplemental_d_losers"]
        lines+=['',f'补充看D实际下跌的{loss["n"]}只：T有{loss["t_restored"]}只收复P基准，U有{loss["u_restored"]}只。此拆分不修改冻结的群体修复定义。',
                '', '## 判断边界','',note["limit"],'',
                f'新闻窗口{detail["news"]["start"]}—{detail["news"]["end"]}；读取{detail["news"]["raw_count"]}条，关键词候选{detail["news"]["candidate_count"]}条，本页选定阅读{len(reviewed)}条。读取条数不等于语义核验条数。',
                '', '累计结果先按逐股close/pre_close复合，再求群体中位数；缺失不填0。结果是价格路径，不是可交易收益。',
                '', link('结构化事实包',folder/"evidence_pack.json")+'；'+link('P日涨停原因原记录',folder/"prior_reasons.json")]
        (folder/"案例.md").write_text('\n'.join(lines)+'\n',encoding='utf-8')
        short.append((note,row,folder))
    lines=['# 分歧修复研究 v1：涨停数量、原成员和核心需要分别判断','',
           '研究范围：2026年1—8月。沿用已公布的338次宽度收缩事件，冻结分歧前名单，补齐次日分钟观察，并阅读4个事后对照案例。**本轮是方法研究，没有产生新的独立预测准确率；95%/95%/100%的目标仍未达到。**','',
           '## 1. 这轮得到什么','',
           '单凭涨停数回升、最高板走强，无法确认“分歧转一致”。本轮发现三种可区分的现象：宽行业标签换了一批强股；原成员价格修复但未重新涨停；少数高度候选独强、群体未跟随。判定应同时报告宽度、固定原群体、核心代表性和市场背景。','',
           '此前“核心先动、群体再跟随”的说法过于严格。样本里既有群体先恢复，也有核心和群体分别短暂转强、从未共同持续转强的情形。应记录顺序和同一时点的共振，不把先后当成带动因果。','',
           '## 2. 样本和口径','',
           '- P是分歧前交易日，D是宽度收缩日，T和U是后两个交易日。冻结P的全部同主归因涨停股，不只保留D还在涨停榜上的幸存者。',
           '- 沿用原事件检索：P宽度≥5、D宽度≤P的60%、同题材事件至少间隔3个交易日，并有两个后续交易日。它检索的是宽度收缩，不是语义真值。',
           '- 原宽度修复代理：T或U的宽度达到P的80%。新增同标签股票可以贡献宽度，因此与原成员反馈分别计算。',
           '- 原群体实际受损：完整P名单在D的当日涨跌幅中位数小于0。原群体修复：先逐股复合参考前收收益链，T或U收盘的累计中位数达到P收盘基准。该定义不代表每只股票都恢复。',
           '- 高度候选：P中至少2连板，在10%和20%制度内分别取最高连续板数，保留并列。全是首板时没有高度候选；不强行制造龙头。',
           '- 原群体、D下跌子群体、池外扩散成员和经语义核验的核心分别保留；宽度沿用KPL主归因，不静默改历史标签。','',
           link('计算前保存的本轮研究协议',BASE/"research/divergence_protocol_v1.json")+'。协议在本轮统计前记录，但1—8月此前已被阅读，不等于全新的预注册独立实验。','',
           '338次中，335次D原群体报价完整：167次中位数为负，168次并未转负；另外3次D报价缺失。完整覆盖D、T、U的有330次；其中166次确实D受损，构成下表分母。其余8次保留在总样本及缺失清单里。','',
           '|166次原群体确实受损的事件|原群体已修复|原群体未修复|合计|','|---|---:|---:|---:|',
           '|涨停宽度已修复|22|12|34|','|涨停宽度未修复|32|100|132|','|合计|54|112|166|','',
           '12次是宽度恢复但原群体没有恢复；32次方向相反。168次宽度缩减而中位数未跌，只能说明“宽度收缩”和“价格受损”并非同一件事，不能据此否认其中存在抛压、冲高回落或其他形式的分歧。338次中126次没有符合条件的连板高度候选。','',
           link('338次逐事件结果',OUT/"all_338_events.csv")+'；'+link('汇总、条件样本及缺失清单',OUT/"summary.json"),'',
           '## 3. 四个同题材近时段对照','',
           '案例根据事后结果选择：5月半导体展示宽度与原群体修复方向不一致；6月机器人使用两个同为7只的原群体作对照。5月初始宽度不同，四例市场背景也不同，均非随机抽样或严格因果配对。','',
           '|D/题材|P→D→T→U宽度|原群体D|T相对P|U相对P|关键现象|','|---|---|---:|---:|---:|---|']
    for note,r,folder in short:
        detail=json.loads((folder/"evidence_pack.json").read_text())
        widths=[next(x["width"] for x in detail["timeline"] if x["date"]==r[k]) for k in ["p_date","date","t_date","u_date"]]
        lines.append(f'|{link(note["date"]+" "+note["theme"],folder/"案例.md")}|'+ '→'.join(map(str,widths))+f'|{pct(r["d_median_pct"])}|{pct(r["t_since_p_median_pct"])}|{pct(r["u_since_p_median_pct"])}|{note["title"]}|')
    lines+=['','需要特别保留的两处反例：',
            '', '- **6月17日机器人**：两个机械修复标签都通过，次日原7只却只有3只上涨；高度候选长裕集团的当时新闻及LP原因偏向小金属/氧化锆，KPL次日主归因也改为非金属材料。这个案例不能被训练成标准“机器人龙头带动修复”。',
            '- **6月24日机器人**：次日10:30红豆股份约+7.32%，原群体中位数约-1.91%、仅2/7上涨。09:36出现过连续3分钟多数上涨，09:43出现核心转正，但没有连续3分钟两者同时成立。将不同时间的单项好现象拼接起来会误判。','',
            '5月21日半导体也有归因疑点：KPL芯片主标签下的最高板达实智能，当时报道主要以液冷/算力解释。其强势可作为情绪高度观察，半导体产业代表性仍待核实。报告没有重新授予某只未来赢家“真龙头”身份。','',
            '## 4. 盘中条件能否预测剩余行情','',
            '只用当时完整固定名单，在T10:30比较三层已冻结的描述条件。后续结果另算每只股票“当日收盘价/10:30价格−1”的中位数，排除上午已经发生的涨幅。','',
            '|10:30已观察到的条件|满足条件的事件数|之后到收盘群体中位数仍下跌|','|---|---:|---:|',
            '|高度候选当日涨跌幅中位数>0|38|18|',
            '|再要求原群体多数上涨|25|9|',
            '|再要求原群体累计中位数收复P基准|15|4|','',
            '增加群体条件有助于描述更完整的状态，但这里尚不能推导预测优势：三组样本数量与构成变化，事件在日期及股票上相互重叠，未做市场环境匹配，也没有新时段验证。15次里仍有4次后半程走弱，不能声称已接近95%。上述价格路径不是策略胜率，未处理买入可达性、交易费用或次日卖出。','',
            '## 5. 写入技能的可执行判断流程','',
            '1. **先核实分歧发生在哪里。** 分开写涨停宽度收缩、原群体实际下跌、核心破坏、炸板和高低位分化；单个现象不足以命名完整生命周期。',
            '2. **冻结分歧前名单。** 保留P原群体，D实际下跌成员作补充观察，新增扩散另列；缺数保留分母。',
            '3. **核验高度候选的代表性。** 对照KPL主归因、多标签、LP原因及截至判断时点的公司事实。标签冲突写未知或低置信，不把最高板直接当产业龙头。',
            '4. **按三个层面观察修复。** 当日转正是反弹，收复P基准是损伤恢复，更多原成员同步且核心具有同一逻辑才支持一致性增强。D下跌子群体是否回升也应独立报告。',
            '5. **要求同一时段相互印证。** 记录谁先动，检查共同维持及后续撤销；不要求固定先后顺序。连续3分钟只是本轮路径摘要，不是已验证的交易阈值。',
            '6. **检查市场和分支。** 普涨中的恢复、弱市里的相对强势、新催化带来的分支更替分别解释；没有去重收益/超额证据时，不声称发生资金搬家。',
            '7. **给出可撤销的结论。** 可以写“原核心反弹、群体未跟随”“原群体部分收复、宽度尚弱”“新分支扩散、旧群体未恢复”，不强行二分为成功/失败。','',
            '预测仍须在下一观察时点到来前落盘，分别评价当时状态识别、下一阶段预期和剩余价格路径。当前四例的解释全是事后阅读，不能代替这项考核。','',
            '## 6. 数据核查和剩余缺口','',
            f'- 本轮重新读取136个T交易日的P名单分钟数据。请求{verification["requested_stock_days"]}个去重股票日；{verification["comparable_stock_days"]}个15:00价格可与日线收盘核对，发现{len(verification["mismatches"])}处差异；缺15:00报价{verification["missing_15_stock_days"]}个股票日，保留缺失。',
            f'- 四个新闻窗口共读取{raw_count}条窗口内记录（不作为跨窗去重总量），本轮报告选择{reviewed_count}条完成语义阅读。其余只是检索候选；媒体AI生成的涨停原因不作为独立事实核验。',
            '- KPL最终榜单的历史首发时间未存档，仍采用次日06:00可见的研究假设；news.datetime也只有来源标注时间，非完整可用性证明。次日06:00后的新闻单列，不能倒写盘后版本。',
            '- 分钟数据没有真实09:25竞价或封单轨迹。本轮不证明开盘封单变化、分钟内回封顺序、谁带动谁的因果。',
            '- 固定池只是P已涨停成员，并未覆盖所有未涨停的同题材公司；核心是高度角色代理，缺少独立龙头真值。归因冲突需要继续做事件层面的成员整理。',
            '- 未做全338例的逐条新闻语义标注、全期同环境因果配对或新时段盲评。这里得到的是口径修正和待验证方法。',
            '- v1预测、标签和冻结模型的哈希均保持不变；未重写旧成绩。关键时间隔离、名单冻结、缺失处理和候选分组验证已通过。','',
            link('价格核查与v1文件校验',OUT/"verification.json")+'；'+link('本轮输入/规则/脚本标识',OUT/"manifest.json"),'',
            '## 7. 复现和下一步','',
            '项目根目录使用`.venv/bin/python`，依次执行策略scripts目录的`study_divergence.py`、`build_divergence_cases.py`、`verify_divergence.py`、`render_divergence_report.py`。新数据只能经data_provider的database_only读取，脚本默认复用该接口取得的本地缓存。',
            '',link('四例已阅读解释及新闻ID',BASE/"research/divergence_case_annotations_v1.json")+'；'+link('研究技能',ROOT/"policyStudy/skills/a-share-theme-research/SKILL.md"),'',
            '下一步应优先修正题材归因和核心代表性：把半导体内部存储、特气、陶瓷等事件分支，与机器人中的独立材料/股权事件区分，再构建逐日条件判断。否则提升打分精度只会放大错标签。新规则冻结后，使用未参与本轮修改规则的新时段，才能重新评估95%目标。']
    report=ROOT/"reports/分歧修复研究_v1.md"
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(report)


if __name__=="__main__":main()
