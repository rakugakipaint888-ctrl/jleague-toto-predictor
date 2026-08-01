"""Version5のモジュール責務と循環import防止を確認する。"""

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_MODULES = {
    path.stem: path
    for path in PROJECT_ROOT.glob("*.py")
}


def local_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])

    return imports & set(PROJECT_MODULES)


class ArchitectureTest(unittest.TestCase):
    def test_project_modules_have_no_circular_imports(self) -> None:
        graph = {
            module_name: local_imports(path)
            for module_name, path in PROJECT_MODULES.items()
        }
        visiting = set()
        visited = set()

        def visit(module_name: str) -> None:
            if module_name in visiting:
                self.fail(f"循環importを検出しました: {module_name}")
            if module_name in visited:
                return

            visiting.add(module_name)
            for dependency in graph[module_name]:
                visit(dependency)
            visiting.remove(module_name)
            visited.add(module_name)

        for module_name in graph:
            visit(module_name)

    def test_adjustment_modules_are_connected_only_by_model_pipeline(self) -> None:
        app_imports = local_imports(PROJECT_ROOT / "app.py")
        self.assertIn("model_pipeline", app_imports)
        self.assertFalse(
            {
                "form_adjuster",
                "venue_adjuster",
                "standings_adjuster",
            }
            & app_imports
        )

    def test_teams_and_elo_remain_independent(self) -> None:
        self.assertNotIn(
            "elo_rating",
            local_imports(PROJECT_ROOT / "teams.py"),
        )
        self.assertNotIn(
            "teams",
            local_imports(PROJECT_ROOT / "elo_rating.py"),
        )


if __name__ == "__main__":
    unittest.main()
