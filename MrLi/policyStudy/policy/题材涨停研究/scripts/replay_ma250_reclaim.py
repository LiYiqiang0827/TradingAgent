"""Freeze inputs/rules, replay Guangli's MA250 rules and produce review charts."""
from __future__ import annotations
import argparse, hashlib, html, json
from dataclasses import asdict, replace
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT), str(ROOT / 'offlineDataManager/scripts')]
from study_ma250_reclaim import Rules, prepare, signals, replay, flat_projection


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(path, value):
    if path.exists() and json.loads(path.read_text()) != value:
        raise ValueError(f'Frozen artifact changed: {path}; use a new output directory')
    dump(path, value)


def ticks(ax, f, offset=0):
    starts = np.flatnonzero(f.datetime.dt.strftime('%H:%M').eq('09:45'))
    step = max(1, int(np.ceil(len(starts) / 12)))
    positions = starts[::step]
    ax.set_xticks(positions + offset)
    ax.set_xticklabels([f.datetime.iloc[i].strftime('%m-%d\n09:45') for i in positions], fontsize=9)


def chart_case(f, signal, trade, folder):
    import matplotlib.pyplot as plt
    from plot.plot_daily_kline import _draw_candles
    i = signal['index']
    end = int(trade.get('exit_index', i + 80)) if trade else i + 80
    q = f.iloc[max(0, i - 96):min(len(f), end + 33)].copy().reset_index(drop=True)
    base = max(0, i - 96)
    fig, (ax, av) = plt.subplots(2, 1, figsize=(17, 9), sharex=True, gridspec_kw={'height_ratios': [4, 1]})
    colors = _draw_candles(ax, q)
    for n, c in ((5, '#9467bd'), (20, '#1f77b4'), (30, '#e09018'), (250, '#222222')):
        ax.plot(q.index, q[f'ma{n}'] / q.scale, label=f'MA{n}', color=c, lw=1.35 if n == 250 else 1)
    ax.axhline(signal['stop_raw_at_signal'], color='#e14c55', ls='--', label=f"冻结金叉止损位 {signal['stop_raw_at_signal']:.3f}")
    ax.axvline(i - base, color='#247ba0', ls=':', alpha=.8)
    ax.annotate('买入判断', (i - base, signal['close']), xytext=(-52, -65), textcoords='offset points',
                arrowprops=dict(arrowstyle='->', color='#247ba0'), color='#247ba0')
    label = '未成交/被已有持仓覆盖'
    if trade:
        bi = trade['entry_index'] - base
        ax.scatter(bi, trade['entry_price'], color='#c42030', marker='^', s=115, zorder=6)
        ax.annotate('买入', (bi, trade['entry_price']), xytext=(5, 28), textcoords='offset points', color='#c42030')
        if trade['status'] == 'closed':
            si = trade['exit_index'] - base
            ax.axvspan(bi, si, color='#f8d773', alpha=.15)
            ax.scatter(si, trade['exit_price'], color='#157d41', marker='v', s=115, zorder=6)
            ax.annotate('卖出', (si, trade['exit_price']), xytext=(6, 30), textcoords='offset points', color='#157d41')
            label = f"净收益 {trade['net_return_pct']:+.2f}% | {trade['exit_reason']}"
        else:
            label = '截至样本末尾仍持仓，收益未实现'
    av.bar(q.index.to_numpy(), q.vol.to_numpy() / 10000, color=colors, width=.72)
    av.set_ylabel('成交量 / 万股')
    ticks(av, q)
    ax.legend(loc='upper left', ncol=3, fontsize=9)
    ax.set_ylabel('价格 / 元')
    ax.grid(alpha=.15); av.grid(alpha=.12)
    ax.set_title(f"光力科技 300480.SZ · {signal['time']} · 15分钟回放\n{label}；下一根开盘代理成交，遵守T+1")
    fig.tight_layout(); fig.savefig(folder / (signal['id'] + '_trade.jpg'), dpi=150); plt.close(fig)
    # Decision-only chart: no actual future candles or closes are loaded into q.
    q = f.iloc[max(0, i - 64):i + 1].copy().reset_index(drop=True)
    fig, (ax, ap) = plt.subplots(2, 1, figsize=(15, 9), gridspec_kw={'height_ratios': [3, 2]})
    _draw_candles(ax, q)
    for n, c in ((5, '#9467bd'), (20, '#1f77b4'), (30, '#e09018'), (250, '#222222')):
        ax.plot(q.index, q[f'ma{n}'] / q.scale, color=c, label=f'MA{n}')
    ax.axhline(signal['stop_raw_at_signal'], color='#e14c55', ls='--', label='冻结止损位')
    pp = pd.DataFrame(signal['projection'])
    scale = float(f.scale.iloc[i])
    ext = len(q) - 1 + pp.h.to_numpy()
    ax.axvspan(len(q) - 1, ext[-1], color='#ddd', alpha=.25)
    ax.plot(ext, pp.assumed_close / scale, color='#c42030', ls=':', label='未来恒价假设')
    for n, c in ((20, '#1f77b4'), (250, '#222222')):
        ax.plot(ext, pp[f'ma{n}'] / scale, color=c, ls='--')
        ap.plot(pp.h, pp[f'ma{n}'] / scale, color=c, marker='.', label=f'重算MA{n}')
    h = signal['projected_cross_h']
    ap.axvline(h, color='#c42030', ls=':', label=f'预计第{h}根金叉')
    ticks(ax, q); ax.legend(ncol=3, fontsize=9)
    ax.set_title(f"{signal['id']} · {signal['time']} · 仅使用买入判断前的数据\n右侧虚线为假设推演，不是实际未来，也不是上涨概率")
    ax.set_ylabel('价格 / 元'); ap.set_xlabel('未来15分钟K线数量'); ap.set_ylabel('均线 / 元')
    ap.legend(); ax.grid(alpha=.15); ap.grid(alpha=.15)
    fig.tight_layout(); fig.savefig(folder / (signal['id'] + '_projection.jpg'), dpi=150); plt.close(fig)


