"""Version4互換値とVersion5の補正順序を一か所で管理する。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from model_config import (
    DEFAULT_ELO_SETTINGS,
    DEFAULT_FORM_SETTINGS,
    DEFAULT_MODEL_SETTINGS,
    DEFAULT_STANDINGS_SETTINGS,
    DEFAULT_VENUE_SETTINGS,
    EloSettings,
    FormSettings,
    ModelSettings,
    StandingsSettings,
    VenueSettings,
)
from elo_rating import adjust_expected_goals
from form_adjuster import FormAdjustment, adjust_team_form
from prediction import (
    calculate_expected_goals,
    calculate_match_probabilities,
    get_toto_prediction,
)
from standings_adjuster import (
    StandingMetrics,
    StandingsAdjustment,
    adjust_expected_goals_by_standings,
)
from venue_adjuster import VenueAdjustment, adjust_for_venue


@dataclass(frozen=True)
class TeamModelInput:
    """1クラブ分の予測入力。欠損値を許容し、モデル内でフォールバックする。"""

    team_name: str = ""
    recent_scored_average: float = 1.2
    recent_conceded_average: float = 1.2
    recent_matches: tuple[Any, ...] = ()
    season_scored_average: Optional[float] = None
    season_conceded_average: Optional[float] = None
    venue_record: Any = None
    rank: Optional[int] = None
    points: Optional[float] = None
    played: Optional[int] = None
    season_draws: Optional[int] = None
    goal_difference: Optional[float] = None
    elo: Optional[float] = None


@dataclass(frozen=True)
class ModelOptions:
    """画面の4スイッチに対応するモデル設定。"""

    use_elo: bool = True
    use_venue: bool = True
    use_recent_weighting: bool = True
    use_standings: bool = True


@dataclass(frozen=True)
class ExpectedGoalsPair:
    home: float
    away: float


@dataclass(frozen=True)
class PredictionSnapshot:
    """Version4またはVersion5の最終予測。"""

    expected_before_elo: ExpectedGoalsPair
    expected_after_elo: ExpectedGoalsPair
    probabilities: dict[str, float]
    prediction: str
    top_probability: float
    elo_adjustment_rate: float
    elo_adjustment_enabled: bool


@dataclass(frozen=True)
class ModelPipelineResult:
    """各補正段階を保持したVersion4・Version5比較結果。"""

    version4: PredictionSnapshot
    version5_probabilities: dict[str, float]
    version5_prediction: str
    version5_top_probability: float
    home_form: FormAdjustment
    away_form: FormAdjustment
    venue: VenueAdjustment
    expected_basic: ExpectedGoalsPair
    expected_after_venue: ExpectedGoalsPair
    expected_after_elo: ExpectedGoalsPair
    expected_after_standings: ExpectedGoalsPair
    expected_final: ExpectedGoalsPair
    standings: StandingsAdjustment
    home_venue_adjustment_rate: float
    away_venue_adjustment_rate: float
    elo_adjustment_rate: float
    elo_adjustment_enabled: bool
    venue_adjustment_enabled: bool
    recent_weighting_enabled: bool
    standings_adjustment_enabled: bool
    fallback_used: bool
    fallback_reason: str = ""

    @property
    def prediction_changed(self) -> bool:
        return self.version4.prediction != self.version5_prediction

    @property
    def maximum_probability_change(self) -> float:
        return self.version5_top_probability - self.version4.top_probability


def _finite_nonnegative(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)

    if not math.isfinite(number) or number < 0:
        return float(fallback)
    return number


def _finite_optional(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None
    return number


def _safe_rate(after: float, before: float) -> float:
    if not math.isfinite(after) or not math.isfinite(before) or before <= 0:
        return 0.0
    return after / before - 1.0


def _pair_is_safe(pair: ExpectedGoalsPair) -> bool:
    return bool(
        math.isfinite(pair.home)
        and math.isfinite(pair.away)
        and pair.home >= 0
        and pair.away >= 0
    )


def _clamp_pair(
    pair: ExpectedGoalsPair,
    settings: ModelSettings,
) -> ExpectedGoalsPair:
    minimum = float(settings.expected_goals_minimum)
    maximum = float(settings.expected_goals_maximum)
    return ExpectedGoalsPair(
        home=max(minimum, min(float(pair.home), maximum)),
        away=max(minimum, min(float(pair.away), maximum)),
    )


def _calculate_pair(
    home_scored: float,
    home_conceded: float,
    away_scored: float,
    away_conceded: float,
    settings: ModelSettings = DEFAULT_MODEL_SETTINGS,
) -> ExpectedGoalsPair:
    home_expected, away_expected = calculate_expected_goals(
        home_scored=home_scored,
        home_conceded=home_conceded,
        away_scored=away_scored,
        away_conceded=away_conceded,
        home_correction=settings.home_correction,
        expected_goals_minimum=settings.expected_goals_minimum,
        expected_goals_maximum=settings.expected_goals_maximum,
    )
    return ExpectedGoalsPair(home_expected, away_expected)


def _apply_elo(
    expected: ExpectedGoalsPair,
    home_elo: Optional[float],
    away_elo: Optional[float],
    requested: bool,
    settings: EloSettings = DEFAULT_ELO_SETTINGS,
) -> tuple[ExpectedGoalsPair, float, bool]:
    normalized_home_elo = _finite_optional(home_elo)
    normalized_away_elo = _finite_optional(away_elo)
    enabled = bool(
        requested
        and normalized_home_elo is not None
        and normalized_away_elo is not None
    )

    if normalized_home_elo is None or normalized_away_elo is None:
        return expected, 0.0, False

    adjustment = adjust_expected_goals(
        home_expected=expected.home,
        away_expected=expected.away,
        home_elo=normalized_home_elo,
        away_elo=normalized_away_elo,
        enabled=enabled,
        settings=settings,
    )
    return (
        ExpectedGoalsPair(adjustment.home_after, adjustment.away_after),
        adjustment.adjustment_rate,
        enabled,
    )


def _prediction_snapshot(
    before_elo: ExpectedGoalsPair,
    after_elo: ExpectedGoalsPair,
    elo_rate: float,
    elo_enabled: bool,
) -> PredictionSnapshot:
    probabilities = calculate_match_probabilities(
        after_elo.home,
        after_elo.away,
    )
    prediction, top_probability = get_toto_prediction(
        probabilities["home_win"],
        probabilities["draw"],
        probabilities["away_win"],
    )
    return PredictionSnapshot(
        expected_before_elo=before_elo,
        expected_after_elo=after_elo,
        probabilities=probabilities,
        prediction=prediction,
        top_probability=top_probability,
        elo_adjustment_rate=elo_rate,
        elo_adjustment_enabled=elo_enabled,
    )


def _standing_metrics(team: TeamModelInput) -> StandingMetrics:
    return StandingMetrics(
        rank=team.rank,
        points=team.points,
        played=team.played,
        goal_difference=team.goal_difference,
    )


def predict_match(
    home: TeamModelInput,
    away: TeamModelInput,
    options: ModelOptions = ModelOptions(),
    form_settings: FormSettings = DEFAULT_FORM_SETTINGS,
    venue_settings: VenueSettings = DEFAULT_VENUE_SETTINGS,
    standings_settings: StandingsSettings = DEFAULT_STANDINGS_SETTINGS,
    model_settings: ModelSettings = DEFAULT_MODEL_SETTINGS,
    elo_settings: EloSettings = DEFAULT_ELO_SETTINGS,
) -> ModelPipelineResult:
    """指定順序で補正し、異常値時はVersion4期待得点へ戻す。"""

    home_regular_scored = _finite_nonnegative(
        home.recent_scored_average,
        1.4,
    )
    home_regular_conceded = _finite_nonnegative(
        home.recent_conceded_average,
        1.2,
    )
    away_regular_scored = _finite_nonnegative(
        away.recent_scored_average,
        1.2,
    )
    away_regular_conceded = _finite_nonnegative(
        away.recent_conceded_average,
        1.4,
    )

    # Version4相当値は常に先に保持し、比較とフォールバックへ使う。
    version4_before_elo = _calculate_pair(
        home_regular_scored,
        home_regular_conceded,
        away_regular_scored,
        away_regular_conceded,
        model_settings,
    )
    version4_after_elo, version4_elo_rate, version4_elo_enabled = _apply_elo(
        version4_before_elo,
        home.elo,
        away.elo,
        options.use_elo,
        elo_settings,
    )
    version4 = _prediction_snapshot(
        version4_before_elo,
        version4_after_elo,
        version4_elo_rate,
        version4_elo_enabled,
    )

    fallback_used = False
    fallback_reason = ""

    try:
        # 1. 全体（シーズン）平均 + 2. 直近5試合の加重平均。
        home_form = adjust_team_form(
            home_regular_scored,
            home_regular_conceded,
            home.season_scored_average,
            home.season_conceded_average,
            home.recent_matches,
            enabled=options.use_recent_weighting,
            settings=form_settings,
        )
        away_form = adjust_team_form(
            away_regular_scored,
            away_regular_conceded,
            away.season_scored_average,
            away.season_conceded_average,
            away.recent_matches,
            enabled=options.use_recent_weighting,
            settings=form_settings,
        )
        expected_basic = _calculate_pair(
            home_form.scored,
            home_form.conceded,
            away_form.scored,
            away_form.conceded,
            model_settings,
        )

        # 3. 会場別成績との混合 + 4. 基本期待得点。
        venue = adjust_for_venue(
            home_form.scored,
            home_form.conceded,
            away_form.scored,
            away_form.conceded,
            home_record=home.venue_record,
            away_record=away.venue_record,
            enabled=options.use_venue,
            settings=venue_settings,
        )
        expected_after_venue = _calculate_pair(
            venue.home.scored,
            venue.home.conceded,
            venue.away.scored,
            venue.away.conceded,
            model_settings,
        )

        # 5. Elo補正。
        expected_after_elo, elo_rate, elo_enabled = _apply_elo(
            expected_after_venue,
            home.elo,
            away.elo,
            options.use_elo,
            elo_settings,
        )

        # 6. 順位・勝点・得失点差補正。
        standings = adjust_expected_goals_by_standings(
            expected_after_elo.home,
            expected_after_elo.away,
            _standing_metrics(home),
            _standing_metrics(away),
            enabled=options.use_standings,
            settings=standings_settings,
        )
        expected_after_standings = ExpectedGoalsPair(
            standings.home_after,
            standings.away_after,
        )

        # 7. 異常値はVersion4へ戻し、正常値は設定範囲へ制限する。
        if not all(
            _pair_is_safe(pair)
            for pair in (
                expected_basic,
                expected_after_venue,
                expected_after_elo,
                expected_after_standings,
            )
        ):
            fallback_used = True
            fallback_reason = "Version5の期待得点が不正なためVersion4へ戻しました。"
            expected_final = _clamp_pair(version4_after_elo, model_settings)
        else:
            expected_final = _clamp_pair(
                expected_after_standings,
                model_settings,
            )

        version5_data_available = bool(
            home_form.applied
            or away_form.applied
            or venue.applied
            or standings.data_available
        )
        if (
            not version5_data_available
            and (
                options.use_recent_weighting
                or options.use_venue
                or options.use_standings
            )
        ):
            fallback_used = True
            fallback_reason = "Version5用データがないためVersion4を使用しました。"
            expected_final = _clamp_pair(version4_after_elo, model_settings)

    except Exception:
        # 想定外の欠損形式もUIへ技術例外を出さずVersion4へ戻す。
        fallback_used = True
        fallback_reason = "Version5の補正を適用できないためVersion4を使用しました。"
        home_form = adjust_team_form(
            home_regular_scored,
            home_regular_conceded,
            None,
            None,
            (),
            enabled=False,
            settings=form_settings,
        )
        away_form = adjust_team_form(
            away_regular_scored,
            away_regular_conceded,
            None,
            None,
            (),
            enabled=False,
            settings=form_settings,
        )
        venue = adjust_for_venue(
            home_form.scored,
            home_form.conceded,
            away_form.scored,
            away_form.conceded,
            enabled=False,
            settings=venue_settings,
        )
        expected_basic = version4_before_elo
        expected_after_venue = version4_before_elo
        expected_after_elo = version4_after_elo
        standings = adjust_expected_goals_by_standings(
            expected_after_elo.home,
            expected_after_elo.away,
            StandingMetrics(),
            StandingMetrics(),
            enabled=False,
            settings=standings_settings,
        )
        expected_after_standings = expected_after_elo
        expected_final = _clamp_pair(version4_after_elo, model_settings)
        elo_rate = version4_elo_rate
        elo_enabled = version4_elo_enabled

    # 8. 最終期待得点だけをポアソン分布へ渡す。
    version5_probabilities = calculate_match_probabilities(
        expected_final.home,
        expected_final.away,
    )
    version5_prediction, version5_top_probability = get_toto_prediction(
        version5_probabilities["home_win"],
        version5_probabilities["draw"],
        version5_probabilities["away_win"],
    )

    return ModelPipelineResult(
        version4=version4,
        version5_probabilities=version5_probabilities,
        version5_prediction=version5_prediction,
        version5_top_probability=version5_top_probability,
        home_form=home_form,
        away_form=away_form,
        venue=venue,
        expected_basic=expected_basic,
        expected_after_venue=expected_after_venue,
        expected_after_elo=expected_after_elo,
        expected_after_standings=expected_after_standings,
        expected_final=expected_final,
        standings=standings,
        home_venue_adjustment_rate=_safe_rate(
            expected_after_venue.home,
            expected_basic.home,
        ),
        away_venue_adjustment_rate=_safe_rate(
            expected_after_venue.away,
            expected_basic.away,
        ),
        elo_adjustment_rate=elo_rate,
        elo_adjustment_enabled=elo_enabled,
        venue_adjustment_enabled=bool(options.use_venue and venue.applied),
        recent_weighting_enabled=bool(
            options.use_recent_weighting
            and (home_form.applied or away_form.applied)
        ),
        standings_adjustment_enabled=bool(
            options.use_standings and standings.data_available
        ),
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
    )
