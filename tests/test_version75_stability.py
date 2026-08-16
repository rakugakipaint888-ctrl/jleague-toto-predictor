"""Version7.5の共通変換・設定保存・エラー耐性を検証する。"""

from __future__ import annotations

import json
import math
import unittest
from datetime import datetime, timedelta

from draw_predictor import DEFAULT_DRAW_SETTINGS
from metrics import normalize_toto_outcome
from parameter_manager import (
    ActiveVersion7BSettings,
    Version7BParameters,
)
from prediction_settings_snapshot import prediction_settings_snapshot
from data_loader import JAPAN_TIMEZONE


class Version75StabilityTest(unittest.TestCase):
    def test_common_toto_outcome_normalizer_handles_csv_and_invalid_values(self) -> None:
        cases = {
            "1": "1",
            0: "0",
            2.0: "2",
            " 0.0 ": "0",
            None: "",
            True: "",
            "nan": "",
            float("inf"): "",
            1.5: "",
            3: "",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_toto_outcome(value), expected)

    def test_prediction_settings_snapshot_is_finite_json_data(self) -> None:
        cutoff = datetime(2026, 8, 17, 0, 0, tzinfo=JAPAN_TIMEZONE)
        prediction_time = cutoff - timedelta(hours=1)
        active = ActiveVersion7BSettings(
            parameters=Version7BParameters(),
            adopted=True,
            draw_override=True,
            adopted_at="2026-08-16T12:00:00+09:00",
        )
        snapshot = prediction_settings_snapshot(
            active,
            draw_settings=DEFAULT_DRAW_SETTINGS,
            model_options={
                "use_elo": True,
                "use_venue": True,
                "use_recent_weighting": True,
                "use_standings": True,
            },
            prediction_time=prediction_time,
            strategy_backtest_cutoff_at=cutoff,
        )

        self.assertEqual(snapshot["prediction_version"], "Version7-B")
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertTrue(snapshot["adopted"])
        self.assertTrue(snapshot["strategy_backtest_eligible"])
        self.assertEqual(snapshot["strategy_backtest_cutoff_at"], cutoff.isoformat())
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        self.assertIn("model_parameters", encoded)
        self.assertIn("draw_parameters", encoded)
        for value in snapshot["model_parameters"].values():
            values = value if isinstance(value, list) else [value]
            self.assertTrue(all(math.isfinite(float(item)) for item in values))

    def test_parameter_manager_keeps_the_legacy_snapshot_import(self) -> None:
        from parameter_manager import (
            prediction_settings_snapshot as legacy_snapshot,
        )

        active = ActiveVersion7BSettings(
            parameters=Version7BParameters(),
            adopted=False,
            draw_override=False,
        )
        direct = prediction_settings_snapshot(
            active,
            draw_settings=DEFAULT_DRAW_SETTINGS,
            model_options={"use_elo": True},
        )
        legacy = legacy_snapshot(
            active,
            draw_settings=DEFAULT_DRAW_SETTINGS,
            model_options={"use_elo": True},
        )
        self.assertEqual(legacy, direct)


if __name__ == "__main__":
    unittest.main()