def render(out, f, candidates, data, comparisons, audit, start, end):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from plot.plot_daily_kline import _configure_chinese_font
    _configure_chinese_font()
    charts = out / 'charts'; charts.mkdir(exist_ok=True)
    trades = {r['id']: r for r in data['trades']}
    for s in candidates:
        chart_case(f, s, trades.get(s['id']), charts)
    q = data['curve']
    fig, (ax, ad) = plt.subplots(2, 1, figsize=(15, 8), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
    dates = pd.to_datetime(q.datetime)
    eq = q.equity.to_numpy()
    dd = 100 * (eq / np.maximum.accumulate(np.r_[Rules().initial_cash, eq])[1:] - 1)
    ax.plot(dates, eq / 10000, color='#2456a6', label='策略：30万元，单股满仓，空仓现金')
    active = f.loc[f.datetime.between(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1))]
    if len(active):
        ax.plot(active.datetime, .0001 * Rules().initial_cash * active.close / active.open.iloc[0],
                color='#888', alpha=.65, label='买入持有价格参考（未扣费用）')
    ax.axhline(30, color='#999', ls=':'); ax.set_ylabel('账户权益 / 万元'); ax.legend()
    ad.fill_between(dates, dd, 0, color='#c42030', alpha=.35); ad.set_ylabel('回撤 / %')
    ax.set_title('光力科技 · MA250上穿规则 · 按15分钟收盘估值的资金曲线')
    ax.grid(alpha=.15); ad.grid(alpha=.15)
    fig.tight_layout(); fig.savefig(charts / 'equity_curve.jpg', dpi=160); plt.close(fig)
    s = data['summary']
    rows = []
    for t in data['trades']:
        rows.append(f"|{t['id']}|{t['signal_time']}|{t['entry_time']}|{t.get('exit_time', '未卖出')}|"
                    f"{t['entry_price']:.3f}|{t.get('exit_price', float('nan')):.3f}|"
                    f"{t['net_return_pct']:+.2f}%|{t.get('actual_cross_h')}|{t.get('exit_reason', '仍持仓')}|") if t['status'] == 'closed' else None
    text = f'''# 光力科技：纯15分钟MA250上穿研究

本轮按用户给定思路固定规则后回放，未加入题材、日线或新闻筛选，未按收益调参。
股票：光力科技300480.SZ；请求的一年区间：{start}—{end}；实际最后行情：{audit['last_bar']}。
预热从2025-07-01开始，测试区间有{audit['test_sessions']}个交易日、{audit['test_bars']}根K线。
15分钟OHLC逐日聚合与本地不复权日线完全一致；一分钟/逐笔成交并未用于本研究。

## 固定买卖规则

1. MA250在最近80根K线（5个交易日）总体下跌，至少80%的单根斜率小于零，当前仍下跌。
2. **当前这一根收盘价从MA250下方上穿**，并且MA5>MA20>MA30；最近16根（含当前）内MA5分别上穿过MA20和MA30。
3. 当前MA20未高于MA250。假设未来16根收盘都等于当前收盘价，**同时重算MA20、MA250**，期间须出现MA20上穿MA250。
4. 冻结最近一次MA5/MA20交叉的价位：以前后两根均线值做线性插值求交点，不能在持仓中上移或改选另一金叉。
5. 下面三个退出条件先出现者生效：完成K线收盘跌破冻结价；信号后第16根完成时仍未发生MA20上穿MA250；买入后曾有完成收盘高于实际买价，此后收盘跌破MA30。
6. 完成K线作决策，**下一根开盘价加减0.05%滑点**作成交代理，100股一手，单股满仓、现金复投、不加仓。买入当天不得卖出，退出指令保留至可卖，不能因反弹撤销。16根从信号而非成交K线计数。
7. 涨停开盘的买单保守地视为不成交，跌停开盘的卖单延后；跳空低于冻结止损价取消买入。佣金万三每单最低5元、卖出印花税万五、过户费每边十万一。15分钟柱不能证明开盘成交/排队流动性，滑点也是假设。
8. 期末未出现卖出信号不人为平仓：权益按末根收盘标记，开放持仓单列。回撤为15分钟收盘账户回撤，不能视为盘中最大亏损。

交易约束参考[深交所交易规则2026修订，第3.1.4条](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)。测试跨2025—2026使用A股股票T+1约束。

## 主口径结果

- 完整买入信号：{s['signals']}；实际买入：{s['buys']}；已卖出：{s['closed']}；期末持仓：{s['open']}。
- 信号处置：{s['dispositions']}。
- 30万元期末权益：{s['final_equity']:,.2f}元；净收益：{s['net_account_return_pct']:+.2f}%；最大回撤：{s['max_drawdown_pct']:.2f}%。
- 已完成交易胜率：{s['win_rate_pct']}%；平均/中位净收益：{s['mean_net_pct']}% / {s['median_net_pct']}%；最差/最好：{s['worst_net_pct']}% / {s['best_net_pct']}%。
- 总手续费：{s['fees']:.2f}元；因T+1等候的K线数：{s['T_plus_1_wait_bars']}；卖出原因：{s['exit_counts']}。

|信号|判断时间（柱收盘）|买入时间（下一柱开盘）|卖出时间|买价|卖价|净收益|实际金叉距信号/根|卖出原因|
|---|---|---|---|---:|---:|---:|---:|---|
{chr(10).join(rows) or '没有已完成交易。'}

## 均线抵扣计算

对于N=20或250、h=1…16：

`MA_N(t+h) = MA_N(t) + (h*C_t - sum(C[t-N+1:t-N+h]))/N`

未来MA250也会改变。恒价假设仅保证该假设路径中的数学计算正确，不能保证真实金叉，更不能直接解释为涨幅预测。
每个候选的隔离推演保存在signals_before_outcomes.json和单独的projection图；实际未来验证另存forecast_outcomes.csv。

## 预先声明的定义敏感性

主口径保持上面80根/80%和当根上穿。另外只比较长期下跌定义32根、160根及80根全部下跌，并单列“上穿后最多等15根”的解释差异，未挑最好结果作为主结论。影线触及止损的比较也只在完成柱后下一开盘执行，不冒充止损价成交。

|定义|信号|已成交|完成交易|期末权益/元|净收益/%|最大回撤/%|
|---|---:|---:|---:|---:|---:|---:|
'''
    for c in comparisons:
        text += f"|{c['variant']}|{c['signals']}|{c['buys']}|{c['closed']}|{c['final_equity']:.2f}|{c['net_account_return_pct']:+.2f}|{c['max_drawdown_pct']:.2f}|\n"
    text += '''
## 可用范围

这是一只事先指定股票的历史开发回放，不是独立留出验证。少量交易不足以证明策略有效；单股结果不能外推至全市场或稳定复利。近期上涨样本尤其需要与震荡、连续下跌股票和未参与选样的未来月份分开检验。

输入、规则、信号和代码哈希分别冻结；不再调整本目录结果。未来变更须使用新的输出目录及规则版本。
'''
    (out / 'REPORT.md').write_text(text, encoding='utf-8')
    gallery = ['<!doctype html><meta charset="utf-8"><title>光力科技MA250策略图册</title>',
               '<style>body{font:16px sans-serif;max-width:1500px;margin:24px auto;background:#f4f5f8}img{width:100%;background:white}article{margin-bottom:40px}a{color:#2456a6}</style>',
               '<h1>光力科技：MA250上穿研究</h1>', '<p><a href="REPORT.md">完整规则和结果</a> · <a href="trades.csv">交易表CSV</a></p>',
               f"<p>信号{s['signals']}个，成交{s['buys']}次。净收益{s['net_account_return_pct']:+.2f}%，最大回撤{s['max_drawdown_pct']:.2f}%。</p>",
               '<img src="charts/equity_curve.jpg">']
    for c in candidates:
        gallery += [f"<article><h2>{c['id']} · {html.escape(c['time'])}</h2>",
                    f'<img loading="lazy" src="charts/{c["id"]}_projection.jpg">',
                    f'<img loading="lazy" src="charts/{c["id"]}_trade.jpg"></article>']
    (out / 'index.html').write_text('\n'.join(gallery), encoding='utf-8')


