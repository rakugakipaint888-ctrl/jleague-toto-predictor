"""Version7-Bの予測モデル呼び出し経路を実行前に検証する。

Streamlitのhot rerunでは、更新前のmoduleが ``sys.modules`` に残り、
現行の ``model_pipeline.predict_match`` が旧 ``prediction`` 関数を
global参照として保持する場合がある。この入口は最上位関数だけでなく、
Trialが使う各補正関数の定義元・名前・signature・直接import参照を確認し、
不一致のあるmoduleだけを依存順に再読込する。
"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from version7b_config import ensure_version7b_model_config


@dataclass(frozen=True)
class FunctionContract:
    """本番経路で必要な関数signature。"""

    module_name: str
    function_name: str
    parameters: tuple[str, ...]
    keyword_parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeFunctionIdentity:
    """監査後に実際に解決された関数情報。"""

    module_name: str
    function_name: str
    signature: str
    source_file: str


@dataclass(frozen=True)
class Version7BModelRuntime:
    """1 Trialが直接使用する検証済みmodule群。"""

    prediction: ModuleType
    elo_rating: ModuleType
    form_adjuster: ModuleType
    venue_adjuster: ModuleType
    standings_adjuster: ModuleType
    draw_predictor: ModuleType
    model_pipeline: ModuleType


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

MODEL_CALL_FUNCTION_CONTRACTS = (
    FunctionContract(
        "prediction",
        "calculate_expected_goals",
        (
            "home_scored",
            "home_conceded",
            "away_scored",
            "away_conceded",
            "home_correction",
            "expected_goals_minimum",
            "expected_goals_maximum",
        ),
        (
            "home_correction",
            "expected_goals_minimum",
            "expected_goals_maximum",
        ),
    ),
    FunctionContract(
        "prediction",
        "calculate_match_probabilities",
        ("home_expected", "away_expected"),
    ),
    FunctionContract(
        "prediction",
        "get_toto_prediction",
        ("home_win", "draw", "away_win"),
    ),
    FunctionContract(
        "elo_rating",
        "adjust_expected_goals",
        (
            "home_expected",
            "away_expected",
            "home_elo",
            "away_elo",
            "enabled",
            "settings",
        ),
        ("enabled", "settings"),
    ),
    FunctionContract(
        "elo_rating",
        "generate_elo_ratings",
        (
            "matches",
            "team_categories",
            "settings",
            "as_of",
            "team_name_normalizer",
        ),
        ("team_categories", "settings", "as_of", "team_name_normalizer"),
    ),
    FunctionContract(
        "elo_rating",
        "get_team_elo",
        ("team_name", "elo_result", "team_name_normalizer"),
        ("team_name_normalizer",),
    ),
    FunctionContract(
        "form_adjuster",
        "adjust_team_form",
        (
            "regular_scored",
            "regular_conceded",
            "season_scored",
            "season_conceded",
            "recent_matches",
            "enabled",
            "settings",
        ),
        ("enabled", "settings"),
    ),
    FunctionContract(
        "venue_adjuster",
        "adjust_for_venue",
        (
            "home_scored",
            "home_conceded",
            "away_scored",
            "away_conceded",
            "home_record",
            "away_record",
            "enabled",
            "settings",
        ),
        ("home_record", "away_record", "enabled", "settings"),
    ),
    FunctionContract(
        "standings_adjuster",
        "adjust_expected_goals_by_standings",
        ("home_expected", "away_expected", "home", "away", "enabled", "settings"),
        ("enabled", "settings"),
    ),
    FunctionContract(
        "draw_predictor",
        "build_draw_context",
        ("matches", "cutoff_at", "category"),
        ("category",),
    ),
    FunctionContract(
        "draw_predictor",
        "predict_draw_aware",
        (
            "base_probabilities",
            "home_expected_goals",
            "away_expected_goals",
            "home",
            "away",
            "context",
            "settings",
        ),
        ("context", "settings"),
    ),
    FunctionContract(
        "model_pipeline",
        "_calculate_pair",
        (
            "home_scored",
            "home_conceded",
            "away_scored",
            "away_conceded",
            "settings",
        ),
        ("settings",),
    ),
    FunctionContract(
        "model_pipeline",
        "_apply_elo",
        ("expected", "home_elo", "away_elo", "requested", "settings"),
        ("settings",),
    ),
    FunctionContract(
        "model_pipeline",
        "predict_match",
        PREDICT_MATCH_PARAMETERS,
        (
            "options",
            "form_settings",
            "venue_settings",
            "standings_settings",
            "model_settings",
            "elo_settings",
        ),
    ),
)

_DEPENDENCY_ORDER = (
    "prediction",
    "elo_rating",
    "form_adjuster",
    "venue_adjuster",
    "standings_adjuster",
    "draw_predictor",
)

_MODEL_CONFIG_BINDINGS = {
    "elo_rating": ("EloSettings", "DEFAULT_ELO_SETTINGS"),
    "form_adjuster": ("FormSettings", "DEFAULT_FORM_SETTINGS"),
    "venue_adjuster": ("VenueSettings", "DEFAULT_VENUE_SETTINGS"),
    "standings_adjuster": (
        "StandingsSettings",
        "DEFAULT_STANDINGS_SETTINGS",
    ),
}

_PIPELINE_IMPORT_BINDINGS = {
    "calculate_expected_goals": ("prediction", "calculate_expected_goals"),
    "calculate_match_probabilities": (
        "prediction",
        "calculate_match_probabilities",
    ),
    "get_toto_prediction": ("prediction", "get_toto_prediction"),
    "adjust_expected_goals": ("elo_rating", "adjust_expected_goals"),
    "adjust_team_form": ("form_adjuster", "adjust_team_form"),
    "adjust_for_venue": ("venue_adjuster", "adjust_for_venue"),
    "adjust_expected_goals_by_standings": (
        "standings_adjuster",
        "adjust_expected_goals_by_standings",
    ),
    "FormAdjustment": ("form_adjuster", "FormAdjustment"),
    "VenueAdjustment": ("venue_adjuster", "VenueAdjustment"),
    "StandingMetrics": ("standings_adjuster", "StandingMetrics"),
    "StandingsAdjustment": ("standings_adjuster", "StandingsAdjustment"),
}

_PIPELINE_CONFIG_BINDINGS = (
    "EloSettings",
    "FormSettings",
    "VenueSettings",
    "StandingsSettings",
    "ModelSettings",
    "DEFAULT_ELO_SETTINGS",
    "DEFAULT_FORM_SETTINGS",
    "DEFAULT_VENUE_SETTINGS",
    "DEFAULT_STANDINGS_SETTINGS",
    "DEFAULT_MODEL_SETTINGS",
)

_DIRECT_IMPORT_CONSUMERS = (
    "app",
    "__main__",
    "backtest",
    "draw_optimizer",
    "draw_predictor",
    "model_optimizer",
    "model_pipeline",
    "parameter_manager",
    "version7b_pipeline",
)

_PREDICT_MATCH_CONSUMERS = (
    "app",
    "__main__",
    "backtest",
    "draw_optimizer",
)


def _contracts_for(module_name: str) -> tuple[FunctionContract, ...]:
    return tuple(
        contract
        for contract in MODEL_CALL_FUNCTION_CONTRACTS
        if contract.module_name == module_name
    )


def _function_errors(
    module: ModuleType,
    contract: FunctionContract,
) -> tuple[str, ...]:
    function = getattr(module, contract.function_name, None)
    prefix = f"{contract.module_name}.{contract.function_name}"
    if not callable(function):
        return (prefix,)
    errors = []
    if getattr(function, "__module__", None) != contract.module_name:
        errors.append(f"{prefix}.__module__")
    if getattr(function, "__name__", None) != contract.function_name:
        errors.append(f"{prefix}.__name__")
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return (*errors, f"{prefix}.signature")
    if tuple(signature.parameters) != contract.parameters:
        errors.append(f"{prefix}.parameters")
    for parameter_name in contract.keyword_parameters:
        parameter = signature.parameters.get(parameter_name)
        if parameter is None or parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            errors.append(f"{prefix}.{parameter_name}.keyword")
    return tuple(errors)


def _dataclass_fields(value: Any) -> set[str]:
    return set(getattr(value, "__dataclass_fields__", {}))


def _module_errors(
    module_name: str,
    module: ModuleType,
    model_config: ModuleType,
) -> tuple[str, ...]:
    errors = [
        error
        for contract in _contracts_for(module_name)
        for error in _function_errors(module, contract)
    ]
    for name in _MODEL_CONFIG_BINDINGS.get(module_name, ()):
        if getattr(module, name, None) is not getattr(model_config, name, None):
            errors.append(f"{module_name}.{name}.model_config_identity")
    if module_name == "standings_adjuster":
        required_fields = {
            "rank_adjustment_rate",
            "home_rank",
            "away_rank",
        }
        if not required_fields.issubset(
            _dataclass_fields(getattr(module, "StandingsAdjustment", None))
        ):
            errors.append("standings_adjuster.StandingsAdjustment.rank_fields")
    return tuple(errors)


def _pipeline_errors(
    pipeline: ModuleType,
    modules: dict[str, ModuleType],
    model_config: ModuleType,
) -> tuple[str, ...]:
    errors = list(_module_errors("model_pipeline", pipeline, model_config))
    for local_name, (module_name, public_name) in _PIPELINE_IMPORT_BINDINGS.items():
        if getattr(pipeline, local_name, None) is not getattr(
            modules[module_name], public_name, None
        ):
            errors.append(f"model_pipeline.{local_name}.import_identity")
    for name in _PIPELINE_CONFIG_BINDINGS:
        if getattr(pipeline, name, None) is not getattr(model_config, name, None):
            errors.append(f"model_pipeline.{name}.model_config_identity")
    return tuple(errors)


def _public_objects(module: ModuleType) -> dict[str, Any]:
    return {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("__") and callable(value)
    }


def _rebind_cached_consumers(
    previous_objects: dict[str, Any],
    current_module: ModuleType,
) -> None:
    """旧objectを直接保持する既存consumerだけを現行objectへ接続する。"""

    for consumer_name in _DIRECT_IMPORT_CONSUMERS:
        consumer = sys.modules.get(consumer_name)
        if consumer is None or consumer is current_module:
            continue
        for object_name, previous in previous_objects.items():
            current = getattr(current_module, object_name, None)
            if current is None or current is previous:
                continue
            if getattr(consumer, object_name, None) is previous:
                setattr(consumer, object_name, current)


def _rebind_predict_match_consumers(pipeline: ModuleType) -> None:
    """同じmodel_pipeline由来の旧predict_match参照を現行関数へ揃える。"""

    current = pipeline.predict_match
    for consumer_name in _PREDICT_MATCH_CONSUMERS:
        consumer = sys.modules.get(consumer_name)
        if consumer is None:
            continue
        previous = getattr(consumer, "predict_match", None)
        if (
            callable(previous)
            and previous is not current
            and getattr(previous, "__module__", None) == "model_pipeline"
            and getattr(previous, "__name__", None) == "predict_match"
        ):
            setattr(consumer, "predict_match", current)


def _reload_if_needed(
    module_name: str,
    module: ModuleType,
    model_config: ModuleType,
) -> ModuleType:
    errors = _module_errors(module_name, module, model_config)
    if not errors:
        return module
    previous_objects = _public_objects(module)
    importlib.invalidate_caches()
    module = importlib.reload(module)
    errors = _module_errors(module_name, module, model_config)
    if errors:
        raise ImportError(
            "Version7-Bモデル関数契約を確認できません: " + ", ".join(errors)
        )
    _rebind_cached_consumers(previous_objects, module)
    return module


def ensure_version7b_model_call_path() -> Version7BModelRuntime:
    """1 Trialが使う全module契約を確認し、検証済み実体を返す。"""

    model_config = ensure_version7b_model_config()
    modules: dict[str, ModuleType] = {}
    for module_name in _DEPENDENCY_ORDER:
        module = importlib.import_module(module_name)
        modules[module_name] = _reload_if_needed(
            module_name,
            module,
            model_config,
        )

    pipeline = importlib.import_module("model_pipeline")
    previous_pipeline_objects = _public_objects(pipeline)
    errors = _pipeline_errors(pipeline, modules, model_config)
    if errors:
        importlib.invalidate_caches()
        pipeline = importlib.reload(pipeline)
        errors = _pipeline_errors(pipeline, modules, model_config)
    if errors:
        raise ImportError(
            "Version7-B予測パイプライン契約を確認できません: "
            + ", ".join(errors)
        )
    _rebind_cached_consumers(previous_pipeline_objects, pipeline)
    _rebind_predict_match_consumers(pipeline)
    return Version7BModelRuntime(
        prediction=modules["prediction"],
        elo_rating=modules["elo_rating"],
        form_adjuster=modules["form_adjuster"],
        venue_adjuster=modules["venue_adjuster"],
        standings_adjuster=modules["standings_adjuster"],
        draw_predictor=modules["draw_predictor"],
        model_pipeline=pipeline,
    )


def runtime_function_identities() -> tuple[RuntimeFunctionIdentity, ...]:
    """検証後に実際に呼ばれる関数の定義元・signatureを返す。"""

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
    identities = []
    for contract in MODEL_CALL_FUNCTION_CONTRACTS:
        function = getattr(modules[contract.module_name], contract.function_name)
        source = inspect.getsourcefile(function) or ""
        identities.append(
            RuntimeFunctionIdentity(
                module_name=function.__module__,
                function_name=function.__name__,
                signature=str(inspect.signature(function)),
                source_file=str(Path(source).resolve()) if source else "",
            )
        )
    return tuple(identities)


__all__ = (
    "FunctionContract",
    "MODEL_CALL_FUNCTION_CONTRACTS",
    "PREDICT_MATCH_PARAMETERS",
    "RuntimeFunctionIdentity",
    "Version7BModelRuntime",
    "ensure_version7b_model_call_path",
    "runtime_function_identities",
)
