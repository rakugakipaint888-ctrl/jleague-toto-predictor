"""Version7-B全体最適化の探索・評価・採用・時系列安全性を確認する。"""

from __future__ import annotations

import builtins
import math
import tempfile
import unittest
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from bootstrap_evaluation import bootstrap_evaluate_rows
from data_loader import JAPAN_TIMEZONE, OfficialMatch
from history_manager import TotoMatch, TotoRound
from model_compare import (
    bootstrap_frame,
    parameter_comparison_frame,
    ranking_frame,
    training_validation_frame,
    trial_metrics_frame,
    version7a_comparison_frame,
)
from model_evaluation import (
    EvaluationWeights,
    PredictionRow,
    build_stability_summary,
    check_draw_degradation,
    check_overfitting,
    evaluate_candidate_rows,
)
from model_optimizer import (
    ALL_LEAGUES,
    GRID_SEARCH,
    OPTUNA_SEARCH,
    RANDOM_SEARCH,
    TWO_STAGE_SEARCH,
    ModelOptimizationDataset,
    ModelOptimizationError,
    SearchConfiguration,
    build_search_plan,
    grid_combination_count,
    mark_optimization_adopted,
    predict_round_rows,
    prepare_model_dataset,
    prepare_model_round,
    run_model_optimization,
    save_model_ranking,
    save_optimization_history,
)
from parameter_manager import (
    ActiveVersion7BSettings,
    ModelParameters,
    Version7BParameters,
    adopt_version7b_settings,
    load_active_version7b_settings,
    restore_latest_version7b_settings,
    to_runtime_settings,
)
from walk_forward_validator import (
    FIXED_SPLIT,
    ROUND_WALK_FORWARD,
    SEASON_WALK_FORWARD,
    ValidationDataError,
    ValidationFold,
    ValidationSplit,
    create_validation_split,
)


@dataclass(frozen=True)
class _RoundStub:
    round_id: int
    cutoff_at: datetime
    season: str
    league: str = "J1"

    def match_count(self, target_league: str) -> int:
        return 6 if target_league in (ALL_LEAGUES, self.league) else 0


def _stub(round_id: int, year: int, season: str | None = None) -> _RoundStub:
    return _RoundStub(
        round_id,
        datetime(year, 6, 20, 0, 0, tzinfo=JAPAN_TIMEZONE),
        season or str(year),
    )


def _lightweight_dataset(*, final_round_id: int = 300) -> ModelOptimizationDataset:
    first = _stub(100, 2023)
    second = _stub(200, 2024)
    final = _stub(final_round_id, 2025)
    split = ValidationSplit(
        method=SEASON_WALK_FORWARD,
        training_rounds=(first, second),
        final_validation_rounds=(final,),
        folds=(ValidationFold("2024シーズン検証", (first,), (second,)),),
    )
    return ModelOptimizationDataset(
        split=split,
        target_league=ALL_LEAGUES,
        requested_period="テスト3シーズン",
        available_leagues=("J1",),
        unavailable_leagues=("J2", "J3"),
    )


def _fake_evaluate_parameter_set(
    rounds,
    parameters,
    *,
    target_league,
    weights,
    fold_validation_rounds=(),
):
    """探索制御を高速に実行しつつ、実評価関数で全指標を算出する。"""

    rows = []
    home_bias = max(-0.12, min(0.12, (parameters.model.home_correction - 1.08)))
    round_shift = sum(getattr(item, "round_id", 0) for item in rounds) % 3
    actuals = ("1", "0", "2", "1", "0", "2")
    for index, actual in enumerate(actuals):
        home = 0.42 + home_bias
        draw = 0.30 - home_bias / 3.0
        away = 1.0 - home - draw
        probabilities = {"1": home, "0": draw, "2": away}
        shifted_actual = ("1", "0", "2")[(index + round_shift) % 3]
        rows.append(
            PredictionRow(
                round_id=getattr(rounds[0], "round_id", 0),
                match_number=index + 1,
                cutoff_at=getattr(
                    rounds[0],
                    "cutoff_at",
                    datetime(2024, 1, 1, tzinfo=JAPAN_TIMEZONE),
                ),
                season=getattr(rounds[0], "season", "2024"),
                league="J1",
                prediction=max(probabilities, key=probabilities.get),
                probabilities=probabilities,
                actual_result=shifted_actual if round_shift else actual,
                draw_candidate=probabilities["0"] >= 0.25,
            )
        )
    folds = (tuple(rows[:3]), tuple(rows[3:])) if fold_validation_rounds else ()
    return evaluate_candidate_rows(rows, weights=weights, fold_rows=folds)


