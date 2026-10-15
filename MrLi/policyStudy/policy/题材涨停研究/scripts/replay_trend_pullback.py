"""Frozen chronological exploration of daily trend / intraday pullback trading.

Run only against the fixed local input snapshot. This creates research artifacts,
never sends orders. Parameter selection uses Jan-Mar; Apr-Sep is evaluated once.
"""
from pathlib import Path
import argparse
import hashlib
import json
import time
from dataclasses import asdict
import numpy as np
import pandas as pd
import joblib
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import log_loss
from trend_pullback_features import daily_context, make_frame, entry_candidates, probability_samples, EntryRules, MODEL_FEATURES
from trend_pullback_theme_context import build_context
from trend_pullback_exit_engine import replay, ExecutionRules


ROOT = Path(__file__).resolve().parents[5]
DEFAULT = ROOT / 'outputs/trend_pullback_multiscale_v1'


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def protocol(out):
    p = out / 'FROZEN_RESEARCH_PROTOCOL.json'
    value = dict(version=1, selection_as_of='20250630', selection_seed=20261002,
                 train=['20250701', '20251130'], calibration=['20251201', '20251231'],
                 validation=['20260101', '20260331'], temporal_test=['20260401', '20260930'],
                 label_horizon_bars=64, purge='Label end strictly before next split; four-session embargo at starts.',
                 entry_grid=[dict(family=f, volume_multiple=v, max_risk_pct=r) for f in
                             ['base_break', 'higher_low', 'ma_reclaim'] for v in [1.5, 2.] for r in [2., 2.5]],
                 policies=['structure', 'adaptive', 'ma30'], probability_models=['constant', 'logistic_sigmoid', 'random_forest_sigmoid'],
                 probability_selection='Minimum multiclass Brier score on independent December calibration; record Jan-Mar diagnostic too.',
                 selection_objective='Jan-Mar: reject unverified cash accounting or any closed trade <-10%; prefer positive net account return, win>=50%, payoff>=2; then highest net account return. If no combination reaches all targets select highest net return among accepted tails and disclose unmet targets.',
                 allocation=dict(initial_cash=300000, single_name_cap=.20, total_cap=.80, account_risk=.005, trial=.5),
                 accounting='Raw execution prices, next physical open, per-lot T+1, 100 shares, fees/slippage, limit-open rejections/delays. Study end is marked, not forcibly sold.',
                 theme='Previous complete KPL day; raw published factual leads before 09:30; technical score five-point bands with theme-heat tie-break; no hindsight catalyst summaries.',
                 limitations=['Survivor metadata and current industry only for pool diversification; not full universe generalization.',
                              'KPL original publication archive unavailable; assume prior-day snapshot available next session.',
                              'Current raw news timestamp availability not independently archived.',
                              'OHLC next-open is a proxy, not order book fill proof.',
                              'Earlier project research has viewed some 2026 months; this is chronological rule-frozen testing, not an untouched blind dataset.'])
    if p.exists():
        if json.loads(p.read_text()) != value:
            raise ValueError('Frozen protocol mismatch: create a new experiment, do not silently retune')
    else:
        write_json(p, value)
    return value


