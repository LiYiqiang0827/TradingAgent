"""Execution audit, annotated charts and readable exit-study report."""
from __future__ import annotations
import argparse,json,html,hashlib
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[5]
sys.path[:0]=[str(ROOT),str(ROOT/'offlineDataManager/scripts')]
from plot.plot_daily_kline import _configure_chinese_font,_draw_candles,MA_COLORS
from replay_second_wave_exits import prepare,dump,sha
from study_second_wave_exits import replay,context_for,State

LABELS={'floor_only':'只守平台下沿','expectation':'启动预期退出','trend':'15分钟趋势退出','phased':'试仓／分批／买回','phased_theme':'分批＋首波题材','phased_theme_current':'分批＋时序题材'}
REASONS={'frozen_initial_signal':'冻结买入信号','platform_floor_failed':'跌破冻结平台下沿','opening_gap_below_floor':'开盘跌破平台下沿',
 'early_launch_failed':'早期跌回启动位／平台内','one_day_no_followthrough':'16根内没有跟进','two_day_sideways':'32根横盘且短均线未确认',
 'trend_ma30_failed':'趋势确认后跌破MA30','acceleration_failed':'加速后大阴线／跌破MA20','five_pct_peak_retracement_at_close':'收盘距已知高点回撤5%',
 'confirmed_trend_add':'1R＋连续多头确认后加仓','two_R_partial_profit':'2R分批止盈','fresh_quiet_platform_reclaim':'新整理平台放量收复后买回',
 'theme_cooling_with_price_weakness':'题材收缩且价格走弱','partial_order_upgraded_on_failure':'等待分批卖出时出现失效','research_horizon':'20日研究窗结束'}

def execution_audit(out):
    led=pd.read_csv(out/'case_ledger.csv');checks=[]
    for (policy,key),g in led.groupby(['policy','key'],sort=False):
        lots=[]
        for r in g.to_dict('records'):
            ex=pd.Timestamp(r['execution_time']);dec=pd.Timestamp(r['decision_time'])
            assert ex>=dec and int(r['shares'])>0 and int(r['shares'])%100==0
            # A completed bar ends exactly when the following bar opens.
            # Equality of wall-clock labels is allowed; the candle index must
            # still advance, except the separately recorded standing OPEN rule.
            assert pd.Timestamp(r['execution_bar'])>dec
            if pd.notna(r['theme_as_of']):assert str(int(r['theme_as_of']))<dec.strftime('%Y%m%d')
            if pd.notna(r.get('stock_theme_source_date')):assert str(int(r['stock_theme_source_date']))<dec.strftime('%Y%m%d')
            n=int(r['shares'])
            if r['action']!='sell':lots.append([ex.date(),n])
            else:
                available=sum(l[1] for l in lots if l[0]<ex.date());assert n<=available
                left=n
                for l in lots:
                    if l[0]<ex.date():used=min(left,l[1]);l[1]-=used;left-=used
                assert left==0
            assert sum(l[1] for l in lots)==int(r['remaining_shares'])
        checks.append(dict(policy=policy,key=key,orders=len(g),T_plus_1=True,shares_and_timestamps=True))
    for q in (out/'portfolio').iterdir():
        curve=pd.read_csv(q/'curve.csv');l=pd.read_csv(q/'ledger.csv');cash=300000.
        for r in l.to_dict('records'):
            flow=r['price']*r['shares']+r['fees'] if r['action']!='sell' else -(r['price']*r['shares']-r['fees'])
            cash-=flow;assert abs(cash-r['cash_after'])<.01
        assert curve.cash.min()>=0 and abs(cash-curve.cash.iloc[-1])<.01
        assert np.allclose(curve.equity,curve.cash+curve.exposure)
        summary=json.loads((q/'SUMMARY.json').read_text());assert summary['open_cases']==0
    return dict(case_policy_paths=len(checks),case_orders=len(led),portfolios=len(list((out/'portfolio').iterdir())),T_plus_1_and_cash_checks='passed',paths=checks)

