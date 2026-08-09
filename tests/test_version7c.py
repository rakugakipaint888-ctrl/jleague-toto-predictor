"""Version7-Cの口数、配置、Coverage、CSV、バックテストを検証する。"""

from __future__ import annotations

from io import BytesIO
import math
import unittest
from unittest.mock import patch

import pandas as pd

from bet_config import (
    DOUBLE_SCORE_WEIGHTS,
    DRAW_INCLUSION_SCORE_WEIGHTS,
    SINGLE_CONFIDENCE_WEIGHTS,
    TRIPLE_SCORE_WEIGHTS,
    UNCERTAINTY_SCORE_WEIGHTS,
)
from bet_evaluation import (
    backtest_bet_strategy,
    compare_bet_strategies,
)
from bet_export import (
    BET_PLAN_DISPLAY_COLUMNS,
    BET_PLAN_FRAME_COLUMNS,
    BetPlanSchemaError,
    CombinationLimitError,
    bet_plan_csv_bytes,
    bet_plan_display_frame,
    bet_plan_frame,
    combination_csv_bytes,
    combination_frame,
)
from bet_optimizer import (
    BET_TYPE_DOUBLE,
    BET_TYPE_SINGLE,
    BET_TYPE_TRIPLE,
    BetOptimizationError,
    MatchPrediction,
    analyze_match_prediction,
    apply_manual_selections,
    build_match_predictions,
    calculate_purchase_amount,
    calculate_ticket_count,
    is_budget_exceeded,
    optimize_bet_plan,
    select_double_outcomes,
)
from history_manager import TotoPayouts


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
            DRAW_INCLUSION_SCORE_WEIGHTS,
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
                    select_double_outcomes(recommendation.analysis),
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


