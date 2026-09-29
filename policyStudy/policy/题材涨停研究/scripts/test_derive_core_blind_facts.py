"""Numerical reading aids: comparison direction, equal duration and missing data."""
from copy import deepcopy
import math

import pytest

from test_run_core_pullback_blind import packet
from derive_core_blind_facts import derive, ratio


def test_first_low_and_session_low_have_distinct_relations(packet):
    packet['daily_bars'][-1][4] = 81.18
    packet['m15_bars'][0][5] = 81.85
    packet['m15_bars'][1][5] = 79.85
    facts = derive(packet)['facts']
    assert facts['first_m15_low_vs_previous_day_low']['value']['relation'] == 'above'
    assert facts['session_low_vs_previous_day_low']['value']['relation'] == 'below'


def test_previous_high_excludes_signal_bar(packet):
    # Four previous observed bars; current high must not raise their barrier.
    prior = [[f'M_D-1_{clock.replace(":", "")}', 'D-1', clock, 100, 102, 99, 101, .1]
             for clock in ['14:15', '14:30', '14:45']]
    packet['m15_bars'] = prior + packet['m15_bars']
    packet['m15_bars'][-1][4] = 999
    packet['m15_bars'][-1][6] = 103
    fact = derive(packet)['facts']['close_vs_prior_4_observed_m15_high']
    assert fact['value'] == {'prior_high': 102, 'signal_close': 103, 'close_above_prior_high': True}
    # D-1 15:00 is absent. Four observed bars are still a valid observed window,
    # but must not be advertised as four consecutive scheduled bars.
    assert fact['observation_window']['available_observed_bars'] == 4
    assert fact['observation_window']['scheduled_slots_between_endpoints'] == 5
    assert fact['observation_window']['missing_scheduled_slots_between_endpoints'] == 1


def full_same_clocks(packet):
    p = deepcopy(packet)
    p['m15_bars'] = [[f'M_D{day}_{clock.replace(":", "")}', f'D{day}', clock,
                      100, 102, 99, 101, .1]
                     for day in range(-5, 0) for clock in ['09:45', '10:00']] + p['m15_bars']
    return p


def test_volume_uses_equal_clocks_not_daily_total(packet):
    packet = full_same_clocks(packet)
    packet['daily_bars'][-1][-1] = 12345
    facts = derive(packet)['facts']
    assert facts['last_m15_volume_vs_five_prior_same_clock_median']['value'] == pytest.approx(2)
    assert facts['matched_clock_cumulative_volume_ratio']['value'] == pytest.approx(1.5)


def test_missing_comparison_day_does_not_shrink_denominator(packet):
    packet = full_same_clocks(packet)
    packet['m15_bars'] = [x for x in packet['m15_bars'] if x[0] != 'M_D-2_1000']
    facts = derive(packet)['facts']
    assert facts['last_m15_volume_vs_five_prior_same_clock_median']['status'] == 'unknown'
    assert facts['matched_clock_cumulative_volume_ratio']['status'] == 'unknown'


def test_absent_d0_and_missing_values_are_unknown_not_zero(packet):
    packet['m15_bars'] = []
    facts = derive(packet)['facts']
    assert facts['current_last_m15']['status'] == 'unknown'
    assert facts['session_low_vs_previous_day_low']['status'] == 'unknown'
    packet['m15_bars'] = [['M_D0_0945', 'D0', '09:45', None, None, None, None, None]]
    facts = derive(packet)['facts']
    assert facts['session_so_far']['status'] == 'unknown'
    assert facts['current_first_m15']['status'] == 'unknown'


def test_missing_first_scheduled_bar_is_not_substituted_by_later_bar(packet):
    packet = full_same_clocks(packet)
    packet['m15_bars'] = [x for x in packet['m15_bars'] if x[0] != 'M_D0_0945']
    facts = derive(packet)['facts']
    assert facts['current_first_m15']['status'] == 'unknown'
    assert facts['first_m15_low_vs_previous_day_low']['status'] == 'unknown'
    assert facts['current_last_m15']['status'] == 'known'
    assert facts['current_session_observation_coverage']['value']['missing_bar_ends'] == ['09:45']
    aligned = facts['matched_clock_cumulative_volume_ratio']
    assert aligned['value'] == pytest.approx(2)
    assert aligned['clock_alignment']['compared_bar_ends'] == ['10:00']
    assert not aligned['clock_alignment']['scheduled_slots_complete']


