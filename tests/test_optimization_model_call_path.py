"""Version7-Bの全モデル関数契約と探索値の実反映を確認する。"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path

import model_config
import model_pipeline
import prediction
import backtest
from version7b_runtime import (
    MODEL_CALL_FUNCTION_CONTRACTS,
    PREDICT_MATCH_PARAMETERS,
    ensure_version7b_model_call_path,
    runtime_function_identities,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _inputs() -> tuple[model_pipeline.TeamModelInput, model_pipeline.TeamModelInput]:
    home_recent = (
        {"scored": 4, "conceded": 0},
        {"scored": 0, "conceded": 2},
        {"scored": 3, "conceded": 1},
        {"scored": 1, "conceded": 2},
        {"scored": 2, "conceded": 0},
    )
    away_recent = (
        {"scored": 0, "conceded": 3},
        {"scored": 3, "conceded": 0},
        {"scored": 1, "conceded": 2},
        {"scored": 2, "conceded": 1},
        {"scored": 0, "conceded": 2},
    )
    return (
        model_pipeline.TeamModelInput(
            team_name="ホーム",
            recent_scored_average=2.0,
            recent_conceded_average=1.0,
            recent_matches=home_recent,
            season_scored_average=1.4,
            season_conceded_average=1.1,
            venue_record={"played": 5, "goals_for": 14, "goals_against": 3},
            rank=2,
            points=31,
            played=15,
            goal_difference=15,
            elo=1650,
        ),
        model_pipeline.TeamModelInput(
            team_name="アウェイ",
            recent_scored_average=1.1,
            recent_conceded_average=1.8,
            recent_matches=away_recent,
            season_scored_average=1.2,
            season_conceded_average=1.5,
            venue_record={"played": 5, "goals_for": 4, "goals_against": 12},
            rank=14,
            points=14,
            played=15,
            goal_difference=-13,
            elo=1380,
        ),
    )


def _snapshot(result: model_pipeline.ModelPipelineResult) -> tuple[float, ...]:
    return (
        result.expected_final.home,
        result.expected_final.away,
        result.version5_probabilities["home_win"],
        result.version5_probabilities["draw"],
        result.version5_probabilities["away_win"],
    )


class OptimizationModelCallPathTest(unittest.TestCase):
    def test_calculate_expected_goals_definition_imports_and_calls_are_audited(
        self,
    ) -> None:
        definitions = []
        invalid_imports = []
        star_imports = []
        calls = []
        for path in sorted(PROJECT_ROOT.rglob("*.py")):
            if any(part in {".git", "__pycache__"} for part in path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = str(path.relative_to(PROJECT_ROOT))
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "calculate_expected_goals"
                ):
                    definitions.append((relative, node.lineno))
                if isinstance(node, ast.ImportFrom):
                    for imported in node.names:
                        if imported.name == "*":
                            star_imports.append((relative, node.module))
                        if imported.name != "calculate_expected_goals":
                            continue
                        if node.module != "prediction" or imported.asname:
                            invalid_imports.append(
                                (relative, node.module, imported.asname)
                            )
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "calculate_expected_goals"
                ):
                    calls.append((relative, node.lineno))
        self.assertEqual([filename for filename, _ in definitions], ["prediction.py"])
        self.assertEqual(invalid_imports, [])
        self.assertIn("model_pipeline.py", [filename for filename, _ in calls])
        self.assertEqual(star_imports, [("config.py", "model_config")])

    def test_runtime_functions_match_the_declared_modules_and_signatures(self) -> None:
        runtime = ensure_version7b_model_call_path()
        modules = {
            name: getattr(runtime, name)
            for name in (
                "prediction",
                "elo_rating",
                "form_adjuster",
                "venue_adjuster",
                "standings_adjuster",
                "draw_predictor",
                "model_pipeline",
            )
        }
        for contract in MODEL_CALL_FUNCTION_CONTRACTS:
            with self.subTest(
                module=contract.module_name,
                function=contract.function_name,
            ):
                function = getattr(
                    modules[contract.module_name],
                    contract.function_name,
                )
                self.assertEqual(function.__module__, contract.module_name)
                self.assertEqual(function.__name__, contract.function_name)
                self.assertEqual(
                    tuple(inspect.signature(function).parameters),
                    contract.parameters,
                )
                self.assertEqual(
                    Path(inspect.getsourcefile(function) or "").resolve(),
                    (PROJECT_ROOT / f"{contract.module_name}.py").resolve(),
                )
        self.assertEqual(
            len(runtime_function_identities()),
            len(MODEL_CALL_FUNCTION_CONTRACTS),
        )
        self.assertIs(
            runtime.model_pipeline.calculate_expected_goals,
            runtime.prediction.calculate_expected_goals,
        )

    def test_legacy_prediction_cache_is_reloaded_before_a_trial(self) -> None:
        script = textwrap.dedent(
            """
            import inspect
            import model_pipeline
            import prediction

            def calculate_expected_goals(
                home_scored,
                home_conceded,
                away_scored,
                away_conceded,
            ):
                return 1.0, 1.0

            calculate_expected_goals.__module__ = "prediction"
            prediction.calculate_expected_goals = calculate_expected_goals
            model_pipeline.calculate_expected_goals = calculate_expected_goals

            from version7b_runtime import ensure_version7b_model_call_path
            runtime = ensure_version7b_model_call_path()
            current = runtime.prediction.calculate_expected_goals
            assert tuple(inspect.signature(current).parameters) == (
                "home_scored",
                "home_conceded",
                "away_scored",
                "away_conceded",
                "home_correction",
                "expected_goals_minimum",
                "expected_goals_maximum",
            )
            assert runtime.model_pipeline.calculate_expected_goals is current
            result = runtime.model_pipeline.predict_match(
                runtime.model_pipeline.TeamModelInput(elo=1550),
                runtime.model_pipeline.TeamModelInput(elo=1450),
                model_settings=__import__("model_config").ModelSettings(
                    home_correction=1.20,
                ),
            )
            assert result.expected_final.home > 0
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr or completed.stdout,
        )

    def test_home_correction_changes_direct_expected_goals(self) -> None:
        common = {
            "home_scored": 1.5,
            "home_conceded": 1.0,
            "away_scored": 1.1,
            "away_conceded": 1.3,
            "expected_goals_minimum": 0.10,
            "expected_goals_maximum": 4.50,
        }
        at_one = prediction.calculate_expected_goals(
            **common,
            home_correction=1.00,
        )
        at_one_twenty = prediction.calculate_expected_goals(
            **common,
            home_correction=1.20,
        )
        self.assertAlmostEqual(at_one[0], 1.40, places=12)
        self.assertAlmostEqual(at_one_twenty[0], 1.68, places=12)
        self.assertNotEqual(at_one[0], at_one_twenty[0])
        self.assertEqual(at_one[1], at_one_twenty[1])

    def test_each_settings_class_and_combination_change_real_model_output(self) -> None:
        runtime = ensure_version7b_model_call_path()
        predict = runtime.model_pipeline.predict_match
        home, away = _inputs()
        options = runtime.model_pipeline.ModelOptions(True, True, True, True)
        baseline = predict(home, away, options=options)
        variants = {
            "elo_settings": replace(
                model_config.DEFAULT_ELO_SETTINGS,
                expected_goals_change_per_100_elo=0.10,
                expected_goals_max_adjustment=0.30,
            ),
            "form_settings": model_config.FormSettings(
                recent_match_weights=(12.0, 1.0, 1.0, 1.0, 1.0),
                recent_weighted_share=0.90,
                season_average_share=0.10,
            ),
            "venue_settings": model_config.VenueSettings(0.20, 0.20, 0.20),
            "standings_settings": model_config.StandingsSettings(
                points_change_per_unit=0.10,
                points_max_adjustment=0.10,
                goal_difference_change_per_unit=0.08,
                goal_difference_max_adjustment=0.08,
                total_max_adjustment=0.20,
                rank_change_per_position=0.01,
                rank_max_adjustment=0.10,
            ),
            "model_settings": model_config.ModelSettings(
                expected_goals_minimum=0.10,
                expected_goals_maximum=4.50,
                home_correction=1.20,
            ),
        }
        for keyword, settings in variants.items():
            with self.subTest(keyword=keyword):
                changed = predict(
                    home,
                    away,
                    options=options,
                    **{keyword: settings},
                )
                self.assertNotEqual(_snapshot(changed), _snapshot(baseline))
        combined = predict(home, away, options=options, **variants)
        self.assertNotEqual(_snapshot(combined), _snapshot(baseline))
        self.assertAlmostEqual(
            sum(
                combined.version5_probabilities[key]
                for key in ("home_win", "draw", "away_win")
            ),
            1.0,
            places=12,
        )

    def test_predict_match_runtime_identity_is_current_model_pipeline(self) -> None:
        runtime = ensure_version7b_model_call_path()
        function = runtime.model_pipeline.predict_match
        self.assertEqual(function.__module__, "model_pipeline")
        self.assertEqual(function.__name__, "predict_match")
        self.assertEqual(
            tuple(inspect.signature(function).parameters),
            PREDICT_MATCH_PARAMETERS,
        )
        self.assertEqual(
            Path(inspect.getsourcefile(function) or "").resolve(),
            (PROJECT_ROOT / "model_pipeline.py").resolve(),
        )
        self.assertIs(backtest.predict_match, function)
        self.assertEqual(backtest.run_backtest.__module__, "backtest")
        self.assertEqual(
            tuple(inspect.signature(backtest.run_backtest).parameters),
            ("toto_round", "historical_matches", "generated_at"),
        )


if __name__ == "__main__":
    unittest.main()