class Version7CDrawInclusionTest(unittest.TestCase):
    @staticmethod
    def _prediction(
        probability_1: float,
        probability_0: float,
        probability_2: float,
        *,
        model_draw_candidate: bool = False,
        match_number: int = 1,
    ) -> MatchPrediction:
        return MatchPrediction(
            match_number=match_number,
            source_match_number=match_number,
            home_team=f"H{match_number}",
            away_team=f"A{match_number}",
            probability_1=probability_1,
            probability_0=probability_0,
            probability_2=probability_2,
            model_draw_candidate=model_draw_candidate,
            model_draw_candidate_reasons=(
                ("引分確率が設定閾値以上",)
                if model_draw_candidate
                else ()
            ),
        )

    @staticmethod
    def _analysis(prediction: MatchPrediction):
        return analyze_match_prediction(
            prediction,
            draw_candidate_threshold=0.25,
            draw_candidate_margin=0.05,
        )

    def _all_double_plan(self, first: MatchPrediction):
        fillers = tuple(
            self._prediction(0.70, 0.18, 0.12, match_number=number)
            for number in range(2, 6)
        )
        return optimize_bet_plan(
            (first, *fillers),
            target="mini_a",
            double_count=5,
            triple_count=0,
            draw_candidate_threshold=0.25,
            draw_candidate_margin=0.05,
        )

    def test_case_1_compares_third_draw_and_can_use_model_draw_evidence(self) -> None:
        without_model_signal = self._analysis(
            self._prediction(0.40, 0.27, 0.33)
        )
        with_model_signal = self._analysis(
            self._prediction(
                0.40,
                0.27,
                0.33,
                model_draw_candidate=True,
            )
        )
        self.assertTrue(without_model_signal.draw_inclusion_evaluated)
        self.assertIsNotNone(without_model_signal.draw_inclusion_score)
        self.assertGreater(
            with_model_signal.draw_inclusion_score or 0.0,
            without_model_signal.draw_inclusion_score or 0.0,
        )
        self.assertEqual(select_double_outcomes(without_model_signal), ("1", "2"))
        self.assertEqual(select_double_outcomes(with_model_signal), ("1", "0"))

    def test_case_2_evaluates_draw_but_keeps_top_two_when_evidence_is_weak(self) -> None:
        analysis = self._analysis(self._prediction(0.30, 0.26, 0.44))
        self.assertTrue(analysis.draw_inclusion_evaluated)
        self.assertFalse(analysis.draw_inclusion_recommended)
        self.assertEqual(select_double_outcomes(analysis), ("2", "1"))

    def test_case_3_uses_draw_when_it_is_already_in_top_two(self) -> None:
        analysis = self._analysis(self._prediction(0.45, 0.35, 0.20))
        self.assertEqual(analysis.ranked_outcomes[:2], ("1", "0"))
        self.assertFalse(analysis.draw_inclusion_evaluated)
        self.assertEqual(select_double_outcomes(analysis), ("1", "0"))

    def test_case_4_is_not_a_draw_candidate_or_special_inclusion(self) -> None:
        analysis = self._analysis(self._prediction(0.70, 0.18, 0.12))
        self.assertFalse(analysis.draw_candidate)
        self.assertFalse(analysis.draw_inclusion_evaluated)
        self.assertIsNone(analysis.draw_inclusion_score)
        # 0は通常の確率2位なので、特別採用ではなく基本上位2として残る。
        self.assertEqual(select_double_outcomes(analysis), ("1", "0"))

    def test_case_5_balanced_distribution_is_high_uncertainty_candidate(self) -> None:
        balanced = self._analysis(self._prediction(0.35, 0.33, 0.32))
        confident = self._analysis(self._prediction(0.70, 0.18, 0.12))
        self.assertGreater(balanced.uncertainty_score, confident.uncertainty_score)
        self.assertGreater(
            balanced.double_candidate_score,
            confident.double_candidate_score,
        )
        self.assertGreater(
            balanced.triple_candidate_score,
            confident.triple_candidate_score,
        )

    def test_case_6_below_threshold_does_not_enter_draw_comparison(self) -> None:
        analysis = self._analysis(
            self._prediction(
                0.45,
                0.249,
                0.301,
                model_draw_candidate=True,
            )
        )
        self.assertFalse(analysis.draw_inclusion_evaluated)
        self.assertIsNone(analysis.draw_inclusion_score)
        self.assertEqual(select_double_outcomes(analysis), ("1", "2"))

    def test_case_7_exact_threshold_enters_draw_comparison(self) -> None:
        analysis = self._analysis(self._prediction(0.45, 0.25, 0.30))
        self.assertTrue(analysis.draw_candidate)
        self.assertTrue(analysis.draw_inclusion_evaluated)
        self.assertIsNotNone(analysis.draw_inclusion_score)
        self.assertEqual(select_double_outcomes(analysis), ("1", "2"))

    def test_selected_and_rejected_draws_both_have_auditable_reasons(self) -> None:
        selected_plan = self._all_double_plan(
            self._prediction(
                0.40,
                0.27,
                0.33,
                model_draw_candidate=True,
            )
        )
        selected = selected_plan.recommendations[0]
        self.assertEqual(selected.outcomes, ("1", "0"))
        self.assertAlmostEqual(selected.coverage, 0.67)
        self.assertIn("0を採用", selected.reason)
        self.assertIn("Coverage低下6.0pt", selected.reason)

        rejected_plan = self._all_double_plan(
            self._prediction(0.30, 0.26, 0.44)
        )
        rejected = rejected_plan.recommendations[0]
        self.assertEqual(rejected.outcomes, ("2", "1"))
        self.assertAlmostEqual(rejected.coverage, 0.74)
        self.assertIn("引分評価対象", rejected.reason)
        self.assertIn("上位2結果2・1を維持", rejected.reason)

    def test_existing_model_draw_reasons_are_passed_to_version7c(self) -> None:
        frame = probability_frame()
        frame.loc[0, "draw_candidate_reasons"] = (
            "引分確率が設定閾値以上／1位確率との差が設定範囲内"
        )
        prediction = build_match_predictions(frame, "mini_a")[0]
        self.assertEqual(
            prediction.model_draw_candidate_reasons,
            ("引分確率が設定閾値以上", "1位確率との差が設定範囲内"),
        )


class Version7CInputValidationTest(unittest.TestCase):
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


