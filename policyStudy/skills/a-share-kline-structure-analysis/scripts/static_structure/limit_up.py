"""Daily-bar limit-up launch anchors, exact when official limit rows are supplied."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .common import number


def detect_limit_up_anchors(
    frame: pd.DataFrame,
    *,
    official_dates: set[str] | None = None,
    quiet_bars: int = 15,
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    dates = official_dates or set()
    pct = frame["close"].pct_change() * 100
    is_limit = pd.Series([str(date) in dates for date in frame["trade_date"]], index=frame.index)
    if not dates:
        is_limit = pct >= 9.5
    anchors: list[dict[str, Any]] = []
    last_event = -10_000
    for index in frame.index[is_limit.fillna(False)]:
        row = frame.iloc[int(index)]
        previous = frame.iloc[int(index) - 1] if int(index) > 0 else None
        quiet = int(index) - last_event > quiet_bars
        prior_start = max(0, int(index) - 20)
        prior = frame.iloc[prior_start:int(index)]
        platform_high = float(prior["high"].quantile(0.90)) if not prior.empty else float(row["open"])
        gap_up = bool(previous is not None and float(row["low"]) > float(previous["high"]))
        later = frame.iloc[int(index) + 1 :]
        prior_close = float(previous["close"]) if previous is not None else float(row["open"])
        # A prior-window high above the limit-up close is overhead resistance, not
        # part of this launch zone. Include the platform edge only when cleared.
        cleared_platform_high = platform_high if platform_high <= float(row["close"]) * 1.01 else prior_close
        anchor_lower = min(float(row["low"]), prior_close, cleared_platform_high)
        anchor_upper = max(float(row["open"]), prior_close, cleared_platform_high)
        if later.empty:
            status, retest_date = "active", None
        else:
            retest = later[(later["low"] <= anchor_upper) & (later["high"] >= anchor_lower)]
            failed = later[later["close"] < anchor_lower]
            retest_date = None if retest.empty else str(retest.iloc[0]["trade_date"])
            status = "failed" if not failed.empty else ("retested" if not retest.empty else "active")
        anchors.append(
            {
                "anchor_id": f"limit_up:{row['trade_date']}",
                "trade_date": str(row["trade_date"]),
                "detection_basis": "official_stk_limit" if dates else "daily_pct_heuristic",
                "quiet_period_launch": quiet,
                "previous_close": None if previous is None else number(previous["close"]),
                "open": number(row["open"]),
                "low": number(row["low"]),
                "high": number(row["high"]),
                "close": number(row["close"]),
                "platform_high": number(platform_high),
                "anchor_lower": number(anchor_lower),
                "anchor_upper": number(anchor_upper),
                "gap_up": gap_up,
                "retest_date": retest_date,
                "status": status,
            }
        )
        last_event = int(index)
    return anchors[-12:]
