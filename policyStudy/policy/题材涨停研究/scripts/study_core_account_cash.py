"""Fixed-signal cash/lot stress ledger; not marked-to-market NAV or a new strategy."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

D = Decimal
CENT = D('0.01')
INITIAL = D('100000')
WEIGHTS = (D('0.05'), D('0.10'), D('0.20'))
SLIPPAGE = D('0.001')
COMMISSION = D('0.0003')
SELL_TAX = D('0.0005')
MIN_COMMISSION = D('5')
LOT = 100
KNOWN_UNFILLED = {'entry_at_up_limit', 'entry_no_volume', 'entry_gap_above_cap'}
ROOT = Path(__file__).resolve().parents[4]
DEFAULT_INPUT = ROOT / 'outputs/core_reactivation_minute_execution_2026/trades.csv'
DEFAULT_OUTPUT = ROOT / 'outputs/core_reactivation_account_risk'


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def charges(price: Decimal, shares: int, selling: bool = False,
            minimum_commission: Decimal = MIN_COMMISSION) -> dict:
    if not price.is_finite() or price <= 0 or shares <= 0 or shares % LOT:
        raise ValueError('Invalid price or round lot')
    gross = money(price * shares)
    slip = money(gross * SLIPPAGE)
    commission = money(max(minimum_commission, gross * COMMISSION))
    tax = money(gross * SELL_TAX) if selling else D('0')
    cash = gross - slip - commission - tax if selling else gross + slip + commission
    return {'gross': gross, 'slippage': slip, 'commission': commission,
            'tax': tax, 'cash': cash}


def size_shares(price: Decimal, budget: Decimal, minimum_commission: Decimal) -> int:
    if price <= 0 or not price.is_finite() or budget < 0:
        raise ValueError('Invalid sizing input')
    shares = int((budget / (price * LOT)).to_integral_value(rounding=ROUND_DOWN)) * LOT
    while shares > 0 and charges(price, shares, minimum_commission=minimum_commission)['cash'] > budget:
        shares -= LOT
    return shares


def _time(value: str) -> datetime | None:
    return datetime.fromisoformat(value) if value and value.lower() not in {'nan', 'none'} else None


def _price(value: str) -> Decimal | None:
    return D(value) if value and value.lower() not in {'nan', 'none'} else None


def normalize(rows: list[dict], prefix: str) -> list[dict]:
    normalized = []
    seen = set()
    for row in rows:
        signal = _time(row['signal_time'])
        if signal is None:
            raise ValueError('Signal time missing')
        key = f"{row['ts_code']}|{signal.isoformat()}"
        if key in seen:
            raise ValueError('Duplicate stock/signal')
        seen.add(key)
        raw_buyable = str(row[f'{prefix}_buyable']).lower()
        buyable = True if raw_buyable == 'true' else False if raw_buyable == 'false' else None
        entry = _time(row.get(f'{prefix}_entry_execution_at', ''))
        exit_at = _time(row.get(f'{prefix}_exit_execution_at', ''))
        status = row[f'{prefix}_status']
        ep = _price(row.get(f'{prefix}_entry_open', ''))
        xp = _price(row.get(f'{prefix}_exit_open', ''))
        if entry is not None and (entry - signal).total_seconds() < 60:
            raise ValueError('Minute source violates frozen reaction delay')
        if buyable:
            if entry is None or ep is None or not ep.is_finite() or ep <= 0:
                raise ValueError('Filled entry must contain valid time and price')
            if status == 'closed':
                if (exit_at is None or xp is None or not xp.is_finite() or xp <= 0
                        or exit_at.date() <= entry.date()):
                    raise ValueError('Closed source must contain T+1 exit and price')
            elif exit_at is not None:
                raise ValueError('Unresolved source cannot have a known exit')
        elif exit_at is not None:
            raise ValueError('Unfilled source cannot have an exit')
        normalized.append({'id': key, 'ts_code': row['ts_code'],
                           'theme_id': row['theme_id'], 'theme_name': row['theme_name'],
                           'signal': signal, 'entry_at': entry or signal,
                           'entry_price': ep, 'buyable': buyable, 'source_status': status,
                           'exit_at': exit_at, 'exit_price': xp})
    return normalized


def replay(trades: list[dict], weight: Decimal, initial: Decimal = INITIAL,
           minimum_commission: Decimal = MIN_COMMISSION) -> dict:
    """Entry allocation never reads return/exit/MAE or later source status.

    Exit events are realized only at their given timestamps. Unknown entries or
    holdings make the final account result unavailable; their budgets are not
    silently treated as proven available capital.
    """
    if not D('0') < weight <= 1 or initial <= 0 or minimum_commission < 0:
        raise ValueError('Invalid ledger configuration')
    budget = money(initial * weight)
    events = []
    for trade in trades:
        events.append((trade['entry_at'], 1, trade['signal'], trade['ts_code'], trade))
        if trade['exit_at'] is not None:
            events.append((trade['exit_at'], 0, trade['signal'], trade['ts_code'], trade))
    events.sort(key=lambda item: item[:4])
    cash = money(initial)
    active = {}
    records = {}
    timeline = []
    counts = Counter()
    uncertainty = False
    max_positions, max_same_theme = 0, 0
    max_cost, max_theme_cost = D('0'), D('0')
    max_loss_settled_equity = D('0')
    peak_settled_equity = money(initial)
    lowest_cash = cash
    current_stamp = None
    entry_cash_available = cash
    for stamp, kind, _, _, t in events:
        if stamp != current_stamp:
            current_stamp = stamp
            entry_cash_available = cash
        if kind == 0:
            position = active.get(t['ts_code'])
            if position is None or position['id'] != t['id']:
                continue
            sell = charges(t['exit_price'], position['shares'], selling=True,
                           minimum_commission=minimum_commission)
            cash += sell['cash']
            del active[t['ts_code']]
            records[t['id']].update({'status': 'closed', 'exit_at': stamp.isoformat(),
                'exit_price': str(t['exit_price']), 'exit_cash': str(sell['cash']),
                'sell_charges': {k: str(v) for k, v in sell.items()},
                'pnl': str(sell['cash'] - position['cost'])})
            counts['closed'] += 1
            event_status = 'closed'
        else:
            counts['signals'] += 1
            record = {'id': t['id'], 'ts_code': t['ts_code'], 'theme_id': t['theme_id'],
                      'entry_at': stamp.isoformat(), 'source_status': t['source_status'],
                      'decision_uncertain_due_to_prior_gap': uncertainty}
            records[t['id']] = record
            if t['buyable'] is not True:
                if t['buyable'] is False and t['source_status'] in KNOWN_UNFILLED:
                    event_status = 'source_unfilled'
                elif t['buyable'] is False and t['source_status'] == 'excluded_limit_regime':
                    event_status = 'source_excluded'
                else:
                    event_status = 'source_entry_unknown'
                    uncertainty = True
            elif t['ts_code'] in active:
                event_status = 'already_held'
            else:
                shares = size_shares(t['entry_price'], budget, minimum_commission)
                if shares == 0:
                    event_status = 'below_one_lot'
                else:
                    buy = charges(t['entry_price'], shares, minimum_commission=minimum_commission)
                    if buy['cash'] > entry_cash_available:
                        event_status = ('cash_same_time_reuse_blocked'
                                        if buy['cash'] <= cash else 'cash_rejected')
                    else:
                        cash -= buy['cash']
                        entry_cash_available -= buy['cash']
                        active[t['ts_code']] = {'id': t['id'], 'shares': shares,
                            'theme_id': t['theme_id'], 'cost': buy['cash']}
                        record.update({'shares': shares, 'entry_price': str(t['entry_price']),
                            'entry_cash': str(buy['cash']),
                            'buy_charges': {k: str(v) for k, v in buy.items()}})
                        event_status = 'accepted'
            counts[event_status] += 1
            record['status'] = event_status
        assert cash >= 0
        cost = sum((v['cost'] for v in active.values()), D('0'))
        themes = Counter(v['theme_id'] for v in active.values())
        theme_cost = {}
        for v in active.values():
            theme_cost[v['theme_id']] = theme_cost.get(v['theme_id'], D('0')) + v['cost']
        max_positions = max(max_positions, len(active))
        max_same_theme = max(max_same_theme, max(themes.values(), default=0))
        max_cost = max(max_cost, cost)
        max_theme_cost = max(max_theme_cost, max(theme_cost.values(), default=D('0')))
        lowest_cash = min(lowest_cash, cash)
        # Held stocks are carried at purchase cost, not current market value.
        settled = cash + cost
        peak_settled_equity = max(peak_settled_equity, settled)
        max_loss_settled_equity = max(max_loss_settled_equity, peak_settled_equity - settled)
        timeline.append({'time': stamp.isoformat(), 'event': event_status,
                         'trade_id': t['id'], 'cash': str(cash), 'held_at_cost': str(cost),
                         'settled_profit_equity_not_nav': str(settled), 'positions': len(active)})
    for position in active.values():
        records[position['id']]['status'] = 'holding_unresolved'
    complete = not active and not uncertainty
    closed = [r for r in records.values() if r['status'] == 'closed']
    return {'initial_cash': str(initial), 'fixed_initial_fraction': str(weight),
            'per_entry_total_cost_budget': str(budget), 'minimum_commission': str(minimum_commission),
            'counts': dict(counts), 'unknown_entry_present': uncertainty,
            'unresolved_holdings': len(active), 'complete': complete,
            'final_cash_if_complete': str(cash) if complete else None,
            'total_return_pct_if_complete': float((cash / initial - 1) * 100) if complete else None,
            'max_positions': max_positions, 'max_same_theme_positions': max_same_theme,
            'max_cost_used': str(max_cost), 'max_theme_cost_used': str(max_theme_cost),
            'lowest_cash': str(lowest_cash),
            'max_settled_profit_drawdown_yuan_not_nav': str(max_loss_settled_equity),
            'pnl_from_closed_positions': str(sum((D(r['pnl']) for r in closed), D('0'))),
            'records': list(records.values()), 'timeline': timeline}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    protocol = {'schema_version': 'core_account_cash.v1', 'input': str(args.input.resolve()),
                'input_sha256': _hash(args.input), 'script_sha256': _hash(Path(__file__)),
                'initial_cash': str(INITIAL), 'fixed_fractions': list(map(str, WEIGHTS)),
                'minimum_commission_scenarios': ['0', str(MIN_COMMISSION)],
                'lot_size': LOT, 'slippage_each_side': str(SLIPPAGE),
                'commission_rate': str(COMMISSION), 'sell_tax': str(SELL_TAX),
                'modes': ['minute_old', 'minute_carry'], 'timezone': 'Asia/Shanghai',
                'tie_policy': 'exits first for holdings; entries sorted by signal_time then ts_code; buy cash capped at timestamp-start cash excluding same-time sales; no partial sizing to remaining cash',
                'notional_policy': 'fixed fraction of INITIAL capital, all-in buy budget; round cash charges to cents half-up',
                'limits': ['Fixed historical signals; known development data; no rule selection or fresh holdout.',
                           'Execution prices inherited proxies, not proven fills or volume capacity.',
                           'No marked-to-market NAV, drawdown, portfolio covariance or safe position limit.',
                           'Zero-minimum paired case isolates minimum-commission cost; both cases have 100-share lots.',
                           'Same-time exit proceeds cannot fund entries at that timestamp; fills remain proxies.',
                           'Commission/tax/slippage are research stress assumptions, not verified broker charges.',
                           'Unknown source entry/holding prevents complete final account result.']}
    pp = args.output / 'CASH_PROTOCOL.json'
    if pp.exists() and json.loads(pp.read_text()) != protocol:
        raise RuntimeError('Different protocol; use new output directory')
    pp.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + '\n')
    with args.input.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    result = {'protocol_sha256': _hash(pp), 'completed_at': datetime.now(timezone.utc).isoformat(),
              'source_signals': len(rows), 'scenarios': {}}
    for prefix in protocol['modes']:
        trades = normalize(rows, prefix)
        for weight in WEIGHTS:
            for minimum in (D('0'), MIN_COMMISSION):
                key = f'{prefix}_w{weight}_min{minimum}'
                result['scenarios'][key] = replay(trades, weight, minimum_commission=minimum)
    (args.output / 'CASH_REPLAY.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: {x: v[x] for x in ['counts', 'complete', 'total_return_pct_if_complete',
          'max_positions', 'max_same_theme_positions', 'max_cost_used', 'max_theme_cost_used']}
          for k, v in result['scenarios'].items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
