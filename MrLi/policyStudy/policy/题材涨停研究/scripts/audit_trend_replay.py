"""Input reconciliation and uncertainty diagnostics; never alters selection."""
from pathlib import Path
import argparse,json,hashlib
import numpy as np
import pandas as pd


def run(out):
    out=Path(out);raw=pd.read_parquet(out/'inputs/fifteen.parquet');daily=pd.read_parquet(out/'inputs/daily.parquet')
    raw['trade_date']=raw.trade_date.astype(str);daily['trade_date']=daily.trade_date.astype(str)
    agg=raw.sort_values('datetime').groupby(['ts_code','trade_date']).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),bars=('close','count')).reset_index()
    q=daily.merge(agg,on=['ts_code','trade_date'],suffixes=('_daily','_15m'),how='left')
    for k in ['open','high','low','close']:q[f'{k}_difference']=q[f'{k}_15m']-q[f'{k}_daily']
    mismatch=q[[f'{k}_difference' for k in ['open','high','low','close']]].abs().max(axis=1)>.011
    q[mismatch].to_csv(out/'OHLC_RECONCILIATION_DIFFERENCES.csv',index=False)
    reconciliation=dict(daily_rows=len(q),complete_16bar_dates=int(q.bars.eq(16).sum()),missing_dates=int(q.bars.isna().sum()),
                        maximum_price_difference=float(q[[f'{k}_difference' for k in ['open','high','low','close']]].abs().max().max()),
                        days_difference_over_0011=int(mismatch.sum()),missing_or_different_dates_not_replaced=True)
    results={}
    for name,folder in [('selected',out/'selected/holdout'),('structure',out/'controls/structure/holdout'),('adaptive',out/'controls/adaptive/holdout'),('ma30',out/'controls/ma30/holdout'),('ma30_trial',out/'diagnostics/ma30_trial_only/holdout')]:
        if not (folder/'SUMMARY.json').exists():continue
        summary=json.loads((folder/'SUMMARY.json').read_text())
        try:t=pd.read_csv(folder/'trades.csv')
        except pd.errors.EmptyDataError:t=pd.DataFrame()
        closed=t[t.closed.eq(True)] if len(t) else t;wins=int(closed.net_return.gt(0).sum()) if len(closed) else 0;n=len(closed)
        phat=wins/n if n else 0;z=1.96;den=1+z*z/n if n else 1
        center=(phat+z*z/(2*n))/den if n else None
        radius=z*np.sqrt(phat*(1-phat)/n+z*z/(4*n*n))/den if n else None
        curve=pd.read_csv(folder/'curve.csv');curve['date']=pd.to_datetime(curve.datetime).dt.normalize()
        eq=curve.groupby('date').equity.last(); r=eq.pct_change(fill_method=None);r.iloc[0]=eq.iloc[0]/summary['initial_cash']-1
        # Circular moving blocks include no-position zero-return days; this is
        # an uncertainty illustration, not another strategy simulation.
        rng=np.random.default_rng(20261002); paths=[];v=r.values
        for _ in range(2000):
            starts=rng.integers(0,len(v),size=int(np.ceil(len(v)/5)))
            draw=np.concatenate([v[(np.arange(5)+i)%len(v)] for i in starts])[:len(v)]
            paths.append(np.prod(1+draw)-1)
        item=dict(closed=n,open=len(t)-n,win_count=wins,win_rate_wilson95=[center-radius,center+radius] if n else None,
                  win_interval_caveat='Binomial illustration overstates independence for same-date/same-theme trades.',
                  block_bootstrap_account_return95=np.quantile(paths,[.025,.975]).tolist(),block_length_sessions=5,
                  bootstrap_caveat='Finite historical regime, no unseen crash or execution uncertainty modeled.',
                  percent_of_calendar_days_exposed=float(curve.groupby('date').positions.max().gt(0).mean()),
                  median_exposure=float((curve.exposure/curve.equity).median()),average_closed_buy_cash=float(closed.buy_cash.mean()) if n else None,
                  partial_exit_trades=int(t.sells.ge(2).sum()) if len(t) else 0,added_trades=int(t.added.eq(True).sum()) if len(t) else 0,
                  max_observed_trade_loss=summary.get('worst_trade_return'),accounting_verified=summary['cash_accounting_verified'])
        results[name]=item
    value=dict(ohlc_reconciliation=reconciliation,uncertainty=results)
    (out/'AUDIT_STATISTICS.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);run(p.parse_args().output)
