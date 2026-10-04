"""Explain absent cohort observations using the same frozen identity snapshot."""
from pathlib import Path
import argparse,json
import pandas as pd


def main():
    p=argparse.ArgumentParser();p.add_argument('--formal',type=Path,required=True)
    p.add_argument('--raw-cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    candidates=list(a.raw_cache.glob('*/metadata.json'))
    assert len(candidates)==1,'Select an unambiguous raw-cache root'
    basic=pd.read_parquet(candidates[0].parent/'basic.parquet')
    c=pd.read_parquet(a.formal/'cohorts.parquet')
    x=c[c.missing|(c.found&~c.return_obs)].copy()
    x=x.merge(basic[['ts_code','name','delist_date']],on='ts_code',how='left',validate='many_to_one')
    x['trade_date']=pd.to_datetime(x.date).dt.strftime('%Y-%m-%d')
    x['delist_date']=x.delist_date.fillna('').astype(str)
    x['absence_explanation']=x.apply(lambda r:'local_basic_delisting_effective_by_date' if r['missing'] and r['delist_date'] and r['delist_date']<=r['date'] else 'unresolved_price_observation',axis=1)
    cols=['trade_date','ts_code','name','prev_height','found','paused','missing','return_obs','ref_ok','delist_date','absence_explanation']
    x[cols].to_csv(a.output/'cohort_exceptions.csv',index=False,encoding='utf-8-sig')
    result={'exception_rows':len(x),'explained_by_local_delisting':int(x.absence_explanation.eq('local_basic_delisting_effective_by_date').sum()),
            'unresolved_observation_rows':int(x.absence_explanation.eq('unresolved_price_observation').sum()),
            'score_or_denominator_changes':False,'basis':'Frozen local stock_basic delist_date; original missing observation count retained; not filled as promotion failure',
            'provider_first_publication_verified':False}
    (a.output/'cohort_exception_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
