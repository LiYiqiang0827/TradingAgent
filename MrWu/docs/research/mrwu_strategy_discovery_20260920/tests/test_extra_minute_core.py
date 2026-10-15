"""Synthetic fixtures only. Kimi checklist independently corrected/verified."""
import copy
import unittest
import numpy as np
import research_extra_minute_core as m


def row():
    return dict(prior_close=100.,prior_amount20=50000.,prior_st_status='NOT_LISTED_ON_ACCEPTED_DAY',
        prior_adjclose=100.,prior_ma20_adjusted=99.,prior_mom5=.01)


def context():
    n=240
    return dict(price=np.full(n,100.),volume=np.full(n,2.),volume_ratio=np.full(n,1.3),
        peer_median=np.full(n,.01),peer_breadth=np.full(n,.6),participation_proxy=np.full(n,.1),
        max_participation_proxy=np.full(n,.2),healthy=np.ones(n,dtype=bool),usable=np.ones(n,dtype=bool),
        anchor_prices=np.full((2,n),103.),anchor_returns=np.full((2,n),.03),
        anchor_drawdown=np.zeros((2,n)),prior_anchor_mom5=.03)


def ready(card,t=None):
    c=context()
    if card=='M03':
        t=9 if t is None else t;c['price'][t:]=101.
        c['peer_breadth'][:t]=.45;c['peer_breadth'][t:]=.65;c['participation_proxy'][t:]=.2
    elif card=='M04':
        t=5 if t is None else t;c['price'][t:]=101.+np.arange(240-t)*.01
        c['peer_breadth'][:t]=.4;c['peer_breadth'][t:]=.6
        c['anchor_prices'][:,:t]=100.1;c['anchor_prices'][:,t:]=100.2+np.arange(240-t)*.01
        c['anchor_returns'][:]=.002;c['healthy'][:]=False
    elif card=='M16':
        t=124 if t is None else t;c['price'][t:]=101.
        c['peer_median'][:120]=.01;c['peer_median'][120:]=.02
        c['peer_breadth'][:120]=.45;c['peer_breadth'][120:]=.7
    elif card=='M17':
        t=20 if t is None else t;c['price'][t-19]=100.;c['price'][t-11]=102.
        c['price'][t-10:t]=102.;c['price'][t]=103.
        c['volume'][t-19:t-10]=10.;c['volume'][t-10:t]=5.;c['volume'][t]=8.
    return c