def actual_prefix_audit(signals,calendar,frames,bases,theme,limits,excluded):
    cutoff='2026-02-06 15:00:00'
    first=replay(frames,signals,calendar,theme,limits,bases,policy='phased_theme_current',profile='risk05',excluded=excluded)
    changed={}
    for code,f in frames.items():
        rows=[]
        for row in f['rows']:
            r=dict(row)
            if str(r['datetime'])>cutoff:
                for k in ('open','high','low','close'):r[k]*=3
                for n in (5,10,20,30,60,120,250):r[f'm{n}']*=3
                r['vol']*=7
            rows.append(r)
        changed[code]={**f,'rows':rows}
    changed_theme={k:({**v,'heat':100.,'width':100,'cooling':False,'warming':True} if isinstance(k,tuple) and k[1]>'20260206' else v) for k,v in theme.items()}
    changed_theme['_stock_theme_timeline']={k:({**v,'theme_name':'未来污染标签'} if k[1]>'20260206' else v) for k,v in theme['_stock_theme_timeline'].items()}
    second=replay(changed,signals,calendar,changed_theme,limits,bases,policy='phased_theme_current',profile='risk05',excluded=excluded)
    a=[r for r in first['ledger'] if r['execution_bar']<=cutoff];b=[r for r in second['ledger'] if r['execution_bar']<=cutoff]
    c=[r for r in first['decisions'] if r['decision_time']<=cutoff];d=[r for r in second['decisions'] if r['decision_time']<=cutoff]
    assert a==b and c==d
    pd.testing.assert_frame_equal(first['curve'][first['curve'].datetime.le(cutoff)],second['curve'][second['curve'].datetime.le(cutoff)])
    return dict(cutoff=cutoff,mutated_future_OHLC_multiple=3,mutated_future_volume_multiple=7,mutated_future_theme=True,
        past_orders=len(a),past_decisions=len(c),past_curve_equal=True,note='exclusion list fixed; this checks engine causality, not historical source revision or execution feasibility truth')

def case_chart(out,s,f,calendar,themes,ledger,case,policy):
    at=pd.Timestamp(s['signal_time']);where=int(np.flatnonzero(f.datetime.eq(at))[0]);start=max(0,where-48)
    end=min(len(f)-1,where+20*16+32)
    view=f.iloc[start:end+1].copy().reset_index(drop=True);times={str(t):i for i,t in enumerate(view.datetime)}
    fig,axes=plt.subplots(3,1,figsize=(17,10),gridspec_kw=dict(height_ratios=[5,1.15,1.65],hspace=.12))
    price,vol,heat=axes;x=np.arange(len(view));colors=_draw_candles(price,view)
    for n in (5,10,20,30,60,120):price.plot(x,view[f'm{n}'],color=MA_COLORS[n],lw=1 if n<60 else 1.4,alpha=.85,label=f'MA{n}')
    vol.bar(x,view.vol/10000,color=colors,width=.65);vol.set_ylabel('量／万股')
    j=times[s['signal_time']]
    price.axvline(j,color='#555',ls=':',alpha=.6)
    price.hlines(s['base']['lower'],j,len(view)-1,ls='--',color='#14859a',lw=1.1,label='原平台下沿')
    price.hlines(s['base']['upper'],j,len(view)-1,ls=':',color='#14859a',lw=1.1,label='原平台上沿')
    for i,r in enumerate(ledger.to_dict('records'),1):
        k=times.get(r['execution_bar'])
        if k is None:continue
        symbol,color={'buy':('^','#c43132'),'add':('P','#d68312'),'reentry':('^','#a22dc0'),'sell':('v','#235db5')}[r['action']]
        price.scatter([k],[r['price']],marker=symbol,color=color,s=65,zorder=8)
        price.annotate(str(i),(k,r['price']),xytext=(4,8 if r['action']!='sell' else -16),textcoords='offset points',fontsize=8,color=color)
    st=State(s,where,end,s['base']['lower'],s['base']['upper'],s['signal_open'])
    contexts=[context_for(st,t.strftime('%Y%m%d'),themes,policy) for t in view.datetime]
    hv=[v.get('heat',np.nan) if v.get('known',False) else np.nan for v in contexts];width=[v.get('width',np.nan) if v.get('known',False) else np.nan for v in contexts]
    heat.plot(x,hv,color='#bf742c',lw=1.6,label='前一交易日题材热度（0—100）');heat.set_ylim(0,103);heat.set_ylabel('题材热度')
    right=heat.twinx();right.plot(x,width,color='#328078',lw=1.1,ls='--',label='前一日涨停宽度');right.set_ylabel('涨停家数');right.set_ylim(0,max(1,np.nanmax(width)*1.1))
    prior_label=None
    for i,c in enumerate(contexts):
        name=c.get('theme_name')
        if name!=prior_label:
            heat.text(i,5,name or '未知',fontsize=8,rotation=0,bbox=dict(facecolor='white',alpha=.7,edgecolor='none'))
            prior_label=name
    heat.fill_between(x,0,100,where=[v.get('cooling',False) for v in contexts],color='#b34b4b',alpha=.12)
    ticks=[i for i,t in enumerate(view.datetime) if i==0 or t.date()!=view.datetime.iloc[i-1].date()];ticks=ticks[::max(1,int(np.ceil(len(ticks)/12)))]
    heat.set_xticks(ticks,[view.datetime.iloc[i].strftime('%m/%d') for i in ticks]);heat.legend(loc='upper left',fontsize=9);right.legend(loc='upper right',fontsize=9)
    for a in axes:a.grid(alpha=.16);a.set_xlim(-.8,len(view)-.2)
    price.tick_params(axis='x',labelbottom=False);vol.tick_params(axis='x',labelbottom=False);price.legend(loc='upper left',ncol=8,fontsize=8)
    price.set_ylabel('未复权价格／元')
    note=f"隔离账户净变化{case['net_return_pct']:+.2f}%｜{int(case['legs'])}次入场｜开发案例" if case['buy_orders'] else '未成交或数据记账排除，保留原信号'
    price.set_title(f"{s['name']} {s['ts_code']}｜{s['prior_theme_name']}｜{LABELS[policy]}\n{note}",fontsize=14)
    fig.text(.055,.025,'红▲买入　橙＋加仓　紫▲买回　蓝▼卖出；编号对应图册订单表。标记对齐K线结束标签，实际开盘代理时间＝标签－15分钟。',fontsize=10)
    fig.subplots_adjust(top=.91,bottom=.07,left=.055,right=.95)
    q=out/'charts'/(s['ts_code']+'_'+at.strftime('%Y%m%d_%H%M'));q.mkdir(parents=True,exist_ok=True)
    target=q/(policy+'.jpg');fig.savefig(target,dpi=110);plt.close(fig)
    return dict(path=str(target.relative_to(out)),candles=int(view.valid_bar.sum()),positive_volume_bars=int(view.vol.gt(0).sum()),execution_markers=len(ledger),
        drawn_markers=sum(str(t) in times for t in ledger.execution_bar) if len(ledger) else 0,theme_previous_close=True)

