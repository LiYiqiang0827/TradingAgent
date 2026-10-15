import unittest
import numpy as np
import pandas as pd
from trend_pullback_features import flat_change, make_frame, entry_candidates, EntryRules, MODEL_FEATURES
from study_ma250_reclaim import TIMES


class FeatureTests(unittest.TestCase):
    def test_outgoing_prices_exact(self):
        closes = pd.Series(np.random.default_rng(11).uniform(10, 20, 160))
        for n in [20, 60, 120]:
            for h in [1, 8, 16]:
                actual = flat_change(closes, n, h)
                for i in range(n-1, len(closes)):
                    before = closes.iloc[i-n+1:i+1].to_list()
                    after = before[h:] + [closes.iloc[i]]*h
                    self.assertAlmostEqual(actual.iloc[i], np.mean(after)-np.mean(before), places=12)

    def data(self):
        days = pd.bdate_range('2025-01-01', periods=60).strftime('%Y%m%d').tolist()
        times = [pd.Timestamp(d+' '+t) for d in days for t in TIMES]
        p = 10 + np.arange(len(times))*.001 + .02*np.sin(np.arange(len(times)))
        raw = pd.DataFrame(dict(datetime=times,open=p-.001,high=p+.01,low=p-.01,close=p,vol=100.,adj_factor=1.))
        daily = pd.DataFrame(dict(trade_date=days,trend_strength=.5,trend_ok=True,atr14_pct=2.,pullback_pct=4.,volume_ratio=1.,near_ma_pct=1.))
        return days, raw, daily

    def test_features_and_candidates_prefix_invariant(self):
        days,raw,d = self.data(); full=make_frame(raw,d,days)
        cut=days[40]; truncated=make_frame(raw[raw.datetime.lt(pd.Timestamp(cut)+pd.Timedelta(days=1))],d[d.trade_date.le(cut)],days[:41])
        pd.testing.assert_frame_equal(full.loc[:len(truncated)-1,MODEL_FEATURES+['daily_as_of']],truncated[MODEL_FEATURES+['daily_as_of']])
        for family in ['base_break','higher_low','ma_reclaim']:
            a=entry_candidates(full,'X',EntryRules(family=family),days[0],cut)
            b=entry_candidates(truncated,'X',EntryRules(family=family),days[0],cut)
            self.assertEqual(a,b)

    def test_today_daily_never_seen(self):
        days,raw,d=self.data(); f=make_frame(raw,d,days)
        mutated=d.copy(); mutated.loc[mutated.trade_date.eq(days[30]),'trend_strength']=999
        g=make_frame(raw,mutated,days)
        mask=f.trade_date.eq(days[30]); self.assertTrue((f.loc[mask,'daily_trend_strength']==g.loc[mask,'daily_trend_strength']).all())
        following=f.trade_date.eq(days[31]); self.assertTrue((g.loc[following,'daily_trend_strength']==999).all())

    def test_missing_bar_breaks_ma(self):
        days,raw,d=self.data(); raw=raw.drop(index=500); f=make_frame(raw,d,days)
        self.assertFalse(f.loc[500,'valid']); self.assertTrue(f.loc[500:519,'ma20'].isna().all())


if __name__=='__main__': unittest.main()
