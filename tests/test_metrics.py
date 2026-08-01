"""Version6のBrier、Log Loss、Calibration、ROIを確認する。"""

import math
import unittest

from metrics import (
    aggregate_roi,
    evaluate_model,
    toto_payout_for_hits,
)


class MetricsTest(unittest.TestCase):
    def test_perfect_labels_have_expected_probability_metrics(self) -> None:
        predictions = ["1", "0", "2"]
        probabilities = [
            {"1": 0.8, "0": 0.1, "2": 0.1},
            {"1": 0.1, "0": 0.8, "2": 0.1},
            {"1": 0.1, "0": 0.1, "2": 0.8},
        ]
        metrics = evaluate_model(
            predictions,
            probabilities,
            ["1", "0", "2"],
            stake_yen=100,
            payout_yen=500,
        )

        self.assertEqual(metrics.hit_count, 3)
        self.assertEqual(metrics.accuracy, 1.0)
        self.assertAlmostEqual(metrics.brier_score, 0.06)
        self.assertAlmostEqual(metrics.log_loss, -math.log(0.8))
        self.assertAlmostEqual(metrics.calibration_error, 0.2)
        self.assertAlmostEqual(metrics.expected_hits, 2.4)
        self.assertEqual(metrics.class_accuracy, {"1": 1.0, "0": 1.0, "2": 1.0})
        self.assertEqual(metrics.roi, 500.0)

    def test_toto_payout_and_aggregate_roi(self) -> None:
        self.assertEqual(toto_payout_for_hits(13, 1000, 200, 50), 1000)
        self.assertEqual(toto_payout_for_hits(12, 1000, 200, 50), 200)
        self.assertEqual(toto_payout_for_hits(11, 1000, 200, 50), 50)
        self.assertEqual(toto_payout_for_hits(10, 1000, 200, 50), 0)
        self.assertEqual(aggregate_roi([0, 200], [100, 100]), 100.0)


if __name__ == "__main__":
    unittest.main()
