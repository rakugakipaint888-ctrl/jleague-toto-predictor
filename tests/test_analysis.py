"""開催回分析・Version比較・累積推移を確認する。"""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from analysis import build_analysis_tables, version7a_history_records
from backtest import run_backtest
from bet_evaluation import compare_bet_strategies
from draw_predictor import DEFAULT_DRAW_SETTINGS
from prediction_history import PredictionHistoryManager
from tests.test_backtest import completed_round, historical_matches


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

    def test_same_completed_round_supports_version6_and_version7a_strategy_backtest(
        self,
    ) -> None:
        toto_round = completed_round()
        source_matches = historical_matches()
        backtest_result = run_backtest(toto_round, source_matches)
        version7a_records = version7a_history_records(
            toto_round,
            source_matches,
            settings=DEFAULT_DRAW_SETTINGS,
            generated_at=backtest_result.generated_at,
        )
        all_records = [
            *backtest_result.history_records(),
            *version7a_records,
        ]

        self.assertEqual(len(version7a_records), 13)
        self.assertEqual(
            {record.toto_match_number for record in version7a_records},
            set(range(1, 14)),
        )
        self.assertTrue(
            all(
                record.actual_result in ("1", "0", "2")
                for record in version7a_records
            )
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            self.assertTrue(
                manager.save_records(
                    all_records,
                    payouts_by_round={toto_round.round_id: toto_round.payouts},
                )
            )
            self.assertTrue(manager.reconcile_actual_results(toto_round))
            saved = manager.load()

        version_counts = saved.groupby("prediction_version").size().to_dict()
        self.assertEqual(version_counts["Version6"], 13)
        self.assertEqual(version_counts["Version7-A"], 13)
        for version in ("Version6", "Version7-A"):
            compared = compare_bet_strategies(
                saved,
                target="toto",
                prediction_version=version,
                double_count=3,
                triple_count=0,
                draw_candidate_threshold=0.25,
                draw_candidate_margin=0.05,
            )
            self.assertTrue(
                all(result.evaluated_rounds == 1 for result in compared)
            )
            self.assertTrue(
                all(result.evaluated_round_ids == (1548,) for result in compared)
            )


if __name__ == "__main__":
    unittest.main()