def prepare(out):
    inp = out / 'inputs'
    pool = pd.read_csv(out / 'observation_pool.csv'); codes = pool.ts_code.tolist()
    calendar = pd.read_parquet(inp / 'calendar.parquet').cal_date.astype(str).tolist()
    daily_raw = pd.read_parquet(inp / 'daily.parquet'); adj = pd.read_parquet(inp / 'adj.parquet')
    raw = pd.read_parquet(inp / 'fifteen.parquet')
    for x in [daily_raw, adj]:
        x['trade_date'] = x.trade_date.astype(str)
    print('Building causal theme context', flush=True)
    context = build_context(pd.read_parquet(inp / 'kpl.parquet'), calendar, codes, pd.read_parquet(inp / 'news.parquet'))
    context.to_parquet(out / 'theme_context.parquet', index=False)
    context.to_csv(out / 'theme_context.csv', index=False)
    frames = {}; daily_frames = {}; audits = []; samples = []
    (out / 'frames').mkdir(exist_ok=True); (out / 'daily_frames').mkdir(exist_ok=True)
    for code in codes:
        d = daily_context(daily_raw[daily_raw.ts_code.eq(code)], adj[adj.ts_code.eq(code)], calendar)
        f = make_frame(raw[raw.ts_code.eq(code)], d, calendar, context[context.ts_code.eq(code)])
        frames[code] = f; daily_frames[code] = d
        d.to_parquet(out / 'daily_frames' / f'{code}.parquet', index=False)
        f.to_parquet(out / 'frames' / f'{code}.parquet', index=False)
        samples.extend(probability_samples(f, code))
        inrange = f.datetime.between('2025-07-01', '2026-09-30 23:59:59')
        audits.append(dict(ts_code=code, bars=int(inrange.sum()), valid_bars=int((f.valid & inrange).sum()),
                           missing_bars=int((~f.valid & inrange).sum()), raw_start=str(f[f.valid].datetime.min()),
                           raw_end=str(f[f.valid].datetime.max()),
                           factor_changes=int(f.loc[inrange, 'scale'].dropna().diff().fillna(0).ne(0).sum()),
                           previous_day_only=bool((f.loc[inrange, 'daily_as_of'] < f.loc[inrange, 'trade_date']).all())))
        print('features', code, 'samples', len(samples), flush=True)
    samples = pd.DataFrame(samples); samples.to_parquet(out / 'probability_samples.parquet', index=False)
    pd.DataFrame(audits).to_csv(out / 'DATA_AUDIT.csv', index=False)
    return frames, samples, calendar


def time_masks(samples, calendar):
    t = pd.to_datetime(samples.time); end = pd.to_datetime(samples.label_end)
    def after_gap(start):
        dates = [d for d in calendar if d >= start]
        return pd.Timestamp(dates[4])
    return dict(train=(t.ge('2025-07-01') & end.lt('2025-12-01')),
                calibration=(t.ge(after_gap('20251201')) & end.lt('2026-01-01')),
                validation=(t.ge(after_gap('20260101')) & end.lt('2026-04-01')),
                temporal_test=(t.ge(after_gap('20260401')) & end.lt('2026-10-01')))


def fit_probabilities(out, samples, frames, calendar):
    masks = time_masks(samples, calendar)
    train, cal = samples[masks['train']], samples[masks['calibration']]
    if len(train) < 500 or train.label.nunique() != 3 or len(cal) < 100 or cal.label.nunique() != 3:
        raise ValueError('Insufficient chronological class coverage; no confident probability deployment')
    xtrain, ytrain = train[MODEL_FEATURES], train.label
    xcal, ycal = cal[MODEL_FEATURES], cal.label
    models = {'constant': None}
    raw_models = dict(logistic_sigmoid=make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000, random_state=20261002)),
                      random_forest_sigmoid=RandomForestClassifier(n_estimators=150, max_depth=4, min_samples_leaf=50, random_state=20261002, n_jobs=2))
    prior = (ytrain.value_counts().reindex([0, 1, 2], fill_value=0).values + 1) / (len(ytrain) + 3)
    scores = []
    def evaluate(name, model, subset, split):
        pred = np.tile(prior, (len(subset), 1)) if model is None else model.predict_proba(subset[MODEL_FEATURES])
        true = np.eye(3)[subset.label.values.astype(int)]
        return dict(model=name, split=split, n=len(subset), independent_dates=subset.time.str[:10].nunique(),
                    brier=float(np.mean(np.sum((pred - true) ** 2, axis=1))),
                    logloss=float(log_loss(subset.label, pred, labels=[0, 1, 2])),
                    predicted_fail=float(pred[:, 0].mean()), realized_fail=float((subset.label == 0).mean()),
                    predicted_extend=float(pred[:, 1].mean()), realized_extend=float((subset.label == 1).mean()))
    for name, m in raw_models.items():
        m.fit(xtrain, ytrain)
        model = CalibratedClassifierCV(m, method='sigmoid', cv='prefit').fit(xcal, ycal)
        models[name] = model
    for name, model in models.items():
        for split in ['calibration', 'validation']:
            subset = samples[masks[split]]
            if len(subset): scores.append(evaluate(name, model, subset, split))
    selected = min([s for s in scores if s['split'] == 'calibration'], key=lambda z: z['brier'])['model']
    model = models[selected]
    # Calibration has fitted sigmoids; this ranking is developmental, NOT an independent performance claim.
    pd.DataFrame(scores).to_csv(out / 'MODEL_DEVELOPMENT.csv', index=False)
    write_json(out / 'FROZEN_MODEL.json', dict(selected=selected, features=MODEL_FEATURES, prior=prior.tolist(),
                train_rows=len(train), calibration_rows=len(cal), train_end=str(train.label_end.max()),
                calibration_end=str(cal.label_end.max()), selection_is_developmental=True,
                target='Next 64 completed closes: failure first / extension first / neither, not executable P&L'))
    joblib.dump(dict(model=model, prior=prior, features=MODEL_FEATURES), out / 'probability_model.joblib')
    for code, f in frames.items():
        valid = f[MODEL_FEATURES].replace([np.inf, -np.inf], np.nan).notna().all(axis=1)
        pred = np.tile(prior, (valid.sum(), 1)) if model is None else model.predict_proba(f.loc[valid, MODEL_FEATURES])
        f['p_fail_pred'] = np.nan; f['p_extend_pred'] = np.nan
        f.loc[valid, 'p_fail_pred'] = pred[:, 0]; f.loc[valid, 'p_extend_pred'] = pred[:, 1]
        # Outside the actual training setup is an unsupported state; do not invent confident guidance.
        f['model_supported'] = valid & f.daily_trend_ok.fillna(False)
        f['n_train'] = len(train)
        f.to_parquet(out / 'frames' / f'{code}.parquet', index=False)
    return model, prior, masks, evaluate, scores


