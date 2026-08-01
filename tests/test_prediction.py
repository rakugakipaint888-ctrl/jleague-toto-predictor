"""Version 1からの予測計算が変わっていないことを確認する。"""

import unittest

from prediction import (
    calculate_expected_goals,
    calculate_match_probabilities,
    create_reason,
    get_confidence_label,
    get_toto_prediction,
    poisson_probability,
)


class PredictionTest(unittest.TestCase):
    def test_expected_goals_keeps_original_home_adjustment(self) -> None:
        home_expected, away_expected = calculate_expected_goals(
            home_scored=1.4,
            home_conceded=1.2,
            away_scored=1.2,
            away_conceded=1.4,
        )

        self.assertAlmostEqual(home_expected, 1.512)
        self.assertAlmostEqual(away_expected, 1.2)

    def test_poisson_and_match_probabilities_are_normalized(self) -> None:
        self.assertAlmostEqual(poisson_probability(0, 1.0), 0.3678794412)

        probabilities = calculate_match_probabilities(1.512, 1.2)
        total = (
            probabilities["home_win"]
            + probabilities["draw"]
            + probabilities["away_win"]
        )

        self.assertAlmostEqual(total, 1.0)
        self.assertEqual(probabilities["home_goals"], 1)
        self.assertEqual(probabilities["away_goals"], 1)

    def test_toto_label_confidence_and_reason(self) -> None:
        prediction, probability = get_toto_prediction(0.62, 0.2, 0.18)

        self.assertEqual(prediction, "1")
        self.assertEqual(probability, 0.62)
        self.assertEqual(get_confidence_label([0.62, 0.2, 0.18]), "鉄板候補")
        self.assertEqual(
            create_reason(2.0, 1.0, 0.62, 0.2, 0.18),
            "ホーム側の期待得点がアウェイ側を大きく上回っています。",
        )


if __name__ == "__main__":
    unittest.main()
