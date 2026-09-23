import pandas as pd
import pytest
from audit_attribution import resolve_evidence
from build_attribution_inputs import asof_news


def test_precise_asof_window_does_not_allow_later_headlines():
    frame=pd.DataFrame([dict(datetime=t,title="消息") for t in [
        "2026-05-20 23:59:00","2026-05-21 06:00","2026-05-21 06:00:01","2026-05-21 08:00:00"]])
    selected=asof_news(frame,"2026-05-20 00:00:00","2026-05-21 06:00:00")
    assert len(selected)==2


def test_future_or_other_company_news_is_not_valid_evidence():
    registry={"x":[dict(datetime="2026-05-21 08:00:00",title="甲公司订单",content="")],
              "y":[dict(datetime="2026-05-20 10:00:00",title="乙公司订单",content="")],
              "z":[dict(datetime="2026-05-20 09:00:00",title="甲公司订单",content="")],}
    for identity in ["x","y","missing"]:
        with pytest.raises(ValueError):resolve_evidence(registry,identity,"2026-05-21 06:00:00","甲公司")
    assert resolve_evidence(registry,"z","2026-05-21 06:00:00","甲公司")["datetime"]=="2026-05-20 09:00:00"


def test_prior_evidence_is_usable_without_using_a_later_reprint():
    registry={"x":[dict(datetime="2026-05-22 08:00:00",title="甲公司公告",content=""),
                    dict(datetime="2026-05-14 16:00:00",title="甲公司公告",content="")]}
    assert resolve_evidence(registry,"x","2026-05-21 06:00:00","甲公司")["datetime"]=="2026-05-14 16:00:00"
