"""Synthetic only; Kimi fixtures corrected against actual accepted schemas."""
import unittest
import numpy as np
import pandas as pd
import market_activity_overlay_core as m

CAL=['20240102','20240103','20240104','20240105','20240108']


def labels():
    rows=[];phases=['RISING','RETREAT','ACTIVE','UNKNOWN','RISING']
    for variant in m.VARIANTS:
        for i,day in enumerate(CAL):
            rows.append(dict(trade_date=day,variant=variant,confirmed_phase=phases[i],level='HIGH',
                decision_phase=phases[i-1] if i else 'UNKNOWN',decision_level='HIGH' if i else 'UNKNOWN',
                decision_source_date=CAL[i-1] if i else None,coverage_state='OBSERVED_UNIVERSE_PROXY',
                decision_coverage_state='OBSERVED_UNIVERSE_PROXY' if i else 'UNKNOWN',market_scope='HS_A_PROXY'))
    return pd.DataFrame(rows)


def early_raw():
    return pd.DataFrame([dict(event_id='e'+str(i),trade_date='20240103',ts_code='000001.SZ',
        closed=closed,entered=entered,net_return=ret,state=state,hold=3,cost='base',
        clock_cohort='TS',time_group='OPEN_0_TO_5') for i,(ret,closed,entered,state) in enumerate([
            (.1,True,True,'CLOSED_PROXY'),(-.05,True,True,'CLOSED_PROXY'),
            (0.,True,True,'CLOSED_PROXY'),(np.nan,False,True,'UNKNOWN_EXIT_PATH'),
            (0.,False,False,'UNFILLED_LIMIT_UP')])])


def minute_raw():
    return pd.DataFrame([dict(cohort_id='c',ts_code='000001.SZ',target_date='20240108',
        state='COMPLETED',entry_date='20240108',net_return=.02,card='M01',scan_stride=1,
        entry_delay=1,exit_point_policy=0,cost='base',scope='POINT_PROXY',baseline='SAME_MINUTE',
        population_role='CANDIDATE',cohort_role='CONTROL')])


