"""開催回分析・Version比較・累積推移を確認する。"""

import unittest

import pandas as pd

from analysis import build_analysis_tables


def history_frame() -> pd.DataFrame:
    rows = []
    for round_id in (1548, 1549):
        for version in ("Version4", "Version5", "Version6"):
            for match_number in range(1, 14):
                actual = ("1", "0", "2")[(match_number - 1) % 3]
                prediction = actual if version != "Version4" else "1"
                rows.append(
                    {
                        "toto_round": round_id,
                        "toto_match_number": match_number,
                        "prediction_version": version,
                        "prediction": prediction,
                        "actual_result": actual,
                        "probability_1": 0.6 if prediction == "1" else 0.2,
                        "probability_0": 0.6 if prediction == "0" else 0.2,
                        "probability_2": 0.6 if prediction == "2" else 0.2,
                        "stake_yen": 100,
                        "payout_yen": 0,
                    }
                )
    return pd.DataFrame(rows)


class AnalysisTest(unittest.TestCase):
    def test_round_version_and_cumulative_tables_are_created(self) -> None:
        tables = build_analysis_tables(history_frame())

        self.assertEqual(len(tables.round_summary), 6)
        self.assertEqual(len(tables.version_summary), 3)
        self.assertEqual(
            set(tables.version_summary["Version"]),
            {"Version4", "Version5", "Version6"},
        )
        version6 = tables.version_summary.loc[
            tables.version_summary["Version"] == "Version6"
        ].iloc[0]
        self.assertEqual(version6["累積開催数"], 2)
        self.assertEqual(version6["累積的中率"], 1.0)
        self.assertFalse(tables.cumulative_trend.empty)
        self.assertFalse(tables.class_accuracy_trend.empty)
        self.assertFalse(tables.prediction_share_trend.empty)
        self.assertFalse(tables.calibration.empty)


if __name__ == "__main__":
    unittest.main()
