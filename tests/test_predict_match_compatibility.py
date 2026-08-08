"""Version7-Bが実行時に使うpredict_matchの実体と設定互換性を確認する。"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from model_config import (
    DEFAULT_ELO_SETTINGS,
    DEFAULT_FORM_SETTINGS,
    DEFAULT_MODEL_SETTINGS,
    DEFAULT_STANDINGS_SETTINGS,
    DEFAULT_VENUE_SETTINGS,
)
from model_pipeline import ModelOptions, TeamModelInput
from version7b_pipeline import (
    PREDICT_MATCH_PARAMETERS,
    get_version7b_predict_match,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTING_ARGUMENTS = {
    "elo_settings": DEFAULT_ELO_SETTINGS,
    "form_settings": DEFAULT_FORM_SETTINGS,
    "venue_settings": DEFAULT_VENUE_SETTINGS,
    "standings_settings": DEFAULT_STANDINGS_SETTINGS,
    "model_settings": DEFAULT_MODEL_SETTINGS,
}


def _inputs() -> tuple[TeamModelInput, TeamModelInput]:
    return (
        TeamModelInput(
            team_name="ホーム",
            recent_scored_average=1.7,
            recent_conceded_average=0.9,
            season_scored_average=1.5,
            season_conceded_average=1.0,
            rank=2,
            points=30,
            played=15,
            goal_difference=12,
            elo=1600,
        ),
        TeamModelInput(
            team_name="アウェイ",
            recent_scored_average=1.0,
            recent_conceded_average=1.6,
            season_scored_average=1.1,
            season_conceded_average=1.4,
            rank=12,
            points=16,
            played=15,
            goal_difference=-8,
            elo=1400,
        ),
    )


class PredictMatchCompatibilityTest(unittest.TestCase):
    def test_repository_has_one_predict_match_definition(self) -> None:
        definitions = []
        for path in sorted(PROJECT_ROOT.rglob("*.py")):
            if any(part in {".git", "__pycache__"} for part in path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            definitions.extend(
                (path.name, node.lineno)
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "predict_match"
            )
        self.assertEqual(
            [filename for filename, _ in definitions], ["model_pipeline.py"]
        )

    def test_predict_match_imports_do_not_use_an_alias_or_unrelated_module(
        self,
    ) -> None:
        invalid_imports = []
        for path in sorted(PROJECT_ROOT.rglob("*.py")):
            if any(part in {".git", "__pycache__"} for part in path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                for imported_name in node.names:
                    if imported_name.name != "predict_match":
                        continue
                    if node.module != "model_pipeline" or imported_name.asname:
                        invalid_imports.append(
                            (
                                str(path.relative_to(PROJECT_ROOT)),
                                node.lineno,
                                node.module,
                                imported_name.asname,
                            )
                        )
        self.assertEqual(invalid_imports, [])

    def test_version7b_resolves_current_model_pipeline_function(self) -> None:
        import model_pipeline

        predict_function = get_version7b_predict_match()
        signature = inspect.signature(predict_function)
        self.assertIs(predict_function, model_pipeline.predict_match)
        self.assertEqual(predict_function.__module__, "model_pipeline")
        self.assertEqual(
            Path(inspect.getsourcefile(predict_function) or "").resolve(),
            (PROJECT_ROOT / "model_pipeline.py").resolve(),
        )
        self.assertEqual(tuple(signature.parameters), PREDICT_MATCH_PARAMETERS)

    def test_each_version7b_settings_keyword_is_accepted(self) -> None:
        predict_function = get_version7b_predict_match()
        home, away = _inputs()
        for argument_name, settings in SETTING_ARGUMENTS.items():
            with self.subTest(argument_name=argument_name):
                result = predict_function(
                    home,
                    away,
                    options=ModelOptions(),
                    **{argument_name: settings},
                )
                self.assertAlmostEqual(
                    sum(
                        result.version5_probabilities[key]
                        for key in ("home_win", "draw", "away_win")
                    ),
                    1.0,
                    places=12,
                )

    def test_version1_to_version7a_call_without_new_settings_is_compatible(
        self,
    ) -> None:
        home, away = _inputs()
        result = get_version7b_predict_match()(home, away, options=ModelOptions())
        self.assertIn(result.version5_prediction, ("1", "0", "2"))

    def test_cached_legacy_predict_match_is_reloaded_and_rebound(self) -> None:
        script = textwrap.dedent("""
            import importlib
            import inspect
            import sys

            import model_optimizer
            import model_pipeline
            import version7b_pipeline

            current_predict_match = model_pipeline.predict_match

            def legacy_predict_match(
                home,
                away,
                options=model_pipeline.ModelOptions(),
                form_settings=None,
                venue_settings=None,
                standings_settings=None,
                model_settings=None,
            ):
                return current_predict_match(home, away, options=options)

            legacy_predict_match.__module__ = "model_pipeline"
            model_pipeline.predict_match = legacy_predict_match
            model_optimizer.predict_match = legacy_predict_match

            del sys.modules["version7b_pipeline"]
            compatibility = importlib.import_module("version7b_pipeline")
            resolved = compatibility.get_version7b_predict_match()

            assert resolved is model_pipeline.predict_match
            assert model_optimizer.predict_match is resolved
            assert "elo_settings" in inspect.signature(resolved).parameters
            result = resolved(
                model_pipeline.TeamModelInput(elo=1550),
                model_pipeline.TeamModelInput(elo=1450),
                elo_settings=__import__("model_config").DEFAULT_ELO_SETTINGS,
            )
            outcome_total = sum(
                result.version5_probabilities[key]
                for key in ("home_win", "draw", "away_win")
            )
            assert abs(outcome_total - 1.0) < 1e-12
            """)
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


if __name__ == "__main__":
    unittest.main()