def run(a):
    out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
    inputs = out / 'inputs'; inputs.mkdir(exist_ok=True)
    rules = Rules()
    config = dict(stock='300480.SZ', stock_name='光力科技', requested_start=a.start, requested_end=a.end,
                  warmup_start='20250701', rules=asdict(rules),
                  signal_time='completed bar close; execution time next physical open',
                  long_decline='80 bars; net decline and >=80% negative slopes; current slope negative',
                  stop='frozen interpolated last MA5/MA20 intersection; completed close trigger',
                  trend_activation='first completed close above actually paid entry price',
                  no_future_signal_fields=True, no_parameter_optimization=True,
                  sensitivity=['down32', 'down160', 'down80_continuous', 'reclaim_within16', 'low_stop'])
    freeze(out / 'FROZEN_RULES.json', config)
    if not (inputs / 'fifteen.parquet').exists():
        from coreClient.data_provider import get_fifteenMin, get_tradecal, get_stk_limit, get_day, get_adj_factor
        calls = dict(fifteen=lambda: get_fifteenMin(ts_code='300480.SZ', start_date='20250701', end_date=a.end),
                     calendar=lambda: get_tradecal(start_date='20250701', end_date=a.end, source='database_only'),
                     limits=lambda: get_stk_limit(ts_code='300480.SZ', start_date='20250701', end_date=a.end, source='database_only'),
                     day=lambda: get_day(ts_code='300480.SZ', start_date='20250701', end_date=a.end, qfq=False, source='database_only'),
                     adj=lambda: get_adj_factor(ts_code='300480.SZ', start_date='20250701', end_date=a.end, source='database_only'))
        for k, fn in calls.items():
            fn().to_parquet(inputs / (k + '.parquet'), index=False)
    paths = list(inputs.glob('*.parquet')) + [Path(__file__), Path(__file__).with_name('study_ma250_reclaim.py')]
    freeze(out / 'FROZEN_INPUT_MANIFEST.json', {str(p): sha(p) for p in paths})
    code = out / 'frozen_code'; code.mkdir(exist_ok=True)
    for p in paths:
        if p.suffix == '.py': (code / p.name).write_bytes(p.read_bytes())
    raw = pd.read_parquet(inputs / 'fifteen.parquet')
    cal = sorted(pd.read_parquet(inputs / 'calendar.parquet').cal_date.astype(str).unique())
    cal = [d for d in cal if pd.Timestamp(d) <= pd.Timestamp(a.end)]
    f = prepare(raw, cal)
    li = pd.read_parquet(inputs / 'limits.parquet')
    limits = {str(r.trade_date): dict(up_limit=float(r.up_limit), down_limit=float(r.down_limit)) for r in li.itertuples()}
    test = f[f.datetime.ge(pd.Timestamp(a.start)) & f.datetime.lt(pd.Timestamp(a.end) + pd.Timedelta(days=1))]
    daily = pd.read_parquet(inputs / 'day.parquet'); daily.trade_date = daily.trade_date.astype(str)
    aggregate = raw.assign(trade_date=raw.trade_date.astype(str)).sort_values('datetime').groupby('trade_date').agg(
        o=('open', 'first'), h=('high', 'max'), l=('low', 'min'), c=('close', 'last')).merge(daily, on='trade_date')
    errors = {k: float((aggregate[k] - aggregate[v]).abs().max()) for k, v in [('o', 'open'), ('h', 'high'), ('l', 'low'), ('c', 'close')]}
    audit = dict(first_bar=str(f.datetime.iloc[0]), last_bar=str(f.datetime.iloc[-1]), test_sessions=int(test.datetime.dt.date.nunique()),
                 test_bars=len(test), invalid_slots=int((~f.valid).sum()), duplicates=int(raw.datetime.duplicated().sum()),
                 factor_count=int(raw.adj_factor.nunique()), missing_limit_dates=sorted(set(test.datetime.dt.strftime('%Y%m%d')) - set(limits)),
                 source='unified get_fifteenMin; native TDX DuckDB, not former CSV',
                 daily_aggregate_max_abs_error=errors, corporate_actions='none: dated factors constant during full loaded interval')
    adj = pd.read_parquet(inputs / 'adj.parquet'); adj.trade_date = adj.trade_date.astype(str)
    factor_check = raw.assign(trade_date=raw.trade_date.astype(str)).merge(adj[['trade_date', 'adj_factor']], on='trade_date', suffixes=('_minute', '_daily'))
    audit['factor_daily_max_error'] = float((factor_check.adj_factor_minute - factor_check.adj_factor_daily).abs().max())
    if audit['invalid_slots'] or audit['missing_limit_dates'] or max(errors.values()) > .011 or audit['factor_daily_max_error'] > 1e-10:
        raise ValueError('Data quality failed: ' + json.dumps(audit, ensure_ascii=False))
    candidates, funnel, marked = signals(f, a.start, a.end, rules)
    freeze(out / 'signals_before_outcomes.json', candidates)
    dump(out / 'SIGNAL_FUNNEL.json', funnel)
    dump(out / 'DATA_AUDIT.json', audit)
    outcomes = []
    for s in candidates:
        i = s['index']
        future = f.iloc[i + 1:i + rules.forecast_bars + 1]
        exact = len(future) == rules.forecast_bars and future.valid.all()
        hh = [int(j - i) for j in future.index[future.cross20_250]]
        outcomes.append(dict(id=s['id'], time=s['time'], projected_cross_h=s['projected_cross_h'],
                             full_horizon=bool(exact), actual_cross_h=hh[0] if hh else None,
                             forecast_realized=bool(hh) if exact else None,
                             future_h16_return_pct=float(100 * (future.close.iloc[-1] / s['close'] - 1)) if exact else None))
    pd.DataFrame(outcomes).to_csv(out / 'forecast_outcomes.csv', index=False, encoding='utf-8-sig')
    data = replay(f, candidates, limits, a.start, a.end, rules)
    for name in ('trades', 'ledger', 'issues', 'observations', 'dispositions'):
        pd.DataFrame(data[name]).to_csv(out / (name + '.csv'), index=False, encoding='utf-8-sig')
    data['curve'].to_csv(out / 'equity_curve.csv', index=False, encoding='utf-8-sig')
    summary = data['summary']
    complete = [r for r in outcomes if r['full_horizon']]
    summary['forecast_realized_count'] = sum(r['forecast_realized'] for r in complete)
    summary['forecast_evaluable_count'] = len(complete)
    dump(out / 'SUMMARY.json', summary)
    comparisons = [dict(variant='main_down80', **summary)]
    for name, ru in (('down32', replace(rules, down_bars=32)), ('down160', replace(rules, down_bars=160)),
                     ('down80_continuous', replace(rules, down_share=1.)), ('reclaim_within16', replace(rules, reclaim_age=15)),
                     ('low_stop', replace(rules, stop_mode='low'))):
        ss, ff, _ = signals(f, a.start, a.end, ru)
        d = replay(f, ss, limits, a.start, a.end, ru)
        folder = out / 'sensitivity' / name; folder.mkdir(parents=True, exist_ok=True)
        dump(folder / 'RULES.json', asdict(ru)); dump(folder / 'signals.json', ss); dump(folder / 'SUMMARY.json', d['summary'])
        pd.DataFrame(d['trades']).to_csv(folder / 'trades.csv', index=False, encoding='utf-8-sig')
        comparisons.append(dict(variant=name, **d['summary']))
    pd.DataFrame(comparisons).to_csv(out / 'sensitivity_summary.csv', index=False, encoding='utf-8-sig')
    render(out, marked, candidates, data, comparisons, audit, a.start, a.end)
    print(json.dumps(dict(audit=audit, funnel=funnel, summary=summary, sensitivity=comparisons), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--start', default='20251002'); p.add_argument('--end', default='20261002')
    p.add_argument('--output', default=str(ROOT / 'outputs/guangli_ma250_15m_20251002_20261002'))
    run(p.parse_args())
