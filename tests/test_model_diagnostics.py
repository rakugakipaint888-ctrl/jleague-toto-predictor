"""Version8-Bの実戦履歴診断・異常検知・読取専用性を確認する。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from bet_optimizer import build_match_predictions, optimize_bet_plan
from diagnostic_config import DEFAULT_DIAGNOSTIC_THRESHOLDS
from diagnostic_history import DiagnosticHistoryError, DiagnosticHistoryManager
from history_manager import JAPAN_TIMEZONE, TotoMatch, TotoPayouts, TotoRound
from live_history import LiveHistoryManager
from model_diagnostics import (
    DiagnosticFilter,
    run_model_diagnostics,
)


BASE_TIME = datetime(2026, 1, 10, 10, 0, tzinfo=JAPAN_TIMEZONE)


def prediction_frame_for(
    round_id: int,
    *,
    version: str = "Version7-B",
    favorites: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    favorites = favorites or tuple(("1", "0", "2")[(n - 1) % 3] for n in range(1, 14))
    probability_by_favorite = {
        "1": (70.0, 20.0, 10.0),
        "0": (20.0, 60.0, 20.0),
        "2": (10.0, 20.0, 70.0),
    }
    rows = []
    for number, favorite in enumerate(favorites, start=1):
        p1, p0, p2 = probability_by_favorite[favorite]
        rows.append(
            {
                "toto_round": round_id,
                "toto_match_number": number,
                "prediction_version": version,
                "league": ("J1", "J2", "J3")[(number - 1) % 3],
                "1": p1,
                "0": p0,
                "2": p2,
                "本命": favorite,
                "予想スコア": "1−0",
                "対戦カード": f"ホーム{round_id}-{number} vs アウェイ{round_id}-{number}",
                "draw_candidate": p0 >= 30.0,
                "draw_candidate_threshold": 0.25,
                "draw_candidate_reasons": "テスト候補" if p0 >= 30.0 else "",
                "version7b_home_elo": 1510.0,
                "version7b_away_elo": 1490.0,
                "version7b_elo_difference": 20.0,
                "home_expected_after_version7b": 1.5,
                "away_expected_after_version7b": 1.0,
            }
        )
    return pd.DataFrame(rows)


def round_for(
    round_id: int,
    offset: int,
    *,
    actuals: dict[int, str] | None = None,
    payouts: TotoPayouts | None = None,
) -> TotoRound:
    start = BASE_TIME + timedelta(days=offset * 7)
    matches = []
    for number in range(1, 14):
        actual = (actuals or {}).get(number)
        scores = {"1": (2, 1), "0": (1, 1), "2": (0, 1)}
        home_goals, away_goals = scores.get(actual, (None, None))
        matches.append(
            TotoMatch(
                round_id=round_id,
                match_number=number,
                home_team=f"ホーム{round_id}-{number}",
                away_team=f"アウェイ{round_id}-{number}",
                match_time=start + timedelta(hours=number),
                actual_result=actual,
                home_goals=home_goals,
                away_goals=away_goals,
            )
        )
    return TotoRound(
        round_id=round_id,
        matches=tuple(matches),
        payouts=payouts or TotoPayouts(),
        source_url="https://example.invalid/official",
    )


class ModelDiagnosticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.manager = LiveHistoryManager(
            round_path=root / "live_round_history.csv",
            match_path=root / "live_match_history.csv",
            bet_path=root / "live_bet_history.csv",
        )
        self.diagnostic_history = DiagnosticHistoryManager(
            root / "model_diagnostic_history.csv"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def save_round(
        self,
        index: int,
        *,
        version: str = "Version7-B",
        favorites: tuple[str, ...] | None = None,
        actuals: dict[int, str] | None = None,
        complete: bool = True,
        evaluate: bool = True,
        recommended: bool = False,
        purchased: bool = False,
    ) -> str:
        round_id = 3000 + index
        frame = prediction_frame_for(
            round_id,
            version=version,
            favorites=favorites,
        )
        prediction_time = BASE_TIME + timedelta(days=index * 7 - 1)
        pending_round = round_for(round_id, index)
        outcome = self.manager.save_prediction(
            frame,
            pending_round,
            settings_snapshot={
                "schema_version": 1,
                "prediction_version": version,
                "model_parameters": {"home_correction": 1.08},
                "draw_parameters": {"candidate_threshold": 0.25},
                "settings_group_test": "A" if index <= 5 else "B",
            },
            prediction_time=prediction_time,
            source_name="toto公式",
        )
        plan = None
        if recommended or purchased:
            plan = optimize_bet_plan(
                build_match_predictions(frame, "toto"),
                target="toto",
                double_count=2,
                triple_count=0,
            )
        recommendation_id = ""
        if recommended and plan is not None:
            recommendation_id = self.manager.save_recommended_bet(
                outcome.prediction_run_id,
                plan,
                generated_at=prediction_time,
            )
        if purchased and plan is not None:
            self.manager.record_purchase(
                outcome.prediction_run_id,
                plan,
                actual_purchase_amount_yen=plan.purchase_amount_yen,
                purchased_at=prediction_time + timedelta(minutes=5),
                source_recommendation_id=recommendation_id,
            )
        if not complete:
            return outcome.prediction_run_id
        if actuals is None:
            actuals = {
                number: str(frame.iloc[number - 1]["本命"])
                for number in range(1, 14)
            }
        completed_round = round_for(
            round_id,
            index,
            actuals=actuals,
            payouts=TotoPayouts(
                first_prize_yen=1_000_000,
                second_prize_yen=100_000,
                third_prize_yen=10_000,
            ),
        )
        self.manager.update_actual_results(
            outcome.prediction_run_id,
            completed_round,
            source_name="toto公式",
        )
        if evaluate:
            self.manager.evaluate_run(outcome.prediction_run_id)
        return outcome.prediction_run_id

    def test_zero_history_is_data_insufficient(self) -> None:
        report = run_model_diagnostics(self.manager)
        self.assertEqual(report.status, "データ不足")
        self.assertEqual(report.counts.match_count, 0)
        self.assertEqual(report.counts.confirmed_run_count, 0)
        self.assertIsNone(report.overall)

    def test_one_confirmed_run_calculates_metrics_but_remains_insufficient(self) -> None:
        self.save_round(1)
        report = run_model_diagnostics(self.manager)
        self.assertEqual(report.status, "データ不足")
        self.assertEqual(report.counts.match_count, 13)
        self.assertEqual(report.counts.round_count, 1)
        self.assertAlmostEqual(report.overall.accuracy, 1.0)
        self.assertIsNotNone(report.overall.brier_score)
        self.assertIsNotNone(report.overall.log_loss)
        self.assertIsNotNone(report.overall.calibration_error)
        self.assertEqual(len(report.calibration_table), 18)
        self.assertEqual(
            set(report.calibration_table["結果"]),
            {"1", "0", "2"},
        )

    def test_unconfirmed_run_is_excluded_and_purchase_absence_has_no_actual_roi(self) -> None:
        self.save_round(1)
        self.save_round(2, complete=False)
        report = run_model_diagnostics(self.manager)
        self.assertEqual(report.counts.predicted_run_count, 2)
        self.assertEqual(report.counts.pending_run_count, 1)
        self.assertEqual(report.counts.confirmed_run_count, 1)
        self.assertEqual(report.counts.match_count, 13)
        self.assertFalse(report.purchase_performance["has_records"])
        self.assertIsNone(report.purchase_performance["roi"])
        self.assertEqual(len(report.timeline), 2)
        self.assertTrue(pd.isna(report.timeline.iloc[-1]["的中率"]))

    def test_pending_purchase_is_not_in_actual_roi_or_coverage_results(self) -> None:
        self.save_round(
            1,
            complete=False,
            recommended=True,
            purchased=True,
        )
        report = run_model_diagnostics(self.manager)
        purchased = report.bet_summary.loc[
            report.bet_summary["区分"] == "実購入（actual）"
        ].iloc[0]
        self.assertEqual(purchased["run数"], 1)
        self.assertEqual(purchased["評価済み買い目数"], 0)
        self.assertFalse(report.purchase_performance["has_evaluated_records"])
        self.assertIsNone(report.purchase_performance["roi"])
        purchased_coverage = report.coverage_summary.loc[
            report.coverage_summary["区分"] == "実購入"
        ]
        self.assertEqual(int(purchased_coverage["評価済み数"].sum()), 0)

    def test_multiple_runs_class_metrics_and_no_draw_zero_division(self) -> None:
        all_home = tuple("1" for _ in range(13))
        actuals = {number: "1" for number in range(1, 14)}
        self.save_round(1, favorites=all_home, actuals=actuals)
        self.save_round(2, favorites=all_home, actuals=actuals)
        report = run_model_diagnostics(self.manager)
        self.assertEqual(report.status, "正常")
        draw = report.class_metrics["0"]
        self.assertEqual(draw.actual_count, 0)
        self.assertEqual(draw.predicted_count, 0)
        self.assertEqual((draw.precision, draw.recall, draw.f1_score), (0.0, 0.0, 0.0))

    def test_no_predicted_draw_with_actual_draw_is_detected(self) -> None:
        all_home = tuple("1" for _ in range(13))
        actuals = {number: ("0" if number <= 5 else "1") for number in range(1, 14)}
        self.save_round(1, favorites=all_home, actuals=actuals)
        self.save_round(2, favorites=all_home, actuals=actuals)
        report = run_model_diagnostics(self.manager)
        codes = {item.code for item in report.anomalies}
        self.assertIn("draw_favorite_rate_gap", codes)
        self.assertIn("low_recall_0", codes)
        self.assertEqual(report.draw.precision, 0.0)
        self.assertEqual(report.draw.recall, 0.0)

    def test_period_league_version_and_custom_date_filters(self) -> None:
        self.save_round(1, version="Version7-A")
        self.save_round(2, version="Version7-B")
        version_report = run_model_diagnostics(
            self.manager,
            DiagnosticFilter(version="Version7-A"),
        )
        self.assertEqual(version_report.counts.match_count, 13)
        league_report = run_model_diagnostics(
            self.manager,
            DiagnosticFilter(league="J1"),
        )
        self.assertEqual(league_report.counts.match_count, 10)
        custom = run_model_diagnostics(
            self.manager,
            DiagnosticFilter(
                period="任意期間",
                start_date=(BASE_TIME + timedelta(days=13)).date(),
                end_date=(BASE_TIME + timedelta(days=15)).date(),
            ),
        )
        self.assertEqual(custom.counts.round_count, 1)

    def test_recent_five_shortage_is_not_fabricated(self) -> None:
        for index in range(1, 4):
            self.save_round(index)
        report = run_model_diagnostics(
            self.manager,
            DiagnosticFilter(period="直近5開催"),
        )
        self.assertEqual(report.status, "データ不足")
        self.assertTrue(report.period_shortage)
        self.assertEqual(report.period_available_round_count, 3)

    def test_rolling_degradation_detects_accuracy_brier_logloss_calibration_and_draw_f1(self) -> None:
        for index in range(1, 6):
            self.save_round(index)
        all_home = tuple("1" for _ in range(13))
        bad_actuals = {number: "0" for number in range(1, 14)}
        for index in range(6, 11):
            self.save_round(index, favorites=all_home, actuals=bad_actuals)
        report = run_model_diagnostics(self.manager)
        codes = {item.code for item in report.anomalies}
        for prefix in (
            "accuracy_drop",
            "brier_increase",
            "log_loss_increase",
            "calibration_increase",
            "draw_f1_drop",
        ):
            self.assertTrue(any(code.startswith(prefix) for code in codes), prefix)
        rolling5 = report.rolling_summary.loc[
            report.rolling_summary["期間"] == "直近5開催"
        ].iloc[0]
        self.assertEqual(rolling5["状態"], "診断可能")
        self.assertLess(rolling5["的中率"], rolling5["全期間的中率"])

    def test_league_version_and_settings_group_summaries(self) -> None:
        for index in range(1, 7):
            self.save_round(
                index,
                version="Version7-A" if index <= 3 else "Version7-B",
            )
        report = run_model_diagnostics(self.manager)
        self.assertEqual(set(report.league_summary["リーグ"]), {"J1", "J2", "J3"})
        self.assertEqual(set(report.version_summary["Version"]), {"Version7-A", "Version7-B"})
        self.assertEqual(len(report.settings_summary), 3)
        self.assertTrue(report.settings_summary["設定group"].str.startswith("setting_").all())

    def test_league_gap_reports_the_metric_that_triggered_it(self) -> None:
        actuals = {
            number: (
                "1"
                if (number - 1) % 3 in (0, 1)
                else "2"
            )
            for number in range(1, 14)
        }
        for index in range(1, 8):
            self.save_round(index, actuals=actuals)
        report = run_model_diagnostics(self.manager)
        anomalies = {
            item.code: item
            for item in report.anomalies
            if item.code.endswith("_J2")
        }
        self.assertIn("league_accuracy_gap_J2", anomalies)
        self.assertIn("league_brier_gap_J2", anomalies)
        self.assertEqual(anomalies["league_accuracy_gap_J2"].metric, "的中率")
        self.assertEqual(
            anomalies["league_brier_gap_J2"].metric,
            "Brier Score",
        )
        self.assertLess(
            anomalies["league_accuracy_gap_J2"].current_value,
            anomalies["league_accuracy_gap_J2"].baseline_value,
        )

    def test_bet_recommended_purchased_roi_and_coverage_are_separate(self) -> None:
        self.save_round(1, recommended=True, purchased=True)
        self.save_round(2, recommended=True, purchased=False)
        report = run_model_diagnostics(self.manager)
        recommended = report.bet_summary.loc[
            report.bet_summary["区分"] == "AI推奨（simulation）"
        ].iloc[0]
        purchased = report.bet_summary.loc[
            report.bet_summary["区分"] == "実購入（actual）"
        ].iloc[0]
        self.assertEqual(recommended["買い目数"], 2)
        self.assertEqual(purchased["買い目数"], 1)
        self.assertTrue(report.purchase_performance["has_evaluated_records"])
        self.assertIsNotNone(report.purchase_performance["roi"])
        self.assertTrue(report.simulation_performance["has_evaluated_records"])
        self.assertGreater(len(report.coverage_summary), 0)
        self.assertIsNotNone(report.draw.recommended_draw_inclusion_rate)
        self.assertIsNotNone(report.draw.purchased_draw_inclusion_rate)

    def test_probability_sum_anomaly_is_excluded_and_warned(self) -> None:
        self.save_round(1)
        frame = pd.read_csv(
            self.manager.match_path,
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
        )
        frame.at[0, "probability_1"] = "0.99"
        frame.to_csv(self.manager.match_path, index=False, encoding="utf-8-sig")
        report = run_model_diagnostics(self.manager)
        codes = {item.code for item in report.quality_issues}
        self.assertIn("probability_sum_anomaly", codes)
        self.assertEqual(report.status, "警告")
        self.assertEqual(report.counts.match_count, 0)

    def test_invalid_actual_nan_and_infinity_do_not_crash(self) -> None:
        self.save_round(1)
        frame = pd.read_csv(
            self.manager.match_path,
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
        )
        frame.at[0, "actual_result"] = "9"
        frame.at[1, "probability_1"] = "NaN"
        frame.at[2, "probability_2"] = "Infinity"
        frame.to_csv(self.manager.match_path, index=False, encoding="utf-8-sig")
        report = run_model_diagnostics(self.manager)
        codes = {item.code for item in report.quality_issues}
        self.assertIn("invalid_actual_result", codes)
        self.assertTrue(
            "missing_probabilities" in codes or "invalid_probabilities" in codes
        )
        self.assertEqual(report.status, "警告")

    def test_duplicate_run_missing_match_version_and_snapshot_are_detected(self) -> None:
        self.save_round(1)
        rounds = pd.read_csv(
            self.manager.round_path,
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
        )
        rounds.at[0, "settings_snapshot_json"] = ""
        rounds = pd.concat([rounds, rounds.iloc[[0]]], ignore_index=True)
        rounds.to_csv(self.manager.round_path, index=False, encoding="utf-8-sig")
        matches = pd.read_csv(
            self.manager.match_path,
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
        )
        matches.at[0, "prediction_version"] = ""
        matches = matches.iloc[:-1]
        matches.to_csv(self.manager.match_path, index=False, encoding="utf-8-sig")
        report = run_model_diagnostics(self.manager)
        codes = {item.code for item in report.quality_issues}
        self.assertIn("duplicate_prediction_run_id", codes)
        self.assertIn("missing_settings_snapshot", codes)
        self.assertIn("missing_prediction_version", codes)
        self.assertIn("incomplete_run_matches", codes)

    def test_diagnostic_history_is_idempotent_and_contains_explainable_values(self) -> None:
        self.save_round(1)
        report = run_model_diagnostics(self.manager)
        self.assertTrue(self.diagnostic_history.save(report))
        self.assertFalse(self.diagnostic_history.save(report))
        history = self.diagnostic_history.load()
        self.assertEqual(len(history), 1)
        self.assertEqual(history.iloc[0]["diagnostic_id"], report.diagnostic_id)
        self.assertEqual(history.iloc[0]["model_status"], "データ不足")
        self.assertIsInstance(json.loads(history.iloc[0]["thresholds_json"]), dict)

    def test_corrupt_diagnostic_history_is_not_overwritten(self) -> None:
        self.save_round(1)
        report = run_model_diagnostics(self.manager)
        self.diagnostic_history.path.write_text('"unterminated', encoding="utf-8")
        original = self.diagnostic_history.path.read_bytes()
        self.assertTrue(self.diagnostic_history.load().empty)
        with self.assertRaises(DiagnosticHistoryError):
            self.diagnostic_history.save(report)
        self.assertEqual(self.diagnostic_history.path.read_bytes(), original)

    def test_diagnostics_never_modify_version8a_files(self) -> None:
        self.save_round(1, recommended=True, purchased=True)
        before = {
            path: path.read_bytes()
            for path in (
                self.manager.round_path,
                self.manager.match_path,
                self.manager.bet_path,
            )
        }
        run_model_diagnostics(self.manager)
        after = {path: path.read_bytes() for path in before}
        self.assertEqual(before, after)

    def test_thresholds_are_centralized_and_reasonable(self) -> None:
        values = DEFAULT_DIAGNOSTIC_THRESHOLDS
        self.assertEqual(values.minimum_match_count, 26)
        self.assertEqual(values.minimum_round_count, 2)
        self.assertLess(values.accuracy_drop_attention, values.accuracy_drop_warning)
        self.assertEqual(values.probability_sum_tolerance, 1e-9)


if __name__ == "__main__":
    unittest.main()