def test_missing_decision_bar_records_actual_observation_end(packet):
    packet['decision']['bar_end'] = '10:15'
    facts = derive(packet)['facts']
    assert facts['current_last_m15']['status'] == 'unknown'
    assert facts['last_m15_volume_vs_five_prior_same_clock_median']['status'] == 'unknown'
    assert facts['session_so_far']['value']['last_observed_bar_end'] == '10:00'
    assert not facts['session_so_far']['value']['scheduled_slots_complete']


def test_missing_volume_preserves_known_price_fields_as_partial(packet):
    packet['m15_bars'][-1][-1] = None
    fact = derive(packet)['facts']['current_last_m15']
    assert fact['status'] == 'partial'
    assert fact['value']['close'] == 102
    assert fact['missing_fields'] == ['volume_ratio']


@pytest.mark.parametrize('invalid', [None, True, 'NaN', -1])
def test_invalid_volume_is_not_used_as_zero_or_a_valid_ratio(packet, invalid):
    packet = full_same_clocks(packet)
    packet['m15_bars'][-1][-1] = invalid
    facts = derive(packet)['facts']
    assert facts['last_m15_volume_vs_five_prior_same_clock_median']['status'] == 'unknown'
    assert facts['matched_clock_cumulative_volume_ratio']['status'] == 'unknown'


@pytest.mark.parametrize('invalid', [math.nan, math.inf, -math.inf])
def test_nonfinite_input_rejected_before_derivation(packet, invalid):
    packet['m15_bars'][-1][-1] = invalid
    with pytest.raises(ValueError):
        derive(packet)


def test_ratio_zero_missing_and_overflow_are_not_misrepresented(packet):
    assert ratio(0, 1) == 0
    assert ratio(1, 0) is None
    assert ratio(1e308, 1e-308) is None
    packet = full_same_clocks(packet)
    for row in packet['m15_bars']:
        row[-1] = 1e308
    facts = derive(packet)['facts']
    assert facts['last_m15_volume_vs_five_prior_same_clock_median']['value'] == 1
    assert facts['matched_clock_cumulative_volume_ratio']['status'] == 'unknown'


@pytest.mark.parametrize('clock', ['9:45', '09:30', '12:00', '15:15'])
def test_invalid_session_clocks_rejected(packet, clock):
    packet['m15_bars'][0][0] = f'M_D-1_{clock.replace(":", "")}'
    packet['m15_bars'][0][1] = 'D-1'
    packet['m15_bars'][0][2] = clock
    with pytest.raises(ValueError, match='scheduled'):
        derive(packet)


def test_noncanonical_relative_zero_cannot_bypass_cutoff(packet):
    packet['m15_bars'].append(['M_D-0_1500', 'D-0', '15:00', 100, 102, 99, 101, .1])
    with pytest.raises(ValueError, match='noncanonical'):
        derive(packet)


def test_anchor_fact_not_discarded_or_promoted_across_days(packet):
    packet['prior_theme']['own_board_height_known_then'] = None
    packet['anchor']['day'] = 'D-1'
    value = derive(packet)['facts']['prior_theme_core_identity']['value']
    assert value['prior_board_height_explicit'] is None
    assert value['anchor_is_prior_day'] and value['anchor_board_height'] == 3
    packet['anchor']['day'] = 'D-3'
    assert not derive(packet)['facts']['prior_theme_core_identity']['value']['anchor_is_prior_day']


def test_future_or_duplicate_input_rejected_and_inputs_unmodified(packet):
    before = deepcopy(packet)
    derive(packet)
    assert packet == before
    packet['m15_bars'].append(packet['m15_bars'][-1])
    with pytest.raises(ValueError, match='duplicate'):
        derive(packet)
    packet = before
    packet['m15_bars'].append(['M_D0_1015', 'D0', '10:15', 100, 102, 99, 101, .1])
    with pytest.raises(ValueError):
        derive(packet)
