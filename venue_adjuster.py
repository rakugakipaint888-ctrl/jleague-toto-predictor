"""ホーム・アウェイ別成績を全体成績へ安全に混合する。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from model_config import DEFAULT_VENUE_SETTINGS, VenueSettings


def _valid_nonnegative(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number) or number < 0:
        return None
    return number


@dataclass(frozen=True)
class VenueSideAdjustment:
    """1クラブの会場別混合後の攻撃・守備指標。"""

    scored: float
    conceded: float
    venue_scored: Optional[float]
    venue_conceded: Optional[float]
    matches_played: int
    venue_share: float
    applied: bool


@dataclass(frozen=True)
class VenueAdjustment:
    """対戦するホーム側とアウェイ側の会場別調整結果。"""

    home: VenueSideAdjustment
    away: VenueSideAdjustment
    enabled: bool
    applied: bool


def _record_metric(record: Any, field_name: str, default: Any = 0) -> Any:
    if record is None:
        return default
    if isinstance(record, dict):
        return record.get(field_name, default)
    return getattr(record, field_name, default)


def _adjust_side(
    overall_scored: float,
    overall_conceded: float,
    record: Any,
    enabled: bool,
    settings: VenueSettings,
) -> VenueSideAdjustment:
    try:
        played = max(0, int(_record_metric(record, "played", 0)))
    except (TypeError, ValueError):
        played = 0

    goals_for = _valid_nonnegative(_record_metric(record, "goals_for", None))
    goals_against = _valid_nonnegative(
        _record_metric(record, "goals_against", None)
    )
    overall_scored_value = _valid_nonnegative(overall_scored)
    overall_conceded_value = _valid_nonnegative(overall_conceded)
    overall_scored_value = (
        overall_scored_value if overall_scored_value is not None else 0.0
    )
    overall_conceded_value = (
        overall_conceded_value if overall_conceded_value is not None else 0.0
    )

    venue_scored = goals_for / played if played and goals_for is not None else None
    venue_conceded = (
        goals_against / played
        if played and goals_against is not None
        else None
    )
    venue_share = settings.share_for(played) if enabled else 0.0

    if venue_scored is None or venue_conceded is None:
        venue_share = 0.0

    scored = (
        venue_scored * venue_share
        + overall_scored_value * (1.0 - venue_share)
        if venue_share > 0 and venue_scored is not None
        else overall_scored_value
    )
    conceded = (
        venue_conceded * venue_share
        + overall_conceded_value * (1.0 - venue_share)
        if venue_share > 0 and venue_conceded is not None
        else overall_conceded_value
    )

    return VenueSideAdjustment(
        scored=scored,
        conceded=conceded,
        venue_scored=venue_scored,
        venue_conceded=venue_conceded,
        matches_played=played,
        venue_share=venue_share,
        applied=venue_share > 0,
    )


def adjust_for_venue(
    home_scored: float,
    home_conceded: float,
    away_scored: float,
    away_conceded: float,
    home_record: Any = None,
    away_record: Any = None,
    enabled: bool = True,
    settings: VenueSettings = DEFAULT_VENUE_SETTINGS,
) -> VenueAdjustment:
    """ホームはホーム実績、アウェイはアウェイ実績を混合する。"""

    home = _adjust_side(
        home_scored,
        home_conceded,
        home_record,
        enabled,
        settings,
    )
    away = _adjust_side(
        away_scored,
        away_conceded,
        away_record,
        enabled,
        settings,
    )

    return VenueAdjustment(
        home=home,
        away=away,
        enabled=bool(enabled),
        applied=bool(home.applied or away.applied),
    )
