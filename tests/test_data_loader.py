"""data_loader.pyのAPI・CSV・手入力切替を確認するテスト。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from data_loader import (
    ApiDataSource,
    CsvMatchDataSource,
    MatchDataSourceError,
    TEAM_NAME_ALIASES,
    get_match_defaults,
    load_matches,
)
from teams import J1, J2, J3


class DataFrameSource:
    """任意のDataFrameを返すテスト用取得元。"""

    name = "テストデータ"

    def __init__(self, matches: pd.DataFrame) -> None:
        self.matches = matches

    def load(self) -> pd.DataFrame:
        return self.matches


class BrokenSource:
    """通信失敗を再現するテスト用取得元。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def load(self) -> pd.DataFrame:
        raise MatchDataSourceError("画面へ表示しない技術的な内容")


class FakeResponse:
    """requests.Responseの必要部分だけを再現する。"""

    def __init__(self, response_items: list[dict]) -> None:
        self.response_items = response_items

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "errors": [],
            "response": self.response_items,
        }


def create_fixture(
    date: str,
    status: str,
    home_team: str,
    away_team: str,
    home_goals=None,
    away_goals=None,
) -> dict:
    """API-Football形式の最小試合データを作る。"""

    return {
        "fixture": {
            "date": date,
            "status": {"short": status},
        },
        "teams": {
            "home": {"name": home_team},
            "away": {"name": away_team},
        },
        "goals": {
            "home": home_goals,
            "away": away_goals,
        },
    }


class DataLoaderTest(unittest.TestCase):
    def test_missing_csv_uses_manual_input_mode(self) -> None:
        missing_path = Path("not-created") / "matches.csv"

        result = load_matches(CsvMatchDataSource(missing_path))

        self.assertEqual(result.status, "missing")
        self.assertTrue(result.matches.empty)

        defaults = get_match_defaults(result.matches, 1)
        self.assertEqual(defaults["home_team"], "")
        self.assertEqual(defaults["home_scored"], 1.4)
        self.assertEqual(defaults["match_date"], "")

    def test_csv_is_loaded_and_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            csv_path = Path(temp_directory) / "matches.csv"
            csv_path.write_text(
                "home_team,away_team,home_scored,match_date\n"
                "鹿島アントラーズ,浦和レッズ,1.8,2026-08-07\n",
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
        self.assertEqual(defaults["match_date"], "2026-08-07")

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

    def test_match_data_source_interface_is_unchanged(self) -> None:
        source = DataFrameSource(
            pd.DataFrame(
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
        )

        result = load_matches(source)

        self.assertTrue(result.is_loaded)
        self.assertEqual(result.source_name, "テストデータ")
        self.assertEqual(result.matches.iloc[0]["home_scored"], 2.0)

    def test_api_calculates_last_five_and_upcoming_fixture(self) -> None:
        leagues = [
            {
                "league": {"id": league_id, "name": league_name},
                "seasons": [{"year": 2026, "current": True}],
            }
            for league_id, league_name in enumerate(
                ("J1 League", "J2 League", "J3 League"),
                start=98,
            )
        ]
        completed_scores = [(1, 0), (2, 1), (0, 1), (3, 2), (4, 1)]
        j1_fixtures = [
            create_fixture(
                f"2026-07-{match_day:02d}T10:00:00+00:00",
                "FT",
                "Kashima Antlers",
                "Urawa Red Diamonds",
                home_goals,
                away_goals,
            )
            for match_day, (home_goals, away_goals) in enumerate(
                completed_scores,
                start=1,
            )
        ]
        j1_fixtures.append(
            create_fixture(
                "2026-08-07T10:00:00+00:00",
                "NS",
                "Kashima Antlers",
                "Urawa Red Diamonds",
            )
        )
        request_log = []

        def fake_get(url, headers, params, timeout):
            request_log.append((url, params.copy()))

            if url.endswith("/leagues"):
                return FakeResponse(leagues)

            if (
                url.endswith("/fixtures")
                and params["league"] == 98
                and params["season"] == 2026
            ):
                return FakeResponse(j1_fixtures)

            return FakeResponse([])

        source = ApiDataSource(
            api_key="test-key",
            now=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )

        with patch("data_loader.requests.get", side_effect=fake_get):
            result = load_matches(source)

        self.assertTrue(result.is_loaded)
        self.assertEqual(len(request_log), 7)
        self.assertEqual(result.matches.iloc[0]["match_date"], "2026-08-07")
        self.assertEqual(
            result.matches.iloc[0]["home_team"],
            "鹿島アントラーズ",
        )
        self.assertEqual(result.matches.iloc[0]["home_scored"], 2.0)
        self.assertEqual(result.matches.iloc[0]["home_conceded"], 1.0)
        self.assertEqual(
            len(result.team_stats["鹿島アントラーズ"].recent_matches),
            5,
        )
        self.assertIn(
            "2026-07-05 H vs 浦和レッズ 4-1",
            result.team_stats["鹿島アントラーズ"].recent_matches,
        )

    def test_default_order_falls_back_from_api_to_csv(self) -> None:
        csv_like_source = DataFrameSource(
            pd.DataFrame(
                [
                    {
                        "home_team": "鹿島アントラーズ",
                        "away_team": "浦和レッズ",
                    }
                ]
            )
        )

        with patch(
            "data_loader.get_default_data_sources",
            return_value=(BrokenSource("テストAPI"), csv_like_source),
        ):
            result = load_matches()

        self.assertTrue(result.is_loaded)
        self.assertEqual(result.source_name, "テストデータ")
        self.assertIn("テストAPIを利用できなかったため", result.message)
        self.assertNotIn("技術的な内容", result.message)

    def test_all_sources_failure_uses_manual_input_without_error_detail(self) -> None:
        with patch(
            "data_loader.get_default_data_sources",
            return_value=(BrokenSource("API"), BrokenSource("CSV")),
        ):
            result = load_matches()

        self.assertEqual(result.status, "manual")
        self.assertTrue(result.matches.empty)
        self.assertEqual(result.source_name, "手入力")
        self.assertNotIn("技術的な内容", result.message)

    def test_api_without_key_does_not_make_network_request(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("data_loader.requests.get") as request_get:
                result = load_matches(ApiDataSource())

        self.assertEqual(result.status, "missing")
        request_get.assert_not_called()

    def test_all_sixty_teams_have_api_name_mapping(self) -> None:
        self.assertEqual(set(TEAM_NAME_ALIASES), set(J1 + J2 + J3))
        self.assertEqual(len(TEAM_NAME_ALIASES), 60)


if __name__ == "__main__":
    unittest.main()
