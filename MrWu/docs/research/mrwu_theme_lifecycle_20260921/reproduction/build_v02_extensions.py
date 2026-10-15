#!/usr/bin/env python3
# Deterministic v0.2 descriptive extensions. Stdlib only. No network, no subprocess, no input mutation.
import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import quote

STUDY_START = '2025-10-30'
STUDY_END = '2026-08-31'
ROLE = {1: 'Dragon1', 2: 'Dragon2', 3: 'Dragon3'}
EPS = 0.000000000001
EXPECTED = {'profile_unique_primary_theme': 137, 'profile_recurrent_union': 80, 'profile_single_wave_multiday': 10, 'episodes_unique_episode_id': 793, 'daily_unique_trade_date_primary_theme': 57605, 'daily_unique_trade_dates': 205, 'roles_unique_episode_id_ts_code': 3018, 'coverage_unique_episode_id': 793, 'coverage_has_freeze_date': 407, 'coverage_complete_top3': 390, 'kpl_unique_trade_date_ts_code': 19716, 'leader_unique_ts_code': 678, 'recurrent_episodes': 736, 'recurrent_freeze_episodes': 386, 'recurrent_themes': 80, 'recurrent_themes_with_two_or_more_freeze_episodes': 54, 'single_wave_multiday_themes': 10, 'single_wave_multiday_with_freeze': 8}
REQ = {'THEME_PROFILE': ['primary_theme', 'is_headline', 'lifecycle_class', 'episode_count'], 'THEME_EPISODES': ['episode_id', 'primary_theme', 'episode_ordinal', 'start_date', 'end_date', 'role_freeze_date', 'calendar_span_days', 'active_day_count', 'displayed_day_count'], 'DAILY_THEME_ALL': ['trade_date', 'primary_theme', 'is_active', 'is_extreme_gap', 'is_fade'], 'EPISODE_STOCK_ROLES_BASE': ['episode_id', 'primary_theme', 'role_freeze_date', 'ts_code', 'name', 'initial_rank', 'initial_role'], 'EPISODE_ROLE_COVERAGE': ['episode_id', 'primary_theme', 'episode_ordinal', 'role_freeze_date', 'has_freeze_date', 'candidate_count', 'leader1_available', 'leader2_available', 'leader3_available', 'coverage_status'], 'KPL_ELIGIBLE_ROWS': ['trade_date', 'ts_code', 'name', 'tag_raw', 'status_raw', 'board_count', 'board_days', 'status_kind', 'is_limit_up', 'is_broken_board', 'lu_desc_raw', 'primary_theme', 'theme_raw', 'fallback_theme', 'theme_fallback_used', 'include_in_height_metrics', 'lu_time_raw', 'bid_amount_raw', 'amount_raw', 'turnover_rate_raw'], 'LEADER_YTD_2026': ['ts_code', 'name', 'is_initial_leader1', 'is_realized_episode_leader', 'themes', 'episode_ids', 'list_date', 'first_return_date', 'last_return_date', 'observed_return_days', 'compounded_ytd_return', 'partial_year', 'list_date_missing']}
FILE_INPUTS = [('THEME_PROFILE', 'theme_profile'), ('THEME_EPISODES', 'episodes'), ('DAILY_THEME_ALL', 'daily_theme_all'), ('EPISODE_STOCK_ROLES_BASE', 'roles'), ('EPISODE_ROLE_COVERAGE', 'role_coverage'), ('KPL_ELIGIBLE_ROWS', 'kpl_eligible'), ('LEADER_YTD_2026', 'leader_ytd')]
DETAIL_FIELDS = ['primary_theme', 'episode_id', 'episode_ordinal', 'start_date', 'end_date', 'role_freeze_date', 'has_freeze_date', 'calendar_span_days', 'active_day_count', 'displayed_day_count', 'segment_start_candidate_count', 'segment_start_top3_complete', 'segment_start_dragon1_ts_code', 'segment_start_dragon1_name', 'segment_start_dragon2_ts_code', 'segment_start_dragon2_name', 'segment_start_dragon3_ts_code', 'segment_start_dragon3_name', 'frozen_dragon1_ts_code', 'frozen_dragon1_name', 'frozen_dragon1_initial_role', 'frozen_dragon2_ts_code', 'frozen_dragon2_name', 'frozen_dragon2_initial_role', 'frozen_dragon3_ts_code', 'frozen_dragon3_name', 'frozen_dragon3_initial_role', 'frozen_top3_complete', 'start_freeze_same_date']
SUMMARY_FIELDS = ['primary_theme', 'episode_count', 'freeze_episode_count', 'segment_start_complete_top3_count', 'later_top3_slot_denominator', 'ever_continuity_count', 'ever_continuity_rate', 'adjacent_continuity_count', 'adjacent_continuity_rate', 'new_dragon_count', 'new_dragon_rate', 'unique_dragon_stock_count', 'persistent_stock_count', 'persistent_stock_rate', 'persistent_always_dragon1', 'persistent_ever_dragon1_moved', 'persistent_only_dragon23', 'persistent_role_paths', 'episode1_start_date', 'episode1_end_date', 'episode1_segment_start_top3', 'episode2_start_date', 'episode2_end_date', 'episode2_segment_start_top3', 'episode3_start_date', 'episode3_end_date', 'episode3_segment_start_top3']
PERF_FIELDS = ['primary_theme', 'prior_episode_id', 'prior_episode_ordinal', 'prior_start_date', 'prior_end_date', 'prior_role', 'prior_ts_code', 'prior_name', 'later_episode_id', 'later_episode_ordinal', 'later_start_date', 'later_end_date', 'return_available', 'valid_return_day_count', 'period_compounded_return_pct', 'max_cumulative_return_pct', 'max_drawdown_pct', 'later_role_available', 'later_top3', 'later_same_role', 'later_role', 'later_dragon1', 'role_transition']
PERF_SUMMARY_FIELDS = ['prior_role', 'return_n', 'period_compounded_return_mean', 'period_compounded_return_median', 'period_compounded_return_p25', 'period_compounded_return_p75', 'positive_return_share', 'max_cumulative_return_median', 'max_drawdown_median', 'later_role_n', 'later_top3_share', 'later_same_role_share', 'later_dragon1_share']
DAILY_FIELDS = ['primary_theme', 'episode_id', 'trade_date', 'tau_trade', 'tau_active', 'is_active', 'is_displayed', 'candidate_count', 'dragon1_ts_code', 'dragon1_name', 'dragon2_ts_code', 'dragon2_name', 'dragon3_ts_code', 'dragon3_name', 't_dragon1_ts_code', 't_dragon1_name', 't_dragon2_ts_code', 't_dragon2_name', 't_dragon3_ts_code', 't_dragon3_name', 't_dragon1_stays_dragon1', 't_dragon1_stays_top3', 't_dragon23_available_slots', 't_dragon23_retained_count', 't_dragon23_promoted_to_dragon1', 'daily_top3_available_slots', 'new_daily_top3_count', 'new_daily_top3', 'role_freeze_date', 'start_equals_freeze', 'freeze_validation_status']
AGG_FIELDS = ['time_axis', 'tau_index', 'episode_denominator', 't_dragon1_stays_dragon1_count', 't_dragon1_stays_dragon1_share', 't_dragon1_stays_top3_count', 't_dragon1_stays_top3_share', 't_dragon23_available_slots', 't_dragon23_retained_count', 't_dragon23_retained_share', 't_dragon23_promoted_episode_count', 't_dragon23_promoted_episode_share', 'daily_top3_available_slots', 'new_daily_top3_count', 'new_daily_top3_share']
CASE_FIELDS = ['primary_theme', 'episode_id', 'start_date', 'end_date', 'role_freeze_date', 'active_day_count', 'displayed_day_count', 'emitted_trading_day_count', 'emitted_active_day_count', 't_top3', 'frozen_top3', 'role_paths']
LEADER_ADD_FIELDS = ['study_start', 'study_end', 'observation_start', 'observation_end', 'valid_day_count', 'missing_ohlc_day_count', 'partial_study_window', 'period_compounded_return_pct', 'ideal_low_to_later_high_pct', 'worst_high_to_later_low_pct', 'ideal_low_date', 'ideal_high_date', 'worst_high_date', 'worst_low_date', 'idealized_daily_bar_path']

def die(msg):
    raise SystemExit('FAIL_CLOSED: %s' % msg)

def expect(cond, msg):
    if not cond:
        die(msg)

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(1048576)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def read_csv(path, required):
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        r = csv.DictReader(f)
        if r.fieldnames is None:
            die('%s has no header' % path)
        missing = [c for c in required if c not in r.fieldnames]
        if missing:
            die('%s missing required columns %s' % (path, missing))
        rows = [dict(x) for x in r]
        return rows, list(r.fieldnames)

