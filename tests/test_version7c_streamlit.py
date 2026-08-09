"""Version7-Cを通常予想からStreamlit画面で操作する。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from data_loader import CsvMatchDataSource
from history_manager import TotoRoundLoadResult
from teams import TEAM_OPTIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NO_TOTO_ROUND = TotoRoundLoadResult(
    toto_round=None,
    source_name="テスト",
    status="error",
    message="テストでは手入力します。",
)


def completed_history() -> pd.DataFrame:
    rows = []
    for round_id in (1701, 1702):
        for match_number in range(1, 14):
            actual = "0" if round_id == 1702 and match_number == 1 else "1"
            rows.append(
                {
                    "toto_round": round_id,
                    "toto_match_number": match_number,
                    "prediction_version": "Version7-A",
                    "prediction_date": f"2026-01-{round_id - 1700:02d}",
                    "home_team": f"H{match_number}",
                    "away_team": f"A{match_number}",
                    "prediction": "1",
                    "probability_1": 0.45,
                    "probability_0": 0.35,
                    "probability_2": 0.20,
                    "actual_result": actual,
                    "stake_yen": 100,
                    "payout_yen": 0,
                }
            )
    return pd.DataFrame(rows)


class Version7CStreamlitTest(unittest.TestCase):
    def setUp(self) -> None:
        st.cache_data.clear()
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.csv_path = root / "matches.csv"
        self.csv_path.write_text(
            (
                "match_number,match_date,home_team,away_team,"
                "home_scored,home_conceded,away_scored,away_conceded\n"
                "1,2026-08-09,鹿島アントラーズ,浦和レッズ,2.0,0.8,1.4,1.2\n"
            ),
            encoding="utf-8",
        )
        self.environment_patcher = patch.dict(
            os.environ,
            {"JLEAGUE_ELO_CACHE_PATH": str(root / "elo.json")},
        )
        self.environment_patcher.start()
        self.source_patcher = patch(
            "data_loader.get_default_data_sources",
            return_value=(CsvMatchDataSource(self.csv_path),),
        )
        self.source_patcher.start()
        self.round_patcher = patch(
            "history_manager.TotoHistoryManager.load_current_round",
            return_value=NO_TOTO_ROUND,
        )
        self.round_patcher.start()
        self.history_frame = pd.DataFrame()
        self.history_patcher = patch(
            "prediction_history.PredictionHistoryManager.load",
            side_effect=lambda: self.history_frame.copy(),
        )
        self.history_patcher.start()

    def tearDown(self) -> None:
        self.history_patcher.stop()
        self.round_patcher.stop()
        self.source_patcher.stop()
        self.environment_patcher.stop()
        self.temporary_directory.cleanup()
        st.cache_data.clear()

    def _predicted_app(self) -> AppTest:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=25)
        team_selectboxes = [
            selectbox
            for selectbox in app.selectbox
            if str(selectbox.key).startswith(("home_team_", "away_team_"))
        ]
        for index, selectbox in enumerate(team_selectboxes):
            selectbox.select(TEAM_OPTIONS[index % len(TEAM_OPTIONS)])
        next(
            button
            for button in app.button
            if button.label == "13試合を予想する"
        ).click()
        return app.run(timeout=25)

    def test_toto_ai_plan_manual_change_and_csv_have_no_screen_error(self) -> None:
        app = self._predicted_app()
        self.assertEqual(len(app.exception), 0)
        next(
            item
            for item in app.selectbox
            if item.key == "version7c_double_choice_toto"
        ).select(3)
        next(
            item
            for item in app.selectbox
            if item.key == "version7c_triple_choice_toto"
        ).select(0)
        next(
            button for button in app.button if button.key == "version7c_optimize"
        ).click()
        app = app.run(timeout=25)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        ai_plan = app.session_state["version7c_ai_plan"]
        manual_plan = app.session_state["version7c_manual_plan"]
        self.assertEqual((ai_plan.double_count, ai_plan.triple_count), (3, 0))
        self.assertEqual(ai_plan.ticket_count, 8)
        self.assertEqual(ai_plan.purchase_amount_yen, 800)
        self.assertEqual(manual_plan.ticket_count, 8)
        self.assertGreaterEqual(
            sum("version7c" in str(item.key) for item in app.download_button),
            2,
        )

        single_type = next(
            item
            for item in app.selectbox
            if str(item.key).startswith("version7c_type_") and item.value == "single"
        )
        single_type.select("triple")
        app = app.run(timeout=25)
        changed = app.session_state["version7c_manual_plan"]
        self.assertEqual(changed.double_count, 3)
        self.assertEqual(changed.triple_count, 1)
        self.assertEqual(changed.ticket_count, 24)
        self.assertEqual(changed.purchase_amount_yen, 2_400)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_mini_b_uses_five_matches_and_requested_counts(self) -> None:
        app = self._predicted_app()
        next(
            item for item in app.selectbox if item.key == "version7c_target"
        ).select("mini toto B組（toto第6～10試合）")
        app = app.run(timeout=25)
        next(
            item
            for item in app.selectbox
            if item.key == "version7c_double_choice_mini_b"
        ).select(2)
        next(
            item
            for item in app.selectbox
            if item.key == "version7c_triple_choice_mini_b"
        ).select(1)
        next(
            button for button in app.button if button.key == "version7c_optimize"
        ).click()
        app = app.run(timeout=25)

        plan = app.session_state["version7c_ai_plan"]
        self.assertEqual(plan.match_count, 5)
        self.assertEqual(plan.double_count, 2)
        self.assertEqual(plan.triple_count, 1)
        self.assertEqual(plan.ticket_count, 12)
        self.assertEqual(plan.purchase_amount_yen, 1_200)
        self.assertEqual(
            [
                item.analysis.prediction.source_match_number
                for item in plan.recommendations
            ],
            [6, 7, 8, 9, 10],
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_saved_probabilities_render_three_strategy_backtest(self) -> None:
        self.history_frame = completed_history()
        app = self._predicted_app()
        next(
            button for button in app.button if button.key == "version7c_optimize"
        ).click()
        app = app.run(timeout=25)
        next(
            button for button in app.button if button.key == "version7c_backtest"
        ).click()
        app = app.run(timeout=25)

        results = app.session_state["version7c_backtest_results"]
        self.assertEqual(len(results), 3)
        self.assertTrue(all(result.evaluated_rounds == 2 for result in results))
        self.assertTrue(all(not result.payout_data_available for result in results))
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)


if __name__ == "__main__":
    unittest.main()
