"""Root integration checks against calendar, independent arithmetic and report totals."""
from pathlib import Path
import argparse, json, sys
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cycle import compute_cycle

def main():
    p=argparse.ArgumentParser()
    for k in ['formal','analysis','calendar','output']:p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();a.output.parent.mkdir(parents=True,exist_ok=True)
    raw=pd.read_csv(a.formal/'daily_raw.csv',dtype={'date':str},float_precision='round_trip')
    full=pd.read_csv(a.formal/'sentiment_daily.csv',float_precision='round_trip');full.trade_date=pd.to_datetime(full.trade_date).dt.strftime('%Y-%m-%d')
    daily=pd.read_csv(a.analysis/'environment_daily.csv',float_precision='round_trip');daily.trade_date=pd.to_datetime(daily.trade_date).dt.strftime('%Y-%m-%d')
    calendar=pd.read_csv(a.calendar,dtype=str)
    dates=sorted(set(pd.to_datetime(calendar.loc[(calendar.exchange=='SSE')&(calendar.is_open=='1'),'cal_date']).dt.strftime('%Y-%m-%d')))
    expected=[d for d in dates if '2026-01-01'<=d<='2026-09-30']
    assert daily.trade_date.tolist()==expected
    assert daily.trade_date.is_unique and full.trade_date.is_unique
    weights=np.array([25,20,20,15,10],dtype=float)
    manual=daily[['M1','M2','M3','M4','M5']].to_numpy()@weights/90
    np.testing.assert_allclose(daily.score5,manual,rtol=0,atol=1e-10,equal_nan=True)
    grade=pd.cut(daily.score5,[-np.inf,35,50,65,80,np.inf],right=False,labels=['D','C','B','A','S']).astype('string')
    assert grade.fillna('UNKNOWN').equals(daily.grade5.astype('string').fillna('UNKNOWN'))
    cal=json.loads((a.formal/'calibration.json').read_text(encoding='utf-8-sig'))
    c=raw[raw.date.str.startswith('2025')]
    for g in ['h1','h2','h3','h4','b1','b2','b3','mid']:
        kp,nprefix=('kz_','nz_') if g.startswith('b') else ('k_','n_')
        k,n=c[kp+g].sum(),c[nprefix+g].sum()
        assert np.isclose(cal['priors'][g],k/n,rtol=0,atol=1e-14)
        assert cal['pooled_counts'][g]=={'k':int(k),'n':int(n)}
    for metric,anchors in cal['anchors'].items():
        values=c[metric].dropna().to_numpy()
        np.testing.assert_allclose(np.quantile(values,[.1,.9],method='linear'),anchors,rtol=0,atol=1e-13)
        assert cal['metric_valid_n'][metric]==len(values)
    robust=pd.read_csv(a.analysis/'robustness_summary.csv')
    a5=robust[robust.a.eq(5)].iloc[0]
    assert a5.paired_n==daily.score5.notna().sum()
    assert a5.max_absolute_score_difference<1e-10 and a5.grade_changed_n==a5.phase_changed_n==0
    # Recompute clipping counts directly, distinguishing equality and strict excess.
    clip=pd.read_csv(a.formal/'clipping_statistics.csv',dtype={'year':str})
    from score import smoothed
    s=smoothed(raw,cal['priors'],5)
    for r in clip.itertuples():
        x=s.loc[s.date.str.startswith(r.year),r.metric];lo,hi=cal['anchors'][r.metric]
        assert r.total_n==len(x) and r.valid_n==x.notna().sum() and r.missing_n==x.isna().sum()
        assert (r.clip_low_n,r.clip_high_n,r.endpoint_low_n,r.endpoint_high_n)==(int((x<lo).sum()),int((x>hi).sum()),int((x==lo).sum()),int((x==hi).sum()))
    monthly=pd.read_csv(a.analysis/'monthly_summary.csv')
    for r in monthly.itertuples():
        g=daily[daily.trade_date.str.startswith(r.month)]
        assert r.calendar_n==len(g) and r.score_valid_n==g.score5.notna().sum()
        assert np.isclose(r.score_mean,g.score5.mean(),atol=1e-12)
        assert sum(getattr(r,x+'_n') for x in ['S','A','B','C','D'])==r.score_valid_n
    for col,file in [('grade5','grade_transitions.csv'),('phase','phase_transitions.csv')]:
        t=pd.read_csv(a.analysis/file);valid=set(t['from']);adj=daily[col].isin(valid)&daily[col].shift(-1).isin(valid)
        assert t['count'].sum()==adj.sum()
        for start,g in t.groupby('from'):
            assert g.denominator.nunique()==1
            assert g['count'].sum()==g.denominator.iloc[0]
            if g.denominator.iloc[0]:assert np.isclose(g.probability.sum(),1,atol=1e-14)
    # C0 prefixes compare all emitted evidence fields, not only labels.
    cyc=compute_cycle(full)
    prefix=[]
    for cutoff in ['2026-03-31','2026-06-30','2026-09-09']:
        x=compute_cycle(full[full.trade_date<=cutoff]);y=cyc[cyc.trade_date<=cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(x,y)
        prefix.append({'cutoff':cutoff,'days_including_2025':len(x),'all_cycle_fields_equal':True})
    summary={'calendar_rows':len(expected),'score_valid_rows':int(daily.score5.notna().sum()),'theme_valid_rows':int(daily.theme_heat_score.notna().sum()),'outer_weight_max_error':float(np.nanmax(np.abs(daily.score5-manual))),'priors_recomputed_from_2025':8,'clipping_rows_independently_checked':len(clip),'monthly_and_transition_denominators':'passed','cycle_prefixes':prefix,'status':'passed'}
    a.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
