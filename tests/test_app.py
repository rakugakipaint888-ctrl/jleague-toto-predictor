"""Streamlit画面、自動入力、修正、予想フローを確認する。"""

import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from data_loader import CsvMatchDataSource
from teams import TEAM_OPTIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_CSV_PATH = PROJECT_ROOT / "data" / "matches.csv"


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

    def tearDown(self) -> None:
        self.source_patcher.stop()
        TEST_CSV_PATH.unlink(missing_ok=True)
        st.cache_data.clear()

    def test_team_selection_and_thirteen_match_prediction_flow(self) -> None:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(len(app.selectbox), 26)
        self.assertEqual(len(app.number_input), 52)
        self.assertEqual(len(app.toggle), 1)
        self.assertEqual(app.number_input[0].value, 2.0)
        self.assertEqual(app.number_input[1].value, 0.8)
        self.assertEqual(app.number_input[2].value, 1.4)
        self.assertEqual(app.number_input[3].value, 1.2)
        self.assertTrue(
            any(
                "試合日：2026-08-07" in caption.value
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
        for option_number, selectbox in enumerate(app.selectbox):
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
        self.assertEqual(len(app.dataframe), 1)
        self.assertEqual(len(app.download_button), 1)

    def test_rank_and_venue_record_can_be_edited(self) -> None:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)

        app.toggle[0].set_value(True)
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.selectbox), 27)
        # 52平均値 + 順位2 + 勝分敗・得失点10
        self.assertEqual(len(app.number_input), 64)

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
        ):
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(
            [message.value for message in app.info],
            ["手入力モードで起動しました。"],
        )


if __name__ == "__main__":
    unittest.main()
