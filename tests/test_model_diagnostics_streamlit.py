"""Version8-Bモデル診断タブの実ユーザー経路をAppTestで確認する。"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from data_loader import CsvMatchDataSource
from diagnostic_history import DiagnosticHistoryManager
from history_manager import TotoRoundLoadResult
from improvement_history import ImprovementHistoryManager
from live_history import LiveHistoryManager
from tests.test_backtest import completed_round
from tests.test_model_diagnostics import (
    BASE_TIME,
    prediction_frame_for,
    round_for,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ModelDiagnosticsStreamlitE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        st.cache_data.clear()
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        csv_path = root / "matches.csv"
        csv_path.write_text(
            (
                "match_number,match_date,home_team,away_team,"
                "home_scored,home_conceded,away_scored,away_conceded\n"
                "1,2025-06-20,鹿島アントラーズ,浦和レッズ,2.0,0.8,1.4,1.2\n"
            ),
            encoding="utf-8",
        )
        base_round = completed_round()
        self.pending_round = replace(
            base_round,
            matches=tuple(
                replace(
                    match,
                    actual_result=None,
                    home_goals=None,
                    away_goals=None,
                )
                for match in base_round.matches
            ),
        )
        self.current_result = TotoRoundLoadResult(
            toto_round=self.pending_round,
            source_name="toto公式",
            status="loaded",
            message="toto公式からテスト開催回を読み込みました。",
        )
        self.live_manager = LiveHistoryManager(
            round_path=root / "live_round_history.csv",
            match_path=root / "live_match_history.csv",
            bet_path=root / "live_bet_history.csv",
        )
        self.diagnostic_manager = DiagnosticHistoryManager(
            root / "model_diagnostic_history.csv"
        )
        self.improvement_manager = ImprovementHistoryManager(
            root / "model_improvement_history.csv"
        )
        self._save_completed_round(1)
        self._save_completed_round(2)
        self.patchers = [
            patch.dict(
                os.environ,
                {"JLEAGUE_ELO_CACHE_PATH": str(root / "elo.json")},
            ),
            patch(
                "data_loader.get_default_data_sources",
                return_value=(CsvMatchDataSource(csv_path),),
            ),
            patch(
                "history_manager.TotoHistoryManager.load_current_round",
                return_value=self.current_result,
            ),
            patch(
                "history_manager.TotoHistoryManager.load_round",
                return_value=self.current_result,
            ),
            patch(
                "prediction_history.PredictionHistoryManager.load",
                return_value=pd.DataFrame(),
            ),
            patch(
                "prediction_history.PredictionHistoryManager.save_prediction_results",
                return_value=True,
            ),
            patch("live_history.LiveHistoryManager", return_value=self.live_manager),
            patch(
                "diagnostic_history.DiagnosticHistoryManager",
                return_value=self.diagnostic_manager,
            ),
            patch(
                "improvement_history.ImprovementHistoryManager",
                return_value=self.improvement_manager,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary_directory.cleanup()
        st.cache_data.clear()

    def _save_completed_round(self, index: int) -> None:
        round_id = 4000 + index
        frame = prediction_frame_for(round_id)
        prediction_time = BASE_TIME + timedelta(days=index * 7 - 1)
        outcome = self.live_manager.save_prediction(
            frame,
            round_for(round_id, index),
            settings_snapshot={
                "schema_version": 1,
                "prediction_version": "Version7-B",
                "model_parameters": {"home_correction": 1.08},
                "draw_parameters": {"candidate_threshold": 0.25},
            },
            prediction_time=prediction_time,
            source_name="toto公式",
        )
        actuals = {
            number: str(frame.iloc[number - 1]["本命"])
            for number in range(1, 14)
        }
        self.live_manager.update_actual_results(
            outcome.prediction_run_id,
            round_for(round_id, index, actuals=actuals),
            source_name="toto公式",
        )
        self.live_manager.evaluate_run(outcome.prediction_run_id)

    @staticmethod
    def _button(app: AppTest, key: str):
        return next(button for button in app.button if button.key == key)

    def test_full_model_diagnostics_user_path_renders_to_bottom(self) -> None:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("実戦履歴", [tab.label for tab in app.tabs])
        self.assertIn("モデル診断", [tab.label for tab in app.tabs])
        self.assertIn("AI改善提案", [tab.label for tab in app.tabs])
        period = next(
            item for item in app.selectbox if item.key == "version8b_period"
        )
        league = next(
            item for item in app.selectbox if item.key == "version8b_league"
        )
        self.assertEqual(period.value, "全実戦履歴")
        self.assertEqual(league.value, "全リーグ")
        period.select("直近5開催")
        league.select("J1")
        app = app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            next(
                item
                for item in app.selectbox
                if item.key == "version8b_period"
            ).value,
            "直近5開催",
        )
        self.assertEqual(
            next(
                item
                for item in app.selectbox
                if item.key == "version8b_league"
            ).value,
            "J1",
        )

        self._button(app, "version8b_run_diagnostics").click()
        app = app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        subheaders = {item.value for item in app.subheader}
        for heading in (
            "現在のモデル状態",
            "全体指標",
            "1 / 0 / 2別診断",
            "引分診断",
            "確率帯別Calibration",
            "Rolling診断・モデル劣化比較",
            "時系列推移",
            "異常一覧",
            "買い目診断",
            "Coverage診断",
            "データ品質診断",
            "診断履歴",
        ):
            self.assertIn(heading, subheaders)
        self.assertGreaterEqual(len(app.dataframe), 8)
        self.assertGreaterEqual(len(app.get("vega_lite_chart")), 2)
        self.assertEqual(len(self.diagnostic_manager.load()), 1)
        self.assertTrue(
            any(
                button.label == "診断履歴CSV"
                for button in app.download_button
            )
        )

        self._button(app, "version8c_generate_recommendations").click()
        app = app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        subheaders = {item.value for item in app.subheader}
        for heading in (
            "1. 現在の状態",
            "2. 検知された問題",
            "3. 原因候補",
            "4. 改善候補",
            "5. 推奨アクション",
            "6. 注意事項",
            "提案履歴",
        ):
            self.assertIn(heading, subheaders)
        self.assertEqual(len(self.improvement_manager.load()), 1)
        self.assertTrue(
            any(
                button.label == "提案履歴CSV"
                for button in app.download_button
            )
        )


if __name__ == "__main__":
    unittest.main()
