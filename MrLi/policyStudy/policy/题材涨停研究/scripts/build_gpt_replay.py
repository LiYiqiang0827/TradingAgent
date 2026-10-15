"""Prepare causal evidence packets; never read outcomes or old predictions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from build_case_dossiers import BASE, SOURCE, ROOT, normalize, save, dp
from stage1_engine import KEYWORDS

OUT=BASE/'data/gpt_replay_v2'
FORBIDDEN={'score','rule_score','state','phase','market_score','baseline_prediction','interpretation'}
COMPANY_WORDS=re.compile('公告|互动|业务|产品|客户|供货|订单|研发|收购|持股|投资|产能|产量|收入|营收|利润|否认|澄清|风险|量产|送样|批量')
AI_WORDS=re.compile('AI基于|由AI|AI生成|AI线索')
PRICE_WORDS=re.compile('收评|午评|涨停|跌停|涨超|股价|连板|复盘|龙虎榜|涨幅|盘中|盘初|资金流')


def h(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def facts(value):
    if isinstance(value,dict):return {k:facts(v) for k,v in value.items() if k not in FORBIDDEN}
    if isinstance(value,list):return [facts(v) for v in value]
    if isinstance(value,float):return round(value,4)
    return value


def text_key(row):
    body=re.sub(r'^(【[^】]*】|[^|]{1,100}\|)','',row['content'])
    return re.sub(r'[\W_]+','',body or row['title'])


def fetch(request):
    date,start,end=request
    path=OUT/'cache'/('news_'+date+'.pkl.gz')
    if path.exists():frame=pd.read_pickle(path)
    else:
        frame=dp.get_news(start_datetime=start,end_datetime=end,limit=None,source='database_only')
        frame.to_pickle(path,compression='gzip')
        save(path.with_suffix('.json'),{'start':start,'end':end,'rows':len(frame),'source':'data_provider.database_only'})
    times=pd.to_datetime(frame.datetime,format='mixed')
    assert times.between(pd.Timestamp(start),pd.Timestamp(end)).all()
    lp_path=OUT/'cache'/('performance_'+date+'.pkl')
    if lp_path.exists():lp=pd.read_pickle(lp_path)
    else:
        lp=dp.get_kpl_limit_performance(trade_date=date,source='database_only');lp.to_pickle(lp_path)
    return date,frame,lp


def standard_news(frame):
    rows=[];seen=set()
    for raw in frame.fillna('').sort_values('datetime',kind='stable').to_dict('records'):
        row={k:str(raw[k]) for k in ['md5','datetime','src','title','content']}
        row['content']=re.sub(r'<[^>]*>',' ',row['content'])
        if AI_WORDS.search(row['title']+' '+row['content']):continue
        identity=text_key(row)
        if not identity or identity in seen:continue
        seen.add(identity);rows.append(row)
    return rows


def company_prefix(row):
    text=(row['title'] or row['content']).lstrip('【[ ')
    prefix=re.split('[：:|丨]',text,1)[0].strip()
    return prefix if 2<=len(prefix)<=12 else None


def build_packet(records,kpl,lp,news,archive,date,cutoff):
    prefix=[r for r in records if r['date']<=date]
    assert prefix and prefix[-1]['date']==date
    current=prefix[-1];past=prefix[-21:-1]
    # Gate every input again, including provider cache hits.
    news=[r for r in news if pd.Timestamp(r['datetime'])<=pd.Timestamp(cutoff)]
    raw=kpl.loc[kpl.trade_date.eq(date)&kpl.tag.eq('涨停')].set_index('ts_code')
    lps=lp.loc[lp.trade_date.astype(str).str.replace('-','').eq(date)].set_index('ts_code') if len(lp) else pd.DataFrame()
    old_names=kpl.loc[kpl.trade_date.le(date)].groupby('ts_code')['name'].agg(lambda s:sorted(set(s)))
    selected={};company_ids={};groups=[]
    for leader in current['leaders']:
        code=leader['ts_code'];names=old_names.get(code,[leader['name']])
        matches=[]
        for name in names:
            for key,items in archive.items():
                if name==key or key.startswith(name+'(') or key.startswith(name+'（'):
                    matches.extend(r for r in items if pd.Timestamp(r['datetime'])<=pd.Timestamp(cutoff))
        # Latest distinct company statements, not the companies with best later returns.
        chosen=[];seen=set()
        for row in sorted(matches,key=lambda r:r['datetime'],reverse=True):
            key=text_key(row)
            if key in seen:continue
            seen.add(key);chosen.append(row)
            if len(chosen)==3:break
        company_ids[code]=[r['md5'] for r in chosen]
        selected.update({r['md5']:r for r in chosen})
    stocks={}
    for theme in sorted(current['themes'],key=lambda t:t['theme']):
        name=theme['theme'];members=[]
        terms=KEYWORDS.get(name,[name])
        candidates=[r for r in news if any(w.lower() in (r['title']+' '+r['content']).lower() for w in terms)]
        candidates.sort(key=lambda r:(bool(PRICE_WORDS.search(r['title'] or r['content'][:100])),
                                      -sum(w.lower() in r['title'].lower() for w in terms),r['datetime'],r['md5']))
        selected_theme_news=candidates[:3]
        selected.update({r['md5']:r for r in selected_theme_news})
        for leader in current['leaders']:
            if leader['theme']!=name:continue
            row=raw.loc[leader['ts_code']];regime='20%' if leader['ts_code'].startswith(('300','301','688','689')) else '10%'
            pr=lps.loc[leader['ts_code']] if len(lps) and leader['ts_code'] in lps.index else {}
            members.append(dict(facts(leader),regime=regime,first_time=row['lu_time'],last_time=row['last_time'],
                                amount_yuan=float(row['amount']),raw_multi_labels=row['theme'],lp_reason=pr.get('limit_reason'),
                                company_news_ids=company_ids[leader['ts_code']]))
        stocks[name]=members
        hs={regime:max([s['height'] for s in members if s['regime']==regime],default=0) for regime in ['10%','20%']}
        history=[next((t['breadth'] for t in p['themes'] if t['theme']==name),0) for p in past]
        groups.append(dict(facts(theme),heights_by_regime=hs,breadth_history=history,
                           news_ids=[r['md5'] for r in selected_theme_news],news_evidence=[]))
    packet={'date':date,'decision_at':cutoff,'data_mode':'historical source-time reconstruction; no first-publication archive',
            'market':facts(current['market']),'past_market':[dict(date=p['date'],**facts(p['market'])) for p in past[-5:]],
            'themes':groups,'stocks_by_theme':stocks,'news':selected,
            'prior_leaders':[{'date':p['date'],'leaders':[{'ts_code':l['ts_code'],'name':l['name'],'theme':l['theme'],'height':l['height']} for l in p['leaders']]} for p in past[-5:]],
            'rotation':facts(current['rotation']),'unknown_breaks':current['unknown_breaks'],
            'news_scope':'Current news since previous decision cutoff; company-headline archive from 2026-01-01. Max3 distinct latest statements per stock; exact historical names. Retrieval not exhaustive business due diligence.',
            'news_window_rows':len(news),'company_evidence_stock_count':sum(bool(v) for v in company_ids.values())}
    assert all(pd.Timestamp(r['datetime'])<=pd.Timestamp(cutoff) for r in selected.values())
    return facts(packet)


def main():
    (OUT/'cache').mkdir(parents=True,exist_ok=True);(OUT/'packets').mkdir(exist_ok=True)
    records=json.loads((SOURCE/'features.json').read_text())
    kpl=normalize(pd.read_pickle(SOURCE/'cache/kpl.pkl'))
    protocol=BASE/'research/gpt_replay_protocol_v2.json'
    refs=ROOT/'policyStudy/skills/a-share-theme-research/references'
    references={name:(refs/name).read_text() for name in ['methodology.md','blind-protocol.md','feature-dictionary.md','attribution-and-roles.md']}
    save(OUT/'judge_references.json',references)
    requests=[];start='2026-01-01 00:00:00'
    for i,r in enumerate(records):
        end=pd.Timestamp(records[i+1]['date']).strftime('%Y-%m-%d')+' 06:00:00' if i+1<len(records) else '2026-08-31 23:59:59'
        requests.append((r['date'],start,end));start=str(pd.Timestamp(end)+pd.Timedelta(seconds=1))
    archive={};manifest=[];raw_n=0
    with ThreadPoolExecutor(max_workers=3) as executor:
        for request,result in zip(requests,executor.map(fetch,requests)):
            date,frame,lp=result;raw_n+=len(frame);current_news=standard_news(frame)
            for row in current_news:
                key=company_prefix(row)
                if key and COMPANY_WORDS.search(row['title']+' '+row['content']):
                    archive.setdefault(key,[]).append(row)
                    archive[key]=archive[key][-15:]
            packet=build_packet(records,kpl,lp,current_news,archive,date,request[2])
            path=OUT/'packets'/(date+'.json');save(path,packet)
            manifest.append({'date':date,'decision_at':packet['decision_at'],'sha256':h(path),'bytes':path.stat().st_size,
                             'raw_news_rows':len(frame),'selected_news_ids':len(packet['news']),
                             'company_evidence_stock_count':packet['company_evidence_stock_count'],
                             'stock_n':sum(len(v) for v in packet['stocks_by_theme'].values())})
            print('packet',date,'stocks',manifest[-1]['stock_n'],'news',len(packet['news']),flush=True)
    save(OUT/'input_manifest.json',{'created_at':datetime.now().isoformat(),'packets':manifest,'days':len(manifest),'raw_news_rows':raw_n,
                                  'protocol_sha256':h(protocol),'features_sha256':h(SOURCE/'features.json'),
                                  'references_sha256':h(OUT/'judge_references.json'),'builder_sha256':h(Path(__file__))})
    print('COMPLETE',len(manifest),raw_n,flush=True)


if __name__=='__main__':main()
