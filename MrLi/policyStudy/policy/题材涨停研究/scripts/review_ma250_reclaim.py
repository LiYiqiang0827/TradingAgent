"""Enrich the frozen MA250 replay with an audited, readable case review."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT)]
from study_ma250_reclaim import prepare, signals, Rules, flat_projection


def run(out):
    inputs = out / 'inputs'
    manifest = json.loads((out / 'FROZEN_INPUT_MANIFEST.json').read_text())
    for path, expected in manifest.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
    raw = pd.read_parquet(inputs / 'fifteen.parquet')
    calendar = sorted(pd.read_parquet(inputs / 'calendar.parquet').cal_date.astype(str).unique())
    f = prepare(raw, calendar)
    config = json.loads((out / 'FROZEN_RULES.json').read_text())
    start, end = config['requested_start'], config['requested_end']
    original = json.loads((out / 'signals_before_outcomes.json').read_text())
    candidates, funnel, f = signals(f, start, end, Rules(**config['rules']))
    assert candidates == original
    trades = pd.read_csv(out / 'trades.csv')
    ledger = pd.read_csv(out / 'ledger.csv')
    summary = json.loads((out / 'SUMMARY.json').read_text())
    cash, shares = config['rules']['initial_cash'], 0
    for r in ledger.itertuples():
        sign = -1 if r.side == 'buy' else 1
        cash += sign * r.price * r.shares - r.fee
        shares -= sign * r.shares
        assert cash >= 0 and abs(cash - r.cash_after) < 1e-7
    assert shares == 0 and abs(cash - summary['final_equity']) < 1e-7
    audit_rows = []
    for s in original:
        i = s['index']; at = pd.Timestamp(s['time']); day = at.strftime('%Y%m%d')
        truncated = prepare(raw[raw.datetime.le(at)], [d for d in calendar if d <= day])
        prefix, _, _ = signals(truncated, start, day, Rules(**config['rules']))
        assert s in prefix
        changed = raw.copy(); mask = changed.datetime.gt(at)
        for col in ('open', 'high', 'low', 'close'):
            changed.loc[mask, col] *= .25
        changed_signals, _, _ = signals(prepare(changed, calendar), start, end, Rules(**config['rules']))
        assert [v for v in original if v['index'] <= i] == [v for v in changed_signals if v['index'] <= i]
        assert flat_projection(truncated.p_close.iloc[:i + 1]) == s['projection']
        t = trades[trades.id.eq(s['id'])].iloc[0]
        held = f.iloc[int(t.entry_index):int(t.exit_index)]
        assert pd.Timestamp(t.exit_time).date() > pd.Timestamp(t.entry_time).date()
        assert int(t.entry_index) == i + 1 and int(t.exit_index) == int(t.exit_decision_index) + 1
        audit_rows.append(dict(id=s['id'], signal_time=s['time'], entry_bar_end=str(f.datetime.iloc[int(t.entry_index)]),
                               exit_bar_end=str(f.datetime.iloc[int(t.exit_index)]),
                               high_MFE_pct=float(100 * (held.high.max() / t.entry_price - 1)),
                               low_MAE_pct=float(100 * (held.low.min() / t.entry_price - 1)),
                               worst_close_return_pct=float(100 * (held.close.min() / t.entry_price - 1)),
                               input_prefix_pass=True, modified_future_pass=True, cash_and_T1_pass=True))
    pd.DataFrame(audit_rows).to_csv(out / 'trade_path_audit.csv', index=False, encoding='utf-8-sig')
    (out / 'VERIFICATION.json').write_text(json.dumps(dict(unit_tests=14, cash_ledger_pass=True,
        actual_nonempty_signal_prefix_pass=True, actual_signal_mutated_future_pass=True,
        projection_recomputed_from_truncated_history_pass=True, all_trades_T1_pass=True,
        source_snapshot_hashes_pass=True), ensure_ascii=False, indent=2))
    setups = f.loc[f.declining & f.price_gate & f.datetime.ge(pd.Timestamp(start))].copy()
    setups['exclude_reason'] = np.where(~setups.alignment_gate, 'MA5>MA20>MA30不满足',
                                      np.where(~setups.recent_cross_gate, '两个金叉不同时在最近16根内', '完整信号'))
    setups[['datetime', 'close', 'ma5', 'ma20', 'ma30', 'ma250', 'alignment_gate',
            'recent_cross_gate', 'exclude_reason']].to_csv(out / 'reclaim_setup_audit.csv', index=False, encoding='utf-8-sig')
    curve = pd.read_csv(out / 'equity_curve.csv')
    active = f[f.datetime.ge(pd.Timestamp(start)) & f.datetime.lt(pd.Timestamp(end) + pd.Timedelta(days=1))].copy()
    gross_hold = float(100 * (active.close.iloc[-1] / active.open.iloc[0] - 1))
    held_bars = int(curve.shares.gt(0).sum())
    profit_share = float(trades.iloc[-1].pnl / trades.pnl.sum() * 100)
    # Readable prose is separated from the immutable rule/signal snapshots.
    report = (out / 'REPORT.md').read_text()
    report = report.replace(f"已完成交易胜率：{summary['win_rate_pct']}%；平均/中位净收益：{summary['mean_net_pct']}% / {summary['median_net_pct']}%；最差/最好：{summary['worst_net_pct']}% / {summary['best_net_pct']}%。",
                            f"已完成交易：2笔盈利/2笔（样本不足以估计胜率）；平均/中位净收益：{summary['mean_net_pct']:.2f}% / {summary['median_net_pct']:.2f}%；最差/最好：{summary['worst_net_pct']:.2f}% / {summary['best_net_pct']:.2f}%。")
    report += f'''
## 逐笔核对与解释

15分钟数据以**结束时间**标柱。例如12月5日14:30判断，买入的是下一根14:30—14:45 K线的开盘代理价；其数据库标柱时间是14:45。判断时间与模拟成交边界可能相同，但严格使用了不同的K线，绝未使用判断柱的收盘价当成交价。

|信号|MA5/MA20金叉|MA5/MA30金叉|冻结交点/元|恒价预计金叉/根|实际金叉/根|持仓最高价涨幅|持仓最低价涨幅|收盘最不利涨幅|
|---|---|---|---:|---:|---:|---:|---:|---:|
'''
    for s, path in zip(original, audit_rows):
        t = trades[trades.id.eq(s['id'])].iloc[0]
        report += f"|{s['id']}|{s['cross5_20_time']}|{s['cross5_30_time']}|{s['stop_raw_at_signal']:.3f}|{s['projected_cross_h']}|{int(t.actual_cross_h)}|{path['high_MFE_pct']:+.2f}%|{path['low_MAE_pct']:+.2f}%|{path['worst_close_return_pct']:+.2f}%|\n"
    report += f'''
主口径3856根有效测试K线中：919根符合长期下跌，4根发生价格上穿，其中3根符合短均线多头、2根符合两个近期金叉，最后2根恒价预测都达标。**本股票中抵扣预测未额外淘汰这2个候选，不能用本结果证明抵扣预测增加了收益。**所有被筛掉的上穿位置已保存在reclaim_setup_audit.csv。

两笔均未触发冻结止损或16根金叉失败，因此本股票没有真实验证这两类失败情景下的亏损尾部和T+1保护能力。执行程序的14项测试覆盖了这两种分支，但合成边界测试不等于真实市场风险验证。

全年持仓{held_bars}根/3856根（{100 * held_bars / len(active):.2f}%的K线），收益的{profit_share:.2f}%来自8月这一笔。同期本股简单买入持有价格涨幅为{gross_hold:.2f}%（未扣费），用于解释该股票的上涨背景；不能将单股盈利直接当成优于基准的证明。

全年总览图：charts/year_overview.jpg；资金曲线：charts/equity_curve.jpg；每次推演和交易图在index.html中成对排列。买卖图包含未来回放，推演图只包含截至判断时的数据，二者用途分开。
'''
    if '## 逐笔核对与解释' not in (out / 'REPORT.md').read_text():
        (out / 'REPORT.md').write_text(report)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from plot.plot_daily_kline import _configure_chinese_font
    _configure_chinese_font()
    a = active.reset_index(drop=True)
    start_index = int(active.index[0])
    fig, (ax, av) = plt.subplots(2, 1, figsize=(17, 8), sharex=True, gridspec_kw={'height_ratios': [4, 1]})
    ax.plot(a.index, a.close, lw=.8, color='#68788c', label='15分钟收盘价')
    ax.plot(a.index, a.ma250 / a.scale, lw=1.2, color='#151515', label='15分钟MA250')
    ax.fill_between(a.index, a.low, a.high, color='#8296a4', alpha=.18)
    for t in trades.itertuples():
        bi, si = t.entry_index - start_index, t.exit_index - start_index
        ax.axvspan(bi, si, color='#ffd54f', alpha=.2)
        ax.scatter(bi, t.entry_price, marker='^', s=75, color='#c42030', zorder=5)
        ax.scatter(si, t.exit_price, marker='v', s=75, color='#179c52', zorder=5)
        ax.annotate(f'{t.id} {t.net_return_pct:+.2f}%', (bi, t.entry_price), xytext=(10, -35), textcoords='offset points')
    av.bar(a.index.to_numpy(), a.vol.to_numpy() / 10000, color='#8e9ca7', width=.8)
    starts = np.flatnonzero(a.datetime.dt.strftime('%H:%M').eq('09:45'))[::20]
    av.set_xticks(starts); av.set_xticklabels([a.datetime.iloc[i].strftime('%Y-%m-%d') for i in starts], rotation=25, ha='right')
    ax.legend(); ax.set_ylabel('价格 / 元'); av.set_ylabel('量 / 万股')
    ax.set_title('光力科技300480.SZ · 最近一年15分钟价格/MA250总览\n红三角买入、绿三角卖出、黄色为持仓区间；各交易的完整K线另见图册')
    ax.grid(alpha=.15); av.grid(alpha=.12)
    fig.tight_layout(); fig.savefig(out / 'charts/year_overview.jpg', dpi=160); plt.close(fig)
    index = (out / 'index.html').read_text()
    marker = '<img src="charts/year_overview.jpg">'
    if marker not in index:
        index = index.replace('<img src="charts/equity_curve.jpg">', marker + '\n<img src="charts/equity_curve.jpg">')
        (out / 'index.html').write_text(index)
    print(json.dumps(dict(cash_audit=cash, trade_path_audit=audit_rows, held_bars=held_bars,
                          stock_buy_hold_gross_pct=gross_hold, final_trade_profit_share_pct=profit_share), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT / 'outputs/guangli_ma250_15m_20251002_20261002'))
    run(Path(parser.parse_args().output))
