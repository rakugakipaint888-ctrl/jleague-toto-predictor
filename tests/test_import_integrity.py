"""全ローカルimportとVersion7-Bの再実行互換性を検証する。"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
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
VERSION7C_MODULES = (
    "bet_config",
    "bet_optimizer",
    "bet_export",
    "bet_evaluation",
    "bet_optimization_ui",
)
VERSION8A_MODULES = (
    "live_history",
    "live_history_ui",
)
VERSION8B_MODULES = (
    "diagnostic_config",
    "diagnostic_metrics",
    "model_diagnostics",
    "diagnostic_history",
    "diagnostic_ui",
)
CLEAN_PROCESS_IMPORT_MODULES = (
    "metrics",
    *VERSION8B_MODULES,
)
VERSION7C_IMPORT_GRAPH_MODULES = {
    "app",
    "analysis",
    "bet_config",
    "bet_optimizer",
    "bet_export",
    "bet_evaluation",
    "bet_optimization_ui",
}


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

    def test_version7c_import_graph_has_no_cycles(self) -> None:
        graph = _local_import_graph(_module_trees())
        version7c_graph = {
            module: graph[module] & VERSION7C_IMPORT_GRAPH_MODULES
            for module in VERSION7C_IMPORT_GRAPH_MODULES
        }
        self.assertEqual(_find_cycle(version7c_graph), ())

    def test_bet_optimizer_imports_only_existing_bet_config_names(self) -> None:
        trees = _module_trees()
        config_exports = _exported_names(trees["bet_config"])
        optimizer_config_imports = {
            alias.name
            for node in ast.walk(trees["bet_optimizer"])
            if isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == "bet_config"
            for alias in node.names
            if alias.name != "*"
        }
        self.assertTrue(optimizer_config_imports)
        self.assertEqual(optimizer_config_imports - config_exports, set())

    def test_app_uses_static_version7c_imports_without_reload(self) -> None:
        tree = _module_trees()["app"]
        imported_renderers = {
            (node.module, alias.name)
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }
        self.assertIn(
            ("analysis", "render_analysis_tab"),
            imported_renderers,
        )
        self.assertIn(
            ("bet_optimization_ui", "render_bet_optimization_tab"),
            imported_renderers,
        )
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "reload"
                for node in ast.walk(tree)
            )
        )

    def test_version7c_modules_import_in_one_clean_process(self) -> None:
        script = "\n".join(
            [*(f"import {module}" for module in VERSION7C_MODULES), """
assert bet_export.BetPlan is bet_optimizer.BetPlan
assert bet_evaluation.BetPlan is bet_optimizer.BetPlan
assert bet_optimization_ui.BetPlan is bet_optimizer.BetPlan
assert bet_optimization_ui.BET_PLAN_DISPLAY_COLUMNS is bet_export.BET_PLAN_DISPLAY_COLUMNS
print("Version7-C clean imports: OK")
"""]
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

    def test_version8a_modules_import_in_one_clean_process(self) -> None:
        script = "\n".join(
            [
                *(f"import {module}" for module in VERSION8A_MODULES),
                'print("Version8-A clean imports: OK")',
            ]
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

    def test_version8b_modules_import_in_independent_clean_processes(self) -> None:
        failures = []
        with tempfile.TemporaryDirectory() as pycache_directory:
            for module in CLEAN_PROCESS_IMPORT_MODULES:
                environment = os.environ.copy()
                environment["PYTHONPYCACHEPREFIX"] = pycache_directory
                completed = subprocess.run(
                    [sys.executable, "-c", f"import {module}"],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if completed.returncode != 0:
                    failures.append(
                        f"{module}: {completed.stderr or completed.stdout}"
                    )
        self.assertEqual(failures, [])

    def test_model_diagnostics_uses_version8b_diagnostic_metrics(self) -> None:
        tree = _module_trees()["model_diagnostics"]
        imports_by_module = {
            node.module: {alias.name for alias in node.names}
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertEqual(
            imports_by_module["diagnostic_metrics"],
            {"OneVsRestMetrics", "evaluate_one_vs_rest"},
        )
        self.assertTrue(
            {"OneVsRestMetrics", "evaluate_one_vs_rest"}.isdisjoint(
                imports_by_module["metrics"]
            )
        )

    def test_app_imports_in_a_clean_python_process(self) -> None:
        with tempfile.TemporaryDirectory() as pycache_directory:
            environment = os.environ.copy()
            environment["PYTHONPYCACHEPREFIX"] = pycache_directory
            environment["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
            completed = subprocess.run(
                [sys.executable, "-c", "import app"],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr or completed.stdout,
        )

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