class CoreTests(unittest.TestCase):
    def signal(self,card,c=None,stride=1,r=None):return m.extra_first_trigger(card,row() if r is None else r,ready(card) if c is None else c,stride)

    def test_all_four_positive_exact_first_trigger(self):
        for card,t in [('M03',9),('M04',5),('M16',124),('M17',20)]:
            with self.subTest(card=card):self.assertEqual(self.signal(card)[:2],(t,m.TRIGGER))

    def test_explicit_ST_string_not_truthy_bool(self):
        for st,status in [('ST_LISTED',m.NO_PRECONDITION),('UNKNOWN_DAY',m.UNKNOWN_PRIOR),
                          ('NOT_LISTED_ON_ACCEPTED_DAY',m.TRIGGER),(None,m.UNKNOWN_PRIOR),(False,m.UNKNOWN_PRIOR)]:
            r=row();r['prior_st_status']=st
            self.assertEqual(self.signal('M03',r=r)[1],status)

    def test_missing_prior_is_unknown_not_no_precondition(self):
        for key in ('prior_close','prior_amount20'):
            r=row();r[key]=np.nan;self.assertEqual(self.signal('M03',r=r)[1],m.UNKNOWN_PRIOR)

    def test_known_prior_liquidity_and_price_fail(self):
        for key,value in [('prior_close',1.9),('prior_amount20',19999.)]:
            r=row();r[key]=value;self.assertEqual(self.signal('M03',r=r)[1],m.NO_PRECONDITION)

    def test_prior_trend_and_M04_anchor_momentum(self):
        for card in ('M04','M17'):
            r=row();r['prior_ma20_adjusted']=101.;self.assertEqual(self.signal(card,r=r)[1],m.NO_PRECONDITION)
        r=row();r['prior_mom5']=.04;self.assertEqual(self.signal('M04',r=r)[1],m.NO_PRECONDITION)
        r=row();r['prior_mom5']=np.nan;self.assertEqual(self.signal('M04',r=r)[1],m.UNKNOWN_PRIOR)
        self.assertEqual(self.signal('M17',r=r)[1],m.TRIGGER)

    def test_grid_exact_intersection_never_before_range(self):
        self.assertEqual(m.decisions(5,5,59)[0],9)
        self.assertEqual(m.decisions(5,20,119)[0],24)
        self.assertEqual(m.decisions(5,124,149),[124,129,134,139,144,149])
        self.assertEqual(self.signal('M04',stride=5)[0],9)
        self.assertEqual(self.signal('M17',ready('M17',24),5)[0],24)
        self.assertIsNone(self.signal('M17',stride=5)[0])

    def test_invalid_card_and_stride_rejected(self):
        for stride in (0,2,True,'5'):
            with self.assertRaises(ValueError):self.signal('M03',stride=stride)
        with self.assertRaises(ValueError):self.signal('M99',context())

    def test_M03_participation_share_not_raw_volume(self):
        c=ready('M03');c['volume'][:]=2.
        self.assertEqual(self.signal('M03',c)[0],9)
        c['volume'][9]=1000.;c['participation_proxy'][:]=.1
        self.assertEqual(self.signal('M03',c)[1],m.NO_TRIGGER)

    def test_M03_cumulative_return_not_one_point_return(self):
        c=ready('M03');c['price'][:9]=103.5;c['price'][9:]=104.
        self.assertLess(c['price'][9]/c['price'][8]-1,.03-.005)
        self.assertGreater(c['price'][9]/100.-1,.03-.005)
        self.assertEqual(self.signal('M03',c)[1],m.NO_TRIGGER)

    def test_M03_platform_excludes_current_anchor_price(self):
        c=ready('M03');c['anchor_prices'][:,9]=200.
        self.assertEqual(self.signal('M03',c)[0],9)
        c['anchor_prices'][:,4]=110.
        self.assertEqual(self.signal('M03',c)[1],m.NO_TRIGGER)

    def test_M03_requires_at_least_one_not_both_platform_anchors(self):
        c=ready('M03');c['anchor_returns'][1,:]=.001
        self.assertEqual(self.signal('M03',c)[0],9)

    def test_M03_peer_concentration_and_lag_conditions(self):
        c=ready('M03');c['max_participation_proxy'][:]=.5
        self.assertEqual(self.signal('M03',c)[1],m.NO_TRIGGER)

    def test_M04_no_old_healthy_or_participation_dependency(self):
        c=ready('M04');c.pop('healthy');c.pop('participation_proxy');c.pop('max_participation_proxy')
        self.assertEqual(self.signal('M04',c)[0],5)

    def test_M04_requires_both_distinct_anchor_conditions(self):
        c=ready('M04');c['anchor_returns'][1,:]=0.
        self.assertEqual(self.signal('M04',c)[1],m.NO_TRIGGER)
        c=ready('M04');c['anchor_drawdown'][1,:]=-.008
        self.assertEqual(self.signal('M04',c)[1],m.NO_TRIGGER)

    def test_M04_gap_missing_is_unknown_and_outside_is_no_precondition(self):
        c=ready('M04');c['price'][0]=np.nan
        self.assertEqual(self.signal('M04',c)[1],m.UNKNOWN_CURRENT)
        c['price'][0]=103.;self.assertEqual(self.signal('M04',c)[1],m.NO_PRECONDITION)

    def test_M16_full_morning_peak_not_only_last30(self):
        c=ready('M16');c['price'][20]=105.
        self.assertEqual(self.signal('M16',c)[1],m.NO_TRIGGER)

    def test_M16_early_morning_missing_immediate_unknown(self):
        c=ready('M16');c['price'][3]=np.nan
        result=self.signal('M16',c)
        self.assertEqual(result[:2],(None,m.UNKNOWN_CURRENT));self.assertEqual(result[2]['t'],124)

    def test_M16_current_return_prior_close_not_open(self):
        c=ready('M16');c['price'][:124]=98.;c['price'][124:]=99.5
        self.assertGreater(c['price'][124]/c['price'][0]-1,0)
        self.assertEqual(self.signal('M16',c)[1],m.NO_TRIGGER)

    def test_M16_needs150_points_no_invented_lunch_bars(self):
        c=ready('M16');c['price']=c['price'][:149]
        self.assertEqual(self.signal('M16',c)[1],m.UNKNOWN_CURRENT)
        c=ready('M16');c['price'][120:124]=np.nan  # Not used by the frozen afternoon definition.
        self.assertEqual(self.signal('M16',c)[0],124)

    def test_M16_does_not_require_unconsumed_volume_or_anchors(self):
        c=ready('M16');c['volume'][:120]=np.nan;c.pop('anchor_prices');c.pop('anchor_returns')
        self.assertEqual(self.signal('M16',c)[0],124)

    def test_M17_current_price_and_volume_excluded_from_prior_windows(self):
        c=ready('M17');c['price'][20]=1000.;c['volume'][20]=1000.
        result=self.signal('M17',c)
        self.assertEqual(result[0],20)
        self.assertEqual(result[2]['prior10_mean_volume'],5.)
        self.assertEqual(result[2]['baseline9_mean_volume'],10.)
        self.assertEqual(result[2]['prior10_high'],102.)

    def test_M17_advance_endpoints_are_tminus19_tminus11(self):
        c=ready('M17');c['price'][9]=100.
        self.assertEqual(self.signal('M17',c)[1],m.NO_TRIGGER)
        c=ready('M17');c['price'][2:9]=np.nan  # These intervening prices do not enter the endpoint formula.
        self.assertEqual(self.signal('M17',c)[0],20)

    def test_M17_nonpositive_baseline_is_known_no_trigger(self):
        c=ready('M17');c['volume'][:]=0.
        self.assertEqual(self.signal('M17',c)[1],m.NO_TRIGGER)

    def test_M17_zero_prior10_volume_not_a_meaningful_volume_multiple(self):
        for current in (0.,100.):
            c=ready('M17');c['volume'][10:20]=0.;c['volume'][20]=current
            self.assertGreater(c['volume'][1:10].mean(),0.)
            self.assertGreater(c['price'][20],c['price'][10:20].max())
            self.assertEqual(self.signal('M17',c)[1],m.NO_TRIGGER)

    def test_M17_does_not_consume_other_mechanisms_fields(self):
        c=ready('M17')
        for key in ('volume_ratio','participation_proxy','max_participation_proxy','anchor_prices','anchor_returns','peer_breadth','peer_median'):c.pop(key)
        self.assertEqual(self.signal('M17',c)[0],20)

    def test_missing_first_decision_never_skipped_for_later_good_signal(self):
        c=ready('M03',14);c['volume_ratio'][9]=np.nan
        result=self.signal('M03',c)
        self.assertEqual(result[:2],(None,m.UNKNOWN_CURRENT));self.assertEqual(result[2]['t'],9)

    def test_false_usable_is_unknown_false_healthy_is_no_signal(self):
        c=ready('M03');c['usable'][9]=False
        self.assertEqual(self.signal('M03',c)[1],m.UNKNOWN_CURRENT)
        c=ready('M03');c['healthy'][:]=False
        self.assertEqual(self.signal('M03',c)[1],m.NO_TRIGGER)

    def test_nan_boolean_not_truthy_and_anchor_shape_exact_two(self):
        c=ready('M03');c['healthy']=np.ones(240);c['healthy'][9]=np.nan
        self.assertEqual(self.signal('M03',c)[1],m.UNKNOWN_CURRENT)
        c=ready('M04');c['anchor_prices']=np.ones((3,240))*103.
        self.assertEqual(self.signal('M04',c)[1],m.UNKNOWN_CURRENT)

    def test_future_mutation_including_non_numeric_cannot_change_earlier_signal(self):
        for card,t in [('M03',9),('M04',5),('M16',124),('M17',20)]:
            c=ready(card);expected=self.signal(card,c)
            for key,value in list(c.items()):
                if isinstance(value,np.ndarray):
                    changed=value.astype(object);changed[...,t+1:]='not observed yet';c[key]=changed
            self.assertEqual(self.signal(card,c),expected)

    def test_inputs_not_modified(self):
        for card in m.CARDS:
            c=ready(card);original=copy.deepcopy(c);r=row();r0=r.copy();self.signal(card,c,r=r)
            self.assertEqual(r,r0)
            for key,value in c.items():np.testing.assert_equal(value,original[key])

    def test_prior_precondition_does_not_need_future_context(self):
        r=row();r['prior_st_status']='ST_LISTED'
        self.assertEqual(self.signal('M04',{},r=r)[1],m.NO_PRECONDITION)

    @unittest.skip("requires the private stage adapter; pure core tests remain public")
    def test_actual_build_context_schema_M04_without_old_healthy(self):
        import pandas as pd
        import research_intraday_relay_signals as original
        members=original.frozen_roles(pd.DataFrame([dict(ts_code=f'{i:06d}.SZ',prior_close=100.,
            prior_mom5=.2-i*.01,prior_participation3=.2-i*.01) for i in range(10)]))
        prices=np.full((10,240),100.);prices[:2,:5]=100.1;prices[:2,5:]=100.2
        prices[2:,5:]=101.
        ctx=original.build_context(members,'000002.SZ',prices,np.full_like(prices,2.),np.ones_like(prices))
        self.assertFalse(ctx['healthy'][5])  # Both rising anchors remain below old single-anchor 1% hurdle.
        self.assertTrue(ctx['usable'][5]);self.assertNotIn('000002.SZ',ctx['anchor_codes'])
        self.assertEqual(self.signal('M04',ctx)[:2],(5,m.TRIGGER))

if __name__=='__main__':unittest.main()
