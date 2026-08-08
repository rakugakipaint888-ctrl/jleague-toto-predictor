"""引分Precision/Recall/F1/Brier/Calibrationと確率帯を確認する。"""

import unittest

from draw_evaluation import (
    draw_calibration_bins,
    evaluate_draw_predictions,
    normalize_toto_label,
)


class DrawEvaluationTest(unittest.TestCase):
    def test_draw_metrics_have_distinct_precision_recall_and_f1(self) -> None:
        predictions = ["0", "0", "1", "2", "1"]
        actuals = ["0", "1", "0", "2", "1"]
        probabilities = [
            {"1": 0.30, "0": 0.45, "2": 0.25},
            {"1": 0.32, "0": 0.40, "2": 0.28},
            {"1": 0.50, "0": 0.30, "2": 0.20},
            {"1": 0.20, "0": 0.20, "2": 0.60},
            {"1": 0.60, "0": 0.20, "2": 0.20},
        ]
        evaluation = evaluate_draw_predictions(
            predictions,
            probabilities,
            actuals,
            candidate_flags=[True, True, True, False, False],
        )

        draw = evaluation.draw
        self.assertEqual(draw.actual_draw_count, 2)
        self.assertEqual(draw.predicted_draw_count, 2)
        self.assertEqual(draw.draw_hit_count, 1)
        self.assertEqual(draw.precision, 0.5)
        self.assertEqual(draw.recall, 0.5)
        self.assertEqual(draw.f1_score, 0.5)
        self.assertIsNotNone(draw.brier_score)
        self.assertIsNotNone(draw.calibration_error)
        self.assertEqual(len(draw.calibration_bins), 6)
        self.assertEqual(sum(item.count for item in draw.calibration_bins), 5)

    def test_zero_counts_do_not_divide_by_zero(self) -> None:
        evaluation = evaluate_draw_predictions(
            ["1", "2"],
            [{"1": 0.6, "0": 0.2, "2": 0.2}] * 2,
            ["1", "2"],
            candidate_flags=[False, False],
        )
        self.assertEqual(evaluation.draw.actual_draw_count, 0)
        self.assertEqual(evaluation.draw.predicted_draw_count, 0)
        self.assertEqual(evaluation.draw.precision, 0.0)
        self.assertEqual(evaluation.draw.recall, 0.0)
        self.assertEqual(evaluation.draw.f1_score, 0.0)

    def test_numeric_zero_is_not_treated_as_missing(self) -> None:
        self.assertEqual(normalize_toto_label(0), "0")
        self.assertEqual(normalize_toto_label(0.0), "0")
        self.assertEqual(normalize_toto_label(False), "")
        evaluation = evaluate_draw_predictions(
            [0],
            [{"1": 0.2, "0": 0.6, "2": 0.2}],
            [0.0],
        )
        self.assertEqual(evaluation.draw.draw_hit_count, 1)

    def test_invalid_calibration_probabilities_are_ignored_safely(self) -> None:
        bins, calibration = draw_calibration_bins(
            [float("nan"), float("inf"), None, 0.30],
            ["0", "0", "0", "0"],
        )
        self.assertEqual(len(bins), 6)
        self.assertEqual(sum(item.count for item in bins), 1)
        self.assertAlmostEqual(calibration, 0.70)


if __name__ == "__main__":
    unittest.main()
