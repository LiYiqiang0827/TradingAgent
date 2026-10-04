"""Independent arithmetic worksheet for three consecutive market days."""
from pathlib import Path
import argparse,json
import numpy as np
import pandas as pd

def main():
    p=argparse.ArgumentParser();p.add_argument('--formal',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    f=pd.read_csv(a.formal/'sentiment_daily.csv',dtype={'date':str}).set_index('date')
    raw=pd.read_csv(a.formal/'daily_raw.csv',dtype={'date':str}).set_index('date')
    cal=json.loads((a.formal/'calibration.json').read_text(encoding='utf-8'))
    events=pd.read_parquet(a.formal/'sample_reconciliation/events.parquet')
    cohorts=pd.read_parquet(a.formal/'sample_reconciliation/cohorts.parquet')
    rows=[];high_rows=[]
    def Q(name,value):
        lo,hi=cal['anchors'][name]
        return np.nan if pd.isna(value) else 50 if lo==hi else min(100,max(0,(value-lo)*100/(hi-lo)))
    def blend(values,weights):
        pairs=[(v,w) for v,w in zip(values,weights) if pd.notna(v)]
        return sum(v*w for v,w in pairs)/sum(w for v,w in pairs) if pairs else np.nan
    for day in ['20260909','20260910','20260911']:
        e=events[events.trade_date.astype(str)==day];c=cohorts[cohorts.date.astype(str)==day];r=f.loc[day];x=raw.loc[day]
        good=e[e.eligible];assert len(good)==r.eligible_n
        cents=lambda values:np.floor(values.to_numpy()*100+.5+1e-9).astype('int64')
        # Rebuild the price facts from raw prices instead of trusting event flags.
        close,high,up,down=map(cents,[good.close,good.high,good.up_limit,good.down_limit])
        np.testing.assert_array_equal(good.U,close==up)
        np.testing.assert_array_equal(good.Touch,high==up)
        np.testing.assert_array_equal(good.Z,(high==up)&(close!=up))
        np.testing.assert_array_equal(good.Dn,close==down)
        for flag,column in [('U','limit_up_n'),('Touch','touched_n'),('Z','broken_n'),('Dn','limit_down_n')]:assert good[flag].sum()==r[column]
        rates=[]
        for group,mask in [('h1',c.prev_height==1),('h2',c.prev_height==2),('h3',c.prev_height==3),('h4',c.prev_height>=4)]:
            g=c[mask&c.promotion_obs];k=int(g.promoted.sum());n=len(g)
            assert (k,n)==(x['k_'+group],x['n_'+group])
            rates.append((k+5*cal['priors'][group])/(n+5) if n else np.nan)
        M1=Q('M1raw',blend(rates,[.25,.30,.25,.20]))
        g=c[c.return_obs];P=.4*Q('P_open',g.oret.mean())+.6*Q('P_close',g.ret.mean())
        cc=c[(c.prev_height>=2)&c.return_obs];C=.4*Q('C_open',cc.oret.mean())+.6*Q('C_close',cc.ret.mean())
        levels=sorted(c.loc[c.prev_height>=2,'prev_height'].unique(),reverse=True)[:3]
        high=c[c.prev_height.isin(levels)&c.high_obs].copy();outcomes=[]
        for stock in high.itertuples():
            value=0 if stock.H2L or stock.Dn else 100 if stock.promoted else 15 if stock.cC*20<=stock.cP*19 else 35 if stock.cC<stock.cP else 50 if stock.cC==stock.cP else 75
            outcomes.append(value);high_rows.append({'trade_date':r.trade_date,'ts_code':stock.ts_code,'prev_height':stock.prev_height,'return':stock.ret,'close_cents':stock.cC,'reference_close_cents':stock.cP,'H2L':bool(stock.H2L),'limit_down':bool(stock.Dn),'promoted':bool(stock.promoted),'outcome':value,'recorded_outcome':stock.high_outcome})
            assert value==stock.high_outcome
        H=np.mean(outcomes) if outcomes else np.nan;M2=blend([P,C,H],[.4,.35,.25])
        breaks=[]
        for group in ['b1','b2','b3']:
            k,n=x['kz_'+group],x['nz_'+group];breaks.append((k+5*cal['priors'][group])/(n+5) if n else np.nan)
        SQ=1-blend(breaks,[.2,.3,.5]);M3=blend([Q('BL',x.BL),Q('SQ',SQ),100-Q('Ddens',x.Ddens)],[.4,.35,.25])
        mid=c[c.prev_height.between(2,4)&c.mid_obs];fail=(mid.cC*20<=mid.cP*19)|mid.Dn
        assert (int(fail.sum()),len(mid))==(x.k_mid,x.n_mid)
        M4=100-Q('MIDfail',(int(fail.sum())+5*cal['priors']['mid'])/(len(mid)+5)) if len(mid) else np.nan
        i=raw.index.tolist().index(day);previous=raw.iloc[i-20:i].amount
        assert len(previous)==20 and np.isclose(previous.mean(),x.amt_ma20)
        amount=e.loc[np.isfinite(e.amount)&(e.amount>0),'amount'].sum()*1000
        assert np.isclose(amount,x.amount)
        panic=x.ratio20>1 and good.ret.median()<0 and ((x.Ddens>0 and x.Ddens>=cal['panic_ddens_p90']) or int(high.H2L.sum())>=1)
        M5=min(50,Q('lr20',x.lr20)) if panic else Q('lr20',x.lr20)
        values=[M1,M2,M3,M4,M5];score=sum(v*w for v,w in zip(values,[25,20,20,15,10]))/90
        np.testing.assert_allclose(values,r[['M1','M2','M3','M4','M5']].astype(float),rtol=0,atol=1e-10)
        assert np.isclose(score,r.score5,rtol=0,atol=1e-10) and bool(panic)==bool(r.panic)
        rows.append({'trade_date':r.trade_date,'eligible_n':len(good),'limit_up_n':int(good.U.sum()),'broken_n':int(good.Z.sum()),'cohort_original_n':len(c),'cohort_observed_n':len(g),'high_levels':','.join(str(int(h)) for h in levels),'high_observed_n':len(high),'H_score_hand':H,'M1_hand':M1,'M2_hand':M2,'M3_hand':M3,'M4_hand':M4,'M5_hand':M5,'score_hand':score,'score_recorded':r.score5,'absolute_error':abs(score-r.score5),'turnover_cny':amount,'previous20_amount_mean':previous.mean(),'panic_hand':bool(panic)})
    pd.DataFrame(rows).to_csv(a.output/'three_day_hand_worksheet.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(high_rows).to_csv(a.output/'three_day_high_stock_worksheet.csv',index=False,encoding='utf-8-sig')
    result={'dates':['2026-09-09','2026-09-10','2026-09-11'],'independent_module_arithmetic':'passed','max_score_error':max(r['absolute_error'] for r in rows),'all_high_stocks_inspected':len(high_rows),'note':'Root must read the worksheet, inspect source rows and resolve differences; this arithmetic check is not worker acceptance by assertion.'}
    (a.output/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
if __name__=='__main__':main()
