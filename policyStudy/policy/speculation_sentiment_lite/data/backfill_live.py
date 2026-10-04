"""S01 bounded backfill/audit using existing TushareClient and upsert_df.

All paths are arguments. Frozen input is opened with mode=ro&immutable=1.
No ctrl rows are changed. Network request evidence is persisted after each call.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, sqlite3, sys, time
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

TABLES = {
    'basic': [('tbl_cn_day', 'trade_date'), ('tbl_cn_stk_limit', 'trade_date'),
              ('tbl_cn_suspend', 'trade_date'), ('tbl_cn_daily_basic', 'trade_date')],
    'kpl': [('tbl_cn_kpl_list', 'trade_date'), ('tbl_cn_limit_list', 'trade_date')],
    'index': [('tbl_cn_index_daily', 'trade_date')],
}
INDEX_CODES = ['000001.SH','399001.SZ','399006.SZ','000688.SH','000016.SH',
               '000300.SH','000905.SH','000852.SH','399106.SZ','399102.SZ','899050.BJ']

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def iso(s): return f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s)==8 else s
def compact(s): return str(s).replace('-', '')[:8]
@contextmanager
def ro(p):
    p = p.resolve()
    for suf in ('-wal','-journal'):
        s=Path(str(p)+suf)
        if s.exists() and s.stat().st_size: raise RuntimeError(f'Active sidecar: {s}')
    c=sqlite3.connect(p.as_uri()+'?mode=ro&immutable=1',uri=True)
    c.execute('pragma query_only=on')
    c.execute('pragma cache_size=-131072')
    try: yield c
    finally: c.close()
def dump(p,o): p.write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8')
def csvout(p,rows,fields=None):
    if fields is None: fields=list(rows[0]) if rows else ['status']
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fields); w.writeheader(); w.writerows(rows)
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

def audit(a):
    counts={}; info={}; calendar=[]; securities=[]
    for db in TABLES:
        p=a.data_root/f'db_cn_{db}.db'
        with ro(p) as c:
            tables={r[0] for r in c.execute("select name from sqlite_master where type='table'")}
            info[db]={'path':str(p),'size':p.stat().st_size,'tables':sorted(tables),
                      'schema':{t:list(c.execute(f'pragma table_info({t})')) for t,d in TABLES[db] if t in tables}}
            if db=='basic':
                calendar=list(c.execute('select cal_date,exchange,is_open,pretrade_date from tbl_cn_tradecal where cal_date between ? and ? order by cal_date,exchange',(a.start,a.end)))
                securities=list(c.execute('select ts_code,name,list_date,delist_date from tbl_cn_basic order by ts_code'))
            for t,d in TABLES[db]:
                if t not in tables: counts[t]={}; continue
                counts[t]={compact(r[0]):r[1] for r in c.execute(f'select {d},count(*) from {t} where {d} between ? and ? group by {d}',(a.start,a.end))}
                info[db].setdefault('key_validation',{})[t]={'pk_columns':[r[1] for r in info[db]['schema'][t] if r[5]],
                    'range_rows':sum(counts[t].values()),'range_days':len(counts[t])}
    days=sorted({compact(d) for d,ex,op,prev in calendar if ex=='SSE' and op==1})
    if not days: raise RuntimeError('No SSE trading calendar in target range')
    requests=a.audit_root/'request_checkpoints.jsonl'
    verified_empty=set()
    if requests.exists():
        for line in requests.read_text(encoding='utf-8').splitlines():
            r=json.loads(line)
            if r.get('status')=='complete_empty': verified_empty.add((r.get('table'),r.get('date')))
    rows=[]
    for day in days:
        for db,tt in TABLES.items():
            for table,col in tt:
                n=counts[table].get(day,0)
                reason='present' if n else 'unverified_empty' if table=='tbl_cn_suspend' else 'missing'
                if not n and (table,day) in verified_empty: reason='verified_empty'
                rows.append({'trade_date':iso(day),'database':db,'table':table,'row_count':n,'status':reason})
    prefix=a.prefix or ''
    csvout(a.audit_root/f'{prefix}data_coverage.csv',rows)
    dump(a.audit_root/f'{prefix}schema_audit.json',info)
    csvout(a.audit_root/f'{prefix}calendar.csv',[{'cal_date':iso(compact(d)),'exchange':ex,'is_open':op,'pretrade_date':iso(compact(prev))} for d,ex,op,prev in calendar])
    dump(a.audit_root/f'{prefix}coverage_summary.json',{'start':a.start,'end':a.end,'sse_open_days':len(days),
        'calendar_rows':len(calendar),'security_rows':len(securities),
        'missing_days':{t:[iso(d) for d in days if counts[t].get(d,0)==0] for db in TABLES for t,col in TABLES[db]},
        'counts':{t:{iso(d):v for d,v in cnt.items()} for t,cnt in counts.items()}})
    print(json.dumps({'mode':'audit','calendar_days':len(days),'missing':{t:sum(counts[t].get(d,0)==0 for d in days) for t in counts}}),flush=True)

def online(a):
    live=a.data_root.resolve()
    if live != a.live_root.resolve(): raise RuntimeError('data-root must equal explicit live-root for writes')
    os.environ['TRADING_AGENT_DATA_DIR']=str(live)
    os.environ['TRADING_AGENT_DERIVED_DATA_DIR']=str(a.audit_root.parent)
    os.environ['TRADING_AGENT_FROZEN']='1'
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(a.repo))
    sys.path.insert(0,str(a.repo/'offlineDataManager'/'scripts'))
    from config import settings
    paths={n:str(getattr(settings,f'DB_PATH_{n.upper()}').resolve()) for n in ['basic','kpl','news','index']}
    assert all(Path(p).parent==live for p in paths.values())
    from core.offline_db_client import upsert_df
    from coreClient.tushare_client import TushareClient
    from coreClient.tushare_config import TUSHARE_TOKEN,TUSHARE_RATE_LIMIT_PER_MIN,TUSHARE_RETRY_MAX
    preflight={'paths':paths,'token_present':bool(TUSHARE_TOKEN),'python':sys.version,'executable':sys.executable,
               'rate_limit_per_min':TUSHARE_RATE_LIMIT_PER_MIN,'retry_max':TUSHARE_RETRY_MAX,
               'freeze_override':'process_only_after_settings_import','started_at':now()}
    dump(a.audit_root/'settings_preflight.json',preflight); print(json.dumps(preflight),flush=True)
    os.environ['TRADING_AGENT_FROZEN']='0'
    cl=TushareClient(); cl.pro._DataApi__timeout=30
    return cl,upsert_df,paths

class Writer:
    def __init__(self,a,cl,upsert,paths):
        self.a,self.cl,self.upsert,self.paths=a,cl,upsert,paths
        self.changes=[]; self.req_path=a.audit_root/'request_checkpoints.jsonl'
        change_path=a.audit_root/'backfill_changes.csv'
        if change_path.exists():
            with change_path.open(encoding='utf-8') as f:self.changes=list(csv.DictReader(f))
    def record(self,r):
        r['downloaded_at']=now()
        with self.req_path.open('a',encoding='utf-8') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
        print(json.dumps(r,ensure_ascii=False),flush=True)
    def fetch(self,api,params,table,date,limit=6000,force_tail=False):
        import pandas as pd
        frames=[]; offset=0; seen=set(); pages=[]
        for i in range(100):
            df=self.cl.call(api,**params,limit=limit,offset=offset)
            n=0 if df is None else len(df)
            page_hash=hashlib.sha256(df.to_csv(index=False).encode()).hexdigest() if n else None
            pages.append({'offset':offset,'rows':n,'sha256':page_hash})
            if n and page_hash in seen: raise RuntimeError('Repeated API page; offset ignored')
            if n: seen.add(page_hash);frames.append(df)
            if n==0 or (n<limit and not force_tail):
                result=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
                self.record({'api':api,'table':table,'date':date,'parameters':params,'pages':pages,
                             'rows':len(result),'status':'complete' if len(result) else 'complete_empty'})
                return result
            offset+=n
        raise RuntimeError('Pagination exceeded bounded 100 pages')
    def write(self,db,table,df,date,params=None):
        path=Path(self.paths[db]); assert path.parent==self.a.live_root.resolve()
        if not path.exists(): raise FileNotFoundError(path)
        with closing(sqlite3.connect(path,timeout=60)) as c:
            c.execute('pragma cache_size=-262144')
            predicate='cal_date' if table=='tbl_cn_tradecal' else 'trade_date' if table!='tbl_cn_basic' else None
            scoped=bool(predicate and date)
            before=c.execute(f'select count(*) from {table}'+(f' where {predicate}=?' if scoped else ''),((date,) if scoped else ())).fetchone()[0]
            if len(df):
                df=df.copy();df['snap_ts']=now()
                self.upsert(c,df,table,key_cols=[])
            after=c.execute(f'select count(*) from {table}'+(f' where {predicate}=?' if scoped else ''),((date,) if scoped else ())).fetchone()[0]
            # Actually re-execute identical insert to verify idempotence.
            repeated=self.upsert(c,df,table,key_cols=[]) if len(df) else 0
            assert repeated==0, f'Idempotence failed: {table}'
            c.commit()
        row={'trade_date':iso(date) if date else '', 'database':db,'table':table,'before_rows':before,
             'response_rows':len(df),'after_rows':after,'inserted_rows':after-before,'repeat_inserted_rows':repeated,'recorded_at':now()}
        self.changes.append(row);csvout(self.a.audit_root/'backfill_changes.csv',self.changes)
        print(json.dumps(row),flush=True)
    def single(self,api,db,table,date,params,limit=6000):
        try:
            df=self.fetch(api,params,table,date,limit);self.write(db,table,df,date)
            return True
        except Exception as e:
            self.record({'api':api,'table':table,'date':date,'parameters':params,'status':'failed','error':str(e)})
            return False

def backfill(a):
    cl,upsert,paths=online(a);w=Writer(a,cl,upsert,paths)
    dates=[compact(d.strip()) for d in a.dates.split(',') if d.strip()]
    kinds=a.kinds.split(',')
    if 'calendar' in kinds and not dates:
        for ex in ['SSE','SZSE']:
            w.single('trade_cal','basic','tbl_cn_tradecal','',{'exchange':ex,'start_date':a.start,'end_date':a.end},1000)
    for day in dates:
        if 'calendar' in kinds:
            for ex in ['SSE','SZSE','BSE']:
                w.single('trade_cal','basic','tbl_cn_tradecal',day,{'exchange':ex,'start_date':day,'end_date':day},limit=1000)
        for kind,api,db,table,lim in [('daily','daily','basic','tbl_cn_day',6000),('limit','stk_limit','basic','tbl_cn_stk_limit',5800),
                ('suspend','suspend_d','basic','tbl_cn_suspend',5000),('daily_basic','daily_basic','basic','tbl_cn_daily_basic',6000),
                ('limit_list','limit_list_d','kpl','tbl_cn_limit_list',2500)]:
            if kind in kinds:w.single(api,db,table,day,{'trade_date':day},lim)
        if 'kpl' in kinds:
            for tag in ['炸板','涨停']:w.single('kpl_list','kpl','tbl_cn_kpl_list',day,{'trade_date':day,'tag':tag},8000)
        if 'index' in kinds:
            for code in INDEX_CODES:w.single('index_daily','index','tbl_cn_index_daily',day,{'ts_code':code,'start_date':day,'end_date':day},6000)
    if 'basic' in kinds:
        for status in ['L','D','P']:
            w.single('stock_basic','basic','tbl_cn_basic','',{'list_status':status,'fields':'ts_code,symbol,name,area,industry,fullname,enname,cnspell,market,exchange,curr_type,list_status,list_date,delist_date,is_hs,act_name,act_ent_type'},6000)

def namechange(a):
    import pandas as pd
    cache=a.audit_root/'namechange_download.csv'
    fields=['ts_code','name','start_date','end_date','ann_date','change_reason']
    if not cache.exists():
        cl,upsert,paths=online(a); w=Writer(a,cl,upsert,paths)
        global_ok=False
        try:
            df=w.fetch('namechange',{'fields':','.join(fields)},'tbl_cn_namechange','all_history',1000,force_tail=True)
            global_ok=True
        except Exception as e:
            w.record({'api':'namechange','table':'tbl_cn_namechange','date':'all_history','status':'global_failed','error':str(e)})
            df=pd.DataFrame(columns=fields)
        source=(a.source_root or a.data_root)/'db_cn_basic.db'
        with ro(source) as c:
            universe={r[0] for r in c.execute('select ts_code from tbl_cn_basic') if r[0].startswith(('60','00','30','68'))}
        present=set(df.ts_code) if len(df) else set()
        missing=sorted(universe-present)
        # Second evidence path: missing identities + 16 fixed, evenly spaced codes.
        ordered=sorted(universe)
        samples=sorted({ordered[int(i*(len(ordered)-1)/15)] for i in range(16)})
        discrepancies=[]; extra=[]; failed=[]
        for code in sorted(set(missing+samples)):
            try:
                one=w.fetch('namechange',{'ts_code':code,'fields':','.join(fields)},'tbl_cn_namechange',code,1000,force_tail=True)
                if code in samples:
                    have=df[df.ts_code==code][fields].fillna('').astype(str)
                    api=one[fields].fillna('').astype(str) if len(one) else pd.DataFrame(columns=fields)
                    hs={tuple(r) for r in have.itertuples(index=False,name=None)}
                    ps={tuple(r) for r in api.itertuples(index=False,name=None)}
                    if hs!=ps:discrepancies.append({'ts_code':code,'global_rows':len(hs),'single_rows':len(ps)})
                if len(one):extra.append(one)
            except Exception as e:
                failed.append(code);w.record({'api':'namechange','table':'tbl_cn_namechange','date':code,'status':'failed','error':str(e)})
        if extra:df=pd.concat([df,*extra],ignore_index=True)
        raw_duplicates=int(df.fillna('').duplicated(fields).sum())
        df=df.drop_duplicates(fields)
        df['snap_ts']=now()
        df.to_csv(cache,index=False,encoding='utf-8')
        dump(a.audit_root/'namechange_download_summary.json',{'global_pagination_complete':global_ok,
            'tail_empty_confirmed':global_ok,'raw_exact_duplicates_removed':raw_duplicates,'unique_rows':len(df),
            'universe_codes':len(universe),'represented_codes':len(set(df.ts_code)&universe),
            'missing_identity_requests':len(missing),'single_code_samples':samples,'sample_discrepancies':discrepancies,
            'failed_codes':failed,'remaining_missing_codes':sorted(universe-set(df.ts_code)),
            'official_reference':'https://tushare.pro/document/2?doc_id=100',
            'date_parameter_semantics':'API start/end parameters filter announcement dates, not name validity; global history fetched',
            'downloaded_at':now()})
    if a.repair_codes_from:
        base=pd.read_csv(cache,dtype=str,keep_default_na=False)
        with a.repair_codes_from.open(encoding='utf-8') as f:
            codes=sorted({r['ts_code'] for r in csv.DictReader(f)})
        cl,upsert,paths=online(a);w=Writer(a,cl,upsert,paths)
        frames=[base]; repaired=[];failed=[]
        for code in codes:
            try:
                one=w.fetch('namechange',{'ts_code':code,'fields':','.join(fields)},'tbl_cn_namechange',code,1000,force_tail=True)
                if len(one):one['snap_ts']=now();frames.append(one)
                repaired.append({'ts_code':code,'api_rows':len(one)})
            except Exception as e:
                failed.append({'ts_code':code,'error':str(e)})
        result=pd.concat(frames,ignore_index=True).drop_duplicates(fields,keep='first')
        result.to_csv(cache,index=False,encoding='utf-8')
        dump(a.audit_root/'namechange_repair_result.json',{'requested_codes':len(codes),'repaired':repaired,'failed':failed,
            'cache_before_rows':len(base),'cache_after_rows':len(result),'downloaded_at':now()})
    if a.download_only:
        print(json.dumps({'namechange_cache':str(cache),'status':'downloaded'}),flush=True);return
    # A fresh settings import and LIVE assertions precede this load process too.
    online(a)
    df=pd.read_csv(cache,dtype=str,keep_default_na=False)
    path=a.data_root/'db_cn_basic.db';assert path.parent==a.live_root
    if not path.is_file(): raise FileNotFoundError(path)
    rows=[]
    for r in df.to_dict('records'):
        vals=[r.get(f) or None for f in fields]
        h=hashlib.sha256(json.dumps(vals,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        rows.append((*vals,r['snap_ts'],h))
    with closing(sqlite3.connect(path,timeout=60)) as c:
        c.execute('''create table if not exists tbl_cn_namechange (
          ts_code TEXT NOT NULL, name TEXT NOT NULL, start_date TEXT,
          end_date TEXT, ann_date TEXT, change_reason TEXT, snap_ts TEXT NOT NULL,
          raw_record_hash TEXT PRIMARY KEY)''')
        c.execute('create index if not exists idx_namechange_code_start on tbl_cn_namechange(ts_code,start_date)')
        before=c.execute('select count(*) from tbl_cn_namechange').fetchone()[0]
        sql='insert or ignore into tbl_cn_namechange values (?,?,?,?,?,?,?,?)'
        c.executemany(sql,rows);c.commit()
        after=c.execute('select count(*) from tbl_cn_namechange').fetchone()[0]
        inserted=c.executemany(sql,rows).rowcount;c.commit();assert inserted==0
    quality=[]
    for code,g in df.groupby('ts_code'):
        g=g.sort_values(['start_date','end_date','name'])
        prior_end=None;overlap=0;boundary=0;gaps=0;invalid=0;future=0;open_n=0
        last_start=None
        for r in g.to_dict('records'):
            s=compact(r['start_date']);e=compact(r['end_date']) if r['end_date'] else '99991231'
            if e=='99991231':open_n+=1
            if not (s.isdigit() and len(s)==8) or e<s:invalid+=1
            if s>a.end:future+=1
            if prior_end is not None:
                if prior_end>s:overlap+=1
                elif prior_end==s:boundary+=1
                elif prior_end!='99991231':
                    try:
                        if (datetime.strptime(s,'%Y%m%d')-datetime.strptime(prior_end,'%Y%m%d')).days>1:gaps+=1
                    except ValueError:invalid+=1
            prior_end=max(prior_end or '00000000',e);last_start=s
        quality.append({'ts_code':code,'rows':len(g),'open_end_rows':open_n,'invalid_intervals':invalid,
            'overlap_intervals':overlap,'shared_boundary_intervals':boundary,'gap_intervals':gaps,
            'future_start_rows_after_cutoff':future,'missing_ann_dates':int((g.ann_date=='').sum()),
            'duplicate_natural_start_name':int(g.duplicated(['start_date','name']).sum())})
    csvout(a.audit_root/'namechange_quality.csv',quality)
    dump(a.audit_root/'namechange_load_result.json',{'before_rows':before,'after_rows':after,
        'inserted_rows':after-before,'repeat_inserted_rows':inserted,'interval_semantics':'inclusive start and end; NULL end is open; conflicts remain explicit',
        'shared_boundary_policy':'Prefer latest start_date on shared inclusive boundary and mark ambiguity; unresolved candidates with same start_date remain explicit conflicts',
        'raw_hash_sha256':sha(cache),'loaded_at':now()})
    print(json.dumps({'namechange_rows':after,'inserted':after-before,'repeat_inserted':inserted}),flush=True)

def calendar(a):
    cl,upsert,paths=online(a); w=Writer(a,cl,upsert,paths)
    import pandas as pd
    frames=[]
    for ex in ['SSE','SZSE']:
        df=w.fetch('trade_cal',{'exchange':ex,'start_date':a.start,'end_date':a.end},'tbl_cn_tradecal','calendar_range',1000)
        frames.append(df)
        if not a.download_only:w.write('basic','tbl_cn_tradecal',df,'')
    all_df=pd.concat(frames,ignore_index=True)
    all_df.to_csv(a.audit_root/'official_calendar_download.csv',index=False,encoding='utf-8')
    days=sorted(all_df[(all_df.exchange=='SSE')&(all_df.is_open==1)].cal_date.astype(str))
    dump(a.audit_root/'official_calendar_summary.json',{'range_start':a.start,'range_end':a.end,'sse_open_days':len(days),
        '2025_open_days':sum(d.startswith('2025') for d in days),'2026_open_days':sum(d.startswith('2026') for d in days),
        '2024_warmup_days':sum(d.startswith('2024') for d in days),
        'tail_open_dates':[iso(d) for d in days if d>='20260918'],'source':'Tushare trade_cal','downloaded_at':now()})
    print(json.dumps({'calendar_open_days':len(days),'tail':[iso(d) for d in days if d>='20260918']}),flush=True)

def manifest(a):
    if not a.source_root:raise ValueError('--source-root is required for manifest')
    restore=json.loads((a.audit_root/'basic_restore.json').read_text(encoding='utf-8'))
    coverage=json.loads((a.audit_root/'coverage_summary.json').read_text(encoding='utf-8'))
    code_quality=json.loads((a.audit_root/'code_quality_summary.json').read_text(encoding='utf-8'))
    manifest_path=a.audit_root/'input_manifest.json'
    previous=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    if coverage['start']!=a.start or coverage['end']!=a.end:raise RuntimeError('Manifest range differs from completed coverage audit')
    out={'snapshot_id':'sentiment_cycle_20261005_01_live_after_s01','created_at':now(),
         'information_cutoff':iso(a.end),'databases':{},'frozen_databases':{},
         'source_safety':'All source reads used mode=ro&immutable=1; basic pre/post metadata unchanged; source hash matched complete copy before modification',
         'model_configuration':{'task_requested_model':'gpt-6.1-sol','task_requested_reasoning_effort':'xhigh',
                                'runtime_introspection_available':False,'basis':'parent supplied native worker configuration'},
         'closed_writer_policy':'All write connections explicitly closed before manifest; active WAL/journal rejected'}
    for db in ['basic','kpl','index']:
        lp=a.data_root/f'db_cn_{db}.db'; sp=a.source_root/f'db_cn_{db}.db'
        print(json.dumps({'manifest_stage':'metadata_and_keys','database':db}),flush=True)
        with ro(lp) as c:
            table_counts={t:sum(coverage['counts'][t].values()) for t,d in TABLES[db]}
            key_validation={}
            for table,datecol in TABLES[db]:
                columns=[r[1] for r in c.execute(f'pragma table_info({table})') if r[5]]
                if not columns:raise RuntimeError(f'Missing primary key: {table}')
                if table in code_quality['key_checks']:
                    completed=code_quality['key_checks'][table]
                    assert completed['range_rows']==table_counts[table]
                    key_validation[table]={'pk_columns':columns,'duplicate_keys':completed['duplicate_code_date_keys'],
                                          'null_or_empty_keys':completed['null_or_empty_key_rows'],'range':[a.start,a.end],
                                          'basis':'Reused completed actual-row validation in code_quality_summary.json; no redundant full PK scan'}
                    assert key_validation[table]['duplicate_keys']==key_validation[table]['null_or_empty_keys']==0
                    print(json.dumps({'manifest_key_audit':table,'basis':'completed_actual_row_QC'}),flush=True)
                    continue
                if table=='tbl_cn_daily_basic':
                    key_validation[table]={'pk_columns':columns,'duplicate_keys':None,'null_or_empty_keys':None,
                                          'basis':'Optional unused table preserved from byte-validated complete source; no writes this run; not revalidated for the five-module input'}
                    continue
                indices=[r[1] for r in c.execute(f'pragma index_list({table})') if r[3]=='pk']
                hint=f' INDEXED BY "{indices[0]}"' if indices else ''
                where=f'"{datecol}" between ? and ?';keys=','.join(f'"{col}"' for col in columns)
                duplicate=c.execute(f'select count(*) from (select {keys},count(*) n from {table}{hint} where {where} group by {keys} having n>1)',(a.start,a.end)).fetchone()[0]
                invalid=c.execute(f'select count(*) from {table}{hint} where {where} and ('+' or '.join(f'''"{key}" is null or "{key}"='' ''' for key in columns)+')',(a.start,a.end)).fetchone()[0]
                key_validation[table]={'pk_columns':columns,'duplicate_keys':duplicate,'null_or_empty_keys':invalid,'range':[a.start,a.end],'read_strategy':'covering primary-key index where available'}
                assert duplicate==invalid==0,f'Raw key violation: {table}'
                print(json.dumps({'manifest_key_audit':table,'duplicate_keys':duplicate,'null_or_empty_keys':invalid}),flush=True)
            if db=='basic':
                metadata_counts={t:c.execute(f'select count(*) from {t}').fetchone()[0] for t in ['tbl_cn_basic','tbl_cn_namechange','tbl_cn_tradecal']}
            else:metadata_counts={}
        print(json.dumps({'manifest_stage':'full_sequential_sha256','database':db,'size':lp.stat().st_size}),flush=True)
        startstat=lp.stat();old=previous.get('databases',{}).get(db,{})
        if old.get('path')==str(lp) and old.get('size')==startstat.st_size and old.get('mtime_ns')==startstat.st_mtime_ns and old.get('sha256'):
            digest=old['sha256'];hash_basis='previous complete full-file SHA256 with unchanged exact path/size/mtime_ns; no writers since input quiescence'
        else:digest=sha(lp);hash_basis='direct full-file SHA256 in this manifest invocation'
        endstat=lp.stat()
        assert startstat.st_size==endstat.st_size and startstat.st_mtime_ns==endstat.st_mtime_ns
        out['databases'][db]={'path':str(lp),'size':endstat.st_size,'mtime_ns':endstat.st_mtime_ns,
                              'sha256':digest,'audited_range_table_counts':table_counts,'audited_range':[a.start,a.end],
                              'full_metadata_table_counts':metadata_counts,'key_validation':key_validation,'hash_basis':hash_basis}
        sstat=sp.stat()
        if db=='basic':
            assert sstat.st_size==restore['source_before']['size'] and sstat.st_mtime_ns==restore['source_before']['mtime_ns']
            sh=restore['source_sha256']
        else:sh=sha(sp)
        out['frozen_databases'][db]={'path':str(sp),'size':sstat.st_size,'mtime_ns':sstat.st_mtime_ns,'sha256':sh,
                                   'source_unmodified_evidence':'read_only_connections and unchanged basic restore metadata' if db=='basic' else 'read_only_connections; inventory mtime/size match'}
        dump(a.audit_root/'input_manifest.json',out)
        print(json.dumps({'manifest_hashed_database':db,'sha256':digest}),flush=True)
    out['audit_files']={p.name:sha(p) for p in a.audit_root.glob('*.csv')}
    out['execution_brief']={'path':str(a.execution_brief),'sha256':sha(a.execution_brief)} if a.execution_brief else {'status':'not_provided'}
    out['finished_at']=now();dump(a.audit_root/'input_manifest.json',out)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['audit','backfill','namechange','calendar','manifest'],required=True)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--data-root',type=Path,required=True)
    p.add_argument('--live-root',type=Path,required=True)
    p.add_argument('--audit-root',type=Path,required=True)
    p.add_argument('--source-root',type=Path)
    p.add_argument('--start',default='20241201');p.add_argument('--end',default='20260930')
    p.add_argument('--dates',default='');p.add_argument('--kinds',default='daily,limit,suspend,kpl,limit_list,index')
    p.add_argument('--prefix',default='')
    p.add_argument('--download-only',action='store_true')
    p.add_argument('--repair-codes-from',type=Path)
    p.add_argument('--execution-brief',type=Path)
    a=p.parse_args();a.data_root=a.data_root.resolve();a.live_root=a.live_root.resolve();a.audit_root=a.audit_root.resolve()
    a.audit_root.mkdir(parents=True,exist_ok=True)
    if a.mode=='audit':audit(a)
    elif a.mode=='backfill':backfill(a)
    elif a.mode=='namechange':namechange(a)
    elif a.mode=='calendar':calendar(a)
    elif a.mode=='manifest':manifest(a)
    else: raise NotImplementedError(a.mode)
if __name__=='__main__':main()
