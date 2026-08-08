"""Version7-Aの引分確率補正と引分候補判定。

Version6のPoisson 3結果確率を入力とし、引分だけを独立した二値事象として
log-odds上で補正する。ホーム勝ちとアウェイ勝ちの相対比は維持するため、
Version1～6の勝敗方向ロジックは変更しない。
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

from data_loader import JAPAN_TIMEZONE, OfficialMatch
from model_config import VERSION7A_DEFAULT_DRAW_PARAMETERS
from prediction import poisson_probability


TOTO_OUTCOMES = ("1", "0", "2")
PROBABILITY_EPSILON = 1e-9


@dataclass(frozen=True)
class DrawSettings:
    """Version7-Aで探索・採用する引分専用パラメータ。"""

    base_draw_logit_bias: float = 0.0
    poisson_draw_weight: float = 1.0
    elo_closeness_weight: float = 0.0
    expected_goal_closeness_weight: float = 0.0
    team_draw_rate_weight: float = 0.0
    recent_draw_rate_weight: float = 0.0
    low_score_weight: float = 0.0
    standing_closeness_weight: float = 0.0
    candidate_threshold: float = 0.25
    candidate_margin: float = 0.05

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "DrawSettings":
        settings = cls(
            **{
                field_name: float(values[field_name])
                for field_name in cls.__dataclass_fields__
            }
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        values = asdict(self)
        if any(not math.isfinite(float(value)) for value in values.values()):
            raise ValueError("引分設定は有限値にしてください。")
        if self.poisson_draw_weight < 0:
            raise ValueError("Poisson引分確率の重みは0以上にしてください。")
        for field_name in (
            "elo_closeness_weight",
            "expected_goal_closeness_weight",
            "team_draw_rate_weight",
            "recent_draw_rate_weight",
            "low_score_weight",
            "standing_closeness_weight",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name}は0以上にしてください。")
        if not 0.0 <= self.candidate_threshold <= 1.0:
            raise ValueError("引分候補閾値は0～1にしてください。")
        if not 0.0 <= self.candidate_margin <= 1.0:
            raise ValueError("引分候補の確率差は0～1にしてください。")

    def as_dict(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}


DEFAULT_DRAW_SETTINGS = DrawSettings.from_mapping(
    VERSION7A_DEFAULT_DRAW_PARAMETERS
)


@dataclass(frozen=True)
class DrawContext:
    """対象試合より前の確定試合だけから作るリーグ・シーズン統計。"""

    historical_match_count: int = 0
    season_match_count: int = 0
    category: str = ""
    league_draw_rate: Optional[float] = None
    season_draw_rate: Optional[float] = None
    zero_zero_rate: Optional[float] = None
    one_one_rate: Optional[float] = None
    low_score_rate: Optional[float] = None
    league_goals_per_team: Optional[float] = None
    season_goals_per_team: Optional[float] = None

    @property
    def reference_draw_rate(self) -> Optional[float]:
        available = [
            value
            for value in (self.league_draw_rate, self.season_draw_rate)
            if value is not None and math.isfinite(value)
        ]
        return sum(available) / len(available) if available else None

    @property
    def reference_goals_per_team(self) -> Optional[float]:
        available = [
            value
            for value in (
                self.league_goals_per_team,
                self.season_goals_per_team,
            )
            if value is not None and math.isfinite(value)
        ]
        return sum(available) / len(available) if available else None


@dataclass(frozen=True)
class DrawFeatures:
    poisson_draw_probability: float
    expected_goal_difference: float
    elo_difference: Optional[float]
    home_scored_average: Optional[float]
    home_conceded_average: Optional[float]
    away_scored_average: Optional[float]
    away_conceded_average: Optional[float]
    home_season_scored_average: Optional[float]
    home_season_conceded_average: Optional[float]
    away_season_scored_average: Optional[float]
    away_season_conceded_average: Optional[float]
    team_draw_rate: Optional[float]
    recent_draw_rate: Optional[float]
    low_score_probability: float
    zero_zero_probability: float
    one_one_probability: float
    expected_goal_closeness: float
    elo_closeness: Optional[float]
    standing_closeness: Optional[float]


@dataclass(frozen=True)
class DrawPrediction:
    """正規化済み3結果確率、本命、引分候補と計算根拠。"""

    probabilities: Mapping[str, float]
    prediction: str
    top_probability: float
    is_draw_candidate: bool
    candidate_reasons: tuple[str, ...]
    features: DrawFeatures
    poisson_draw_probability: float
    adjusted_draw_probability: float


def _finite_probability(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number


def normalize_three_way_probabilities(
    probabilities: Mapping[str, Any],
) -> dict[str, float]:
    """1・0・2を有限、非負、合計1.0へ正規化する。"""

    aliases = {
        "1": ("1", "home_win"),
        "0": ("0", "draw"),
        "2": ("2", "away_win"),
    }
    values = {}
    for outcome, keys in aliases.items():
        value = 0.0
        for key in keys:
            if key in probabilities:
                value = _finite_probability(probabilities.get(key))
                break
        values[outcome] = value
    total = sum(values.values())
    if total <= 0:
        return {outcome: 1.0 / 3.0 for outcome in TOTO_OUTCOMES}
    normalized = {outcome: values[outcome] / total for outcome in TOTO_OUTCOMES}
    # 浮動小数の丸め差は最後のクラスで吸収し、合計を厳密に1へ寄せる。
    normalized["2"] = max(0.0, 1.0 - normalized["1"] - normalized["0"])
    corrected_total = sum(normalized.values())
    return {
        outcome: normalized[outcome] / corrected_total
        for outcome in TOTO_OUTCOMES
    }


def probability_percentages(
    probabilities: Mapping[str, Any],
    *,
    digits: int = 1,
) -> dict[str, float]:
    """表示値も非負・100%合計になるよう最大剰余法で丸める。"""

    decimal_places = max(0, int(digits))
    unit_scale = 10 ** decimal_places
    total_units = 100 * unit_scale
    normalized = normalize_three_way_probabilities(probabilities)
    raw_units = {
        outcome: normalized[outcome] * total_units
        for outcome in TOTO_OUTCOMES
    }
    allocated = {
        outcome: math.floor(raw_units[outcome])
        for outcome in TOTO_OUTCOMES
    }
    remaining = total_units - sum(allocated.values())
    order = sorted(
        TOTO_OUTCOMES,
        key=lambda outcome: (
            raw_units[outcome] - allocated[outcome],
            -TOTO_OUTCOMES.index(outcome),
        ),
        reverse=True,
    )
    for index in range(remaining):
        allocated[order[index % len(order)]] += 1
    return {
        outcome: allocated[outcome] / unit_scale
        for outcome in TOTO_OUTCOMES
    }


def _as_local_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=JAPAN_TIMEZONE)
    return value.astimezone(JAPAN_TIMEZONE)


def _season_start(cutoff_at: datetime) -> date:
    local_date = _as_local_time(cutoff_at).date()
    # 2026/27特別大会以後は7月開始。それ以前は暦年シーズン。
    if local_date >= date(2026, 7, 1):
        start_year = local_date.year if local_date.month >= 7 else local_date.year - 1
        return date(start_year, 7, 1)
    return date(local_date.year, 1, 1)


def _category_name(value: Any) -> str:
    return str(value or "").split("/")[0].strip()


def _rate(matches: Sequence[OfficialMatch], predicate) -> Optional[float]:
    if not matches:
        return None
    return sum(bool(predicate(match)) for match in matches) / len(matches)


def _goals_per_team(matches: Sequence[OfficialMatch]) -> Optional[float]:
    if not matches:
        return None
    total_goals = sum(
        int(match.home_goals) + int(match.away_goals)
        for match in matches
    )
    return total_goals / (2.0 * len(matches))


def build_draw_context(
    matches: Sequence[OfficialMatch],
    cutoff_at: datetime,
    *,
    category: str = "",
) -> DrawContext:
    """cutoffより前の確定結果だけで引分・スコア傾向を集計する。"""

    cutoff = _as_local_time(cutoff_at)
    completed = [
        match
        for match in matches
        if match.is_completed and _as_local_time(match.match_time) < cutoff
    ]
    requested_category = _category_name(category)
    category_matches = [
        match
        for match in completed
        if _category_name(match.category) == requested_category
    ]
    # 少数リーグ標本を無理に使わず、30件未満ならJ1～J3全体へ戻す。
    league_matches = (
        category_matches
        if requested_category and len(category_matches) >= 30
        else completed
    )
    start_date = _season_start(cutoff)
    season_matches = [
        match
        for match in league_matches
        if _as_local_time(match.match_time).date() >= start_date
    ]
    score_source = season_matches or league_matches

    return DrawContext(
        historical_match_count=len(league_matches),
        season_match_count=len(season_matches),
        category=(requested_category if league_matches is category_matches else "J1-J3"),
        league_draw_rate=_rate(
            league_matches,
            lambda match: match.home_goals == match.away_goals,
        ),
        season_draw_rate=_rate(
            season_matches,
            lambda match: match.home_goals == match.away_goals,
        ),
        zero_zero_rate=_rate(
            score_source,
            lambda match: match.home_goals == 0 and match.away_goals == 0,
        ),
        one_one_rate=_rate(
            score_source,
            lambda match: match.home_goals == 1 and match.away_goals == 1,
        ),
        low_score_rate=_rate(
            score_source,
            lambda match: int(match.home_goals) + int(match.away_goals) <= 2,
        ),
        league_goals_per_team=_goals_per_team(league_matches),
        season_goals_per_team=_goals_per_team(season_matches),
    )


def _safe_optional(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _team_draw_rate(team: Any) -> Optional[float]:
    values = []
    played = _safe_optional(getattr(team, "played", None))
    draws = _safe_optional(getattr(team, "season_draws", None))
    if played is not None and draws is not None and played > 0:
        values.append(max(0.0, min(1.0, draws / played)))
    venue_record = getattr(team, "venue_record", None)
    venue_played = _safe_optional(getattr(venue_record, "played", None))
    venue_draws = _safe_optional(getattr(venue_record, "draws", None))
    if venue_played is not None and venue_draws is not None and venue_played > 0:
        values.append(max(0.0, min(1.0, venue_draws / venue_played)))
    return sum(values) / len(values) if values else None


def _recent_result_is_draw(item: Any) -> Optional[bool]:
    scored = None
    conceded = None
    if isinstance(item, Mapping):
        scored = item.get("scored")
        conceded = item.get("conceded")
        result = str(item.get("result", ""))
    else:
        scored = getattr(item, "scored", None)
        conceded = getattr(item, "conceded", None)
        result = str(getattr(item, "result", ""))
    scored_number = _safe_optional(scored)
    conceded_number = _safe_optional(conceded)
    if scored_number is not None and conceded_number is not None:
        return scored_number == conceded_number
    if result:
        if result in ("分", "D", "draw", "Draw"):
            return True
        if result in ("勝", "敗", "W", "L", "win", "loss"):
            return False
    score_match = re.search(r"(\d+)\s*[-−]\s*(\d+)", str(item))
    if score_match:
        return int(score_match.group(1)) == int(score_match.group(2))
    return None


def _recent_draw_rate(team: Any) -> Optional[float]:
    results = [
        result
        for result in (
            _recent_result_is_draw(item)
            for item in tuple(getattr(team, "recent_matches", ()) or ())[:5]
        )
        if result is not None
    ]
    return sum(results) / len(results) if results else None


def _mean_optional(values: Sequence[Optional[float]]) -> Optional[float]:
    available = [value for value in values if value is not None and math.isfinite(value)]
    return sum(available) / len(available) if available else None


def _closeness(difference: float, scale: float) -> float:
    return math.exp(-abs(float(difference)) / max(PROBABILITY_EPSILON, scale))


def _standing_closeness(home: Any, away: Any) -> Optional[float]:
    signals = []
    home_rank = _safe_optional(getattr(home, "rank", None))
    away_rank = _safe_optional(getattr(away, "rank", None))
    if home_rank is not None and away_rank is not None:
        signals.append(_closeness(home_rank - away_rank, 6.0))

    home_played = _safe_optional(getattr(home, "played", None))
    away_played = _safe_optional(getattr(away, "played", None))
    home_points = _safe_optional(getattr(home, "points", None))
    away_points = _safe_optional(getattr(away, "points", None))
    if (
        home_played is not None
        and away_played is not None
        and home_points is not None
        and away_points is not None
        and home_played > 0
        and away_played > 0
    ):
        signals.append(
            _closeness(home_points / home_played - away_points / away_played, 0.75)
        )

    home_goal_difference = _safe_optional(getattr(home, "goal_difference", None))
    away_goal_difference = _safe_optional(getattr(away, "goal_difference", None))
    if (
        home_played is not None
        and away_played is not None
        and home_goal_difference is not None
        and away_goal_difference is not None
        and home_played > 0
        and away_played > 0
    ):
        signals.append(
            _closeness(
                home_goal_difference / home_played
                - away_goal_difference / away_played,
                1.0,
            )
        )
    return sum(signals) / len(signals) if signals else None


def extract_draw_features(
    base_probabilities: Mapping[str, Any],
    home_expected_goals: float,
    away_expected_goals: float,
    home: Any,
    away: Any,
) -> DrawFeatures:
    """既存入力から推測を足さず、安全に算出できる引分要素だけを返す。"""

    base = normalize_three_way_probabilities(base_probabilities)
    home_expected = max(0.0, _safe_optional(home_expected_goals) or 0.0)
    away_expected = max(0.0, _safe_optional(away_expected_goals) or 0.0)
    zero_zero = poisson_probability(0, home_expected) * poisson_probability(
        0, away_expected
    )
    one_one = poisson_probability(1, home_expected) * poisson_probability(
        1, away_expected
    )
    low_score = sum(
        poisson_probability(home_goals, home_expected)
        * poisson_probability(away_goals, away_expected)
        for home_goals in range(3)
        for away_goals in range(3 - home_goals)
    )
    home_elo = _safe_optional(getattr(home, "elo", None))
    away_elo = _safe_optional(getattr(away, "elo", None))
    elo_difference = (
        abs(home_elo - away_elo)
        if home_elo is not None and away_elo is not None
        else None
    )

    return DrawFeatures(
        poisson_draw_probability=base["0"],
        expected_goal_difference=abs(home_expected - away_expected),
        elo_difference=elo_difference,
        home_scored_average=_safe_optional(
            getattr(home, "recent_scored_average", None)
        ),
        home_conceded_average=_safe_optional(
            getattr(home, "recent_conceded_average", None)
        ),
        away_scored_average=_safe_optional(
            getattr(away, "recent_scored_average", None)
        ),
        away_conceded_average=_safe_optional(
            getattr(away, "recent_conceded_average", None)
        ),
        home_season_scored_average=_safe_optional(
            getattr(home, "season_scored_average", None)
        ),
        home_season_conceded_average=_safe_optional(
            getattr(home, "season_conceded_average", None)
        ),
        away_season_scored_average=_safe_optional(
            getattr(away, "season_scored_average", None)
        ),
        away_season_conceded_average=_safe_optional(
            getattr(away, "season_conceded_average", None)
        ),
        team_draw_rate=_mean_optional(
            (_team_draw_rate(home), _team_draw_rate(away))
        ),
        recent_draw_rate=_mean_optional(
            (_recent_draw_rate(home), _recent_draw_rate(away))
        ),
        low_score_probability=max(0.0, min(1.0, low_score)),
        zero_zero_probability=max(0.0, min(1.0, zero_zero)),
        one_one_probability=max(0.0, min(1.0, one_one)),
        expected_goal_closeness=_closeness(home_expected - away_expected, 0.75),
        elo_closeness=(
            _closeness(elo_difference, 150.0)
            if elo_difference is not None
            else None
        ),
        standing_closeness=_standing_closeness(home, away),
    )


def _logit(probability: float) -> float:
    safe = min(1.0 - PROBABILITY_EPSILON, max(PROBABILITY_EPSILON, probability))
    return math.log(safe / (1.0 - safe))


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-min(value, 700.0))
        return 1.0 / (1.0 + exponential)
    exponential = math.exp(max(value, -700.0))
    return exponential / (1.0 + exponential)


def _signed_closeness(value: Optional[float]) -> float:
    return 0.0 if value is None else max(-1.0, min(1.0, value * 2.0 - 1.0))


def _rate_signal(value: Optional[float], reference: Optional[float]) -> float:
    if value is None or reference is None:
        return 0.0
    return max(-1.0, min(1.0, (value - reference) / 0.15))


def _low_score_signal(features: DrawFeatures, context: DrawContext) -> float:
    values = []
    if context.low_score_rate is not None:
        values.append(
            max(
                -1.0,
                min(1.0, (features.low_score_probability - context.low_score_rate) / 0.20),
            )
        )
    historical_equal_score_rate = None
    if context.zero_zero_rate is not None and context.one_one_rate is not None:
        historical_equal_score_rate = context.zero_zero_rate + context.one_one_rate
    if historical_equal_score_rate is not None:
        values.append(
            max(
                -1.0,
                min(
                    1.0,
                    (
                        features.zero_zero_probability
                        + features.one_one_probability
                        - historical_equal_score_rate
                    )
                    / 0.15,
                ),
            )
        )
    reference_goals = context.reference_goals_per_team
    observed_goal_averages = [
        value
        for value in (
            features.home_scored_average,
            features.home_conceded_average,
            features.away_scored_average,
            features.away_conceded_average,
            features.home_season_scored_average,
            features.home_season_conceded_average,
            features.away_season_scored_average,
            features.away_season_conceded_average,
        )
        if value is not None and math.isfinite(value)
    ]
    if reference_goals is not None and observed_goal_averages:
        observed_goals = sum(observed_goal_averages) / len(observed_goal_averages)
        # 両チームの得点・失点平均が当時のリーグ平均より低いほど正方向。
        values.append(
            max(-1.0, min(1.0, (reference_goals - observed_goals) / 0.75))
        )
    return sum(values) / len(values) if values else 0.0


def predict_draw_aware(
    base_probabilities: Mapping[str, Any],
    home_expected_goals: float,
    away_expected_goals: float,
    home: Any,
    away: Any,
    *,
    context: DrawContext = DrawContext(),
    settings: DrawSettings = DEFAULT_DRAW_SETTINGS,
) -> DrawPrediction:
    """Poisson引分確率へ連続量の補正を掛け、3結果へ戻す。"""

    settings.validate()
    base = normalize_three_way_probabilities(base_probabilities)
    features = extract_draw_features(
        base,
        home_expected_goals,
        away_expected_goals,
        home,
        away,
    )
    reference_draw_rate = context.reference_draw_rate
    reference_probability = (
        reference_draw_rate
        if reference_draw_rate is not None
        else features.poisson_draw_probability
    )
    draw_logit = (
        settings.poisson_draw_weight * _logit(features.poisson_draw_probability)
        + (1.0 - settings.poisson_draw_weight) * _logit(reference_probability)
        + settings.base_draw_logit_bias
        + settings.elo_closeness_weight
        * _signed_closeness(features.elo_closeness)
        + settings.expected_goal_closeness_weight
        * _signed_closeness(features.expected_goal_closeness)
        + settings.team_draw_rate_weight
        * _rate_signal(features.team_draw_rate, reference_draw_rate)
        + settings.recent_draw_rate_weight
        * _rate_signal(features.recent_draw_rate, reference_draw_rate)
        + settings.low_score_weight * _low_score_signal(features, context)
        + settings.standing_closeness_weight
        * _signed_closeness(features.standing_closeness)
    )
    adjusted_draw = min(
        1.0 - PROBABILITY_EPSILON,
        max(PROBABILITY_EPSILON, _sigmoid(draw_logit)),
    )
    decisive_total = base["1"] + base["2"]
    if decisive_total <= 0:
        home_share = away_share = 0.5
    else:
        home_share = base["1"] / decisive_total
        away_share = base["2"] / decisive_total
    probabilities = normalize_three_way_probabilities(
        {
            "1": (1.0 - adjusted_draw) * home_share,
            "0": adjusted_draw,
            "2": (1.0 - adjusted_draw) * away_share,
        }
    )
    prediction = max(TOTO_OUTCOMES, key=lambda outcome: probabilities[outcome])
    top_probability = probabilities[prediction]
    difference_from_top = top_probability - probabilities["0"]
    reasons = []
    if prediction == "0":
        reasons.append("引分確率が3結果中最大")
    if probabilities["0"] >= settings.candidate_threshold:
        reasons.append("引分確率が設定閾値以上")
    if (
        difference_from_top <= settings.candidate_margin
        and probabilities["0"] >= min(0.20, settings.candidate_threshold)
    ):
        reasons.append("1位確率との差が設定範囲内")

    return DrawPrediction(
        probabilities=probabilities,
        prediction=prediction,
        top_probability=top_probability,
        is_draw_candidate=bool(reasons),
        candidate_reasons=tuple(reasons),
        features=features,
        poisson_draw_probability=features.poisson_draw_probability,
        adjusted_draw_probability=probabilities["0"],
    )