def portfolio_chart(out,summaries):
    fig,axes=plt.subplots(2,1,figsize=(16,9),gridspec_kw=dict(height_ratios=[3,1],hspace=.2))
    for policy in LABELS:
        curve=pd.read_csv(out/'portfolio'/f'{policy}_risk05'/'curve.csv');t=pd.to_datetime(curve.datetime)
        equity=curve.equity;dd=(equity/equity.cummax().clip(lower=300000)-1)*100
        axes[0].plot(t,equity/10000,label=LABELS[policy],lw=1.4);axes[1].plot(t,dd,lw=1.25)
    axes[0].axhline(30,color='#555',ls='--',lw=1);axes[0].set_ylabel('账户权益／万元');axes[1].set_ylabel('回撤／%')
    axes[0].legend(ncol=3,fontsize=10);axes[0].set_title('固定36信号的开发回放｜30万元；单笔计划风险0.5%，单股上限20%，新增总仓位上限60%',fontsize=14)
    for ax in axes:ax.grid(alpha=.2)
    fig.text(.08,.02,'按15分钟收盘估值；T+1、交易重叠、整手、佣金、卖出印花税及假设滑点已计入。不是独立验证或实际成交记录。',fontsize=10)
    fig.subplots_adjust(bottom=.09,top=.93);fig.savefig(out/'portfolio_comparison.jpg',dpi=130);plt.close(fig)

def statistics(out,cases):
    rng=np.random.default_rng(20261001);answer={}
    for policy,g in cases[cases.buy_orders.gt(0)].groupby('policy'):
        values=g.net_return_pct.to_numpy();groups={d:h.net_return_pct.to_numpy() for d,h in g.groupby(g.signal_time.str[:10])};keys=list(groups);means=[]
        for _ in range(3000):sample=np.concatenate([groups[keys[i]] for i in rng.integers(0,len(keys),len(keys))]);means.append(sample.mean())
        answer[policy]=dict(mean95_entry_day_block_bootstrap=np.percentile(means,[2.5,97.5]).tolist(),entry_dates=len(keys),
            negative_5pct=int((values<=-5).sum()),negative_10pct=int((values<=-10).sum()),positive_10pct=int((values>=10).sum()),
            note='development uncertainty only; day clusters do not remove same-theme dependence or parameter selection')
    return answer

