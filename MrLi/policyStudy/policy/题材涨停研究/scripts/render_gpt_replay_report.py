"""Render all frozen decisions and evaluation, never run inside a judge."""
import hashlib
import json
from pathlib import Path

from build_case_dossiers import BASE, ROOT, save

OUT=BASE/'data/gpt_replay_v2'


def rate(metric):
    return '无样本' if not metric['n'] else f"{metric['accuracy']:.1%}（{metric['correct']}/{metric['n']}）"


def clean_cell(text):return str(text or '未定').replace('|','／').replace('\n',' ')


def main():
    metrics=json.loads((OUT/'metrics.json').read_text());ledger=json.loads((OUT/'daily_ledger.json').read_text())
    manifest=json.loads((OUT/'prediction_manifest.json').read_text());inputs=json.loads((OUT/'input_manifest.json').read_text())
    assert metrics['provenance']['predictions_sha256']==manifest['predictions_sha256']
    assert hashlib.sha256((OUT/'frozen_predictions.json').read_bytes()).hexdigest()==manifest['predictions_sha256']
    s=metrics['overall'];keys=[
        ('未来3日主线题材，第一候选','mainline_future3_top1'),
        ('未来3日主线题材，前三候选含正确答案','mainline_future3_top3'),
        ('所选题材内未来两日最强股，至少2候选','leader_selected_theme_return_multi'),
        ('题材选对后再选中最强股，至少2候选','leader_when_future3_mainline_correct_return_multi'),
        ('题材与最强股同时选对','mainline_and_leader_end_to_end_strict'),
        ('次日已知涨停篮子可做性，冷/中/热','emotion'),
        ('原池后续高度第一，辅助口径','leader_height_secondary')]
    lines=['# GPT逐日历史时点回放：2026年1—8月','',
           '本轮由三个不继承研究对话的独立GPT判断器完成全部160日判断。每次只通过门控获取当前日截至时点的数据，先冻结判断，再进入下一日；全部判断冻结后才由独立程序读取未来标签评分。', '',
           '**这是限制输入时点的历史回放。** 1—8月已经用于形成方法，不能再称为未经使用的样本；下列准确率针对事先固定、可重复计算的代理目标，不能等同于公认真龙头识别率、实盘胜率或未来保证。', '',
           '## 本轮准确率','', '| 指标 | 命中率 | 标签覆盖 |','|---|---:|---:|']
    for title,key in keys:
        m=s[key];coverage='—' if m['coverage'] is None else f"{m['known_n']}/{m['n']}"
        lines.append(f'| {title} | {rate(m)} | {coverage} |')
    lines+=['',f"另列下一交易日主线命中：{rate(s['mainline_nextday_top1'])}。单候选题材的个股命中为{rate(s['leader_selected_theme_return_singleton'])}，不并入至少2候选的选股成绩。", '',
            '“所选题材内”不要求题材选对；“题材选对后”才是真正的条件准确率。整条链条以所有完整未来窗口日为分母，题材错即失败，不能只展示局部较高的选股率。', '',
            '**当前方法未达到95%/95%/100%的目标。** 主线命中只比当日宽度基线多4日；个股命中少于同题材最早首封基线。此次回放不足以证明新闻语义判断带来了稳定的预测增益，也不能据此声称“已经学会”。', '',
            '## 评分标准在判断前固定','',
            '- **主线**：未来三个交易日，开盘啦主归因下累计涨停股日数最多的题材；并列均接受，未来新出现题材也参与真值。它衡量宽题材持续广度，不是人工认定的事件主线。',
            '- **最强股代理**：判断日该题材已经涨停的候选中，未来两日复合超额收益第一；不是从全题材未涨停成员中找启动龙，也不能直接代表对后排的带动。沿用前版冻结标签计算，未事后换定义。',
            '- **情绪代理**：已知涨停篮子次日相对沪深300的中位超额和大跌比例，按预定冷/中/热条件评分。当前情绪及生命周期仍没有独立人工真值，不能另报一个主观“准确率”。',
            '- **高度辅助**：原池后续榜单高度第一，允许并列；此代理也可能只有首板，且10%/20%混合，不能把其命中率当作真正龙头正确率。', '',
            '## 与简单基线比较','',
            '| 比较任务 | GPT | 简单基线 |','|---|---:|---:|',
            f"| 未来3日主线 | {rate(s['mainline_future3_top1'])} | 当日宽度第一：{rate(s['baselines']['mainline_breadth']['mainline_future3_top1'])} |"]
    for title,role in [('原池最高板','height'),('原池最早首封','early'),('原池成交额最大','capacity')]:
        b=s['baselines']['selected_theme_leader_'+role]
        lines.append(f"| 同一所选题材内的两日最强股 | {rate(s['leader_selected_theme_return_multi'])} | {title}：{rate(b['leader_selected_theme_return_multi'])} |")
    lines+=['','个股基线使用GPT所选的同一题材原池，因此分母可直接核对；同时还保存宽度第一题材搭配三种个股基线的完整结果，见metrics.json。高度并列基线按代码排序破同分，这也是先固定的规则。比较的是本轮观察值，不据此宣称独立样本中的预测优势。', '',
            '## 按月份拆分','',
            '| 月份 | 判断日数 | 主线第一候选 | 主线前三 | 所选题材内最强股 | 题材和股同时正确 |','|---|---:|---:|---:|---:|---:|']
    for month,m in metrics['by_month'].items():
        lines.append(f"| {month[:4]}-{month[4:]} | {m['days']} | {rate(m['mainline_future3_top1'])} | {rate(m['mainline_future3_top3'])} | {rate(m['leader_selected_theme_return_multi'])} | {rate(m['mainline_and_leader_end_to_end_strict'])} |")
    diag=s['diagnostics'];em=s['emotion']
    lines+=['','## 拒判、数据缺口与时点限制','',
            f"- 共160日，末尾缺1/2/3个交易日的后续观察，因此次日、两日、三日窗口分别有{s['immature']['future1']}/{s['immature']['future2']}/{s['immature']['future3']}日未成熟，不读取9月结果，也不把未成熟算错。",
            f"- 主线拒判{diag['mainline_abstention']}日，个股拒判{diag['leader_abstention']}日；所选题材两日价格真值缺失{diag['selected_theme_price_truth_missing']}日，仍保留严格分母。",
            f"- 完整三日窗口中，真实未来主线完全不在当日候选题材池的日期有{diag['true_mainline_absent_from_original_pool']}日；这部分属于当前候选范围的限制，照样计主线未命中。当前范围下全样本top1的理论上限为{(s['mainline_future3_top1']['n']-diag['true_mainline_absent_from_original_pool'])/s['mainline_future3_top1']['n']:.1%}，因此仅在当日已涨停题材中选，连理论上也达不到95%。后续扩展必须使用当时已经可知的候选，不能用未来题材倒填。",
            f"- 高度标签出现并列赢家{diag['height_secondary_tied_winners']}日（全两日成熟原池诊断口径），不要用较宽松的并列高度命中替代最强股主指标。",
            f"- 本轮检索原始多源新闻{inputs['raw_news_rows']:,}条；记录量不是逐条人工阅读量，转载不是多个独立证据。接口返回的新闻全文按日去重共{sum(x['news_full_read_requests_total'] for x in manifest['judge_sessions'])}条次；接口返回量不能证明模型已逐条阅读。",
            '- 新闻窗口截至下一交易日06:00，最后一包新闻封顶8月31日23:59:59。KPL最终榜单、历史新闻版本没有首发存档；这是按来源字段重建的信息可用性假设。',
            '- 门控访问日志验证逐日顺序、候选池、引用时点与冻结文件指纹。子代理共享文件系统，逻辑访问限制不等于独立操作系统沙箱，也无法证明预训练知识从未见过相关历史。',
            '- 输入包括全部当日合格涨停候选、此前结构、公司历史新闻检索及本期主题新闻。公司归因仍是有限检索，未覆盖全市场题材成员，未提供09:25竞价、盘中封单和全天分钟确认；本轮只报告盘前能力。', '',
            '## 全文判读流程审计','',
            'A组报告20260227、20260306、20260309、20260311、20260312存在部分全文查询与提交同批执行：模型在冻结前没有看到这些新增全文，只能使用此前已经看到的数据、摘要和其他原文。20260311此前另有五条全文在独立调用中读取。五日不重填、不剔除，全部保留在主成绩内。B组前26日因上下文压缩无法复核是否同批；其后28日确认分别调用。门控日志只能证明接口调用顺序，不能单独证明模型看到了每次返回；各组自查范围与限制见[流程记录]('+str(OUT/'judge_process_incidents.json')+')。','',
            '## 情绪代理混淆矩阵','',
            '| 真实类别＼预测类别 | cold | neutral | hot | 拒判 |','|---|---:|---:|---:|---:|']
    for i,label in enumerate(em['confusion_labels']):lines.append(f"| {label} | {' | '.join(str(v) for v in em['confusion_matrix'][i])} | {em['abstention_column'][i]} |")
    ba='未知' if em['balanced_accuracy'] is None else f"{em['balanced_accuracy']:.1%}"
    majority='未知' if em['majority_baseline_known_truth'] is None else f"{em['majority_baseline_known_truth']:.1%}"
    lines+=['',f'各类别平均召回率为{ba}；永远猜样本最多一类的事后参照为{majority}。后者不是可交易策略。Wilson区间保存在结果文件中，未校正交易日之间的相关性。', '',
            '## 错误类型核对','',
            '下表每类取时间上第一个符合条件的日期；同一日可以属于多类。所有日期仍保留在主成绩与逐日表中。', '',
            '| 类型 | 日数 | 首个日期 | 当时预测 | 未来代理真值 |',
            '|---|---:|---|---|---|']
    cases=[
        ('题材错，但所选题材内个股对',lambda r:r['measures']['mainline_future3_top1']['status']=='miss' and r['measures']['leader_selected_theme_return_multi']['status']=='hit'),
        ('题材对，但个股错',lambda r:r['measures']['mainline_future3_top1']['status']=='hit' and r['measures']['leader_selected_theme_return_multi']['status']=='miss'),
        ('前三题材也未覆盖真值',lambda r:r['measures']['mainline_future3_top3']['status']=='miss'),
        ('未来主线完全不在当日题材池',lambda r:r['diagnostics']['true_mainline_absent_from_original_pool'] is True),
        ('所选题材价格标签缺失',lambda r:r['diagnostics']['selected_theme_price_truth_missing'])]
    for title,test in cases:
        rows=[r for r in ledger if test(r)]
        if not rows:continue
        r=rows[0];p=r['prediction'];chosen=r['truth']['leaders'].get(p['mainline'])
        stocks='、'.join(chosen['winners']) if chosen else '价格真值缺失'
        lines.append(f"| {title} | {len(rows)} | {r['date']} | {clean_cell(p['mainline'])} / {clean_cell(p['leader'])} | 主线：{clean_cell('、'.join(r['truth']['mainline']))}；所选题材股：{clean_cell(stocks)} |")
    lines+=['','## 下一轮应验证什么','',
            '先补当时可知的事件题材与成员池，并建立独立人工标注来区分“情绪状态”“接力可做性”“高度核心”“带动性龙头”和“未来收益冠军”。随后在同一候选池、同一日期上对照结构判断与加入新闻后的GPT判断，验证新闻的实际增量；在本轮用过的1—8月只做开发和错题研究，达标验证另留未使用时段。本轮不改目标、不调参、不重填答案。','',
            '## 全部逐日判断与错题','']
    for month in metrics['by_month']:
        days=[r for r in ledger if r['date'].startswith(month)]
        body=['# '+month+' GPT逐日判断与核对','',
              '预测原文先于评分冻结，以下真值列只在评分后加入；它们不能再提供给历史判断器。','',
              '| 日期 | 主线预测 | 未来3日主线代理 | 主线命中 | 个股预测 | 所选主题内赢家 | 整链 |',
              '|---|---|---|---|---|---|---|']
        for r in days:
            p=r['prediction'];truth=r['truth'];chosen=truth.get('leaders',{}).get(p['mainline'])
            body.append(f"| {r['date']} | {clean_cell(p['mainline'])} | {clean_cell('、'.join(truth['mainline']))} | {r['measures']['mainline_future3_top1']['status']} | {clean_cell(p['leader'])} | {clean_cell('、'.join(chosen['winners']) if chosen else '')} | {r['measures']['mainline_and_leader_end_to_end_strict']['status']} |")
        for r in days:
            p=r['prediction'];body+=['',f"## {r['date']} 判断原文",'',
                f"当前情绪：{p['state_now']}；下日情绪预测：{p['emotion_prediction']}；题材阶段：{p['phase']}；置信：{p['confidence']}。",'',
                f"主线候选：{'、'.join(p['top3']) or '拒判'}；个股：{p['leader'] or '拒判'}；替代：{'、'.join(p['alternatives']) or '无'}。",'',
                p['reasoning'],'',f"失效条件：{p['invalidation']}",'',f"证据缺口：{p['evidence_limits']}",'',
                f"冻结时间：{p['frozen_at']}；输入指纹：`{p['packet_sha256']}`。",'',
                f"新闻ID：{'、'.join(p['evidence_ids']) or '仅结构证据'}；[当时完整数据包]({OUT/'packets'/(r['date']+'.json')})。"]
        path=OUT/(month+'_逐日判断.md');path.write_text('\n'.join(body)+'\n')
        lines.append(f'- [{month[:4]}年{month[4:]}月：完整判断与核对]({path})')
    lines+=['','## 复核入口','',
            f'- [冻结的160份GPT判断]({OUT/"frozen_predictions.json"})',
            f'- [冻结清单及访问审计]({OUT/"prediction_manifest.json"})',
            f'- [全部指标、分母与基线]({OUT/"metrics.json"})',
            f'- [逐日预测与真值合并表]({OUT/"daily_ledger.json"})',
            f'- [事前评分协议]({BASE/"research/gpt_replay_protocol_v2.json"})','',
            '原v1模型、预测和标签未改写。本轮不根据结果调参或重填判断。']
    path=ROOT/'reports/GPT逐日时点回放_2026年1至8月_v2.md';path.write_text('\n'.join(lines)+'\n')
    save(OUT/'report_manifest.json',{'report_path':str(path),'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                                    'prediction_sha256':manifest['predictions_sha256'],'scoring_sha256':hashlib.sha256((OUT/'metrics.json').read_bytes()).hexdigest()})
    print(path)


if __name__=='__main__':main()
