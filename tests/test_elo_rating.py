"""Version4のElo更新・期待得点補正・キャッシュを確認する。"""

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DEFAULT_ELO_SETTINGS
from elo_rating import (
    adjust_expected_goals,
    calculate_expected_score,
    elo_difference_to_adjustment,
    generate_elo_ratings,
    get_goal_difference_multiplier,
    load_or_calculate_elo,
    update_elo_ratings,
)


TEAM_CATEGORIES = {
    "鹿島アントラーズ": "J1",
    "浦和レッズ": "J1",
    "ベガルタ仙台": "J2",
    "モンテディオ山形": "J2",
}


def completed_match(
    match_time: datetime,
    home_team: str = "鹿島アントラーズ",
    away_team: str = "浦和レッズ",
    home_goals=1,
    away_goals=0,
    category: str = "J1",
) -> dict:
    return {
        "match_time": match_time,
        "home_team": home_team,
        "away_team": away_team,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "category": category,
    }


class EloFormulaTest(unittest.TestCase):
    def test_equal_ratings_have_fifty_percent_neutral_expectation(self) -> None:
        self.assertAlmostEqual(calculate_expected_score(1500, 1500), 0.5)

    def test_higher_rating_has_higher_expected_score(self) -> None:
        self.assertGreater(calculate_expected_score(1600, 1500), 0.5)
        self.assertLess(calculate_expected_score(1400, 1500), 0.5)

    def test_draw_is_updated_as_actual_score_point_five(self) -> None:
        expected_home = calculate_expected_score(
            1500 + DEFAULT_ELO_SETTINGS.home_advantage,
            1500,
        )
        home_after, away_after = update_elo_ratings(1500, 1500, 1, 1)
        expected_change = DEFAULT_ELO_SETTINGS.k_factor * (0.5 - expected_home)

        self.assertAlmostEqual(home_after, 1500 + expected_change)
        self.assertAlmostEqual(away_after, 1500 - expected_change)

    def test_winner_rises_loser_falls_and_total_is_preserved(self) -> None:
        home_after, away_after = update_elo_ratings(1500, 1500, 2, 1)

        self.assertGreater(home_after, 1500)
        self.assertLess(away_after, 1500)
        self.assertAlmostEqual(home_after + away_after, 3000)

    def test_goal_difference_adjustment_can_be_switched(self) -> None:
        enabled = DEFAULT_ELO_SETTINGS
        disabled = replace(
            DEFAULT_ELO_SETTINGS,
            goal_difference_adjustment_enabled=False,
        )

        self.assertEqual(get_goal_difference_multiplier(3, enabled), 1.5)
        self.assertEqual(get_goal_difference_multiplier(3, disabled), 1.0)

        home_enabled, _ = update_elo_ratings(1500, 1500, 3, 0, enabled)
        home_disabled, _ = update_elo_ratings(1500, 1500, 3, 0, disabled)
        self.assertGreater(
            home_enabled - 1500,
            home_disabled - 1500,
        )

    def test_expected_goals_adjustment_never_exceeds_fifteen_percent(self) -> None:
        self.assertEqual(elo_difference_to_adjustment(1000), 0.15)
        self.assertEqual(elo_difference_to_adjustment(-1000), -0.15)

        positive = adjust_expected_goals(2.0, 1.0, 2500, 1500)
        negative = adjust_expected_goals(2.0, 1.0, 500, 1500)

        self.assertAlmostEqual(positive.home_after, 2.30)
        self.assertAlmostEqual(positive.away_after, 0.85)
        self.assertAlmostEqual(negative.home_after, 1.70)
        self.assertAlmostEqual(negative.away_after, 1.15)


class EloHistoryAndCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    def test_unplayed_or_future_matches_are_not_used(self) -> None:
        matches = [
            completed_match(self.now - timedelta(days=2)),
            completed_match(
                self.now - timedelta(days=1),
                home_goals=None,
                away_goals=None,
            ),
            completed_match(self.now + timedelta(days=1), home_goals=4),
        ]

        result = generate_elo_ratings(
            matches,
            TEAM_CATEGORIES,
            as_of=self.now,
        )

        self.assertEqual(result.processed_match_count, 1)
        self.assertEqual(result.ratings["鹿島アントラーズ"].matches_played, 1)
        self.assertEqual(result.ratings["浦和レッズ"].matches_played, 1)

    def test_elo_total_does_not_drift_across_multiple_matches(self) -> None:
        matches = [
            completed_match(self.now - timedelta(days=3)),
            completed_match(
                self.now - timedelta(days=2),
                "ベガルタ仙台",
                "モンテディオ山形",
                0,
                2,
                "J2",
            ),
            completed_match(
                self.now - timedelta(days=1),
                "浦和レッズ",
                "ベガルタ仙台",
                1,
                1,
                "J1",
            ),
        ]
        initial_total = 1500 + 1500 + 1450 + 1450

        result = generate_elo_ratings(
            matches,
            TEAM_CATEGORIES,
            as_of=self.now,
        )
        final_total = sum(rating.rating for rating in result.ratings.values())

        self.assertAlmostEqual(final_total, initial_total)

    def test_same_history_uses_cache_and_new_match_is_incremental(self) -> None:
        first_match = completed_match(self.now - timedelta(days=2))
        second_match = completed_match(
            self.now - timedelta(days=1),
            home_goals=0,
            away_goals=1,
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            cache_path = Path(temp_directory) / "elo.json"
            first = load_or_calculate_elo(
                [first_match],
                TEAM_CATEGORIES,
                cache_path=cache_path,
                as_of=self.now,
            )
            same = load_or_calculate_elo(
                [first_match],
                TEAM_CATEGORIES,
                cache_path=cache_path,
                as_of=self.now,
            )
            updated = load_or_calculate_elo(
                [first_match, second_match],
                TEAM_CATEGORIES,
                cache_path=cache_path,
                as_of=self.now,
            )

        self.assertFalse(first.from_cache)
        self.assertTrue(same.from_cache)
        self.assertEqual(same.incremental_match_count, 0)
        self.assertEqual(updated.incremental_match_count, 1)
        self.assertEqual(updated.processed_match_count, 2)


if __name__ == "__main__":
    unittest.main()
