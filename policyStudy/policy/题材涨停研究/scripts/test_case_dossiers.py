import pandas as pd
from build_case_dossiers import cohort_observation, news_candidates
from audit_start_cohorts import compound_price_returns


def test_fixed_cohort_excludes_new_winners_and_future_timestamps():
    day = pd.DataFrame([
        dict(ts_code="A", trade_date="20260106", pre_close=10, close=10.5, pct_chg=5),
        dict(ts_code="C", trade_date="20260106", pre_close=10, close=12, pct_chg=20),
    ])
    limits = pd.DataFrame([dict(ts_code="A", trade_date="20260106", up_limit=11)])
    minute = pd.DataFrame([
        dict(ts_code="A", datetime="2026-01-06 10:30:00", price=10.2),
        dict(ts_code="C", datetime="2026-01-06 10:30:00", price=12),
        dict(ts_code="A", datetime="2026-01-07 10:30:00", price=11),
    ])
    result = cohort_observation(["A", "B"], "20260106", day, minute, limits)
    snap = next(s for s in result["snapshots"] if s["time"]=="10:30:00")
    assert result["daily_covered"]==1 and result["daily_missing"]==["B"]
    assert snap["covered"]==1 and snap["expected"]==2 and snap["positive"]==1
    assert abs(snap["median_pct"]-2)<1e-8


def test_bad_minute_price_remains_missing_in_denominator():
    day = pd.DataFrame([dict(ts_code="A",trade_date="20260106",pre_close=10,close=11,pct_chg=10)])
    limits = pd.DataFrame([dict(ts_code="A",trade_date="20260106",up_limit=11)])
    minute = pd.DataFrame([dict(ts_code="A",datetime="2026-01-06 10:30:00",price=float("nan"))])
    result = cohort_observation(["A"], "20260106", day, minute, limits)
    snap = next(s for s in result["snapshots"] if s["time"]=="10:30:00")
    assert snap["covered"]==0 and snap["expected"]==1 and snap["missing"]==["A"]


def test_exact_news_reprints_keep_provenance_without_double_counting():
    news = pd.DataFrame([
        dict(title="机器人量产",content="某公司宣布量产。",src="a",md5="id1",datetime="2026-01-06 08:00:00"),
        dict(title="机器人量产",content="某公司宣布量产。",src="b",md5="id2",datetime="2026-01-06 09:00:00"),
        dict(title="天气",content="晴。",src="a",md5="id3",datetime="2026-01-06 08:00:00"),
    ])
    result = news_candidates(news,"机器人",[])
    assert len(result)==1 and result[0]["datetime"]=="2026-01-06 08:00:00"
    assert {r["md5"] for r in result[0]["source_records"]}=={"id1","id2"}


def test_roundtrip_is_flat_and_missing_prices_are_not_zero_returns():
    first = pd.DataFrame(dict(close=[11,11],pre_close=[10,10]),index=["A","B"])
    second = pd.DataFrame(dict(close=[10,float("nan")],pre_close=[11,11]),index=["A","B"])
    returns = compound_price_returns(first,second)
    assert returns["A"]==0 and pd.isna(returns["B"])
