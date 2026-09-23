import numpy as np
import pandas as pd

from build_divergence_cases import first_sustained
from build_case_dossiers import news_candidates
from study_divergence import choose_cores, finite_ratio, snapshot


def test_height_roles_are_separate_and_first_boards_are_not_cores():
    leaders=[dict(ts_code=c,name=c,height=h) for c,h in [("A",4),("B",4),("C",2),("D",1)]]
    prices=pd.DataFrame({"pre_close":[10]*4},index=list("ABCD"))
    limits=pd.DataFrame({"up_limit":[11,11,12,12]},index=list("ABCD"))
    result=choose_cores(leaders,prices,limits)
    assert {(r["ts_code"],r["regime"]) for r in result}=={("A","10%"),("B","10%"),("C","20%")}
    assert choose_cores([dict(r,height=1) for r in leaders],prices,limits)==[]


def test_missing_old_member_and_future_winner_cannot_confirm_the_group():
    minute=pd.DataFrame([
        dict(ts_code="A",datetime="2026-01-06 10:30:00",price=10.2),
        dict(ts_code="C",datetime="2026-01-06 10:30:00",price=12),
        dict(ts_code="B",datetime="2026-01-06 10:31:00",price=11),
        dict(ts_code="B",datetime="2026-01-07 10:30:00",price=11),
    ])
    day=pd.DataFrame({"pre_close":[10,10],"close":[10.5,9]},index=["A","B"])
    result=snapshot(["A","B"],["A"],"20260106",minute,day,pd.Series([.95,.9],index=["A","B"]),"10:30:00")
    assert result["expected"]==2 and result["covered"]==1 and result["missing"]==["B"]
    assert result["core_only"] and not result["core_group"]
    assert result["median_pct"]==pytest_approx(2)


def pytest_approx(value):
    # Import lazily to keep the study scripts independent of pytest.
    import pytest
    return pytest.approx(value)


def test_future_close_changes_outcome_but_not_snapshot_signals():
    minute=pd.DataFrame([dict(ts_code=c,datetime="2026-01-06 10:30:00",price=11) for c in "AB"])
    day=pd.DataFrame({"pre_close":[10,10],"close":[12,12]},index=list("AB"))
    ratio=pd.Series([1,1],index=list("AB"))
    a=snapshot(list("AB"),["A"],"20260106",minute,day,ratio,"10:30:00")
    day["close"]=[8,8]
    b=snapshot(list("AB"),["A"],"20260106",minute,day,ratio,"10:30:00")
    assert a["remaining_median_pct"]>0>b["remaining_median_pct"]
    assert {k:v for k,v in a.items() if not k.startswith("remaining_")}=={k:v for k,v in b.items() if not k.startswith("remaining_")}


def test_sustained_confirmation_uses_third_minute_and_resets_at_gaps():
    index=pd.to_datetime(["2026-01-06 "+t for t in ["11:29","11:30","13:01","13:02","13:03"]])
    assert first_sustained(pd.Series(True,index=index))=="13:03:00"
    assert first_sustained(pd.Series([True,True,np.nan,True,True],index=index)) is None


def test_invalid_ratios_stay_missing_and_roundtrip_is_flat():
    a=finite_ratio(pd.Series([10,0,10,10]),pd.Series([10,10,0,np.nan]))
    assert a.iloc[0]==1 and a.iloc[1:].isna().all()
    minute=pd.DataFrame([dict(ts_code="A",datetime="2026-01-06 10:30:00",price=10)])
    day=pd.DataFrame({"pre_close":[11],"close":[10]},index=["A"])
    r=snapshot(["A"],["A"],"20260106",minute,day,pd.Series([1.1],index=["A"]),"10:30:00")
    assert r["since_p_median_pct"]==0


def test_named_catalyst_can_be_retrieved_without_industry_keyword():
    rows=pd.DataFrame([dict(title="长鑫科技更新招股书",content="业绩增长",src="sina",md5="x",datetime="2026-05-17 19:38:46")])
    assert news_candidates(rows,"半导体",[])==[]
    result=news_candidates(rows,"半导体",[],extra_keywords=["长鑫"])
    assert len(result)==1 and result[0]["md5"]=="x"
