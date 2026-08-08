"""Version7-B予測パイプラインの後方互換入口。"""

from __future__ import annotations

from types import ModuleType
from typing import Any, Callable

from version7b_runtime import (
    PREDICT_MATCH_PARAMETERS,
    ensure_version7b_model_call_path,
)


def ensure_version7b_model_pipeline() -> ModuleType:
    """全モデル関数を検証し、現行model_pipelineを返す。"""

    return ensure_version7b_model_call_path().model_pipeline


def get_version7b_predict_match() -> Callable[..., Any]:
    """Trial実行時点で検査済みのpredict_match実体を返す。"""

    return ensure_version7b_model_pipeline().predict_match


ensure_version7b_model_pipeline()

__all__ = (
    "PREDICT_MATCH_PARAMETERS",
    "ensure_version7b_model_pipeline",
    "get_version7b_predict_match",
)
