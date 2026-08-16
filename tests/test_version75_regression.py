"""Version7.5でVersion7-Cの数値・買い目を変えない回帰基準。"""

from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

import pandas as pd

from bet_optimizer import build_match_predictions, optimize_bet_plan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "version75_regression_baseline.json"
FLOAT_TOLERANCE = 1e-12


def _plan_snapshot(plan):
    rows = []
    for recommendation in plan.recommendations:
        analysis = recommendation.analysis
        prediction = analysis.prediction
        rows.append(
            {
                "bet_type": recommendation.bet_type,
                "coverage": recommendation.coverage,
                "double_candidate_score": analysis.double_candidate_score,
                "draw_candidate": analysis.draw_candidate,
                "draw_inclusion_recommended": analysis.draw_inclusion_recommended,
                "draw_inclusion_score": analysis.draw_inclusion_score,
                "match_number": prediction.match_number,
                "outcomes": list(recommendation.outcomes),
                "probability_0": prediction.probability_0,
                "probability_1": prediction.probability_1,
                "probability_2": prediction.probability_2,
                "single_confidence": analysis.single_confidence,
                "source_match_number": prediction.source_match_number,
                "top_outcome": analysis.top_outcome,
                "triple_candidate_score": analysis.triple_candidate_score,
                "uncertainty_score": analysis.uncertainty_score,
            }
        )
    return {
        "estimated_full_coverage": plan.estimated_full_coverage,
        "purchase_amount_yen": plan.purchase_amount_yen,
        "rows": rows,
        "ticket_count": plan.ticket_count,
    }


class Version75RegressionBaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        cls.frame = pd.DataFrame(cls.baseline["probability_input"])

    def assertSnapshotEqual(self, expected, actual, path="root") -> None:
        if isinstance(expected, float):
            self.assertIsInstance(actual, (int, float), path)
            self.assertTrue(
                math.isclose(
                    expected,
                    float(actual),
                    rel_tol=0.0,
                    abs_tol=FLOAT_TOLERANCE,
                ),
                f"{path}: {expected!r} != {actual!r}",
            )
            return
        if isinstance(expected, dict):
            self.assertEqual(set(expected), set(actual), path)
            for key in expected:
                self.assertSnapshotEqual(
                    expected[key], actual[key], f"{path}.{key}"
                )
            return
        if isinstance(expected, list):
            self.assertEqual(len(expected), len(actual), path)
            for index, (expected_item, actual_item) in enumerate(
                zip(expected, actual)
            ):
                self.assertSnapshotEqual(
                    expected_item,
                    actual_item,
                    f"{path}[{index}]",
                )
            return
        self.assertEqual(expected, actual, path)

    def test_version7c_plans_match_pre_refactor_baseline(self) -> None:
        cases = {
            "toto": ("toto", 3, 0),
            "mini_a": ("mini_a", 3, 0),
            "mini_b": ("mini_b", 3, 0),
            "toto_with_triple": ("toto", 2, 1),
        }
        for name, (target, double_count, triple_count) in cases.items():
            with self.subTest(name=name):
                plan = optimize_bet_plan(
                    build_match_predictions(self.frame, target),
                    target=target,
                    double_count=double_count,
                    triple_count=triple_count,
                )
                self.assertSnapshotEqual(
                    self.baseline["plans"][name],
                    _plan_snapshot(plan),
                    name,
                )

    def test_all_baseline_probabilities_sum_to_one(self) -> None:
        for target in ("toto", "mini_a", "mini_b"):
            for prediction in build_match_predictions(self.frame, target):
                self.assertAlmostEqual(
                    sum(prediction.probabilities.values()),
                    1.0,
                    delta=FLOAT_TOLERANCE,
                )


if __name__ == "__main__":
    unittest.main()
