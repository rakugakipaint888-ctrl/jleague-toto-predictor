"""Version7-Bの固定分割・シーズン／開催回ウォークフォワード。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

FIXED_SPLIT = "fixed"
SEASON_WALK_FORWARD = "season_walk_forward"
ROUND_WALK_FORWARD = "round_walk_forward"
VALIDATION_METHODS = (
    FIXED_SPLIT,
    SEASON_WALK_FORWARD,
    ROUND_WALK_FORWARD,
)


class ValidationDataError(ValueError):
    """未来データなしの分割を成立させられない。"""


@dataclass(frozen=True)
class ValidationFold:
    label: str
    training_rounds: tuple[Any, ...]
    validation_rounds: tuple[Any, ...]


@dataclass(frozen=True)
class ValidationSplit:
    method: str
    training_rounds: tuple[Any, ...]
    final_validation_rounds: tuple[Any, ...]
    folds: tuple[ValidationFold, ...]

    @property
    def training_period(self) -> str:
        return period_label(self.training_rounds)

    @property
    def validation_period(self) -> str:
        return period_label(self.final_validation_rounds)

    @property
    def actual_period(self) -> str:
        return period_label((*self.training_rounds, *self.final_validation_rounds))


def _cutoff(item: Any) -> datetime:
    value = getattr(item, "cutoff_at", None)
    if not isinstance(value, datetime):
        raise ValidationDataError("開催回の基準日時を確認できません。")
    return value


def _season(item: Any) -> str:
    value = str(getattr(item, "season", "") or "")
    if not value:
        raise ValidationDataError("開催回のシーズンを確認できません。")
    return value


def period_label(rounds: Sequence[Any]) -> str:
    if not rounds:
        return "確認できません"
    dates = sorted(_cutoff(item).date() for item in rounds)
    return f"{dates[0].isoformat()}～{dates[-1].isoformat()}"


def _validate_separation(
    training: Sequence[Any],
    validation: Sequence[Any],
    *,
    label: str,
) -> None:
    if not training:
        raise ValidationDataError(f"{label}のTrainingが0開催回です。")
    if not validation:
        raise ValidationDataError(f"{label}のValidationが0開催回です。")
    if max(_cutoff(item) for item in training) >= min(
        _cutoff(item) for item in validation
    ):
        raise ValidationDataError(f"{label}で未来データがTrainingへ混入します。")


def _fixed_split(rounds: tuple[Any, ...]) -> ValidationSplit:
    if len(rounds) < 4:
        raise ValidationDataError("固定分割には4開催回以上必要です。")
    outer_index = max(2, min(len(rounds) - 1, math.floor(len(rounds) * 0.8)))
    training = rounds[:outer_index]
    final_validation = rounds[outer_index:]
    inner_index = max(
        1,
        min(len(training) - 1, math.floor(len(training) * 0.8)),
    )
    fold = ValidationFold(
        "固定Training内20%",
        training[:inner_index],
        training[inner_index:],
    )
    _validate_separation(training, final_validation, label="最終固定分割")
    _validate_separation(
        fold.training_rounds,
        fold.validation_rounds,
        label=fold.label,
    )
    return ValidationSplit(FIXED_SPLIT, training, final_validation, (fold,))


def _season_walk_forward(rounds: tuple[Any, ...]) -> ValidationSplit:
    seasons = tuple(dict.fromkeys(_season(item) for item in rounds))
    if len(seasons) < 3:
        raise ValidationDataError(
            "シーズン単位ウォークフォワードには3シーズン以上必要です。"
            f"利用可能シーズン: {', '.join(seasons) or 'なし'}"
        )
    final_season = seasons[-1]
    training = tuple(item for item in rounds if _season(item) != final_season)
    final_validation = tuple(item for item in rounds if _season(item) == final_season)
    training_seasons = seasons[:-1]
    folds = []
    for index in range(1, len(training_seasons)):
        validation_season = training_seasons[index]
        fold_training_seasons = set(training_seasons[:index])
        fold_training = tuple(
            item for item in training if _season(item) in fold_training_seasons
        )
        fold_validation = tuple(
            item for item in training if _season(item) == validation_season
        )
        fold = ValidationFold(
            f"{validation_season}シーズン検証",
            fold_training,
            fold_validation,
        )
        _validate_separation(
            fold.training_rounds,
            fold.validation_rounds,
            label=fold.label,
        )
        folds.append(fold)
    _validate_separation(training, final_validation, label="最終シーズン分割")
    if not folds:
        raise ValidationDataError("Training内のシーズン検証Foldが0件です。")
    return ValidationSplit(
        SEASON_WALK_FORWARD,
        training,
        final_validation,
        tuple(folds),
    )


def _round_walk_forward(rounds: tuple[Any, ...]) -> ValidationSplit:
    if len(rounds) < 5:
        raise ValidationDataError(
            "開催回単位ウォークフォワードには5開催回以上必要です。"
        )
    outer_index = max(3, min(len(rounds) - 1, math.floor(len(rounds) * 0.8)))
    training = rounds[:outer_index]
    final_validation = rounds[outer_index:]
    initial_window = max(2, len(training) // 2)
    folds = []
    for index in range(initial_window, len(training)):
        fold = ValidationFold(
            f"第{getattr(training[index], 'round_id', index + 1)}回検証",
            training[:index],
            (training[index],),
        )
        _validate_separation(
            fold.training_rounds,
            fold.validation_rounds,
            label=fold.label,
        )
        folds.append(fold)
    _validate_separation(training, final_validation, label="最終開催回分割")
    if not folds:
        raise ValidationDataError("Training内の開催回検証Foldが0件です。")
    return ValidationSplit(
        ROUND_WALK_FORWARD,
        training,
        final_validation,
        tuple(folds),
    )


def create_validation_split(
    rounds: Sequence[Any],
    method: str = SEASON_WALK_FORWARD,
) -> ValidationSplit:
    """時系列順を維持し、最終Validationを探索対象から完全に分離する。"""

    if method not in VALIDATION_METHODS:
        raise ValidationDataError(f"未対応の検証方式です: {method}")
    ordered = tuple(sorted(rounds, key=_cutoff))
    if len({_cutoff(item) for item in ordered}) != len(ordered):
        raise ValidationDataError("同一基準日時の開催回が重複しています。")
    if method == FIXED_SPLIT:
        return _fixed_split(ordered)
    if method == ROUND_WALK_FORWARD:
        return _round_walk_forward(ordered)
    return _season_walk_forward(ordered)
