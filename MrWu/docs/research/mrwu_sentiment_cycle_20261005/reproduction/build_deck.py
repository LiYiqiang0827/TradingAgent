"""Free, database-free PDF rebuild from the published small research tables."""
from pathlib import Path
import argparse, io, json, textwrap
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader

NAVY='#14213D'; PAPER='#F7F3EA'; RED='#D85C41'; TEAL='#2A9D8F'; GRAY='#667085'
GRADES=['D','C','B','A','S']; GCOL=['#4777B8','#B0BCCB','#E9C46A','#2A9D8F','#D85C41']
PHASES=['冰点','退潮','启动候选','修复','分歧','扩散','强势','中性/混沌']
PCOL=['#4777B8','#9C3B36','#77BBAE','#2A9D8F','#D89059','#DFAC39','#D85C41','#A2AAB6']
URL='https://github.com/LiYiqiang0827/TradingAgent/tree/MrWu/docs/research/mrwu_sentiment_cycle_20261005'

class Deck:
    def __init__(self,out,font,draft_label=''):
        pdfmetrics.registerFont(TTFont('CN',str(font),subfontIndex=0));font_manager.fontManager.addfont(str(font));family=font_manager.FontProperties(fname=str(font)).get_name()
        plt.rcParams.update({'font.family':family,'axes.unicode_minus':False,'font.size':14,'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#ABB2BC','axes.labelcolor':NAVY,'text.color':NAVY,'xtick.color':NAVY,'ytick.color':NAVY,'figure.facecolor':'white','axes.facecolor':'white','savefig.dpi':160})
        self.c=Canvas(str(out),pagesize=(1280,720),pageCompression=1,invariant=1);self.c.setTitle('A股接力情绪与周期研究 2026年1—9月');self.c.setAuthor('Project Rocket');self.n=0;self.pages=[];self.draft_label=draft_label
    def text(self,t,x,y,size=24,color=NAVY):
        self.c.setFont('CN',size);self.c.setFillColor(HexColor(color));self.c.drawString(x,720-y,str(t))
    def wrap(self,t,x,y,width,size=24,line=1.5,color=NAVY):
        s=''; yy=y
        for ch in str(t):
            if ch=='\n' or pdfmetrics.stringWidth(s+ch,'CN',size)>width:
                self.text(s,x,yy,size,color);yy+=size*line;s='' if ch=='\n' else ch
            else:s+=ch
        if s:self.text(s,x,yy,size,color);yy+=size*line
        return yy
    def page(self,title,source,n,subtitle=''):
        if self.n:self.c.showPage()
        self.n+=1;self.c.setFillColor(HexColor(PAPER));self.c.rect(0,0,1280,720,fill=1,stroke=0);self.c.setFillColor(HexColor(RED));self.c.rect(0,0,12,720,fill=1,stroke=0)
        self.wrap(title,64,75,1150,32,1.2)
        if subtitle:self.text(subtitle,64,117,18,GRAY)
        self.c.setStrokeColor(HexColor('#CBD0D5'));self.c.line(64,584,1216,584)
        self.text(f'描述性研究 | 2026-01-01 至 2026-09-30 | N={n} | {source}',64,681,12,GRAY);self.text(f'{self.n:02}',1190,681,14,RED)
        if self.draft_label:self.text(self.draft_label,64,706,13,RED)
        self.pages.append({'page':self.n,'title':title,'source':source,'N':n})
    def note(self,t):self.wrap(t,64,614,1150,18,1.35)
    def chart(self,fig,x=64,y=160,w=1152,h=410):
        stream=io.BytesIO();fig.savefig(stream,format='png',bbox_inches='tight',facecolor='white');plt.close(fig);stream.seek(0);self.c.drawImage(ImageReader(stream),x,720-y-h,width=w,height=h,preserveAspectRatio=True,anchor='c',mask='auto')
    def table(self,headers,rows,widths,x=64,y=172,rowh=38,size=20):
        headerh=min(rowh,46)
        self.c.setFillColor(HexColor(NAVY));self.c.rect(x,720-y-headerh,sum(widths),headerh,fill=1,stroke=0)
        xx=x
        for h,w in zip(headers,widths):self.text(h,xx+9,y+headerh*.68,size,'#FFFFFF');xx+=w
        y+=headerh
        for j,row in enumerate(rows):
            self.c.setFillColor(HexColor('#FFFFFF' if j%2==0 else '#EAEFF5'));self.c.rect(x,720-y-rowh,sum(widths),rowh,fill=1,stroke=0);xx=x
            for v,w in zip(row,widths):self.wrap(v,xx+9,y+size+8,w-18,size,1.18);xx+=w
            y+=rowh
    def end(self,out):
        self.c.save();(out.parent/'page_sources.json').write_text(json.dumps(self.pages,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[1]/'data');p.add_argument('--output',type=Path,required=True);p.add_argument('--font',type=Path,default=Path(__file__).parent/'fonts'/'RocketSansSC.ttf');p.add_argument('--draft-label',default='');a=p.parse_args();a.output.parent.mkdir(parents=True,exist_ok=True)
    def read(name):return pd.read_csv(a.data/name)
    f=read('environment_daily.csv');f.trade_date=pd.to_datetime(f.trade_date);mo=read('monthly_summary.csv');mods=read('monthly_modules.csv');tr=read('grade_transitions.csv');runs=read('runs.csv');events=read('event_windows.csv');evsum=read('event_summary.csv');rob=read('robustness_summary.csv');cases=read('case_selection.csv');cw=read('case_windows.csv');human=read('human_comparison.csv');rotation=read('monthly_rotation.csv');summary=json.loads((a.data/'analysis_summary.json').read_text(encoding='utf-8'))
    n=len(f);nv=int(f.score5.notna().sum());nt=int(f.theme_heat_score.notna().sum());d=Deck(a.output,a.font,a.draft_label)
    counts=f.grade5.value_counts();mean=f.score5.mean()
    d.page('A股接力情绪与周期：2026年1—9月', 'sentiment_daily.csv',n,'Project Rocket | 市场环境及策略研究接口 | F1-a5 / C0-v1')
    d.text(f'{nv}/{n}',64,245,72);d.text('交易日有正式五模块分',68,297,26);d.text(f'{mean:.1f}',695,245,72,TEAL);d.text('全期平均分 / 100',700,297,26)
    fig,ax=plt.subplots(figsize=(12,2.4));ax.bar(GRADES,[counts.get(g,0) for g in GRADES],color=GCOL);ax.set_ylabel('交易日数')
    for i,g in enumerate(GRADES):ax.text(i,counts.get(g,0)+.8,str(counts.get(g,0)),ha='center')
    d.chart(fig,y=353,h=210);d.note('本报告描述历史环境；2026年已用于项目研究。相位为探索性整理规则，尚未连接策略收益。')
    d.page('已有数据与复盘资产，现在补齐可计算的市场环境层','项目检讨v2；背景来源索引','背景')
    timeline=[('8月底—9月中旬','云端形成情绪→题材→个股→形态→执行的研究顺序'),('9月16—23日','本地数据接口、回放与复盘工具箱逐步形成'),('9月28日','题材每日复盘已有热度、结构和龙头候选'),('10月4—5日','以固定五模块和探索性相位建立统一环境日表')]
    for i,(date,txt) in enumerate(timeline):d.text(date,64,200+i*93,25,RED);d.wrap(txt,326,200+i*93,850,25)
    d.note('继承已有数据和研究基础设施；环境分层的价值仍须用固定策略、完整分母与新增证据检验。')
    d.page('环境日表为策略研究分组，接在选股与执行之前','METHOD_AND_DATA.md','方法')
    for i,(title,body) in enumerate([('情绪','接力意愿与失败惩罚'),('题材','主线、热度与结构'),('个股','候选与核心身份'),('形态','入场事件与失效条件'),('执行','成交、成本与风控')]):
        x=64+i*232;d.text(title,x,254,33,TEAL if i<2 else NAVY);d.wrap(body,x,315,194,24)
        if i<4:d.text('→',x+176,257,30,RED)
    d.wrap('输出：trade_date + score5 + grade5 + phase + theme_heat + structure + quality',64,464,1130,24)
    d.note('本次完成环境测量和描述研究；未修改交易执行，也未把高分转换为买入权限。')
    d.page('强弱、过程与题材结构需要三条独立信息','formal_spec.json；CYCLE_SPEC.md','方法')
    d.table(['维度','测量内容','不能由它单独推断'],[['强弱 score5','当日五模块的固定加权水平','接下来一定继续上涨'],['周期 phase','过去5日路径、改善宽度和惩罚变化','强制按圆圈依次演进'],['题材热度 / 结构','近120日相对热度与主线组织','市场接力者一定赚钱']],[210,520,420],rowh=98,size=24)
    d.note('同样60分可以来自低位改善，也可以来自高位回落。题材热度不进入正式五模块合成。')
    d.page('晋级与强势股反馈合计占正式分的一半','formal_spec.json；calibration.json',5)
    names=['M1 连板接力','M2 强势股反馈','M3 涨跌停结构','M4 中位股生态','M5 流动性'];weights=np.array([25,20,20,15,10])/90
    d.table(['模块','正式权重','指标含义'],[[names[i],f'{weights[i]*100:.1f}%',x] for i,x in enumerate(['分高度晋级率，a=5固定2025先验','昨日涨停/连板溢价及高标逐股反馈','涨停宽度×梯队、封板质量、跌停密度','昨日2—4板群体的失败惩罚','成交额/前20日均额；恐慌时上限50'])],[265,165,720],rowh=68,size=23)
    d.note('计算精确使用25/90、20/90、20/90、15/90、10/90；2025 P10/P90固定锚点，正反方向分别登记。')
    d.page(f'正式分覆盖{nv}/{n}日，数据近似另列质量字段','quality_daily.csv；data_coverage.csv',n)
    q=f.quality_status.value_counts().to_dict();missing=int(f.missing_prev_cohort_n.fillna(0).sum());excluded=f.excluded_n.sum()/((f.eligible_n+f.excluded_n).sum())
    d.table(['审计项目','本次结果'],[['正式五模块',f'{nv}/{n}日；分数缺失不由辅助分替代'],['题材对照',f'{nt}/{n}日；KPL首发和修订时间未验证'],['样本池排除',f'{excluded:.2%}股票日（ST、新股、特殊制度等合计）'],['昨日群体漏行',f'累计{missing}个成员日；停牌另列'],['质量类别','；'.join(f'{k}: {v}日' for k,v in q.items())]],[290,860],rowh=70,size=22)
    d.note('9/10与9月下旬按日修复；历史名称按有效区间识别。少量个股缺口局部排除，原始库保留不改。')
    ext=read('external_reconciliation.csv')
    d.page('外部10日对账区分生产样本池与复盘统计口径','external_reconciliation.csv',len(ext))
    rows=[]
    for _,r in ext.iterrows():rows.append([str(r.trade_date)[5:],f'{r.production_up:.0f} / {r.external_up:.0f}',f'{r.comparison_up:.0f}',f'{r.production_broken:.0f} / {r.external_broken:.0f}',f'{r.turnover_trillion:.3f} / {r.external_turnover_trillion:.2f}'])
    d.table(['日期','正式/外部涨停','宽口径涨停','正式/外部炸板','正式/外部成交万亿'],rows,[130,250,210,270,290],rowh=37,size=18)
    d.note('外部值来自原设计附录B转述，非独立真值。新股、退市、沪深京及盘中触板口径不同；原文矛盾保留在表中。')
    d.page('全期强弱反复切换，档位用于描述当日接力环境','sentiment_daily.csv',nv)
    fig,ax=plt.subplots(figsize=(13,4.5));bounds=[0,35,50,65,80,100]
    for i,col in enumerate(GCOL):ax.axhspan(bounds[i],bounds[i+1],color=col,alpha=.14)
    ax.plot(f.trade_date,f.score5,color=NAVY,lw=1.8);ax.set_ylim(0,100);ax.set_ylabel('正式 score5');ax.grid(axis='y',alpha=.2)
    for idx in [f.score5.idxmax(),f.score5.idxmin()]:r=f.loc[idx];ax.annotate(f'{r.trade_date:%m-%d}  {r.score5:.1f}',(r.trade_date,r.score5),xytext=(5,12),textcoords='offset points',fontsize=10)
    d.chart(fig);d.note('背景依次为D<35、C<50、B<65、A<80、S≥80。折线使用完整精度；显示取整不改变档位。')
    best=mo.loc[mo.score_mean.idxmax()];worst=mo.loc[mo.score_mean.idxmin()]
    d.page(f'{best.month[5:]}月均分最高，{worst.month[5:]}月最低：月份环境不可互换','monthly_summary.csv',nv)
    fig,ax=plt.subplots(figsize=(13,4.3));bottom=np.zeros(len(mo));xx=np.arange(len(mo))
    for grade,col in zip(GRADES,GCOL):ax.bar(xx,mo[grade+'_n'],bottom=bottom,label=grade,color=col);bottom+=mo[grade+'_n'].to_numpy()
    ax.set_xticks(xx,mo.month.str[5:]);ax.set_xlabel('2026月份');ax.set_ylabel('交易日数');ax.legend(ncol=5,loc='upper left',bbox_to_anchor=(0,1.13));twin=ax.twinx();twin.plot(xx,mo.score_mean,color=NAVY,marker='o');twin.set_ylim(0,100);twin.set_ylabel('月均 score5')
    d.chart(fig);d.note(f'最高月均{best.score_mean:.1f}，最低{worst.score_mean:.1f}。分组研究需控制月份构成，不能把月份差异直接称作收益过滤能力。')
    d.page('模块拆解显示同一总分可以有不同的形成原因','monthly_modules.csv',nv)
    pivot=mods.pivot(index='module',columns='month',values='mean').reindex(['M1','M2','M3','M4','M5']);fig,ax=plt.subplots(figsize=(13,4));im=ax.imshow(pivot,vmin=0,vmax=100,cmap='RdYlGn');ax.set_xticks(range(len(pivot.columns)),[m[5:] for m in pivot.columns]);ax.set_yticks(range(5),names)
    for i in range(5):
        for j in range(len(pivot.columns)):ax.text(j,i,f'{pivot.iloc[i,j]:.0f}',ha='center',va='center',fontsize=12)
    fig.colorbar(im,ax=ax,label='月均模块分');d.chart(fig)
    lag=read('feedback_lead_lag.csv');lag=lag[lag.feature_change_at_t.eq('M4')].set_index('score_change_offset')
    d.note(f'M4变化与同日/次日总分变化相关分别为{lag.loc[0,"pearson"]:.2f}/{lag.loc[1,"pearson"]:.2f}（N={lag.loc[0,"paired_n"]}/{lag.loc[1,"paired_n"]}）。共享公式，同日相关不能证明领先能力。')
    d.page('C0按当日证据识别过程，保留真实跳变','cycle_daily.csv；CYCLE_SPEC.md',n)
    fig,axs=plt.subplots(2,1,figsize=(13,4.5),gridspec_kw={'height_ratios':[1,3]});vals=[PHASES.index(x) if x in PHASES else np.nan for x in f.phase];from matplotlib.colors import ListedColormap
    axs[0].imshow([vals],aspect='auto',cmap=ListedColormap(PCOL),vmin=0,vmax=7);axs[0].set_yticks([]);axs[0].set_xticks(np.linspace(0,n-1,9).astype(int),f.trade_date.iloc[np.linspace(0,n-1,9).astype(int)].dt.strftime('%m-%d'))
    pc=f.phase.value_counts().reindex(PHASES,fill_value=0);axs[1].bar(PHASES,pc,color=PCOL);axs[1].set_ylabel('交易日数');axs[1].tick_params(axis='x',labelsize=11);fig.tight_layout();d.chart(fig)
    d.note(f'扩散{pc["扩散"]}日、强势{pc["强势"]}日：高分日常先命中修复/分歧等规则。优先级决定分类，不能把稀少标签解释成自然周期不存在；C0未按案例改阈值。')
    d.page('转移与持续长度描述反复程度，不构成择时胜率','grade_transitions.csv；runs.csv',int(tr['count'].sum()))
    mat=tr.pivot(index='from',columns='to',values='probability').reindex(index=GRADES,columns=GRADES);fig,axs=plt.subplots(1,2,figsize=(13,4.4));axs[0].imshow(mat,vmin=0,vmax=1,cmap='Blues');axs[0].set_xticks(range(5),GRADES);axs[0].set_yticks(range(5),GRADES);axs[0].set_xlabel('下一交易日');axs[0].set_ylabel('当日')
    for i in range(5):
        for j in range(5):axs[0].text(j,i,f'{mat.iloc[i,j]:.0%}',ha='center',va='center')
    rr=runs[runs.kind=='grade5'];axs[1].boxplot([rr[rr.state.eq(g)].duration for g in GRADES],tick_labels=GRADES,showmeans=True);axs[1].set_ylabel('连续交易日数');fig.tight_layout();d.chart(fig)
    longest=rr[rr.state.eq('D')].sort_values(['duration','start_date'],ascending=[False,True]).iloc[0]
    d.note(f'最长D段为{str(longest.start_date)[5:]}至{str(longest.end_date)[5:]}，连续{longest.duration}日。转移不跨缺口，边界段标删失；观测长度不是寿命预测。')
    same=read('same_score_direction.csv');sg=same[same.score_band.eq('50-65')&same.direction.isin(['上','下'])]
    d.page('同处50—65分区间，上升与下降日的模块结构仍可不同','same_score_direction.csv',int(sg.n.sum()))
    fig,ax=plt.subplots(figsize=(12,4));xx=np.arange(5)
    for j,(_,r) in enumerate(sg.iterrows()):ax.bar(xx+(j-.5)*.32,[r[m] for m in ['M1','M2','M3','M4','M5']],.32,label=f'{r.direction}：N={r.n}',color=[TEAL,RED][j%2])
    ax.set_xticks(xx,['M1','M2','M3','M4','M5']);ax.set_ylim(0,100);ax.set_ylabel('模块均分');ax.legend();d.chart(fig);d.note('方向取相邻交易日±2分；这不是严格匹配实验。模块本身参与总分，图中关系不能称为增量预测。')
    pan=events[events.event.eq('panic')];cluster=int(evsum.loc[evsum.event.eq('panic'),'consecutive_cluster_n'].iloc[0])
    d.page(f'恐慌放量共有{len(pan)}日，分布在{cluster}个连续事件簇','event_windows.csv；event_summary.csv',len(pan))
    fig,ax=plt.subplots(figsize=(12,4));cols=['score_t','score_t1','score_t3','score_t5'];xx=[0,1,3,5]
    for _,r in pan.iterrows():ax.plot(xx,[r[c] for c in cols],alpha=.25,color=GRAY)
    ax.plot(xx,[pan[c].mean() for c in cols],color=RED,lw=3,marker='o',label='可观测事件均值');ax.set_ylim(0,100);ax.set_xticks(xx);ax.set_xlabel('事件后市场交易日');ax.set_ylabel('score5');ax.legend();d.chart(fig);d.note(f'后1/3/5日完整窗口N={pan.score_t1.notna().sum()}/{pan.score_t3.notna().sum()}/{pan.score_t5.notna().sum()}。事件可重叠，缺少未来窗口保留空值；路径不代表买入收益或因果效应。')
    paired=f.dropna(subset=['score5','theme_heat_score']);corr=paired.score5.corr(paired.theme_heat_score)
    d.page(f'接力分与题材热度相关系数为{corr:.2f}，两者保留独立解释','environment_daily.csv',len(paired))
    fig,ax=plt.subplots(figsize=(12,4.4));sc=ax.scatter(paired.score5,paired.theme_heat_score,c=paired.trade_date.dt.month,cmap='viridis',s=32,alpha=.8);ax.plot([0,100],[0,100],ls='--',color=GRAY);ax.set(xlim=(0,100),ylim=(0,100),xlabel='接力 score5',ylabel='题材热度（近120日百分位）');fig.colorbar(sc,ax=ax,label='月份')
    diverge=paired.loc[(paired.theme_heat_score-paired.score5).idxmax()];ax.annotate(f'{diverge.trade_date:%m-%d}',(diverge.score5,diverge.theme_heat_score),xytext=(10,-22),textcoords='offset points',fontsize=12,color=RED,arrowprops={'arrowstyle':'->','color':RED})
    d.chart(fig);d.note(f'最大热度－接力分差日{diverge.trade_date:%m-%d}：热度{diverge.theme_heat_score:.1f}、接力{diverge.score5:.1f}。两种标尺不同且共享量价来源；Pearson仅作描述。')
    d.page('主线延续与更替提供题材结构，不能从总分反推出','monthly_rotation.csv',nt)
    d.table(['月份','有效日','第一题材种类','相邻更替次数','最常见第一题材'],[[r.month[5:],str(r.theme_valid_n),str(r.top1_distinct_n),f'{r.top1_replacements_n}/{r.adjacent_observed_n}',str(r.dominant_theme)] for r in rotation.itertuples()],[130,150,230,240,400],rowh=40,size=21)
    dc=read('direction_theme_structure.csv');up=dc[dc.direction.eq('上')].iloc[0]
    d.note(f'情绪上升日相邻第一题材更替{up.top1_changed_n}/{up.adjacent_observed_n}次；低档C/D而热度≥65共{int(dc.low_score_high_theme_n.sum())}日。题材排序换位不等同于产业主线替换。')
    def case_page(case_name,fallback,title):
        z=cases[cases['case'].eq(case_name)]
        if z.empty:z=cases[cases['case'].eq(fallback)]
        r=z.iloc[0];x=cw[cw['case'].eq(r['case'])];d.page(f'{str(r.trade_date)[5:]} {title}：{r.score5:.1f}分 / {r.phase}','case_selection.csv；case_windows.csv',len(x))
        fig,ax=plt.subplots(figsize=(12,4.3))
        for m,col in zip(['M1','M2','M3','M4','M5'],['#14213D',TEAL,RED,'#DFAC39','#4777B8']):ax.plot(x.relative_market_day,x[m],marker='o',label=m,color=col,lw=1.6)
        ax.axvline(0,color=NAVY,ls='--');ax.axvspan(.1,5.5,color='#D0D5DD',alpha=.3);ax.set(xlim=(x.relative_market_day.min()-.3,x.relative_market_day.max()+.3),ylim=(0,100),xlabel='相对事件日（右侧灰区是未来结果）',ylabel='模块分');ax.legend(ncol=5);d.chart(fig)
        day=f[f.trade_date.eq(pd.Timestamp(r.trade_date))].iloc[0];d.note(f'选择规则：{r.rule}。当日涨停{day.limit_up_n:.0f}、最高{day.max_height:.0f}板；题材：{str(day.top3_themes)[:42]}。')
    case_page('high_score','high_score','全期高分案例')
    case_page('repair_failure','longest_low','改善以后再次走弱')
    case_page('low_repair','longest_low','低位改善与启动候选')
    d.page('人工用语与机械相位的边界需要保留，而非强行对齐','human_comparison.csv',len(human))
    rows=[]
    for r in human.itertuples():rows.append([r.trade_date[5:],str(r.original_judgment)[:24],f'{r.score5:.1f} {r.grade5}',str(r.phase)])
    d.table(['日期','原文短摘录（对象：市场或另注）','正式分 / 档','C0相位'],rows,[130,560,210,250],rowh=33,size=17)
    boundary=human[human.trade_date.eq('2026-09-04')]
    if 'disagreement' in human and len(boundary):
        d.note(f'9/4当日上升{boundary.iloc[0].delta1:.1f}分仍为{boundary.iloc[0].phase}；9/14单日改善仍命中五日退潮。题材S-不当市场S；原文非认证前向样本。')
    else:d.note('部分原文为周度/次日回顾。题材S评级不当市场S；档案创建时间不证明当日首发。')
    d.page('a=2/10只改变收缩强度，主版本保持a=5','robustness_summary.csv；reference_to_formal_diff.csv',nv)
    d.table(['a','配对日','平均绝对分差','档位改变','相位改变'],[[str(r.a),str(r.paired_n),f'{r.mean_absolute_score_difference:.3f}',f'{r.grade_changed_n} ({r.grade_changed_rate:.1%})',f'{r.phase_changed_n} ({r.phase_changed_rate:.1%})'] for r in rob.itertuples()],[120,150,310,290,280],rowh=54,size=22)
    changes=read('data_change_summary.csv');changes=changes[(changes.year.astype(str)=='2026')&changes.step.ne('delta_total')]
    labels={'delta_live_input':'补齐输入','delta_historical_ST':'历史ST身份','delta_calendar_cohort_quality':'市场日 / 群体 / 质量','delta_calibration':'重建2025标尺'}
    if len(changes):
        d.table(['数据修正步骤','旧版共同日','受影响日','平均绝对增量','最大绝对增量'],[[labels[r.step],str(r.paired_n),str(r.affected_n),f'{r.mean_absolute_change:.3f}',f'{r.max_absolute_change:.3f}'] for r in changes.itertuples()],[330,170,170,240,240],y=411,rowh=35,size=18)
    else:d.wrap('未提供B0旧输入，本次重建不能复验数据差异归因。',64,450,1140,24)
    d.note('上表固定先验、锚点和其他规则；下表依次修正数据，有符号增量逐日相加。未读取策略收益择优。')
    d.page('收盘环境只能支持下一交易时点，日期连接还须检查可用时间','environment_daily.csv；REPRODUCE.md','接口')
    stages=[('t日收盘','行情、群体反馈完成'),('数据齐备后','环境表生成；保留质量'),('t+1准备','以已可用环境连接信号'),('账本检验','同规则、同成本、完整分母')]
    for i,(title,body) in enumerate(stages):d.text(title,64+i*288,246,29,TEAL);d.wrap(body,64+i*288,310,245,24)
    d.wrap('连接条件：available_at ≤ decision_time；同一日期不能无条件合并。',64,470,1130,29,1.5,RED)
    d.note('当前采用收盘后研究约定，未验证每条供应商数据的精确首发时间；不能用于t日上午入场解释。')
    d.page('后续只登记三类实验：先固定策略，再比较环境增量','NEXT_EXPERIMENTS.md',3)
    d.table(['固定策略 / 事件','比较','主要结果与失败解释'],[['首板次日早盘强度','无过滤 / 昨日反馈 / 晋级率 / 综合分','日均净收益、尾部与不成交；若综合分不胜单指标，优先简单基准'],['首次回调再启','只按分数 / 分数加相位','控制重复事件和月份；若相位无增量，仅保留描述层'],['情绪×题材结构','情绪单独 / 题材单独 / 联合','以完整候选分母与可成交结果比较；样本不足只形成新假设']],[295,360,495],rowh=125,size=23)
    d.note('本报告未跑上述收益实验；2026已研究，未来验证须使用新增证据并登记尝试次数。')
    high=int(f.grade5.isin(['S','A']).sum());low=int(f.grade5.isin(['C','D']).sum())
    d.page('环境层已可复刻，策略增量仍需独立检验','analysis_summary.json；DELIVERY_MANIFEST.json',nv)
    findings=[f'{nv}/{n}个交易日有正式分；高档S/A共{high}日，低档C/D共{low}日。',f'最高与最低月均分别为{best.score_mean:.1f}和{worst.score_mean:.1f}，月份构成影响明显。',f'题材对照覆盖{nt}日，与接力分相关{corr:.2f}，不能互相替代。','周期相位为固定规则的描述性输出；未验证其收益增量。']
    for i,t in enumerate(findings):d.wrap(t,64,205+i*83,1150,26)
    d.text('GitHub MrWu：报告、日表、复刻说明与免费生成源',64,560,22,TEAL);d.c.linkURL(URL,(64,139,1180,181),relative=0)
    d.note('剩余来源边界：KPL首发/修订时间、历史身份降级和成交代理局限见方法说明。没有自动交易或自动日更。')
    d.end(a.output)
    print(json.dumps({'pdf':str(a.output),'pages':d.n,'formal_days':nv,'calendar_days':n},ensure_ascii=False))

if __name__=='__main__':main()