def _metric_rows(*, good: bool) -> tuple[PredictionRow, ...]:
    rows = []
    actuals = ("1", "0", "2", "0", "1", "2")
    for index, actual in enumerate(actuals):
        predicted = actual if good else "1"
        if predicted == "1":
            probabilities = {"1": 0.72, "0": 0.16, "2": 0.12}
        elif predicted == "0":
            probabilities = {"1": 0.15, "0": 0.70, "2": 0.15}
        else:
            probabilities = {"1": 0.12, "0": 0.16, "2": 0.72}
        rows.append(
            PredictionRow(
                round_id=1,
                match_number=index + 1,
                cutoff_at=datetime(2025, 5, 1, tzinfo=JAPAN_TIMEZONE),
                season="2025",
                league="J1" if index < 3 else "J2",
                prediction=predicted,
                probabilities=probabilities,
                actual_result=actual,
                draw_candidate=predicted == "0",
            )
        )
    return tuple(rows)


def _official_round(round_id: int, year: int) -> TotoRound:
    kickoff = datetime(year, 6, 21, 15, 0, tzinfo=JAPAN_TIMEZONE)
    outcomes = ("1", "0", "2")
    return TotoRound(
        round_id=round_id,
        matches=tuple(
            TotoMatch(
                round_id=round_id,
                match_number=number,
                home_team="鹿島アントラーズ",
                away_team="浦和レッズ",
                match_time=kickoff + timedelta(minutes=number),
                actual_result=outcomes[(number - 1) % 3],
            )
            for number in range(1, 14)
        ),
    )


def _history() -> tuple[OfficialMatch, ...]:
    matches = []
    for year in (2022, 2023, 2024, 2025, 2026):
        start = datetime(year, 1, 10, 14, 0, tzinfo=JAPAN_TIMEZONE)
        for index in range(10):
            matches.append(
                OfficialMatch(
                    match_time=start + timedelta(days=index * 14),
                    home_team=("鹿島アントラーズ" if index % 2 == 0 else "浦和レッズ"),
                    away_team=("浦和レッズ" if index % 2 == 0 else "鹿島アントラーズ"),
                    home_goals=index % 3,
                    away_goals=(index + 1) % 3,
                    category="J1",
                )
            )
    return tuple(matches)


