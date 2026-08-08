"""Version7-Bが使う予測パイプラインの再実行互換性を管理する。

Streamlitのhot rerunでは、更新前の ``model_pipeline`` が ``sys.modules``
に残ったまま、新規Version7-Bモジュールだけが読み込まれる場合がある。
Version7-Bはこの入口で ``predict_match`` の実体とsignatureを検査し、
必要な設定引数がない旧moduleだけを再読込する。
"""

from __future__ import annotations

import importlib
import inspect
import sys
from types import ModuleType
from typing import Any, Callable

from version7b_config import ensure_version7b_model_config

PREDICT_MATCH_PARAMETERS = (
    "home",
    "away",
    "options",
    "form_settings",
    "venue_settings",
    "standings_settings",
    "model_settings",
    "elo_settings",
)

# Version7-A以前からpredict_matchを直接importしているmodule。旧関数そのものを
# 保持している場合だけ、再読込後の同一関数へ参照を付け替える。
_DIRECT_IMPORT_CONSUMERS = (
    "app",
    "__main__",
    "backtest",
    "draw_optimizer",
    "model_optimizer",
)


def _contract_errors(module: ModuleType) -> tuple[str, ...]:
    """Version7-Bが必要とするpredict_match契約の不一致を返す。"""

    predict_function = getattr(module, "predict_match", None)
    if not callable(predict_function):
        return ("predict_match",)
    try:
        signature = inspect.signature(predict_function)
    except (TypeError, ValueError):
        return ("predict_match.signature",)

    errors = []
    for parameter_name in PREDICT_MATCH_PARAMETERS:
        parameter = signature.parameters.get(parameter_name)
        if parameter is None:
            errors.append(f"predict_match.{parameter_name}")
            continue
        if parameter_name.endswith("_settings") and parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            errors.append(f"predict_match.{parameter_name}.keyword")
    for class_name in ("ModelOptions", "TeamModelInput"):
        if not isinstance(getattr(module, class_name, None), type):
            errors.append(class_name)
    return tuple(errors)


def _rebind_cached_consumers(
    previous_predict_match: Any,
    current_module: ModuleType,
) -> None:
    """旧predict_matchを直接保持する読込済みmoduleだけを安全に更新する。"""

    current_predict_match = current_module.predict_match
    for module_name in _DIRECT_IMPORT_CONSUMERS:
        consumer = sys.modules.get(module_name)
        if consumer is None:
            continue
        if getattr(consumer, "predict_match", None) is previous_predict_match:
            setattr(consumer, "predict_match", current_predict_match)


def ensure_version7b_model_pipeline() -> ModuleType:
    """現行predict_matchを保証し、旧module cacheだけを再読込する。"""

    ensure_version7b_model_config()
    pipeline_module = importlib.import_module("model_pipeline")
    previous_predict_match = getattr(pipeline_module, "predict_match", None)
    errors = _contract_errors(pipeline_module)
    if errors:
        importlib.invalidate_caches()
        pipeline_module = importlib.reload(pipeline_module)
        errors = _contract_errors(pipeline_module)
    if errors:
        missing_names = ", ".join(errors)
        raise ImportError(
            "model_pipeline.pyのVersion7-B予測契約を確認できません: " f"{missing_names}"
        )
    if previous_predict_match is not pipeline_module.predict_match:
        _rebind_cached_consumers(previous_predict_match, pipeline_module)
    return pipeline_module


def get_version7b_predict_match() -> Callable[..., Any]:
    """Trial実行時点で検査済みのpredict_match実体を返す。"""

    return ensure_version7b_model_pipeline().predict_match


ensure_version7b_model_pipeline()

__all__ = (
    "PREDICT_MATCH_PARAMETERS",
    "ensure_version7b_model_pipeline",
    "get_version7b_predict_match",
)
