"""Read already-saved groups, then attach descriptive future-price feedback."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from build_case_dossiers import BASE, save
from supplement_attribution import OUT
from audit_attribution_v2 import protected_hashes

ROOT=Path(__file__).resolve().parents[4]
METRICS=['d_pct','t_pct','t_since_p_pct','u_since_p_pct']


def feedback_for_pool(codes, outcomes):
    frame=pd.DataFrame(outcomes).set_index('ts_code')
    assert frame.index.is_unique and len(codes)==len(set(codes))
    selected=frame.reindex(codes)
    metrics={}
    for key in METRICS:
        values=pd.to_numeric(selected[key],errors='coerce')
        complete=bool(values.notna().all())
        metrics[key]={'expected':len(codes),'covered':int(values.notna().sum()),
                      'median_pct':float(values.median()) if complete else None,
                      'positive_n':int((values>0).sum()),'complete':complete}
    return metrics


def fmt(x):return '未知' if x is None else f'{x:+.2f}%'


def link(path,label):return f'[{label}]({path})'


def main():
    summary=json.loads((OUT/'summary.json').read_text())
    assert hashlib.sha256((OUT/'fixed_groups.json').read_bytes()).hexdigest()==summary['fixed_groups_sha256']
    assert hashlib.sha256((OUT/'all_52_attributions.json').read_bytes()).hexdigest()==summary['ledger_sha256']
    groups=json.loads((OUT/'fixed_groups.json').read_text())
    rows=json.loads((OUT/'all_52_attributions.json').read_text())
    news={r['md5']:r for r in json.loads((OUT/'reviewed_news.json').read_text())}
    # This is the first future-price read in the v2 group workflow, not a blind test.
    events=json.loads((BASE/'data/divergence_v1/event_details.json').read_text())
    by_case={(e['summary']['p_date'],e['summary']['theme']):e for e in events}
    feedback=[]
    for g in groups:
        e=by_case[g['date'],g['theme']]
        feedback.append({'group_id':g['id'],'date':g['date'],'name':g['name'],'kind':g['kind'],
                         'members':g['members'],'metrics':feedback_for_pool(g['members'],e['stock_outcomes'])})
    save(OUT/'group_feedback.json',feedback)
    lines=['# 题材成员补查与事件分组 v2','',
           '本轮完成前次剩余32条股票—日期归因调查，并保留原52条名单。补查完成不等于32条都已证实，更不是龙头判断准确率。', '',
           '## 本轮查到了什么','',
           '| 补查结论 | 条数 | 解释 |','|---|---:|---|',
           '| 关联有支持 | 15 | 业务或资本关系有当时公司表述的转述支持，仍受阶段和规模限制 |',
           '| 仅市场叙事 | 8 | 曾被行情稿列入方向，不能据此核实商业关系和本次催化 |',
           '| 局部事实 | 5 | 已找到公司事实，题材关联仍缺关键连接 |',
           '| 仍未定 | 4 | 当前检索不足以确认，不推断无业务 |','',
           f'通过data_provider的database_only接口，按周读取2026年1月1日至最晚案例截止时点的{summary["news_records_retrieved"]:,}条新闻，再按各股票的当时简称和已见历史简称筛选、截断。该数字是检索记录量，不是逐条语义阅读量，也不是独立信息源数量。', '',
           f'32条中28条附有本轮选读证据，24条使用了旧14天窗口之外的证据。合并前一版及分组引用后保留{len(news)}个不同新闻ID；转载、重复内容及同一公告的多家转述不能算独立确认。4条未定仍保留在原池及覆盖率分母。', '',
           '材料属于本地历史新闻档案。公司事实主要来自公告、互动问答或说明会的媒体转述，本轮没有把所有转述逐一回到交易所公告原件复核。发布时间按数据字段截断；档案抓取时间不能证明当时已经取得该版本。', '',
           '## 影响判断的实质区别','',
           '1. **把长期关系与新催化分开。** 联芸的存储主控出货、露笑的碳化硅样品，较早报道可以补全业务认识，不能当成5月新增订单。每家公司需要随历史时间更新的关系档案，每次行情另建催化记录。',
           '2. **资本关系与产品关系分开。** 锋龙的优必选持股关系有支持；公司在6月8日同时明确无当前人形机器人业务、未向优必选供货。它可以是情绪映射观察对象，却不是凭这条关系就能认定的产品订单核心。',
           '3. **同一事件可以有不同受益路径。** 长鑫行情中的合百、兆易、柏诚不必拥有相同主营。柏诚明确无长鑫持股，不能因此排除客户链的可能性，也不能仅靠行情报道就确认合同。',
           '4. **旧标签可以滞后。** 和顺出现高速接口IP控制权交易，凯伦出现已并表收购，不能永远按旧石油或防水标签判断；反过来，拟收购、受理、表决权安排也不能自动写成已经完成并表。',
           '5. **情绪核心和产业核心分别评估。** 对关系有疑问的高标保留其价格与高度记录，不用“业务不纯”删除；产业角色则允许未知。', '',
           '## 将宽标签拆成可检查的观察组','',
           '以下六组是在已看过历史的情况下重建并保存的观察示例。先保存成员与证据，再由单独步骤读取后续价格。程序顺序不消除研究者已经知道历史的影响；不能称作预注册或独立盲评。', '',
           '| P日 | 观察组 | 性质 | 固定成员 |','|---|---|---|---|']
    for g in groups:
        names=[next(r['name'] for r in rows if r['feature_date']==g['date'] and r['ts_code']==c) for c in g['members']]
        lines.append(f'| {g["date"]} | {g["name"]} | {g["kind"]} | {"、".join(names)} |')
    lines+=['','“同日市场叙事”只说明当时有共同归类，并不证明共同上涨因果；“近期事件叙事”明确标出较早日期；“业务关系示例”不能直接当作本次事件宽度。', '',
            '| P日 | 原宽标签成员 | 示例组去重覆盖 | 尚未纳入示例组 |','|---|---:|---:|---:|']
    for c in summary['case_group_coverage']:
        lines.append(f'| {c["date"]} | {c["original_n"]} | {c["example_group_union_n"]} | {c["ungrouped_n"]} |')
    lines+=['','5月20日三组有成员重叠，组人数相加为8，去重只有6。6月16日没有在本轮材料上强行凑出一个共催化组。未纳入示例组不等于排除出题材，不给“完整纠正后的宽度”。', '',
            '## 固定成员之后，后续价格如何反馈','',
            'P为建池日，D为原宽标签机械判定的下一交易日分歧日，T/U为再后两个交易日；不表示每个子组都发生了分歧。沿用前轮逐股未复权收盘价与当日参考昨收的价格比序列，先算每股再取等权中位数；不平均板高、不把20%板与10%板视为同一高度。这里是价格观察，不是可成交收益，P收盘价也不是本研究可执行的入场价。', '',
            '| P日/池 | 人数 | D当日中位数 | T当日上涨数 | T当日中位数 | 截至T累计中位数 | 截至U累计中位数 |',
            '|---|---:|---:|---:|---:|---:|---:|']
    for key in sorted({(r['feature_date'],r['primary_theme']) for r in rows}):
        e=by_case[key];s=e['summary'];m=feedback_for_pool(e['pool'],e['stock_outcomes'])
        def tab(label,metrics,n):
            return f'| {key[0]}/{label} | {n} | {fmt(metrics["d_pct"]["median_pct"])} | {metrics["t_pct"]["positive_n"]}/{n} | {fmt(metrics["t_pct"]["median_pct"])} | {fmt(metrics["t_since_p_pct"]["median_pct"])} | {fmt(metrics["u_since_p_pct"]["median_pct"])} |'
        lines.append(tab('原池',m,len(e['pool'])))
        for g in groups:
            if (g['date'],g['theme'])==key:
                f=next(f for f in feedback if f['group_id']==g['id'])
                lines.append(tab(g['name'],f['metrics'],len(g['members'])))
    lines+=['','所有六组均列出，未按好坏删除；所列每日价格覆盖完整。每组仅2—3只，既非全行业样本，也不是独立统计检验。中位数修复不代表每只股票修复；不同组异向时，应保留分支差异，不能用总榜单数量替代原成员反馈。', '',
            '5月14日建池后，D日碳化硅三股中位数−2.90%，工业气体两股却为＋6.57%。工业气体组在D并没有中位价格受损，不能因为宽标签进入分歧，就给该组套用“分歧后修复”的同一判断。应先确认观察对象，再判断它自己的生命周期。', '',
            '另一面，5月20日存储产品关系组截至U中位数−4.63%，较早IPO行情映射组为＋8.60%。组小、重叠且经过历史选取，不能估计谁有预测优势；它至少提醒研究者不要预设“业务关联越直接，短期表现必然越强”。', '',
            '这些结果只用于检查分组之后能看清什么，不把“更纯的组涨得更好”设为结论，也不声称已证明分组改善预测。', '',
            '## 每个案例的完整成员表与证据','']
    for date in sorted({r['feature_date'] for r in rows}):
        case=[r for r in rows if r['feature_date']==date];cutoff=case[0]['decision_at']
        folder=OUT/(date+'_'+case[0]['primary_theme']);folder.mkdir(exist_ok=True)
        detail=['# '+date+' 成员补查与事件分组','',f'历史判断截止：{cutoff}。保留原{len(case)}只股票。','',
                '| 股票 | 原板高 | 归因分支 | 本轮状态 | 依据与限制 |','|---|---:|---|---|---|']
        for r in case:
            status=r.get('supplement_review',{}).get('status','前版已核验；本轮保留')
            detail.append(f'| {r["name"]} {r["ts_code"]} | {r["height"]} | {r["branch"]} | {status} | {r["judgment"]} {r["invalid_inference"]} |')
        for r in case:
            detail+=['',f'## {r["name"]}：证据定位','']
            if not r['evidence']:detail.append('本轮无足以确认题材关联的选定证据；检索命中不等于没有新闻，也不等于关系为零。')
            for evidence in r['evidence']:
                n=news[evidence['id']]
                detail.append(f'- {n["datetime"]} / {n["src"]} / {n.get("title") or "无标题快讯"}；记录ID `{n["md5"]}`。')
        for g in groups:
            if g['date']==date:detail+=['',f'## 观察组：{g["name"]}','',g['note']]
        detail+=['','原始选读内容见 '+link(OUT/'reviewed_news.json','新闻证据汇总')+'。新闻转述不是原公告逐一核实。']
        dest=folder/'补查与分组.md';dest.write_text('\n'.join(detail)+'\n')
        lines.append('- '+link(dest,f'{date}：{len(case)}只完整名单、分支与新闻时间'))
    lines+=['','## 写入方法及下一步评估','',
            '日常判断应输出三张互相校验的表：公司关系表（主体、阶段、规模、反证和生效日期）、本次事件表（催化、共同成员、首次可见时间和待定关系）、价格反馈表（原池与新增扩散池分列）。旧消息用于背景，新消息用于是否有增量，价格用于验证市场是否认同。', '',
            '之后需要在固定截止时点，让AI先写出情绪、主线及角色候选与失效条件，再保存原文，最后读取下一截面评分。对同一材料比较“仅宽标签”和“加入语义归因”两种方法，保留所有日期、拒判及缺失，才能知道这次改进有没有提高判断能力。1—8月已经被研究，不再当作未见测试；当前没有新的95%/95%/100%达标结果。', '',
            '候选证据若仍只来自行情稿，就明确写“市场叙事候选”；有业务证据但没有当前催化时，明确写“业务相关、当次事件未定”。不为给出唯一龙头而把未知补成肯定。', '',
            '## 可复核文件','',
            '- '+link(OUT/'all_52_attributions.json','完整52条台账'),
            '- '+link(BASE/'research/attribution_supplement_reviews_v2.json','32条人工补查结论'),
            '- '+link(OUT/'fixed_groups.json','读取后续价格前保存的六组成员与证据'),
            '- '+link(OUT/'group_feedback.json','六组完整价格反馈与覆盖分母'),
            '- '+link(OUT/'retrieval_manifest.json','查询范围、记录量及逐股截止时点'),
            '- '+link(OUT/'summary.json','核验摘要与文件指纹'),
            '- '+link(ROOT/'policyStudy/skills/a-share-theme-research/SKILL.md','研究技能'),'',
            '原v1模型、逐日预测和标签文件指纹均保持不变。']
    report=ROOT/'reports/题材成员补查与事件分组_v2.md'
    report.write_text('\n'.join(lines)+'\n')
    assert all(m['complete'] for f in feedback for m in f['metrics'].values())
    assert all(protected_hashes().values())
    print(report)
    for f in feedback:print(f['name'],{k:v['median_pct'] for k,v in f['metrics'].items()})


if __name__=='__main__':main()
