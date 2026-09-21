from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def pick(row: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys}


def compact_packet(packet: dict[str, Any], catalysts: list[dict[str, Any]]) -> dict[str, Any]:
    ladder = packet["ladder"]
    level_one = ladder.get("continuous_by_level", {}).get("1", [])
    first_board_counts = Counter(str(row.get("primary_theme") or "未分类") for row in level_one)
    first_board_examples: dict[str, list[dict[str, Any]]] = {}
    for theme, _ in first_board_counts.most_common(10):
        rows = [row for row in level_one if str(row.get("primary_theme") or "未分类") == theme]
        rows.sort(key=lambda row: (row.get("first_limit_time") is None, row.get("first_limit_time") or "", row.get("ts_code") or ""))
        first_board_examples[theme] = [pick(row, ["ts_code", "name", "first_limit_time", "one_price_proxy"]) for row in rows[:2]]

    noteworthy_outcomes = [
        row for row in ladder.get("previous_high_board_outcomes", [])
        if int(row.get("from_board") or 0) >= 2
    ]
    theme_keys = [
        "theme", "eligible_member_count", "equal_weight_pct_chg", "market_amount_share",
        "closed_limit_up_count", "theme_close_limit_rate", "broken_board_count",
        "touch_seal_rate", "highest_continuous_board", "continuous_board_levels",
        "first_limit_representatives",
    ]
    candidate_keys = [
        "ts_code", "name", "primary_theme", "themes", "board_kind", "board_days", "board_count",
        "pattern_stage", "pattern_shape", "launch_date", "pullback_sessions",
        "close_vs_launch_close", "amount_vs_launch_day",
        "first_limit_time", "one_price_proxy", "today_amount", "today_turnover_rate",
        "amount_vs_prior_20d_median", "return_20d", "risk_flags",
    ]
    past_rows = []
    for row in packet.get("past_mainline_tracking", []):
        today = row.get("today") or {}
        past_rows.append({
            "theme": row.get("theme"),
            "past_15d_best_rank": row.get("past_15d_best_rank"),
            "past_15d_display_days": row.get("past_15d_display_days"),
            "past_15d_active_days": row.get("past_15d_active_days"),
            "past_15d_peak_limit_up_count": row.get("past_15d_peak_limit_up_count"),
            "past_15d_peak_board": row.get("past_15d_peak_board"),
            "last_seen_date": row.get("last_seen_date"),
            "today": pick(today, [
                "closed_limit_up_count", "broken_board_count", "highest_continuous_board",
                "equal_weight_pct_chg", "market_amount_share", "evidence_score",
            ]),
        })

    market = dict(packet["market"])
    market["indexes"] = [pick(row, ["ts_code", "name", "close", "pct_chg"]) for row in market.get("indexes", [])]
    market["broken_board_representatives"] = market.get("broken_board_representatives", [])[:8]
    compact_themes = []
    for row in packet.get("themes", [])[:8]:
        item = pick(row, theme_keys)
        item["first_limit_representatives"] = [
            pick(rep, ["ts_code", "name", "time", "board_count", "one_price_proxy"])
            for rep in row.get("first_limit_representatives", [])[:2]
        ]
        compact_themes.append(item)

    candidate_cards = packet.get("candidate_cards", [])
    pullback_rows = [row for row in candidate_cards if row.get("pattern_stage") == "pullback_in_progress"][:6]
    restart_rows = [row for row in candidate_cards if row.get("pattern_stage") == "restart_completed"][:4]
    board_rows = [row for row in candidate_cards if row.get("pattern_stage") == "continuous_board"][:6]
    launch_rows = [
        row for row in candidate_cards
        if row.get("pattern_stage") == "initial_launch" and row.get("themes")
    ][:4]

    return {
        "task": {
            "trade_date": packet["metadata"]["trade_date"],
            "mode": "end_of_day_short_term_market_review",
            "facts_policy": "Market facts below are authoritative. Do not browse, replace, or silently reconcile them.",
            "catalyst_policy": "Catalysts are a bounded auxiliary layer. Attribute them and do not turn them into extra market statistics.",
            "output_language": "zh-CN",
            "output_contract": [
                "Write a complete review in the narrative style of MR_Example.",
                "Start with a one-paragraph market characterization.",
                "Cover index/liquidity/breadth, continuous ladder, prior-day outcomes, theme structure, money-migration proxy, past-three-week mainline tracking, and at most five watch candidates.",
                "Do not write any If-Then section or If/Then phrasing.",
                "Keep N-day-M-board separate from the continuous ladder.",
                "Treat one-price proxies as difficult or impossible to buy, not normal candidates.",
                "Use no authoritative net-inflow claim; reason from breadth, returns, amount share, limit-up width, sealing quality, and ladder depth.",
                "Past-mainline rows are raw tracking candidates; reject broad attribute labels that are not real trading mainlines.",
                "Candidate output is a watchlist, not an order instruction; an empty list is allowed.",
                "Only first_pullback_in_progress may be described as a pending first-pullback opportunity; restart_completed_samples are hindsight samples and must not be relabelled as pre-restart entries.",
                "Late broken boards are unavailable unless minute validation says otherwise.",
                "Never invent facts, links, announcements, shareholder structure, or active free float.",
            ],
            "narrative_contract": [
                "Open with one decisive market characterization and the session's most important contradiction; do not open with methodology.",
                "Tell the high-board route as yesterday-to-today promotion, failure, repair, and loss-effect evidence rather than a name catalogue.",
                "For each major theme connect width and ladder, timing and core stocks, why it mattered today, current classification, and the next-session evidence to watch.",
                "Keep source notes and corrected provider anomalies in a compact data note unless the correction changes the market conclusion.",
                "End with one arrow-style capital-migration route and a clear trade/no-trade stance.",
            ],
        },
        "metadata": packet["metadata"],
        "definitions": packet["definitions"],
        "data_quality": {
            "required_missing": packet["data_quality"].get("required_missing"),
            "late_break_minute_validated": packet["data_quality"].get("late_break_minute_validated"),
            "previous_trade_date": packet["data_quality"].get("previous_trade_date"),
            "history_dates": packet["data_quality"].get("history_dates"),
        },
        "market": market,
        "ladder": {
            "highest_continuous_board": ladder.get("highest_continuous_board"),
            "continuous_levels_2_plus": {
                level: [pick(row, ["ts_code", "name", "primary_theme", "first_limit_time", "one_price_proxy"]) for row in rows]
                for level, rows in ladder.get("continuous_by_level", {}).items() if level != "1"
            },
            "first_board_counts_by_kpl_primary_theme": dict(first_board_counts.most_common()),
            "first_board_examples": dict(list(first_board_examples.items())[:10]),
            "n_day_m_board": ladder.get("n_day_m_board", []),
            "promotion_rates": ladder.get("promotion_rates", {}),
            "previous_limit_up_feedback": ladder.get("previous_limit_up_feedback", {}),
            "previous_2plus_board_outcomes": noteworthy_outcomes,
        },
        "themes": compact_themes,
        "past_mainline_tracking_raw": past_rows[:8],
        "candidate_pools": {
            "first_pullback_in_progress": [pick(row, candidate_keys) for row in pullback_rows],
            "restart_completed_samples": [pick(row, candidate_keys) for row in restart_rows],
            "continuous_board_relay": [pick(row, candidate_keys) for row in board_rows],
            "new_launches_for_future_tracking": [pick(row, candidate_keys) for row in launch_rows],
        },
        "catalysts": [pick(row, ["theme", "fact", "source_title", "source_url", "confidence"]) for row in catalysts],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compress MR_PACKET for a bounded external writing worker")
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--catalysts", type=Path)
    args = parser.parse_args()
    catalyst_rows = read_json(args.catalysts) if args.catalysts else []
    if not isinstance(catalyst_rows, list):
        raise ValueError("catalysts JSON must be a list")
    compact = compact_packet(read_json(args.packet), catalyst_rows)
    encoded = json.dumps(compact, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > 65536:
        raise ValueError(f"agent packet exceeds 64 KiB scoped-read limit: {len(encoded)} bytes")
    write_json(args.output, compact)
    print(json.dumps({"status": "PASS", "bytes": len(encoded), "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
