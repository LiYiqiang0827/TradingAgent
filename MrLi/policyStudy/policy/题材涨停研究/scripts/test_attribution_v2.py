import pytest

from audit_attribution_v2 import validate_group
from render_attribution_v2_report import feedback_for_pool


def test_group_cannot_add_a_future_winner_or_use_later_evidence():
    original=[dict(feature_date='20260520',ts_code='A',name='甲公司',primary_theme='半导体',decision_at='2026-05-21 06:00:00')]
    group=dict(date='20260520',theme='半导体',members=['A'],shared_news_ids=['x'])
    registry={'x':[dict(datetime='2026-05-21 06:00:01',title='甲公司',content='',src='test') ]}
    with pytest.raises(ValueError):validate_group(group,original,registry)
    registry['x'][0]['datetime']='2026-05-20 18:00:00'
    assert validate_group(group,original,registry)['members']==['A']
    with pytest.raises(ValueError):validate_group(dict(group,members=['A','B']),original,registry)


def test_shared_headline_has_to_include_every_frozen_member():
    original=[dict(feature_date='20260520',ts_code=c,name=n,primary_theme='半导体',decision_at='2026-05-21 06:00:00') for c,n in [('A','甲公司'),('B','乙公司')]]
    registry={'x':[dict(datetime='2026-05-20 14:00:00',title='甲公司涨停',content='',src='test') ]}
    with pytest.raises(ValueError):validate_group(dict(date='20260520',theme='半导体',members=['A','B'],shared_news_ids=['x']),original,registry)


def test_missing_member_stays_in_denominator_and_blocks_complete_median():
    outcome=[dict(ts_code='A',d_pct=-1,t_pct=3,t_since_p_pct=1.97,u_since_p_pct=0)]
    result=feedback_for_pool(['A','B'],outcome)
    assert result['t_pct']==dict(expected=2,covered=1,median_pct=None,positive_n=1,complete=False)
