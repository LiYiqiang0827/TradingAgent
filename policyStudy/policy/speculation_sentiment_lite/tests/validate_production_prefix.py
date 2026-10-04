"""Recompute F1 from physically truncated raw frames and compare full evidence."""
from pathlib import Path
import argparse,json,subprocess,sys,hashlib
import pandas as pd

def main():
    p=argparse.ArgumentParser()
    for k in ['data-root','full-formal','input-manifest','cache-dir','output']:p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--cutoffs',default='20260331,20260630,20260909,20260930')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    before={p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in a.data_root.glob('db_cn_*.db')}
    source=a.full_formal
    daily=pd.read_csv(source/'sentiment_daily.csv',dtype={'date':str})
    raw=pd.read_csv(source/'daily_raw.csv',dtype={'date':str})
    events=pd.read_parquet(source/'events.parquet');cohorts=pd.read_parquet(source/'cohorts.parquet')
    results=[]
    for cutoff in a.cutoffs.split(','):
        out=a.output/cutoff
        cmd=[sys.executable,str(Path(__file__).resolve().parents[1]/'production.py'),'--data-root',str(a.data_root),'--output',str(out),'--end',cutoff,'--fixed-calibration',str(source/'calibration.json'),'--input-manifest',str(a.input_manifest),'--cache-dir',str(a.cache_dir)]
        with (a.output/(cutoff+'.log')).open('w',encoding='utf-8') as log:run=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,text=True,encoding='utf-8')
        if run.returncode:raise RuntimeError(f'Prefix/repeat computation failed {cutoff}: see log')
        x=pd.read_csv(out/'sentiment_daily.csv',dtype={'date':str});y=daily[daily.date<=cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(x,y,check_dtype=False,check_exact=True)
        x=pd.read_csv(out/'daily_raw.csv',dtype={'date':str});y=raw[raw.date<=cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(x,y,check_dtype=False,check_exact=True)
        ev=pd.read_parquet(out/'events.parquet');expected=events[events.trade_date.astype(str)<=cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(ev,expected,check_dtype=False,check_categorical=False,check_exact=True)
        co=pd.read_parquet(out/'cohorts.parquet');expected=cohorts[cohorts.date.astype(str)<=cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(co,expected,check_dtype=False,check_categorical=False,check_exact=True)
        same_cal=(out/'calibration.json').read_bytes()==(source/'calibration.json').read_bytes()
        assert same_cal
        row={'cutoff':cutoff,'kind':'repeat_full' if cutoff=='20260930' else 'prefix','daily_rows':int((daily.date<=cutoff).sum()),'raw_rows_with_warmup':len(y),'events':len(ev),'cohort_rows':len(co),'daily_raw_all_fields_exact':True,'events_all_fields_exact':True,'cohorts_all_fields_exact':True,'same_calibration_bytes':True}
        results.append(row);(a.output/'validation.json').write_text(json.dumps(results,indent=2),encoding='utf-8');print(json.dumps(row),flush=True)
    after={p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in a.data_root.glob('db_cn_*.db')}
    assert before==after
    (a.output/'nonmutation.json').write_text(json.dumps({'same_live_file_size_mtime_ns':True,'files':list(before),'connections':'mode=ro&immutable=1, query_only; input content fingerprints recorded before runs'},indent=2),encoding='utf-8')
if __name__=='__main__':main()
