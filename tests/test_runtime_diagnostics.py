"""Cloud実行コード診断の回帰テスト。"""

from __future__ import annotations

import subprocess
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import bet_optimization_ui
from runtime_diagnostics import (
    UNKNOWN,
    first_scalar_source,
    get_app_commit,
    log_runtime_diagnostics,
    short_app_commit,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuntimeDiagnosticsTest(unittest.TestCase):
    def test_get_app_commit_matches_current_checkout(self) -> None:
        expected = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()

        self.assertEqual(get_app_commit(PROJECT_ROOT), expected)

    def test_get_app_commit_returns_unknown_when_git_is_unavailable(self) -> None:
        with patch("runtime_diagnostics.subprocess.run", side_effect=OSError):
            self.assertEqual(get_app_commit(PROJECT_ROOT), UNKNOWN)

    def test_short_app_commit_uses_eight_characters(self) -> None:
        self.assertEqual(short_app_commit("25402081ceeeb71e"), "25402081")
        self.assertEqual(short_app_commit(UNKNOWN), UNKNOWN)

    def test_first_scalar_source_is_loaded_code(self) -> None:
        source = first_scalar_source(bet_optimization_ui)

        self.assertIn("if isinstance(values, pd.Series):", source)
        self.assertIn("return values.iloc[0]", source)

    def test_first_scalar_source_reports_missing_old_module(self) -> None:
        module = types.ModuleType("old_bet_optimization_ui")

        self.assertEqual(
            first_scalar_source(module),
            "MISSING: _first_scalar is not loaded",
        )

    def test_log_contains_commit_file_and_source(self) -> None:
        with self.assertLogs("runtime_diagnostics", level="WARNING") as logs:
            log_runtime_diagnostics(bet_optimization_ui, "25402081")

        output = "\n".join(logs.output)
        self.assertIn("App Commit: 25402081", output)
        self.assertIn("bet_optimization_ui.__file__:", output)
        self.assertIn("return values.iloc[0]", output)


if __name__ == "__main__":
    unittest.main()
