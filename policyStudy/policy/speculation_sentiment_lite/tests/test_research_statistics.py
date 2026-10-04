"""Small boundary tests for report statistics; no dependence on observed findings."""
from pathlib import Path
import sys
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[4]/'docs/research/mrwu_sentiment_cycle_20261005/reproduction'))
from analyze import transitions, runs

def test_transition_denominator_does_not_bridge_unknown():
    f=pd.DataFrame({'grade5':['B','B',None,'B','A']})
    t=transitions(f,'grade5',['A','B'])
    b=t[t['from']=='B']
    assert b.denominator.eq(2).all()
    assert b['count'].sum()==2
    assert b.probability.eq(.5).all()

def test_runs_censor_edges_and_missing_separately():
    f=pd.DataFrame({'trade_date':['a','b','c','d','e','f'],'grade5':['B','B',None,'A','A','B']})
    r=runs(f,'grade5',['A','B'])
    assert r.duration.tolist()==[2,2,1]
    assert r.left_censored.tolist()==[True,False,False]
    assert r.right_censored.tolist()==[True,False,True]
    assert r.completed.tolist()==[False,True,False]
