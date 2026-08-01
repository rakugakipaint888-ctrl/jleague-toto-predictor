"""勝点と得失点差の1試合平均から期待得点を緩やかに補正する。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from config import DEFAULT_STANDINGS_SETTINGS, StandingsSettings


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None
    return number


def _clamp(value: float, limit: float) -> float:
    safe_limit = abs(float(limit))
    return max(-safe_limit, min(float(value), safe_limit))


@dataclass(frozen=True)
class StandingMetrics:
    """補正に必要な順位表のクラブ別指標。"""

    rank: Optional[int] = None
    points: Optional[float] = None
    played: Optional[int] = None
    goal_difference: Optional[float] = None

    @property
    def points_per_match(self) -> Optional[float]:
        played = _finite(self.played)
        points = _finite(self.points)
        if played is None or played <= 0 or points is None:
            return None
        return points / played

    @property
    def goal_difference_per_match(self) -> Optional[float]:
        played = _finite(self.played)
        goal_difference = _finite(self.goal_difference)
        if played is None or played <= 0 or goal_difference is None:
            return None
        return goal_difference / played


@dataclass(frozen=True)
class StandingsAdjustment:
    """順位表補正前後と補正率。率はホーム側を正とする。"""

    home_before: float
    away_before: float
    home_after: float
    away_after: float
    points_adjustment_rate: float
    goal_difference_adjustment_rate: float
    total_adjustment_rate: float
    home_points_per_match: Optional[float]
    away_points_per_match: Optional[float]
    home_goal_difference_per_match: Optional[float]
    away_goal_difference_per_match: Optional[float]
    enabled: bool
    data_available: bool
    applied: bool


def adjust_expected_goals_by_standings(
    home_expected: float,
    away_expected: float,
    home: StandingMetrics,
    away: StandingMetrics,
    enabled: bool = True,
    settings: StandingsSettings = DEFAULT_STANDINGS_SETTINGS,
) -> StandingsAdjustment:
    """Elo補正後へ勝点差最大5%、得失点差最大3%を加える。"""

    home_ppm = home.points_per_match
    away_ppm = away.points_per_match
    home_gdpm = home.goal_difference_per_match
    away_gdpm = away.goal_difference_per_match

    points_rate = 0.0
    goal_difference_rate = 0.0

    if enabled and home_ppm is not None and away_ppm is not None:
        points_rate = _clamp(
            (home_ppm - away_ppm) * settings.points_change_per_unit,
            settings.points_max_adjustment,
        )

    if enabled and home_gdpm is not None and away_gdpm is not None:
        goal_difference_rate = _clamp(
            (home_gdpm - away_gdpm)
            * settings.goal_difference_change_per_unit,
            settings.goal_difference_max_adjustment,
        )

    data_available = bool(
        (home_ppm is not None and away_ppm is not None)
        or (home_gdpm is not None and away_gdpm is not None)
    )
    total_rate = _clamp(
        points_rate + goal_difference_rate,
        settings.total_max_adjustment,
    )
    applied = bool(enabled and data_available)

    return StandingsAdjustment(
        home_before=float(home_expected),
        away_before=float(away_expected),
        home_after=float(home_expected) * (1.0 + total_rate),
        away_after=float(away_expected) * (1.0 - total_rate),
        points_adjustment_rate=points_rate,
        goal_difference_adjustment_rate=goal_difference_rate,
        total_adjustment_rate=total_rate,
        home_points_per_match=home_ppm,
        away_points_per_match=away_ppm,
        home_goal_difference_per_match=home_gdpm,
        away_goal_difference_per_match=away_gdpm,
        enabled=bool(enabled),
        data_available=data_available,
        applied=applied,
    )
