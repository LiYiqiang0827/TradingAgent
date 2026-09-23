"""Reports for reviewed historical narratives, with unresolved records retained."""
import json
from pathlib import Path
import pandas as pd

from build_attribution_inputs import OUT
from build_case_dossiers import BASE, ROOT, save


def link(label,path):return f"[{label}]({path.resolve()})"


def main():
    summary=json.loads((OUT/"summary.json").read_text())
    rows=json.loads((OUT/"all_52_attributions.json").read_text())
    evidence={r["md5"]:r for r in json.loads((OUT/"reviewed_news.json").read_text())}
    decisions_path=BASE/"research/attribution_case_decisions_v1.json"
    decisions=json.loads(decisions_path.read_text())
    assert (summary["rows"],summary["reviewed_stock_dates"],summary["selected_news_ids"] )==(52,20,29)
    for case in decisions["cases"]:
        date=case["date"]; theme=case["theme"]
        folder=OUT/(date+"_"+theme)
        group=[r for r in rows if r["feature_date"]==date]
        by_code={r["ts_code"]:r for r in group}
        assert all(c["code"] in by_code for c in case["candidates"])
        assert all(r["decision_at"]==case["decision_at"] for r in group)
        lines=[f'# {date} {theme}：归因与角色重建','',
               f'判断信息截止：**{case["decision_at"]}**。{decisions["mode"]}。{decisions["scope"]}。','',
               case["interpretation"],'',case["focus"],'','## 原名单完整保留','',
               '|股票|连续高度/制度|KPL主归因|多标签|LP原因|本轮状态|',
               '|---|---|---|---|---|---|']
        for r in group:
            lines.append(f'|{r["name"]} {r["ts_code"]}|{r["height"]} / {r["regime"]}|{r["raw_main_label"]}|{r["raw_multi_labels"]}|{r["lp_reason"]}|{r["review_state"]}|')
        lines+=['','未核实成员保留在总数中。没有公司新闻只表示本轮有界窗口、当时名称检索没有命中，不证明没有业务。KPL多项接口也不是多个独立信息来源。',
                '','## 角色候选及失效条件','',
                '|候选|本次角色|P日首封/末封|P日成交额（亿元）|P日封单/自由流通市值|','|---|---|---|---:|---:|']
        for c in case["candidates"]:
            r=by_code[c["code"]]
            lines.append(f'|{r["name"]}|{c["role"]}|{r["first_limit_time"]} / {r["last_limit_time"]}|{r["turnover_yuan"]/1e8:.2f}|{r["order_float_pct"]:.2f}%|')
        lines+=['','以上封板时间和封单来自最终榜单，是按盘前可见假设读取的P日事实；不构成P日盘中已知池或次日成交保证。容量指本样本成交规模，不是已验证的带动因果。','']
        for c in case["candidates"]:
            name=by_code[c["code"]]["name"]
            lines += [f'**{name}**：{c["reason"]}','',f'失效/降级条件：{c["invalidation"]}','']
        lines+=['## 逐条新闻判断','']
        selected=[]
        for r in group:
            if r["review_state"]!="新闻逐条核验":continue
            lines+=[f'### {r["name"]}：{r["branch"]}','',f'经济关联：{r["exposure"]}。','',r["judgment"],'',f'不能推出：{r["invalid_inference"]}','',
                    '|证据时间|来源/ID|标题或原文开头|','|---|---|---|']
            for ref in r["evidence"]:
                e=evidence[ref["id"]]
                assert pd.Timestamp(e["datetime"])<=pd.Timestamp(case["decision_at"])
                # Source content is in the linked evidence artifact; short labels keep this readable.
                title=e["title"] or str(e["content"]).split('】')[0].lstrip('【')
                title=title.replace('|','／').replace('\n',' ')[:75]
                lines.append(f'|{e["datetime"]}|{e["src"]} / {e["md5"]}|{title}|')
                selected.append(e)
            lines.append('')
        unique={r["md5"]:r for r in selected}
        save(folder/"selected_news.json",list(unique.values()))
        lines += [link('本例引用新闻原文',folder/"selected_news.json"),'',
                  '较早已读证据允许延续使用，但不能携带更晚的修订或解读。方正电机引用的5月14日报道早于本例14天初筛窗口，是从此前已读归档中显式补入，仍通过本例时点校验。' if date=="20260623" else '',
                  '', '## 不确定性','',case["uncertainty"],'',
                  '新闻为媒体归档，公告和互动答复多数是转述；本轮未逐条追溯原公告。历史概念描述可能滞后或被回填，只作线索。角色候选尚无独立真值与后续胜率。','',
                  link('结构化归因记录',folder/"reviewed_attribution.json")+'；'+link('截至时点的事实包',folder/"input_packet.json")]
        (folder/"归因与候选.md").write_text('\n'.join(lines)+'\n',encoding='utf-8')
    lines=['# 题材归因与龙头角色研究 v1','',
           '**这轮把“市场在炒什么”“公司如何受益”“股票扮演什么角色”分开。** 已建立4个历史截面的52条股票记录，其中20条结合29条新闻逐条核验，32条保留语义未定。形成4份有信息截止时点、候选角色和失效条件的判断重建。','',
           '研究者此前已阅读这段历史。本轮事实包不含之后的价格和结果，仍不能因此称为独立盲评；没有新增主线/龙头准确率，95%/95%/100%的目标尚未达到。','',
           '## 1. 对原判断方法的修正','',
           '情绪高度与产业代表性使用不同证据。业务关联弱、利润尚小的公司仍可能是当时的情绪高标；产业业务真实、规模大的公司也未必最早启动或最适合代表涨停接力。不能单凭公司澄清删除情绪角色，也不能用股价强势把产业传闻变成事实。','',
           '|要判断什么|主要证据|不能互相替代的内容|','|---|---|---|',
           '|市场注意力|连续高度、首封与末封、成交规模、同群体同步、当时行情叙事|资金关注不等于公司已兑现利润|',
           '|产业受益路径|具体主体、产品、客户/订单、投资关系、收入与利润规模、业务阶段|同一控制方、参股、主营供货不是同一种关系|',
           '|题材代表性|同一事件逻辑、多个独立成员、价格共同反应、归因反证|最高板或最大成交额不能单独证明带动|','',
           '## 2. 五个原高度候选，拆开后怎样看','',
           '|P日/原宽标签|原高度候选|应保留的角色|产业侧的限制|','|---|---|---|---|',
           '|5月14日/半导体|蒙娜丽莎，10%制度5板|陶瓷叙事情绪高度|主营建筑陶瓷，参股公司仍在研发送样，供应商传闻被否认|',
           '|5月14日/半导体|中船特气，20%制度2板|电子特气高度及样本内容量候选|已有业务，但截至盘前未签新增长期或大额实质订单|',
           '|5月20日/半导体|达实智能，10%制度4板|AIoT/物理AI等跨题材情绪高度|相关业务占比低，不制造液冷或机器人元件；不自动代表存储制造|',
           '|6月16日/机器人|长裕集团，10%制度2板|保留原榜高度，材料方向另核验|LP原因和当日媒体偏向锆材料；机器人代表性证据不足|',
           '|6月23日/机器人|红豆股份，10%制度2板|新设机器人公司叙事情绪高度|经营范围信息不能当作成熟业务、订单或技术证明|','',
           '上述限制是对命题范围的判断，不是对股票次日涨跌的预测，也不是断言这些公司永远与该题材无关。原始标签均保留，没有用后来标签或收益赢家重写前日名单。','',
           '## 3. 两个最容易发生的相反错误','',
           '**旧行业导致漏判。** 诚邦股份的历史概念描述仍以生态园林为主，但5月20日18:58的公告转述已经说明控股子公司经营存储器，并披露并表收入和利润。应保留其存储产品3板候选，同时限制对利润规模的推断。','',
           '**新概念导致高估。** 合百集团的报道澄清主营不涉及存储、与长鑫没有业务往来；历史成分描述却有通过基金投资的线索。这两条信息可以同时成立。股权映射、主营供货、业绩兑现应分开，不能把“无业务往来”扩大为“不存在间接投资”，也不能反向把基金投资说成存储产品销售。','',
           '同样，中科三环“机器人订单较少”不能改写成“零订单”；晶升股份“全部产能投向碳化硅时的每月能力”不能改写为真实月产量；达实智能“不制造相关元件”不能改写为“没有相关智能化服务”。','',
           '## 4. 怎样改变候选观察','',
           '|历史截面|重建后的观察重点|候选角色变化|已核验/原池|','|---|---|---|---:|']
    case_text={"20260514":("电子特气、碳化硅与陶瓷叙事分别观察","中船特气/晶升股份为不同分支；蒙娜丽莎保留情绪高度"),
               "20260520":("存储产品与参股映射分组","诚邦产品高度、合百映射高度、兆易容量；达实跨题材另记"),
               "20260616":("机器人产业高度核心暂缺","余下6只全是首板；中科三环等只列容量/先手/投资观察候选"),
               "20260623":("情绪高度与关节电机/减速器同看","红豆情绪、方正先手、巨轮容量；要求同一时段群体确认")}
    for s in summary["cases"]:
        date=s["feature_date"];folder=OUT/(date+"_"+s["theme"]); focus,role=case_text[date]
        lines.append(f'|{link(date+" "+s["theme"],folder/"归因与候选.md")}|{focus}|{role}|{s["reviewed_n"]}/{s["pool_n"]}|')
    lines+=['','这里是原标签内部的角色比较，不是全市场主线排序。部分成员尚未定性，因此不能给出完整的“纠正后题材宽度”，也不能通过删掉待定成员提高修复比例。','',
            '例如，5月20日兆易创新在原26只中的成交额约265.24亿元，却是首板且14:37:52才封板；3板诚邦、合百在09:31附近已封板。容量与接力高度必须分列。6月23日巨轮与方正成交额分别约6.46亿、6.33亿，接近而非悬殊；方正先于巨轮封板，也不证明它带动了巨轮。','',
            '## 5. 可直接执行的归因流程','',
            '1. 冻结时点及股票池，保存原主归因、多标签、涨停原因和历史描述，避免把后来的赢家添加进来。',
            '2. 把新闻写成“谁—做了什么—涉及什么产品/客户—处于什么阶段—规模多少”的事实命题；记录时间、来源、限定和反证。',
            '3. 分辨上市公司、控股子公司、参股公司、投资基金、股东关联方。关系不明就保留未核实，不用集团关联代替上市公司持股。',
            '4. 分开标注市场叙事和经济关联。缺主营收入仍可能有情绪角色；有主营收入仍需验证同群体同步和持续性。',
            '5. 在同一事件内比较高度、先手、换手与容量角色，给候选及可撤销条件。没有产业高度候选时允许空缺，不强制从首板中选龙头。',
            '6. 原群体、事件分支、个股独立事件与未定成员分别报告。多重归因保留，合并计数必须去重。',
            '7. 先固定这些判断，再评价之后的状态与价格；已经读过未来的重建不得充当准确率样本。','',
            '## 6. 覆盖、复核与限制','',
            f'- 四个初筛新闻窗口各从P前14个自然日起至下一交易日06:00，共{sum(json.loads(p.read_text())["news_rows"] for p in OUT.glob("202*_*/input_packet.json"))}条窗口内记录，窗口重叠，不是去重总量；只有29个选定ID被本报告逐条引用，不能把抓取数量称为已理解数量。',
            '- 20条股票记录有本轮选定新闻的语义判断；32条仍是标签或描述线索。每条保留在52条总表中，缺新闻也不移除。较早已读事实可显式补入，并重新校验本例截止时点。',
            '- 29条所选新闻均通过各引用案例的截止时点与公司名称校验。名称命中只是技术检查，命题主体及用途由阅读注释确认；它本身不能证明相关性或事实为真。',
            '- KPL的榜单、表现和概念描述来自同一来源体系，不能算三个独立确认。新闻多为媒体转述，未逐条追溯交易所原公告；历史概念描述可能滞后、修订或回填。',
            '- 原KPL榜单仍采用下一交易日06:00可见假设，缺少首发存档；按来源datetime截断不能证明信息当时确已可得。',
            '- 52条KPL成交额与日线成交额转换为同一单位后，差异均小于0.1%；v1预测、标签和冻结模型哈希保持不变。时点隔离、错误公司证据拒绝等3项测试通过。',
            '- 本轮尚未验证候选能否预测未来主线或龙头，不能宣称准确率提升。也未覆盖未涨停同题材公司、完整盘口或09:25竞价。','',
            link('全部52条归因记录',OUT/"all_52_attributions.csv")+'；'+link('29条已引用新闻及来源',OUT/"reviewed_news.json")+'；'+link('覆盖与复核结果',OUT/"summary.json"),'',
            '## 7. 产出与后续验证','',
            link('研究技能',ROOT/"policyStudy/skills/a-share-theme-research/SKILL.md")+'已加入题材归因与角色分离的具体规范。'
            +'重建判断保存在'+link('四例时点判断',decisions_path)+'，逐条阅读记录保存在'+link('归因注释',BASE/"research/attribution_reviews_v1.json")+'。','',
            '复现顺序：项目根目录用`.venv/bin/python`依次运行策略scripts目录的`build_attribution_inputs.py`、`audit_attribution.py`、`render_attribution_report.py`。新读取均经data_provider的database_only；不会改写原行情库或v1预测。','',
            '下一轮先将剩余未定成员按证据缺口补齐，再冻结事件归属与评分口径，生成逐日情绪—题材—角色判断。真正考核需要未参与修改规则的新时段；少数精读案例只能形成方法，不能代替全样本检验。']
    path=ROOT/"reports/题材归因与龙头角色研究_v1.md"
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(path)


if __name__=="__main__":main()
