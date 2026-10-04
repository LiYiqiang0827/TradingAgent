"""Download-only evidence for existing suspend endpoint/key collisions."""
import argparse,json
from pathlib import Path
from backfill_live import online,Writer,dump,now

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--live-root',type=Path,required=True);p.add_argument('--audit-root',type=Path,required=True)
    p.add_argument('--date',required=True);a=p.parse_args();a.live_root=a.live_root.resolve();a.data_root=a.live_root;a.audit_root=a.audit_root.resolve()
    cl,upsert,paths=online(a);w=Writer(a,cl,upsert,paths)
    df=w.fetch('suspend_d',{'trade_date':a.date},'tbl_cn_suspend',a.date,5000)
    # No Writer.write call: this is API evidence only and opens no writable DB.
    df.to_csv(a.audit_root/f'suspend_response_{a.date}.csv',index=False,encoding='utf-8')
    duplicate=df[df.duplicated(['ts_code','trade_date'],keep=False)]
    result={'downloaded_at':now(),'trade_date':a.date,'raw_response_rows':len(df),
            'unique_existing_pk_rows':len(df.drop_duplicates(['ts_code','trade_date'])),
            'source_rows_sharing_existing_pk':duplicate.to_dict('records'),
            'policy':'Existing table PK(ts_code,trade_date) collapses provider same-day multiple suspension events; original conflicting response retained in audit CSV; no source DB or LIVE writes'}
    dump(a.audit_root/f'suspend_response_quality_{a.date}.json',result);print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
