"""Version7-Aの時系列分割、Optuna小規模探索、保存・採用を確認する。"""

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from data_loader import JAPAN_TIMEZONE, OfficialMatch, VenueRecord
from draw_optimizer import (
    DrawOptimizationDataset,
    DrawOptimizationError,
    OptunaUnavailableError,
    PreparedDrawRound,
    PreparedDrawRow,
    adopt_draw_settings,
    load_active_draw_settings,
    prepare_draw_dataset,
    restore_latest_draw_settings,
    run_draw_optimization,
    save_optimization_result,
)
from draw_predictor import DEFAULT_DRAW_SETTINGS, DrawContext
from history_manager import TotoMatch, TotoRound
from model_pipeline import TeamModelInput


def _team(index: int, *, home: bool) -> TeamModelInput:
    played = 10 + index % 4
    draws = 2 + index % 3
    return TeamModelInput(
        team_name="鹿島アントラーズ" if home else "浦和レッズ",
        recent_scored_average=0.9 + (index % 4) * 0.2,
        recent_conceded_average=0.8 + (index % 3) * 0.2,
        season_scored_average=1.2,
        season_conceded_average=1.1,
        venue_record=VenueRecord(played, 4, draws, played - 4 - draws, 12, 11),
        rank=3 + index % 8,
        points=16 + index,
        played=played,
        season_draws=draws,
        goal_difference=2 - index % 5,
        elo=1480 + (15 if home else -10) + index,
    )


def _prepared_round(round_id: int, year: int, actual_shift: int = 0) -> PreparedDrawRound:
    cutoff = datetime(year, 6, 21, tzinfo=JAPAN_TIMEZONE)
    context = DrawContext(
        historical_match_count=120,
        season_match_count=80,
        category="J1",
        league_draw_rate=0.27,
        season_draw_rate=0.29,
        zero_zero_rate=0.07,
        one_one_rate=0.12,
        low_score_rate=0.48,
        league_goals_per_team=1.30,
        season_goals_per_team=1.25,
    )
    rows = []
    outcomes = ("1", "0", "2")
    for index in range(18):
        home_expected = 0.9 + (index % 5) * 0.18
        away_expected = 0.8 + ((index + 2) % 5) * 0.16
        base_draw = 0.22 + (index % 4) * 0.025
        home_win = 0.48 - (index % 3) * 0.04
        away_win = 1.0 - base_draw - home_win
        base = {"1": home_win, "0": base_draw, "2": away_win}
        rows.append(
            PreparedDrawRow(
                round_id=round_id,
                match_number=index + 1,
                cutoff_at=cutoff,
                actual_result=outcomes[(index + actual_shift) % 3],
                base_prediction=max(base, key=base.get),
                base_probabilities=base,
                home_expected_goals=home_expected,
                away_expected_goals=away_expected,
                home_input=_team(index, home=True),
                away_input=_team(index, home=False),
                context=context,
                latest_source_time=cutoff - timedelta(days=1),
            )
        )
    return PreparedDrawRound(
        toto_round=TotoRound(round_id=round_id, matches=()),
        cutoff_at=cutoff,
        historical_match_count=120,
        rows=tuple(rows),
    )


def _dataset(validation_shift: int = 0) -> DrawOptimizationDataset:
    return DrawOptimizationDataset(
        training_rounds=(_prepared_round(1500, 2025),),
        validation_rounds=(_prepared_round(1600, 2026, validation_shift),),
    )


def _official_round(round_id: int, kickoff: datetime) -> TotoRound:
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


def _history() -> list[OfficialMatch]:
    matches = []
    for year in (2024, 2025):
        base = datetime(year, 1, 10, 14, 0, tzinfo=JAPAN_TIMEZONE)
        for index in range(10):
            matches.append(
                OfficialMatch(
                    match_time=base + timedelta(days=index * 15),
                    home_team=(
                        "鹿島アントラーズ" if index % 2 == 0 else "浦和レッズ"
                    ),
                    away_team=(
                        "浦和レッズ" if index % 2 == 0 else "鹿島アントラーズ"
                    ),
                    home_goals=index % 3,
                    away_goals=(index + 1) % 3,
                    category="J1",
                )
            )
    matches.append(
        OfficialMatch(
            match_time=datetime(2026, 6, 22, 14, 0, tzinfo=JAPAN_TIMEZONE),
            home_team="鹿島アントラーズ",
            away_team="浦和レッズ",
            home_goals=9,
            away_goals=0,
            category="J1",
        )
    )
    return matches


