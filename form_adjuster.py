"""直近5試合を時系列で重み付けし、シーズン平均と混合する。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from config import DEFAULT_FORM_SETTINGS, FormSettings


def _value(item: Any, field_name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(field_name)
    return getattr(item, field_name, None)


def _valid_nonnegative(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number) or number < 0:
        return None
    return number


@dataclass(frozen=True)
class WeightedRecentForm:
    """取得できた直近試合から計算した加重平均。"""

    average_scored: Optional[float]
    average_conceded: Optional[float]
    match_count: int
    used_weights: tuple[float, ...] = ()


@dataclass(frozen=True)
class FormAdjustment:
    """通常平均、加重平均、シーズン平均を含む調整結果。"""

    scored: float
    conceded: float
    regular_scored: float
    regular_conceded: float
    weighted_scored: Optional[float]
    weighted_conceded: Optional[float]
    season_scored: Optional[float]
    season_conceded: Optional[float]
    recent_match_count: int
    enabled: bool
    applied: bool


def calculate_weighted_recent_form(
    recent_matches: Sequence[Any],
    settings: FormSettings = DEFAULT_FORM_SETTINGS,
) -> WeightedRecentForm:
    """最新順の試合へ5,4,3,2,1の順で重みを付ける。"""

    weighted_scored = 0.0
    weighted_conceded = 0.0
    total_weight = 0.0
    used_weights = []

    for index, recent_match in enumerate(recent_matches):
        if index >= len(settings.recent_match_weights):
            break

        scored = _valid_nonnegative(_value(recent_match, "scored"))
        conceded = _valid_nonnegative(_value(recent_match, "conceded"))

        if scored is None or conceded is None:
            continue

        weight = _valid_nonnegative(settings.recent_match_weights[index])

        if weight is None or weight <= 0:
            continue

        weighted_scored += scored * weight
        weighted_conceded += conceded * weight
        total_weight += weight
        used_weights.append(weight)

    if total_weight <= 0:
        return WeightedRecentForm(None, None, 0)

    return WeightedRecentForm(
        average_scored=weighted_scored / total_weight,
        average_conceded=weighted_conceded / total_weight,
        match_count=len(used_weights),
        used_weights=tuple(used_weights),
    )


def _blend_metric(
    regular_average: float,
    weighted_average: Optional[float],
    season_average: Optional[float],
    enabled: bool,
    settings: FormSettings,
) -> tuple[float, bool]:
    regular = _valid_nonnegative(regular_average)
    regular = regular if regular is not None else 0.0

    if not enabled or weighted_average is None:
        return regular, False

    weighted = _valid_nonnegative(weighted_average)
    season = _valid_nonnegative(season_average)

    if weighted is None:
        return regular, False
    if season is None:
        return weighted, True

    recent_share = max(0.0, float(settings.recent_weighted_share))
    season_share = max(0.0, float(settings.season_average_share))
    total_share = recent_share + season_share

    if total_share <= 0:
        return weighted, True

    return (
        (weighted * recent_share + season * season_share) / total_share,
        True,
    )


def adjust_team_form(
    regular_scored: float,
    regular_conceded: float,
    season_scored: Optional[float],
    season_conceded: Optional[float],
    recent_matches: Sequence[Any] = (),
    enabled: bool = True,
    settings: FormSettings = DEFAULT_FORM_SETTINGS,
) -> FormAdjustment:
    """通常平均を保持し、利用可能な場合だけ加重平均を混合する。"""

    weighted = calculate_weighted_recent_form(recent_matches, settings)
    scored, scored_applied = _blend_metric(
        regular_scored,
        weighted.average_scored,
        season_scored,
        enabled,
        settings,
    )
    conceded, conceded_applied = _blend_metric(
        regular_conceded,
        weighted.average_conceded,
        season_conceded,
        enabled,
        settings,
    )

    return FormAdjustment(
        scored=scored,
        conceded=conceded,
        regular_scored=float(regular_scored),
        regular_conceded=float(regular_conceded),
        weighted_scored=weighted.average_scored,
        weighted_conceded=weighted.average_conceded,
        season_scored=_valid_nonnegative(season_scored),
        season_conceded=_valid_nonnegative(season_conceded),
        recent_match_count=weighted.match_count,
        enabled=bool(enabled),
        applied=bool(scored_applied or conceded_applied),
    )
