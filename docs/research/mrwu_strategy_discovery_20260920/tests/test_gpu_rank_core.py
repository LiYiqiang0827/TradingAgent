"""Root acceptance tests of Kimi numerical implementation; no network/trades."""
import unittest
import numpy as np
import pandas as pd
import research_gpu_rank_core as g

def fixture():
    f = pd.DataFrame([dict(ts_code=s,trade_date=f'202501{d+1:02d}',day_index=d,
        open=10.,high=11.,low=9.,close=10.,vol=100.,up_limit=11.,down_limit=9.,
        adj_factor=1.,st_status='NOT_LISTED_ON_ACCEPTED_DAY')
        for s in ['000001.SZ','600000.SH'] for d in range(10)])
    return f

class Labels(unittest.TestCase):
    def test_exact_day_identity_and_net_math(self):
        f=fixture(); y=g.make_labels(f)
        self.assertEqual(len(f),len(y))
        self.assertEqual(y.iloc[10].entry_index,1)
        self.assertEqual(y.iloc[10].exit_index,6)
        paid=100031.; proceeds=100000*(.997/1.003)
        expected=(proceeds-proceeds*.00081-paid)/paid
        self.assertAlmostEqual(y.iloc[10].net_return,expected,places=12)
        self.assertEqual(y.iloc[10].win,0.)
    def test_missing_calendar_session_not_shifted(self):
        f=fixture().drop(index=1); y=g.make_labels(f)
        self.assertEqual(y.loc[0].label_status,'missing endpoints')
        self.assertTrue(np.isnan(y.loc[0].net_return))
    def test_unknown_ST_not_named_listed(self):
        f=fixture(); f.loc[1,'st_status']='UNKNOWN_DAY'
        y=g.make_labels(f);self.assertEqual(y.loc[0].label_status,'UNKNOWN_ST')
        self.assertTrue(np.isnan(y.loc[0].win))
    def test_invalid_factors_never_completed(self):
        for i in [1,6]:
            for v in [0.,-1.,np.nan,np.inf]:
                f=fixture(); f.loc[i,'adj_factor']=v;y=g.make_labels(f)
                self.assertEqual(y.loc[0].label_status,'invalidbar')
                self.assertTrue(np.isnan(y.loc[0].net_return))
    def test_boundaries_and_no_delayed_sale(self):
        f=fixture();f.loc[6,'open']=9.;y=g.make_labels(f)
        self.assertEqual(y.loc[0].label_status,'exitlowerblocked')
        self.assertTrue(np.isnan(y.loc[0].net_return))
        f=fixture();f.loc[1,'open']=11.;self.assertEqual(g.make_labels(f).loc[0].label_status,'entryupperblocked')
    def test_duplicate_rejected(self):
        f=fixture();f=pd.concat([f,f.iloc[:1]])
        with self.assertRaises(AssertionError):g.make_labels(f)
    def test_row_order_invariance(self):
        f=fixture();a=g.make_labels(f).sort_index();b=g.make_labels(f.sample(frac=1,random_state=2)).sort_index()
        pd.testing.assert_frame_equal(a,b)
    def test_forward_change_does_not_change_features(self):
        f=fixture()
        for k in g.PRICE_FIELDS+g.CONTEXT_FIELDS:f[k]=1.
        for k in ['ma5','ma10','ma20','hi20','lo10']:f[k]=10.
        for k in ['vol5','vol20']:f[k]=100.
        f['regime']='unknown';a,_,_=g.make_features(f)
        f.loc[6,'close']=99.;b,_,_=g.make_features(f)
        pd.testing.assert_series_equal(a.loc[0],b.loc[0])
        self.assertEqual(a.loc[0,'regime_unknown_flag'],1.)
        self.assertEqual(a.loc[0,'adjclose_ma5_ratio'],0.)

if __name__=='__main__':unittest.main()
