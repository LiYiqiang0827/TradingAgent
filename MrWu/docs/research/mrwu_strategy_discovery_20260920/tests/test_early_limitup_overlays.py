import pandas as pd
import numpy as np
import unittest
import early_limitup_overlays as e

DAYS = ['20260105','20260106','20260107','20260108','20260109','20260112','20260113']
CODES = ['000001.SZ','000002.SZ','000003.SZ']

def cols(df, p):
    return [f'{p}_flow5', f'{p}_breadth', f'{p}_active', f'{p}_all3_state', f'{p}_source_date']

class TestAttach(unittest.TestCase):
    def mother(self, codes=CODES, days=DAYS):
        rows = []
        for d in days:
            for i, c in enumerate(codes):
                rows.append({'ts_code': c, 'trade_date': d, 'time_group': 'g', 'board': 'main',
                             'st_status': 0, 'flat_at_upper': 0, 'reopen_label': 0,
                             'board_streak_group': i})
        return pd.DataFrame(rows)

    def peer(self, days=DAYS, ratio=0.1, cov=1.0, skip=None):
        rows = []
        for d in days:
            if d == skip:
                continue
            for c in CODES:
                rows.append({'trade_date': d, 'ts_code': c, 'peer_flow_ratio': ratio,
                             'peer_breadth': 0.7, 'peer_active': 1.2, 'peer_return_coverage': cov,
                             'peer_active_coverage': cov, 'peer_flow_coverage': cov, 'theme_count': 2})
        return pd.DataFrame(rows)

    def sig(self, name, rows):
        df = pd.DataFrame(rows)
        for c in ['selection_score', 'aux_source_date']:
            if c not in df:
                df[c] = np.nan if c == 'selection_score' else None
        return {name: df}

    def test_flow5_requires_exact_five_days_and_drop_midday_unknown(self):
        m = self.mother()
        ok = e.attach_context(m, self.peer(), DAYS, {})
        bad = e.attach_context(m, self.peer(skip='20260107'), DAYS, {})
        for p in ('postclose', 'lag1'):
            self.assertTrue((ok.loc[ok.trade_date == '20260112', f'{p}_all3_state'] != 'UNKNOWN').all())
            self.assertTrue((bad.loc[bad.trade_date == '20260112', f'{p}_all3_state'] == 'UNKNOWN').all())

    def test_lag1_source_jan09_for_jan12_excludes_jan12(self):
        p = self.peer()
        p.loc[p.trade_date == '20260112', 'peer_flow_ratio'] = 99.0
        out = e.attach_context(self.mother(), p, DAYS, {})
        row = out[(out.trade_date == '20260112') & (out.ts_code == '000001.SZ')].iloc[0]
        self.assertEqual(row['lag1_source_date'], '20260109')
        self.assertNotEqual(row['lag1_flow5'], 99.0)

    def test_future_append_cannot_change_prior_row(self):
        m = self.mother(days=DAYS[:5])
        before = e.attach_context(m, self.peer(days=DAYS[:5]), DAYS[:5], {})
        after = e.attach_context(self.mother(), self.peer(), DAYS, {})
        a = before[(before.trade_date == '20260109') & (before.ts_code == '000001.SZ')].iloc[0]
        b = after[(after.trade_date == '20260109') & (after.ts_code == '000001.SZ')].iloc[0]
        for c in cols(before, 'lag1') + cols(before, 'postclose'):
            if c in before:
                self.assertTrue((pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c])

    def test_low_coverage_one_flow_day_resets_rolling(self):
        p = self.peer()
        p.loc[p.trade_date == '20260108', 'peer_flow_coverage'] = 0.0
        out = e.attach_context(self.mother(), p, DAYS, {})
        self.assertTrue((out.loc[out.trade_date == '20260112', 'postclose_all3_state'] == 'UNKNOWN').all())

    def test_global_top8_not_rank_within_mother_rank9_stays_fail(self):
        m = self.mother(codes=['000009.SZ'])
        rows = [{'ts_code': f'{i:06d}.SZ', 'trade_date': '20260109', 'selection_score': 100 - i,
                 'aux_source_date': '20260108'} for i in range(1,11)]
        sf = {'combo_participation': pd.DataFrame(rows)}
        out = e.attach_context(m, self.peer(), DAYS, sf)
        r = out[out.trade_date == '20260109'].iloc[0]
        self.assertEqual(r['combo_participation_top8_state'], 'FAIL')
        self.assertFalse(r['combo_participation_literal_top8_intersection'])

    def test_outside_frozen_pool_unknown_and_empty_signal_frames(self):
        m = self.mother(codes=['000001.SZ'])
        pool = pd.DataFrame([{'ts_code': '000002.SZ', 'trade_date': '20260109', 'selection_score': 1,
                              'aux_source_date': '20260108'}])
        out = e.attach_context(m, self.peer(), DAYS, {'leader_continuation': pool})
        self.assertEqual(out.loc[out.trade_date == '20260109', 'leader_continuation_top8_state'].iloc[0], 'UNKNOWN')
        empty = e.attach_context(self.mother(days=DAYS[:1]), self.peer(days=DAYS[:1]), DAYS[:1], {})
        for p in ('postclose', 'lag1'):
            self.assertEqual(empty[f'{p}_all3_state'].iloc[0], 'UNKNOWN')
        self.assertFalse(bool(empty['leader_continuation_literal_top8_intersection'].iloc[0]))

    def test_duplicate_mother_or_peer_raises(self):
        m = pd.concat([self.mother(days=DAYS[:1]), self.mother(days=DAYS[:1])], ignore_index=True)
        with self.assertRaises(Exception):
            e.attach_context(m, self.peer(days=DAYS[:1]), DAYS[:1], {})
        p = pd.concat([self.peer(days=DAYS[:1]), self.peer(days=DAYS[:1])], ignore_index=True)
        with self.assertRaises(Exception):
            e.attach_context(self.mother(days=DAYS[:1]), p, DAYS[:1], {})

    def test_higher_scoring_kpl_cannot_displace_tushare_rank(self):
        codes=[f'{i:06d}.SZ' for i in range(1,6)]
        m=self.mother(codes=codes[:4],days=[DAYS[-1]])
        m['time_source']='TUSHARE_FIRST_TIME';m['time_quality']='TUSHARE_ONLY'
        pieces=[]
        for i,code in enumerate(codes,1):
            p=self.peer().loc[lambda f:f.ts_code.eq(CODES[0])].copy()
            p['ts_code']=code;p['peer_flow_ratio']=i*.1;p['peer_breadth']=.5+i*.05;p['peer_active']=1+i*.1
            pieces.append(p)
        peer=pd.concat(pieces,ignore_index=True)
        base=e.attach_context(m,peer,DAYS,{})
        extra=m.iloc[[0]].copy();extra['ts_code']=codes[-1]
        extra['time_source']='KPL_LU_TIME_ONLY';extra['time_quality']='KPL_ONLY_NEEDS_SEMANTIC_ACCEPTANCE'
        extended=e.attach_context(pd.concat([m,extra],ignore_index=True),peer,DAYS,{})
        for prefix in ('postclose','lag1'):
            cols=['ts_code',prefix+'_rank',prefix+'_rank_score',prefix+'_rank3_state']
            pd.testing.assert_frame_equal(base[cols],extended.loc[extended.ts_code.isin(codes[:4]),cols].reset_index(drop=True))
        self.assertEqual(extended.loc[extended.ts_code.eq(codes[-1]),'postclose_rank'].iloc[0],1)