def sval(row, col):
    v = row.get(col)
    return '' if v is None else str(v)

def pdate(v, what):
    s = str(v).strip()
    try:
        date.fromisoformat(s)
    except Exception:
        die('invalid date for %s: %s' % (what, s))
    return s

def normalize_db_date(v, what):
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        s = '%s-%s-%s' % (s[0:4], s[4:6], s[6:8])
    return pdate(s, what)

def parse_float(v, what, allow_none=True):
    if v is None or str(v).strip() == '':
        if allow_none:
            return None
        die('missing numeric value for %s' % what)
    try:
        x = float(str(v).strip())
    except Exception:
        die('invalid numeric value for %s: %s' % (what, v))
    if not math.isfinite(x):
        die('non-finite numeric value for %s: %s' % (what, v))
    return x

def parse_int(v, what, allow_none=False):
    x = parse_float(v, what, allow_none=allow_none)
    if x is None:
        return None
    if x != int(x):
        die('invalid integer for %s: %s' % (what, v))
    return int(x)

def parse_bool(v):
    s = '' if v is None else str(v).strip().lower()
    if s in ('1', 'true', 't', 'yes', 'y'):
        return True
    if s in ('0', 'false', 'f', 'no', 'n', ''):
        return False
    die('invalid boolean value: %s' % v)

def b01(v):
    return '1' if v else '0'

def parse_time_seconds(v):
    s = '' if v is None else str(v).strip()
    if not s:
        return None
    try:
        if ':' in s:
            p = s.split(':')
            h = int(p[0]); m = int(p[1]); sec = int(p[2]) if len(p) > 2 else 0
        else:
            d = ''.join(ch for ch in s if ch.isdigit())
            if len(d) >= 6:
                h = int(d[0:2]); m = int(d[2:4]); sec = int(d[4:6])
            elif len(d) >= 4:
                h = int(d[0:2]); m = int(d[2:4]); sec = 0
            else:
                return None
        if h < 0 or h > 23 or m < 0 or m > 59 or sec < 0 or sec > 59:
            return None
        return h * 3600 + m * 60 + sec
    except Exception:
        return None

def check_unique(rows, keyfunc, what):
    seen = set()
    for r in rows:
        k = keyfunc(r)
        if k in seen:
            die('duplicate %s: %s' % (what, k))
        seen.add(k)
    return len(seen)

def fmt_num(x):
    if x is None:
        return ''
    y = float(x)
    if not math.isfinite(y):
        die('non-finite output value')
    if abs(y) < 0.0000000000005:
        y = 0.0
    out = format(y, '.12f').rstrip('0').rstrip('.')
    if out in ('', '-0'):
        return '0'
    return out

def rate(num, den):
    return None if den == 0 else float(num) / float(den)

def fmt_rate(num, den):
    return '' if den == 0 else fmt_num(rate(num, den))

def mean(vals):
    return None if not vals else float(sum(vals)) / float(len(vals))

def quantile(vals, q):
    vals = sorted(float(v) for v in vals)
    n = len(vals)
    if n == 0:
        return None
    if n == 1:
        return vals[0]
    pos = (n - 1) * q
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)

def jnum(x):
    if x is None:
        return None
    y = float(x)
    if abs(y) < 0.0000000000005:
        y = 0.0
    return y

def stat_block(vals, positive=False):
    out = {'n': len(vals), 'mean': jnum(mean(vals)), 'median': jnum(quantile(vals, 0.5)), 'p25': jnum(quantile(vals, 0.25)), 'p75': jnum(quantile(vals, 0.75))}
    if positive:
        out['positive_share'] = jnum(rate(sum(1 for v in vals if v > 0), len(vals)))
    return out

def atomic_write_csv(path, fieldnames, rows):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator=chr(10), extrasaction='raise')
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)

def atomic_write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + chr(10))
    os.replace(tmp, path)

