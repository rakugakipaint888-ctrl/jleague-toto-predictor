"""Streamlit画面、チーム連動、自動切替、予想フローを確認する。"""

import os
import unittest
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from teams import TEAM_OPTIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_CSV_PATH = PROJECT_ROOT / "data" / "matches.csv"


class StreamlitAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_key = os.environ.pop("API_FOOTBALL_KEY", None)
        st.cache_data.clear()
        TEST_CSV_PATH.write_text(
            (
                "match_number,match_date,home_team,away_team,"
                "home_scored,home_conceded,away_scored,away_conceded,"
                "home_recent_matches,away_recent_matches\n"
                "1,2026-08-07,鹿島アントラーズ,浦和レッズ,"
                "2.0,0.8,1.4,1.2,2026-07-30 H vs 柏レイソル 2-0,"
                "2026-07-30 A vs 柏レイソル 1-1\n"
                "2,2026-08-08,ヴィッセル神戸,ガンバ大阪,"
                "1.9,0.7,1.1,1.6,2026-07-31 H vs セレッソ大阪 3-1,"
                "2026-07-31 A vs セレッソ大阪 0-2\n"
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        TEST_CSV_PATH.unlink(missing_ok=True)
        st.cache_data.clear()

        if self.original_api_key is not None:
            os.environ["API_FOOTBALL_KEY"] = self.original_api_key

    def test_team_selection_and_thirteen_match_prediction_flow(self) -> None:
        app = AppTest.from_file(
            str(PROJECT_ROOT / "app.py"),
        ).run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(len(app.selectbox), 26)
        self.assertEqual(len(app.number_input), 52)
        self.assertEqual(app.number_input[0].value, 2.0)
        self.assertEqual(app.number_input[1].value, 0.8)
        self.assertEqual(app.number_input[2].value, 1.4)
        self.assertEqual(app.number_input[3].value, 1.2)
        self.assertTrue(
            any("試合日：2026-08-07" in caption.value for caption in app.caption)
        )

        # チーム変更で、そのクラブの平均得点・平均失点へ自動更新される。
        selected_vissel = next(
            option for option in TEAM_OPTIONS if option[1] == "ヴィッセル神戸"
        )
        app.selectbox[0].select(selected_vissel)
        app.run(timeout=20)

        self.assertEqual(app.number_input[0].value, 1.9)
        self.assertEqual(app.number_input[1].value, 0.7)

        # 自動入力後もユーザーが修正でき、同じチームなら値を保持する。
        app.number_input[0].set_value(2.3)
        app.run(timeout=20)
        self.assertEqual(app.number_input[0].value, 2.3)

        # 13試合すべてのホーム・アウェイを選択して予想を実行する。
        for option_number, selectbox in enumerate(app.selectbox):
            selectbox.select(
                TEAM_OPTIONS[option_number % len(TEAM_OPTIONS)]
            )

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


if __name__ == "__main__":
    unittest.main()