class TestStatisticsAcceptance(unittest.TestCase):
    """Codex checks mathematical/denominator acceptance after Kimi drafts."""
    def trades(self,returns=(.2,-.1,0.,-.3)):
        return pd.DataFrame([dict(ts_code=f'{i+1:06d}.SZ',trade_date='20260105',hold=1,cost='base',
            entered=True,closed=True,state='CLOSED_PROXY',net_return=r,actual_holding_sessions=1,
            time_group='OPEN_0_TO_5' if i%2==0 else 'AFTER10',board='MAINBOARD',st_status='ST_NOT_LISTED',
            flat_at_upper=False,reopen_label='NO_REOPEN_REPORTED',board_streak_group='FIRST_BOARD',
            time_source='TUSHARE_FIRST_TIME',time_quality='TUSHARE_ONLY',postclose_all3_state='PASS' if i%2==0 else 'FAIL',
            postclose_rank3_state='PASS',combo_participation_top8_state='UNKNOWN') for i,r in enumerate(returns)])

    def test_win_loss_zero_profit_factor_and_tail_denominators(self):
        m=e.metric(self.trades(),'trade')
        self.assertEqual((m['wins'],m['losses'],m['zeros']),(1,2,1))
        self.assertEqual(m['win_rate'],.25);self.assertAlmostEqual(m['avgloss'],-.2)
        self.assertAlmostEqual(m['payoff'],1.);self.assertAlmostEqual(m['profit_factor'],.5)
        self.assertEqual((m['tail15_count'],m['tail30_count']),(1,1))
        self.assertEqual(m['closed_returns'],4)

    def test_one_sided_empty_and_unfilled_are_not_infinite_or_win(self):
        g=self.trades((.2,.1))
        g.loc[1,['entered','closed','state']]=[False,False,'UNFILLED_ENTRY_AT_UPPER']
        m=e.metric(g,'trade')
        self.assertEqual(m['mother_candidates'],2);self.assertEqual(m['entered'],1)
        self.assertEqual(m['not_entered'],1);self.assertEqual(m['closed_returns'],1)
        self.assertIsNone(m['payoff']);self.assertIsNone(m['profit_factor'])
        self.assertIsNone(e.metric(g.iloc[:0],'trade')['mean'])

    def test_closed_missing_and_unresolved_preserved(self):
        g=self.trades((np.nan,.3))
        g.loc[1,['closed','state']]=[False,'UNKNOWN_EXIT_PATH']
        m=e.metric(g,'trade')
        self.assertEqual(m['finite_missing_closed'],1);self.assertEqual(m['unresolved'],1)
        self.assertEqual(m['mother_candidates'],2);self.assertEqual(m['closed_returns'],0)

    def test_description_does_not_require_trade_fields(self):
        g=self.trades().drop(columns=['entered','closed','state','net_return']).assign(h=1,complete=True,status='DESCRIPTIVE_COMPLETE',ret=[.2,-.1,0.,-.3])
        m=e.metric(g,'description');self.assertTrue(m['entry_not_applicable']);self.assertIsNone(m['entered'])
        self.assertEqual(m['closed_returns'],4)

    def test_strict_boolean_and_duplicate_scenario(self):
        g=self.trades();g['entered']='False'
        with self.assertRaises(ValueError): e.summarize(pd.DataFrame(),g)
        g=self.trades()
        with self.assertRaises(ValueError): e.summarize(pd.DataFrame(),pd.concat([g,g]))

    def test_date_pairing_controls_strata_and_bootstrap_deterministic(self):
        rows=[]
        for d in range(5):
            for board,base in [('A',.1),('B',-.2)]:
                for state,add in [('PASS',.02),('FAIL',0)]:
                    rows.append(dict(trade_date=f'202601{d+5:02d}',board=board,closed=True,net_return=base+add,state=state))
        g=pd.DataFrame(rows)
        r=e.paired_dates(g,'state','PASS','FAIL',['board'])
        self.assertAlmostEqual(r['paired_mean'],.02);self.assertEqual(r['shared_dates'],5)
        self.assertEqual(r,e.paired_dates(g,'state','PASS','FAIL',['board']))
        self.assertEqual(r['bootstrap_draws'],500);self.assertEqual(r['matched_strata'],10)
        sparse=e.paired_dates(g.loc[g.trade_date.eq('20260105')],'state','PASS','FAIL',['board'])
        self.assertIsNone(sparse['ci_low']);self.assertEqual(sparse['status'],'INSUFFICIENT_DATES')

    def test_clock_split_early_overlay_denominator_and_unknown(self):
        g=self.trades()
        g.loc[2,'time_source']='KPL_LU_TIME_ONLY'
        outputs=e.summarize(pd.DataFrame(),g)
        main=outputs['main_comparisons']
        self.assertEqual(set(main.clock_cohort),{'TUSHARE_SOURCE_LABEL_PROXY','KPL_ONLY_UNCERTIFIED'})
        s=outputs['summary']
        rows=s.loc[s.dimension.eq('time_group_overlay')&s.clock_cohort.eq('ALL_CLOCKS_DESCRIPTIVE_ONLY')&s.time_group.eq('OPEN_0_TO_5')&s.overlay.eq('combo_participation_top8_state')]
        self.assertEqual(int(rows.loc[rows.label.eq('UNKNOWN'),'mother_candidates'].iloc[0]),2)
        self.assertEqual(int(rows.loc[rows.label.eq('ALL_KNOWN'),'mother_candidates'].iloc[0]),0)
        union=s.loc[s.dimension.eq('time_group')&s.label.eq('OPEN_0_TO10_OVERLAPPING_UNION')&s.clock_cohort.eq('ALL_CLOCKS_DESCRIPTIVE_ONLY')]
        self.assertEqual(int(union.mother_candidates.iloc[0]),2)