def probability(row, state):
    return dict(p_fail=row['p_fail_pred'], p_extend=row['p_extend_pred'], n=int(row['n_train']), supported=bool(row['model_supported']))


def candidates_for(frames, rules, start, end, theme_filter=False):
    candidates = []
    for code, frame in frames.items():
        for c in entry_candidates(frame, code, rules, start, end):
            if theme_filter and not(c['theme_heat'] >= 40 and c['theme_width'] >= 2):
                continue
            c['technical_score'] = c['entry_score']
            c['entry_score'] = np.floor(c['entry_score'] / 5) * 100 + c['theme_heat']
            candidates.append(c)
    return candidates


def truncate(frames, start, end):
    # Keep original frame indices; engine only receives the evaluation clock.
    return {c: f[f.datetime.ge(pd.Timestamp(start)) & f.datetime.lt(pd.Timestamp(end) + pd.Timedelta(days=1))].copy() for c, f in frames.items()}


def enrich_summary(result):
    s = result['summary']; closed = [t for t in result['trades'] if t['closed']]
    r = np.array([t['net_return'] for t in closed], float)
    gain = r[r > 0]; loss = r[r <= 0]
    s.update(mean_trade_return=float(r.mean()) if len(r) else None,
             median_trade_return=float(np.median(r)) if len(r) else None,
             win_rate=float((r > 0).mean()) if len(r) else None,
             payoff_ratio=float(gain.mean() / -loss.mean()) if len(gain) and len(loss) and loss.mean() < 0 else None,
             worst_trade=float(r.min()) if len(r) else None,
             closed_trades=len(closed), open_trades=len(result['trades'])-len(closed))
    return s


def save_replay(out, result, candidates):
    out.mkdir(parents=True, exist_ok=True)
    for key in ['trades', 'ledger', 'decisions', 'issues', 'curve']:
        pd.DataFrame(result[key]).to_csv(out / f'{key}.csv', index=False)
    pd.DataFrame(candidates).to_csv(out / 'candidates.csv', index=False)
    write_json(out / 'SUMMARY.json', enrich_summary(result))


