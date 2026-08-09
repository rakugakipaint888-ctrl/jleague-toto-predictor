"""Version7-Cの口数、配置、Coverage、CSV、バックテストを検証する。"""

from __future__ import annotations

import math
import unittest

import pandas as pd

from bet_config import (
    DOUBLE_SCORE_WEIGHTS,
    SINGLE_CONFIDENCE_WEIGHTS,
    TRIPLE_SCORE_WEIGHTS,
    UNCERTAINTY_SCORE_WEIGHTS,
)
from bet_evaluation import (
    backtest_bet_strategy,
    compare_bet_strategies,
)
from bet_export import (
    CombinationLimitError,
    bet_plan_csv_bytes,
    bet_plan_frame,
    combination_csv_bytes,
    combination_frame,
)
from bet_optimizer import (
    BET_TYPE_DOUBLE,
    BET_TYPE_SINGLE,
    BET_TYPE_TRIPLE,
    BetOptimizationError,
    apply_manual_selections,
    build_match_predictions,
    calculate_purchase_amount,
    calculate_ticket_count,
    is_budget_exceeded,
    optimize_bet_plan,
)


def probability_frame() -> pd.DataFrame:
    patterns = (
        (70.0, 18.0, 12.0),
        (38.0, 35.0, 27.0),
        (35.0, 33.0, 32.0),
        (50.0, 10.0, 40.0),
        (45.0, 35.0, 20.0),
    )
    return pd.DataFrame(
        [
            {
                "toto_match_number": number,
                "対戦カード": f"ホーム{number} vs アウェイ{number}",
                "1": patterns[(number - 1) % len(patterns)][0],
                "0": patterns[(number - 1) % len(patterns)][1],
                "2": patterns[(number - 1) % len(patterns)][2],
                "draw_candidate": number % 5 in (2, 3, 0),
            }
            for number in range(1, 14)
        ]
    )


def completed_history() -> pd.DataFrame:
    rows = []
    for round_id in (1701, 1702):
        for match_number in range(1, 14):
            actual = "1"
            if round_id == 1702 and match_number == 1:
                actual = "0"
            rows.append(
                {
                    "toto_round": round_id,
                    "toto_match_number": match_number,
                    "prediction_version": "Version7-A",
                    "prediction_date": f"2026-01-{round_id - 1700:02d}",
                    "home_team": f"H{match_number}",
                    "away_team": f"A{match_number}",
                    "probability_1": 0.45,
                    "probability_0": 0.35,
                    "probability_2": 0.20,
                    "actual_result": actual,
                }
            )
    return pd.DataFrame(rows)