def connect_ro(path):
    ap = Path(path).resolve().as_posix()
    conn = sqlite3.connect('file:' + quote(ap, safe='/:') + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    cols = [r[1] for r in conn.execute('PRAGMA table_info(tbl_cn_day)')]
    need = ['ts_code', 'trade_date', 'high', 'low', 'close', 'pct_chg']
    missing = [c for c in need if c not in cols]
    if missing:
        die('tbl_cn_day missing required columns %s' % missing)
    return conn, cols

def fetch_daily(conn, code, start, end):
    rows = []
    seen = set()
    sample = conn.execute(
        'SELECT trade_date FROM tbl_cn_day WHERE ts_code = ? ORDER BY trade_date LIMIT 1',
        (code,),
    ).fetchone()
    compact_dates = bool(sample and len(str(sample[0]).strip()) == 8 and str(sample[0]).strip().isdigit())
    query_start = start.replace('-', '') if compact_dates else start
    query_end = end.replace('-', '') if compact_dates else end
    q = 'SELECT ts_code, trade_date, high, low, close, pct_chg FROM tbl_cn_day WHERE ts_code = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date'
    for ts, td, high, low, close, pct in conn.execute(q, (code, query_start, query_end)):
        if str(ts) != code:
            die('db returned wrong ts_code')
        d = normalize_db_date(td, 'tbl_cn_day trade_date')
        if d in seen:
            die('duplicate stock-date row in tbl_cn_day: %s %s' % (code, d))
        seen.add(d)
        pct_chg = parse_float(pct, 'tbl_cn_day pct_chg', allow_none=False)
        h = parse_float(high, 'tbl_cn_day high', allow_none=True)
        l = parse_float(low, 'tbl_cn_day low', allow_none=True)
        c = parse_float(close, 'tbl_cn_day close', allow_none=True)
        if c is not None and c == 0:
            die('invalid zero close in tbl_cn_day: %s %s' % (code, d))
        rows.append({'ts_code': code, 'trade_date': d, 'high': h, 'low': l, 'close': c, 'pct_chg': pct_chg})
    return rows

def qualifies(row, theme):
    if sval(row, 'primary_theme') == theme:
        return True
    return sval(row, 'fallback_theme') == theme and parse_bool(row.get('is_broken_board'))

def daily_rank(rows, theme, trade_date):
    cand = [r for r in rows if sval(r, 'trade_date') == trade_date and qualifies(r, theme)]
    def key(r):
        bc = parse_float(r.get('board_count'), 'board_count')
        broken = parse_bool(r.get('is_broken_board'))
        limit_up = parse_bool(r.get('is_limit_up'))
        cat = 1 if broken else (0 if limit_up else 2)
        lt = parse_time_seconds(r.get('lu_time_raw'))
        bid = parse_float(r.get('bid_amount_raw'), 'bid_amount_raw')
        amt = parse_float(r.get('amount_raw'), 'amount_raw')
        return ((0, -bc) if bc is not None else (1, 0.0), cat, (0, lt) if lt is not None else (1, 0), (0, -bid) if bid is not None else (1, 0.0), (0, -amt) if amt is not None else (1, 0.0), sval(r, 'ts_code'))
    return sorted(cand, key=key)

def top_display(top):
    parts = []
    for i in range(3):
        if i < len(top):
            parts.append('%s=%s:%s' % (ROLE[i + 1], sval(top[i], 'ts_code'), sval(top[i], 'name')))
        else:
            parts.append('%s=' % ROLE[i + 1])
    return ';'.join(parts)

def get_displayed(row):
    if row is None:
        return False
    for c in ('is_displayed', 'displayed', 'is_display_day', 'on_display'):
        if c in row:
            return parse_bool(row.get(c))
    return False

def make_episode(e):
    ep = dict(e)
    ep['episode_ordinal_i'] = parse_int(ep.get('episode_ordinal'), 'episode_ordinal')
    ep['start_date'] = pdate(ep.get('start_date'), 'episode start_date')
    ep['end_date'] = pdate(ep.get('end_date'), 'episode end_date')
    if ep['end_date'] < ep['start_date']:
        die('episode end before start: %s' % sval(ep, 'episode_id'))
    rf = sval(ep, 'role_freeze_date').strip()
    ep['role_freeze_date'] = pdate(rf, 'episode role_freeze_date') if rf else ''
    return ep

def ep_key(e):
    return (sval(e, 'primary_theme'), e['episode_ordinal_i'], e['start_date'], sval(e, 'episode_id'))

def build_role_maps(role_rows, coverage_rows):
    role_rank = {}
    for r in role_rows:
        ep = sval(r, 'episode_id')
        rank = parse_int(r.get('initial_rank'), 'initial_rank')
        if rank in (1, 2, 3):
            if (ep, rank) in role_rank:
                die('duplicate frozen role slot: %s %s' % (ep, rank))
            role_rank[(ep, rank)] = r
    by_ep = {}
    for (ep, rank), r in role_rank.items():
        by_ep.setdefault(ep, {})[rank] = sval(r, 'ts_code')
    for ep, m in by_ep.items():
        codes = [m[i] for i in (1, 2, 3) if i in m]
        if len(codes) != len(set(codes)):
            die('duplicate ts_code inside frozen top3: %s' % ep)
    cov = {}
    for c in coverage_rows:
        ep = sval(c, 'episode_id')
        if ep in cov:
            die('duplicate role coverage episode_id: %s' % ep)
        cov[ep] = c
    for ep, c in cov.items():
        if parse_bool(c.get('has_freeze_date')):
            for n in (1, 2, 3):
                av = parse_bool(c.get('leader%d_available' % n))
                has = (ep, n) in role_rank
                if av != has:
                    die('frozen coverage mismatch: %s leader%d_available=%s role_row=%s' % (ep, n, av, has))
    return role_rank, cov

def load_inputs(paths, enforce):
    data = {}
    headers = {}
    meta = []
    for label, key in FILE_INPUTS:
        p = paths[key]
        rows, header = read_csv(p, REQ[label])
        data[label] = rows
        headers[label] = header
        meta.append({'name': label, 'path': p, 'sha256_before': sha256_file(p), 'row_count': len(rows), 'schema_required': list(REQ[label]), 'schema_observed': header})
    for label, key in [('RESEARCH_SPEC', 'spec'), ('BASIC_DB', 'basic_db')]:
        p = paths[key]
        meta.append({'name': label, 'path': p, 'sha256_before': sha256_file(p), 'row_count': None, 'schema_required': [], 'schema_observed': []})
    conn, db_cols = connect_ro(paths['basic_db'])
    for m in meta:
        if m['name'] == 'BASIC_DB':
            m['schema_required'] = ['ts_code', 'trade_date', 'high', 'low', 'close', 'pct_chg']
            m['schema_observed'] = db_cols
    profile = data['THEME_PROFILE']; episodes = data['THEME_EPISODES']; daily = data['DAILY_THEME_ALL']; roles = data['EPISODE_STOCK_ROLES_BASE']; cov = data['EPISODE_ROLE_COVERAGE']; kpl = data['KPL_ELIGIBLE_ROWS']; leader = data['LEADER_YTD_2026']
    check_unique(profile, lambda r: sval(r, 'primary_theme'), 'THEME_PROFILE primary_theme')
    check_unique(episodes, lambda r: sval(r, 'episode_id'), 'THEME_EPISODES episode_id')
    check_unique(daily, lambda r: (pdate(r.get('trade_date'), 'DAILY_THEME_ALL trade_date'), sval(r, 'primary_theme')), 'DAILY_THEME_ALL trade_date+primary_theme')
    check_unique(roles, lambda r: (sval(r, 'episode_id'), sval(r, 'ts_code')), 'EPISODE_STOCK_ROLES_BASE episode_id+ts_code')
    check_unique(cov, lambda r: sval(r, 'episode_id'), 'EPISODE_ROLE_COVERAGE episode_id')
    check_unique(kpl, lambda r: (pdate(r.get('trade_date'), 'KPL_ELIGIBLE_ROWS trade_date'), sval(r, 'ts_code')), 'KPL_ELIGIBLE_ROWS trade_date+ts_code')
    check_unique(leader, lambda r: sval(r, 'ts_code'), 'LEADER_YTD_2026 ts_code')
    eps = [make_episode(e) for e in episodes]
    recurrent = sorted(sval(r, 'primary_theme') for r in profile if sval(r, 'lifecycle_class') == 'recurrent_union')
    single = sorted(sval(r, 'primary_theme') for r in profile if sval(r, 'lifecycle_class') == 'single_wave_multiday')
    by_theme = {}
    for e in sorted(eps, key=ep_key):
        by_theme.setdefault(sval(e, 'primary_theme'), []).append(e)
    rec_set = set(recurrent); single_set = set(single)
    rec_eps = [e for e in eps if sval(e, 'primary_theme') in rec_set]
    rec_freeze = [e for e in rec_eps if e['role_freeze_date']]
    freeze_by_theme = {}
    for e in rec_freeze:
        freeze_by_theme.setdefault(sval(e, 'primary_theme'), 0)
        freeze_by_theme[sval(e, 'primary_theme')] += 1
    single_freeze_themes = sorted(set(sval(e, 'primary_theme') for e in eps if sval(e, 'primary_theme') in single_set and e['role_freeze_date']))
    daily_dates = sorted(set(pdate(r.get('trade_date'), 'DAILY_THEME_ALL trade_date') for r in daily))
    complete_top3 = sum(1 for c in cov if parse_bool(c.get('leader1_available')) and parse_bool(c.get('leader2_available')) and parse_bool(c.get('leader3_available')))
    if enforce:
        expect(len(profile) == EXPECTED['profile_unique_primary_theme'], 'THEME_PROFILE unique primary_theme mismatch')
        expect(len(recurrent) == EXPECTED['profile_recurrent_union'], 'recurrent_union theme count mismatch')
        expect(len(single) == EXPECTED['profile_single_wave_multiday'], 'single_wave_multiday theme count mismatch')
        expect(len(eps) == EXPECTED['episodes_unique_episode_id'], 'THEME_EPISODES unique episode_id mismatch')
        expect(len(daily) == EXPECTED['daily_unique_trade_date_primary_theme'], 'DAILY_THEME_ALL unique trade_date+primary_theme mismatch')
        expect(len(daily_dates) == EXPECTED['daily_unique_trade_dates'], 'DAILY_THEME_ALL trade_date count mismatch')
        expect(len(roles) == EXPECTED['roles_unique_episode_id_ts_code'], 'EPISODE_STOCK_ROLES_BASE unique episode_id+ts_code mismatch')
        expect(len(cov) == EXPECTED['coverage_unique_episode_id'], 'EPISODE_ROLE_COVERAGE unique episode_id mismatch')
        expect(sum(1 for c in cov if parse_bool(c.get('has_freeze_date'))) == EXPECTED['coverage_has_freeze_date'], 'EPISODE_ROLE_COVERAGE has_freeze_date mismatch')
        expect(complete_top3 == EXPECTED['coverage_complete_top3'], 'EPISODE_ROLE_COVERAGE complete top3 mismatch')
        expect(len(kpl) == EXPECTED['kpl_unique_trade_date_ts_code'], 'KPL_ELIGIBLE_ROWS unique trade_date+ts_code mismatch')
        expect(len(leader) == EXPECTED['leader_unique_ts_code'], 'LEADER_YTD_2026 unique ts_code mismatch')
        expect(len(rec_eps) == EXPECTED['recurrent_episodes'], 'recurrent episode count mismatch')
        expect(len(rec_freeze) == EXPECTED['recurrent_freeze_episodes'], 'recurrent freeze episode count mismatch')
        expect(len(rec_set) == EXPECTED['recurrent_themes'], 'recurrent theme count mismatch')
        expect(sum(1 for t, n in freeze_by_theme.items() if n >= 2) == EXPECTED['recurrent_themes_with_two_or_more_freeze_episodes'], 'recurrent themes with two or more freeze episodes mismatch')
        expect(len(single) == EXPECTED['single_wave_multiday_themes'], 'single-wave multiday theme count mismatch')
        expect(len(single_freeze_themes) == EXPECTED['single_wave_multiday_with_freeze'], 'single-wave multiday with freeze mismatch')
        expect(set(sval(c, 'episode_id') for c in cov) == set(sval(e, 'episode_id') for e in eps), 'coverage episode set mismatch')
    kpl_by_date = {}
    for r in kpl:
        kpl_by_date.setdefault(pdate(r.get('trade_date'), 'KPL_ELIGIBLE_ROWS trade_date'), []).append(r)
    daily_by_key = {}
    for r in daily:
        daily_by_key[(pdate(r.get('trade_date'), 'DAILY_THEME_ALL trade_date'), sval(r, 'primary_theme'))] = r
    role_rank, cov_map = build_role_maps(roles, cov)
    return {'data': data, 'headers': headers, 'meta': meta, 'conn': conn, 'episodes': eps, 'by_theme': by_theme, 'recurrent_themes': recurrent, 'single_themes': single, 'calendar': daily_dates, 'kpl_by_date': kpl_by_date, 'daily_by_key': daily_by_key, 'role_rank': role_rank, 'coverage': cov_map, 'counts': {'complete_top3': complete_top3, 'recurrent_episodes': len(rec_eps), 'recurrent_freeze_episodes': len(rec_freeze), 'single_freeze_themes': len(single_freeze_themes)}}

def segment_top(ctx, theme, d):
    ranked = daily_rank(ctx['kpl_by_date'].get(d, []), theme, d)
    return ranked, ranked[:3]

def frozen_cell(ctx, ep, n, col):
    r = ctx['role_rank'].get((sval(ep, 'episode_id'), n))
    return '' if r is None else sval(r, col)

def build_recurrent(ctx):
    detail_rows = []
    summary_rows = []
    summary_nums = []
    seg = {}
    for theme in ctx['recurrent_themes']:
        eps = ctx['by_theme'].get(theme, [])
        tops = []
        for e in eps:
            ranked, top = segment_top(ctx, theme, e['start_date'])
            tops.append(top)
            seg[(theme, sval(e, 'episode_id'))] = {'episode': e, 'ranked': ranked, 'top': top}
            row = {'primary_theme': theme, 'episode_id': sval(e, 'episode_id'), 'episode_ordinal': str(e['episode_ordinal_i']), 'start_date': e['start_date'], 'end_date': e['end_date'], 'role_freeze_date': e['role_freeze_date'], 'has_freeze_date': b01(bool(e['role_freeze_date'])), 'calendar_span_days': sval(e, 'calendar_span_days'), 'active_day_count': sval(e, 'active_day_count'), 'displayed_day_count': sval(e, 'displayed_day_count'), 'segment_start_candidate_count': str(len(ranked)), 'segment_start_top3_complete': b01(len(top) >= 3), 'start_freeze_same_date': b01(bool(e['role_freeze_date']) and e['role_freeze_date'] == e['start_date'])}
            for i in range(3):
                row['segment_start_dragon%d_ts_code' % (i + 1)] = sval(top[i], 'ts_code') if i < len(top) else ''
                row['segment_start_dragon%d_name' % (i + 1)] = sval(top[i], 'name') if i < len(top) else ''
            if e['role_freeze_date']:
                for n in (1, 2, 3):
                    row['frozen_dragon%d_ts_code' % n] = frozen_cell(ctx, e, n, 'ts_code')
                    row['frozen_dragon%d_name' % n] = frozen_cell(ctx, e, n, 'name')
                    row['frozen_dragon%d_initial_role' % n] = frozen_cell(ctx, e, n, 'initial_role')
                row['frozen_top3_complete'] = b01(all((sval(e, 'episode_id'), n) in ctx['role_rank'] for n in (1, 2, 3)))
            else:
                for n in (1, 2, 3):
                    row['frozen_dragon%d_ts_code' % n] = ''
                    row['frozen_dragon%d_name' % n] = ''
                    row['frozen_dragon%d_initial_role' % n] = ''
                row['frozen_top3_complete'] = ''
            detail_rows.append(row)
        denom = ever = adj = new = 0
        earlier = set(); prev = set()
        appearances = {}
        for idx, e in enumerate(eps):
            top = tops[idx]
            rank_by_code = {sval(r, 'ts_code'): i + 1 for i, r in enumerate(top)}
            for code, rk in rank_by_code.items():
                if code not in appearances:
                    appearances[code] = {'name': sval(top[rk - 1], 'name'), 'roles': ['out'] * len(eps), 'count': 0}
                appearances[code]['roles'][idx] = ROLE[rk]
                appearances[code]['count'] += 1
            if idx > 0:
                for code in rank_by_code:
                    denom += 1
                    if code in earlier:
                        ever += 1
                    else:
                        new += 1
                    if code in prev:
                        adj += 1
            earlier |= set(rank_by_code)
            prev = set(rank_by_code)
        persistent = []
        persistent_always_dragon1 = []
        persistent_ever_dragon1_moved = []
        persistent_only_dragon23 = []
        for code in sorted(appearances):
            a = appearances[code]
            if a['count'] >= 2:
                path = '|'.join('e%d:%s' % (i + 1, a['roles'][i]) for i in range(len(eps)))
                persistent.append('%s:%s=%s' % (code, a['name'], path))
                observed_roles = [role for role in a['roles'] if role != 'out']
                label = '%s:%s' % (code, a['name'])
                if observed_roles and all(role == 'Dragon1' for role in observed_roles):
                    persistent_always_dragon1.append(label)
                elif 'Dragon1' in observed_roles:
                    persistent_ever_dragon1_moved.append(label)
                else:
                    persistent_only_dragon23.append(label)
        unique_count = len(appearances)
        nums = {'primary_theme': theme, 'episode_count': len(eps), 'freeze_episode_count': sum(1 for e in eps if e['role_freeze_date']), 'segment_start_complete_top3_count': sum(1 for t in tops if len(t) >= 3), 'later_top3_slot_denominator': denom, 'ever_continuity_count': ever, 'ever_continuity_rate': rate(ever, denom), 'adjacent_continuity_count': adj, 'adjacent_continuity_rate': rate(adj, denom), 'new_dragon_count': new, 'new_dragon_rate': rate(new, denom), 'unique_dragon_stock_count': unique_count, 'persistent_stock_count': len(persistent), 'persistent_stock_rate': rate(len(persistent), unique_count), 'persistent_always_dragon1_count': len(persistent_always_dragon1), 'persistent_ever_dragon1_moved_count': len(persistent_ever_dragon1_moved), 'persistent_only_dragon23_count': len(persistent_only_dragon23)}
        summary_nums.append(nums)
        row = {'primary_theme': theme, 'episode_count': str(len(eps)), 'freeze_episode_count': str(nums['freeze_episode_count']), 'segment_start_complete_top3_count': str(nums['segment_start_complete_top3_count']), 'later_top3_slot_denominator': str(denom), 'ever_continuity_count': str(ever), 'ever_continuity_rate': fmt_rate(ever, denom), 'adjacent_continuity_count': str(adj), 'adjacent_continuity_rate': fmt_rate(adj, denom), 'new_dragon_count': str(new), 'new_dragon_rate': fmt_rate(new, denom), 'unique_dragon_stock_count': str(unique_count), 'persistent_stock_count': str(len(persistent)), 'persistent_stock_rate': fmt_rate(len(persistent), unique_count), 'persistent_always_dragon1': ';'.join(persistent_always_dragon1), 'persistent_ever_dragon1_moved': ';'.join(persistent_ever_dragon1_moved), 'persistent_only_dragon23': ';'.join(persistent_only_dragon23), 'persistent_role_paths': ';;'.join(persistent)}
        for i in range(3):
            if i < len(eps):
                row['episode%d_start_date' % (i + 1)] = eps[i]['start_date']
                row['episode%d_end_date' % (i + 1)] = eps[i]['end_date']
                row['episode%d_segment_start_top3' % (i + 1)] = top_display(tops[i])
            else:
                row['episode%d_start_date' % (i + 1)] = ''
                row['episode%d_end_date' % (i + 1)] = ''
                row['episode%d_segment_start_top3' % (i + 1)] = ''
        summary_rows.append(row)
    return detail_rows, summary_rows, summary_nums, seg

def compounded_path(rows):
    if not rows:
        return 0, 0, None, None, None
    idx = 1.0; peak = 1.0; max_cum = 0.0; mdd = 0.0
    for r in rows:
        idx *= 1.0 + r['pct_chg'] / 100.0
        if idx > peak:
            peak = idx
        max_cum = (peak - 1.0) * 100.0
        dd = (idx / peak - 1.0) * 100.0
        if dd < mdd:
            mdd = dd
    return 1, len(rows), (idx - 1.0) * 100.0, max_cum, mdd

def build_prior_performance(ctx, seg):
    rows = []
    stats = []
    for theme in ctx['recurrent_themes']:
        eps = ctx['by_theme'].get(theme, [])
        for i in range(len(eps) - 1):
            prior = eps[i]; later = eps[i + 1]
            ptop = seg[(theme, sval(prior, 'episode_id'))]['top']
            ltop = seg[(theme, sval(later, 'episode_id'))]['top']
            lrank = {sval(r, 'ts_code'): n + 1 for n, r in enumerate(ltop)}
            later_available = bool(ltop)
            for n, r in enumerate(ptop, start=1):
                code = sval(r, 'ts_code')
                fetched = fetch_daily(ctx['conn'], code, later['start_date'], later['end_date'])
                av, valid, ret, mfe, mdd = compounded_path(fetched)
                if later_available:
                    lr = lrank.get(code)
                    later_top3 = 1 if lr is not None else 0
                    later_same = 1 if lr == n else 0
                    later_role = ROLE[lr] if lr is not None else ''
                    later_d1 = 1 if lr == 1 else 0
                    trans = '%s->%s' % (ROLE[n], later_role if later_role else 'out')
                else:
                    later_top3 = ''; later_same = ''; later_role = ''; later_d1 = ''; trans = ''
                row = {'primary_theme': theme, 'prior_episode_id': sval(prior, 'episode_id'), 'prior_episode_ordinal': str(prior['episode_ordinal_i']), 'prior_start_date': prior['start_date'], 'prior_end_date': prior['end_date'], 'prior_role': ROLE[n], 'prior_ts_code': code, 'prior_name': sval(r, 'name'), 'later_episode_id': sval(later, 'episode_id'), 'later_episode_ordinal': str(later['episode_ordinal_i']), 'later_start_date': later['start_date'], 'later_end_date': later['end_date'], 'return_available': str(av), 'valid_return_day_count': str(valid), 'period_compounded_return_pct': fmt_num(ret), 'max_cumulative_return_pct': fmt_num(mfe), 'max_drawdown_pct': fmt_num(mdd), 'later_role_available': b01(later_available), 'later_top3': str(later_top3), 'later_same_role': str(later_same), 'later_role': later_role, 'later_dragon1': str(later_d1), 'role_transition': trans}
                rows.append(row)
                stats.append({'prior_role': ROLE[n], 'return_available': av, 'ret': ret, 'mfe': mfe, 'mdd': mdd, 'later_available': later_available, 'later_top3': later_top3, 'later_same_role': later_same, 'later_dragon1': later_d1})
    out = []
    for g in ('Dragon1', 'Dragon2', 'Dragon3', 'all_top3'):
        sub = stats if g == 'all_top3' else [x for x in stats if x['prior_role'] == g]
        rets = [x['ret'] for x in sub if x['return_available'] and x['ret'] is not None]
        mfes = [x['mfe'] for x in sub if x['return_available'] and x['mfe'] is not None]
        mdds = [x['mdd'] for x in sub if x['return_available'] and x['mdd'] is not None]
        lat = [x for x in sub if x['later_available']]
        row = {'prior_role': g, 'return_n': str(len(rets)), 'period_compounded_return_mean': fmt_num(mean(rets)), 'period_compounded_return_median': fmt_num(quantile(rets, 0.5)), 'period_compounded_return_p25': fmt_num(quantile(rets, 0.25)), 'period_compounded_return_p75': fmt_num(quantile(rets, 0.75)), 'positive_return_share': fmt_rate(sum(1 for v in rets if v > 0), len(rets)), 'max_cumulative_return_median': fmt_num(quantile(mfes, 0.5)), 'max_drawdown_median': fmt_num(quantile(mdds, 0.5)), 'later_role_n': str(len(lat)), 'later_top3_share': fmt_rate(sum(1 for x in lat if x['later_top3'] == 1), len(lat)), 'later_same_role_share': fmt_rate(sum(1 for x in lat if x['later_same_role'] == 1), len(lat)), 'later_dragon1_share': fmt_rate(sum(1 for x in lat if x['later_dragon1'] == 1), len(lat))}
        out.append(row)
    return rows, out, stats

def build_single_wave(ctx):
    daily_rows = []
    case_rows = []
    stats = []
    for theme in ctx['single_themes']:
        eps = ctx['by_theme'].get(theme, [])
        for e in eps:
            start = e['start_date']; end = e['end_date']
            if start not in ctx['calendar']:
                die('single-wave episode start is not a trading date: %s' % sval(e, 'episode_id'))
            dates = [d for d in ctx['calendar'] if start <= d <= end]
            t_ranked, t_top = segment_top(ctx, theme, start)
            t_codes = set(sval(r, 'ts_code') for r in t_top)
            t_d1 = sval(t_top[0], 'ts_code') if t_top else ''
            t23 = [sval(t_top[i], 'ts_code') for i in (1, 2) if i < len(t_top)]
            if e['role_freeze_date'] and e['role_freeze_date'] == start:
                for n in (1, 2, 3):
                    f = frozen_cell(ctx, e, n, 'ts_code')
                    t = sval(t_top[n - 1], 'ts_code') if n - 1 < len(t_top) else ''
                    if f != t:
                        die('single-wave T ranking does not match frozen roles: %s rank%d' % (sval(e, 'episode_id'), n))
                fstatus = 'ok'
            else:
                fstatus = 'not_applicable'
            union = {}
            for r in t_top:
                union.setdefault(sval(r, 'ts_code'), sval(r, 'name'))
            day_records = []
            active_idx = 0
            for tau, d in enumerate(dates):
                drow = ctx['daily_by_key'].get((d, theme))
                active = parse_bool(drow.get('is_active')) if drow is not None else False
                disp = get_displayed(drow)
                tau_active = active_idx if active else None
                if active:
                    active_idx += 1
                ranked, top = segment_top(ctx, theme, d)
                codes = [sval(r, 'ts_code') for r in top]
                for r in top:
                    union.setdefault(sval(r, 'ts_code'), sval(r, 'name'))
                cur_d1 = codes[0] if codes else ''
                stay_d1 = 1 if t_d1 and cur_d1 == t_d1 else 0
                stay_top3 = 1 if t_d1 and t_d1 in codes else 0
                retained = sum(1 for c in t23 if c in codes)
                promoted = 1 if cur_d1 and cur_d1 in t23 else 0
                new_codes = [c for c in codes if c not in t_codes]
                new_names = ';'.join('%s:%s' % (c, union.get(c, '')) for c in new_codes)
                row = {'primary_theme': theme, 'episode_id': sval(e, 'episode_id'), 'trade_date': d, 'tau_trade': str(tau), 'tau_active': '' if tau_active is None else str(tau_active), 'is_active': b01(active), 'is_displayed': b01(disp), 'candidate_count': str(len(ranked)), 't_dragon1_ts_code': t_d1, 't_dragon1_name': sval(t_top[0], 'name') if t_top else '', 't_dragon2_ts_code': sval(t_top[1], 'ts_code') if len(t_top) > 1 else '', 't_dragon2_name': sval(t_top[1], 'name') if len(t_top) > 1 else '', 't_dragon3_ts_code': sval(t_top[2], 'ts_code') if len(t_top) > 2 else '', 't_dragon3_name': sval(t_top[2], 'name') if len(t_top) > 2 else '', 't_dragon1_stays_dragon1': str(stay_d1), 't_dragon1_stays_top3': str(stay_top3), 't_dragon23_available_slots': str(len(t23)), 't_dragon23_retained_count': str(retained), 't_dragon23_promoted_to_dragon1': str(promoted), 'daily_top3_available_slots': str(len(top)), 'new_daily_top3_count': str(len(new_codes)), 'new_daily_top3': new_names, 'role_freeze_date': e['role_freeze_date'], 'start_equals_freeze': b01(bool(e['role_freeze_date']) and e['role_freeze_date'] == start), 'freeze_validation_status': fstatus}
                for i in range(3):
                    row['dragon%d_ts_code' % (i + 1)] = codes[i] if i < len(codes) else ''
                    row['dragon%d_name' % (i + 1)] = sval(top[i], 'name') if i < len(top) else ''
                daily_rows.append(row)
                day_records.append((tau, {sval(r, 'ts_code'): n + 1 for n, r in enumerate(top)}))
                stats.append({'episode_id': sval(e, 'episode_id'), 'tau_trade': tau, 'tau_active': tau_active, 'stay_d1': stay_d1, 'stay_top3': stay_top3, 't23_slots': len(t23), 't23_retained': retained, 'promoted': promoted, 'daily_slots': len(top), 'new_count': len(new_codes)})
            paths = []
            for code in sorted(union):
                p = '|'.join('t%d:%s' % (tau, ROLE.get(rank_by.get(code), 'out')) for tau, rank_by in day_records)
                paths.append('%s:%s=%s' % (code, union[code], p))
            frozen = ''
            if e['role_freeze_date']:
                frozen = ';'.join('%s=%s:%s' % (ROLE[n], frozen_cell(ctx, e, n, 'ts_code'), frozen_cell(ctx, e, n, 'name')) for n in (1, 2, 3))
            case_rows.append({'primary_theme': theme, 'episode_id': sval(e, 'episode_id'), 'start_date': start, 'end_date': end, 'role_freeze_date': e['role_freeze_date'], 'active_day_count': sval(e, 'active_day_count'), 'displayed_day_count': sval(e, 'displayed_day_count'), 'emitted_trading_day_count': str(len(dates)), 'emitted_active_day_count': str(active_idx), 't_top3': top_display(t_top), 'frozen_top3': frozen, 'role_paths': ';;'.join(paths)})
    agg = []
    for axis in ('tau_trade', 'tau_active'):
        vals = sorted(set(x[axis] for x in stats if x[axis] is not None))
        for idx in vals:
            sub = [x for x in stats if x[axis] == idx]
            den = len(sub)
            t23_slots = sum(x['t23_slots'] for x in sub)
            t23_ret = sum(x['t23_retained'] for x in sub)
            daily_slots = sum(x['daily_slots'] for x in sub)
            new_count = sum(x['new_count'] for x in sub)
            agg.append({'time_axis': axis, 'tau_index': str(idx), 'episode_denominator': str(den), 't_dragon1_stays_dragon1_count': str(sum(x['stay_d1'] for x in sub)), 't_dragon1_stays_dragon1_share': fmt_rate(sum(x['stay_d1'] for x in sub), den), 't_dragon1_stays_top3_count': str(sum(x['stay_top3'] for x in sub)), 't_dragon1_stays_top3_share': fmt_rate(sum(x['stay_top3'] for x in sub), den), 't_dragon23_available_slots': str(t23_slots), 't_dragon23_retained_count': str(t23_ret), 't_dragon23_retained_share': fmt_rate(t23_ret, t23_slots), 't_dragon23_promoted_episode_count': str(sum(x['promoted'] for x in sub)), 't_dragon23_promoted_episode_share': fmt_rate(sum(x['promoted'] for x in sub), den), 'daily_top3_available_slots': str(daily_slots), 'new_daily_top3_count': str(new_count), 'new_daily_top3_share': fmt_rate(new_count, daily_slots)})
    return daily_rows, agg, case_rows, stats

def build_leader_extended(ctx):
    src = ctx['data']['LEADER_YTD_2026']
    header = ctx['headers']['LEADER_YTD_2026']
    rows = []
    period = []; ideal = []; worst = []
    no_obs = 0; missing_total = 0; valid_total = 0
    for r in sorted(src, key=lambda x: sval(x, 'ts_code')):
        code = sval(r, 'ts_code')
        fetched = fetch_daily(ctx['conn'], code, STUDY_START, STUDY_END)
        out = {h: r.get(h, '') for h in header}
        out['study_start'] = STUDY_START; out['study_end'] = STUDY_END
        out['idealized_daily_bar_path'] = '1'
        if not fetched:
            no_obs += 1
            out.update({'observation_start': '', 'observation_end': '', 'valid_day_count': '0', 'missing_ohlc_day_count': '0', 'partial_study_window': '1', 'period_compounded_return_pct': '', 'ideal_low_to_later_high_pct': '', 'worst_high_to_later_low_pct': '', 'ideal_low_date': '', 'ideal_high_date': '', 'worst_high_date': '', 'worst_low_date': ''})
        else:
            idx = 1.0; valid = 0; missing = 0
            best = None; worstp = None; min_low = None; min_low_date = ''; max_high = None; max_high_date = ''
            for x in fetched:
                idx *= 1.0 + x['pct_chg'] / 100.0
                valid += 1
                if x['high'] is None or x['low'] is None or x['close'] is None:
                    missing += 1
                    continue
                ah = idx * x['high'] / x['close']; al = idx * x['low'] / x['close']; d = x['trade_date']
                if min_low is None or al < min_low - EPS:
                    min_low = al; min_low_date = d
                cand = ah / min_low - 1.0
                if best is None or cand > best[0] + EPS:
                    best = (cand, min_low_date, d)
                if max_high is None or ah > max_high + EPS:
                    max_high = ah; max_high_date = d
                candw = al / max_high - 1.0
                if worstp is None or candw < worstp[0] - EPS:
                    worstp = (candw, max_high_date, d)
            valid_total += valid; missing_total += missing
            pr = (idx - 1.0) * 100.0
            period.append(pr)
            out['observation_start'] = fetched[0]['trade_date']; out['observation_end'] = fetched[-1]['trade_date']
            out['valid_day_count'] = str(valid); out['missing_ohlc_day_count'] = str(missing)
            out['partial_study_window'] = b01(out['observation_start'] != STUDY_START or out['observation_end'] != STUDY_END)
            out['period_compounded_return_pct'] = fmt_num(pr)
            if best is None:
                out['ideal_low_to_later_high_pct'] = ''; out['ideal_low_date'] = ''; out['ideal_high_date'] = ''
            else:
                ideal.append(best[0] * 100.0); out['ideal_low_to_later_high_pct'] = fmt_num(best[0] * 100.0); out['ideal_low_date'] = best[1]; out['ideal_high_date'] = best[2]
            if worstp is None:
                out['worst_high_to_later_low_pct'] = ''; out['worst_high_date'] = ''; out['worst_low_date'] = ''
            else:
                worst.append(worstp[0] * 100.0); out['worst_high_to_later_low_pct'] = fmt_num(worstp[0] * 100.0); out['worst_high_date'] = worstp[1]; out['worst_low_date'] = worstp[2]
        rows.append(out)
    fields = header + LEADER_ADD_FIELDS
    return rows, fields, {'period': period, 'ideal': ideal, 'worst': worst, 'no_obs': no_obs, 'missing_total': missing_total, 'valid_total': valid_total}

def write_outputs(ctx, outdir):
    os.makedirs(outdir, exist_ok=True)
    detail, summary_rows, summary_nums, seg = build_recurrent(ctx)
    perf, perf_sum, perf_stats = build_prior_performance(ctx, seg)
    daily, agg, case_rows, single_stats = build_single_wave(ctx)
    leader_rows, leader_fields, leader_stats = build_leader_extended(ctx)
    outputs = {}
    def put(name, fields, rows):
        p = os.path.join(outdir, name)
        atomic_write_csv(p, fields, rows)
        outputs[name] = p
    put('RECURRENT_THEME_DRAGON_DETAIL.csv', DETAIL_FIELDS, detail)
    put('RECURRENT_THEME_DRAGON_SUMMARY.csv', SUMMARY_FIELDS, summary_rows)
    put('RECURRENT_PRIOR_DRAGON_LATER_PERFORMANCE.csv', PERF_FIELDS, perf)
    put('RECURRENT_PRIOR_DRAGON_LATER_SUMMARY.csv', PERF_SUMMARY_FIELDS, perf_sum)
    put('SINGLE_WAVE_MULTIDAY_DRAGON_DAILY.csv', DAILY_FIELDS, daily)
    put('SINGLE_WAVE_MULTIDAY_DRAGON_AGGREGATE.csv', AGG_FIELDS, agg)
    put('SINGLE_WAVE_MULTIDAY_DRAGON_CASES.csv', CASE_FIELDS, case_rows)
    put('LEADER_RETURNS_EXTENDED.csv', leader_fields, leader_rows)
    total_den = sum(x['later_top3_slot_denominator'] for x in summary_nums)
    total_ever = sum(x['ever_continuity_count'] for x in summary_nums)
    total_adj = sum(x['adjacent_continuity_count'] for x in summary_nums)
    total_new = sum(x['new_dragon_count'] for x in summary_nums)
    group_json = []
    for row in perf_sum:
        group_json.append({'prior_role': row['prior_role'], 'return_n': int(row['return_n']), 'period_compounded_return_mean': jnum(parse_float(row['period_compounded_return_mean'], 'mean') if row['period_compounded_return_mean'] else None), 'period_compounded_return_median': jnum(parse_float(row['period_compounded_return_median'], 'median') if row['period_compounded_return_median'] else None), 'period_compounded_return_p25': jnum(parse_float(row['period_compounded_return_p25'], 'p25') if row['period_compounded_return_p25'] else None), 'period_compounded_return_p75': jnum(parse_float(row['period_compounded_return_p75'], 'p75') if row['period_compounded_return_p75'] else None), 'positive_return_share': jnum(parse_float(row['positive_return_share'], 'positive') if row['positive_return_share'] else None), 'max_cumulative_return_median': jnum(parse_float(row['max_cumulative_return_median'], 'mfe') if row['max_cumulative_return_median'] else None), 'max_drawdown_median': jnum(parse_float(row['max_drawdown_median'], 'mdd') if row['max_drawdown_median'] else None), 'later_role_n': int(row['later_role_n']), 'later_top3_share': jnum(parse_float(row['later_top3_share'], 'lt') if row['later_top3_share'] else None), 'later_same_role_share': jnum(parse_float(row['later_same_role_share'], 'ls') if row['later_same_role_share'] else None), 'later_dragon1_share': jnum(parse_float(row['later_dragon1_share'], 'ld') if row['later_dragon1_share'] else None)})
    total_unique_dragons = sum(x['unique_dragon_stock_count'] for x in summary_nums)
    total_persistent_dragons = sum(x['persistent_stock_count'] for x in summary_nums)
    summary = {'coverage': ctx['counts'], 'continuity': {'adjacent_continuity_count': total_adj, 'adjacent_continuity_rate': jnum(rate(total_adj, total_den)), 'ever_continuity_count': total_ever, 'ever_continuity_rate': jnum(rate(total_ever, total_den)), 'later_top3_slot_denominator': total_den, 'new_dragon_count': total_new, 'new_dragon_rate': jnum(rate(total_new, total_den)), 'unique_dragon_stock_count': total_unique_dragons, 'persistent_stock_count': total_persistent_dragons, 'persistent_stock_rate': jnum(rate(total_persistent_dragons, total_unique_dragons)), 'persistent_always_dragon1_count': sum(x['persistent_always_dragon1_count'] for x in summary_nums), 'persistent_ever_dragon1_moved_count': sum(x['persistent_ever_dragon1_moved_count'] for x in summary_nums), 'persistent_only_dragon23_count': sum(x['persistent_only_dragon23_count'] for x in summary_nums), 'recurrent_themes': len(ctx['recurrent_themes'])}, 'leader_returns_extended': {'ideal_low_to_later_high_pct': stat_block(leader_stats['ideal']), 'period_compounded_return_pct': stat_block(leader_stats['period'], positive=True), 'rows': len(leader_rows), 'worst_high_to_later_low_pct': stat_block(leader_stats['worst'])}, 'prior_dragon_later_performance': {'groups': group_json, 'rows': len(perf)}, 'single_wave_multiday': {'aggregate_rows': agg, 'themes': len(ctx['single_themes'])}, 'universe': {'daily_trade_dates': len(ctx['calendar']), 'episodes': len(ctx['episodes']), 'leader_rows': len(ctx['data']['LEADER_YTD_2026']), 'profile_themes': len(ctx['data']['THEME_PROFILE']), 'recurrent_themes': len(ctx['recurrent_themes']), 'single_wave_multiday_themes': len(ctx['single_themes'])}}
    p = os.path.join(outdir, 'SUMMARY_V02.json')
    atomic_write_json(p, summary)
    outputs['SUMMARY_V02.json'] = p
    for m in ctx['meta']:
        m['sha256_after'] = sha256_file(m['path'])
        m['unchanged'] = m['sha256_after'] == m['sha256_before']
        if not m['unchanged']:
            die('source hash changed while running: %s' % m['path'])
    out_hashes = {name: sha256_file(path) for name, path in sorted(outputs.items())}
    dq = {'denominators_exclusions_invariants': {'fallback_theme_qualifies_only_for_broken_board': True, 'frozen_role_uniqueness_and_coverage_checked': True, 'idealized_daily_bar_path_not_achievable_return': True, 'leader_missing_ohlc_day_count_total': leader_stats['missing_total'], 'leader_rows_with_no_study_observations': leader_stats['no_obs'], 'leader_valid_day_count_total': leader_stats['valid_total'], 'no_future_data_used_for_segment_start_or_frozen_roles': True, 'prior_performance_later_role_unavailable_rows': sum(1 for x in perf_stats if not x['later_available']), 'prior_performance_return_unavailable_rows': sum(1 for x in perf_stats if not x['return_available']), 'recurrent_later_top3_slot_denominator': total_den, 'single_wave_daily_rows': len(daily), 'single_wave_never_carries_forward_missing_daily_candidates': True}, 'db_hash': [m for m in ctx['meta'] if m['name'] == 'BASIC_DB'][0]['sha256_before'], 'inputs': ctx['meta'], 'output_hashes': out_hashes, 'source_hashes_unchanged_before_after': all(m['unchanged'] for m in ctx['meta'])}
    atomic_write_json(os.path.join(outdir, 'DATA_QUALITY_V02.json'), dq)
    return outputs

def run_pipeline(args, enforce):
    ctx = load_inputs(args, enforce)
    try:
        return write_outputs(ctx, args['output_dir'])
    finally:
        ctx['conn'].close()

def write_csv(path, header, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator=chr(10))
        w.writerow(header)
        for r in rows:
            w.writerow(r)

def read_out(path):
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def must_fail(fn, needle):
    try:
        fn()
    except SystemExit as e:
        if needle not in str(e):
            raise AssertionError('unexpected failure text: %s' % e)
        return
    raise AssertionError('expected failure containing %s' % needle)

def run_self_test():
    td = tempfile.TemporaryDirectory()
    root = td.name
    d1 = '2026-01-05'; d2 = '2026-01-06'; d3 = '2026-01-07'; d4 = '2026-01-08'
    profile_h = REQ['THEME_PROFILE']
    write_csv(os.path.join(root, 'profile.csv'), profile_h, [['A', '0', 'recurrent_union', '2'], ['B', '0', 'recurrent_union', '1'], ['S', '0', 'single_wave_multiday', '1'], ['S2', '0', 'single_wave_multiday', '1'], ['X', '0', 'headline_only', '1']])
    ep_h = REQ['THEME_EPISODES']
    write_csv(os.path.join(root, 'ep.csv'), ep_h, [['e1', 'A', '1', d1, d2, d1, '2', '2', '2'], ['e2', 'A', '2', d3, d4, '', '2', '2', '2'], ['b1', 'B', '1', d1, d2, '', '2', '1', '1'], ['s1', 'S', '1', d1, d3, d1, '3', '2', '3'], ['u1', 'S2', '1', d1, d2, '', '2', '2', '2'], ['x1', 'X', '1', d1, d1, '', '1', '1', '1']])
    daily_h = REQ['DAILY_THEME_ALL'] + ['is_displayed']
    drows = []
    for d in (d1, d2, d3, d4):
        for t in ('A', 'B', 'S', 'S2', 'X'):
            active = '1'
            if t == 'S' and d == d3:
                active = '0'
            drows.append([d, t, active, '0', '0', '1'])
    write_csv(os.path.join(root, 'daily.csv'), daily_h, drows)
    roles_h = REQ['EPISODE_STOCK_ROLES_BASE']
    write_csv(os.path.join(root, 'roles.csv'), roles_h, [['e1', 'A', d1, 'X', 'x', '1', 'raw1'], ['e1', 'A', d1, 'Y', 'y', '2', 'raw2'], ['e1', 'A', d1, 'Z', 'z', '3', 'raw3'], ['s1', 'S', d1, 'P', 'p', '1', 'raw1'], ['s1', 'S', d1, 'Q', 'q', '2', 'raw2'], ['s1', 'S', d1, 'R', 'r', '3', 'raw3']])
    cov_h = REQ['EPISODE_ROLE_COVERAGE']
    write_csv(os.path.join(root, 'cov.csv'), cov_h, [['e1', 'A', '1', d1, '1', '3', '1', '1', '1', 'complete'], ['e2', 'A', '2', '', '0', '0', '0', '0', '0', 'no_freeze'], ['b1', 'B', '1', '', '0', '0', '0', '0', '0', 'no_freeze'], ['s1', 'S', '1', d1, '1', '3', '1', '1', '1', 'complete'], ['u1', 'S2', '1', '', '0', '0', '0', '0', '0', 'no_freeze'], ['x1', 'X', '1', '', '0', '0', '0', '0', '0', 'no_freeze']])
    kpl_h = REQ['KPL_ELIGIBLE_ROWS']
    def k(d, code, name, bc, lim, broken, tm, bid, amt, theme, fb=''):
        return [d, code, name, 'tag', 'st', str(bc), '1', 'kind', str(lim), str(broken), 'desc', theme, theme, fb, '1' if fb else '0', '1', tm, str(bid), str(amt), '1']
    krows = [k(d1, 'X', 'x', 3, 1, 0, '09:30:00', 100, 1000, 'A'), k(d1, 'Y', 'y', 2, 1, 0, '09:31:00', 90, 900, 'A'), k(d1, 'Z', 'z', 1, 1, 0, '09:32:00', 80, 800, 'A'), k(d3, 'Y', 'y', 3, 1, 0, '09:30:00', 100, 1000, 'A'), k(d3, 'X', 'x', 2, 1, 0, '09:31:00', 90, 900, 'A'), k(d3, 'W', 'w', 1, 1, 0, '09:32:00', 80, 800, 'A'), k(d1, 'M', 'm', 1, 1, 0, '09:30:00', 10, 100, 'B'), k(d1, 'P', 'p', 3, 1, 0, '09:30:00', 100, 1000, 'S'), k(d1, 'Q', 'q', 2, 1, 0, '09:31:00', 90, 900, 'S'), k(d1, 'R', 'r', 1, 1, 0, '09:32:00', 80, 800, 'S'), k(d2, 'Q', 'q', 3, 1, 0, '09:30:00', 100, 1000, 'S'), k(d2, 'P', 'p', 2, 1, 0, '09:31:00', 90, 900, 'S'), k(d2, 'N', 'n', 1, 1, 0, '09:32:00', 80, 800, 'S'), k(d3, 'P', 'p', 1, 1, 0, '09:30:00', 10, 100, 'S'), k(d1, 'U', 'u', 1, 1, 0, '09:30:00', 10, 100, 'S2'), k(d2, 'U', 'u', 2, 1, 0, '09:30:00', 20, 200, 'S2'), k(d2, 'V', 'v', 1, 1, 0, '09:31:00', 10, 100, 'S2')]
    write_csv(os.path.join(root, 'kpl.csv'), kpl_h, krows)
    lead_h = REQ['LEADER_YTD_2026']
    write_csv(os.path.join(root, 'lead.csv'), lead_h, [['L', 'ell', '1', '1', 'A', 'e1', '2020-01-01', '2025-10-30', '2026-08-31', '3', '0', '0', '0']])
    dbp = os.path.join(root, 'basic.db')
    conn = sqlite3.connect(dbp)
    conn.execute('CREATE TABLE tbl_cn_day (ts_code TEXT, trade_date TEXT, high REAL, low REAL, close REAL, pct_chg REAL)')
    rows = [('X', d3, 11, 9, 10, 10), ('X', d4, 10, 8, 9, -10), ('Y', d3, 10, 9, 10, 1), ('Y', d4, 11, 10, 10.1, 1), ('L', '2025-10-30', 110, 90, 100, 0), ('L', '2025-11-02', 132, 99, 110, 10), ('L', '2026-08-31', 120, 80, 99, -10)]
    conn.executemany('INSERT INTO tbl_cn_day VALUES (?,?,?,?,?,?)', rows)
    conn.commit(); conn.close()
    args = {'theme_profile': os.path.join(root, 'profile.csv'), 'episodes': os.path.join(root, 'ep.csv'), 'daily_theme_all': os.path.join(root, 'daily.csv'), 'roles': os.path.join(root, 'roles.csv'), 'role_coverage': os.path.join(root, 'cov.csv'), 'kpl_eligible': os.path.join(root, 'kpl.csv'), 'leader_ytd': os.path.join(root, 'lead.csv'), 'basic_db': dbp, 'spec': os.path.join(root, 'spec.json'), 'output_dir': os.path.join(root, 'out'), 'self_test': True}
    write_csv(args['spec'], ['x'], [['1']])
    run_pipeline(args, enforce=False)
    summ = read_out(os.path.join(args['output_dir'], 'RECURRENT_THEME_DRAGON_SUMMARY.csv'))
    a = [r for r in summ if r['primary_theme'] == 'A'][0]
    assert a['later_top3_slot_denominator'] == '3' and a['ever_continuity_count'] == '2' and a['new_dragon_count'] == '1' and a['adjacent_continuity_count'] == '2' and a['unique_dragon_stock_count'] == '4' and a['persistent_stock_count'] == '2' and a['persistent_stock_rate'] == '0.5'
    assert 'X:x' in a['persistent_ever_dragon1_moved'] and 'Y:y' in a['persistent_ever_dragon1_moved'] and a['persistent_always_dragon1'] == '' and a['persistent_only_dragon23'] == ''
    assert 'X:x=e1:Dragon1|e2:Dragon2' in a['persistent_role_paths'] and 'Y:y=e1:Dragon2|e2:Dragon1' in a['persistent_role_paths']
    detail = read_out(os.path.join(args['output_dir'], 'RECURRENT_THEME_DRAGON_DETAIL.csv'))
    e2 = [r for r in detail if r['episode_id'] == 'e2'][0]
    assert e2['frozen_dragon1_ts_code'] == '' and e2['has_freeze_date'] == '0'
    b = [r for r in detail if r['episode_id'] == 'b1'][0]
    assert b['segment_start_candidate_count'] == '1' and b['segment_start_dragon2_ts_code'] == ''
    perf = read_out(os.path.join(args['output_dir'], 'RECURRENT_PRIOR_DRAGON_LATER_PERFORMANCE.csv'))
    xrow = [r for r in perf if r['prior_ts_code'] == 'X'][0]
    assert xrow['period_compounded_return_pct'] == '-1' and xrow['max_cumulative_return_pct'] == '10' and xrow['max_drawdown_pct'] == '-10' and xrow['later_role'] == 'Dragon2' and xrow['role_transition'] == 'Dragon1->Dragon2'
    zrow = [r for r in perf if r['prior_ts_code'] == 'Z'][0]
    assert zrow['return_available'] == '0'
    sdaily = read_out(os.path.join(args['output_dir'], 'SINGLE_WAVE_MULTIDAY_DRAGON_DAILY.csv'))
    sd2 = [r for r in sdaily if r['primary_theme'] == 'S' and r['trade_date'] == d2][0]
    assert sd2['t_dragon1_stays_dragon1'] == '0' and sd2['t_dragon1_stays_top3'] == '1' and sd2['t_dragon23_promoted_to_dragon1'] == '1' and sd2['new_daily_top3_count'] == '1'
    sd3 = [r for r in sdaily if r['primary_theme'] == 'S' and r['trade_date'] == d3][0]
    assert sd3['tau_active'] == '' and sd3['daily_top3_available_slots'] == '1'
    u0 = [r for r in sdaily if r['primary_theme'] == 'S2' and r['tau_trade'] == '0'][0]
    assert u0['t_dragon23_available_slots'] == '0'
    agg = read_out(os.path.join(args['output_dir'], 'SINGLE_WAVE_MULTIDAY_DRAGON_AGGREGATE.csv'))
    a1 = [r for r in agg if r['time_axis'] == 'tau_trade' and r['tau_index'] == '1'][0]
    assert a1['episode_denominator'] == '2' and a1['new_daily_top3_count'] == '2'
    lead = read_out(os.path.join(args['output_dir'], 'LEADER_RETURNS_EXTENDED.csv'))[0]
    assert lead['ideal_low_date'] == '2026-08-31' and lead['ideal_high_date'] == '2026-08-31' and lead['worst_high_date'] == '2025-11-02' and lead['worst_low_date'] == '2026-08-31' and lead['idealized_daily_bar_path'] == '1'
    assert abs(float(lead['ideal_low_to_later_high_pct']) - 50.0) < 0.0001
    assert abs(float(lead['worst_high_to_later_low_pct']) + 39.3939393939) < 0.0001
    mem = sqlite3.connect(':memory:')
    mem.execute('CREATE TABLE tbl_cn_day (ts_code TEXT, trade_date TEXT, high REAL, low REAL, close REAL, pct_chg REAL)')
    mem.executemany('INSERT INTO tbl_cn_day VALUES (?,?,?,?,?,?)', [('D', d1, 1, 1, 1, 1), ('D', d1, 1, 1, 1, 1)])
    must_fail(lambda: fetch_daily(mem, 'D', d1, d1), 'duplicate stock-date')
    must_fail(lambda: read_csv(os.path.join(root, 'kpl.csv'), ['not_a_column']), 'missing required columns')
    must_fail(lambda: check_unique([{'a': '1'}, {'a': '1'}], lambda r: r['a'], 'self key'), 'duplicate self key')
    td.cleanup()

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--theme-profile', dest='theme_profile')
    p.add_argument('--episodes', dest='episodes')
    p.add_argument('--daily-theme-all', dest='daily_theme_all')
    p.add_argument('--roles', dest='roles')
    p.add_argument('--role-coverage', dest='role_coverage')
    p.add_argument('--kpl-eligible', dest='kpl_eligible')
    p.add_argument('--leader-ytd', dest='leader_ytd')
    p.add_argument('--basic-db', dest='basic_db')
    p.add_argument('--spec', dest='spec')
    p.add_argument('--output-dir', dest='output_dir')
    p.add_argument('--self-test', dest='self_test', action='store_true')
    a = vars(p.parse_args(argv))
    if a['self_test']:
        run_self_test()
        print('SELF_TEST_OK')
        return 0
    need = ['theme_profile', 'episodes', 'daily_theme_all', 'roles', 'role_coverage', 'kpl_eligible', 'leader_ytd', 'basic_db', 'spec', 'output_dir']
    missing = [k for k in need if not a.get(k)]
    if missing:
        die('missing CLI arguments %s' % missing)
    run_pipeline(a, enforce=True)
    print('OK')
    return 0

if __name__ == '__main__':
    sys.exit(main())
