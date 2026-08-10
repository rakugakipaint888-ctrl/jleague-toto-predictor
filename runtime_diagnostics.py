"""実行中のGit commitとVersion7-C moduleを診断する。"""

from __future__ import annotations

import inspect
import logging
import subprocess
from pathlib import Path
from types import ModuleType


UNKNOWN = "UNKNOWN"
LOGGER = logging.getLogger(__name__)
_LOGGED_SIGNATURES: set[tuple[str, str, str]] = set()


def get_app_commit(project_root: Path | None = None) -> str:
    """現在のcheckoutの完全なCommit SHAを返し、取得不能時はUNKNOWNにする。"""

    root = project_root or Path(__file__).resolve().parent
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return UNKNOWN

    commit_sha = completed.stdout.strip()
    if completed.returncode != 0 or not commit_sha:
        return UNKNOWN
    return commit_sha


def short_app_commit(commit_sha: str) -> str:
    """画面表示用にCommit SHAを8文字へ短縮する。"""

    return commit_sha[:8] if commit_sha != UNKNOWN else UNKNOWN


def first_scalar_source(module: ModuleType) -> str:
    """実際にload済みの_first_scalarソースを返す。"""

    helper = getattr(module, "_first_scalar", None)
    if helper is None:
        return "MISSING: _first_scalar is not loaded"
    try:
        return inspect.getsource(helper).strip()
    except (OSError, TypeError):
        return "UNAVAILABLE: inspect.getsource(_first_scalar) failed"


def log_runtime_diagnostics(module: ModuleType, commit_sha: str) -> None:
    """Cloud起動ログへCommit・module path・実loadソースを出す。"""

    module_file = str(getattr(module, "__file__", UNKNOWN))
    source = first_scalar_source(module)
    signature = (commit_sha, module_file, source)
    if signature in _LOGGED_SIGNATURES:
        return
    _LOGGED_SIGNATURES.add(signature)

    LOGGER.warning("Runtime diagnostic | App Commit: %s", commit_sha)
    LOGGER.warning(
        "Runtime diagnostic | bet_optimization_ui.__file__: %s",
        module_file,
    )
    LOGGER.warning(
        "Runtime diagnostic | inspect.getsource(_first_scalar):\n%s",
        source,
    )
