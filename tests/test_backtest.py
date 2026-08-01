"""Version6バックテストのデータリーク防止とVersion比較を確認する。"""

import unittest
from datetime import datetime, timedelta

from backtest import (
    calculate_team_stats_as_of,
    fetch_historical_matches,
    run_backtest,
)
from data_loader import JAPAN_TIMEZONE, OfficialMatch
from history_manager import TotoMatch, TotoPayouts, TotoRound


def completed_round() -> TotoRound:
    kickoff = datetime(2025, 6, 21, 15, 0, tzinfo=JAPAN_TIMEZONE)
    outcomes = ("1", "0", "2")
    matches = tuple(
        TotoMatch(
            round_id=1548,
            match_number=number,
            home_team="鹿島アントラーズ",
            away_team="浦和レッズ",
            match_time=kickoff + timedelta(minutes=number),
            actual_result=outcomes[(number - 1) % 3],
            home_goals=2,
            away_goals=1,
        )
        for number in range(1, 14)
    )
    return TotoRound(
        round_id=1548,
        matches=matches,
        payouts=TotoPayouts(1000, 200, 50),
    )


def historical_matches() -> list[OfficialMatch]:
    base = datetime(2025, 1, 10, 14, 0, tzinfo=JAPAN_TIMEZONE)
    matches = [
        OfficialMatch(
            match_time=base + timedelta(days=index * 20),
            home_team="鹿島アントラーズ" if index % 2 == 0 else "浦和レッズ",
            away_team="浦和レッズ" if index % 2 == 0 else "鹿島アントラーズ",
            home_goals=2 if index % 2 == 0 else 0,
            away_goals=0 if index % 2 == 0 else 1,
            category="J1",
        )
        for index in range(6)
    ]
    # 開催後の極端な結果。予測へ混入してはならない。
    matches.append(
        OfficialMatch(
            match_time=datetime(2025, 6, 22, 14, 0, tzinfo=JAPAN_TIMEZONE),
            home_team="浦和レッズ",
            away_team="鹿島アントラーズ",
            home_goals=9,
            away_goals=0,
            category="J1",
        )
    )
    return matches


class FakeHistoricalSource:
    def __init__(self, matches):
        self.matches = matches
        self.pages = []

    def fetch_schedule_page(self, page):
        self.pages.append(page)
        return list(self.matches)


class BacktestTest(unittest.TestCase):
    def test_future_results_do_not_change_backtest(self) -> None:
        toto_round = completed_round()
        history = historical_matches()

        with_future = run_backtest(toto_round, history)
        without_future = run_backtest(toto_round, history[:-1])

        self.assertEqual(with_future.historical_match_count, 6)
        self.assertEqual(
            with_future.matches[0].versions["Version6"],
            without_future.matches[0].versions["Version6"],
        )
        self.assertEqual(len(with_future.matches), 13)
        self.assertEqual(len(with_future.history_records()), 39)
        version5 = with_future.matches[0].versions["Version5"]
        version6 = with_future.matches[0].versions["Version6"]
        self.assertEqual(version5.prediction, version6.prediction)
        self.assertEqual(version5.probabilities, version6.probabilities)
        self.assertEqual(
            version5.home_expected_goals,
            version6.home_expected_goals,
        )

    def test_team_stats_use_only_values_before_cutoff(self) -> None:
        cutoff = datetime(2025, 6, 21, 0, 0, tzinfo=JAPAN_TIMEZONE)
        stats = calculate_team_stats_as_of(
            historical_matches(),
            cutoff,
            ["鹿島アントラーズ", "浦和レッズ"],
        )
        self.assertEqual(stats["浦和レッズ"].recent_results[0].match_date.year, 2025)
        self.assertLess(stats["浦和レッズ"].average_scored, 9.0)

    def test_historical_source_fetches_current_and_prior_period(self) -> None:
        source = FakeHistoricalSource(historical_matches())
        loaded = fetch_historical_matches(completed_round(), data_source=source)
        self.assertGreater(len(loaded), 0)
        self.assertEqual(len(source.pages), 2)
        self.assertEqual(source.pages[0].year_id, "2024")
        self.assertEqual(source.pages[1].year_id, "2025")


if __name__ == "__main__":
    unittest.main()
