"""data_loader.py の基本動作を確認するテスト。"""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data_loader import (
    CsvMatchDataSource,
    get_match_defaults,
    load_matches,
)


class FakeApiDataSource:
    """将来のAPIデータ取得元を想定したテスト用クラス。"""

    @property
    def name(self) -> str:
        return "テストAPI"

    def load(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "match_number": 1,
                    "home_team": "鹿島アントラーズ",
                    "away_team": "浦和レッズ",
                    "home_scored": 2.0,
                    "home_conceded": 0.8,
                    "away_scored": 1.4,
                    "away_conceded": 1.2,
                }
            ]
        )


class DataLoaderTest(unittest.TestCase):
    def test_missing_csv_uses_manual_input_mode(self) -> None:
        missing_path = Path("not-created") / "matches.csv"

        result = load_matches(CsvMatchDataSource(missing_path))

        self.assertEqual(result.status, "missing")
        self.assertTrue(result.matches.empty)

        defaults = get_match_defaults(result.matches, 1)
        self.assertEqual(defaults["home_team"], "")
        self.assertEqual(defaults["home_scored"], 1.4)

    def test_csv_is_loaded_and_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            csv_path = Path(temp_directory) / "matches.csv"
            csv_path.write_text(
                "home_team,away_team,home_scored\n"
                "鹿島アントラーズ,浦和レッズ,1.8\n",
                encoding="utf-8",
            )

            result = load_matches(CsvMatchDataSource(csv_path))

        self.assertTrue(result.is_loaded)
        self.assertEqual(len(result.matches), 1)

        defaults = get_match_defaults(result.matches, 1)
        self.assertEqual(defaults["home_team"], "鹿島アントラーズ")
        self.assertEqual(defaults["away_team"], "浦和レッズ")
        self.assertEqual(defaults["home_scored"], 1.8)
        self.assertEqual(defaults["home_conceded"], 1.2)

    def test_invalid_csv_falls_back_without_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            csv_path = Path(temp_directory) / "matches.csv"
            csv_path.write_text(
                "team,score\n鹿島アントラーズ,2\n",
                encoding="utf-8",
            )

            result = load_matches(CsvMatchDataSource(csv_path))

        self.assertEqual(result.status, "error")
        self.assertTrue(result.matches.empty)

    def test_api_source_can_use_the_same_loader(self) -> None:
        result = load_matches(FakeApiDataSource())

        self.assertTrue(result.is_loaded)
        self.assertEqual(result.source_name, "テストAPI")
        self.assertEqual(result.matches.iloc[0]["home_scored"], 2.0)


if __name__ == "__main__":
    unittest.main()
