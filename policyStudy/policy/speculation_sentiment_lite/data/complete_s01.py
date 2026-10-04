"""Resumable bounded S01 completion after verified LIVE restoration.

All network writes are delegated to backfill_live's asserted LIVE-only adapter.
This command never restores/overwrites databases and never writes KPL again.
"""
import argparse,csv,json,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
from backfill_live import ro,dump,now

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--live-root',type=Path,required=True);p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--run-root',type=Path,required=True);p.add_argument('--execution-brief',type=Path)
    p.add_argument('--start',default='20241101');p.add_argument('--end',default='20260930')
    p.add_argument('--finalize-only',action='store_true',help='Use completed audits/manifest without another download or database write')
    a=p.parse_args();a.live_root=a.live_root.resolve();audit=a.run_root.resolve()/'data_audit'
    restore=json.loads((audit/'basic_restore.json').read_text(encoding='utf-8'))
    if restore['status']!='done' or not (a.live_root/'db_cn_basic.db').is_file():raise RuntimeError('Verified standard LIVE basic is not ready')
    code=Path(__file__).resolve().parent;state={'status':'running','started_at':now(),'steps':[]}
    state_path=audit/'completion_state.json'
    common=['--repo',str(a.repo),'--data-root',str(a.live_root),'--live-root',str(a.live_root),'--audit-root',str(audit),'--source-root',str(a.source_root),'--start',a.start,'--end',a.end]
    def run(label,script,args):
        command=[sys.executable,str(code/script),*args]
        result=subprocess.run(command)
        state['steps'].append({'label':label,'command':command,'exit_code':result.returncode,'finished_at':now()})
        dump(state_path,state)
        if result.returncode:raise RuntimeError(f'Step failed: {label}, {result.returncode}')
    if not a.finalize_only:
        run('calendar','backfill_live.py',['--mode','calendar',*common])
        run('basic_identity','backfill_live.py',['--mode','backfill',*common,'--kinds','basic'])
        with (audit/'official_calendar_download.csv').open(encoding='utf-8') as f:
            days=sorted(r['cal_date'] for r in csv.DictReader(f) if r['exchange']=='SSE' and r['is_open']=='1')
        dates=[d for d in days if d=='20260910' or d>='20260918']
        run('basic_market_backfill','backfill_live.py',['--mode','backfill',*common,'--dates',','.join(dates),'--kinds','daily,limit,suspend'])
        run('namechange_load','backfill_live.py',['--mode','namechange',*common])
        quality_args=['--data-root',str(a.live_root),'--audit-root',str(audit),'--start',a.start,'--end',a.end]
        run('code_quality','s01_quality.py',quality_args)
        quality=json.loads((audit/'code_quality_summary.json').read_text(encoding='utf-8'))
        if quality['historical_name_missing_codes']:
            run('namechange_bounded_remaining_repair','backfill_live.py',['--mode','namechange',*common,'--repair-codes-from',str(audit/'namechange_missing_intervals.csv')])
            run('code_quality_after_repair','s01_quality.py',quality_args)
        run('final_coverage','backfill_live.py',['--mode','audit',*common])
        run('frozen_warmup_audit','backfill_live.py',['--mode','audit',*sum(([flag,value] for flag,value in [('--repo',str(a.repo)),('--data-root',str(a.source_root)),('--live-root',str(a.live_root)),('--audit-root',str(audit)),('--start',a.start),('--end',a.end),('--prefix','frozen_final_')]),[])])
        manifest_args=['--mode','manifest',*common]
        if a.execution_brief:manifest_args+=['--execution-brief',str(a.execution_brief)]
        run('input_manifest','backfill_live.py',manifest_args)
    else:
        state=json.loads(state_path.read_text(encoding='utf-8'));state['finalize_resumed_at']=now()
    quality=json.loads((audit/'code_quality_summary.json').read_text(encoding='utf-8'))
    coverage=json.loads((audit/'coverage_summary.json').read_text(encoding='utf-8'))
    index_quality=json.loads((audit/'index_quality_summary.json').read_text(encoding='utf-8')) if (audit/'index_quality_summary.json').exists() else None
    manifest=json.loads((audit/'input_manifest.json').read_text(encoding='utf-8'))
    if not manifest.get('finished_at') or set(manifest['databases'])!={'basic','kpl','index'}:raise RuntimeError('Manifest is unfinished')
    with ro(a.live_root/'db_cn_basic.db') as c:
        neighbor={d:c.execute('select count(*) from tbl_cn_day where trade_date=?',(d,)).fetchone()[0] for d in ['20260909','20260910','20260911']}
        identities=c.execute('select count(*) from tbl_cn_basic').fetchone()[0]
        names=c.execute('select count(*) from tbl_cn_namechange').fetchone()[0]
        name_duplicate=c.execute('select count(*) from (select raw_record_hash,count(*) n from tbl_cn_namechange group by raw_record_hash having n>1)').fetchone()[0]
    required=['tbl_cn_day','tbl_cn_stk_limit','tbl_cn_kpl_list','tbl_cn_limit_list','tbl_cn_index_daily']
    missing={k:coverage['missing_days'][k] for k in required if coverage['missing_days'][k]}
    key_fail={k:v for k,v in quality['key_checks'].items() if v['duplicate_code_date_keys'] or v['null_or_empty_key_rows']}
    ready=not missing and not key_fail and (index_quality is None or not index_quality['main_five_missing'])
    local_exclusions=quality['historical_name_missing_rows'] or quality['daily_basic_identity_missing_rows'] or quality['missing_listing_date_rows']
    result={'task_id':'S01','status':'input_ready_with_local_exclusions_for_parent_acceptance' if ready and local_exclusions else 'input_ready_for_parent_acceptance' if ready else 'partial','input_ready':ready,
            'model_configuration':{'model':'gpt-6.1-sol','reasoning_effort':'xhigh','basis':'parent supplied native worker configuration; independent runtime introspection unavailable'},
            'input_snapshot_id':manifest['snapshot_id'],'sse_open_days':coverage['sse_open_days'],'date_range':[a.start,a.end],
            'neighbor_daily_rows':neighbor,'basic_identity_rows':identities,'namechange_rows':names,'duplicate_namechange_hash_keys':name_duplicate,
            'quality':quality,'required_missing_days':missing,'key_failures':key_fail,
            'index_quality':index_quality,
            'local_identity_policy':'Missing historical name/listing identity is excluded per stock-day and reported; it does not invalidate the entire market day or block readable input readiness',
            'local_identity_exclusions':json.loads((audit/'local_identity_exclusion.json').read_text(encoding='utf-8')) if (audit/'local_identity_exclusion.json').exists() else None,
            'optional_daily_basic_missing_days':coverage['missing_days']['tbl_cn_daily_basic'],
            'optional_daily_basic_policy':'Not needed by the frozen five modules; preserved historical table, no unrelated tail download',
            'restore_evidence':restore,'source_safety':manifest['source_safety'],'writer_transactions_closed':True,
            'files':[str(f) for f in audit.glob('*') if f.is_file()],
            'namechange_completeness':'Global empty-tail pagination plus fixed single-code discrepancies and bounded actual coverage repairs; no claim of completeness outside actual research stock-day range',
            'remaining':'Parent independent acceptance; localized price/limit quality exclusions passed to F1; provider first-publication timestamps unverified',
            'finished_at':now()}
    dump(a.run_root/'tasks/S01_result.json',result)
    state['status']='complete' if ready else 'partial';state['finished_at']=now();dump(state_path,state)
    if ready:dump(audit/'input_ready.json',{'input_ready':True,'writer_transactions_closed':True,'snapshot_id':manifest['snapshot_id'],'manifest':str(audit/'input_manifest.json'),'finished_at':now()})
    print(json.dumps({k:result[k] for k in ['task_id','status','input_ready','sse_open_days','neighbor_daily_rows','namechange_rows','required_missing_days']},ensure_ascii=False),flush=True)
    if not ready:raise SystemExit(2)
if __name__=='__main__':main()
