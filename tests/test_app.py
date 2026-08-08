"""Streamlit画面、自動入力、Elo補正、CSV保存を確認する。"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from data_loader import (
    JAPAN_TIMEZONE,
    CsvMatchDataSource,
    OfficialDataBundle,
    OfficialMatch,
)
from history_manager import TotoMatch, TotoRound, TotoRoundLoadResult
from teams import TEAM_OPTIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_CSV_PATH = PROJECT_ROOT / "data" / "matches.csv"


NO_TOTO_ROUND = TotoRoundLoadResult(
    toto_round=None,
    source_name="エラー",
    status="error",
    message="toto開催回を取得できませんでした。13試合は手入力できます。",
)


def completed_analysis_history() -> pd.DataFrame:
    rows = []
    for version in ("Version4", "Version5", "Version6"):
        for match_number in range(1, 14):
            actual_result = ("1", "0", "2")[(match_number - 1) % 3]
            rows.append(
                {
                    "toto_round": 1548,
                    "toto_match_number": match_number,
                    "prediction_version": version,
                    "prediction": actual_result,
                    "actual_result": actual_result,
                    "probability_1": 0.6 if actual_result == "1" else 0.2,
                    "probability_0": 0.6 if actual_result == "0" else 0.2,
                    "probability_2": 0.6 if actual_result == "2" else 0.2,
                    "stake_yen": 100,
                    "payout_yen": 0,
                }
            )
    return pd.DataFrame(rows)


class StreamlitAppTest(unittest.TestCase):
    def setUp(self) -> None:
        st.cache_data.clear()
        TEST_CSV_PATH.write_text(
            (
                "match_number,match_date,home_team,away_team,"
                "home_scored,home_conceded,away_scored,away_conceded,"
                "home_recent_matches,away_recent_matches,"
                "home_rank,away_rank,home_played,home_wins,home_draws,"
                "home_losses,home_goals_for,home_goals_against,"
                "away_played,away_wins,away_draws,away_losses,"
                "away_goals_for,away_goals_against\n"
                "1,2026-08-07,鹿島アントラーズ,浦和レッズ,"
                "2.0,0.8,1.4,1.2,2026-07-30 H vs 柏レイソル 2-0,"
                "2026-07-30 A vs 柏レイソル 1-1,2,6,10,8,1,1,"
                "18,6,10,4,3,3,12,11\n"
                "2,2026-08-08,ヴィッセル神戸,ガンバ大阪,"
                "1.9,0.7,1.1,1.6,2026-07-31 H vs セレッソ大阪 3-1,"
                "2026-07-31 A vs セレッソ大阪 0-2,1,8,10,7,2,1,"
                "19,9,10,3,4,3,13,14\n"
            ),
            encoding="utf-8",
        )
        self.source_patcher = patch(
            "data_loader.get_default_data_sources",
            return_value=(CsvMatchDataSource(TEST_CSV_PATH),),
        )
        self.source_patcher.start()
        self.toto_patcher = patch(
            "history_manager.TotoHistoryManager.load_current_round",
            return_value=NO_TOTO_ROUND,
        )
        self.toto_patcher.start()
        self.history_patcher = patch(
            "prediction_history.PredictionHistoryManager.load",
            return_value=pd.DataFrame(),
        )
        self.history_patcher.start()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.environment_patcher = patch.dict(
            os.environ,
            {
                "JLEAGUE_ELO_CACHE_PATH": str(
                    Path(self.temp_directory.name) / "elo.json"
                )
            },
        )
        self.environment_patcher.start()

    def tearDown(self) -> None:
        self.source_patcher.stop()
        self.toto_patcher.stop()
        self.history_patcher.stop()
        self.environment_patcher.stop()
        self.temp_directory.cleanup()
        TEST_CSV_PATH.unlink(missing_ok=True)
        st.cache_data.clear()

    def test_team_selection_and_thirteen_match_prediction_flow(self) -> None:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(
            [tab.label for tab in app.tabs],
            ["予想", "分析", "引分分析"],
        )
        self.assertEqual(len(app.selectbox), 29)
        self.assertEqual(len(app.number_input), 53)
        self.assertEqual(len(app.toggle), 5)
        self.assertEqual(app.number_input[0].value, 2.0)
        self.assertEqual(app.number_input[1].value, 0.8)
        self.assertEqual(app.number_input[2].value, 1.4)
        self.assertEqual(app.number_input[3].value, 1.2)
        self.assertTrue(
            any(
                warning.value
                == "Eloデータを取得できないため、Elo補正なしで計算しました。"
                for warning in app.warning
            )
        )
        self.assertTrue(
            any(
                "試合日時：2026-08-07" in caption.value
                for caption in app.caption
            )
        )
        self.assertTrue(
            any(
                "2位｜ホーム成績：10試合 8勝1分1敗 18得点6失点"
                in caption.value
                for caption in app.caption
            )
        )

        # チーム変更で、そのクラブの平均値・順位・会場成績へ更新される。
        selected_vissel = next(
            option for option in TEAM_OPTIONS if option[1] == "ヴィッセル神戸"
        )
        app.selectbox[0].select(selected_vissel)
        app.run(timeout=20)

        self.assertEqual(app.number_input[0].value, 1.9)
        self.assertEqual(app.number_input[1].value, 0.7)
        self.assertTrue(
            any(
                "1位｜ホーム成績：10試合 7勝2分1敗 19得点9失点"
                in caption.value
                for caption in app.caption
            )
        )

        # 自動入力後もユーザーが平均値を修正できる。
        app.number_input[0].set_value(2.3)
        app.run(timeout=20)
        self.assertEqual(app.number_input[0].value, 2.3)

        # 13試合すべてのホーム・アウェイを選択して予想を実行する。
        team_selectboxes = [
            selectbox
            for selectbox in app.selectbox
            if str(selectbox.key).startswith(("home_team_", "away_team_"))
        ]
        for option_number, selectbox in enumerate(team_selectboxes):
            selectbox.select(TEAM_OPTIONS[option_number % len(TEAM_OPTIONS)])

        app.button[0].click()
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertTrue(
            any(
                message.value == "13試合の予想が完了しました。"
                for message in app.success
            )
        )
        self.assertEqual(len(app.dataframe), 2)
        self.assertEqual(len(app.download_button), 1)

        result_df = app.session_state["latest_prediction_results"]
        expected_csv_columns = {
            "toto_round",
            "toto_match_number",
            "prediction_version",
            "actual_result",
            "hit",
            "total_hits",
            "accuracy",
            "prediction_date",
            "home_elo",
            "away_elo",
            "elo_difference",
            "home_expected_before_elo",
            "away_expected_before_elo",
            "home_expected_after_elo",
            "away_expected_after_elo",
            "elo_adjustment_enabled",
            "home_rank",
            "away_rank",
            "home_points",
            "away_points",
            "home_goal_difference",
            "away_goal_difference",
            "home_points_per_match",
            "away_points_per_match",
            "home_recent_scored_average",
            "home_recent_conceded_average",
            "away_recent_scored_average",
            "away_recent_conceded_average",
            "home_recent_weighted_scored",
            "home_recent_weighted_conceded",
            "away_recent_weighted_scored",
            "away_recent_weighted_conceded",
            "home_home_scored_average",
            "home_home_conceded_average",
            "away_away_scored_average",
            "away_away_conceded_average",
            "home_expected_before_version5",
            "away_expected_before_version5",
            "home_expected_after_version5",
            "away_expected_after_version5",
            "venue_adjustment_enabled",
            "recent_weighting_enabled",
            "standings_adjustment_enabled",
            "version4_prediction",
            "version5_prediction",
            "version6_prediction",
            "version7a_prediction",
            "version7a_home_win",
            "version7a_draw",
            "version7a_away_win",
            "draw_candidate",
            "draw_candidate_reasons",
            "prediction_changed",
        }
        self.assertTrue(expected_csv_columns.issubset(result_df.columns))
        self.assertTrue(
            ((result_df[["1", "0", "2"]].sum(axis=1) - 100.0).abs() < 1e-9).all()
        )
        self.assertFalse(result_df[["1", "0", "2"]].isna().any().any())
        self.assertTrue((result_df[["1", "0", "2"]] >= 0.0).all().all())
        self.assertEqual(len(result_df), 13)
        self.assertTrue(
            (
                result_df["home_expected_before_elo"]
                == result_df["home_expected_after_elo"]
            ).all()
        )
        self.assertFalse(result_df["elo_adjustment_enabled"].any())

    def test_toto_round_controls_input_result_and_csv_order(self) -> None:
        team_names = [team_name for _, team_name in TEAM_OPTIONS]
        official_cards = [
            (team_names[(12 - index) * 2], team_names[(12 - index) * 2 + 1])
            for index in range(13)
        ]
        kickoff = datetime(2026, 8, 8, 14, 0, tzinfo=JAPAN_TIMEZONE)
        toto_round = TotoRound(
            round_id=1644,
            matches=tuple(
                TotoMatch(
                    round_id=1644,
                    match_number=index,
                    home_team=home_team,
                    away_team=away_team,
                    match_time=kickoff + timedelta(minutes=index),
                )
                for index, (home_team, away_team) in enumerate(
                    official_cards,
                    start=1,
                )
            ),
        )
        loaded_round = TotoRoundLoadResult(
            toto_round=toto_round,
            source_name="toto公式",
            status="loaded",
            message="toto公式順を読み込みました。",
        )

        with patch(
            "history_manager.TotoHistoryManager.load_current_round",
            return_value=loaded_round,
        ), patch(
            "prediction_history.PredictionHistoryManager.save_prediction_results",
            return_value=True,
        ):
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)
            team_selectboxes = [
                selectbox
                for selectbox in app.selectbox
                if str(selectbox.key).startswith(("home_team_", "away_team_"))
            ]
            displayed_cards = [
                (
                    team_selectboxes[index * 2].value[1],
                    team_selectboxes[index * 2 + 1].value[1],
                )
                for index in range(13)
            ]
            self.assertEqual(displayed_cards, official_cards)

            app.button[0].click()
            app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        result_df = app.session_state["latest_prediction_results"]
        self.assertEqual(result_df["toto_match_number"].tolist(), list(range(1, 14)))
        self.assertEqual(result_df["toto_round"].tolist(), [1644] * 13)
        self.assertEqual(
            result_df["prediction_version"].tolist(),
            ["Version7-A"] * 13,
        )
        self.assertEqual(
            result_df["対戦カード"].tolist(),
            [f"{home} vs {away}" for home, away in official_cards],
        )

    def test_analysis_tab_renders_tables_metrics_and_all_graphs(self) -> None:
        with patch(
            "prediction_history.PredictionHistoryManager.load",
            return_value=completed_analysis_history(),
        ):
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(
            [tab.label for tab in app.tabs],
            ["予想", "分析", "引分分析"],
        )
        self.assertEqual(len(app.metric), 8)
        self.assertEqual(len(app.get("vega_lite_chart")), 5)
        self.assertEqual(len(app.dataframe), 3)
        analysis_subheaders = {item.value for item in app.subheader}
        self.assertTrue(
            {
                "開催回一覧",
                "Version比較",
                "開催回別的中数",
                "累積的中率",
                "Version比較（Brier Score）",
                "1・0・2別正答率の推移",
                "ホーム・引分・アウェイ予測割合",
                "Calibration",
            }.issubset(analysis_subheaders)
        )

    def test_rank_and_venue_record_can_be_edited(self) -> None:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)

        edit_toggle = next(
            toggle
            for toggle in app.toggle
            if toggle.key == "edit_detail_stats"
        )
        edit_toggle.set_value(True)
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.selectbox), 30)
        # 52平均値 + 順位表18 + 会場別10
        self.assertEqual(len(app.number_input), 81)

        rank_input = next(
            item
            for item in app.number_input
            if item.key == "home_rank_1"
        )
        wins_input = next(
            item
            for item in app.number_input
            if item.key == "home_wins_1"
        )
        rank_input.set_value(3)
        wins_input.set_value(9)
        app.run(timeout=20)

        self.assertTrue(
            any(
                "3位｜ホーム成績：11試合 9勝1分1敗"
                in caption.value
                for caption in app.caption
            )
        )


class StreamlitManualFallbackTest(unittest.TestCase):
    def test_manual_mode_shows_only_safe_message(self) -> None:
        st.cache_data.clear()
        missing_csv = CsvMatchDataSource(
            PROJECT_ROOT / "data" / "not-created.csv"
        )

        with patch(
            "data_loader.get_default_data_sources",
            return_value=(missing_csv,),
        ), patch(
            "history_manager.TotoHistoryManager.load_current_round",
            return_value=NO_TOTO_ROUND,
        ), patch(
            "prediction_history.PredictionHistoryManager.load",
            return_value=pd.DataFrame(),
        ):
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertIn(
            "手入力モードで起動しました。",
            [message.value for message in app.info],
        )
        self.assertTrue(
            any(
                warning.value
                == "Eloデータを取得できないため、Elo補正なしで計算しました。"
                for warning in app.warning
            )
        )


class OfficialBundleSource:
    """画面統合テスト用に完了試合履歴も返す取得元。"""

    name = "テスト公式データ"

    def __init__(self, matches: pd.DataFrame, now: datetime) -> None:
        self.matches = matches
        self.now = now

    def load_bundle(self) -> OfficialDataBundle:
        completed_matches = tuple(
            OfficialMatch(
                match_time=self.now - timedelta(days=match_number),
                home_team="鹿島アントラーズ",
                away_team="浦和レッズ",
                home_goals=2 if match_number < 4 else 1,
                away_goals=0 if match_number < 4 else 1,
                category="J1",
            )
            for match_number in range(1, 6)
        )
        return OfficialDataBundle(
            matches=self.matches,
            completed_matches=completed_matches,
            fetched_at=self.now,
        )


class StreamlitEloIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        st.cache_data.clear()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.environment_patcher = patch.dict(
            os.environ,
            {
                "JLEAGUE_ELO_CACHE_PATH": str(
                    Path(self.temp_directory.name) / "elo.json"
                )
            },
        )
        self.environment_patcher.start()
        now = datetime(2026, 8, 1, 12, 0, tzinfo=JAPAN_TIMEZONE)
        matches = pd.DataFrame(
            [
                {
                    "match_number": 1,
                    "match_date": "2026-08-07",
                    "home_team": "鹿島アントラーズ",
                    "away_team": "浦和レッズ",
                    "home_scored": 1.8,
                    "home_conceded": 0.8,
                    "away_scored": 1.2,
                    "away_conceded": 1.4,
                    "home_recent_matches": (
                        "2026-07-31 H vs 浦和レッズ 3-0 / "
                        "2026-07-24 A vs 浦和レッズ 2-1 / "
                        "2026-07-17 H vs 浦和レッズ 1-0 / "
                        "2026-07-10 A vs 浦和レッズ 1-1 / "
                        "2026-07-03 H vs 浦和レッズ 0-1"
                    ),
                    "away_recent_matches": (
                        "2026-07-31 A vs 鹿島アントラーズ 0-3 / "
                        "2026-07-24 H vs 鹿島アントラーズ 1-2 / "
                        "2026-07-17 A vs 鹿島アントラーズ 0-1 / "
                        "2026-07-10 H vs 鹿島アントラーズ 1-1 / "
                        "2026-07-03 A vs 鹿島アントラーズ 1-0"
                    ),
                    "home_rank": 1,
                    "away_rank": 10,
                    "home_points": 28,
                    "away_points": 12,
                    "home_goal_difference": 14,
                    "away_goal_difference": -5,
                    "home_season_played": 12,
                    "away_season_played": 12,
                    "home_season_wins": 9,
                    "home_season_draws": 1,
                    "home_season_losses": 2,
                    "away_season_wins": 3,
                    "away_season_draws": 3,
                    "away_season_losses": 6,
                    "home_season_goals_for": 25,
                    "home_season_goals_against": 11,
                    "away_season_goals_for": 12,
                    "away_season_goals_against": 17,
                    "home_played": 6,
                    "home_wins": 5,
                    "home_draws": 1,
                    "home_losses": 0,
                    "home_goals_for": 15,
                    "home_goals_against": 4,
                    "away_played": 6,
                    "away_wins": 1,
                    "away_draws": 2,
                    "away_losses": 3,
                    "away_goals_for": 5,
                    "away_goals_against": 11,
                }
            ]
        )
        self.source_patcher = patch(
            "data_loader.get_default_data_sources",
            return_value=(OfficialBundleSource(matches, now),),
        )
        self.source_patcher.start()
        self.toto_patcher = patch(
            "history_manager.TotoHistoryManager.load_current_round",
            return_value=NO_TOTO_ROUND,
        )
        self.toto_patcher.start()
        self.history_patcher = patch(
            "prediction_history.PredictionHistoryManager.load",
            return_value=pd.DataFrame(),
        )
        self.history_patcher.start()

    def tearDown(self) -> None:
        self.source_patcher.stop()
        self.toto_patcher.stop()
        self.history_patcher.stop()
        self.environment_patcher.stop()
        self.temp_directory.cleanup()
        TEST_CSV_PATH.unlink(missing_ok=True)
        st.cache_data.clear()

    def _select_all_teams(self, app: AppTest) -> None:
        team_selectboxes = [
            selectbox
            for selectbox in app.selectbox
            if str(selectbox.key).startswith(("home_team_", "away_team_"))
        ]
        for option_number, selectbox in enumerate(team_selectboxes):
            if selectbox.value is None:
                selectbox.select(
                    TEAM_OPTIONS[option_number % len(TEAM_OPTIONS)]
                )

    def test_elo_on_off_list_and_csv_columns(self) -> None:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(len(app.toggle), 5)
        self.assertEqual(len(app.selectbox), 30)
        self.assertEqual(len(app.dataframe), 1)
        elo_table = app.dataframe[0].value
        self.assertEqual(len(elo_table), 60)
        self.assertEqual(
            list(elo_table.columns),
            [
                "カテゴリー",
                "順位",
                "チーム名",
                "Elo",
                "対象試合数",
                "最終更新日",
            ],
        )
        self.assertGreaterEqual(
            elo_table.iloc[0]["Elo"],
            elo_table.iloc[1]["Elo"],
        )
        self.assertFalse(
            any("Eloデータを取得できない" in item.value for item in app.warning)
        )

        self._select_all_teams(app)
        app.button[0].click()
        app.run(timeout=20)

        result_on = app.session_state["latest_prediction_results"]
        self.assertEqual(len(result_on), 13)
        self.assertTrue(result_on.iloc[0]["elo_adjustment_enabled"])
        self.assertTrue(result_on.iloc[0]["venue_adjustment_enabled"])
        self.assertTrue(result_on.iloc[0]["recent_weighting_enabled"])
        self.assertTrue(result_on.iloc[0]["standings_adjustment_enabled"])
        self.assertNotEqual(
            result_on.iloc[0]["home_expected_before_elo"],
            result_on.iloc[0]["home_expected_after_elo"],
        )
        self.assertEqual(len(app.download_button), 1)
        self.assertEqual(len(app.dataframe), 3)

        elo_toggle = next(
            toggle
            for toggle in app.toggle
            if toggle.key == "use_elo_adjustment"
        )
        elo_toggle.set_value(False)
        app.run(timeout=20)
        app.button[0].click()
        app.run(timeout=20)

        result_off = app.session_state["latest_prediction_results"]
        self.assertFalse(result_off["elo_adjustment_enabled"].any())
        self.assertTrue(
            (
                result_off["home_expected_before_elo"]
                == result_off["home_expected_after_elo"]
            ).all()
        )

    def test_version5_switches_off_reproduce_version4(self) -> None:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)
        self._select_all_teams(app)

        for toggle_key in (
            "use_venue_adjustment",
            "use_recent_weighting",
            "use_standings_adjustment",
        ):
            next(toggle for toggle in app.toggle if toggle.key == toggle_key).set_value(
                False
            )

        app.run(timeout=20)
        app.button[0].click()
        app.run(timeout=20)

        results = app.session_state["latest_prediction_results"]
        self.assertEqual(len(results), 13)
        self.assertTrue(results.iloc[0]["elo_adjustment_enabled"])
        self.assertFalse(results["venue_adjustment_enabled"].any())
        self.assertFalse(results["recent_weighting_enabled"].any())
        self.assertFalse(results["standings_adjustment_enabled"].any())
        self.assertTrue(
            (
                results["version4_prediction"]
                == results["version5_prediction"]
            ).all()
        )
        self.assertTrue(
            (
                results["home_expected_before_version5"]
                == results["home_expected_after_version5"]
            ).all()
        )


if __name__ == "__main__":
    unittest.main()
