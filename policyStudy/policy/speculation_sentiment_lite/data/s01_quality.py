"""Read-only, deterministic coverage/identity validation for S01."""
from __future__ import annotations
import argparse, csv, json, sqlite3, sys
from collections import defaultdict,Counter
from pathlib import Path
sys.dont_write_bytecode=True
from backfill_live import ro, csvout, dump, iso, compact, INDEX_CODES

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data-root',type=Path,required=True)
    p.add_argument('--audit-root',type=Path,required=True)
    p.add_argument('--start',default='20241201');p.add_argument('--end',default='20260930')
    p.add_argument('--names-only',action='store_true')
    a=p.parse_args()
    with (a.audit_root/'official_calendar_download.csv').open(encoding='utf-8') as f:
        days=sorted(r['cal_date'] for r in csv.DictReader(f) if r['exchange']=='SSE' and r['is_open']=='1')
    intervals=defaultdict(list)
    with (a.audit_root/'namechange_download.csv').open(encoding='utf-8') as f:
        for r in csv.DictReader(f):
            s=compact(r['start_date']);e=compact(r['end_date']) if r['end_date'] else '99991231'
            if len(s)==8 and s.isdigit() and e>=s:
                intervals[r['ts_code']].append((s,e,r['name'],r['ann_date']))
    basic={}; codes_by_day=defaultdict(set); limits_by_day=defaultdict(set); susp_by_day=defaultdict(set);key_checks={}
    with ro(a.data_root/'db_cn_basic.db') as c:
        basic={r[0]:{'list_date':r[1] or '', 'delist_date':r[2] or ''} for r in c.execute('select ts_code,list_date,delist_date from tbl_cn_basic')}
        if a.names_only:
            for d in days:
                codes_by_day[d]={code for code,r in basic.items() if code.startswith(('60','00','30','68')) and r['list_date'] and r['list_date']<=d and (not r['delist_date'] or d<=r['delist_date'])}
        else:
            for table,target in [('tbl_cn_day',codes_by_day),('tbl_cn_stk_limit',limits_by_day),('tbl_cn_suspend',susp_by_day)]:
                duplicate=0;null_key=0;seen=set();range_rows=0
                query=f'select trade_date,ts_code from {table} where trade_date between ? and ?'
                plan=[r[3] for r in c.execute('explain query plan '+query,(a.start,a.end))]
                if any('USING INDEX' in v and 'COVERING' not in v for v in plan):query=query.replace(f'from {table} where',f'from {table} NOT INDEXED where')
                print(json.dumps({'table':table,'original_query_plan':plan,'executed_query_plan':[r[3] for r in c.execute('explain query plan '+query,(a.start,a.end))]}),flush=True)
                for date,code in c.execute(query,(a.start,a.end)):
                    range_rows+=1;key=(date,code)
                    if key in seen:duplicate+=1
                    seen.add(key)
                    if not date or not code:null_key+=1;continue
                    if code.startswith(('60','00','30','68')):target[date].add(code)
                key_checks[table]={'range_rows':range_rows,'duplicate_code_date_keys':duplicate,'null_or_empty_key_rows':null_key}
                print(json.dumps({'read_table':table,'rows':sum(map(len,target.values()))}),flush=True)
    name_daily=[]; identity_gaps=[]; missing_intervals=defaultdict(list); market_daily=[]; limit_gaps=[]
    for d in days:
        codes=codes_by_day[d]; missing=[]; ambiguous=0; conflicting_st=0; same_start_st_conflicts=0
        for code in codes:
            candidates=[v for v in intervals.get(code,[]) if v[0]<=d<=v[1]]
            if not candidates:
                missing.append(code);missing_intervals[code].append(d)
            elif len(candidates)>1:
                ambiguous+=1
                if len({'ST' in v[2].upper() for v in candidates})>1:conflicting_st+=1
                latest=max(v[0] for v in candidates)
                if len({'ST' in v[2].upper() for v in candidates if v[0]==latest})>1:same_start_st_conflicts+=1
        unknown=sorted(codes-set(basic))
        if unknown: identity_gaps.extend({'trade_date':iso(d),'ts_code':code,'reason':'no_basic_identity'} for code in unknown)
        name_daily.append({'trade_date':iso(d),'observed_or_listed_codes':len(codes),'historical_name_available':len(codes)-len(missing),
            'historical_name_missing':len(missing),'overlap_candidate_codes':ambiguous,'overlap_conflicting_st_codes':conflicting_st,
            'latest_start_conflicting_st_codes':same_start_st_conflicts,'basis':'listed_reference' if a.names_only else 'actual_daily_rows'})
        if not a.names_only:
            lacks=sorted(codes-limits_by_day[d]);limit_gaps.extend({'trade_date':iso(d),'ts_code':code,'reason':'daily_without_limit'} for code in lacks)
            market_daily.append({'trade_date':iso(d),'sh_sz_daily_n':len(codes),'sh_sz_limit_n':len(limits_by_day[d]),
                'daily_without_limit_n':len(lacks),'daily_without_basic_n':len(unknown),'suspend_event_codes':len(susp_by_day[d]),
                'basic_codes_with_list_date':sum(bool(basic.get(code,{}).get('list_date')) for code in codes),
                'missing_listing_date_n':sum(not bool(basic.get(code,{}).get('list_date')) for code in codes)})
    name_missing=[{'ts_code':code,'missing_days':len(dd),'first_missing':iso(min(dd)),'last_missing':iso(max(dd))} for code,dd in sorted(missing_intervals.items())]
    prefix='preliminary_' if a.names_only else ''
    csvout(a.audit_root/f'{prefix}namechange_daily_coverage.csv',name_daily)
    csvout(a.audit_root/f'{prefix}namechange_missing_intervals.csv',name_missing,['ts_code','missing_days','first_missing','last_missing'])
    if not a.names_only:
        csvout(a.audit_root/'market_code_coverage.csv',market_daily)
        csvout(a.audit_root/'daily_limit_gaps.csv',limit_gaps,['trade_date','ts_code','reason'])
        csvout(a.audit_root/'daily_identity_gaps.csv',identity_gaps,['trade_date','ts_code','reason'])
        with ro(a.data_root/'db_cn_index.db') as c:
            index_counts=Counter((code,date) for code,date in c.execute('select ts_code,trade_date from tbl_cn_index_daily where trade_date between ? and ?',(a.start,a.end)))
        index_rows=[{'trade_date':iso(d),'ts_code':code,'required_main_five':code in INDEX_CODES[:5],
                     'row_count':index_counts[(code,d)],'status':'present' if index_counts[(code,d)] else 'missing'} for d in days for code in INDEX_CODES]
        csvout(a.audit_root/'index_code_coverage.csv',index_rows)
        dump(a.audit_root/'index_quality_summary.json',{'days':len(days),'main_five_codes':INDEX_CODES[:5],
             'main_five_missing':[r for r in index_rows if r['required_main_five'] and not r['row_count']],
             'optional_other_missing':[r for r in index_rows if not r['required_main_five'] and not r['row_count']]})
    summary={'trading_days':len(days),'price_rows':sum(map(len,codes_by_day.values())),
             'historical_name_missing_rows':sum(r['historical_name_missing'] for r in name_daily),
             'historical_name_missing_codes':len(name_missing),'overlap_candidate_rows':sum(r['overlap_candidate_codes'] for r in name_daily),
             'overlap_conflicting_st_rows':sum(r['overlap_conflicting_st_codes'] for r in name_daily),
             'latest_start_conflicting_st_rows':sum(r['latest_start_conflicting_st_codes'] for r in name_daily),
             'daily_limit_missing_rows':len(limit_gaps),'daily_basic_identity_missing_rows':len(identity_gaps),
             'key_checks':key_checks,'missing_listing_date_rows':sum(r['missing_listing_date_n'] for r in market_daily),
             'basis':'listed_reference' if a.names_only else 'actual_daily_rows'}
    dump(a.audit_root/f'{prefix}code_quality_summary.json',summary)
    print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
