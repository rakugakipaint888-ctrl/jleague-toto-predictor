"""Version8-Aの実ユーザー経路をStreamlit AppTestで確認する。"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from data_loader import CsvMatchDataSource
from history_manager import TotoRoundLoadResult
from live_history import LiveHistoryManager
from tests.test_backtest import completed_round


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LiveHistoryStreamlitE2ETest(unittest.TestCase):
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
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary_directory.cleanup()
        st.cache_data.clear()

    @staticmethod
    def _button(app: AppTest, label: str):
        return next(button for button in app.button if button.label == label)

    def test_prediction_bet_save_history_purchase_and_pending_result_flow(self) -> None:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("実戦履歴", [tab.label for tab in app.tabs])

        self._button(app, "13試合を予想する").click()
        app = app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        predictions = app.session_state["latest_prediction_results"]
        self.assertEqual(len(predictions), 13)
        self.assertTrue(
            all(
                abs(float(row["1"]) + float(row["0"]) + float(row["2"]) - 100.0)
                <= 1e-12
                for _, row in predictions.iterrows()
            )
        )

        next(
            item
            for item in app.selectbox
            if item.key == "version7c_double_choice_toto"
        ).select(2)
        next(
            item
            for item in app.selectbox
            if item.key == "version7c_triple_choice_toto"
        ).select(1)
        next(
            button for button in app.button if button.key == "version7c_optimize"
        ).click()
        app = app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        ai_plan = app.session_state["version7c_ai_plan"]
        self.assertEqual((ai_plan.double_count, ai_plan.triple_count), (2, 1))
        self.assertEqual(ai_plan.ticket_count, 12)
        self.assertTrue(math_is_finite_positive(ai_plan.estimated_full_coverage))

        # 1試合を手動でトリプルへ広げ、最終買い目を購入記録対象にする。
        manual_single = next(
            item
            for item in app.selectbox
            if str(item.key).startswith("version7c_type_") and item.value == "single"
        )
        manual_single.select("triple")
        app = app.run(timeout=30)
        manual_plan = app.session_state["version7c_manual_plan"]
        self.assertEqual(manual_plan.triple_count, 2)

        self._button(app, "実戦予測として保存").click()
        app = app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        rounds = self.live_manager.load_rounds()
        matches = self.live_manager.load_matches()
        bets = self.live_manager.load_bets()
        self.assertEqual(len(rounds), 1)
        self.assertEqual(len(matches), 13)
        self.assertEqual(len(bets), 1)
        self.assertEqual(bets.iloc[0]["record_type"], "recommended")
        self.assertEqual(rounds.iloc[0]["round_status"], "predicted")
        self.assertAlmostEqual(
            float(matches.iloc[0]["probability_1"]),
            float(predictions.iloc[0]["live_probability_1"]),
            delta=1e-15,
        )
        self.assertTrue(
            any(
                isinstance(element.value, pd.DataFrame)
                and len(element.value) == 13
                and "AI推奨買い目" in element.value.columns
                for element in app.dataframe
            )
        )
        self.assertEqual(
            {
                button.label
                for button in app.download_button
                if str(button.key).startswith("version8a_download_")
            },
            {"開催回サマリーCSV", "試合単位履歴CSV", "買い目履歴CSV"},
        )

        # 同じ画面予測の保存ボタンを再度押してもrunと13行は増えない。
        self._button(app, "実戦予測として保存").click()
        app = app.run(timeout=30)
        self.assertEqual(len(self.live_manager.load_rounds()), 1)
        self.assertEqual(len(self.live_manager.load_matches()), 13)

        self._button(app, "この買い目を実際に購入したとして記録").click()
        app = app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        bets = self.live_manager.load_bets()
        self.assertEqual(set(bets["record_type"]), {"recommended", "purchased"})
        purchased = bets.loc[bets["record_type"] == "purchased"].iloc[0]
        self.assertEqual(
            int(purchased["actual_purchase_amount_yen"]),
            manual_plan.purchase_amount_yen,
        )
        self.assertEqual(
            json_outcomes(purchased["selections_json"]),
            [list(item.outcomes) for item in manual_plan.recommendations],
        )

        self._button(app, "公式結果を更新").click()
        app = app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(self.live_manager.load_rounds().iloc[0]["round_status"], "pending_result")
        self.assertEqual(self.live_manager.load_rounds().iloc[0]["actual_result_count"], "0")
        self.assertEqual(set(self.live_manager.load_matches()["actual_result"]), {""})


def math_is_finite_positive(value: float) -> bool:
    return value > 0.0 and value <= 1.0


def json_outcomes(value: str) -> list[list[str]]:
    import json

    return [item["outcomes"] for item in json.loads(value)]


if __name__ == "__main__":
    unittest.main()
