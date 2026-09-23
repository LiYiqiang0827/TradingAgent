"""Kimi draft, root reviewed causal state logic; no IO or order execution."""
import math


def simulate_rebound_path(rows, wait_days):
    OPEN_STATES = {"SELLABLE_PROXY", "BLOCKED", "UNKNOWN"}
    KEYS = {"open_state", "adj_low", "adj_close"}

    if isinstance(wait_days, bool) or not isinstance(wait_days, int) or wait_days not in (0, 3, 5, 10):
        raise ValueError("invalid wait_days")
    if not isinstance(rows, list) or not (1 <= len(rows) <= 41):
        raise ValueError("invalid rows length")
    for row in rows:
        if not isinstance(row, dict) or set(row.keys()) != KEYS:
            raise ValueError("invalid row schema")
        if row["open_state"] not in OPEN_STATES:
            raise ValueError("invalid open_state")

    def pos_finite(x):
        return (
            not isinstance(x, bool)
            and isinstance(x, (int, float))
            and math.isfinite(x)
            and x > 0
        )

    def valid_bar(row):
        low = row["adj_low"]
        close = row["adj_close"]
        return pos_finite(low) and pos_finite(close) and low <= close

    state = None
    exit_offset = None
    signal_offset = None
    trace = []
    locked = wait_days == 0
    low_floor = None

    if wait_days > 0:
        if not valid_bar(rows[0]):
            return {
                "state": "UNKNOWN_POLICY_PATH",
                "exit_offset": None,
                "signal_offset": None,
                "trace": [],
            }
        low_floor = float(rows[0]["adj_low"])

    last_i = min(len(rows), 41) - 1
    for i in range(1, last_i + 1):
        row = rows[i]
        open_state = row["open_state"]
        intent = "WANT_SELL" if locked or i > wait_days else "WAIT"
        trace.append({"offset": i, "intent": intent, "open_state": open_state})

        if intent == "WANT_SELL":
            # Missing execution evidence cannot be skipped as if it proved an
            # unfilled order. A later quote cannot identify the first fill.
            if open_state == "UNKNOWN":
                state = "UNKNOWN_POLICY_PATH"
                break
            if open_state == "SELLABLE_PROXY":
                state = "EXITED_PROXY"
                exit_offset = i
                break
            locked = True
            continue

        if not valid_bar(row):
            state = "UNKNOWN_POLICY_PATH"
            break

        low = float(row["adj_low"])
        close = float(row["adj_close"])
        low_floor = min(low_floor, low)
        if close >= low_floor * 1.05:
            signal_offset = i
            locked = True

    if state is None:
        state = "RIGHT_CENSORED" if len(rows) < 41 else "UNRESOLVED_EXIT_BLOCKED"

    return {
        "state": state,
        "exit_offset": exit_offset,
        "signal_offset": signal_offset,
        "trace": trace,
    }
