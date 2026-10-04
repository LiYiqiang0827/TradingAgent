import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import numpy as np
import pandas as pd
import pytest
from cycle import compute_cycle
from score import combine, smoothed, score_raw, ANCHORS

def sequence(scores):
    f=pd.DataFrame({'trade_date':pd.bdate_range('2026-01-01',periods=len(scores)).strftime('%Y-%m-%d'),'score5':scores,'panic':False})
    for c in ['M1','M2','M3','M4','M5']:f[c]=scores
    return f

def test_cycle_priority_and_prefix():
    f=sequence([40,42,39,30,33,52,70,82,71,40,25,32,53])
    c=compute_cycle(f)
    assert c.phase.iloc[5]=='启动候选'
    assert c.phase.iloc[9]=='退潮'
    assert c.phase.iloc[10]=='冰点'
    assert c.phase_tag.iloc[11]=='冰点内改善'
    pd.testing.assert_frame_equal(c.iloc[:10],compute_cycle(f.iloc[:10]))

def test_cycle_missing_history_does_not_bridge():
    f=sequence([40,42,np.nan,30,33,52,70,82]); f.loc[2,'M1']=np.nan
    c=compute_cycle(f)
    assert c.phase.iloc[2]=='证据不足'
    assert c.phase.iloc[7]=='证据不足'

def test_internal_empty_group_is_not_zero():
    assert np.allclose(combine([(np.array([80,np.nan]),.4),(np.array([np.nan,20]),.6)]),[80,20])
    assert np.isnan(combine([(np.array([np.nan]),.4)])[0])

def raw_fixture():
    f=pd.DataFrame({'date':['20260105','20260106'],'P_open':[.1,.1],'P_close':[.1,.1],'C_open':[.1,.1],'C_close':[.1,.1],'H_score':[75,75],'BL':[3,3],'Ddens':[1,1],'lr20':[.5,.5],'ratio20':[1.2,1.2],'median_ret':[-.1,.1],'H_h2l':[0,0]})
    for g in ['h1','h2','h3','h4']:f['k_'+g]=1;f['n_'+g]=2
    for g in ['b1','b2','b3']:f['kz_'+g]=1;f['nz_'+g]=2
    f['k_mid']=1;f['n_mid']=2
    return f

def calibration():
    return {'priors':{g:.4 for g in ['h1','h2','h3','h4','b1','b2','b3','mid']},'anchors':{c:[0,1] for c in ANCHORS},'panic_ddens_p90':1}

def test_fixed_weights_missing_and_panic():
    f=raw_fixture();c=calibration(); c['anchors']['lr20']=[0,.5]
    s=score_raw(f,c)
    assert s.panic.tolist()==[True,False]
    assert s.M5.tolist()==[50,100]
    assert np.allclose(s.score5,(25*s.M1+20*s.M2+20*s.M3+15*s.M4+10*s.M5)/90)
    f.loc[0,'n_mid']=0;s=score_raw(f,c)
    assert pd.isna(s.score5.iloc[0]) and pd.notna(s.score_available.iloc[0])
    assert s.available_weight_sum.iloc[0]==75/90

def test_clipping_strict_endpoints_and_constant():
    f=raw_fixture();c=calibration();c['anchors']['BL']=[3,3];s=score_raw(f,c)
    assert s.Q_BL.tolist()==[50,50]
    assert not s.clip_low_BL.any() and not s.clip_high_BL.any()
    assert s.endpoint_low_BL.all() and s.constant_anchor_BL.all()

def test_shrinkage_changes_only_a():
    f=raw_fixture();p=calibration()['priors']
    for a in [2,5,10]:assert abs(smoothed(f,p,a).M1raw.iloc[0]-(1+a*.4)/(2+a))<1e-12

@pytest.mark.parametrize('scores,expected',[
    ([40,42,45,44,47,55],'修复'),
    ([70,68,69,75,78,70],'分歧'),
    ([50,51,52,55,60,68],'扩散'),
    ([70,70,70,70,70,70],'强势'),
    ([52,55,54,55,55,55],'中性/混沌'),
])
def test_cycle_remaining_branches(scores,expected):
    assert compute_cycle(sequence(scores)).phase.iloc[-1]==expected

def test_panic_decline_has_priority_over_divergence():
    f=sequence([80,80,80,80,80,70]);f.loc[5,'panic']=True
    assert compute_cycle(f).phase.iloc[-1]=='退潮'

def test_panic_strict_volume_and_nonzero_down_density():
    f=raw_fixture();cal=calibration();f.ratio20=1
    assert not score_raw(f,cal).panic.any()
    f.ratio20=1.1;f.Ddens=0;cal['panic_ddens_p90']=0
    assert not score_raw(f,cal).panic.any()
    f.loc[0,'H_h2l']=1
    assert score_raw(f,cal).panic.tolist()==[True,False]