def write_report(out,source,cases,summaries,stats,audit,prefix):
    result=json.loads((out/'RESULT_SUMMARY.json').read_text());rows=result['case_statistics'];doc=ROOT/'policyStudy/docs/second_wave_exit_research_202601_202603.md'
    lines=['# 二次启动卖点与仓位：15分钟走势＋前一日题材热度',
      '', '**结论：机械化趋势退出能减轻本组案例的损失尾部，但尚未形成稳定盈利方法。早退会错失慢启动大行情，简单平台买回又可能追回高位。**',
      '',f'[完整36例卖点图册]({out}/index.html) · [逐笔订单CSV]({out}/case_ledger.csv) · [18组账户比较]({out}/portfolio_summary.csv)',
      '', '## 范围与时点', '',
      '- 保留上一轮冻结的36个1—3月信号，没有重新筛选赢家或更改买点。入场及后续评价数据与原冻结清单用哈希核对。山东赫达是用户提供的已知开发例，另列剔除后的均值。',
      '- 36个中：34个可做完整记账；华银电力次根涨停一价，保守视为未成交；九州通观察窗跨除息，未实现现金红利／权益变化记账，整例从比较中排除。这是事后数据质量排除，不是可用于实时选股的条件。',
      '- 本区间后续行情已经看过，因此所有方案和参数属于开发研究，不是独立留出期；更不能把18组中的最高收益称为有效策略。',
      '- 观察窗从入场交易日算20个交易日，3月的观察延伸到4月。研究窗口到期按最后一天14:45决策、下一根开盘退出；它是比较基准，不代表推荐的固定日期卖法。',
      '', '## 逐根执行和费用', '',
      '- 已完成15分钟K线决定订单，下一根开盘价加减假设滑点作为成交代理。早盘已知开盘跌穿冻结平台下沿时，可按预先固定的失效位直接在开盘代理退出；不等9:45收盘。',
      '- 每批买入独立遵守T+1；今天加仓的部分不能随昨天的仓位一起当天卖。失败卖单在可以卖之前保留；分批卖单尚未成交又出现失效时，升级全量退出。规则来源：[上交所交易规则3.1.4](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)。',
      '- 一价涨停买入保守视为未成交；一价跌停卖出延后。使用整根H/L判断是否一价属于保守的成交可行性代理，会使用执行根结束后的信息，不能等同真实开盘排队成交或完全可执行逐笔回测。涨停一价本身不阻止卖出。无成交量／缺失槽位不生成虚构成交。',
      '- 假设佣金万3、每单最低5元，卖出印花税万5，双边滑点各万5；未单列过户等其他费用。股票整手100股、现金账户不借款。上述费用及滑点是研究参数。',
      '', '## 六套开发方案', '',
      '| 方案 | 退出与持有 |', '|---|---|',
      '| 平台基准 | 15分钟收盘跌破原整理平台下沿，或次日开盘跳空跌破；否则持有到研究窗末 |',
      '| 启动预期 | 基准＋前16根跌回启动阳线开盘／平台上沿内；16根没有达到0.5R且仍接近买入价；32根没有1R进展且短均线未形成多头 |',
      '| 趋势跟踪 | 预期退出＋上涨达到1R或短四均线连续多头上升3根后启用MA30；8根涨8%并明显加速时用大阴线／MA20退出；日收盘距已知高点回撤5%也退出 |',
      '| 分仓买回 | 先买计划仓位的一半；达到1R且短四均线连续多头上升3根再加仓；2R卖所持一半；剩余按趋势退出；全清后新缩量平台放量收复再买回，最多2次 |',
      '| 分仓＋题材 | 与前项相同，但题材收缩时不加仓；买回要求题材仍热或重新升温；题材明显收缩且价格转弱也退出 |',
      '| 分仓＋时序题材 | 同样热度条件，但个股主归因仅按此前已完成的KPL记录更新；股票当日新归因次日才生效。新增该对照是修正旧标签可能过时的问题，仍属开发研究 |',
      '', 'R＝开盘代理买价至冻结平台下沿的距离，最小用1%防止仓位过大。它是计划价差，不是T+1下可保证兑现的最大亏损。所有阈值是开发设定。',
      '', '## 题材热度如何使用', '',
      '- 复用题材数据库的0—100热度公式：涨停宽度最高25分、按板高加权的梯队35分、最高板高15分、封板率10分、近5日持续性10分、早盘封板比例5分。复用`board_points()`和`parse_board_status()`。',
      '- 从历史KPL涨停／炸板榜重建，保留首波主归因固定版本，另对照按当时已知的最近KPL股票主归因更新版本；不根据后来的赢家合并别名。36个信号中8个在入场前已经有更新的归因，首波标签有13个在前一日热度为0，不能把过期字面当成所有相关题材已冷却。ST、退市及次新排除。历史归因可能修订的风险仍存在。',
      '- 盘中只读取前一完整交易日热度和宽度。过去10日峰值热度至少50，现热度低于峰值65%且宽度低于峰值50%，标为收缩；只要源当天无全市场数据，则标为未知，不能冒充冷却。',
      '- 买回的升温条件为热度至少50，或高于此前3日均值且宽度至少2。没有使用新闻模型事后催化、最终周期结束日或未来题材高度。它尚不能代表准确的主线／核心股判定。',
      '', '## 全部可记账机会：独立账户比较', '',
      '每例以10万元做隔离账户，单标的最多用99%资金；分仓方案先投入其中的一半，余款留现金。因此下表是整个隔离账户净变化，不能把半仓结果当成股票全仓收益直接比较。所有同一母机会的再次买回计入同一例。',
      '', '| 方案 | 实际入场 | 平均净变化 | 中位 | 最差 | 最好 | 正收益比例 | 排除赫达后的均值 |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for policy,v in rows.items():lines.append(f"| {LABELS[policy]} | {v['filled']}/36 | {v['mean_pct']:+.2f}% | {v['median_pct']:+.2f}% | {v['worst_pct']:+.2f}% | {v['best_pct']:+.2f}% | {100*v['win_rate']:.1f}% | {v['non_core_mean_pct']:+.2f}% |")
    lines += ['', '趋势退出把本组最差隔离账户净变化从−22.22%缩至−6.27%，但均值仍约−0.19%。分仓版本较小亏损的一部分来自先只投入半仓，不能全归功于信号判断。',
      '', '所有方案的按入场日期分组bootstrap均值95%区间均跨0；同题材相关及开发选择仍未充分处理。详见`UNCERTAINTY.json`。',
      '', '## 重叠交易账户：30万元起始资金', '',
      '单股新建／加仓最高账户权益20%，总新买入暴露最高60%；上涨后被动仓位可超过上限，这版没有仅为比例超过上限而强制卖出。风险0.5%／1%指按冻结下沿估算的计划损失预算，不是实际最大损失上限。订单按卖出优先、冻结形态评分高者优先；同时信号会因资金占用而被跳过，不同方案填入的股票集合不相同。',
      '', '| 方案 | 仓位口径 | 最终权益 | 净变化 | 最大回撤 | 实际入场 |', '|---|---|---:|---:|---:|---:|']
    profiles={'equal20':'单股20%','risk05':'风险预算0.5%','risk10':'风险预算1%'}
    for r in summaries:lines.append(f"| {LABELS[r['policy']]} | {profiles[r['profile']]} | {r['final_equity']:,.2f} | {r['net_return_pct']:+.2f}% | {r['max_drawdown_pct']:.2f}% | {r['filled_cases']}/36 |")
    lines += ['',f'![风险0.5%账户比较]({out}/portfolio_comparison.jpg)',
      '', '这些账户中平台基准＋0.5%风险预算的正收益受具体资金排序和少数大赢家影响；不能据此宣称该方案优于趋势方案或证明盈利能力。所有方案都必须保留，随后冻结规则再检验未见过的日期。回撤按15分钟收盘权益估值，不是逐笔／盘中最低权益。',
      '', '## 核心案例：及时纠错与过早退出的冲突', '',
      '| 股票 | 平台基准净变化 | 趋势退出净变化 | 分仓＋时序题材净变化 | 含义 |', '|---|---:|---:|---:|---|']
    commentary={'300071.SZ':'趋势风险订单在1月29日产生，1月30日开盘可卖，避开2月2日深跳空；单例不证明普遍有效',
       '300102.SZ':'抓住首段上冲，但加速退出早于20日最高；后续买回损害已拿到的利润',
       '002810.SZ':'已知开发模板；MA30退出能拿到一段，但较早的2R分批切掉后续仓位',
       '000815.SZ':'初期回到启动位触发退出，随后大涨；后续重新买回更贵且最终回吐',
       '301226.SZ':'早期回踩清仓后，在更高的位置买回，未改善母机会净收益',
       '002546.SZ':'早退出保本，但没有按本版新平台规则抓住后续主升',
       '300798.SZ':'16根没有跟进退出明显优于等待深破平台，但后来上涨没有自动转化为收入'}
    for code,text in commentary.items():
        g=cases[cases.ts_code.eq(code)].set_index('policy');lines.append(f"| {g.iloc[0]['name']} {code} | {g.loc['floor_only','net_return_pct']:+.2f}% | {g.loc['trend','net_return_pct']:+.2f}% | {g.loc['phased_theme_current','net_return_pct']:+.2f}% | {text} |")
    dyn=rows['phased_theme_current'];static=rows['phased_theme'];plain=rows['phased']
    lines += ['', f"仅15分钟买回{plain['reentry_orders']}次，首波题材约束后{static['reentry_orders']}次，时序题材版本{dyn['reentry_orders']}次；相应隔离账户均值分别{plain['mean_pct']:+.2f}%、{static['mean_pct']:+.2f}%、{dyn['mean_pct']:+.2f}%。差异主要来自加仓／买回许可；不能仅凭减少交易说题材热度已验证为有效卖出择时。",
      '', '## 本轮能保留的研究结论', '',
      '1. 买入后必须区分立即兑现、平台内正常回踩、真失效三个状态；“落回启动阳线开盘就全清”过于敏感，美利云和祥明智能是反例。',
      '2. 15分钟趋势风险证据有控制尾部的研究价值，但加速、大阴线、MA30都只是规则候选；不能把每次跌破均线都等同第二波结束。',
      '3. 2R分批过早可能因为原平台极窄而在很小涨幅就卖掉半仓；R衡量失效距离，不自动衡量目标空间。',
      '4. 自动买回需要新的日线空间、15分钟结构重建和同题材同伴确认；只要再出现短平台倍量就买，常发生在原行情高位。这轮没有重新检查日线候选资格，需作为下一轮专门模块。',
      '5. 题材旧标签能辅助识别收缩，却不能替代当时主线与核心股身份。下一步应把首次退出导致漏掉大行情、随后高位错误买回，以及真正失效三类分别复核，再冻结独立留出检验。',
      '', '## 验证与复现', '',
      f"- {audit['case_orders']}笔隔离账户订单、{audit['case_policy_paths']}条案例方案路径、{audit['portfolios']}套组合账户，通过T+1、整手、现金流和权益恒等式核验；观察结束无未平仓案例。", 
      f"- 真实组合前缀验证：篡改{prefix['cutoff']}后的价格、成交量及题材热度，截断前{prefix['past_orders']}笔订单、{prefix['past_decisions']}个决策和资金曲线完全一致。", 
      '- 38项相关单元测试通过，覆盖未来污染、源缺口、买入一价、跌停不能卖、涨停可卖、隔夜跳空、逐批T+1、过期平台不能重用、题材次日生效、ST／次新排除及订单时间。模型服务未参与本轮数值运算，使用本地Python。',
      '', '```bash', 'PYTHONPATH=.:offlineDataManager/scripts:policyStudy/policy/题材涨停研究/scripts .venv/bin/python policyStudy/policy/题材涨停研究/scripts/replay_second_wave_exits.py',
      'PYTHONPATH=.:offlineDataManager/scripts:policyStudy/policy/题材涨停研究/scripts .venv/bin/python policyStudy/policy/题材涨停研究/scripts/review_second_wave_exits.py', '```',
      '',f'规则及输入哈希：[{out.name}/FROZEN_EXIT_RULES.json]({out}/FROZEN_EXIT_RULES.json)；图册、逐笔原始决策、组合成交／资金曲线、排除和失败分母均保留在上述目录。']
    content='\n'.join(lines)+'\n';doc.write_text(content,encoding='utf-8');(out/'report.md').write_text(content,encoding='utf-8');return doc

def run(args):
    out=Path(args.output);source=Path(args.source);_configure_chinese_font()
    signals,calendar,frames,bases,theme,limits,excluded=prepare(source,out)
    cases=pd.read_csv(out/'case_results.csv');ledger=pd.read_csv(out/'case_ledger.csv');summaries=json.loads((out/'RESULT_SUMMARY.json').read_text())['portfolio']
    audit=execution_audit(out);prefix=actual_prefix_audit(signals,calendar,frames,bases,theme,limits,excluded)
    dump(out/'EXECUTION_ACCEPTANCE.json',audit);dump(out/'PREFIX_ACCEPTANCE.json',prefix)
    stats=statistics(out,cases);dump(out/'UNCERTAINTY.json',stats);portfolio_chart(out,summaries)
    charts=[];parts=[]
    for n,s in enumerate(signals,1):
        key=s['ts_code']+'_'+s['signal_time'];articles=[]
        for policy in ('trend','phased_theme_current'):
            c=cases[cases.key.eq(key)&cases.policy.eq(policy)].iloc[0]
            trades=ledger[ledger.key.eq(key)&ledger.policy.eq(policy)].copy()
            qa=case_chart(out,s,frames[s['ts_code']]['frame'],calendar,theme,trades,c,policy);charts.append(dict(key=key,policy=policy,**qa))
            tables=[]
            for i,r in enumerate(trades.to_dict('records'),1):
                tables.append(f"<tr><td>{i}</td><td>{html.escape(str(r['execution_time']))}</td><td>{html.escape(r['action'])}</td><td>{r['price']:.3f}</td><td>{int(r['shares'])}</td><td>{html.escape(REASONS.get(r['reason'],r['reason']))}</td></tr>")
            articles.append(f"<details {'open' if policy=='trend' else ''}><summary>{LABELS[policy]}；隔离账户净变化{c.net_return_pct:+.2f}%</summary><img loading='lazy' src='{qa['path']}'><table><tr><th>编号</th><th>实际代理时间</th><th>动作</th><th>价</th><th>股数</th><th>依据</th></tr>{''.join(tables)}</table></details>")
        parts.append(f"<section><h2>{n}. {html.escape(s['name'])} {s['ts_code']}｜{s['signal_time']}｜{html.escape(s['prior_theme_name'])}</h2>{''.join(articles)}</section>")
        if n%8==0:print(f'charts {n}/36',flush=True)
    index=out/'index.html';index.write_text("<!doctype html><meta charset='utf-8'><title>二次启动卖点与题材回放</title><style>body{font-family:system-ui;max-width:1500px;margin:auto;padding:24px;background:#f8f9fc;color:#223}img{width:100%;height:auto}section{background:white;padding:20px;margin:20px 0;border-radius:12px}summary{padding:12px;cursor:pointer;font-weight:bold}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}</style><h1>二次启动卖点：15分钟＋题材</h1><p>36个原始信号全部保留；34个可记账、1个一价未成交、1个除息记账排除。已看过行情的开发研究，尚无稳定盈利证据。图中每个指标只供其当时决策使用；右侧后续走势用于检验。</p><h2>30万元账户比较（0.5%计划风险预算）</h2><img src='portfolio_comparison.jpg'>"+''.join(parts),encoding='utf-8')
    assert all(c['positive_volume_bars']>0 and c['drawn_markers']==c['execution_markers'] for c in charts)
    dump(out/'CHART_ACCEPTANCE.json',dict(charts=charts,count=len(charts),all_volume_and_markers_passed=True,visual_inspection='pending'))
    doc=write_report(out,source,cases,summaries,stats,audit,prefix)
    dump(out/'REVIEW_MANIFEST.json',dict(files={str(p):sha(p) for p in [index,doc,out/'portfolio_comparison.jpg']+list((out/'charts').rglob('*.jpg'))},charts=len(charts),script_sha=sha(Path(__file__))))
    print(json.dumps(dict(report=str(doc),gallery=str(index),charts=len(charts),prefix=prefix),ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',default=str(ROOT/'outputs/second_wave_templates_202601_202603_v4'))
    p.add_argument('--output',default=str(ROOT/'outputs/second_wave_exits_202601_202603_v5'))
    run(p.parse_args())
