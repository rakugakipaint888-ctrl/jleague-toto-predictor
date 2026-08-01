"""Version5の直近・会場・順位表補正と安全制御を確認する。"""

import math
import unittest

from form_adjuster import calculate_weighted_recent_form
from model_pipeline import ModelOptions, TeamModelInput, predict_match
from standings_adjuster import (
    StandingMetrics,
    adjust_expected_goals_by_standings,
)
from venue_adjuster import adjust_for_venue


RECENT_MATCHES = (
    {"scored": 5, "conceded": 0},
    {"scored": 4, "conceded": 1},
    {"scored": 3, "conceded": 2},
    {"scored": 2, "conceded": 3},
    {"scored": 1, "conceded": 4},
)


class FormAdjusterTest(unittest.TestCase):
    def test_latest_match_has_largest_weight_and_average_is_correct(self) -> None:
        result = calculate_weighted_recent_form(RECENT_MATCHES)

        self.assertEqual(result.used_weights, (5.0, 4.0, 3.0, 2.0, 1.0))
        self.assertAlmostEqual(result.average_scored, 55 / 15)
        self.assertAlmostEqual(result.average_conceded, 20 / 15)
        self.assertEqual(result.match_count, 5)

    def test_available_matches_only_are_used(self) -> None:
        result = calculate_weighted_recent_form(
            (
                {"scored": 2, "conceded": 1},
                {"scored": None, "conceded": 0},
                {"scored": 1, "conceded": 2},
            )
        )

        self.assertEqual(result.used_weights, (5.0, 3.0))
        self.assertAlmostEqual(result.average_scored, 13 / 8)
        self.assertAlmostEqual(result.average_conceded, 11 / 8)


class VenueAdjusterTest(unittest.TestCase):
    def test_five_matches_uses_seventy_percent_venue_data(self) -> None:
        result = adjust_for_venue(
            1.0,
            1.0,
            1.0,
            1.0,
            home_record={"played": 5, "goals_for": 15, "goals_against": 5},
            away_record={"played": 5, "goals_for": 5, "goals_against": 10},
        )

        self.assertAlmostEqual(result.home.venue_share, 0.70)
        self.assertAlmostEqual(result.home.scored, 2.40)
        self.assertAlmostEqual(result.away.conceded, 1.70)
        self.assertTrue(result.applied)

    def test_small_samples_increase_overall_average_share(self) -> None:
        four = adjust_for_venue(
            1.0,
            1.0,
            1.0,
            1.0,
            home_record={"played": 4, "goals_for": 8, "goals_against": 4},
        )
        three = adjust_for_venue(
            1.0,
            1.0,
            1.0,
            1.0,
            home_record={"played": 3, "goals_for": 6, "goals_against": 3},
        )
        none = adjust_for_venue(1.0, 1.0, 1.0, 1.0)

        self.assertAlmostEqual(four.home.venue_share, 0.60)
        self.assertAlmostEqual(three.home.venue_share, 0.40)
        self.assertAlmostEqual(four.home.scored, 1.60)
        self.assertAlmostEqual(three.home.scored, 1.40)
        self.assertEqual(none.home.venue_share, 0.0)
        self.assertFalse(none.applied)


class StandingsAdjusterTest(unittest.TestCase):
    def test_total_adjustment_never_exceeds_eight_percent(self) -> None:
        result = adjust_expected_goals_by_standings(
            1.5,
            1.5,
            StandingMetrics(points=90, played=30, goal_difference=90),
            StandingMetrics(points=0, played=30, goal_difference=-90),
        )

        self.assertAlmostEqual(result.points_adjustment_rate, 0.05)
        self.assertAlmostEqual(result.goal_difference_adjustment_rate, 0.03)
        self.assertAlmostEqual(result.total_adjustment_rate, 0.08)
        self.assertAlmostEqual(result.home_after, 1.62)
        self.assertAlmostEqual(result.away_after, 1.38)
        self.assertTrue(result.data_available)

    def test_missing_standings_leave_expected_goals_unchanged(self) -> None:
        result = adjust_expected_goals_by_standings(
            1.4,
            1.2,
            StandingMetrics(),
            StandingMetrics(),
        )

        self.assertEqual(result.home_after, 1.4)
        self.assertEqual(result.away_after, 1.2)
        self.assertFalse(result.data_available)
        self.assertFalse(result.applied)


