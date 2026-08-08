"""Version7-Aの3クラス確率、引分補正、候補判定を確認する。"""

import math
import unittest
from dataclasses import replace
from datetime import datetime, timedelta

from data_loader import JAPAN_TIMEZONE, OfficialMatch, RecentMatchRecord, VenueRecord
from draw_predictor import (
    DEFAULT_DRAW_SETTINGS,
    DrawSettings,
    build_draw_context,
    normalize_three_way_probabilities,
    predict_draw_aware,
    probability_percentages,
)
from model_pipeline import TeamModelInput


def team(
    *,
    elo=1500,
    draws=4,
    played=10,
    recent_draws=3,
) -> TeamModelInput:
    base = datetime(2026, 1, 1, tzinfo=JAPAN_TIMEZONE).date()
    recent = tuple(
        RecentMatchRecord(
            match_date=base + timedelta(days=index),
            opponent="浦和レッズ",
            venue="H",
            scored=1,
            conceded=1 if index < recent_draws else 0,
            result="分" if index < recent_draws else "勝",
        )
        for index in range(5)
    )
    return TeamModelInput(
        recent_scored_average=1.1,
        recent_conceded_average=1.0,
        recent_matches=recent,
        venue_record=VenueRecord(played, 3, draws, played - 3 - draws, 10, 10),
        rank=5,
        points=15,
        played=played,
        season_draws=draws,
        goal_difference=1,
        elo=elo,
    )


class DrawPredictorTest(unittest.TestCase):
    def test_neutral_settings_exactly_reproduce_version6_probabilities(self) -> None:
        base = {"home_win": 0.46, "draw": 0.27, "away_win": 0.27}
        result = predict_draw_aware(base, 1.5, 1.0, team(), team())

        self.assertEqual(result.prediction, "1")
        self.assertAlmostEqual(result.probabilities["1"], 0.46)
        self.assertAlmostEqual(result.probabilities["0"], 0.27)
        self.assertAlmostEqual(result.probabilities["2"], 0.27)

    def test_three_probabilities_are_finite_nonnegative_and_sum_to_one(self) -> None:
        normalized = normalize_three_way_probabilities(
            {"1": float("nan"), "0": -1.0, "2": float("inf")}
        )
        self.assertAlmostEqual(sum(normalized.values()), 1.0)
        self.assertTrue(all(math.isfinite(value) for value in normalized.values()))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in normalized.values()))
        self.assertIn("0", normalized)

        displayed = probability_percentages(
            {"1": 0.33335, "0": 0.33335, "2": 0.33330}
        )
        self.assertEqual(sum(displayed.values()), 100.0)
        self.assertTrue(all(0.0 <= value <= 100.0 for value in displayed.values()))

    def test_small_strength_gap_can_raise_draw_and_large_gap_can_lower_it(self) -> None:
        settings = replace(
            DEFAULT_DRAW_SETTINGS,
            elo_closeness_weight=1.0,
            expected_goal_closeness_weight=1.0,
        )
        close = predict_draw_aware(
            {"1": 0.38, "0": 0.24, "2": 0.38},
            1.2,
            1.2,
            team(elo=1500),
            team(elo=1505),
            settings=settings,
        )
        wide = predict_draw_aware(
            {"1": 0.60, "0": 0.24, "2": 0.16},
            2.1,
            0.6,
            team(elo=1750),
            team(elo=1300),
            settings=settings,
        )
        self.assertGreater(close.probabilities["0"], 0.24)
        self.assertLess(wide.probabilities["0"], 0.24)

    def test_draw_is_a_real_argmax_candidate_and_candidate_is_separate(self) -> None:
        draw_favorite = predict_draw_aware(
            {"1": 0.39, "0": 0.22, "2": 0.39},
            1.0,
            1.0,
            team(),
            team(),
            settings=replace(DEFAULT_DRAW_SETTINGS, base_draw_logit_bias=1.5),
        )
        self.assertEqual(draw_favorite.prediction, "0")
        self.assertTrue(draw_favorite.is_draw_candidate)

        close_candidate = predict_draw_aware(
            {"1": 0.38, "0": 0.35, "2": 0.27},
            1.2,
            1.0,
            team(),
            team(),
            settings=DrawSettings(candidate_threshold=0.40, candidate_margin=0.05),
        )
        self.assertEqual(close_candidate.prediction, "1")
        self.assertTrue(close_candidate.is_draw_candidate)

    def test_context_uses_only_completed_matches_before_cutoff(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=JAPAN_TIMEZONE)
        matches = (
            OfficialMatch(
                cutoff - timedelta(days=2),
                "鹿島アントラーズ",
                "浦和レッズ",
                1,
                1,
                "J1",
            ),
            OfficialMatch(
                cutoff + timedelta(days=1),
                "鹿島アントラーズ",
                "浦和レッズ",
                0,
                0,
                "J1",
            ),
        )
        context = build_draw_context(matches, cutoff, category="J1")
        self.assertEqual(context.historical_match_count, 1)
        self.assertEqual(context.league_draw_rate, 1.0)
        self.assertEqual(context.league_goals_per_team, 1.0)


if __name__ == "__main__":
    unittest.main()
