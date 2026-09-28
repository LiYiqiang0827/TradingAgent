from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

import pandas as pd


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_dynamic_paths.py"
SPEC = importlib.util.spec_from_file_location("update_dynamic_paths", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def bars(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trade_date": f"2026010{index}", "high": high, "low": low, "close": close}
            for index, (high, low, close) in enumerate(rows, start=1)
        ]
    )


class DynamicPathTest(unittest.TestCase):
    def test_p1_breakout_without_support_touch(self) -> None:
        context = MODULE.event_context(bars([(10.4, 9.6, 10.2), (11.2, 10.0, 11.1)]), 11.0, 9.0, 8.0)
        self.assertEqual(context["origin_path"], "P1")

    def test_p2_support_then_breakout(self) -> None:
        context = MODULE.event_context(bars([(10.0, 8.9, 9.4), (11.3, 9.4, 11.1)]), 11.0, 9.0, 8.0)
        self.assertEqual(context["origin_path"], "P2")

    def test_p4_invalidation_before_breakout(self) -> None:
        context = MODULE.event_context(bars([(10.0, 7.8, 7.9), (11.3, 8.0, 11.1)]), 11.0, 9.0, 8.0)
        self.assertEqual(context["origin_path"], "P4")

    def test_long_only_risk_mapping(self) -> None:
        weights = {"P1": 75.0, "P2": 5.0, "P3": 5.0, "P4": 5.0, "P5": 10.0}
        bounds = {"upper": 11.0, "support": 9.0, "invalidation": 8.0}
        self.assertEqual(MODULE.risk_posture("breakout_hold", weights, bounds)["state"], "upside_confirmed")
        self.assertEqual(MODULE.risk_posture("breakout_retest", weights, bounds)["state"], "reduce_risk")
        self.assertEqual(MODULE.risk_posture("structure_invalidated", weights, bounds)["state"], "exit_risk")


if __name__ == "__main__":
    unittest.main()
