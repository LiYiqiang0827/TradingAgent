"""Deterministic reading aids from an already isolated packet, never outcomes.

Separate v2 preparation: this does not modify frozen v1 model inputs or answers.
Facts are observations, not a strategy or labels for subjective structure checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from run_core_pullback_blind import canonical, validate_packet


M15_CLOCKS = tuple(f'{hour:02d}:{minute:02d}' for hour, minute in
                   [(9, 45), (10, 0), (10, 15), (10, 30), (10, 45),
                    (11, 0), (11, 15), (11, 30), (13, 15), (13, 30),
                    (13, 45), (14, 0), (14, 15), (14, 30), (14, 45), (15, 0)])
BAR_FIELDS = ('open', 'high', 'low', 'close', 'volume_ratio')


def day_index(day):
    return int(day[1:])


def known(rows, field):
    return bool(rows) and all(isinstance(x.get(field), (int, float))
                              and not isinstance(x[field], bool) and math.isfinite(x[field])
                              and (x[field] >= 0 if field == 'volume_ratio' else x[field] > 0)
                              for x in rows)


def ratio(numerator, denominator):
    if not math.isfinite(numerator) or not math.isfinite(denominator) or numerator < 0 or denominator <= 0:
        return None
    result = numerator / denominator
    return result if math.isfinite(result) else None


def derive(packet):
    validate_packet(packet)
    daily = sorted([dict(zip(packet['daily_columns'], x)) for x in packet['daily_bars']],
                   key=lambda x: day_index(x['day']))
    minute = sorted([dict(zip(packet['m15_columns'], x)) for x in packet['m15_bars']],
                    key=lambda x: (day_index(x['day']), x['bar_end']))
    if len({x['id'] for x in daily + minute}) != len(daily + minute):
        raise ValueError('duplicate bar evidence id')
    # The frozen v1 validator is intentionally unchanged. Derived facts impose
    # explicit session-clock semantics rather than lexicographic time guesses.
    if packet['decision']['bar_end'] not in M15_CLOCKS:
        raise ValueError('decision is not a scheduled completed m15 bar')
    for row in daily + minute + [packet['anchor'], packet['prior_theme']]:
        if row['day'] != f'D{day_index(row["day"])}':
            raise ValueError('noncanonical relative day')
    if any(row['bar_end'] not in M15_CLOCKS for row in minute):
        raise ValueError('bar is not a scheduled completed m15 clock')
    d0 = [x for x in minute if x['day'] == 'D0']
    first = next((x for x in d0 if x['bar_end'] == M15_CLOCKS[0]), None)
    previous = next((x for x in daily if x['day'] == 'D-1'), None)
    last = next((x for x in d0 if x['bar_end'] == packet['decision']['bar_end']), None)
    facts = {}

    def add(key, value, rows, definition):
        facts[key] = {'value': value, 'source_ids': [x['id'] for x in rows],
                      'definition': definition, 'status': 'known' if value is not None else 'unknown'}

    for key, rows in [('previous_completed_day', [previous] if previous else []),
                      ('current_first_m15', [first] if first else []),
                      ('current_last_m15', [last] if last else [])]:
        fields = {k: known(rows, k) for k in BAR_FIELDS}
        value = {k: rows[0][k] if fields[k] else None for k in BAR_FIELDS} if rows else None
        add(key, value, rows, 'Literal bar fields; daily and m15 are different durations. '
            'First m15 means scheduled 09:45, never a later observed substitute.')
        facts[key]['missing_fields'] = [k for k, available in fields.items() if not available]
        facts[key]['status'] = ('known' if all(fields.values()) else
                                'partial' if any(fields.values()) else 'unknown')

    expected_clocks = list(M15_CLOCKS[:M15_CLOCKS.index(packet['decision']['bar_end']) + 1])
    missing_clocks = [clock for clock in expected_clocks if clock not in {x['bar_end'] for x in d0}]
    add('current_session_observation_coverage', {
        'expected_bar_ends': expected_clocks, 'observed_bar_ends': [x['bar_end'] for x in d0],
        'missing_bar_ends': missing_clocks, 'complete': not missing_clocks,
        'last_observed_bar_end': d0[-1]['bar_end'] if d0 else None,
        'decision_bar_present': last is not None, 'first_scheduled_bar_present': first is not None,
    }, d0, 'Coverage of scheduled bar slots through the decision, not proof all field values are available.')

    if known(d0, 'low') and known(d0, 'high'):
        value = {'observed_bars': len(d0), 'through': packet['decision']['bar_end'],
                 'last_observed_bar_end': d0[-1]['bar_end'],
                 'scheduled_slots_complete': not missing_clocks,
                 'low': min(x['low'] for x in d0), 'high': max(x['high'] for x in d0)}
    else:
        value = None
    add('session_so_far', value, d0, 'Observed completed D0 m15 bars only; never full-day OHLC.')
    for key, rows in [('first_m15_low_vs_previous_day_low', [first] if first else []),
                      ('session_low_vs_previous_day_low', d0)]:
        comparison = None
        if previous and rows and known([previous] + rows, 'low'):
            observed_low = min(x['low'] for x in rows)
            comparison = {'observed_low': observed_low, 'previous_day_low': previous['low'],
                          'relation': 'above' if observed_low > previous['low'] else
                          'below' if observed_low < previous['low'] else 'equal'}
        add(key, comparison, ([previous] if previous else []) + rows,
            'Low versus low only. This is not confirmation of a swing higher-low pattern.')

    for count in [4, 8, 16]:
        before = [x for x in minute if last and
                  (day_index(x['day']), x['bar_end']) < (0, last['bar_end'])][-count:]
        value = None
        if last and len(before) == count and known(before, 'high') and known([last], 'close'):
            barrier = max(x['high'] for x in before)
            value = {'prior_high': barrier, 'signal_close': last['close'],
                     'close_above_prior_high': last['close'] > barrier}
        add(f'close_vs_prior_{count}_observed_m15_high', value, before + ([last] if last else []),
            'Excludes current bar from prior high. Missing scheduled bars are not imputed; '
            'window is observed bars. One close above is not proof of a sustained breakout.')
        scheduled_span = (day_index(before[-1]['day']) - day_index(before[0]['day'])) * len(M15_CLOCKS) \
            + M15_CLOCKS.index(before[-1]['bar_end']) - M15_CLOCKS.index(before[0]['bar_end']) + 1 \
            if before else 0
        facts[f'close_vs_prior_{count}_observed_m15_high']['observation_window'] = {
            'required_observed_bars': count, 'available_observed_bars': len(before),
            'first_source_id': before[0]['id'] if before else None,
            'last_source_id': before[-1]['id'] if before else None,
            'scheduled_slots_between_endpoints': scheduled_span,
            'missing_scheduled_slots_between_endpoints': scheduled_span - len(before),
        }

    # Compare equal clock windows; do not compare one m15 bar with an entire day.
    clocks = {x['bar_end'] for x in d0}
    historical_single = [x for x in minute if last and -5 <= day_index(x['day']) <= -1
                         and x['bar_end'] == last['bar_end']]
    single_ratio = None
    if len(historical_single) == 5 and last and known(historical_single + [last], 'volume_ratio'):
        single_ratio = ratio(last['volume_ratio'], median(x['volume_ratio'] for x in historical_single))
    add('last_m15_volume_vs_five_prior_same_clock_median', single_ratio,
        historical_single + ([last] if last else []), 'Current m15 volume / median of same-clock m15 in D-5..D-1.')
    matched, sums = [], []
    for day in range(-5, 0):
        rows = [x for x in minute if x['day'] == f'D{day}' and x['bar_end'] in clocks]
        if {x['bar_end'] for x in rows} == clocks and known(rows, 'volume_ratio'):
            sums.append(sum(x['volume_ratio'] for x in rows)); matched.extend(rows)
    cumulative = None
    if len(sums) == 5 and known(d0, 'volume_ratio'):
        cumulative = ratio(sum(x['volume_ratio'] for x in d0), median(sums))
    add('matched_clock_cumulative_volume_ratio', cumulative, matched + d0,
        'Sum of observed D0 clock slots / median sum of exactly the same slots in each of D-5..D-1. '
        'This does not fill missing clock slots or estimate full-day volume.')
    facts['matched_clock_cumulative_volume_ratio']['clock_alignment'] = {
        'compared_bar_ends': sorted(clocks), 'scheduled_slots_complete': not missing_clocks,
        'historical_days_with_all_compared_slots_and_volume': len(sums),
    }

    anchor = packet['anchor']
    prior = packet['prior_theme']
    add('prior_theme_core_identity', {
        'prior_theme_rank': prior.get('eligible_theme_rank'),
        'prior_theme_heat': prior.get('heat_score'),
        'prior_theme_limit_up_count': prior.get('limit_up_count'),
        'prior_core_rank_explicit': prior.get('core_rank_known_then'),
        'prior_board_height_explicit': prior.get('own_board_height_known_then'),
        'anchor_day': anchor['day'], 'anchor_core_rank': anchor.get('leader_rank_known_then'),
        'anchor_board_height': anchor.get('board_height_known_then'),
        'anchor_is_prior_day': anchor['day'] == 'D-1',
    }, [prior, anchor], 'Null prior fields do not erase anchor facts; an older anchor is not current core proof.')
    return {'schema_version': 'core_blind_derived_facts.v1', 'sample_id': packet['sample_id'],
            'packet_sha256': hashlib.sha256(canonical(packet)).hexdigest(),
            'decision': packet['decision'], 'facts': facts,
            'missing_past_fields': packet['data_quality']['missing_past_fields'],
            'limitations': ['No forecast, trade or subjective structure verdict.',
                            'Prepared after v1 responses: not a modification or rerun of that trial.']}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--packet', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = derive(json.loads(args.packet.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