class Version7CCountAndOptimizationTest(unittest.TestCase):
    def test_all_documented_score_weights_sum_to_one(self) -> None:
        for weights in (
            UNCERTAINTY_SCORE_WEIGHTS,
            DOUBLE_SCORE_WEIGHTS,
            TRIPLE_SCORE_WEIGHTS,
            SINGLE_CONFIDENCE_WEIGHTS,
        ):
            self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_required_ticket_and_amount_examples(self) -> None:
        cases = (
            (0, 0, 1, 100),
            (1, 0, 2, 200),
            (3, 0, 8, 800),
            (2, 1, 12, 1_200),
            (3, 1, 24, 2_400),
            (0, 1, 3, 300),
        )
        for doubles, triples, tickets, amount in cases:
            with self.subTest(doubles=doubles, triples=triples):
                self.assertEqual(calculate_ticket_count(doubles, triples), tickets)
                self.assertEqual(
                    calculate_purchase_amount(doubles, triples), amount
                )

    def test_toto_and_mini_targets_use_official_source_order(self) -> None:
        frame = probability_frame()
        toto = build_match_predictions(frame, "toto")
        mini_a = build_match_predictions(frame, "mini_a")
        mini_b = build_match_predictions(frame, "mini_b")
        self.assertEqual(len(toto), 13)
        self.assertEqual(len(mini_a), 5)
        self.assertEqual(len(mini_b), 5)
        self.assertEqual(
            [item.source_match_number for item in mini_a],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [item.source_match_number for item in mini_b],
            [6, 7, 8, 9, 10],
        )
        self.assertEqual([item.match_number for item in mini_b], [1, 2, 3, 4, 5])

    def test_double_and_triple_rules_and_exact_counts(self) -> None:
        predictions = build_match_predictions(probability_frame(), "toto")
        plan = optimize_bet_plan(
            predictions,
            target="toto",
            double_count=3,
            triple_count=1,
        )
        self.assertEqual(plan.double_count, 3)
        self.assertEqual(plan.triple_count, 1)
        self.assertEqual(plan.ticket_count, 24)
        self.assertEqual(plan.purchase_amount_yen, 2_400)
        self.assertEqual(
            sum(item.bet_type == BET_TYPE_SINGLE for item in plan.recommendations),
            9,
        )
        for recommendation in plan.recommendations:
            if recommendation.bet_type == BET_TYPE_DOUBLE:
                self.assertEqual(
                    recommendation.outcomes,
                    recommendation.analysis.ranked_outcomes[:2],
                )
            elif recommendation.bet_type == BET_TYPE_TRIPLE:
                self.assertEqual(recommendation.outcomes, ("1", "0", "2"))

    def test_balanced_match_is_prioritized_and_confident_match_stays_single(self) -> None:
        plan = optimize_bet_plan(
            build_match_predictions(probability_frame(), "mini_a"),
            target="mini_a",
            double_count=1,
            triple_count=1,
        )
        by_source = {
            item.analysis.prediction.source_match_number: item
            for item in plan.recommendations
        }
        self.assertEqual(by_source[3].bet_type, BET_TYPE_TRIPLE)
        self.assertEqual(by_source[1].bet_type, BET_TYPE_SINGLE)
        self.assertEqual(by_source[1].analysis.single_confidence, "高")
        self.assertEqual(by_source[3].analysis.single_confidence, "低")

    def test_draw_is_used_when_top_two_but_not_forced_when_third(self) -> None:
        plan = optimize_bet_plan(
            build_match_predictions(probability_frame(), "mini_a"),
            target="mini_a",
            double_count=5,
            triple_count=0,
            draw_candidate_threshold=0.25,
        )
        by_source = {
            item.analysis.prediction.source_match_number: item
            for item in plan.recommendations
        }
        self.assertEqual(by_source[2].outcomes, ("1", "0"))
        self.assertEqual(by_source[4].outcomes, ("1", "2"))
        self.assertTrue(by_source[2].analysis.draw_candidate)
        self.assertFalse(by_source[4].analysis.draw_candidate)

    def test_probability_errors_are_rejected_without_nan_or_infinity(self) -> None:
        for invalid in (float("nan"), float("inf")):
            frame = probability_frame()
            frame.loc[0, "1"] = invalid
            with self.subTest(value=invalid):
                with self.assertRaises(BetOptimizationError):
                    build_match_predictions(frame, "toto")
        frame = probability_frame()
        frame.loc[0, ["1", "0", "2"]] = [40.0, 30.0, 20.0]
        with self.assertRaises(BetOptimizationError):
            build_match_predictions(frame, "toto")

    def test_too_many_types_and_missing_matches_are_rejected(self) -> None:
        frame = probability_frame()
        with self.assertRaises(BetOptimizationError):
            build_match_predictions(frame.iloc[:4], "mini_a")
        predictions = build_match_predictions(frame, "mini_a")
        with self.assertRaises(BetOptimizationError):
            optimize_bet_plan(
                predictions,
                target="mini_a",
                double_count=3,
                triple_count=3,
            )

    def test_budget_warning_is_calculated_without_changing_the_plan(self) -> None:
        self.assertTrue(is_budget_exceeded(3, 1, 2_000))
        self.assertFalse(is_budget_exceeded(3, 1, 3_000))
        self.assertFalse(is_budget_exceeded(3, 1, None))