class Version7BParameterAndEvaluationTest(unittest.TestCase):
    def test_every_model_parameter_is_mapped_to_runtime_settings(self) -> None:
        parameters = ModelParameters.from_mapping(
            {
                "home_correction": 1.15,
                "elo_correction_rate": 0.08,
                "home_advantage": 90,
                "k_factor": 28,
                "recent_match_weights": (6, 4, 3, 2, 1),
                "recent_weighted_share": 0.70,
                "season_average_share": 0.30,
                "venue_mix_rate": 0.80,
                "rank_correction_rate": 0.006,
                "points_correction_rate": 0.08,
                "goal_difference_correction_rate": 0.06,
                "expected_goals_minimum": 0.10,
                "expected_goals_maximum": 4.50,
            }
        )
        runtime = to_runtime_settings(parameters)
        self.assertEqual(runtime.model.home_correction, 1.15)
        self.assertEqual(runtime.model.expected_goals_minimum, 0.10)
        self.assertEqual(runtime.model.expected_goals_maximum, 4.50)
        self.assertEqual(runtime.elo.k_factor, 28)
        self.assertEqual(runtime.elo.home_advantage, 90)
        self.assertEqual(runtime.elo.expected_goals_change_per_100_elo, 0.08)
        self.assertEqual(runtime.form.recent_match_weights, (6, 4, 3, 2, 1))
        self.assertEqual(runtime.form.recent_weighted_share, 0.70)
        self.assertEqual(runtime.form.season_average_share, 0.30)
        self.assertAlmostEqual(runtime.venue.five_plus_share, 0.80)
        self.assertEqual(runtime.standings.rank_change_per_position, 0.006)
        self.assertEqual(runtime.standings.points_change_per_unit, 0.08)
        self.assertEqual(runtime.standings.goal_difference_change_per_unit, 0.06)

    def test_invalid_ranges_nan_and_infinity_are_rejected(self) -> None:
        invalid_values = (
            {"home_correction": math.nan},
            {"k_factor": math.inf},
            {"expected_goals_minimum": 4.0, "expected_goals_maximum": 3.0},
            {"venue_mix_rate": -0.1},
            {"recent_match_weights": (1, 2, 3, 4, 5)},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                ModelParameters.from_mapping(values)

    def test_composite_score_is_normalized_and_extreme_probabilities_are_safe(
        self,
    ) -> None:
        weights = EvaluationWeights(30, 20, 15, 15, 10, 10)
        self.assertTrue(weights.totals_one_hundred_percent)
        self.assertAlmostEqual(sum(weights.normalized().values()), 1.0)
        rows = list(_metric_rows(good=True))
        rows[0] = replace(
            rows[0],
            probabilities={"1": 1.0, "0": 0.0, "2": 0.0},
        )
        evaluation = evaluate_candidate_rows(rows, weights=weights)
        self.assertTrue(math.isfinite(evaluation.score))
        self.assertTrue(math.isfinite(evaluation.metrics.brier_score))
        self.assertTrue(math.isfinite(evaluation.metrics.log_loss))
        self.assertTrue(math.isfinite(evaluation.metrics.calibration_error))
        self.assertTrue(0 <= evaluation.score <= 100)

    def test_draw_protection_and_overfitting_use_validation_metrics(self) -> None:
        good = evaluate_candidate_rows(_metric_rows(good=True))
        poor = evaluate_candidate_rows(_metric_rows(good=False))
        degradation = check_draw_degradation(
            good,
            poor,
            {
                "draw_f1_drop": 0.01,
                "draw_recall_drop": 0.01,
                "draw_brier_increase": 0.01,
                "draw_calibration_increase": 0.01,
            },
        )
        self.assertTrue(degradation.degraded)
        self.assertGreater(degradation.penalty, 0)
        self.assertEqual(degradation.label, "引分性能悪化")
        overfit = check_overfitting(good, poor)
        self.assertTrue(overfit.is_overfitting)
        self.assertEqual(overfit.label, "過学習の可能性")

    def test_league_stability_uses_only_the_supplied_final_validation_rows(
        self,
    ) -> None:
        training_rows = _metric_rows(good=True)
        validation_rows = tuple(
            replace(row, league="J3", season="2026") for row in _metric_rows(good=False)
        )
        stability = build_stability_summary(
            (*training_rows, *validation_rows),
            league_rows=validation_rows,
        )
        self.assertEqual(set(stability.league_scores), {"J3"})
        self.assertEqual(set(stability.season_scores), {"2025", "2026"})

    def test_yes_only_adoption_backup_restore_and_corrupt_fallback(self) -> None:
        candidate = Version7BParameters.from_mapping({"home_correction": 1.15})
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path = root / "active.json"
            backups = root / "backups"
            declined = adopt_version7b_settings(
                candidate,
                confirmed=False,
                include_draw_parameters=False,
                path=path,
                backup_directory=backups,
            )
            self.assertFalse(declined.adopted)
            self.assertFalse(path.exists())
            adopted = adopt_version7b_settings(
                candidate,
                confirmed=True,
                include_draw_parameters=False,
                path=path,
                backup_directory=backups,
            )
            self.assertTrue(adopted.adopted)
            self.assertTrue(adopted.backup_path.exists())
            self.assertTrue(load_active_version7b_settings(path).adopted)
            restored = restore_latest_version7b_settings(
                path=path,
                backup_directory=backups,
            )
            self.assertTrue(restored.adopted)
            self.assertFalse(load_active_version7b_settings(path).adopted)
            path.write_text("{broken", encoding="utf-8")
            fallback = load_active_version7b_settings(path)
            self.assertFalse(fallback.adopted)
            self.assertIn("Version7-A", fallback.warning)


class Version7BWalkForwardAndPipelineTest(unittest.TestCase):
    def test_all_validation_methods_are_chronological(self) -> None:
        rounds = tuple(_stub(100 + index, 2021 + index) for index in range(5))
        for method in (FIXED_SPLIT, SEASON_WALK_FORWARD, ROUND_WALK_FORWARD):
            with self.subTest(method=method):
                split = create_validation_split(rounds, method)
                self.assertLess(
                    max(item.cutoff_at for item in split.training_rounds),
                    min(item.cutoff_at for item in split.final_validation_rounds),
                )
                for fold in split.folds:
                    self.assertLess(
                        max(item.cutoff_at for item in fold.training_rounds),
                        min(item.cutoff_at for item in fold.validation_rounds),
                    )

    def test_season_walk_forward_does_not_silently_fallback(self) -> None:
        with self.assertRaisesRegex(ValidationDataError, "3シーズン以上"):
            create_validation_split(
                (_stub(1, 2024), _stub(2, 2025)), SEASON_WALK_FORWARD
            )

    def test_real_pipeline_excludes_future_and_outputs_valid_probabilities(
        self,
    ) -> None:
        toto_round = _official_round(1600, 2025)
        future = OfficialMatch(
            match_time=datetime(2025, 7, 1, tzinfo=JAPAN_TIMEZONE),
            home_team="鹿島アントラーズ",
            away_team="浦和レッズ",
            home_goals=9,
            away_goals=0,
            category="J1",
        )
        with_future = prepare_model_round(toto_round, (*_history(), future))
        without_future = prepare_model_round(toto_round, _history())
        self.assertLess(with_future.latest_source_time, with_future.cutoff_at)
        first = predict_round_rows(
            with_future,
            Version7BParameters(),
            target_league=ALL_LEAGUES,
        )
        second = predict_round_rows(
            without_future,
            Version7BParameters(),
            target_league=ALL_LEAGUES,
        )
        self.assertEqual(len(first), 13)
        self.assertEqual(
            [row.probabilities for row in first],
            [row.probabilities for row in second],
        )
        for row in first:
            self.assertAlmostEqual(sum(row.probabilities.values()), 1.0, places=12)
            self.assertTrue(
                all(
                    math.isfinite(value) and value >= 0
                    for value in row.probabilities.values()
                )
            )

    def test_three_season_dataset_reports_actual_period_and_league_availability(
        self,
    ) -> None:
        dataset = prepare_model_dataset(
            (
                _official_round(1400, 2023),
                _official_round(1500, 2024),
                _official_round(1600, 2025),
            ),
            _history(),
            validation_method=SEASON_WALK_FORWARD,
            target_league="J1",
            requested_period="直近5シーズン",
        )
        self.assertEqual(dataset.training_match_count, 26)
        self.assertEqual(dataset.validation_match_count, 13)
        self.assertEqual(dataset.available_leagues, ("J1",))
        self.assertEqual(dataset.unavailable_leagues, ("J2", "J3"))
        self.assertIn("2023-06-21", dataset.actual_period)
        self.assertIn("2025-06-21", dataset.actual_period)

        with self.assertRaisesRegex(ModelOptimizationError, "Trainingが0試合"):
            prepare_model_dataset(
                (
                    _official_round(1400, 2023),
                    _official_round(1500, 2024),
                    _official_round(1600, 2025),
                ),
                _history(),
                validation_method=SEASON_WALK_FORWARD,
                target_league="J2",
            )


class Version7BSearchAndReportingTest(unittest.TestCase):
    def _run(self, method: str, trial_count: int, root: Path, *, seed: int = 1234):
        configuration = SearchConfiguration(
            method=method,
            trial_count=trial_count,
            model_limit=10000,
            random_seed=seed,
            truncate_grid_to_limit=(method == GRID_SEARCH),
        )
        with patch(
            "model_optimizer.evaluate_parameter_set",
            side_effect=_fake_evaluate_parameter_set,
        ):
            return run_model_optimization(
                _lightweight_dataset(),
                configuration,
                partial_path=root / f"{method}-{trial_count}-{seed}.csv",
            )

    def test_optuna_10_and_30_trials_complete_and_seed_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ten = self._run(OPTUNA_SEARCH, 10, root, seed=777)
            first = self._run(OPTUNA_SEARCH, 30, root, seed=888)
            second = self._run(OPTUNA_SEARCH, 30, root, seed=888)
        self.assertEqual(len(ten.all_trials), 10)
        self.assertEqual(len(first.all_trials), 30)
        self.assertEqual(
            [record.parameters for record in first.all_trials],
            [record.parameters for record in second.all_trials],
        )
        self.assertEqual(first.best_parameters, second.best_parameters)

    def test_optuna_1_and_10_trials_complete_through_real_prediction_pipeline(
        self,
    ) -> None:
        dataset = prepare_model_dataset(
            (
                _official_round(1400, 2023),
                _official_round(1500, 2024),
                _official_round(1600, 2025),
            ),
            _history(),
            validation_method=SEASON_WALK_FORWARD,
            target_league=ALL_LEAGUES,
            requested_period="実予測パイプライン統合テスト",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            one = run_model_optimization(
                dataset,
                SearchConfiguration(
                    method=OPTUNA_SEARCH,
                    trial_count=1,
                    random_seed=7001,
                ),
                partial_path=root / "actual-optuna-1.csv",
            )
            ten = run_model_optimization(
                dataset,
                SearchConfiguration(
                    method=OPTUNA_SEARCH,
                    trial_count=10,
                    random_seed=7010,
                ),
                partial_path=root / "actual-optuna-10.csv",
            )
            ranking_path = root / "actual-ranking.csv"
            history_path = root / "actual-history.csv"
            self.assertTrue(save_model_ranking(ten, path=ranking_path))
            self.assertTrue(save_optimization_history(ten, path=history_path))
            ranking_lines = ranking_path.read_text(encoding="utf-8-sig").splitlines()
            history_lines = history_path.read_text(encoding="utf-8-sig").splitlines()
            partial_lines = (root / "actual-optuna-10.csv").read_text(
                encoding="utf-8-sig"
            ).splitlines()

        self.assertEqual(len(one.all_trials), 1)
        self.assertEqual(len(one.ranking), 1)
        self.assertEqual(len(ten.all_trials), 10)
        self.assertEqual(len(ten.ranking), 10)
        self.assertEqual(len(ranking_lines), 11)
        self.assertEqual(len(history_lines), 2)
        self.assertEqual(len(partial_lines), 11)
        self.assertTrue(
            all(record.final_validation is not None for record in ten.ranking)
        )
        self.assertGreater(
            len({record.parameters for record in ten.all_trials}),
            1,
        )
        self.assertGreater(
            len(
                {
                    tuple(record.selection_validation.rows[0].probabilities.values())
                    for record in ten.all_trials
                }
            ),
            1,
        )
        final = ten.best_final_validation
        self.assertTrue(math.isfinite(ten.best_score))
        self.assertTrue(math.isfinite(final.metrics.brier_score))
        self.assertTrue(math.isfinite(final.metrics.log_loss))
        self.assertTrue(math.isfinite(final.draw.f1_score))

    def test_random_grid_and_two_stage_search_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            random_result = self._run(RANDOM_SEARCH, 10, root)
            grid_result = self._run(GRID_SEARCH, 10, root)
            two_stage_result = self._run(TWO_STAGE_SEARCH, 10, root)
        self.assertEqual(len(random_result.all_trials), 10)
        self.assertEqual(len(grid_result.all_trials), 10)
        self.assertEqual(len(two_stage_result.all_trials), 10)
        self.assertIn(
            "stage2_grid", {item.search_stage for item in two_stage_result.all_trials}
        )

    def test_draw_parameters_can_be_included_without_changing_the_active_baseline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = SearchConfiguration(
                method=RANDOM_SEARCH,
                trial_count=10,
                include_draw_parameters=True,
                random_seed=4321,
            )
            with patch(
                "model_optimizer.evaluate_parameter_set",
                side_effect=_fake_evaluate_parameter_set,
            ):
                result = run_model_optimization(
                    _lightweight_dataset(),
                    configuration,
                    partial_path=root / "draw.csv",
                )
        baseline = next(
            record for record in result.all_trials if record.search_stage == "current"
        )
        self.assertEqual(baseline.parameters.draw.base_draw_logit_bias, 0.0)
        self.assertTrue(
            any(
                record.parameters.draw != result.current_settings.parameters.draw
                for record in result.all_trials
                if record.search_stage != "current"
            )
        )

    def test_missing_optuna_has_an_actionable_error(self) -> None:
        original_import = builtins.__import__

        def import_without_optuna(name, *args, **kwargs):
            if name == "optuna":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "model_optimizer.evaluate_parameter_set",
            side_effect=_fake_evaluate_parameter_set,
        ), patch("builtins.__import__", side_effect=import_without_optuna):
            with self.assertRaisesRegex(ModelOptimizationError, "未インストール"):
                run_model_optimization(
                    _lightweight_dataset(),
                    SearchConfiguration(method=OPTUNA_SEARCH, trial_count=2),
                    partial_path=Path(temporary_directory) / "missing.csv",
                )

    def test_final_validation_never_changes_search_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = SearchConfiguration(
                method=RANDOM_SEARCH,
                trial_count=10,
                random_seed=2468,
            )
            with patch(
                "model_optimizer.evaluate_parameter_set",
                side_effect=_fake_evaluate_parameter_set,
            ):
                first = run_model_optimization(
                    _lightweight_dataset(final_round_id=300),
                    configuration,
                    partial_path=root / "first.csv",
                )
                changed_final = run_model_optimization(
                    _lightweight_dataset(final_round_id=301),
                    configuration,
                    partial_path=root / "changed.csv",
                )
        self.assertEqual(first.best_parameters, changed_final.best_parameters)
        self.assertEqual(first.best_score, changed_final.best_score)
        self.assertNotEqual(
            [row.actual_result for row in first.best_final_validation.rows],
            [row.actual_result for row in changed_final.best_final_validation.rows],
        )

    def test_version7a_baseline_stays_fixed_after_a_version7b_adoption(self) -> None:
        adopted_parameters = Version7BParameters.from_mapping({"home_correction": 1.18})
        adopted = ActiveVersion7BSettings(
            parameters=adopted_parameters,
            adopted=True,
            draw_override=False,
        )
        observed = []

        def evaluate_and_capture(rounds, parameters, **kwargs):
            observed.append(parameters.model.home_correction)
            return _fake_evaluate_parameter_set(rounds, parameters, **kwargs)

        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "model_optimizer.evaluate_parameter_set",
            side_effect=evaluate_and_capture,
        ):
            result = run_model_optimization(
                _lightweight_dataset(),
                SearchConfiguration(method=RANDOM_SEARCH, trial_count=1),
                current_settings=adopted,
                partial_path=Path(temporary_directory) / "adopted.csv",
            )
        self.assertEqual(observed[:3], [1.08, 1.08, 1.08])
        self.assertEqual(result.current_settings.parameters, adopted_parameters)
        self.assertEqual(result.best_parameters, adopted_parameters)

    def test_limits_grid_count_and_large_trial_options_are_validated(self) -> None:
        self.assertEqual(grid_combination_count(False), 4096)
        self.assertEqual(grid_combination_count(True), 4194304)
        blocked = build_search_plan(
            SearchConfiguration(
                method=GRID_SEARCH,
                trial_count=100,
                model_limit=1000,
            )
        )
        self.assertFalse(blocked.executable)
        self.assertIn("4,096", blocked.reason)
        self.assertIn("1,000", blocked.reason)
        for trials in (100, 500, 1000, 3000, 5000, 10000):
            self.assertEqual(
                SearchConfiguration(trial_count=trials).trial_count, trials
            )
        with self.assertRaises(ValueError):
            SearchConfiguration(trial_count=0)
        with self.assertRaises(ValueError):
            SearchConfiguration(model_limit=50001)

    def test_history_ranking_frames_bootstrap_and_adoption_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result = self._run(RANDOM_SEARCH, 10, root)
            history_path = root / "history.csv"
            ranking_path = root / "ranking.csv"
            self.assertTrue(save_optimization_history(result, path=history_path))
            self.assertTrue(save_model_ranking(result, path=ranking_path))
            self.assertTrue(mark_optimization_adopted(result.run_id, path=history_path))
            self.assertIn(",True", history_path.read_text(encoding="utf-8-sig"))
            self.assertLessEqual(
                len(ranking_path.read_text(encoding="utf-8-sig").splitlines()), 21
            )

            bootstrap_first = bootstrap_evaluate_rows(
                result.best_final_validation.rows,
                100,
                random_seed=555,
            )
            bootstrap_second = bootstrap_evaluate_rows(
                result.best_final_validation.rows,
                100,
                random_seed=555,
            )
            self.assertEqual(bootstrap_first, bootstrap_second)
            for distribution in bootstrap_first.metrics.values():
                self.assertLessEqual(distribution.confidence_lower, distribution.mean)
                self.assertLessEqual(distribution.mean, distribution.confidence_upper)

            self.assertEqual(len(training_validation_frame(result)), 4)
            self.assertGreaterEqual(len(version7a_comparison_frame(result)), 10)
            self.assertEqual(len(ranking_frame(result)), 10)
            self.assertEqual(len(trial_metrics_frame(result)), 10)
            self.assertGreater(len(parameter_comparison_frame(result)), 10)
            self.assertEqual(len(bootstrap_frame({1: bootstrap_first})), 5)

            history_path.write_text("broken,columns\n", encoding="utf-8")
            with self.assertRaisesRegex(ModelOptimizationError, "列が壊れています"):
                save_optimization_history(result, path=history_path)

    def test_partial_csv_corruption_and_stop_are_not_silently_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            broken = root / "broken.csv"
            broken.write_text("bad,header\n", encoding="utf-8")
            configuration = SearchConfiguration(method=RANDOM_SEARCH, trial_count=3)
            with patch(
                "model_optimizer.evaluate_parameter_set",
                side_effect=_fake_evaluate_parameter_set,
            ):
                with self.assertRaisesRegex(ModelOptimizationError, "列が壊れています"):
                    run_model_optimization(
                        _lightweight_dataset(),
                        configuration,
                        partial_path=broken,
                    )
                with self.assertRaises(KeyboardInterrupt):
                    run_model_optimization(
                        _lightweight_dataset(),
                        configuration,
                        partial_path=root / "stopped.csv",
                        should_stop=lambda: True,
                    )

    def test_backup_write_failure_returns_a_visible_adoption_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            invalid_path = root / "directory_instead_of_file"
            invalid_path.mkdir()
            adoption = adopt_version7b_settings(
                Version7BParameters(),
                confirmed=True,
                include_draw_parameters=False,
                path=invalid_path,
                backup_directory=root / "backups",
            )
        self.assertFalse(adoption.adopted)
        self.assertIn("採用できませんでした", adoption.message)


if __name__ == "__main__":
    unittest.main()
