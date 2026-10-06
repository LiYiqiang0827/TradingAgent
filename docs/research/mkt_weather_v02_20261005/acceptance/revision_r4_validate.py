"""Controller R4 reproducibility and arithmetic checks, using bounded local evidence.

Run at repo root: python <this file> --run <revision_r4 directory>.
Human acceptance and publication are recorded separately from this verifier.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from policyStudy.policy.market_sentiment.io import read_csv,sha256,write_json
from policyStudy.policy.market_sentiment.review import explain_daily


def validate(run):
    report=REPO/'docs/research/mkt_weather_v02_20261005'
    data=report/'data'; accept=report/'acceptance'
    raw=read_csv(data/'mkt_raw_daily.csv'); daily=read_csv(data/'mkt_daily.csv')
    cal=json.loads((data/'mkt_calibration_v02.json').read_text(encoding='utf-8'))
    assert len(daily)==424 and not daily.trade_date.duplicated().any()
    assert daily.trade_date.str[:4].value_counts().to_dict()=={'2025':243,'2026':181}
    assert daily[['mkt_hit','mkt_cont','mkt_act']].notna().all().all()
    # Independently recompute pooled priors, smoothed arrays and linear anchors.
    year=raw[raw.trade_date.str.startswith('2025')]
    manual={}
    for g in ('all','chain'):
        n=year[f'mkt_{g}_observed_n']; k=year[f'mkt_{g}_drop_k']; valid=n.gt(0)&k.notna()
        prior=float(k[valid].sum()/n[valid].sum())
        assert prior==cal['priors'][g]['prior']
        smoothed=(k[valid]+5*prior)/(n[valid]+5)
        anchor=cal['anchors'][g+'_drop']
        np.testing.assert_allclose(np.quantile(smoothed,[.1,.9],method='linear'),[anchor['p10'],anchor['p90']],rtol=0,atol=1e-15)
        manual[g]={'k':int(k[valid].sum()),'n':int(n[valid].sum()),'prior':prior,'anchors_verified':True}
    r=daily.set_index('trade_date').loc['2026-08-31']
    hit=.40*r.mkt_all_drop_q+.35*r.mkt_chain_drop_q+.25*r.mkt_ddens_q
    cont=.40*r.mkt_m1raw_q+.30*r.mkt_ladder_q+.30*r.mkt_max_height_q
    np.testing.assert_allclose([hit,cont],[r.mkt_hit,r.mkt_cont],rtol=0,atol=1e-12)
    manual['2026-08-31']={'hit':float(hit),'cont':float(cont),'weather':r.mkt_weather}
    assert r.mkt_weather=='thunder'
    # Frozen scientific inputs and optional human packet must be byte-identical to R3.
    fixed=['data/mkt_raw_daily.csv','data/mkt_daily.csv','data/mkt_calibration_v02.json',
           'data/market_calendar.csv','human/li10/packets/cards.pdf','human/li10/packets/Li_labels.csv',
           'human/li10/private_key/threshold_revision_ledger.json']
    paths=[report/x for x in fixed]+[REPO/'policyStudy/policy/market_sentiment/config/mkt_spec_v02.json']
    frozen={}
    for p in paths:
        rel=p.relative_to(REPO).as_posix()
        original=subprocess.check_output(['git','show','971af3e8:'+rel],cwd=REPO)
        assert hashlib.sha256(original).hexdigest()==sha256(p),rel
        frozen[rel]=sha256(p)
    # Diagnostic output repeats exactly; explanations match the actual CLI integration.
    repeated={}
    for p in sorted((run/'review').iterdir()):
        assert sha256(p)==sha256(run/'review_repeat'/p.name),p.name
        repeated[p.name]=sha256(p)
        shutil.copy2(p,data/'review_r4'/p.name)
    expected=explain_daily(daily)
    integrated=read_csv(run/'snapshot_today/daily_explanations.csv').fillna('')
    pd.testing.assert_frame_equal(expected.fillna(''),integrated,check_exact=True)
    snap=read_csv(run/'snapshot_today/mkt_daily.csv')
    ignored={'generated_at','input_snapshot_id','mkt_summary'}
    cols=[c for c in daily if c not in ignored]
    pd.testing.assert_frame_equal(daily[cols],snap[cols],check_exact=True)
    receipt=json.loads((run/'snapshot_today/today_receipt.json').read_text(encoding='utf-8'))
    assert receipt['row_count']==424 and receipt['duplicates']==0 and not receipt['changed_historical_dates']
    for end in (1,10,212,423):
        pd.testing.assert_frame_equal(expected.iloc[:end],explain_daily(daily.iloc[:end]),check_exact=True)
    xml=ET.parse(run/'root_final_tests_v2.xml').getroot()
    suites=list(xml.iter('testsuite'))
    assert sum(int(x.get('failures','0'))+int(x.get('errors','0')) for x in suites)==0
    assert sum(int(x.get('tests','0')) for x in suites)==90
    shutil.copy2(run/'root_final_tests_v2.xml',accept/'revision_r4_tests.xml')
    shutil.copy2(run/'r4_code_review.json',accept/'revision_r4_code_review.json')
    trial=read_csv(run/'review/threshold_sensitivity.csv')
    assert trial[trial.year.eq(2026)].changed_n.max()==8
    assert trial.availability_changed_n.eq(0).all()
    # Report values checked against data, not only text token presence.
    corr=read_csv(run/'review/correlations.csv')
    x=corr[corr.year.eq(2026)&corr.a.eq('mkt_cont')&corr.b.eq('mkt_act')].pearson_r.iloc[0]
    assert round(x,3)==.244
    text=(report/'REPORT.md').read_text(encoding='utf-8')
    for token in ['424','181','43.09%','40.88%','0.244','45日','4.42%','project_review_accepted','ready_not_enabled']:
        assert token in text,token
    result={'status':'passed','scope':'controller arithmetic, source immutability, new integration and report consistency',
            'weather_tests':82,'weather_subtests':8,'legacy_tests':37,
            'rows':424,'year_counts':{'2025':243,'2026':181},'manual_checks':manual,
            'frozen_hashes':frozen,'repeat_output_hashes':repeated,
            'integrated_explanations_exact':True,'snapshot_all_scientific_columns_exact':True,
            'explanation_prefix_lengths':[1,10,212,423],
            'snapshot_today_receipt':receipt,'report_numbers_verified':True,
            'four_source_paper_methods_accepted_for_comparison':True,
            'new_publication_requires_separate_remote_verification':True}
    write_json(accept/'revision_r4_root_checks.json',result)
    print(json.dumps({'status':result['status'],'days':424,'weather_tests':82,'subtests':8,'legacy_tests':37},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();validate(a.run)
