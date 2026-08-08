"""Streamlit画面からVersion7-B実経路を10 Trial完走させる。"""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import model_optimization_ui
from data_loader import CsvMatchDataSource, JAPAN_TIMEZONE, OfficialMatch
from history_manager import TotoMatch, TotoRound, TotoRoundLoadResult
from model_optimizer import RoundCollection
from parameter_manager import default_active_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

NO_TOTO_ROUND = TotoRoundLoadResult(
    toto_round=None,
    source_name="テスト",
    status="error",
    message="テストでは現在開催回を使用しません。",
)


def _official_round(round_id: int, year: int) -> TotoRound:
    kickoff = datetime(year, 6, 21, 15, 0, tzinfo=JAPAN_TIMEZONE)
    outcomes = ("1", "0", "2")
    return TotoRound(
        round_id=round_id,
        matches=tuple(
            TotoMatch(
                round_id=round_id,
                match_number=number,
                home_team="鹿島アントラーズ",
                away_team="浦和レッズ",
                match_time=kickoff + timedelta(minutes=number),
                actual_result=outcomes[(number - 1) % 3],
            )
            for number in range(1, 14)
        ),
    )


def _history() -> tuple[OfficialMatch, ...]:
    matches = []
    for year in (2022, 2023, 2024, 2025, 2026):
        start = datetime(year, 1, 10, 14, 0, tzinfo=JAPAN_TIMEZONE)
        for index in range(10):
            matches.append(
                OfficialMatch(
                    match_time=start + timedelta(days=index * 14),
                    home_team=(
                        "鹿島アントラーズ" if index % 2 == 0 else "浦和レッズ"
                    ),
                    away_team=(
                        "浦和レッズ" if index % 2 == 0 else "鹿島アントラーズ"
                    ),
                    home_goals=index % 3,
                    away_goals=(index + 1) % 3,
                    category="J1",
                )
            )
    return tuple(matches)


class Version7BStreamlitTest(unittest.TestCase):
    def test_streamlit_start_button_completes_ten_real_trials(self) -> None:
        st.cache_data.clear()
        rounds = (
            _official_round(1400, 2023),
            _official_round(1500, 2024),
            _official_round(1600, 2025),
        )
        collection = RoundCollection(
            rounds=rounds,
            requested_years=(2022, 2023, 2024, 2025, 2026),
            used_years=(2023, 2024, 2025),
            missing_years=(2022, 2026),
        )
        original_run = model_optimization_ui.run_model_optimization
        original_history_save = model_optimization_ui.save_optimization_history
        original_ranking_save = model_optimization_ui.save_model_ranking

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            csv_path = root / "matches.csv"
            csv_path.write_text(
                (
                    "match_number,match_date,home_team,away_team,"
                    "home_scored,home_conceded,away_scored,away_conceded\n"
                    "1,2026-08-07,鹿島アントラーズ,浦和レッズ,2.0,0.8,1.4,1.2\n"
                ),
                encoding="utf-8",
            )
            partial_path = root / "partial.csv"
            history_path = root / "history.csv"
            ranking_path = root / "ranking.csv"

            def run_from_screen(*args, **kwargs):
                return original_run(*args, **kwargs, partial_path=partial_path)

            def save_history_from_screen(result):
                return original_history_save(result, path=history_path)

            def save_ranking_from_screen(result):
                return original_ranking_save(result, path=ranking_path)

            with patch.dict(
                os.environ,
                {"JLEAGUE_ELO_CACHE_PATH": str(root / "elo.json")},
            ), patch(
                "data_loader.get_default_data_sources",
                return_value=(CsvMatchDataSource(csv_path),),
            ), patch(
                "history_manager.TotoHistoryManager.load_current_round",
                return_value=NO_TOTO_ROUND,
            ), patch(
                "prediction_history.PredictionHistoryManager.load",
                return_value=pd.DataFrame(),
            ), patch(
                "model_optimization_ui.collect_available_completed_rounds",
                return_value=collection,
            ), patch(
                "model_optimization_ui.collect_historical_matches",
                return_value=_history(),
            ), patch(
                "model_optimization_ui.load_active_version7b_settings",
                return_value=default_active_settings(),
            ), patch(
                "model_optimization_ui.run_model_optimization",
                side_effect=run_from_screen,
            ), patch(
                "model_optimization_ui.save_optimization_history",
                side_effect=save_history_from_screen,
            ), patch(
                "model_optimization_ui.save_model_ranking",
                side_effect=save_ranking_from_screen,
            ):
                app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(
                    timeout=30
                )
                next(
                    item
                    for item in app.selectbox
                    if item.key == "version7b_trials_choice"
                ).select(10)
                next(
                    item
                    for item in app.selectbox
                    if item.key == "version7b_bootstrap_choice"
                ).select(0)
                app.run(timeout=30)
                next(
                    item
                    for item in app.button
                    if item.key == "version7b_start"
                ).click()
                app.run(timeout=60)

            result = app.session_state["version7b_optimization_result"]
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            self.assertEqual(len(result.all_trials), 10)
            self.assertEqual(len(result.ranking), 10)
            with partial_path.open(encoding="utf-8-sig") as partial_file:
                self.assertEqual(sum(1 for _ in csv.DictReader(partial_file)), 10)
            with history_path.open(encoding="utf-8-sig") as history_file:
                self.assertEqual(sum(1 for _ in csv.DictReader(history_file)), 1)
            with ranking_path.open(encoding="utf-8-sig") as ranking_file:
                self.assertEqual(sum(1 for _ in csv.DictReader(ranking_file)), 10)
            self.assertTrue(
                any("10モデル完了" in item.value for item in app.success)
            )
        st.cache_data.clear()


if __name__ == "__main__":
    unittest.main()
