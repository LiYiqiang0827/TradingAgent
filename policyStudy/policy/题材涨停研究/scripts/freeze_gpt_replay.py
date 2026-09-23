"""Audit the chronological gate and freeze all 160 predictions before scoring."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from build_case_dossiers import BASE, SOURCE, save

OUT=BASE/'data/gpt_replay_v2'
SESSIONS=Path('/tmp/tradingagent_gpt_replay_v2_20260921')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_session(session,expected):
    config=json.loads((session/'session_config.json').read_text())
    allowed=config['dates'];position=0;shown=False;details=set();seen_evidence=set();submitted=[]
    logs=[json.loads(line) for line in (session/'access.jsonl').read_text().splitlines()]
    for event in logs:
        if event['action']=='references':continue
        if position>=len(allowed) or event['date']!=allowed[position]:
            raise ValueError('Non-chronological or post-submit read in '+str(session))
        if event['action']=='show':
            if event['packet_sha256']!=expected[event['date']]['sha256']:raise ValueError('Stale input')
            shown=True
        elif event['action']=='detail':
            if not shown:raise ValueError('Detail before show')
            details.update(event['themes'])
        elif event['action']=='news':seen_evidence.update(event['news_ids'])
        elif event['action']=='submit':
            if not shown:raise ValueError('Decision without current observation')
            path=session/'decisions'/(event['date']+'.json')
            if digest(path)!=event['prediction_sha256']:raise ValueError('Modified frozen decision')
            row=json.loads(path.read_text())
            if row['packet_sha256']!=expected[event['date']]['sha256']:raise ValueError('Wrong input hash')
            if not set(row['top3'])<=details:raise ValueError('Uninspected top3')
            row['full_news_read_n']=len(seen_evidence)
            row['citations_not_opened_full_n']=len(set(row.get('evidence_ids',[]))-seen_evidence)
            submitted.append(row)
            position+=1;shown=False;details=set();seen_evidence=set()
        else:raise ValueError('Unknown access type')
    if position!=len(allowed):raise ValueError(f'Incomplete session {session}: {position}/{len(allowed)}')
    return submitted,{'judge_id':config['judge_id'],'days':position,'access_sha256':digest(session/'access.jsonl'),
                      'first_date':allowed[0],'last_date':allowed[-1],
                      'news_full_read_requests_total':sum(r['full_news_read_n'] for r in submitted),
                      'rows_with_partial_only_citation':sum(r['citations_not_opened_full_n']>0 for r in submitted)}


def main():
    inputs=json.loads((OUT/'input_manifest.json').read_text());expected={p['date']:p for p in inputs['packets']}
    assert digest(BASE/'research/gpt_replay_protocol_v2.json')==inputs['protocol_sha256'],'Protocol changed during replay'
    assert digest(OUT/'judge_references.json')==inputs['references_sha256'],'References changed during replay'
    assert digest(SESSIONS/'references.json')==inputs['references_sha256'],'Judges received different references'
    assert digest(SESSIONS/'gate.py')==digest(Path(__file__).with_name('gpt_replay_gate.py')),'Executed gate differs from audited gate'
    for d,info in expected.items():
        if digest(OUT/'packets'/(d+'.json'))!=info['sha256']:raise ValueError('Input modified '+d)
    rows=[];sessions=[]
    for name in ['a','b','c']:
        r,s=audit_session(SESSIONS/name,expected);rows.extend(r);sessions.append(s)
    rows.sort(key=lambda r:r['date']);dates=[r['date'] for r in rows]
    assert len(rows)==len(set(dates))==160 and dates==sorted(expected)
    dest=OUT/'frozen_predictions.json'
    encoded=json.dumps(rows,ensure_ascii=False,indent=2,allow_nan=False)
    if dest.exists():
        assert dest.read_text()==encoded,'Never overwrite different frozen predictions'
    else:
        with dest.open('x') as f:f.write(encoded)
    protected=json.loads((SOURCE/'prediction_manifest.json').read_text())
    assert all(digest(SOURCE/file)==protected[key+'_sha256'] for key,file in [
        ('predictions','predictions.json'),('labels','labels.json'),('model','frozen_model.json')])
    manifest={'predictions_sha256':digest(dest),'prediction_count':160,'dates':dates,
              'packet_sha256_by_date':{d:r['sha256'] for d,r in expected.items()},
              'frozen_at':datetime.now(timezone.utc).isoformat(),'judge_sessions':sessions,
              'judge_model':'Inherited Codex session model; exact backend model id not exposed by collaboration API',
              'method':'Actual LLM per-day decisions; fresh agents, no forked conversation, no rule-generated decisions',
              'isolation':'Chronological gate and explicit permitted-tool protocol; shared filesystem is not an OS sandbox',
              'limitations':'January-August already used to develop methodology; no untouched out-of-sample claim',
              'protocol_sha256':digest(BASE/'research/gpt_replay_protocol_v2.json'),
              'input_manifest_sha256':digest(OUT/'input_manifest.json'),
              'judge_references_sha256':digest(OUT/'judge_references.json'),
              'gate_sha256':digest(Path(__file__).with_name('gpt_replay_gate.py')),
              'v1_files_unchanged':True}
    incidents=OUT/'judge_process_incidents.json'
    if incidents.exists():
        manifest['process_incidents_path']=str(incidents)
        manifest['process_incidents_sha256']=digest(incidents)
    save(OUT/'prediction_manifest.json',manifest)
    # Retain original access ledgers beside the result, not only in /tmp.
    for name in ['a','b','c']:
        (OUT/('judge_'+name+'_access.jsonl')).write_bytes((SESSIONS/name/'access.jsonl').read_bytes())
    print(json.dumps({'frozen':len(rows),'manifest':str(OUT/'prediction_manifest.json'),'sessions':sessions},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