class Version7CPlanDisplaySchemaTest(unittest.TestCase):
    @staticmethod
    def _plan(target: str, doubles: int, triples: int):
        return optimize_bet_plan(
            build_match_predictions(probability_frame(), target),
            target=target,
            double_count=doubles,
            triple_count=triples,
        )

    def _assert_formal_schema(self, plan, expected_rows: int) -> None:
        full_frame = bet_plan_frame(plan)
        display_frame = bet_plan_display_frame(plan)
        self.assertEqual(len(full_frame), expected_rows)
        self.assertEqual(len(display_frame), expected_rows)
        self.assertEqual(tuple(full_frame.columns), BET_PLAN_FRAME_COLUMNS)
        self.assertEqual(tuple(display_frame.columns), BET_PLAN_DISPLAY_COLUMNS)
        self.assertEqual(
            set(BET_PLAN_DISPLAY_COLUMNS) - set(display_frame.columns),
            set(),
        )

    def test_toto_mini_a_mini_b_and_manual_plan_share_one_schema(self) -> None:
        cases = (
            ("toto", 3, 1, 13),
            ("mini_a", 2, 1, 5),
            ("mini_b", 2, 1, 5),
        )
        for target, doubles, triples, expected_rows in cases:
            with self.subTest(target=target):
                self._assert_formal_schema(
                    self._plan(target, doubles, triples),
                    expected_rows,
                )

        base_plan = self._plan("mini_a", 0, 0)
        manual_plan = apply_manual_selections(
            base_plan,
            {1: ("1", "0"), 2: ("1", "0", "2")},
        )
        self._assert_formal_schema(manual_plan, 5)
        manual_frame = bet_plan_display_frame(manual_plan)
        self.assertEqual(manual_frame.loc[0, "推奨区分"], "ダブル")
        self.assertEqual(manual_frame.loc[1, "推奨区分"], "トリプル")
        self.assertEqual(manual_frame.loc[0, "判定理由"], "手動調整")

    def test_draw_candidate_and_non_candidate_rows_keep_draw_columns(self) -> None:
        predictions = (
            MatchPrediction(1, 1, "H1", "A1", 0.40, 0.27, 0.33, True),
            MatchPrediction(2, 2, "H2", "A2", 0.70, 0.18, 0.12),
            MatchPrediction(3, 3, "H3", "A3", 0.35, 0.33, 0.32),
            MatchPrediction(4, 4, "H4", "A4", 0.50, 0.10, 0.40),
            MatchPrediction(5, 5, "H5", "A5", 0.45, 0.35, 0.20),
        )
        plan = optimize_bet_plan(
            predictions,
            target="mini_a",
            double_count=5,
            triple_count=0,
        )
        frame = bet_plan_display_frame(plan)
        self.assertIn("候補", set(frame["引分候補"]))
        self.assertIn("—", set(frame["引分候補"]))
        self.assertTrue(
            any(value != "—" for value in frame["Draw Inclusion Score"])
        )
        self.assertTrue(
            any(value == "—" for value in frame["Draw Inclusion Score"])
        )

    def test_missing_required_columns_fail_with_names(self) -> None:
        plan = self._plan("mini_a", 2, 1)
        incomplete = bet_plan_frame(plan).drop(
            columns=["Draw Inclusion Score", "Draw Inclusion判定"]
        )
        with patch("bet_export.bet_plan_frame", return_value=incomplete):
            with self.assertRaisesRegex(
                BetPlanSchemaError,
                "Draw Inclusion Score.*Draw Inclusion判定",
            ):
                bet_plan_display_frame(plan)


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
        plan_csv = bet_plan_csv_bytes(plan)
        self.assertTrue(plan_csv.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(combination_csv_bytes(plan).startswith(b"\xef\xbb\xbf"))
        csv_frame = pd.read_csv(BytesIO(plan_csv), encoding="utf-8-sig")
        self.assertEqual(tuple(csv_frame.columns), BET_PLAN_FRAME_COLUMNS)
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
    def test_missing_required_history_column_is_named(self) -> None:
        history = completed_history().drop(columns=["actual_result"])

        with self.assertRaisesRegex(
            BetOptimizationError,
            "actual_result",
        ):
            backtest_bet_strategy(
                history,
                strategy="A",
                target="toto",
                prediction_version="Version7-A",
                double_count=0,
                triple_count=0,
                draw_candidate_threshold=0.25,
                draw_candidate_margin=0.05,
            )

    def test_optional_history_columns_can_be_absent(self) -> None:
        history = completed_history().drop(
            columns=[
                "prediction_date",
                "home_team",
                "away_team",
            ]
        )

        result = backtest_bet_strategy(
            history,
            strategy="A",
            target="toto",
            prediction_version="Version7-A",
            double_count=0,
            triple_count=0,
            draw_candidate_threshold=0.25,
            draw_candidate_margin=0.05,
        )

        self.assertEqual(result.evaluated_rounds, 2)

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

    def test_one_incomplete_payout_round_disables_roi_not_hit_evaluation(self) -> None:
        results = compare_bet_strategies(
            completed_history(),
            target="toto",
            prediction_version="Version7-A",
            double_count=1,
            triple_count=1,
            draw_candidate_threshold=0.25,
            draw_candidate_margin=0.05,
            payouts_by_round={
                1701: TotoPayouts(10_000, 1_000, 100),
                1702: {
                    "second_prize_yen": 2_000,
                    "third_prize_yen": 200,
                },
            },
        )

        self.assertTrue(all(item.evaluated_rounds == 2 for item in results))
        self.assertTrue(all(not item.payout_data_available for item in results))
        self.assertTrue(all(item.total_payout_yen is None for item in results))
        self.assertTrue(all(item.profit_yen is None for item in results))
        self.assertTrue(all(item.roi is None for item in results))


if __name__ == "__main__":
    unittest.main()
