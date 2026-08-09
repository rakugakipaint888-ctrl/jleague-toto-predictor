"""Version7-B設定のimport契約とStreamlit再実行互換性を管理する。

Streamlitのhot rerunでは、更新前の ``model_config`` が ``sys.modules``
に残ったまま、新規Version7-Bモジュールだけが読み込まれる場合がある。
Version7-B側はこのモジュールを入口にし、必要な定数とdataclassフィールドを
検証してから ``model_config`` の公開値を再利用する。
"""

from __future__ import annotations

import importlib
from types import ModuleType

import model_config as _model_config

VERSION7B_CONFIG_EXPORTS = (
    "VERSION7B_MODEL_VERSION",
    "VERSION7B_DEFAULT_MODEL_PARAMETERS",
    "VERSION7B_MODEL_SEARCH_SPACE",
    "VERSION7B_MODEL_GRID_SPACE",
    "VERSION7B_DRAW_GRID_SPACE",
    "VERSION7B_TRIAL_COUNT_DEFAULT",
    "VERSION7B_TRIAL_COUNT_CHOICES",
    "VERSION7B_MODEL_LIMITS",
    "VERSION7B_RANDOM_SEED",
    "VERSION7B_RANKING_LIMIT",
    "VERSION7B_DEFAULT_EVALUATION_WEIGHTS",
    "VERSION7B_ROBUST_SELECTION_SETTINGS",
    "VERSION7B_DRAW_DEGRADATION_TOLERANCES",
    "VERSION7B_OVERFIT_THRESHOLDS",
    "VERSION7B_BOOTSTRAP_CHOICES",
)

_REQUIRED_DATACLASS_FIELDS = {
    "ModelSettings": ("home_correction",),
    "StandingsSettings": ("rank_change_per_position", "rank_max_adjustment"),
}


def _missing_contract(module: ModuleType) -> tuple[str, ...]:
    """Version7-Bが必要とする定数・型フィールドの不足名を返す。"""

    missing = [name for name in VERSION7B_CONFIG_EXPORTS if not hasattr(module, name)]
    for class_name, required_fields in _REQUIRED_DATACLASS_FIELDS.items():
        settings_class = getattr(module, class_name, None)
        dataclass_fields = getattr(settings_class, "__dataclass_fields__", {})
        missing.extend(
            f"{class_name}.{field_name}"
            for field_name in required_fields
            if field_name not in dataclass_fields
        )
    return tuple(missing)


def ensure_version7b_model_config() -> ModuleType:
    """旧module cacheを検出した場合だけmodel_configを安全に再読込する。"""

    missing = _missing_contract(_model_config)
    if missing:
        importlib.invalidate_caches()
        importlib.reload(_model_config)
        missing = _missing_contract(_model_config)
    if missing:
        missing_names = ", ".join(missing)
        raise ImportError(
            "model_config.pyのVersion7-B設定契約を確認できません: " f"{missing_names}"
        )
    return _model_config


_current_model_config = ensure_version7b_model_config()

VERSION7B_MODEL_VERSION = _current_model_config.VERSION7B_MODEL_VERSION
VERSION7B_DEFAULT_MODEL_PARAMETERS = (
    _current_model_config.VERSION7B_DEFAULT_MODEL_PARAMETERS
)
VERSION7B_MODEL_SEARCH_SPACE = _current_model_config.VERSION7B_MODEL_SEARCH_SPACE
VERSION7B_MODEL_GRID_SPACE = _current_model_config.VERSION7B_MODEL_GRID_SPACE
VERSION7B_DRAW_GRID_SPACE = _current_model_config.VERSION7B_DRAW_GRID_SPACE
VERSION7B_TRIAL_COUNT_DEFAULT = _current_model_config.VERSION7B_TRIAL_COUNT_DEFAULT
VERSION7B_TRIAL_COUNT_CHOICES = _current_model_config.VERSION7B_TRIAL_COUNT_CHOICES
VERSION7B_MODEL_LIMITS = _current_model_config.VERSION7B_MODEL_LIMITS
VERSION7B_RANDOM_SEED = _current_model_config.VERSION7B_RANDOM_SEED
VERSION7B_RANKING_LIMIT = _current_model_config.VERSION7B_RANKING_LIMIT
VERSION7B_DEFAULT_EVALUATION_WEIGHTS = (
    _current_model_config.VERSION7B_DEFAULT_EVALUATION_WEIGHTS
)
VERSION7B_ROBUST_SELECTION_SETTINGS = (
    _current_model_config.VERSION7B_ROBUST_SELECTION_SETTINGS
)
VERSION7B_DRAW_DEGRADATION_TOLERANCES = (
    _current_model_config.VERSION7B_DRAW_DEGRADATION_TOLERANCES
)
VERSION7B_OVERFIT_THRESHOLDS = _current_model_config.VERSION7B_OVERFIT_THRESHOLDS
VERSION7B_BOOTSTRAP_CHOICES = _current_model_config.VERSION7B_BOOTSTRAP_CHOICES

__all__ = (*VERSION7B_CONFIG_EXPORTS, "ensure_version7b_model_config")