def run(out=DEFAULT, stage='prepare'):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    frozen = protocol(out)
    if stage == 'prepare':
        frames, samples, calendar = prepare(out)
        fit_probabilities(out, samples, frames, calendar)
        return
    pool = pd.read_csv(out / 'observation_pool.csv')
    frames = {code: pd.read_parquet(out / 'frames' / f'{code}.parquet') for code in pool.ts_code}
    lim = pd.read_parquet(out / 'inputs/limits.parquet')
    lim['trade_date'] = lim.trade_date.astype(str)
    limits = {(r.ts_code, r.trade_date): dict(up_limit=r.up_limit, down_limit=r.down_limit) for r in lim.itertuples()}
    if stage == 'development':
        vf = truncate(frames, '20260101', '20260331'); grid = []
        for ri, spec in enumerate(frozen['entry_grid']):
            rules = EntryRules(**spec)
            for theme_filter in [False, True]:
                cs = candidates_for(frames, rules, '20260101', '20260331', theme_filter)
                for policy in ['structure', 'adaptive']:
                    result = replay(vf, cs, limits, probability_fn=probability, policy=policy)
                    s = enrich_summary(result)
                    s.update(grid_id=ri, rules=asdict(rules), theme_filter=theme_filter, policy=policy,
                             candidates_count=len(cs))
                    grid.append(s)
                    print('development', ri, theme_filter, policy, len(cs), s, flush=True)
                    save_replay(out / 'development' / f'g{ri}_{int(theme_filter)}_{policy}', result, cs)
        write_json(out / 'DEVELOPMENT_GRID.json', grid)
        # Complete denominator, explicit no-target-hit fallback, no held-out return is consulted.
        acceptable = [s for s in grid if s['cash_accounting_verified'] and s['closed_trades'] >= 5 and (s['worst_trade'] is None or s['worst_trade'] >= -.10)]
        if not acceptable:
            write_json(out / 'SELECTION_BLOCKED.json', dict(reason='No valid accounting / minimum trade count / accepted loss-tail combination', grid_rows=len(grid)))
            return
        ideal = [s for s in acceptable if s['win_rate'] >= .5 and (s['payoff_ratio'] or 0) >= 2 and s['net_return'] > 0]
        best = max(ideal or acceptable, key=lambda s: s['net_return'])
        write_json(out / 'FROZEN_STRATEGY.json', dict(selected=best, development_targets_met=bool(ideal), selected_before_temporal_test=True,
                   source_hashes={p.name: digest(p) for p in [Path(__file__), Path(__file__).with_name('trend_pullback_features.py'), Path(__file__).with_name('trend_pullback_exit_engine.py'), Path(__file__).with_name('trend_pullback_theme_context.py')]}))
        return
    if stage == 'test':
        selection = json.loads((out / 'FROZEN_STRATEGY.json').read_text())
        for name, expected in selection['source_hashes'].items():
            if digest(Path(__file__).with_name(name)) != expected:
                raise ValueError(f'Frozen source changed: {name}')
        if (out / 'selected/holdout/SUMMARY.json').exists():
            raise ValueError('Temporal test already run; no silent retest/retuning')
        best = selection['selected']; rules = EntryRules(**best['rules'])
        for split, start, end in [('validation', '20260101', '20260331'), ('holdout', '20260401', '20260930')]:
            cs = candidates_for(frames, rules, start, end, best['theme_filter'])
            ff = truncate(frames, start, end)
            for policy in [best['policy']] + [p for p in ['structure', 'adaptive', 'ma30'] if p != best['policy']]:
                result = replay(ff, cs, limits, probability_fn=probability, policy=policy)
                folder = out / 'selected' / split if policy == best['policy'] else out / 'controls' / policy / split
                save_replay(folder, result, cs)
                print(split, policy, enrich_summary(result), flush=True)
        model = joblib.load(out / 'probability_model.joblib')
        samples = pd.read_parquet(out / 'probability_samples.parquet')
        calendar = pd.read_parquet(out / 'inputs/calendar.parquet').cal_date.astype(str).tolist()
        test = samples[time_masks(samples, calendar)['temporal_test']]
        pred = np.tile(model['prior'], (len(test), 1)) if model['model'] is None else model['model'].predict_proba(test[MODEL_FEATURES])
        pd.DataFrame(dict(time=test.time, ts_code=test.ts_code, label=test.label, p_fail=pred[:, 0], p_extend=pred[:, 1])).to_csv(out / 'MODEL_TEMPORAL_TEST.csv', index=False)
        true = np.eye(3)[test.label.astype(int)]
        write_json(out / 'MODEL_TEMPORAL_METRICS.json', dict(n=len(test), dates=test.time.str[:10].nunique(),
                   brier=float(np.mean(np.sum((pred - true)**2, axis=1))), logloss=float(log_loss(test.label, pred, labels=[0,1,2])),
                   note='Overlapping four-session outcomes are not independent observations; probabilities are not calibrated trade returns.'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, default=DEFAULT)
    parser.add_argument('--stage', choices=['prepare', 'development', 'test'], default='prepare')
    args = parser.parse_args(); run(args.output, args.stage)