class OverlayTests(unittest.TestCase):
    def test_minute_exact_previous_weekend_and_source_coverage(self):
        l=labels();l.loc[l.trade_date.eq('20240108'),'coverage_state']='UNKNOWN'
        out=m.join_labels(m.normalize_events(minute_raw(),'minute8'),l,CAL)
        self.assertEqual(set(out.regime_source_date),{'20240105'})
        self.assertEqual(set(out.activity_phase),{'UNKNOWN'})
        self.assertEqual(set(out.activity_coverage),{'OBSERVED_UNIVERSE_PROXY'})

    def test_close_uses_current_not_previous(self):
        out=m.join_labels(m.normalize_events(early_raw(),'early_trade'),labels(),CAL)
        self.assertEqual(len(out),10);self.assertEqual(set(out.activity_phase),{'RETREAT'})
        self.assertEqual(set(out.regime_source_date),{'20240103'})

    def test_missing_label_no_row_loss_or_ffill(self):
        l=labels();l=l.loc[~l.trade_date.eq('20240103')].copy()
        mask=l.trade_date.eq('20240104')
        l.loc[mask,['decision_phase','decision_level','decision_coverage_state']]='UNKNOWN'
        out=m.join_labels(m.normalize_events(early_raw(),'early_trade'),l,CAL)
        self.assertEqual(len(out),10);self.assertEqual(set(out.activity_phase),{'UNKNOWN'})

    def test_duplicate_labels_and_events_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):m.normalize_events(pd.concat([early_raw(),early_raw()]),'early_trade')
        with self.assertRaises(ValueError):m.join_labels(m.normalize_events(early_raw(),'early_trade'),pd.concat([labels(),labels()]),CAL)

    def test_exact_lag_and_shifted_phase_not_optional(self):
        for column,value in [('decision_source_date','20240102'),('decision_phase','ACTIVE'),('decision_level','LOW')]:
            l=labels();l.loc[l.trade_date.eq('20240108'),column]=value
            with self.assertRaises(ValueError):m.join_labels(m.normalize_events(minute_raw(),'minute8'),l,CAL)

    def test_wlz_unresolved_and_no_trade_cash_excluded(self):
        out=m.join_labels(m.normalize_events(early_raw(),'early_trade'),labels(),CAL)
        summary=m.summarize(out)
        self.assertEqual(len(summary),2)
        for r in summary.itertuples():
            self.assertEqual((r.candidate_rows,r.entered,r.closed,r.W,r.L,r.Z,r.unresolved_entered),(5,4,3,1,1,1,1))
            self.assertAlmostEqual(r.mean,.05/3);self.assertAlmostEqual(r.win_rate,1/3)
            self.assertEqual((r.all_entered_win_rate_lower_bound,r.all_entered_win_rate_upper_bound),(.25,.5))
            self.assertAlmostEqual(r.return_profit_factor,2.);self.assertAlmostEqual(r.payoff,2.)
            self.assertEqual(r.unknown_outcome_rows,1);self.assertEqual(r.state_counts['UNFILLED_LIMIT_UP'],1)

    def test_cost_clock_and_card_never_pooled(self):
        frame=early_raw().iloc[:1].copy();second=frame.copy();second['cost']='stress';third=frame.copy();third['clock_cohort']='KPL'
        out=m.join_labels(m.normalize_events(pd.concat([frame,second,third]),'early_trade'),labels(),CAL)
        self.assertEqual(len(m.summarize(out)),6)
        frame=minute_raw();second=frame.copy();second['card']='M02'
        out=m.join_labels(m.normalize_events(pd.concat([frame,second]),'minute_extra'),labels(),CAL)
        self.assertEqual(len(m.summarize(out)),4)

    def test_same_layer_duplicate_stock_day_in_other_cohort_blocks(self):
        first=minute_raw();second=first.copy();second['cohort_id']='different_theme'
        with self.assertRaisesRegex(ValueError,'within_context'):
            m.normalize_events(pd.concat([first,second]),'minute8')
        second['cohort_role']='DIFFERENT_CONTEXT'
        out=m.summarize(m.join_labels(m.normalize_events(pd.concat([first,second]),'minute8'),labels(),CAL))
        self.assertEqual(len(out),4)  # separate contexts, never a pooled pair

    def test_future_current_close_cannot_change_past_join(self):
        events=m.normalize_events(early_raw(),'early_trade');l=labels()
        before=m.join_labels(events,l,CAL);l.loc[l.trade_date.eq(CAL[-1]),'confirmed_phase']='INACTIVE'
        pd.testing.assert_frame_equal(before,m.join_labels(events,l,CAL))

    def test_unknown_breaks_switch_chain(self):
        result=m.market_coverage(labels(),CAL)
        self.assertEqual(result.switch_count.tolist(),[2,2])
        self.assertEqual(result.unknown_phase_dates.tolist(),[['20240105'],['20240105']])

    def test_actual_description_ret_field_and_no_trade_bounds(self):
        frame=pd.DataFrame([dict(event_id='d',trade_date='20240103',complete=True,ret=.2,
            status='COMPLETE',h=3,clock_cohort='TS',time_group='EARLY')])
        out=m.summarize(m.join_labels(m.normalize_events(frame,'early_description'),labels(),CAL))
        self.assertEqual(out['mean'].tolist(),[.2,.2]);self.assertTrue(out.entered.isna().all())
        self.assertTrue(out.all_entered_win_rate_upper_bound.isna().all())

    def test_bool_date_and_closed_return_strict(self):
        for col,value in [('closed','True'),('trade_date',20240103),('net_return',np.nan)]:
            frame=early_raw().iloc[:1].copy();frame[col]=value
            with self.assertRaises(ValueError):m.normalize_events(frame,'early_trade')

    def test_exit_adapter_preserves_old_regime(self):
        frame=pd.DataFrame([dict(event_id='x',trade_date='20240103',entered=True,state='COMPLETED',net_return=-.2,
            family='leader',top_n=8,policy='H10',cost='base',regime='OLD_BULL')])
        out=m.summarize(m.join_labels(m.normalize_events(frame,'exit_policy_v2'),labels(),CAL))
        self.assertEqual(set(out.regime),{'OLD_BULL'});self.assertEqual(set(out.activity_phase),{'RETREAT'})
        self.assertEqual(out.tail15_count.tolist(),[1,1])


if __name__=='__main__':unittest.main()
