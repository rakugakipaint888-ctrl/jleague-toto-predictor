"""開催回分析・Version比較・累積推移を確認する。"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from analysis import (
    build_analysis_tables,
    ensure_version7a_strategy_history,
    reconcile_saved_strategy_history,
    reconcile_saved_version7b_strategy_history,
    version7a_history_records,
)
from backtest import run_backtest
from bet_evaluation import compare_bet_strategies
from draw_predictor import DEFAULT_DRAW_SETTINGS
from history_manager import TotoRound, TotoRoundLoadResult, TotoRoundSummary
from prediction_history import PredictionHistoryManager
from tests.test_backtest import completed_round, historical_matches


class SingleRoundHistoryManager:
    def __init__(self):
        self.toto_round = completed_round()
        self.load_round_calls = 0

    def load_catalog(self):
        return (
            TotoRoundSummary(
                round_id=self.toto_round.round_id,
                fiscal_year=2025,
                label=f"第{self.toto_round.round_id}回",
            ),
        )

    def load_round(self, round_id):
        self.load_round_calls += 1
        if int(round_id) != self.toto_round.round_id:
            return TotoRoundLoadResult(
                toto_round=None,
                source_name="テスト",
                status="error",
                message="対象外",
            )
        return TotoRoundLoadResult(
            toto_round=self.toto_round,
            source_name="テスト",
            status="loaded",
            message="読み込みました。",
        )

    def load_saved_round(self, round_id):
        return self.toto_round if int(round_id) == self.toto_round.round_id else None


class MappedRoundHistoryManager:
    def __init__(self, rounds):
        self.rounds = {
            int(round_id): toto_round
            for round_id, toto_round in rounds.items()
        }

    def load_round(self, round_id):
        toto_round = self.rounds.get(int(round_id))
        return TotoRoundLoadResult(
            toto_round=toto_round,
            source_name="テスト",
            status="loaded" if toto_round is not None else "error",
            message="読み込みました。" if toto_round is not None else "対象外",
        )


def round_with_id(
    round_id: int,
    *,
    actual_results: tuple[str | None, ...],
) -> TotoRound:
    base = completed_round()
    return replace(
        base,
        round_id=int(round_id),
        matches=tuple(
            replace(
                match,
                round_id=int(round_id),
                actual_result=actual_results[index],
            )
            for index, match in enumerate(base.matches)
        ),
    )


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

    def test_empty_version7a_history_is_generated_and_not_duplicated(self) -> None:
        source_matches = historical_matches()
        history_manager = SingleRoundHistoryManager()
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            self.assertTrue(manager.load().empty)
            with patch(
                "analysis.collect_historical_matches",
                return_value=tuple(source_matches),
            ) as collect_mock:
                first = ensure_version7a_strategy_history(
                    prediction_history_manager=manager,
                    history_manager=history_manager,
                    settings=DEFAULT_DRAW_SETTINGS,
                    fresh_round_limit=1,
                )
            collect_mock.assert_called_once()

            saved = manager.load()
            version7a = saved.loc[
                saved["prediction_version"] == "Version7-A"
            ]
            self.assertEqual(first.target_round_count, 1)
            self.assertEqual(first.generated_round_count, 1)
            self.assertEqual(first.generated_match_count, 13)
            self.assertEqual(first.actual_result_count, 13)
            self.assertEqual(len(version7a), 13)
            self.assertEqual(
                set(saved["prediction_version"]),
                {"Version7-A"},
            )
            self.assertEqual(
                set(version7a["toto_match_number"].astype(int)),
                set(range(1, 14)),
            )
            self.assertTrue(
                version7a["actual_result"].isin(("1", "0", "2")).all()
            )
            compared = compare_bet_strategies(
                saved,
                target="toto",
                prediction_version="Version7-A",
                double_count=3,
                triple_count=0,
                draw_candidate_threshold=0.25,
                draw_candidate_margin=0.05,
            )
            self.assertEqual(len(compared), 3)
            self.assertTrue(
                all(result.evaluated_rounds == 1 for result in compared)
            )

            with patch(
                "analysis.collect_historical_matches"
            ) as second_collect_mock:
                second = ensure_version7a_strategy_history(
                    prediction_history_manager=manager,
                    history_manager=history_manager,
                    settings=DEFAULT_DRAW_SETTINGS,
                    fresh_round_limit=1,
                )
            second_collect_mock.assert_not_called()
            second_saved = manager.load()
            second_version7a = second_saved.loc[
                second_saved["prediction_version"] == "Version7-A"
            ]
            self.assertEqual(second.target_round_count, 1)
            self.assertEqual(second.generated_round_count, 0)
            self.assertEqual(second.generated_match_count, 0)
            self.assertEqual(second.actual_result_count, 13)
            self.assertEqual(len(second_version7a), 13)

    def test_saved_version6_round_is_used_without_catalog_preparation(self) -> None:
        toto_round = completed_round()
        source_matches = historical_matches()
        history_manager = SingleRoundHistoryManager()
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            version6_backtest = run_backtest(toto_round, source_matches)
            self.assertTrue(
                manager.save_records(version6_backtest.history_records())
            )
            before = manager.load()
            self.assertEqual(
                len(before.loc[before["prediction_version"] == "Version6"]),
                13,
            )
            self.assertEqual(
                len(before.loc[before["prediction_version"] == "Version7-A"]),
                0,
            )
            with (
                patch.object(
                    history_manager,
                    "load_catalog",
                    side_effect=AssertionError("catalog must not be required"),
                ) as catalog_mock,
                patch(
                    "analysis.collect_historical_matches",
                    return_value=tuple(source_matches),
                ),
            ):
                generated = ensure_version7a_strategy_history(
                    prediction_history_manager=manager,
                    history_manager=history_manager,
                    settings=DEFAULT_DRAW_SETTINGS,
                )
            catalog_mock.assert_not_called()
            after = manager.load()
            self.assertEqual(generated.target_round_ids, (1548,))
            self.assertEqual(generated.generated_round_ids, (1548,))
            self.assertEqual(
                len(after.loc[after["prediction_version"] == "Version6"]),
                13,
            )
            self.assertEqual(
                len(after.loc[after["prediction_version"] == "Version7-A"]),
                13,
            )
            self.assertNotIn("Version7-C", set(after["prediction_version"]))

    def test_unconfirmed_round_is_excluded_even_when_saved_labels_look_valid(
        self,
    ) -> None:
        confirmed_round = completed_round()
        unconfirmed_round = round_with_id(
            1645,
            actual_results=(None,) * 13,
        )
        source_matches = historical_matches()
        confirmed_records = version7a_history_records(
            confirmed_round,
            source_matches,
            settings=DEFAULT_DRAW_SETTINGS,
            generated_at=run_backtest(
                confirmed_round,
                source_matches,
            ).generated_at,
        )
        unconfirmed_records = [
            replace(
                record,
                toto_round=1645,
                actual_result="1",
                hit=record.prediction == "1",
            )
            for record in confirmed_records
        ]
        history_manager = MappedRoundHistoryManager(
            {
                confirmed_round.round_id: confirmed_round,
                1645: unconfirmed_round,
            }
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            self.assertTrue(
                manager.save_records(
                    [*confirmed_records, *unconfirmed_records]
                )
            )
            result = ensure_version7a_strategy_history(
                prediction_history_manager=manager,
                history_manager=history_manager,
                settings=DEFAULT_DRAW_SETTINGS,
            )
            saved = manager.load()

        self.assertEqual(result.target_round_ids, (1548,))
        self.assertEqual(result.failed_round_ids, (1645,))
        self.assertEqual(result.actual_result_count, 13)
        self.assertTrue(
            any(
                "第1645回" in message and "対象外" in message
                for message in result.messages
            )
        )
        compared = compare_bet_strategies(
            saved,
            target="toto",
            prediction_version="Version7-A",
            double_count=3,
            triple_count=0,
            draw_candidate_threshold=0.25,
            draw_candidate_margin=0.05,
            verified_round_ids=result.target_round_ids,
        )
        self.assertTrue(
            all(item.evaluated_round_ids == (1548,) for item in compared)
        )

    def test_version6_unconfirmed_round_is_not_evaluable(self) -> None:
        unconfirmed_round = round_with_id(
            1645,
            actual_results=(None,) * 13,
        )
        records = [
            {
                "toto_round": 1645,
                "toto_match_number": match_number,
                "prediction_version": "Version6",
                "prediction": "1",
                "probability_1": 0.6,
                "probability_0": 0.2,
                "probability_2": 0.2,
                "actual_result": "1",
            }
            for match_number in range(1, 14)
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            self.assertTrue(manager.save_records(records))
            result = reconcile_saved_strategy_history(
                prediction_history_manager=manager,
                history_manager=MappedRoundHistoryManager(
                    {1645: unconfirmed_round}
                ),
                prediction_version="Version6",
            )

        self.assertEqual(result.saved_round_ids, (1645,))
        self.assertEqual(result.evaluable_round_ids, ())
        self.assertEqual(result.excluded_round_ids, (1645,))
        self.assertEqual(result.actual_result_count, 0)
        self.assertTrue(
            any(
                "第1645回" in message and "対象外" in message
                for message in result.messages
            )
        )

    def test_version7b_reconciles_only_saved_predictions(self) -> None:
        toto_round = completed_round()
        source_matches = historical_matches()
        history_manager = SingleRoundHistoryManager()
        version7a_records = version7a_history_records(
            toto_round,
            source_matches,
            settings=DEFAULT_DRAW_SETTINGS,
            generated_at=run_backtest(toto_round, source_matches).generated_at,
        )
        version7b_records = [
            replace(
                record,
                prediction_version="Version7-B",
                actual_result="",
                hit=None,
            )
            for record in version7a_records
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = PredictionHistoryManager(
                Path(temporary_directory) / "prediction_history.csv"
            )
            self.assertTrue(manager.save_records(version7b_records))
            before = manager.load()
            self.assertEqual(len(before), 13)
            self.assertFalse(
                before["actual_result"].isin(("1", "0", "2")).any()
            )
            reconciled = reconcile_saved_version7b_strategy_history(
                prediction_history_manager=manager,
                history_manager=history_manager,
            )
            after = manager.load()

        self.assertEqual(reconciled.saved_round_ids, (1548,))
        self.assertEqual(reconciled.evaluable_round_ids, (1548,))
        self.assertEqual(reconciled.reconciled_round_ids, (1548,))
        self.assertEqual(reconciled.actual_result_count, 13)
        self.assertEqual(len(after), 13)
        self.assertEqual(set(after["prediction_version"]), {"Version7-B"})
        self.assertTrue(after["actual_result"].isin(("1", "0", "2")).all())

    def test_empty_history_can_be_regenerated_after_restart(self) -> None:
        source_matches = historical_matches()
        history_manager = SingleRoundHistoryManager()
        with tempfile.TemporaryDirectory() as temporary_directory:
            first_path = Path(temporary_directory) / "first" / "history.csv"
            restarted_path = Path(temporary_directory) / "restart" / "history.csv"
            first_manager = PredictionHistoryManager(first_path)
            restarted_manager = PredictionHistoryManager(restarted_path)
            with patch(
                "analysis.collect_historical_matches",
                return_value=tuple(source_matches),
            ):
                first = ensure_version7a_strategy_history(
                    prediction_history_manager=first_manager,
                    history_manager=history_manager,
                    settings=DEFAULT_DRAW_SETTINGS,
                    fresh_round_limit=1,
                )
                restarted = ensure_version7a_strategy_history(
                    prediction_history_manager=restarted_manager,
                    history_manager=history_manager,
                    settings=DEFAULT_DRAW_SETTINGS,
                    fresh_round_limit=1,
                )
            self.assertEqual(first.generated_match_count, 13)
            self.assertEqual(restarted.generated_match_count, 13)
            self.assertEqual(len(restarted_manager.load()), 13)

    def test_version7a_regeneration_ignores_future_match_results(self) -> None:
        toto_round = completed_round()
        source_matches = historical_matches()
        with_future = version7a_history_records(
            toto_round,
            source_matches,
            settings=DEFAULT_DRAW_SETTINGS,
            generated_at=run_backtest(toto_round, source_matches).generated_at,
        )
        without_future = version7a_history_records(
            toto_round,
            source_matches[:-1],
            settings=DEFAULT_DRAW_SETTINGS,
            generated_at=run_backtest(toto_round, source_matches[:-1]).generated_at,
        )
        self.assertEqual(
            [
                (
                    row.probability_1,
                    row.probability_0,
                    row.probability_2,
                )
                for row in with_future
            ],
            [
                (
                    row.probability_1,
                    row.probability_0,
                    row.probability_2,
                )
                for row in without_future
            ],
        )


if __name__ == "__main__":
    unittest.main()
