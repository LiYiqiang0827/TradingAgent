"""Daily-rebudgeted compounding and synchronized minute-close market NAV.

Same frozen signals and execution quotes; this is not a new entry strategy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from study_core_account_cash import charges, money, normalize, size_shares, KNOWN_UNFILLED

D = Decimal
ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'outputs/core_reactivation_compound_300k_2026'
INPUT = ROOT / 'outputs/core_reactivation_minute_execution_2026/trades.csv'


def minute_closes(day: str) -> list[datetime]:
    midnight = datetime.strptime(day, '%Y%m%d')
    return [midnight + timedelta(minutes=start + i) for start in (571, 781) for i in range(120)]


def drawdown(points: list[dict]) -> dict:
    """Observed peak-to-trough decline on the supplied synchronized samples."""
    peak, max_dd = None, D('0')
    peak_time = worst_peak = trough_time = None
    worst_peak_value = trough_value = None
    for row in points:
        nav = D(str(row['nav']))
        if not nav.is_finite() or nav <= 0:
            raise ValueError('Invalid NAV observation')
        if peak is None or nav > peak:
            peak, peak_time = nav, row['time']
        dd = 1 - nav / peak
        if dd > max_dd:
            max_dd = dd
            worst_peak, trough_time = peak_time, row['time']
            worst_peak_value, trough_value = peak, nav
    recovery_time = None
    if trough_time is not None:
        for row in points:
            if row['time'] > trough_time and D(str(row['nav'])) >= worst_peak_value:
                recovery_time = row['time']
                break
    return {'max_drawdown_pct': float(max_dd * 100), 'peak_time': worst_peak,
            'trough_time': trough_time, 'recovery_time': recovery_time,
            'peak_nav': str(worst_peak_value) if worst_peak_value else None,
            'trough_nav': str(trough_value) if trough_value else None}


def _quote(quotes: dict, code: str, stamp: datetime, factor: D | None = None,
           price_field: str = 'close') -> dict:
    value = quotes.get((code, stamp))
    if value is None:
        raise ValueError(f'Missing quote: {code} {stamp}')
    if value.get('duplicate'):
        raise ValueError(f'Duplicate required quote: {code} {stamp}')
    for key in (price_field, 'adj_factor'):
        if key not in value or not value[key].is_finite() or value[key] <= 0:
            raise ValueError(f'Invalid {key}: {code} {stamp}')
    if factor is not None and abs(value['adj_factor'] / factor - 1) > D('0.00001'):
        raise ValueError(f'Corporate action or factor change requires explicit accounting: {code} {stamp}')
    return value


def replay_compound(trades: list[dict], calendar: list[str], quotes: dict,
                    weight: D, initial: D = D('300000'), minimum_commission: D = D('5')) -> dict:
    if not calendar or sorted(set(calendar)) != calendar or not D('0') < weight <= 1:
        raise ValueError('Invalid calendar or weight')
    if not initial.is_finite() or initial <= 0 or minimum_commission < 0:
        raise ValueError('Invalid capital or fees')
    ids = [t['id'] for t in trades]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate trade identifier')
    events = {}
    for t in trades:
        if t['entry_at'].strftime('%Y%m%d') not in calendar:
            raise ValueError('Entry outside calendar')
        events.setdefault(t['entry_at'], []).append((1, t))
        if t['exit_at'] is not None:
            if t['exit_at'].strftime('%Y%m%d') not in calendar:
                raise ValueError('Exit outside calendar')
            events.setdefault(t['exit_at'], []).append((0, t))
    cash = money(initial)
    previous_nav = cash
    active, records = {}, {}
    counts = Counter()
    first = datetime.strptime(calendar[0], '%Y%m%d').replace(hour=9, minute=30)
    initial_point = {'time': first.isoformat(), 'cash': str(cash), 'market_value': '0',
                     'nav': str(cash), 'positions': 0}
    points, eod, event_log, budgets = [initial_point], [initial_point.copy()], [], []
    max_positions, max_theme_positions = 0, 0
    for day in calendar:
        day_budget = money(previous_nav * weight)
        budgets.append({'day': day, 'previous_close_nav': str(previous_nav),
                        'total_cost_budget_per_entry': str(day_budget)})
        marks = set(minute_closes(day))
        today_events = {s: v for s, v in events.items() if s.strftime('%Y%m%d') == day}
        # No orders at 15:00: that boundary has no next tradable minute open.
        for stamp in today_events:
            clock = stamp.strftime('%H:%M:%S')
            if stamp.second or stamp.microsecond or not ('09:30:00' <= clock < '11:30:00' or '13:00:00' <= clock < '15:00:00'):
                raise ValueError('Execution outside continuous-session open grid')
        for stamp in sorted(marks | set(today_events)):
            # A bar ending at t closes before the next bar's t-open executions.
            if stamp in marks:
                market_value = D('0')
                for code, pos in active.items():
                    q = _quote(quotes, code, stamp, pos['factor'])
                    market_value += q['close'] * pos['shares']
                nav = money(cash + market_value)
                points.append({'time': stamp.isoformat(), 'cash': str(cash),
                    'market_value': str(money(market_value)), 'nav': str(nav),
                    'positions': len(active)})
            available_before_sales = cash
            for kind, t in sorted(today_events.get(stamp, []), key=lambda z: (z[0], z[1]['signal'], z[1]['ts_code'])):
                if kind == 0:
                    pos = active.get(t['ts_code'])
                    if pos is None or pos['id'] != t['id']:
                        continue
                    q = _quote(quotes, t['ts_code'], stamp + timedelta(minutes=1), pos['factor'], 'open')
                    if abs(q['open'] - t['exit_price']) > D('0.000001'):
                        raise ValueError('Frozen exit quote has changed')
                    sale = charges(t['exit_price'], pos['shares'], True, minimum_commission)
                    cash += sale['cash']
                    del active[t['ts_code']]
                    records[t['id']].update({'status': 'closed', 'exit_at': stamp.isoformat(),
                        'exit_cash': str(sale['cash']), 'exit_price': str(t['exit_price']),
                        'sell_charges': {k: str(v) for k, v in sale.items()},
                        'pnl': str(sale['cash'] - pos['cost'])})
                    counts['closed'] += 1
                    status = 'closed'
                else:
                    counts['signals'] += 1
                    record = {'id': t['id'], 'ts_code': t['ts_code'], 'theme_id': t['theme_id'],
                              'entry_at': stamp.isoformat(), 'day_budget': str(day_budget),
                              'source_status': t['source_status']}
                    records[t['id']] = record
                    if t['buyable'] is not True:
                        if t['buyable'] is False and t['source_status'] in KNOWN_UNFILLED:
                            status = 'source_unfilled'
                        elif t['buyable'] is False and t['source_status'] == 'excluded_limit_regime':
                            status = 'source_excluded'
                        else:
                            raise ValueError('Unknown entry prevents a complete NAV path')
                    elif t['ts_code'] in active:
                        status = 'already_held'
                    else:
                        quantity = size_shares(t['entry_price'], day_budget, minimum_commission)
                        if quantity == 0:
                            status = 'below_one_lot'
                        else:
                            buy = charges(t['entry_price'], quantity, minimum_commission=minimum_commission)
                            if buy['cash'] > available_before_sales:
                                status = 'cash_same_time_reuse_blocked' if buy['cash'] <= cash else 'cash_rejected'
                            else:
                                q = _quote(quotes, t['ts_code'], stamp + timedelta(minutes=1), price_field='open')
                                if abs(q['open'] - t['entry_price']) > D('0.000001'):
                                    raise ValueError('Frozen entry quote has changed')
                                cash -= buy['cash']
                                available_before_sales -= buy['cash']
                                active[t['ts_code']] = {'id': t['id'], 'shares': quantity,
                                    'theme_id': t['theme_id'], 'factor': q['adj_factor'], 'cost': buy['cash']}
                                record.update({'shares': quantity, 'entry_price': str(t['entry_price']),
                                    'entry_cash': str(buy['cash']),
                                    'buy_charges': {k: str(v) for k, v in buy.items()}})
                                status = 'accepted'
                    counts[status] += 1
                    record['status'] = status
                if cash < 0:
                    raise ValueError('Cash overdrawn')
                max_positions = max(max_positions, len(active))
                max_theme_positions = max(max_theme_positions,
                    max(Counter(p['theme_id'] for p in active.values()).values(), default=0))
                event_log.append({'time': stamp.isoformat(), 'id': t['id'], 'event': status,
                    'cash': str(cash), 'positions': len(active),
                    'remaining_timestamp_start_cash': str(available_before_sales)})
        previous_nav = D(points[-1]['nav'])
        if points[-1]['time'] != datetime.strptime(day, '%Y%m%d').replace(hour=15).isoformat():
            raise ValueError('Daily close is not observed')
        eod.append(points[-1].copy())
    if active:
        raise ValueError('Unresolved holding: do not silently liquidate or publish complete NAV')
    if counts['signals'] != len(trades):
        raise ValueError('Signal denominator lost')
    total_pnl = sum((D(r['pnl']) for r in records.values() if r['status'] == 'closed'), D('0'))
    if cash != initial + total_pnl or D(points[-1]['nav']) != cash:
        raise ValueError('Cash reconciliation failure')
    return {'initial_capital': str(initial), 'weight': str(weight), 'counts': dict(counts),
        'final_capital': str(cash), 'total_return_pct': float((cash / initial - 1) * 100),
        'minute_drawdown': drawdown(points), 'daily_drawdown': drawdown(eod),
        'max_positions': max_positions, 'max_same_theme_positions': max_theme_positions,
        'records': list(records.values()), 'events': event_log, 'budgets': budgets,
        'minute_nav': points, 'daily_nav': eod}


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_quotes(path: Path) -> dict:
    import pandas as pd
    data = pd.read_parquet(path)
    def number(value):
        # Preserve unavailable prices; only check them when their clock/field is needed.
        from decimal import InvalidOperation
        try:
            return D(str(value))
        except (InvalidOperation, ValueError):
            return D('NaN')
    quotes = {}
    for r in data.itertuples():
        key = (str(r.ts_code), pd.Timestamp(r.datetime).to_pydatetime())
        quotes[key] = ({'duplicate': True} if key in quotes else
                       {k: number(getattr(r, k)) for k in ['open', 'close', 'adj_factor']})
    return quotes


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--input', type=Path, default=INPUT)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    market = args.output / 'market'
    weights = [D('0.05'), D('0.10'), D('0.20')]
    protocol = {'version': 'core_compound_nav.v1', 'initial_capital': '300000',
        'start': '20260601', 'end': '20260924', 'weights': list(map(str, weights)),
        'modes': ['minute_old', 'minute_carry'],
        'rebudget': 'each day, fraction of previous session closing cash plus raw-price marked holdings',
        'nav_clock': 'all scheduled minute closes, before executions opening the next minute at that boundary',
        'max_drawdown': '1 - synchronized close NAV / running maximum including initial capital; daily close also reported',
        'costs': {'slippage_each_side': '0.001', 'commission_each_side': '0.0003', 'min_commission_each_side': '5', 'sell_tax': '0.0005'},
        'lot': 100, 'same_time_sales_reuse': False, 'liquidation_costs_on_unrealized_holdings': False,
        'source': str(args.input.resolve()), 'source_sha256': _hash(args.input),
        'code_sha256': _hash(__file__),
        'cash_helper_sha256': _hash(Path(__file__).with_name('study_core_account_cash.py')),
        'market_sha256': {name: _hash(market/name) for name in ['one_min.parquet', 'calendar.json', 'day.parquet', 'MANIFEST.json']},
        'limits': ['Known development sample, unchanged setup and exit rules; not a new holdout.',
                   'No live fills, capacity or queue model; order size uses frozen execution quote.',
                   'Minute-close drawdown is not tick or continuous intraminute worst drawdown.',
                   'Realized fees are included; future liquidation fees of open holdings are not accrued.',
                   'No cash interest or dividends; any holding factor change fails until explicitly accounted.',
                   'Historical per-stock source lineage remains incomplete.']}
    pp = args.output/'PROTOCOL.json'
    if pp.exists() and json.loads(pp.read_text()) != protocol:
        raise ValueError('New protocol requires new directory')
    if (args.output/'RESULTS.json').exists():
        raise ValueError('Completed result exists; do not overwrite')
    pp.write_text(json.dumps(protocol, ensure_ascii=False, indent=2)+'\n')
    with args.input.open() as handle:
        rows = list(csv.DictReader(handle))
    calendar = json.loads((market/'calendar.json').read_text())
    quotes = load_quotes(market/'one_min.parquet')
    result = {'protocol_sha256': _hash(pp), 'completed_at': datetime.now(timezone.utc).isoformat(), 'scenarios': {}}
    import pandas as pd
    for mode in protocol['modes']:
        trades = normalize(rows, mode)
        for weight in weights:
            key = f'{mode}_w{weight}'
            replay = replay_compound(trades, calendar, quotes, weight)
            for freq in ['minute', 'daily']:
                pd.DataFrame(replay.pop(f'{freq}_nav')).to_csv(args.output/f'{key}_{freq}_nav.csv', index=False)
            result['scenarios'][key] = replay
    (args.output/'RESULTS.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: {f:v[f] for f in ['final_capital','total_return_pct','counts','minute_drawdown','daily_drawdown']}
                      for k,v in result['scenarios'].items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
