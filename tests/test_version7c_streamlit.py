"""Version7-Cを通常予想からStreamlit画面で操作する。"""

from __future__ import annotations

import ast
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import bet_optimization_ui
from analysis import Version7AHistoryGenerationResult
from bet_export import BET_PLAN_DISPLAY_COLUMNS
from data_loader import CsvMatchDataSource
from history_manager import (
    TotoRoundLoadResult,
    TotoRoundSummary,
)
from prediction_history import PredictionHistoryManager
from teams import TEAM_OPTIONS
from tests.test_backtest import completed_round, historical_matches


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NO_TOTO_ROUND = TotoRoundLoadResult(
    toto_round=None,
    source_name="テスト",
    status="error",
    message="テストでは手入力します。",
)


class LegacyMissingPayoutRecord:
    """canonicalな1等列を持たない旧Series adapter。"""

    def __init__(self) -> None:
        self.row = pd.Series(
            {
                "second_prize": 200,
                "third_prize": 50,
            }
        )

    @property
    def first_prize_yen(self):
        return self.row["first_prize_yen"]


class LegacyMissingPayoutRound:
    payouts = LegacyMissingPayoutRecord()


def completed_history(version: str = "Version7-A") -> pd.DataFrame:
    rows = []
    for round_id in (1701, 1702):
        for match_number in range(1, 14):
            actual = "0" if round_id == 1702 and match_number == 1 else "1"
            rows.append(
                {
                    "toto_round": round_id,
                    "toto_match_number": match_number,
                    "prediction_version": version,
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


def display_probability_frame(*, include_draw_candidates: bool) -> pd.DataFrame:
    patterns = (
        (70.0, 18.0, 12.0),
        (38.0, 35.0, 27.0),
        (35.0, 33.0, 32.0),
        (50.0, 10.0, 40.0),
        (45.0, 35.0, 20.0),
    )
    rows = []
    for number in range(1, 14):
        probabilities = (
            patterns[(number - 1) % len(patterns)]
            if include_draw_candidates
            else (70.0, 10.0, 20.0)
        )
        rows.append(
            {
                "toto_round": 1703,
                "toto_match_number": number,
                "対戦カード": f"ホーム{number} vs アウェイ{number}",
                "1": probabilities[0],
                "0": probabilities[1],
                "2": probabilities[2],
                "draw_candidate": bool(
                    include_draw_candidates and number % 5 in (2, 3, 0)
                ),
                "prediction_version": "Version7-A",
            }
        )
    return pd.DataFrame(rows)


class Version7CScalarAccessTest(unittest.TestCase):
    def test_first_scalar_reads_zero_index_series(self) -> None:
        values = pd.Series(["Version7-A"], index=[0])

        self.assertEqual(bet_optimization_ui._first_scalar(values), "Version7-A")

    def test_first_scalar_reads_nonzero_index_series_by_position(self) -> None:
        values = pd.Series(["Version7-A"], index=[13])

        self.assertEqual(bet_optimization_ui._first_scalar(values), "Version7-A")

    def test_first_scalar_reads_round_index_series_by_position(self) -> None:
        values = pd.Series(["Version7-A"], index=[1645])

        self.assertEqual(bet_optimization_ui._first_scalar(values), "Version7-A")

    def test_first_scalar_reads_first_of_nonzero_index_series(self) -> None:
        values = pd.Series(["Version7-A", "Version6"], index=[13, 26])

        self.assertEqual(bet_optimization_ui._first_scalar(values), "Version7-A")

    def test_first_scalar_returns_empty_text_for_empty_series(self) -> None:
        self.assertEqual(
            bet_optimization_ui._first_scalar(pd.Series(dtype=object)),
            "",
        )

    def test_first_scalar_reads_list(self) -> None:
        self.assertEqual(
            bet_optimization_ui._first_scalar(["Version7-A", "Version6"]),
            "Version7-A",
        )

    def test_first_scalar_reads_tuple(self) -> None:
        self.assertEqual(
            bet_optimization_ui._first_scalar(("Version7-A", "Version6")),
            "Version7-A",
        )

    def test_first_scalar_preserves_scalar(self) -> None:
        self.assertEqual(
            bet_optimization_ui._first_scalar("Version7-A"),
            "Version7-A",
        )

    def test_first_scalar_returns_empty_text_for_none(self) -> None:
        self.assertEqual(bet_optimization_ui._first_scalar(None), "")

    def test_first_scalar_reads_dataframe_by_position(self) -> None:
        values = pd.DataFrame(
            {"prediction_version": ["Version7-A"]},
            index=[13],
        )

        self.assertEqual(bet_optimization_ui._first_scalar(values), "Version7-A")

    def test_first_scalar_reads_numpy_array(self) -> None:
        values = np.array(["Version7-A", "Version6"], dtype=object)

        self.assertEqual(bet_optimization_ui._first_scalar(values), "Version7-A")

    def test_prediction_version_keeps_nonzero_series_index_safe(self) -> None:
        frame = pd.DataFrame(
            {"prediction_version": ["Version7-A", "Version6"]},
            index=[13, 26],
        )

        self.assertEqual(
            bet_optimization_ui._prediction_version(frame),
            "Version7-A",
        )


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
        self.persisted_history_manager = PredictionHistoryManager(
            root / "prediction_history.csv"
        )
        self.original_history_load = PredictionHistoryManager.load
        self.original_history_save = PredictionHistoryManager.save_records
        self.original_history_reconcile = (
            PredictionHistoryManager.reconcile_actual_results
        )
        self.history_patcher = patch(
            "prediction_history.PredictionHistoryManager.load",
            side_effect=lambda: self.history_frame.copy(),
        )
        self.history_patcher.start()
        self.history_save_patcher = patch(
            "prediction_history.PredictionHistoryManager.save_records",
            side_effect=self._save_history_records,
        )
        self.history_save_patcher.start()
        self.history_reconcile_patcher = patch(
            "prediction_history.PredictionHistoryManager.reconcile_actual_results",
            side_effect=self._reconcile_history,
        )
        self.history_reconcile_patcher.start()

    def tearDown(self) -> None:
        self.history_reconcile_patcher.stop()
        self.history_save_patcher.stop()
        self.history_patcher.stop()
        self.round_patcher.stop()
        self.source_patcher.stop()
        self.environment_patcher.stop()
        self.temporary_directory.cleanup()
        st.cache_data.clear()

    def _save_history_records(self, records, *, payouts_by_round=None):
        saved = self.original_history_save(
            self.persisted_history_manager,
            records,
            payouts_by_round=payouts_by_round,
        )
        self.history_frame = self.original_history_load(
            self.persisted_history_manager
        )
        return saved

    def _reconcile_history(self, toto_round):
        reconciled = self.original_history_reconcile(
            self.persisted_history_manager,
            toto_round,
        )
        self.history_frame = self.original_history_load(
            self.persisted_history_manager
        )
        return reconciled

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

    @staticmethod
    def _display_plan_frames(app: AppTest) -> list[pd.DataFrame]:
        return [
            element.value
            for element in app.dataframe
            if isinstance(element.value, pd.DataFrame)
            and tuple(element.value.columns) == BET_PLAN_DISPLAY_COLUMNS
        ]

    def test_renderer_signature_matches_every_app_call(self) -> None:
        signature = inspect.signature(
            bet_optimization_ui.render_bet_optimization_tab
        )
        expected_keywords = tuple(signature.parameters)
        self.assertEqual(
            expected_keywords,
            (
                "prediction_history_manager",
                "history_manager",
                "active_draw_settings",
                "fallback_matches",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in signature.parameters.values()
            )
        )

        app_tree = ast.parse(
            (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
        )
        calls = [
            node
            for node in ast.walk(app_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "render_bet_optimization_tab"
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args), 0)
        self.assertEqual(
            tuple(keyword.arg for keyword in calls[0].keywords),
            expected_keywords,
        )

    def test_toto_ai_plan_manual_change_and_csv_have_no_screen_error(self) -> None:
        app = self._predicted_app()
        self.assertEqual(len(app.exception), 0)
        latest_results = app.session_state["latest_prediction_results"].copy()
        latest_results.index = pd.Index(
            [13 * number for number in range(1, len(latest_results) + 1)]
        )
        app.session_state["latest_prediction_results"] = latest_results
        app = app.run(timeout=25)
        self.assertEqual(
            list(app.session_state["latest_prediction_results"].index[:2]),
            [13, 26],
        )
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
        plan_frames = self._display_plan_frames(app)
        self.assertGreaterEqual(len(plan_frames), 2)
        self.assertTrue(all(len(frame) == 13 for frame in plan_frames))
        self.assertEqual(
            set(BET_PLAN_DISPLAY_COLUMNS) - set(plan_frames[0].columns),
            set(),
        )
        self.assertGreaterEqual(
            sum("version7c" in str(item.key) for item in app.download_button),
            2,
        )
        self.assertTrue(
            any(
                item.label == "AI提案 推定Coverage"
                and str(item.value).endswith("%")
                for item in app.metric
            )
        )
        self.assertTrue(
            any(item.key == "version7c_backtest" for item in app.button)
        )
        self.assertTrue(
            any(
                item.key == "version7c_backtest_version"
                for item in app.selectbox
            )
        )
        self.assertTrue(
            any(
                item.label == "過去データで買い目戦略を比較"
                for item in app.expander
            )
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
        self.assertTrue(
            any(
                str(caption.value).startswith("変更後Coverage：")
                for caption in app.caption
            )
        )
        changed_frames = self._display_plan_frames(app)
        self.assertGreaterEqual(len(changed_frames), 2)
        self.assertTrue(all(len(frame) == 13 for frame in changed_frames))
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_mini_a_uses_formal_display_schema(self) -> None:
        app = self._predicted_app()
        next(
            item for item in app.selectbox if item.key == "version7c_target"
        ).select("mini toto A組（toto第1～5試合）")
        app = app.run(timeout=25)
        next(
            item
            for item in app.selectbox
            if item.key == "version7c_double_choice_mini_a"
        ).select(2)
        next(
            item
            for item in app.selectbox
            if item.key == "version7c_triple_choice_mini_a"
        ).select(1)
        next(
            button for button in app.button if button.key == "version7c_optimize"
        ).click()
        app = app.run(timeout=25)

        plan = app.session_state["version7c_ai_plan"]
        self.assertEqual(
            (plan.match_count, plan.double_count, plan.triple_count),
            (5, 2, 1),
        )
        self.assertEqual(plan.ticket_count, 12)
        self.assertEqual(plan.purchase_amount_yen, 1_200)
        self.assertEqual(
            [
                item.analysis.prediction.source_match_number
                for item in plan.recommendations
            ],
            [1, 2, 3, 4, 5],
        )
        frames = self._display_plan_frames(app)
        self.assertGreaterEqual(len(frames), 2)
        self.assertTrue(all(len(frame) == 5 for frame in frames))
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
        frames = self._display_plan_frames(app)
        self.assertGreaterEqual(len(frames), 2)
        self.assertTrue(all(len(frame) == 5 for frame in frames))
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_draw_candidate_and_no_candidate_frames_render_in_streamlit(self) -> None:
        app = self._predicted_app()
        app.session_state["latest_prediction_results"] = display_probability_frame(
            include_draw_candidates=True
        )
        app = app.run(timeout=25)
        next(
            button for button in app.button if button.key == "version7c_optimize"
        ).click()
        app = app.run(timeout=25)
        candidate_frame = self._display_plan_frames(app)[0]
        self.assertIn("候補", set(candidate_frame["引分候補"]))
        self.assertIn("—", set(candidate_frame["引分候補"]))
        self.assertEqual(len(app.exception), 0)

        app.session_state["latest_prediction_results"] = display_probability_frame(
            include_draw_candidates=False
        )
        app = app.run(timeout=25)
        next(
            button for button in app.button if button.key == "version7c_optimize"
        ).click()
        app = app.run(timeout=25)
        no_candidate_frame = self._display_plan_frames(app)[0]
        self.assertEqual(set(no_candidate_frame["引分候補"]), {"—"})
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_version7a_saved_probabilities_render_three_strategy_backtest(
        self,
    ) -> None:
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
        self.assertTrue(
            all(result.prediction_version == "Version7-A" for result in results)
        )
        self.assertTrue(all(result.evaluated_rounds == 2 for result in results))
        self.assertTrue(all(not result.payout_data_available for result in results))
        self.assertFalse(
            any(
                warning.value
                == "実結果まで揃った対象開催回を確認できませんでした。"
                for warning in app.warning
            )
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_empty_version7a_history_is_generated_from_strategy_screen(self) -> None:
        toto_round = completed_round()
        catalog = (
            TotoRoundSummary(
                round_id=toto_round.round_id,
                fiscal_year=2025,
                label=f"第{toto_round.round_id}回",
            ),
        )
        loaded = TotoRoundLoadResult(
            toto_round=toto_round,
            source_name="テスト",
            status="loaded",
            message="読み込みました。",
        )
        with (
            patch(
                "history_manager.TotoHistoryManager.load_catalog",
                return_value=catalog,
            ),
            patch(
                "history_manager.TotoHistoryManager.load_round",
                return_value=loaded,
            ),
            patch(
                "analysis.collect_historical_matches",
                return_value=tuple(historical_matches()),
            ) as collect_mock,
        ):
            app = self._predicted_app()
            next(
                button
                for button in app.button
                if button.key == "version7c_optimize"
            ).click()
            app = app.run(timeout=25)
            next(
                button
                for button in app.button
                if button.key == "version7c_backtest"
            ).click()
            app = app.run(timeout=25)

            collect_mock.assert_called_once()
            result = app.session_state[
                "version7c_version7a_history_generation"
            ]
            self.assertIsInstance(result, Version7AHistoryGenerationResult)
            self.assertEqual(result.target_round_count, 1)
            self.assertEqual(result.generated_round_count, 1)
            self.assertEqual(result.generated_match_count, 13)
            self.assertEqual(result.actual_result_count, 13)
            saved = self.history_frame.loc[
                self.history_frame["prediction_version"] == "Version7-A"
            ]
            self.assertEqual(len(saved), 13)
            self.assertEqual(
                set(saved["toto_match_number"].astype(int)),
                set(range(1, 14)),
            )
            self.assertTrue(saved["actual_result"].isin(("1", "0", "2")).all())
            results = app.session_state["version7c_backtest_results"]
            self.assertEqual(len(results), 3)
            self.assertTrue(
                all(item.evaluated_rounds == 1 for item in results)
            )
            self.assertFalse(
                any(
                    warning.value
                    == "実結果まで揃った対象開催回を確認できませんでした。"
                    for warning in app.warning
                )
            )

            next(
                button
                for button in app.button
                if button.key == "version7c_backtest"
            ).click()
            app = app.run(timeout=25)
            second = app.session_state[
                "version7c_version7a_history_generation"
            ]
            self.assertEqual(second.target_round_count, 1)
            self.assertEqual(second.generated_round_count, 0)
            self.assertEqual(second.generated_match_count, 0)
            self.assertEqual(len(self.history_frame), 13)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)

    def test_version6_strategy_backtest_still_renders(self) -> None:
        self.history_frame = completed_history("Version6")
        app = self._predicted_app()
        next(
            item
            for item in app.selectbox
            if item.key == "version7c_backtest_version"
        ).select("Version6")
        app = app.run(timeout=25)
        next(
            button
            for button in app.button
            if button.key == "version7c_backtest"
        ).click()
        app = app.run(timeout=25)

        results = app.session_state["version7c_backtest_results"]
        self.assertEqual(len(results), 3)
        self.assertTrue(
            all(result.prediction_version == "Version6" for result in results)
        )
        self.assertTrue(all(result.evaluated_rounds == 2 for result in results))
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_end_to_end_version6_then_on_demand_version7a_without_payout(
        self,
    ) -> None:
        """起動から買い目、Version6、Version7-A生成まで1本で通す。"""

        version6_history = completed_history("Version6")
        version6_history = version6_history.loc[
            version6_history["toto_round"] == 1701
        ].copy()
        version6_history["toto_round"] = 1548
        self.history_frame = version6_history
        toto_round = completed_round()
        loaded = TotoRoundLoadResult(
            toto_round=toto_round,
            source_name="テスト",
            status="loaded",
            message="読み込みました。",
        )

        with (
            patch(
                "history_manager.TotoHistoryManager.load_round",
                return_value=loaded,
            ),
            patch(
                "history_manager.TotoHistoryManager.load_saved_round",
                return_value=LegacyMissingPayoutRound(),
            ),
            patch(
                "analysis.collect_historical_matches",
                return_value=tuple(historical_matches()),
            ),
        ):
            app = self._predicted_app()
            self.assertEqual(len(app.exception), 0)

            next(
                button
                for button in app.button
                if button.key == "version7c_optimize"
            ).click()
            app = app.run(timeout=25)
            self.assertGreaterEqual(len(self._display_plan_frames(app)), 2)
            self.assertGreaterEqual(
                sum("version7c" in str(item.key) for item in app.download_button),
                2,
            )

            next(
                item
                for item in app.selectbox
                if item.key == "version7c_backtest_version"
            ).select("Version6")
            app = app.run(timeout=25)
            next(
                button
                for button in app.button
                if button.key == "version7c_backtest"
            ).click()
            app = app.run(timeout=25)
            version6_results = app.session_state["version7c_backtest_results"]
            self.assertTrue(
                all(item.evaluated_rounds == 1 for item in version6_results)
            )
            self.assertTrue(
                all(not item.payout_data_available for item in version6_results)
            )

            next(
                item
                for item in app.selectbox
                if item.key == "version7c_backtest_version"
            ).select("Version7-A")
            app = app.run(timeout=25)
            next(
                button
                for button in app.button
                if button.key == "version7c_backtest"
            ).click()
            app = app.run(timeout=25)

        generation = app.session_state[
            "version7c_version7a_history_generation"
        ]
        version7a_results = app.session_state["version7c_backtest_results"]
        self.assertEqual(generation.generated_round_count, 1)
        self.assertEqual(generation.generated_match_count, 13)
        self.assertEqual(generation.actual_result_count, 13)
        self.assertTrue(
            all(item.evaluated_rounds == 1 for item in version7a_results)
        )
        self.assertTrue(
            all(not item.payout_data_available for item in version7a_results)
        )
        self.assertTrue(
            any(
                "払戻データなし：払戻金・収支・ROIは推測せず算出していません。"
                == item.value
                for item in app.info
            )
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_version7b_without_saved_history_is_not_regenerated(self) -> None:
        with patch(
            "analysis.collect_historical_matches",
            side_effect=AssertionError("Version7-B must not be regenerated"),
        ) as collect_mock:
            app = self._predicted_app()
            next(
                item
                for item in app.selectbox
                if item.key == "version7c_backtest_version"
            ).select("Version7-B")
            app = app.run(timeout=25)
            next(
                button
                for button in app.button
                if button.key == "version7c_backtest"
            ).click()
            app = app.run(timeout=25)

        collect_mock.assert_not_called()
        self.assertTrue(
            any(
                "Version7-Bは当時保存された予測履歴が必要です。"
                in warning.value
                for warning in app.warning
            )
        )
        self.assertTrue(self.history_frame.empty)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)


if __name__ == "__main__":
    unittest.main()