class DrawOptimizerTest(unittest.TestCase):
    def test_optuna_10_trials_complete_and_validation_does_not_select_model(self) -> None:
        first = run_draw_optimization(_dataset(0), 10, random_seed=1234)
        changed_validation = run_draw_optimization(_dataset(1), 10, random_seed=1234)

        self.assertEqual(len(first.trials), 10)
        self.assertEqual(first.best_settings, changed_validation.best_settings)
        self.assertEqual(first.training_score.score, changed_validation.training_score.score)
        self.assertNotEqual(
            first.validation_best.actual_results,
            changed_validation.validation_best.actual_results,
        )

    def test_optuna_30_trials_complete_and_result_csv_is_safe(self) -> None:
        result = run_draw_optimization(_dataset(), 30, random_seed=5678)
        self.assertEqual(len(result.trials), 30)
        self.assertIn(result.best_trial, range(30))
        self.assertTrue(0.0 <= result.best_score <= 100.0)

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "version7a.csv"
            self.assertTrue(save_optimization_result(result, path))
            self.assertTrue(save_optimization_result(result, path))
            self.assertEqual(len(path.read_text(encoding="utf-8-sig").splitlines()), 3)
            path.write_text("broken,columns\n", encoding="utf-8")
            before = path.read_bytes()
            self.assertFalse(save_optimization_result(result, path))
            self.assertEqual(path.read_bytes(), before)

    def test_training_validation_are_chronological_and_future_match_is_excluded(self) -> None:
        training_round = _official_round(
            1548,
            datetime(2025, 6, 21, 15, 0, tzinfo=JAPAN_TIMEZONE),
        )
        validation_round = _official_round(
            1600,
            datetime(2026, 6, 21, 15, 0, tzinfo=JAPAN_TIMEZONE),
        )
        with_future = prepare_draw_dataset(
            [training_round, validation_round],
            _history(),
            training_years=[2025],
            validation_years=[2026],
        )
        without_future = prepare_draw_dataset(
            [training_round, validation_round],
            _history()[:-1],
            training_years=[2025],
            validation_years=[2026],
        )

        self.assertEqual(len(with_future.training_rows), 13)
        self.assertEqual(len(with_future.validation_rows), 13)
        self.assertTrue(
            all(row.latest_source_time < row.cutoff_at for row in with_future.training_rows)
        )
        self.assertTrue(
            all(row.latest_source_time < row.cutoff_at for row in with_future.validation_rows)
        )
        self.assertEqual(
            [row.base_probabilities for row in with_future.validation_rows],
            [row.base_probabilities for row in without_future.validation_rows],
        )
        with self.assertRaisesRegex(DrawOptimizationError, "Validationより後"):
            prepare_draw_dataset(
                [training_round, validation_round],
                _history(),
                training_years=[2026],
                validation_years=[2025],
            )

    def test_only_yes_adopts_with_backup_and_latest_can_be_restored(self) -> None:
        first_settings = replace(DEFAULT_DRAW_SETTINGS, base_draw_logit_bias=0.20)
        second_settings = replace(DEFAULT_DRAW_SETTINGS, base_draw_logit_bias=0.45)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path = root / "active.json"
            backups = root / "backups"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_active_draw_settings(path), DEFAULT_DRAW_SETTINGS)
            path.unlink()

            declined = adopt_draw_settings(
                first_settings,
                confirmed=False,
                path=path,
                backup_directory=backups,
            )
            self.assertFalse(declined.adopted)
            self.assertFalse(path.exists())

            first = adopt_draw_settings(
                first_settings,
                confirmed=True,
                path=path,
                backup_directory=backups,
            )
            second = adopt_draw_settings(
                second_settings,
                confirmed=True,
                path=path,
                backup_directory=backups,
            )
            self.assertTrue(first.adopted and second.adopted)
            self.assertGreaterEqual(len(tuple(backups.glob("*.json"))), 2)
            self.assertEqual(load_active_draw_settings(path), second_settings)

            restored = restore_latest_draw_settings(
                path=path,
                backup_directory=backups,
            )
            self.assertTrue(restored.adopted)
            self.assertEqual(load_active_draw_settings(path), first_settings)

    def test_zero_sized_dataset_and_missing_optuna_have_clear_errors(self) -> None:
        empty = DrawOptimizationDataset((), ())
        with self.assertRaisesRegex(DrawOptimizationError, "0試合"):
            run_draw_optimization(empty, 10)

        original_import = __import__

        def import_without_optuna(name, *args, **kwargs):
            if name == "optuna":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_optuna):
            with self.assertRaisesRegex(OptunaUnavailableError, "未インストール"):
                run_draw_optimization(_dataset(), 10)


if __name__ == "__main__":
    unittest.main()
