"""Causal daily context and competing intraday pullback entry families.

All price features use raw price * dated factor; volume is never adjusted.
Candidate construction never reads its outcomes. Model labels are separate.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd
from study_ma250_reclaim import prepare as prepare_clock
from study_fifteen_min_ma_coil import rising_run
from study_daily_intraday_pullback import prepare_daily


@dataclass(frozen=True)
class EntryRules:
    family: str = 'base_break'
    volume_multiple: float = 1.5
    max_risk_pct: float = 2.5
    base_bars: int = 8
    base_width_pct: float = 3.
    daily_rise_days: int = 10
    pullback_min_pct: float = 2.
    pullback_max_pct: float = 18.


MODEL_FEATURES = ['daily_trend_strength', 'daily_atr_pct', 'daily_pullback_pct',
                  'daily_volume_ratio', 'daily_near_ma_pct', 'coil_pct', 'compression_ratio',
                  'volume_multiple8', 'same_clock_volume', 'body_pct', 'close_location',
                  'd_ma20', 'd_ma60', 'd_ma120', 'slope20_pct', 'slope60_pct',
                  'flat20_change16_pct', 'flat60_change16_pct', 'flat20_rising16',
                  'supply_balance', 'efficiency16', 'drawdown32_pct', 'ret4_pct',
                  'breakout_hold4', 'theme_known', 'theme_heat', 'theme_width', 'theme_heat_change',
                  'logic_evidence_known']


def universe_snapshot(raw, adj, calendar, basic, as_of='20250630', seed=20261002, count=20):
    """Rank-free stratified draw from a historical close, never future winners.

    Current metadata is only a conservative ST filter/industry diversification.
    This source stores surviving L/P stocks, so survivorship caveat is explicit.
    """
    x = raw.copy(); x.trade_date = x.trade_date.astype(str)
    a = adj.copy(); a.trade_date = a.trade_date.astype(str)
    x = x[x.trade_date.le(as_of)].merge(a[['ts_code', 'trade_date', 'adj_factor']], on=['ts_code', 'trade_date'], validate='one_to_one')
    x = x.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    x['p_close'] = x.close * x.adj_factor
    day_index = {d: i for i, d in enumerate(calendar)}
    x['date_index'] = x.trade_date.map(day_index)
    g = x.groupby('ts_code', sort=False)
    for n in (5, 10, 20, 30, 60, 120):
        x[f'ma{n}'] = g.p_close.transform(lambda z: z.rolling(n).mean())
        if n in (20, 30, 60):
            x[f'rise{n}'] = x.groupby('ts_code', sort=False)[f'ma{n}'].transform(rising_run)
    x['history_span120'] = g.date_index.transform(lambda z: z - z.shift(119))
    x['volatility20'] = g.p_close.transform(lambda z: z.pct_change(fill_method=None).rolling(20).std())
    x['amount20'] = g.amount.transform(lambda z: z.rolling(20).mean()) * 1000  # daily amount is CNY thousands
    q = x[x.trade_date.eq(as_of)].copy()
    q = q.merge(basic[['ts_code', 'name', 'industry', 'list_date']], on='ts_code', how='left')
    q['eligible'] = (q.history_span120.eq(119) & q.ts_code.str.endswith(('.SH', '.SZ'))
                     & ~q.name.fillna('').str.contains('ST|退', case=False)
                     & q.amount20.ge(1e8) & q.close.ge(3) & q.close.le(150)
                     & q.ma5.gt(q.ma10) & q.ma10.gt(q.ma20) & q.ma20.gt(q.ma30)
                     & q.ma30.gt(q.ma60) & q.ma60.gt(q.ma120)
                     & q[['rise20', 'rise30', 'rise60']].max(axis=1).ge(20))
    pool = q[q.eligible].copy()
    if len(pool) < count:
        raise ValueError(f'Only {len(pool)} eligible stocks, cannot draw {count} without changing criteria')
    pool['vol_bucket'] = pd.qcut(pool.volatility20.rank(method='first'), 4, labels=False)
    rng = np.random.default_rng(seed)
    pool['random_draw'] = rng.random(len(pool))
    selected = []; industry_counts = {}
    # Five per volatility bucket, max three stocks per current industry.
    for b in range(4):
        picked = 0
        for _, r in pool[pool.vol_bucket.eq(b)].sort_values('random_draw').iterrows():
            key = str(r.industry)
            if industry_counts.get(key, 0) >= 3:
                continue
            selected.append(r); industry_counts[key] = industry_counts.get(key, 0) + 1; picked += 1
            if picked == count // 4:
                break
    if len(selected) < count:
        used = {r.ts_code for r in selected}
        for _, r in pool[~pool.ts_code.isin(used)].sort_values('random_draw').iterrows():
            if industry_counts.get(str(r.industry), 0) >= 3:
                continue
            selected.append(r); industry_counts[str(r.industry)] = industry_counts.get(str(r.industry), 0) + 1
            if len(selected) == count:
                break
    if len(selected) != count:
        raise ValueError('Industry cap leaves too few eligible observations')
    return pd.DataFrame(selected).reset_index(drop=True), q


def daily_context(raw, adj, calendar):
    f = prepare_daily(raw, adj, calendar)
    f['trend_strength'] = f[['rise20', 'rise30', 'rise60']].max(axis=1).clip(upper=60) / 60
    f['trend_ok'] = (f.ma20.ge(f.ma30 * .99) & f.ma30.ge(f.ma60 * .99)
                      & f.ma60.ge(f.ma120 * .99) & f.close.ge(f.ma60 * .98)
                      & f[['rise20', 'rise30', 'rise60']].max(axis=1).ge(10))
    f['peak20'] = f.high.rolling(20).max()
    f['pullback_pct'] = 100 * (1 - f.close / f.peak20)
    f['volume_ratio'] = f.raw_vol.rolling(5).mean() / f.raw_vol.shift(5).rolling(20).mean()
    f['near_ma_pct'] = pd.concat([100 * (f.close / f[f'ma{n}'] - 1).abs() for n in (20, 30, 60)], axis=1).min(axis=1)
    return f


def flat_change(close, n, horizon=16):
    """Exact h-step flat-price change, keeping all outgoing historical closes."""
    old_sum = close.shift(n - horizon).rolling(horizon).sum()
    return (horizon * close - old_sum) / n


def make_frame(raw, daily, calendar, theme=None):
    f = prepare_clock(raw, calendar)
    # Unnormalized cumulative adjustment agrees with daily_context.
    f['scale'] = f.adj_factor
    for c in ('open', 'high', 'low', 'close'):
        f[f'p_{c}'] = f[c] * f.scale
    for n in (5, 10, 20, 30, 60, 120, 250):
        f[f'ma{n}'] = f.p_close.rolling(n).mean()
        f[f'rise{n}'] = rising_run(f[f'ma{n}'])
        f[f'd_ma{n}'] = 100 * (f.p_close / f[f'ma{n}'] - 1)
    for n in (20, 60):
        f[f'slope{n}_pct'] = 100 * f[f'ma{n}'].pct_change(fill_method=None)
        f[f'flat{n}_change16_pct'] = 100 * flat_change(f.p_close, n) / f[f'ma{n}']
    # Share of all projected adjacent slopes that are positive, not just final MA value.
    old = pd.concat([f.p_close.shift(20 - h) for h in range(1, 17)], axis=1)
    f['flat20_rising16'] = old.lt(f.p_close, axis=0).mean(axis=1).where(old.notna().all(axis=1))
    f['cross20_60'] = f.ma20.shift().le(f.ma60.shift()) & f.ma20.gt(f.ma60)
    prior = f.p_close.shift()
    tr = pd.concat([f.p_high - f.p_low, (f.p_high - prior).abs(), (f.p_low - prior).abs()], axis=1).max(axis=1)
    f['atr14'] = tr.rolling(14).mean()
    f['body_pct'] = 100 * (f.p_close / f.p_open - 1)
    span = f.p_high - f.p_low
    f['close_location'] = (f.p_close - f.p_low) / span.replace(0, np.nan)
    f['volume_multiple8'] = f.vol / f.vol.shift().rolling(8).mean().replace(0, np.nan)
    prior_slots = pd.concat([f.vol.shift(16 * h) for h in range(1, 6)], axis=1)
    f['same_clock_volume'] = f.vol / prior_slots.median(axis=1).where(prior_slots.notna().all(axis=1)).replace(0, np.nan)
    high, low = f[['ma5', 'ma10', 'ma20', 'ma30']].max(axis=1), f[['ma5', 'ma10', 'ma20', 'ma30']].min(axis=1)
    f['coil_pct'] = 100 * (high / low - 1)
    f['compression_ratio'] = (f.p_high - f.p_low).rolling(8).mean() / (f.p_high - f.p_low).shift(8).rolling(24).mean()
    ups = f.vol.where(f.p_close.ge(f.p_open), 0).rolling(8).sum()
    downs = f.vol.where(f.p_close.lt(f.p_open), 0).rolling(8).sum()
    f['supply_balance'] = (ups - downs) / (ups + downs).replace(0, np.nan)
    f['efficiency16'] = (f.p_close - f.p_close.shift(16)).abs() / f.p_close.diff().abs().rolling(16).sum().replace(0, np.nan)
    f['drawdown32_pct'] = 100 * (1 - f.p_close / f.p_high.rolling(32).max())
    f['ret4_pct'] = 100 * (f.p_close / f.p_close.shift(4) - 1)
    prior_upper = f.p_high.shift().rolling(8).max()
    f['breakout_hold4'] = f.p_close.gt(prior_upper.shift(4)).rolling(4).mean()
    d = daily.set_index('trade_date')
    prev = {calendar[i]: calendar[i - 1] for i in range(1, len(calendar))}
    f['trade_date'] = f.datetime.dt.strftime('%Y%m%d')
    f['daily_as_of'] = f.trade_date.map(prev)
    mapping = {'trend_strength': 'daily_trend_strength', 'trend_ok': 'daily_trend_ok',
               'atr14_pct': 'daily_atr_pct', 'pullback_pct': 'daily_pullback_pct',
               'volume_ratio': 'daily_volume_ratio', 'near_ma_pct': 'daily_near_ma_pct'}
    for src, dest in mapping.items():
        f[dest] = f.daily_as_of.map(d[src])
    f['trend_strength'] = f.daily_trend_strength
    for col in ('theme_known', 'theme_heat', 'theme_width', 'theme_heat_change', 'logic_evidence_known'):
        f[col] = 0.
    if theme is not None and len(theme):
        t = theme.drop_duplicates('trade_date').set_index('trade_date')
        for col in ('theme_known', 'theme_heat', 'theme_width', 'theme_heat_change', 'logic_evidence_known'):
            if col in t:
                f[col] = f.trade_date.map(t[col]).fillna(0).astype(float)
    return f


def entry_candidates(f, code, rules=EntryRules(), start='20250701', end='20260930'):
    prev_high = f.p_high.shift().rolling(rules.base_bars).max()
    prev_low = f.p_low.shift().rolling(rules.base_bars).min()
    width = 100 * (prev_high / prev_low - 1)
    common = (f.valid & f.daily_trend_ok.fillna(False) & f.ma120.notna()
              & f.daily_pullback_pct.between(rules.pullback_min_pct, rules.pullback_max_pct)
              & f.daily_near_ma_pct.le(6.) & f.volume_multiple8.ge(rules.volume_multiple)
              & f.same_clock_volume.ge(1.) & f.body_pct.ge(.2) & f.close_location.ge(.6)
              & f.ma5.gt(f.ma10) & f.ma10.gt(f.ma20) & f.slope20_pct.gt(0)
              & f.flat20_change16_pct.gt(0) & f.datetime.ge(pd.Timestamp(start))
              & f.datetime.lt(pd.Timestamp(end) + pd.Timedelta(days=1)))
    if rules.family == 'base_break':
        gate = common & width.le(rules.base_width_pct) & f.compression_ratio.shift().le(.9) & f.p_close.gt(prev_high)
        stop = prev_low
    elif rules.family == 'higher_low':
        first = f.p_low.shift(8).rolling(8).min()
        latter = f.p_low.shift().rolling(6).min()
        gate = common & latter.gt(first * 1.001) & f.p_close.gt(f.p_high.shift().rolling(4).max()) & f.p_close.gt(f.ma30)
        stop = latter
    elif rules.family == 'ma_reclaim':
        crossed = f.p_close.shift().le(f.ma60.shift()) & f.p_close.gt(f.ma60)
        age = f['index'] - f['index'].where(crossed).ffill()
        gate = common & age.between(0, 4) & f.flat60_change16_pct.gt(0) & f.ma20.gt(f.ma30)
        stop = f.p_low.shift().rolling(4).min()
    else:
        raise ValueError('Unknown entry family')
    risk = 100 * (f.p_close / stop - 1)
    gate &= risk.between(.3, rules.max_risk_pct)
    rows = []
    last = -1000
    for i in np.flatnonzero(gate):
        if i - last < 16:  # Each fresh attempt at least a trading-session apart.
            continue
        r = f.iloc[i]; last = i
        score = (30 * r.daily_trend_strength + 20 * min(1, r.volume_multiple8 / 3)
                 + 15 * max(0, 1 - risk.iloc[i] / 3) + 15 * max(0, 1 - width.iloc[i] / 6)
                 + 10 * r.close_location + 10 * min(1, max(0, r.flat20_change16_pct) / 2))
        rows.append(dict(id=f'{code}_{r.datetime:%Y%m%d_%H%M}', ts_code=code, index=int(i),
                         signal_time=str(r.datetime), stop=float(stop.iloc[i]), entry_score=float(score),
                         risk_pct=float(risk.iloc[i]), family=rules.family, rules=asdict(rules),
                         daily_as_of=r.daily_as_of, theme_heat=float(r.theme_heat), theme_width=float(r.theme_width),
                         logic_evidence_known=bool(r.logic_evidence_known)))
    return rows


def probability_samples(f, code, horizon=64, stride=4):
    """Competing barriers on FUTURE CLOSES, separated from entry construction.

    Upper=max(3%,3ATR), lower=max(1.5%,2ATR), all frozen at label origin.
    Censored and missing horizons never silently count as failure. Barrier labels
    are model-training targets, not a claim of executable profit/stop price.
    """
    valid = f[MODEL_FEATURES].replace([np.inf, -np.inf], np.nan).notna().all(axis=1)
    # Exit probability needs advancing as well as pullback states. Restricting
    # training to entry pullbacks would make profitable holdings out of domain.
    eligible = valid & f.daily_trend_ok.fillna(False)
    rows = []
    for i in np.flatnonzero(eligible):
        if i % stride or i + horizon >= len(f):
            continue
        path = f.iloc[i + 1:i + horizon + 1]
        if not path.valid.all():
            continue
        r = f.iloc[i]
        upper = r.p_close * (1 + max(.03, 3 * r.atr14 / r.p_close))
        lower = r.p_close * (1 - max(.015, 2 * r.atr14 / r.p_close))
        up = np.flatnonzero(path.p_close.ge(upper)); dn = np.flatnonzero(path.p_close.le(lower))
        label = 2  # No directional barrier reached within four trading sessions.
        if len(up) and (not len(dn) or up[0] < dn[0]):
            label = 1
        elif len(dn):
            label = 0
        rows.append(dict(ts_code=code, time=str(r.datetime), label_end=str(path.datetime.iloc[-1]), label=label,
                         **{k: float(r[k]) for k in MODEL_FEATURES}))
    return rows
