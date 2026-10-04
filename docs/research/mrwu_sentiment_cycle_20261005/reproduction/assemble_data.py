"""Publish a small, explicit selection of derived research evidence; no databases."""
from pathlib import Path
import argparse, json, shutil
import pandas as pd

def main():
    p=argparse.ArgumentParser()
    for name in ['formal','analysis','audit','theme','output']:
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    files=[]
    for src in sorted(a.analysis.glob('*.csv'))+sorted(a.analysis.glob('*.json')):
        shutil.copy2(src,a.output/src.name);files.append(src.name)
    for name in ['calibration.json','daily_raw.csv','quality_daily.csv','clipping_statistics.csv','reference_to_formal_diff.csv','external_reconciliation.csv','production_validation.json']:
        src=a.formal/name
        if src.exists():shutil.copy2(src,a.output/name);files.append(name)
    shutil.copy2(a.formal/'sentiment_daily.csv',a.output/'formal_daily_2025_2026.csv')
    for name in ['calendar.csv','data_coverage.csv','official_calendar_summary.json','namechange_daily_coverage.csv',
                 'backfill_changes.csv','code_quality_summary.json','market_code_coverage.csv','daily_identity_gaps.csv',
                 'daily_limit_gaps.csv','namechange_missing_intervals.csv','namechange_load_result.json',
                 'daily_0910_neighbor_reconciliation.json','index_code_coverage.csv','index_quality_summary.json',
                 'local_identity_exclusion.json','old_snapshot_identity_lookup.json']:
        src=a.audit/name
        if src.exists():shutil.copy2(src,a.output/name);files.append(name)
    # Public provenance keeps source identity/hash, without private machine paths.
    def portable(x):
        if isinstance(x,dict):
            return {k:Path(v).name if isinstance(v,str) and (':\\' in v or ':/' in v) else portable(v) for k,v in x.items()}
        if isinstance(x,list):return [portable(v) for v in x]
        return x
    for root,name,outname in [(a.formal,'input_manifest.json','input_manifest.json'),(a.theme,'theme_validation.json','theme_validation.json')]:
        if (root/name).exists():
            obj=portable(json.loads((root/name).read_text(encoding='utf-8-sig')))
            (a.output/outname).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');files.append(outname)
    summary=json.loads((a.analysis/'analysis_summary.json').read_text(encoding='utf-8'))
    rows=[{'number':k,'value':v,'source':'analysis_summary.json','filter':'2026-01-01..2026-09-30'} for k,v in summary.items() if isinstance(v,(int,float)) and not isinstance(v,bool)]
    for family in ['grade_counts','phase_counts']:
        rows.extend({'number':family+'.'+k,'value':v,'source':'analysis_summary.json','filter':'2026-01-01..2026-09-30'} for k,v in summary[family].items())
    for table,keys in [('monthly_summary.csv',['score_mean','calendar_n','score_valid_n']),('robustness_summary.csv',['mean_absolute_score_difference','max_absolute_score_difference','grade_changed_n','phase_changed_n'])]:
        f=pd.read_csv(a.analysis/table)
        for _,r in f.iterrows():
            for k in keys:rows.append({'number':k,'value':r[k],'source':table,'filter':str(r.get('month',r.get('a')))})
    pd.DataFrame(rows).to_csv(a.output/'report_numbers.csv',index=False,encoding='utf-8-sig')
    print(json.dumps({'published_files':sorted(files),'output':str(a.output)},ensure_ascii=False))

if __name__=='__main__':main()
