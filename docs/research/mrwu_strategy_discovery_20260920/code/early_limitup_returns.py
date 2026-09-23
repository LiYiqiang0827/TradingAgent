"""Primary-corrected early-seal event core; retrospective proxies, never orders.

Kimi's draft invented existing helper signatures and omitted adjustment factors;
that draft was rejected. This uses actual tested ex helpers and fail-closed paths.
"""
import numpy as np
import research_exit_policy as ex

HOLDS = (1, 3, 5)
BLOCKED = ('BLOCKED_EXIT_DOWN_LIMIT', 'BLOCKED_EXIT_SLIPPAGE_LIMIT')


def validate_inputs(data, dates, s, d, h):
    shape = data['open'].shape
    if len(shape) != 2 or not shape[0] or not shape[1]:
        raise ValueError('early_empty_shape')
    if any(data[k].shape != shape for k in ex.FIELDS):
        raise ValueError('early_shape_mismatch')
    if len(dates) != shape[0] or str(dates[-1]) > '20260831':
        raise ValueError('early_date_boundary')
    if s != int(s) or d != int(d) or not 0 <= s < shape[1] or not 0 <= d < shape[0] or h not in HOLDS:
        raise ValueError('early_invalid_index_or_hold')


def open_bounds_ok(data, d, s):
    p, lo, hi = (data[k][d, s] for k in ('open', 'down_limit', 'up_limit'))
    return all(ex.positive(x) for x in (p, lo, hi)) and lo <= hi and lo - .011 <= p <= hi + .011


def outcome(data, dates, stock_index, signal_index, hold, cost_name,
            entry_st_status, board_exclusion=''):
    validate_inputs(data, dates, stock_index, signal_index, hold)
    if cost_name not in ex.COSTS:
        raise ValueError('early_invalid_cost')
    s, d = int(stock_index), int(signal_index)
    entry, target = d + 1, d + 1 + hold
    cost = ex.COSTS[cost_name]
    r = dict(state='', signal_index=d, entry_index=entry, target_index=target,
             stock_index=s, hold=hold, cost=cost_name, entered=False, closed=False,
             entry_date='', exit_date='', exit_index=-1, blocked_exit_sessions=0,
             entry_fill=np.nan, exit_fill=np.nan, net_return=np.nan,
             entry_status=entry_st_status, board_exclusion=board_exclusion,
             label='NONEXECUTABLE_FIXED_NOTIONAL_EVENT_PROXY_NOT_ACCOUNT')
    if board_exclusion:
        r['state'] = 'UNSUPPORTED_BOARD_ENTRY'
        return r
    if entry >= len(dates):
        r['state'] = 'RIGHT_CENSORED_ENTRY'
        return r
    r['entry_date'] = dates[entry]
    if not open_bounds_ok(data, entry, s):
        r['state'] = 'UNKNOWN_ENTRY'
        return r
    state, fill = ex.entry_state(data, entry, s, cost, entry_st_status)
    if state != 'ENTERED':
        r['state'] = 'UNKNOWN_ENTRY' if state == 'UNFILLED_ENTRY_PRICE_OR_VOLUME_UNKNOWN' else state
        return r
    r.update(entered=True, entry_fill=float(fill))
    for day in range(target, target + 11):
        if day >= len(dates):
            r['state'] = 'RIGHT_CENSORED_EXIT'
            return r
        if not open_bounds_ok(data, day, s):
            r.update(state='UNKNOWN_EXIT_PATH', unknown_exit_index=day)
            return r
        state, sell = ex.sell_state(data, day, s, cost)
        if state in BLOCKED:
            r['blocked_exit_sessions'] += 1
            continue
        if state != 'SELLABLE_PROXY':
            r.update(state='UNKNOWN_EXIT_PATH', unknown_exit_index=day)
            return r
        if day <= entry:
            raise AssertionError('early_Tplus1_violation')
        r.update(state='CLOSED_PROXY', closed=True, exit_index=day, exit_date=dates[day],
                 exit_fill=float(sell), actual_holding_sessions=day-entry)
        r.update(ex.costed_event(float(fill), float(sell), float(data['adj_factor'][entry, s]),
                                 float(data['adj_factor'][day, s]), cost))
        return r
    r['state'] = 'BLOCKED_EXIT_AT_MAX_DELAY'
    return r


def continuation(data, dates, s, d, h):
    validate_inputs(data, dates, s, d, h)
    s, d = int(s), int(d)
    end = d + h
    r = dict(complete=False, status='', stock_index=s, signal_index=d, h=h,
             end_index=end, ret=np.nan, min_low_rel=np.nan,
             future_upper_limit_close_count=None, initial_consecutive_limit_close_count=None,
             first_break_offset=None, descriptive_only=True)
    if end >= len(dates):
        r['status'] = 'RIGHT_CENSORED_DESCRIPTION'
        return r
    states = []
    for day in range(d, end + 1):
        state = ex.limit_close_state(data, day, s)
        if state is None:
            r.update(status='UNKNOWN_DESCRIPTION_PATH', bad_index=day)
            return r
        if day > d:
            states.append(state)
    base = float(data['close'][d, s] * data['adj_factor'][d, s])
    lows = data['low'][d+1:end+1, s] * data['adj_factor'][d+1:end+1, s]
    final = float(data['close'][end, s] * data['adj_factor'][end, s])
    consecutive = 0
    for state in states:
        if not state:
            break
        consecutive += 1
    r.update(complete=True, status='DESCRIPTIVE_COMPLETE', ret=final/base-1,
             min_low_rel=float(np.min(lows)/base-1),
             future_upper_limit_close_count=int(sum(states)),
             initial_consecutive_limit_close_count=consecutive,
             first_break_offset=consecutive+1 if consecutive < len(states) else None)
    return r
