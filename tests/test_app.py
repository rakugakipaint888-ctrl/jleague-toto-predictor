"""Streamlit画面とVersion 1の予想フローを確認するテスト。"""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from teams import TEAM_OPTIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StreamlitAppTest(unittest.TestCase):
    def test_thirteen_match_prediction_flow(self) -> None:
        app = AppTest.from_file(
            str(PROJECT_ROOT / "app.py"),
        ).run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.selectbox), 26)
        self.assertEqual(len(app.number_input), 52)

        # 13試合すべてのホーム・アウェイを選択して予想を実行する。
        for option_number, selectbox in enumerate(app.selectbox):
            selectbox.select(
                TEAM_OPTIONS[option_number % len(TEAM_OPTIONS)]
            )

        app.button[0].click()
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertTrue(
            any(
                message.value == "13試合の予想が完了しました。"
                for message in app.success
            )
        )
        self.assertEqual(len(app.dataframe), 1)


if __name__ == "__main__":
    unittest.main()
