from copy import deepcopy
import json

import pytest

from build_gpt_replay import facts
from gpt_replay_gate import validate_decision
from freeze_gpt_replay import audit_session


def fixture():
    packet={'date':'20260105','stocks_by_theme':{'甲题材':[{'ts_code':'A'}]},'news':{'known':{}}}
    decision={'date':'20260105','emotion_prediction':'hot','mainline':'甲题材','top3':['甲题材'],'leader':'A','alternatives':[],
              'reasoning':'这是仅依据当前截面已知的结构和证据所作出的判断，需要在下一个观察点验证群体是否继续而不是只有个股上涨。',
              'state_now':'升温','phase':'启动','confidence':'中','evidence_ids':['known'],
              'evidence_limits':'公司收入未知','invalidation':'原成员同时走弱'}
    return packet,decision


def test_unknown_future_stock_citation_and_unread_theme_rejected():
    packet,decision=fixture()
    validate_decision(decision,packet,{'甲题材'})
    for key,bad in [('leader','FUTURE_WINNER'),('date','20260106'),('evidence_ids',['future_news'])]:
        changed=deepcopy(decision);changed[key]=bad
        with pytest.raises(ValueError):validate_decision(changed,packet,{'甲题材'})
    with pytest.raises(ValueError):validate_decision(decision,packet,set())


def test_derived_answers_removed_recursively():
    assert facts({'themes':[{'theme':'甲','score':99,'rule_score':99,'phase':'answer','breadth':3}],
                  'market':{'state':'answer','baseline_prediction':'hot','breadth':.8}})=={
        'themes':[{'theme':'甲','breadth':3}],'market':{'breadth':.8}}


def test_access_audit_rejects_next_day_before_freeze(tmp_path):
    (tmp_path/'session_config.json').write_text(json.dumps({'dates':['20260105','20260106']}))
    logs=[{'date':'20260105','action':'show','packet_sha256':'x'},
          {'date':'20260106','action':'show','packet_sha256':'y'}]
    (tmp_path/'access.jsonl').write_text('\n'.join(json.dumps(r) for r in logs))
    with pytest.raises(ValueError,match='chronological'):
        audit_session(tmp_path,{'20260105':{'sha256':'x'},'20260106':{'sha256':'y'}})
