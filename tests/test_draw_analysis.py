"""Version7-A画面用の比較表・確率帯・グラフ入力を確認する。"""

import unittest
from types import SimpleNamespace

from draw_analysis import (
    class_performance_frame,
    draw_bins_frame,
    evaluation_frame,
    parameters_frame,
    trial_score_frame,
    validation_comparison_frame,
)
from draw_evaluation import evaluate_draw_predictions
from draw_predictor import DEFAULT_DRAW_SETTINGS


def _evaluation(draw_probability: float):
    actuals = ["1", "0", "2", "0", "1", "2"]
    probabilities = [
        {
            "1": (1.0 - draw_probability) * 0.55,
            "0": draw_probability,
            "2": (1.0 - draw_probability) * 0.45,
        }
        for _ in actuals
    ]
    predictions = [max(row, key=row.get) for row in probabilities]
    return evaluate_draw_predictions(predictions, probabilities, actuals)


class DrawAnalysisTest(unittest.TestCase):
    def test_required_tables_and_graph_inputs_are_available(self) -> None:
        version6 = _evaluation(0.22)
        version7a = _evaluation(0.36)
        result = SimpleNamespace(
            validation_version6=version6,
            validation_best=version7a,
            trials=(
                SimpleNamespace(trial_number=0, score=50.0),
                SimpleNamespace(trial_number=1, score=55.0),
            ),
            best_settings=DEFAULT_DRAW_SETTINGS,
        )

        evaluation = evaluation_frame(version6, version7a, period="Validation")
        comparison = validation_comparison_frame(result)
        bins = draw_bins_frame(version7a)
        classes = class_performance_frame(result)
        trials = trial_score_frame(result)
        parameters = parameters_frame(result)

        self.assertEqual(len(evaluation), 2)
        self.assertIn("引分予測時の平均確率", evaluation.columns)
        self.assertTrue(
            {"1の成績", "0の成績", "2の成績"}.issubset(
                set(comparison["項目"])
            )
        )
        self.assertIn("評価", comparison.columns)
        self.assertEqual(len(bins), 6)
        self.assertEqual(set(classes["結果"]), {"1", "0", "2"})
        self.assertEqual(trials.index.tolist(), [0, 1])
        self.assertEqual(len(parameters), 10)


if __name__ == "__main__":
    unittest.main()
