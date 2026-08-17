"""現在App Versionの単一管理とStreamlit描画を新規processで確認する。"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AppVersionDisplayTest(unittest.TestCase):
    def test_app_version_has_one_source_definition(self) -> None:
        definitions: list[Path] = []
        for path in PROJECT_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(target, ast.Name) and target.id == "APP_VERSION"
                    for target in targets
                ):
                    definitions.append(path)
        self.assertEqual(definitions, [PROJECT_ROOT / "app_version.py"])

        app_source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("from app_version import APP_VERSION", app_source)
        self.assertIn(
            'st.sidebar.caption(f"App Version: {APP_VERSION}")',
            app_source,
        )

    def test_stale_import_cache_still_renders_source_version_and_current_commit(self) -> None:
        script = r'''
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import app_version
from data_loader import CsvMatchDataSource
from history_manager import TotoRoundLoadResult
from live_history import LiveHistoryManager
from streamlit.testing.v1 import AppTest
from runtime_diagnostics import get_app_commit, short_app_commit

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    csv_path = root / "matches.csv"
    csv_path.write_text(
        "match_number,home_team,away_team,home_scored,home_conceded,"
        "away_scored,away_conceded\n"
        "1,鹿島アントラーズ,浦和レッズ,2.0,0.8,1.4,1.2\n",
        encoding="utf-8",
    )
    live_manager = LiveHistoryManager(
        round_path=root / "live_round_history.csv",
        match_path=root / "live_match_history.csv",
        bet_path=root / "live_bet_history.csv",
    )
    no_round = TotoRoundLoadResult(
        toto_round=None,
        source_name="テスト",
        status="error",
        message="テストでは開催回を読み込みません。",
    )
    app_version.APP_VERSION = "Version7.5"
    with (
        patch.dict(os.environ, {"JLEAGUE_ELO_CACHE_PATH": str(root / "elo.json")}),
        patch(
            "data_loader.get_default_data_sources",
            return_value=(CsvMatchDataSource(csv_path),),
        ),
        patch(
            "history_manager.TotoHistoryManager.load_current_round",
            return_value=no_round,
        ),
        patch(
            "prediction_history.PredictionHistoryManager.load",
            return_value=pd.DataFrame(),
        ),
        patch("live_history.LiveHistoryManager", return_value=live_manager),
    ):
        app = AppTest.from_file("app.py").run(timeout=40)
    captions = [item.value for item in app.caption]
    expected_commit = short_app_commit(get_app_commit())
    assert len(app.exception) == 0, [str(item.value) for item in app.exception]
    assert "App Version: Version8" in captions, captions
    assert f"App Commit: {expected_commit}" in captions, captions
    assert app_version.APP_VERSION == "Version8", app_version.APP_VERSION
    print("SIDEBAR_VERSION=App Version: Version8")
    print(f"SIDEBAR_COMMIT=App Commit: {expected_commit}")
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SIDEBAR_VERSION=App Version: Version8", result.stdout)
        self.assertIn("SIDEBAR_COMMIT=App Commit:", result.stdout)


if __name__ == "__main__":
    unittest.main()
