"""One-date evidence gate with append-only LLM decisions. No outcome reads."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())
def now():return datetime.now(timezone.utc).isoformat()


def dump(value):print(json.dumps(value,ensure_ascii=False,separators=(',',':')))


def session_paths(session):
    config=read(session/'session_config.json')
    completed={p.stem for p in (session/'decisions').glob('*.json')}
    remaining=[date for date in config['dates'] if date not in completed]
    return config,remaining[0] if remaining else None


def log(session,action,date,**extra):
    with (session/'access.jsonl').open('a') as f:
        f.write(json.dumps(dict(at=now(),action=action,date=date,**extra),ensure_ascii=False)+'\n')


def packet_for(session):
    config,date=session_paths(session)
    if date is None:return config,None,None
    path=Path(config['packet_root'])/(date+'.json')
    return config,date,read(path)


def aliases(packet):
    return {'N'+str(i+1):identity for i,identity in enumerate(sorted(packet['news'],key=lambda x:(packet['news'][x]['datetime'],x)))}


def summary(packet):
    alias={identity:label for label,identity in aliases(packet).items()}
    rows=[]
    for t in packet['themes']:
        names=packet['stocks_by_theme'][t['theme']]
        salient=sorted(names,key=lambda s:(-s['height'],-s['amount_yuan'],s['ts_code']))[:3]
        rows.append([t['theme'],t['breadth'],t['heights_by_regime'],t['multi_share'],t['seal_rate'],
                     round(t['order_float']*100,3),t['early_share'],t['breadth_history'],
                     [[s['name'],s['ts_code'],s['height'],round(s['amount_yuan']/1e8,2)] for s in salient],
                     [[alias[i],packet['news'][i]['datetime'],packet['news'][i]['title'] or packet['news'][i]['content'][:85]] for i in t['news_ids']]])
    return {'date':packet['date'],'decision_at':packet['decision_at'],'market':packet['market'],
            'past_market':packet['past_market'],'rotation':packet['rotation'],
            'columns':['theme','up_n','height_by_regime','multi_share','seal_rate_assigned_subset','order_float_pct','early_share','prior20_breadth','height_capacity_preview_name_code_height_amount_yi','current_news_id_time_headline'],
            'themes':rows,'unknown_break_n':len(packet['unknown_breaks']),
            'news_scope':packet['news_scope'],'news_window_rows':packet['news_window_rows'],
            'company_evidence_stock_count':packet['company_evidence_stock_count'],
            'instruction':'Read detail for the candidate themes before deciding. Preview stocks are not the complete pool; detail includes every member. No outcomes are accessible through this gate.'}


def detail(packet,names):
    alias={identity:label for label,identity in aliases(packet).items()}
    result={}
    for name in names:
        if name not in packet['stocks_by_theme']:raise ValueError('Unknown current theme '+name)
        stocks=packet['stocks_by_theme'][name]
        theme=next(t for t in packet['themes'] if t['theme']==name)
        identities=set(theme['news_ids'])
        for s in stocks:identities.update(s['company_news_ids'])
        # Compact retrieval preview. Full text must be requested for decisive facts.
        news=[[alias[i],packet['news'][i]['datetime'],packet['news'][i]['src'],packet['news'][i]['title'],
               packet['news'][i]['content'][:160],len(packet['news'][i]['content'])>160] for i in sorted(identities)]
        stock_rows=[[s['ts_code'],s['name'],s['height'],s['regime'],s['first_time'],s['last_time'],
                     round(s['amount_yuan']/1e8,3),s['turnover'],round(s['order_float']*100,3),s['seal_decay'],
                     s['momentum5'],s['ma20_distance'],s['log_free_mv'],s['one_price'],s['raw_multi_labels'],s['lp_reason'],
                     [alias[i] for i in s['company_news_ids']]] for s in stocks]
        result[name]={'stock_columns':['code','name','height','regime','first_time','last_time','amount_yi','free_turnover_pct','order_float_pct','seal_decay','momentum5_pct','ma20_distance_pct','log10_free_mv_yuan','one_price','raw_multi_labels','lp_reason','company_news'],
                      'stocks':stock_rows,'news_columns':['id','datetime','src','title','excerpt','truncated'],
                      'news':news,'theme_news':[alias[i] for i in theme['news_ids']],
                      'prior_leaders':[{'date':p['date'],'leaders':[l for l in p['leaders'] if l['theme']==name]} for p in packet['prior_leaders']]}
    return {'date':packet['date'],'detail':result}


def validate_decision(value,packet,seen_details):
    if value.get('date')!=packet['date']:raise ValueError('Wrong current date')
    if value.get('emotion_prediction') not in ['cold','neutral','hot',None]:raise ValueError('Invalid emotion')
    main=value.get('mainline');top=value.get('top3',[]);leader=value.get('leader')
    if len(top)>3 or len(set(top))!=len(top):raise ValueError('top3 must have <=3 distinct exact theme names')
    if any(t not in packet['stocks_by_theme'] for t in top):raise ValueError('Non-current theme; never add future names')
    if main is not None:
        if main not in top or top[0]!=main:raise ValueError('Top1 must be first in top3')
        pool={s['ts_code'] for s in packet['stocks_by_theme'][main]}
        if leader is not None and leader not in pool:raise ValueError('Leader outside fixed pool')
        if any(c not in pool for c in value.get('alternatives',[])):raise ValueError('Alternative outside fixed pool')
    elif leader is not None:raise ValueError('Null theme cannot have leader')
    if not set(top)<=set(seen_details):raise ValueError('Read detail for every top3 theme before submitting')
    if any(i not in packet['news'] for i in value.get('evidence_ids',[])):raise ValueError('News citation not in packet')
    if len(value.get('reasoning',''))<35:raise ValueError('Need actual contemporaneous LLM reasoning, not a mechanical rank')
    for key in ['state_now','phase','confidence','evidence_limits','invalidation']:
        if not value.get(key):raise ValueError('Missing '+key)


def baselines(packet):
    by_theme={}
    for name,pool in packet['stocks_by_theme'].items():
        by_theme[name]={'leader_height':sorted(pool,key=lambda s:(-s['height'],s['ts_code']))[0]['ts_code'],
                        'leader_early':sorted(pool,key=lambda s:(s['first_time'] or '99:99:99',s['ts_code']))[0]['ts_code'],
                        'leader_capacity':sorted(pool,key=lambda s:(-s['amount_yuan'],s['ts_code']))[0]['ts_code']}
    main=sorted(packet['themes'],key=lambda t:(-t['breadth'],t['theme']))[0]['theme']
    return dict(mainline_breadth=main,leaders_by_theme=by_theme,
                **{k:{'theme':main,'leader':v} for k,v in by_theme[main].items()})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('command',choices=['references','show','detail','news','submit','status'])
    parser.add_argument('items',nargs='*')
    args=parser.parse_args();session=args.session
    config,date,packet=packet_for(session)
    if args.command=='references':
        dump(read(Path(config['references'])));log(session,'references',date);return
    if args.command=='status':dump({'next_date':date,'completed':len(list((session/'decisions').glob('*.json'))),'total':len(config['dates'])});return
    if packet is None:dump({'complete':True});return
    packet_path=Path(config['packet_root'])/(date+'.json')
    if args.command=='show':dump(summary(packet));log(session,'show',date,packet_sha256=digest(packet_path))
    elif args.command=='detail':dump(detail(packet,args.items));log(session,'detail',date,themes=args.items)
    elif args.command=='news':
        identities=[aliases(packet).get(i,i) for i in args.items]
        dump([packet['news'][i] for i in identities]);log(session,'news',date,news_ids=identities)
    elif args.command=='submit':
        value=json.load(sys.stdin)
        value['evidence_ids']=[aliases(packet).get(i,i) for i in value.get('evidence_ids',[])]
        events=[json.loads(line) for line in (session/'access.jsonl').read_text().splitlines()]
        seen={t for e in events if e['action']=='detail' and e['date']==date for t in e['themes']}
        validate_decision(value,packet,seen)
        value.update(packet_sha256=digest(packet_path),frozen_at=now(),judge_id=config['judge_id'],
                     baselines=baselines(packet),original_candidate_themes=list(packet['stocks_by_theme']),
                     original_leader_pools={t:[s['ts_code'] for s in pool] for t,pool in packet['stocks_by_theme'].items()},
                     news_gap=packet['news_window_rows']==0,company_evidence_stock_count=packet['company_evidence_stock_count'],
                     original_stock_n=sum(len(v) for v in packet['stocks_by_theme'].values()))
        dest=session/'decisions'/(date+'.json')
        with dest.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
        log(session,'submit',date,prediction_sha256=digest(dest),packet_sha256=value['packet_sha256'])
        dump({'frozen':date,'completed':len(list((session/'decisions').glob('*.json'))),'next_date':session_paths(session)[1]})


if __name__=='__main__':main()
