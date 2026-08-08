"""全ローカルimportとVersion7-Bの再実行互換性を検証する。"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import model_config
import version7b_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION7B_MODULES = (
    "version7b_config",
    "version7b_runtime",
    "version7b_pipeline",
    "walk_forward_validator",
    "model_evaluation",
    "bootstrap_evaluation",
    "parameter_manager",
    "model_optimizer",
    "model_compare",
    "model_optimization_ui",
)


def _module_trees() -> dict[str, ast.Module]:
    return {
        path.stem: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(PROJECT_ROOT.glob("*.py"))
        if path.name != "__init__.py"
    }


def _exported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname or alias.name for alias in node.names if alias.name != "*"
            )
    return names


def _local_import_graph(trees: dict[str, ast.Module]) -> dict[str, set[str]]:
    graph = {module: set() for module in trees}
    for source, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                target = node.module.split(".")[0]
                if target in trees:
                    graph[source].add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name.split(".")[0]
                    if target in trees:
                        graph[source].add(target)
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> tuple[str, ...]:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(module: str) -> tuple[str, ...]:
        if module in visiting:
            start = path.index(module)
            return tuple((*path[start:], module))
        if module in visited:
            return ()
        visiting.add(module)
        path.append(module)
        for target in sorted(graph[module]):
            cycle = visit(target)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(module)
        visited.add(module)
        return ()

    for module in sorted(graph):
        cycle = visit(module)
        if cycle:
            return cycle
    return ()


class ImportIntegrityTest(unittest.TestCase):
    def test_all_local_from_import_names_exist(self) -> None:
        trees = _module_trees()
        exports = {module: _exported_names(tree) for module, tree in trees.items()}
        missing = []
        for source, tree in trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level != 0 or not node.module:
                    continue
                target = node.module.split(".")[0]
                if target not in trees:
                    continue
                missing.extend(
                    f"{source}:{node.lineno} -> {target}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*" and alias.name not in exports[target]
                )
        self.assertEqual(missing, [])

    def test_local_import_graph_has_no_cycles(self) -> None:
        cycle = _find_cycle(_local_import_graph(_module_trees()))
        self.assertEqual(cycle, ())

    def test_version7b_config_exports_are_model_config_values(self) -> None:
        self.assertIs(version7b_config.ensure_version7b_model_config(), model_config)
        for name in version7b_config.VERSION7B_CONFIG_EXPORTS:
            self.assertTrue(hasattr(model_config, name), name)
            self.assertIs(getattr(version7b_config, name), getattr(model_config, name))

    def test_version7b_modules_import_independently(self) -> None:
        failures = []
        for module in VERSION7B_MODULES:
            completed = subprocess.run(
                [sys.executable, "-c", f"import {module}"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                failures.append(f"{module}: {completed.stderr or completed.stdout}")
        self.assertEqual(failures, [])

    def test_cached_version7a_model_config_is_reloaded_before_version7b_imports(
        self,
    ) -> None:
        script = textwrap.dedent("""
            from dataclasses import dataclass
            import model_config

            for name in tuple(vars(model_config)):
                if name.startswith("VERSION7B_"):
                    delattr(model_config, name)

            @dataclass(frozen=True)
            class LegacyModelSettings:
                expected_goals_minimum: float = 0.15
                expected_goals_maximum: float = 4.00

            @dataclass(frozen=True)
            class LegacyStandingsSettings:
                points_change_per_unit: float = 0.05
                points_max_adjustment: float = 0.05
                goal_difference_change_per_unit: float = 0.03
                goal_difference_max_adjustment: float = 0.03
                total_max_adjustment: float = 0.08

            model_config.ModelSettings = LegacyModelSettings
            model_config.StandingsSettings = LegacyStandingsSettings

            import model_evaluation
            import bootstrap_evaluation
            import parameter_manager
            import model_optimizer
            import model_compare
            import model_optimization_ui

            runtime = parameter_manager.to_runtime_settings(
                parameter_manager.ModelParameters()
            )
            assert runtime.model.home_correction == 1.08
            assert runtime.standings.rank_change_per_position == 0.0
            assert hasattr(
                model_config,
                "VERSION7B_DEFAULT_EVALUATION_WEIGHTS",
            )
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
