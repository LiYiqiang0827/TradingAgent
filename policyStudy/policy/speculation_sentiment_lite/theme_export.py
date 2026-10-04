"""Read existing historical theme-review interface; never writes source databases."""
from pathlib import Path
import argparse, os, sys, json, hashlib
import pandas as pd

def main():
    p=argparse.ArgumentParser(); p.add_argument('--repo-root',type=Path,required=True); p.add_argument('--theme-db',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--start',default='20260101'); p.add_argument('--end',default='20260930');p.add_argument('--from-json',type=Path)
    a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    os.environ['TRADING_AGENT_THEME_GRAPH_DB_PATH']=str(a.theme_db); os.environ['TRADING_AGENT_FROZEN']='1'
    sys.path[:0]=[str(a.repo_root),str(a.repo_root/'offlineDataManager'/'scripts')]
    if a.from_json:
        reviews=json.loads(a.from_json.read_text(encoding='utf-8'))
    else:
        # Reuse the exact store implementation behind coreClient's public interface.
        # This avoids importing unrelated providers in the older MrWu checkout.
        from core.theme_graph_store import ThemeGraphStore
        reviews=ThemeGraphStore(database=a.theme_db).query_market_theme_review_series(a.start,a.end,top_n=10,leader_count=3)
    (a.output/'theme_review_raw.json').write_text(json.dumps(reviews,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    daily=[]; ranks=[]
    for r in reviews:
        t=pd.to_datetime(r['trade_date']).strftime('%Y-%m-%d'); st=r.get('structure',{})
        daily.append({'trade_date':t,'theme_heat_score':r.get('theme_sentiment_score'),'theme_heat_level':r.get('theme_sentiment_level'),'top3_heat_composite':r.get('top3_heat_composite'),'history_window_sessions':r.get('history_window_sessions'),'structure':st.get('code'),'structure_label':st.get('label'),'structure_evidence':json.dumps(st,ensure_ascii=False),'theme_method_version':r.get('method_version'),'point_in_time':r.get('point_in_time'),'theme_quality':'source_tags_first_publication_unverified','top3_themes':' / '.join(x.get('theme',x.get('canonical_name',x.get('name',''))) for x in r.get('hot_themes',[])[:3])})
        for i,x in enumerate(r.get('hot_themes',[])):
            ranks.append({'trade_date':t,'rank':i+1,**{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in x.items()}})
    df=pd.DataFrame(daily); assert not df.trade_date.duplicated().any()
    df.to_csv(a.output/'theme_context_daily.csv',index=False,encoding='utf-8-sig'); pd.DataFrame(ranks).to_csv(a.output/'theme_rank_daily.csv',index=False,encoding='utf-8-sig')
    summary={'days':len(df),'start':df.trade_date.min(),'end':df.trade_date.max(),'method_versions':df.theme_method_version.unique().tolist(),'all_point_in_time':bool(df.point_in_time.all()),'source_db_sha256':hashlib.file_digest(a.theme_db.open('rb'),'sha256').hexdigest(),'source_timestamp_limit':'Point-in-time algorithmic slicing does not authenticate original KPL publication or revisions.'}
    (a.output/'theme_validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__': main()
