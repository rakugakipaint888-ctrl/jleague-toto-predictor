"""開催回・Version別の履歴保存とCSV必須列を確認する。"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from history_manager import JAPAN_TIMEZONE, TotoMatch, TotoPayouts, TotoRound
from prediction_history import (
    HISTORY_COLUMNS,
    PredictionHistoryManager,
    finalize_prediction_results,
    records_from_prediction_results,
)


def result_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "試合": number,
                "対戦カード": "鹿島アントラーズ vs 浦和レッズ",
                "1": 60.0,
                "0": 25.0,
                "2": 15.0,
                "本命": "1",
                "version4_prediction": "1",
                "version5_prediction": "1",
                "version4_home_win": 55.0,
                "version4_draw": 25.0,
                "version4_away_win": 20.0,
                "home_expected_before_version5": 1.5,
                "away_expected_before_version5": 1.0,
                "home_expected_after_version5": 1.6,
                "away_expected_after_version5": 0.9,
            }
            for number in range(1, 14)
        ]
    )


def round_with_results(complete: bool = True) -> TotoRound:
    kickoff = datetime(2025, 6, 21, 15, 0, tzinfo=JAPAN_TIMEZONE)
    return TotoRound(
        round_id=1548,
        matches=tuple(
            TotoMatch(
                round_id=1548,
                match_number=number,
                home_team="鹿島アントラーズ",
                away_team="浦和レッズ",
                match_time=kickoff + timedelta(minutes=number),
                actual_result="1" if complete else None,
            )
            for number in range(1, 14)
        ),
        payouts=TotoPayouts(1000, 200, 50),
    )


class PredictionHistoryTest(unittest.TestCase):
    def test_actual_comparison_adds_hits_to_screen_csv(self) -> None:
        frame = result_frame().assign(
            toto_match_number=list(range(1, 14)),
            actual_result=["1"] * 12 + ["2"],
            hit=None,
            total_hits=None,
            accuracy=None,
        )

        finalized = finalize_prediction_results(frame)

        self.assertEqual(finalized["hit"].tolist(), [True] * 12 + [False])
        self.assertEqual(finalized["total_hits"].tolist(), [12] * 13)
        self.assertTrue((finalized["accuracy"] == 12 / 13).all())

    def test_numeric_draw_zero_is_not_lost_when_screen_results_are_finalized(self) -> None:
        frame = pd.DataFrame(
            {
                "actual_result": [0.0] * 13,
                "本命": [0] * 13,
            }
        )
        finalized = finalize_prediction_results(frame)
        self.assertTrue(finalized["hit"].all())
        self.assertTrue((finalized["total_hits"] == 13).all())

    def test_prediction_frame_expands_to_four_versions_and_saves_csv(self) -> None:
        records = records_from_prediction_results(
            result_frame(),
            round_with_results(),
        )
        self.assertEqual(len(records), 52)
        self.assertEqual(
            {record.prediction_version for record in records},
            {"Version4", "Version5", "Version6", "Version7-A"},
        )
        self.assertTrue(all(record.total_hits == 13 for record in records))

        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            self.assertTrue(
                manager.save_records(
                    records,
                    payouts_by_round={1548: round_with_results().payouts},
                )
            )
            loaded = manager.load()

        self.assertEqual(len(loaded), 52)
        self.assertEqual(tuple(loaded.columns), HISTORY_COLUMNS)
        for required in (
            "toto_round",
            "toto_match_number",
            "prediction_version",
            "actual_result",
            "hit",
            "total_hits",
            "accuracy",
            "prediction_date",
        ):
            self.assertIn(required, loaded.columns)

    def test_reconcile_adds_actual_results_to_saved_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            incomplete = round_with_results(complete=False)
            self.assertTrue(manager.save_prediction_results(result_frame(), incomplete))
            self.assertTrue(manager.reconcile_actual_results(round_with_results()))
            loaded = manager.load()

        self.assertTrue((loaded["actual_result"].astype(str) == "1").all())
        self.assertTrue((loaded["total_hits"] == 13).all())

    def test_completed_round_can_follow_an_incomplete_round_in_same_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            self.assertTrue(
                manager.save_prediction_results(
                    result_frame(),
                    round_with_results(complete=False),
                )
            )
            complete_records = records_from_prediction_results(
                result_frame(),
                round_with_results(complete=True),
            )
            complete_records = [
                {
                    **record.__dict__,
                    "toto_round": 1549,
                }
                for record in complete_records
            ]
            self.assertTrue(manager.save_records(complete_records))
            loaded = manager.load()

        self.assertEqual(len(loaded), 104)
        completed = loaded.loc[loaded["toto_round"] == 1549]
        self.assertEqual(len(completed), 52)
        self.assertEqual(set(completed["actual_result"]), {"1"})
        self.assertTrue((completed["total_hits"] == 13).all())


if __name__ == "__main__":
    unittest.main()
