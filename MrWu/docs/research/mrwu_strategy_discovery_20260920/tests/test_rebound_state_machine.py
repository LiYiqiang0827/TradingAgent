"""Primary corrected Kimi draft: removed invented API and wrong timing oracles."""
import unittest
from rebound_state_machine import simulate_rebound_path as simulate


def row(price=100., low=100., state='SELLABLE_PROXY'):
    return dict(open_state=state, adj_low=low, adj_close=price)


class ReboundTests(unittest.TestCase):
    def test_r0_next_open(self):
        self.assertEqual(simulate([row()]*3, 0)['exit_offset'], 1)

    def test_event_day_not_a_signal(self):
        r=simulate([row(106),row(),row(),row(),row()],3)
        self.assertIsNone(r['signal_offset'])
        self.assertEqual(r['exit_offset'],4)

    def test_signal_cannot_fill_same_close(self):
        r=simulate([row(),row(105),row()],5)
        self.assertEqual((r['signal_offset'],r['exit_offset']),(1,2))

    def test_timeout(self):
        for w in (3,5,10):
            self.assertEqual(simulate([row()]*(w+1),w)['state'],'RIGHT_CENSORED')
            self.assertEqual(simulate([row()]*(w+2),w)['exit_offset'],w+1)

    def test_intent_never_resets(self):
        r=simulate([row(),row(105),row(80,80,'BLOCKED'),row(75,75)],10)
        self.assertEqual((r['signal_offset'],r['exit_offset']),(1,3))
        self.assertEqual([t['intent'] for t in r['trace']],['WAIT','WANT_SELL','WANT_SELL'])

    def test_running_low_can_trigger(self):
        r=simulate([row(),row(104),row(94.5,90),row()],10)
        self.assertEqual((r['signal_offset'],r['exit_offset']),(2,3))

    def test_unknown_when_selling_stops(self):
        r=simulate([row(),row(state='UNKNOWN'),row()],0)
        self.assertEqual(r['state'],'UNKNOWN_POLICY_PATH')
        self.assertIsNone(r['exit_offset'])

    def test_unknown_open_when_waiting_does_not_invent_sale(self):
        r=simulate([row(),row(state='UNKNOWN')],5)
        self.assertEqual(r['state'],'RIGHT_CENSORED')

    def test_bad_close_while_waiting(self):
        for q in (row(90,95),row(float('nan')),row(0,0)):
            self.assertEqual(simulate([row(),q],5)['state'],'UNKNOWN_POLICY_PATH')

    def test_future_close_not_required_for_open_exit(self):
        self.assertEqual(simulate([row(),row(float('nan'))],0)['exit_offset'],1)

    def test_full_blocked_path(self):
        self.assertEqual(simulate([row(state='BLOCKED')]*41,0)['state'],'UNRESOLVED_EXIT_BLOCKED')
        self.assertEqual(simulate([row(state='BLOCKED')]*40,0)['state'],'RIGHT_CENSORED')

    def test_validation(self):
        for w in (True,False,-1,4,3.,None):
            with self.assertRaises(ValueError):simulate([row()],w)
        for rows in ([],[row()]*42,[{}],[row(state='BAD')]):
            with self.assertRaises(ValueError):simulate(rows,3)


if __name__=='__main__':unittest.main()