class ModelPipelineTest(unittest.TestCase):
    def _teams(self) -> tuple[TeamModelInput, TeamModelInput]:
        home = TeamModelInput(
            team_name="ホーム",
            recent_scored_average=2.0,
            recent_conceded_average=1.0,
            recent_matches=RECENT_MATCHES,
            season_scored_average=1.5,
            season_conceded_average=1.2,
            venue_record={
                "played": 5,
                "goals_for": 12,
                "goals_against": 4,
            },
            rank=1,
            points=30,
            played=12,
            goal_difference=18,
            elo=1600,
        )
        away = TeamModelInput(
            team_name="アウェイ",
            recent_scored_average=1.2,
            recent_conceded_average=1.8,
            recent_matches=tuple(reversed(RECENT_MATCHES)),
            season_scored_average=1.1,
            season_conceded_average=1.6,
            venue_record={
                "played": 5,
                "goals_for": 4,
                "goals_against": 10,
            },
            rank=12,
            points=10,
            played=12,
            goal_difference=-10,
            elo=1400,
        )
        return home, away

    def test_all_adjustments_respect_expected_goal_bounds(self) -> None:
        home, away = self._teams()
        home = TeamModelInput(**{**home.__dict__, "elo": 2500})
        away = TeamModelInput(**{**away.__dict__, "elo": 500})

        result = predict_match(home, away)

        self.assertGreaterEqual(result.expected_final.home, 0.15)
        self.assertLessEqual(result.expected_final.home, 4.00)
        self.assertGreaterEqual(result.expected_final.away, 0.15)
        self.assertLessEqual(result.expected_final.away, 4.00)
        self.assertLessEqual(abs(result.standings.total_adjustment_rate), 0.08)

    def test_each_switch_disables_only_its_adjustment(self) -> None:
        home, away = self._teams()
        all_on = predict_match(home, away)
        no_form = predict_match(
            home,
            away,
            ModelOptions(use_recent_weighting=False),
        )
        no_venue = predict_match(
            home,
            away,
            ModelOptions(use_venue=False),
        )
        no_elo = predict_match(
            home,
            away,
            ModelOptions(use_elo=False),
        )
        no_standings = predict_match(
            home,
            away,
            ModelOptions(use_standings=False),
        )

        self.assertTrue(all_on.recent_weighting_enabled)
        self.assertFalse(no_form.recent_weighting_enabled)
        self.assertTrue(no_form.venue_adjustment_enabled)
        self.assertFalse(no_venue.venue_adjustment_enabled)
        self.assertTrue(no_venue.recent_weighting_enabled)
        self.assertFalse(no_elo.elo_adjustment_enabled)
        self.assertEqual(no_elo.expected_after_elo, no_elo.expected_after_venue)
        self.assertFalse(no_standings.standings_adjustment_enabled)
        self.assertEqual(
            no_standings.expected_after_standings,
            no_standings.expected_after_elo,
        )

    def test_version4_is_reproduced_when_version5_adjustments_are_off(self) -> None:
        home, away = self._teams()
        result = predict_match(
            home,
            away,
            ModelOptions(
                use_elo=True,
                use_venue=False,
                use_recent_weighting=False,
                use_standings=False,
            ),
        )

        self.assertAlmostEqual(
            result.expected_final.home,
            result.version4.expected_after_elo.home,
        )
        self.assertAlmostEqual(
            result.expected_final.away,
            result.version4.expected_after_elo.away,
        )
        for key in ("home_win", "draw", "away_win"):
            self.assertAlmostEqual(
                result.version5_probabilities[key],
                result.version4.probabilities[key],
            )
        self.assertEqual(result.version5_prediction, result.version4.prediction)

    def test_missing_and_invalid_values_fall_back_without_exception(self) -> None:
        result = predict_match(
            TeamModelInput(
                recent_scored_average=float("nan"),
                recent_conceded_average=-1,
                recent_matches=({"scored": math.inf, "conceded": None},),
            ),
            TeamModelInput(
                recent_scored_average=None,
                recent_conceded_average=float("inf"),
            ),
        )

        self.assertTrue(result.fallback_used)
        self.assertTrue(math.isfinite(result.expected_final.home))
        self.assertTrue(math.isfinite(result.expected_final.away))
        self.assertIn(result.version5_prediction, ("1", "0", "2"))


if __name__ == "__main__":
    unittest.main()
