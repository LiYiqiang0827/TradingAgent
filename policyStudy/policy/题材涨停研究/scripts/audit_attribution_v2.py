"""Resolve supplemental evidence and save groups without reading future prices."""
import hashlib
import json
from collections import Counter

import pandas as pd

from audit_attribution import resolve_evidence
from build_case_dossiers import BASE, SOURCE, save
from supplement_attribution import OLD, OUT


def validate_group(group, original, registry):
    by_code={r['ts_code']:r for r in original if r['feature_date']==group['date']}
    codes=group['members']
    if not codes or len(codes)!=len(set(codes)) or not set(codes)<=set(by_code):
        raise ValueError('Group contains duplicate, missing, or future-added members')
    evidence=[]
    for code in codes:
        row=by_code[code]
        if row['primary_theme']!=group['theme']:
            raise ValueError('Group crosses original pool')
        identities=group['shared_news_ids']+group.get('member_news_ids',{}).get(code,[])
        if not identities:
            raise ValueError('Every member needs a scoped evidence reference')
        for identity in identities:
            r=resolve_evidence(registry,identity,row['decision_at'],row['name'])
            evidence.append({'code':code,'id':identity,'datetime':r['datetime'],'src':r['src']})
    return dict(group,decision_at=by_code[codes[0]]['decision_at'],evidence=evidence)


def protected_hashes():
    expected=json.loads((SOURCE/'prediction_manifest.json').read_text())
    result={key:hashlib.sha256((SOURCE/file).read_bytes()).hexdigest()==expected[key+'_sha256']
            for key,file in [('predictions','predictions.json'),('labels','labels.json'),('model','frozen_model.json')]}
    assert all(result.values())
    return result


def main():
    original=json.loads((OLD/'all_52_attributions.json').read_text())
    reviews_path=BASE/'research/attribution_supplement_reviews_v2.json'
    source=json.loads(reviews_path.read_text())
    reviews={(r['date'],r['code']):r for r in source['reviews']}
    needed={(r['feature_date'],r['ts_code']) for r in original if r['review_state']!='新闻逐条核验'}
    assert set(reviews)==needed and len(reviews)==len(source['reviews'])==32
    registry={}
    for p in sorted(OLD.glob('202*_*/input_packet.json')):
        for stock in json.loads(p.read_text())['stocks']:
            for r in stock['company_news']:registry.setdefault(r['md5'],[]).append(r)
    for p in sorted(OUT.glob('202*_*/[0-9]*_news.json')):
        for r in json.loads(p.read_text()):registry.setdefault(r['md5'],[]).append(r)
    rows=[];selected={};earlier=0
    for raw in original:
        row=dict(raw)
        review=reviews.get((row['feature_date'],row['ts_code']))
        if review:
            row.update(supplement_review=review,review_state='已完成补查；结论见关联状态',
                       branch=review['branch'],exposure=review['status'],judgment=review['finding'],
                       invalid_inference=review['limit'],evidence=[])
            older=False
            for identity in review['news_ids']:
                r=resolve_evidence(registry,identity,row['decision_at'],row['name'])
                assert not any(word in r.get('content','') for word in ['AI基于','由AI','AI生成','AI线索'])
                older |= pd.Timestamp(r['datetime']) < pd.Timestamp(row['feature_date'])-pd.Timedelta(days=14)
                row['evidence'].append({'id':identity,'datetime':r['datetime'],'src':r['src']})
                selected[identity]=r
            earlier+=older
        else:
            for e in row['evidence']:
                selected[e['id']]=resolve_evidence(registry,e['id'],row['decision_at'],row['name'])
        rows.append(row)
    assert {(r['feature_date'],r['ts_code']) for r in rows}=={(r['feature_date'],r['ts_code']) for r in original}
    group_path=BASE/'research/attribution_groups_v2.json'
    group_source=json.loads(group_path.read_text())
    assert len({g['id'] for g in group_source['groups']})==len(group_source['groups'])
    groups=[validate_group(g,original,registry) for g in group_source['groups']]
    for g in groups:
        for e in g['evidence']:
            name=next(r['name'] for r in rows if r['feature_date']==g['date'] and r['ts_code']==e['code'])
            selected[e['id']]=resolve_evidence(registry,e['id'],g['decision_at'],name)
    save(OUT/'all_52_attributions.json',rows)
    save(OUT/'reviewed_news.json',list(selected.values()))
    save(OUT/'fixed_groups.json',groups)
    frame=pd.DataFrame(rows);frame['evidence_ids']=frame.evidence.map(lambda es:';'.join(e['id'] for e in es))
    frame.drop(columns=['evidence','supplement_review']).to_csv(OUT/'all_52_attributions.csv',index=False,encoding='utf-8-sig')
    manifest=json.loads((OUT/'retrieval_manifest.json').read_text())
    summary={'original_rows':len(rows),'prior_reviewed':20,'supplement_reviewed':32,
             'supplement_status_counts':dict(Counter(r['status'] for r in reviews.values())),
             'supplement_with_evidence':sum(bool(r['news_ids']) for r in reviews.values()),
             'supplement_using_older_evidence':earlier,'unique_selected_news_ids_including_v1_and_groups':len(selected),
             'news_records_retrieved':manifest['raw_rows'],'groups':len(groups),
             'case_group_coverage':[], 'mode':source['mode'],'v1_unchanged':protected_hashes(),
             'ledger_sha256':hashlib.sha256((OUT/'all_52_attributions.json').read_bytes()).hexdigest(),
             'fixed_groups_sha256':hashlib.sha256((OUT/'fixed_groups.json').read_bytes()).hexdigest(),
             'reviews_sha256':hashlib.sha256(reviews_path.read_bytes()).hexdigest(),
             'group_spec_sha256':hashlib.sha256(group_path.read_bytes()).hexdigest()}
    for date in sorted({r['feature_date'] for r in rows}):
        case=[r for r in rows if r['feature_date']==date];gs=[g for g in groups if g['date']==date]
        union={c for g in gs for c in g['members']}
        summary['case_group_coverage'].append({'date':date,'original_n':len(case),'example_group_union_n':len(union),
                                               'group_count':len(gs),'ungrouped_n':len(case)-len(union)})
    save(OUT/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
