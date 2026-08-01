"""data_loader.pyの公式取得・CSV・手入力切替を確認する。"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import data_loader
from data_loader import (
    JAPAN_TIMEZONE,
    CsvMatchDataSource,
    JLeagueOfficialDataSource,
    MatchDataSourceError,
    OFFICIAL_TEAM_ABBREVIATIONS,
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
    """取得失敗を再現するテスト用取得元。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def load(self) -> pd.DataFrame:
        raise MatchDataSourceError("画面へ表示しない技術的な内容")


class UnexpectedBrokenSource:
    """公式HTMLの想定外変更に相当する例外を再現する。"""

    name = "想定外エラー"

    def load(self) -> pd.DataFrame:
        raise RuntimeError("画面へ表示してはいけない例外")


class FakeResponse:
    """requests.ResponseのHTML取得に必要な部分だけを再現する。"""

    def __init__(self, html: str) -> None:
        self.text = html

    def raise_for_status(self) -> None:
        return None


def schedule_html(rows: list[dict]) -> str:
    """J. League Data Siteに近い日程表HTMLを作る。"""

    columns = [
        "シーズン",
        "大会",
        "節",
        "試合日",
        "K/O時刻",
        "ホーム",
        "スコア",
        "アウェイ",
        "スタジアム",
        "入場者数",
        "インターネット中継・TV放送",
    ]
    return pd.DataFrame(rows, columns=columns).to_html(index=False)


def schedule_row(
    date: str,
    home_team: str,
    score: str,
    away_team: str,
    kickoff: str = "19:00",
) -> dict:
    return {
        "シーズン": "2026/27",
        "大会": "Ｊ１",
        "節": "第1節第1日",
        "試合日": date,
        "K/O時刻": kickoff,
        "ホーム": home_team,
        "スコア": score,
        "アウェイ": away_team,
        "スタジアム": "テストスタジアム",
        "入場者数": "",
        "インターネット中継・TV放送": "",
    }


def standings_html(rows: list[dict]) -> str:
    return pd.DataFrame(rows, columns=["順位順位", "クラブ"]).to_html(
        index=False
    )


