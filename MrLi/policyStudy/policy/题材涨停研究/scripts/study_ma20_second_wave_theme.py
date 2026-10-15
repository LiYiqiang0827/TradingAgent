"""Theme breadth and frozen first-wave companion audit for MA20 retests.

Only KPL primary-attribution limit-up events count as theme membership.
All features are cut off at the touch (or confirmation) close; later peer
performance is stored in explicitly retrospective columns.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import (
    get_day, get_kpl_list, get_theme_daily, get_theme_members,
    get_theme_profile,
)


def market_dates(start: str, end: str) -> list[str]:
    day = get_day(ts_code="000001.SZ", start_date=start, end_date=end,
                  qfq=False, source="database_only")
    return sorted(day.trade_date.astype(str).unique().tolist())


def _labels(value: object) -> set[str]:
    if value is None or pd.isna(value):
        return set()
    return {part.strip() for part in re.split(r"[、，,;/；]", str(value or ""))
            if part.strip()}


def _prefix_theme_features(theme_id: str, anchor: str, first: str,
                           peak: str, as_of: str, calendar: list[str]) -> dict:
    """Query providers only through ``as_of``; freeze companions at peak."""
    first_i = calendar.index(first)
    wave_start = calendar[max(0, first_i - 5)]
    members = get_theme_members(theme_id, as_of=as_of, historical=True)
    members.trade_date = members.trade_date.astype(str)
    wave = members[members.trade_date.between(wave_start, peak)]
    companions = sorted(set(wave.ts_code) - {anchor})
    day_members = set(members.loc[members.trade_date == as_of, "ts_code"])
    old_limit = sorted(day_members & set(companions))
    nonfrozen_limit = sorted(day_members - set(companions) - {anchor})
    peer_day = (get_day(ts_codes=companions, trade_date=as_of, qfq=False,
                        source="database_only") if companions else pd.DataFrame())
    peer_strong7 = (sorted(peer_day.loc[pd.to_numeric(peer_day.pct_chg, errors="coerce") >= 7,
                                       "ts_code"].astype(str).tolist())
                    if not peer_day.empty else [])
    # Daily theme facts are available only for active event dates. A missing
    # row on a covered market date means no primary-theme limits or breaks.
    daily = get_theme_daily(theme_id=theme_id, start_date=wave_start, end_date=as_of)
    daily.trade_date = daily.trade_date.astype(str)
    by_date = {r.trade_date: r for r in daily.itertuples()}
    wave_days = calendar[calendar.index(wave_start):calendar.index(peak)+1]
    wave_widths = [int(by_date[d].limit_up_count) if d in by_date else 0 for d in wave_days]
    current = by_date.get(as_of)
    current_width = int(current.limit_up_count) if current is not None else 0
    after_peak = members[members.trade_date.between(calendar[calendar.index(peak)+1], as_of)] \
        if calendar.index(peak)+1 < len(calendar) and as_of > peak else members.iloc[0:0]
    old_relimit_so_far = sorted(set(after_peak.ts_code) & set(companions))
    return {
        "wave_start": wave_start,
        "frozen_old_peers": len(companions),
        "frozen_old_peer_codes": ",".join(companions),
        "wave_theme_peak_width": max(wave_widths) if wave_widths else 0,
        "wave_theme_peak_day_width": int(by_date[peak].limit_up_count) if peak in by_date else 0,
        "theme_width": current_width,
        "member_width_matches_daily": len(day_members) == current_width,
        "theme_heat": float(current.heat_score) if current is not None else None,
        "theme_height": int(current.max_board_height) if current is not None
                        and pd.notna(current.max_board_height) else 0,
        "theme_episode": str(current.episode_id) if current is not None else "",
        "old_peer_limit": len(old_limit),
        "nonfrozen_peer_limit": len(nonfrozen_limit),
        "old_peer_strong7": len(peer_strong7),
        "old_peer_quoted": len(peer_day),
        "old_peer_relimit_after_peak": len(old_relimit_so_far),
        "old_peer_limit_codes": ",".join(old_limit),
        "old_peer_strong7_codes": ",".join(peer_strong7),
    }


def _restart_codes(kpl: pd.DataFrame, companions: set[str],
                   since: str, end: str, calendar: list[str]) -> set[str]:
    """Old companions returning to a board after two market days off-board."""
    position = {day: i for i, day in enumerate(calendar)}
    eligible = kpl[(kpl.ts_code.isin(companions)) & (kpl.trade_date <= end)]
    restarted = set()
    for code, part in eligible.groupby("ts_code"):
        board_days = set(part.trade_date)
        for day in board_days:
            if day <= since or day not in position or position[day] < 2:
                continue
            i = position[day]
            if calendar[i-1] not in board_days and calendar[i-2] not in board_days:
                restarted.add(code)
                break
    return restarted


def _retrospective_peer_outcome(theme_id: str, companions: set[str],
                                anchor: str, touch: str, end: str,
                                kpl: pd.DataFrame, calendar: list[str]) -> dict:
    members = get_theme_members(theme_id, as_of=end, historical=True)
    members.trade_date = members.trade_date.astype(str)
    future = members[(members.trade_date > touch) & (members.trade_date <= end)]
    primary_returners = set(future.ts_code) & companions
    any_kpl = kpl[(kpl.trade_date > touch) & (kpl.trade_date <= end)]
    any_returners = set(any_kpl.ts_code) & companions
    nonfrozen_returners = set(future.ts_code) - companions - {anchor}
    touch_i = calendar.index(touch)
    end3 = calendar[min(touch_i + 3, len(calendar)-1)]
    future3 = future[future.trade_date <= end3]
    nonfrozen3 = set(future3.ts_code) - companions - {anchor}
    any3 = any_kpl[any_kpl.trade_date <= end3]
    restarted = _restart_codes(kpl, companions, touch, end, calendar)
    restarted3 = _restart_codes(kpl, companions, touch, end3, calendar)
    return {
        "outcome_old_peer_primary_relimit_10d": len(primary_returners),
        "outcome_old_peer_any_relimit_10d": len(any_returners),
        "outcome_nonfrozen_theme_members_limit_10d": len(nonfrozen_returners),
        "outcome_theme_primary_width_peak_next3": int(
            future3.groupby("trade_date").ts_code.nunique().max()) if len(future3) else 0,
        "outcome_old_peer_primary_relimit_next3": len(set(future3.ts_code) & companions),
        "outcome_old_peer_any_relimit_next3": len(set(any3.ts_code) & companions),
        "outcome_nonfrozen_theme_members_limit_next3": len(nonfrozen3),
        "outcome_old_peer_restart_gap2_next3": len(restarted3),
        "outcome_old_peer_restart_gap2_10d": len(restarted),
        "outcome_old_peer_restart_codes_10d": ",".join(sorted(restarted)),
        "outcome_old_peer_any_codes": ",".join(sorted(any_returners)),
    }


def run(audit_path: Path, output: Path) -> dict:
    audit = pd.read_csv(audit_path, dtype={col: str for col in
                        ("first_date", "peak_date", "touch_date", "signal_date")})
    cases = audit[audit.early_trade_status == "closed"].copy()
    calendar = market_dates("20260501", "20260924")
    kpl = get_kpl_list(start_date="20260501", end_date="20260924",
                       tags="涨停", source="database_only")
    kpl.trade_date = kpl.trade_date.astype(str)
    kpl = kpl[~kpl.name.fillna("").str.contains("ST|退", case=False)
              & ~kpl.ts_code.str.endswith(".BJ")].copy()
    lookup = {(r.ts_code, r.trade_date): r for r in kpl.itertuples()}
    rows = []
    for case in cases.itertuples():
        first_event = lookup.get((case.ts_code, case.first_date))
        peak_event = lookup.get((case.ts_code, case.peak_date))
        if first_event is None or peak_event is None:
            rows.append({"ts_code": case.ts_code, "name": case.name,
                         "touch_date": case.touch_date, "status": "anchor_kpl_missing"})
            continue
        first_label = str(first_event.lu_desc or "")
        peak_label = str(peak_event.lu_desc or "")
        aux_labels = sorted(_labels(peak_event.theme) - {peak_label})
        profile = get_theme_profile(name=peak_label, as_of=case.peak_date)
        theme_id = profile.get("theme_id") if profile else None
        if not theme_id:
            rows.append({"ts_code": case.ts_code, "name": case.name,
                         "touch_date": case.touch_date, "status": "theme_unmapped",
                         "first_primary": first_label, "peak_primary": peak_label})
            continue
        touch = _prefix_theme_features(theme_id, case.ts_code, case.first_date,
                                       case.peak_date, case.touch_date, calendar)
        companions = set(filter(None, touch["frozen_old_peer_codes"].split(",")))
        touch_restarted = _restart_codes(kpl, companions, case.peak_date,
                                         case.touch_date, calendar)
        touch_market = kpl[(kpl.trade_date == case.touch_date)
                           & (kpl.ts_code != case.ts_code)]
        aux_touch = {label: sum(label == str(event.lu_desc)
                                or label in _labels(event.theme)
                                for event in touch_market.itertuples())
                     for label in aux_labels}
        touch_i = calendar.index(case.touch_date)
        end = calendar[touch_i + 10] if touch_i + 10 < len(calendar) else ""
        outcome = (_retrospective_peer_outcome(theme_id, companions, case.ts_code,
                                               case.touch_date, end, kpl, calendar)
                   if end else {})
        own_future = kpl[(kpl.ts_code == case.ts_code)
                         & (kpl.trade_date > case.touch_date)
                         & (kpl.trade_date <= end)].sort_values("trade_date") if end else kpl.iloc[0:0]
        second_primary = str(own_future.iloc[0].lu_desc) if len(own_future) else ""
        second_date = str(own_future.iloc[0].trade_date) if len(own_future) else ""
        old_theme_on_second = (get_theme_members(theme_id, as_of=second_date, historical=True)
                               if second_date else pd.DataFrame())
        old_theme_day_codes = (set(old_theme_on_second.loc[
            old_theme_on_second.trade_date.astype(str) == second_date, "ts_code"])
                               if not old_theme_on_second.empty else set())
        relimit_primary_width = 0
        if second_date and second_primary:
            second_profile = get_theme_profile(name=second_primary, as_of=second_date)
            if second_profile and second_profile.get("theme_id"):
                second_daily = get_theme_daily(theme_id=second_profile["theme_id"],
                                               start_date=second_date, end_date=second_date)
                if not second_daily.empty:
                    relimit_primary_width = int(second_daily.iloc[0].limit_up_count)
        result = {
            "ts_code": case.ts_code, "name": case.name,
            "first_date": case.first_date, "peak_date": case.peak_date,
            "touch_date": case.touch_date, "signal_date": case.signal_date,
            "first_primary": first_label, "peak_primary": peak_label,
            "peak_aux_labels": ",".join(aux_labels),
            "touch_aux_max_width": max(aux_touch.values(), default=0),
            "touch_aux_max_label": max(aux_touch, key=aux_touch.get) if aux_touch else "",
            "primary_changed": first_label != peak_label,
            "theme_id": theme_id, "status": "ok",
            "stock_outcome_plus10_10d": bool(case.outcome_max_close_gain_10d_pct >= 10),
            "stock_outcome_max_close_gain_10d_pct": case.outcome_max_close_gain_10d_pct,
            "early_net_return_pct": case.early_net_return_pct,
            "self_relimit_10d": bool(len(own_future)),
            "outcome_self_relimit_date": second_date,
            "self_second_primary": second_primary,
            "self_second_same_primary": bool(second_primary and second_primary == peak_label),
            "outcome_self_original_theme_width": len(old_theme_day_codes),
            "outcome_self_original_old_peer_limit": len(old_theme_day_codes & companions),
            "outcome_self_original_nonfrozen_peer_limit": len(
                old_theme_day_codes - companions - {case.ts_code}),
            "outcome_self_relimit_primary_theme_width": relimit_primary_width,
            **{f"touch_{key}": value for key, value in touch.items()},
            "touch_old_peer_restart_gap2": len(touch_restarted),
            "touch_old_peer_restart_codes": ",".join(sorted(touch_restarted)),
            "outcome_end": end,
            **outcome,
        }
        if isinstance(case.signal_date, str) and case.signal_date <= "20260831":
            signal = _prefix_theme_features(theme_id, case.ts_code, case.first_date,
                                            case.peak_date, case.signal_date, calendar)
            confirm_restarted = _restart_codes(kpl, companions, case.peak_date,
                                               case.signal_date, calendar)
            result.update({f"confirm_{key}": value for key, value in signal.items()
                           if key not in ("wave_start", "frozen_old_peers",
                                          "frozen_old_peer_codes", "wave_theme_peak_width",
                                          "wave_theme_peak_day_width")})
            result["confirm_old_peer_restart_gap2"] = len(confirm_restarted)
        rows.append(result)
    out = pd.DataFrame(rows).sort_values(["touch_date", "ts_code"])
    output.mkdir(parents=True, exist_ok=True)
    out.to_csv(output / "theme_cohort_audit.csv", index=False, encoding="utf-8-sig")
    valid = out[out.status == "ok"].copy()
    summary = {
        "source_cases": len(cases), "mapped": len(valid),
        "unmapped": len(out) - len(valid),
        "primary_changed": int(valid.primary_changed.sum()),
        "self_relimit_10d": int(valid.self_relimit_10d.sum()),
        "self_relimit_same_primary_10d": int(valid.self_second_same_primary.sum()),
        "self_relimit_with_old_peer_same_day": int(
            ((valid.self_relimit_10d)
             & (valid.outcome_self_original_old_peer_limit >= 1)).sum()),
        "self_relimit_with_at_least_two_old_peers_same_day": int(
            ((valid.self_relimit_10d)
             & (valid.outcome_self_original_old_peer_limit >= 2)).sum()),
        "first_wave_theme_width_ge3": int((valid.touch_wave_theme_peak_width >= 3).sum()),
        "first_wave_theme_width_median": float(valid.touch_wave_theme_peak_width.median()),
        "frozen_old_peers_median": float(valid.touch_frozen_old_peers.median()),
        "touch_theme_width_ge3": int((valid.touch_theme_width >= 3).sum()),
        "next3_theme_width_ge3": int(
            (valid.outcome_theme_primary_width_peak_next3 >= 3).sum()),
        "first_broad_touch_narrow_next3_broad": int(
            ((valid.touch_wave_theme_peak_width >= 3)
             & (valid.touch_theme_width < 3)
             & (valid.outcome_theme_primary_width_peak_next3 >= 3)).sum()),
        "touch_width_mismatches": int((~valid.touch_member_width_matches_daily).sum()),
        "touch_old_peer_limit_ge1": int((valid.touch_old_peer_limit >= 1).sum()),
        "touch_old_peer_strong7_ge1": int((valid.touch_old_peer_strong7 >= 1).sum()),
        "touch_old_peer_restart_gap2_ge1": int((valid.touch_old_peer_restart_gap2 >= 1).sum()),
        "future_old_peer_any_relimit_ge1": int((valid.outcome_old_peer_any_relimit_10d >= 1).sum()),
        "future_old_peer_restart_gap2_next3_ge1": int(
            (valid.outcome_old_peer_restart_gap2_next3 >= 1).sum()),
    }
    for label, subset in (("stock_plus10", valid[valid.stock_outcome_plus10_10d]),
                          ("stock_not_plus10", valid[~valid.stock_outcome_plus10_10d])):
        summary[label] = {
            "n": len(subset),
            "touch_theme_width_median": float(subset.touch_theme_width.median()) if len(subset) else None,
            "touch_old_peer_limit_ge1": int((subset.touch_old_peer_limit >= 1).sum()),
            "touch_old_peer_strong7_ge1": int((subset.touch_old_peer_strong7 >= 1).sum()),
            "touch_old_peer_restart_gap2_ge1": int((subset.touch_old_peer_restart_gap2 >= 1).sum()),
            "old_peer_any_relimit_10d_ge1": int((subset.outcome_old_peer_any_relimit_10d >= 1).sum()),
            "old_peer_restart_gap2_next3_ge1": int(
                (subset.outcome_old_peer_restart_gap2_next3 >= 1).sum()),
            "theme_width_peak_next3_median": float(
                subset.outcome_theme_primary_width_peak_next3.median()) if len(subset) else None,
            "early_net_mean_pct": float(subset.early_net_return_pct.mean()) if len(subset) else None,
        }
    (output / "theme_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608/candidate_audit.csv"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608"))
    args = parser.parse_args()
    print(json.dumps(run(args.audit, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