class Version7CCoverageManualAndExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = optimize_bet_plan(
            build_match_predictions(probability_frame(), "mini_a"),
            target="mini_a",
            double_count=0,
            triple_count=0,
        )

    def test_match_and_full_coverage_use_selected_probability_sum_and_product(self) -> None:
        first = self.plan.recommendations[0]
        self.assertAlmostEqual(first.coverage, 0.70)
        expected = math.prod(item.coverage for item in self.plan.recommendations)
        self.assertAlmostEqual(self.plan.estimated_full_coverage, expected)
        manual = apply_manual_selections(
            self.plan,
            {1: ("1", "0"), 2: ("1", "0", "2")},
        )
        self.assertAlmostEqual(manual.recommendations[0].coverage, 0.88)
        self.assertAlmostEqual(manual.recommendations[1].coverage, 1.0)
        self.assertAlmostEqual(
            manual.estimated_full_coverage,
            math.prod(item.coverage for item in manual.recommendations),
        )

    def test_manual_change_recalculates_type_tickets_amount_and_coverage(self) -> None:
        manual = apply_manual_selections(
            self.plan,
            {1: ("1", "0"), 2: ("1", "0", "2")},
        )
        self.assertEqual(manual.double_count, 1)
        self.assertEqual(manual.triple_count, 1)
        self.assertEqual(manual.ticket_count, 6)
        self.assertEqual(manual.purchase_amount_yen, 600)
        with self.assertRaises(BetOptimizationError):
            apply_manual_selections(self.plan, {1: ()})

    def test_csv_and_combination_expansion_are_complete(self) -> None:
        plan = optimize_bet_plan(
            build_match_predictions(probability_frame(), "mini_a"),
            target="mini_a",
            double_count=2,
            triple_count=1,
        )
        summary = bet_plan_frame(plan)
        combinations = combination_frame(plan)
        self.assertEqual(len(summary), 5)
        self.assertEqual(len(combinations), 12)
        self.assertEqual(list(combinations["口番号"]), list(range(1, 13)))
        self.assertTrue(bet_plan_csv_bytes(plan).startswith(b"\xef\xbb\xbf"))
        self.assertTrue(combination_csv_bytes(plan).startswith(b"\xef\xbb\xbf"))
        self.assertFalse(summary.isna().any().any())

    def test_combination_explosion_is_blocked_but_summary_remains_available(self) -> None:
        plan = optimize_bet_plan(
            build_match_predictions(probability_frame(), "toto"),
            target="toto",
            double_count=3,
            triple_count=1,
        )
        with self.assertRaises(CombinationLimitError):
            combination_frame(plan, max_combinations=23)
        self.assertEqual(len(bet_plan_frame(plan)), 13)


class Version7CBacktestTest(unittest.TestCase):
    def test_strategy_backtest_counts_hits_stake_and_known_payout(self) -> None:
        history = completed_history()
        payouts = {
            1701: {
                "first_prize_yen": 10_000,
                "second_prize_yen": 1_000,
                "third_prize_yen": 100,
            },
            1702: {
                "first_prize_yen": 20_000,
                "second_prize_yen": 2_000,
                "third_prize_yen": 200,
            },
        }
        singles = backtest_bet_strategy(
            history,
            strategy="A",
            target="toto",
            prediction_version="Version7-A",
            double_count=0,
            triple_count=0,
            draw_candidate_threshold=0.25,
            draw_candidate_margin=0.05,
            payouts_by_round=payouts,
        )
        double = backtest_bet_strategy(
            history,
            strategy="B",
            target="toto",
            prediction_version="Version7-A",
            double_count=1,
            triple_count=0,
            draw_candidate_threshold=0.25,
            draw_candidate_margin=0.05,
            payouts_by_round=payouts,
        )
        self.assertEqual(singles.evaluated_rounds, 2)
        self.assertEqual(singles.total_ticket_count, 2)
        self.assertEqual(singles.total_purchase_yen, 200)
        self.assertEqual(singles.full_hit_count, 1)
        self.assertEqual(singles.total_payout_yen, 12_000)
        self.assertEqual(double.total_ticket_count, 4)
        self.assertEqual(double.total_purchase_yen, 400)
        self.assertEqual(double.full_hit_count, 2)
        self.assertEqual(double.total_payout_yen, 33_000)
        self.assertEqual(double.profit_yen, 32_600)
        self.assertAlmostEqual(double.roi or 0.0, 8_250.0)

    def test_missing_payout_is_not_inferred_and_three_strategies_compare(self) -> None:
        results = compare_bet_strategies(
            completed_history(),
            target="mini_a",
            prediction_version="Version7-A",
            double_count=2,
            triple_count=1,
            draw_candidate_threshold=0.25,
            draw_candidate_margin=0.05,
        )
        self.assertEqual(len(results), 3)
        self.assertEqual(
            [(result.double_count, result.triple_count) for result in results],
            [(0, 0), (2, 0), (2, 1)],
        )
        self.assertTrue(all(result.evaluated_rounds == 2 for result in results))
        self.assertTrue(all(not result.payout_data_available for result in results))
        self.assertTrue(all(result.total_payout_yen is None for result in results))
        self.assertTrue(all(result.roi is None for result in results))


if __name__ == "__main__":
    unittest.main()