class DataLoaderTest(unittest.TestCase):
    def test_missing_csv_uses_safe_defaults(self) -> None:
        missing_path = Path("not-created") / "matches.csv"

        result = load_matches(CsvMatchDataSource(missing_path))

        self.assertEqual(result.status, "missing")
        self.assertTrue(result.matches.empty)

        defaults = get_match_defaults(result.matches, 1)
        self.assertEqual(defaults["home_team"], "")
        self.assertEqual(defaults["home_scored"], 1.4)
        self.assertEqual(defaults["match_date"], "")
        self.assertIsNone(defaults["home_rank"])
        self.assertEqual(defaults["home_wins"], 0)

    def test_csv_is_loaded_and_old_format_remains_compatible(self) -> None:
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
        self.assertIsNone(defaults["home_rank"])

    def test_csv_detail_fields_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            csv_path = Path(temp_directory) / "matches.csv"
            csv_path.write_text(
                (
                    "home_team,away_team,home_rank,away_rank,"
                    "home_wins,home_draws,home_losses,"
                    "away_wins,away_draws,away_losses\n"
                    "鹿島アントラーズ,浦和レッズ,2,6,8,1,1,4,3,3\n"
                ),
                encoding="utf-8",
            )
            result = load_matches(CsvMatchDataSource(csv_path))

        defaults = get_match_defaults(result.matches, 1)
        self.assertEqual(defaults["home_rank"], 2)
        self.assertEqual(defaults["away_rank"], 6)
        self.assertEqual(defaults["home_wins"], 8)
        self.assertEqual(defaults["away_losses"], 3)

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

    def test_official_source_calculates_stats_rank_and_venue_records(self) -> None:
        completed_scores = [(1, 0), (2, 1), (0, 1), (3, 2), (4, 1)]
        history_j1_rows = [
            schedule_row(
                f"26/07/{match_day:02d}(水)",
                "鹿島",
                f"{home_goals}-{away_goals}",
                "浦和",
            )
            for match_day, (home_goals, away_goals) in enumerate(
                completed_scores,
                start=1,
            )
        ]

        responses = {
            (
                "competition_years=2026&competition_frame_ids=1"
                "&competition_frame_ids=2&competition_frame_ids=3"
            ): schedule_html(
                [
                    schedule_row("26/08/07(金)", "鹿島", "vs", "浦和"),
                    schedule_row("26/08/08(土)", "札幌", "vs", "徳島"),
                    schedule_row("26/08/08(土)", "相模原", "vs", "熊本"),
                ]
            ),
            (
                "competition_years=20261&competition_frame_ids=35"
                "&competition_frame_ids=36"
            ): schedule_html(
                history_j1_rows
                + [schedule_row("26/06/06(土)", "仙台", "1-0", "山形")]
            ),
            "/j1/standings/": standings_html(
                [{"順位順位": 2, "クラブ": "鹿島アントラーズ"}]
            ),
            "/j2/standings/": standings_html(
                [{"順位順位": "-", "クラブ": "北海道コンサドーレ札幌"}]
            ),
            "/j3/standings/": standings_html(
                [{"順位順位": "-", "クラブ": "ＳＣ相模原"}]
            ),
        }
        request_log = []

        def fake_get(url, headers, timeout):
            request_log.append((url, headers, timeout))
            for url_part, html in responses.items():
                if url_part in url:
                    return FakeResponse(html)
            raise AssertionError(f"想定外のURL: {url}")

        source = JLeagueOfficialDataSource(
            now=datetime(2026, 8, 1, 12, 0, tzinfo=JAPAN_TIMEZONE),
        )

        with patch("data_loader.requests.get", side_effect=fake_get):
            result = load_matches(source)

        self.assertTrue(result.is_loaded)
        self.assertEqual(len(request_log), 5)
        self.assertTrue(
            all("API" not in str(headers) for _, headers, _ in request_log)
        )
        self.assertEqual(result.matches.iloc[0]["match_date"], "2026-08-07")
        self.assertEqual(result.matches.iloc[0]["home_team"], "鹿島アントラーズ")
        self.assertEqual(result.matches.iloc[0]["home_scored"], 2.0)
        self.assertEqual(result.matches.iloc[0]["home_conceded"], 1.0)
        self.assertEqual(result.matches.iloc[0]["home_rank"], 2)
        self.assertEqual(result.matches.iloc[0]["home_wins"], 4)
        self.assertEqual(result.matches.iloc[0]["home_losses"], 1)
        self.assertEqual(
            len(result.team_stats["鹿島アントラーズ"].recent_matches),
            5,
        )
        self.assertIn(
            "2026-07-05 H vs 浦和レッズ 4-1",
            result.team_stats["鹿島アントラーズ"].recent_matches,
        )

    def test_pk_score_uses_score_before_shootout(self) -> None:
        self.assertEqual(data_loader._parse_score("1-1 (PK1-4)"), (1, 1))
        self.assertIsNone(data_loader._parse_score("vs"))

    def test_default_order_falls_back_from_official_to_csv(self) -> None:
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
            return_value=(BrokenSource("公式データ"), csv_like_source),
        ):
            result = load_matches()

        self.assertTrue(result.is_loaded)
        self.assertEqual(result.source_name, "テストデータ")
        self.assertEqual(result.message, "テストデータから1試合を読み込みました。")
        self.assertNotIn("公式データ", result.message)
        self.assertNotIn("技術的な内容", result.message)

    def test_all_sources_failure_uses_exact_manual_message(self) -> None:
        with patch(
            "data_loader.get_default_data_sources",
            return_value=(BrokenSource("公式"), BrokenSource("CSV")),
        ):
            result = load_matches()

        self.assertEqual(result.status, "manual")
        self.assertTrue(result.matches.empty)
        self.assertEqual(result.source_name, "手入力")
        self.assertEqual(result.message, "手入力モードで起動しました。")
        self.assertNotIn("技術的な内容", result.message)

    def test_unexpected_source_error_is_hidden_and_falls_back(self) -> None:
        with patch(
            "data_loader.get_default_data_sources",
            return_value=(UnexpectedBrokenSource(), BrokenSource("CSV")),
        ):
            result = load_matches()

        self.assertEqual(result.status, "manual")
        self.assertEqual(result.message, "手入力モードで起動しました。")
        self.assertNotIn("例外", result.message)

    def test_all_sixty_teams_have_official_abbreviation_mapping(self) -> None:
        self.assertEqual(
            set(OFFICIAL_TEAM_ABBREVIATIONS.values()),
            set(J1 + J2 + J3),
        )
        self.assertEqual(len(OFFICIAL_TEAM_ABBREVIATIONS), 60)

    def test_api_football_implementation_is_removed(self) -> None:
        self.assertFalse(hasattr(data_loader, "ApiDataSource"))
        self.assertFalse(hasattr(data_loader, "API_FOOTBALL_KEY"))


if __name__ == "__main__":
    unittest.main()
