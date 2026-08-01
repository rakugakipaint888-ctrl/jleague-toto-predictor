"""EloとVersion5モデルの設定値を一元管理する。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


# Elo計算用の定数はチーム情報から分離し、このファイルだけで管理する。
INITIAL_ELO = 1500.0
CATEGORY_BONUS = {
    "J1": 0.0,
    "J2": -50.0,
    "J3": -100.0,
}
LEAGUE_INITIAL_ELO = {
    category: INITIAL_ELO + bonus
    for category, bonus in CATEGORY_BONUS.items()
}
K_FACTOR = 20.0
HOME_ADVANTAGE = 65.0
GOAL_DIFFERENCE_ADJUSTMENT_ENABLED = True
GOAL_DIFFERENCE_MULTIPLIERS = (
    (1, 1.00),
    (2, 1.25),
    (3, 1.50),
    (4, 1.75),
)
ELO_EXPECTED_GOALS_CHANGE_PER_100 = 0.05
ELO_EXPECTED_GOALS_MAX_ADJUSTMENT = 0.15
ELO_ADJUSTMENT_STRENGTH = 1.0

# Version5: 直近成績、会場別成績、順位表補正、期待得点の安全制御。
RECENT_MATCH_WEIGHTS = (5.0, 4.0, 3.0, 2.0, 1.0)
RECENT_WEIGHTED_SHARE = 0.60
SEASON_AVERAGE_SHARE = 0.40

VENUE_SHARE_5_PLUS = 0.70
VENUE_SHARE_4 = 0.60
VENUE_SHARE_1_TO_3 = 0.40

POINTS_PER_MATCH_CHANGE_PER_UNIT = 0.05
POINTS_PER_MATCH_MAX_ADJUSTMENT = 0.05
GOAL_DIFFERENCE_PER_MATCH_CHANGE_PER_UNIT = 0.03
GOAL_DIFFERENCE_PER_MATCH_MAX_ADJUSTMENT = 0.03
STANDINGS_TOTAL_MAX_ADJUSTMENT = 0.08

EXPECTED_GOALS_MINIMUM = 0.15
EXPECTED_GOALS_MAXIMUM = 4.00


@dataclass(frozen=True)
class EloSettings:
    """Elo更新と期待得点補正に使う変更可能な設定値。"""

    default_initial_rating: float = INITIAL_ELO
    use_category_initial_ratings: bool = True
    j1_initial_rating: float = LEAGUE_INITIAL_ELO["J1"]
    j2_initial_rating: float = LEAGUE_INITIAL_ELO["J2"]
    j3_initial_rating: float = LEAGUE_INITIAL_ELO["J3"]
    k_factor: float = K_FACTOR
    home_advantage: float = HOME_ADVANTAGE
    goal_difference_adjustment_enabled: bool = (
        GOAL_DIFFERENCE_ADJUSTMENT_ENABLED
    )
    goal_difference_multipliers: tuple[tuple[int, float], ...] = (
        GOAL_DIFFERENCE_MULTIPLIERS
    )
    expected_goals_change_per_100_elo: float = (
        ELO_EXPECTED_GOALS_CHANGE_PER_100
    )
    expected_goals_max_adjustment: float = (
        ELO_EXPECTED_GOALS_MAX_ADJUSTMENT
    )
    elo_adjustment_strength: float = ELO_ADJUSTMENT_STRENGTH

    def initial_rating_for(self, category: str | None) -> float:
        """カテゴリーに応じた初期Eloを返す。"""

        if not self.use_category_initial_ratings:
            return self.default_initial_rating

        return {
            "J1": self.j1_initial_rating,
            "J2": self.j2_initial_rating,
            "J3": self.j3_initial_rating,
        }.get(category, self.default_initial_rating)

    def as_serializable_dict(self) -> dict[str, Any]:
        """キャッシュの設定一致判定に使う辞書を返す。"""

        return asdict(self)


DEFAULT_ELO_SETTINGS = EloSettings()


@dataclass(frozen=True)
class FormSettings:
    """直近成績の時系列重みとシーズン平均との混合割合。"""

    recent_match_weights: tuple[float, ...] = RECENT_MATCH_WEIGHTS
    recent_weighted_share: float = RECENT_WEIGHTED_SHARE
    season_average_share: float = SEASON_AVERAGE_SHARE


@dataclass(frozen=True)
class VenueSettings:
    """ホーム・アウェイ別平均を混合する試合数別の割合。"""

    five_plus_share: float = VENUE_SHARE_5_PLUS
    four_match_share: float = VENUE_SHARE_4
    one_to_three_share: float = VENUE_SHARE_1_TO_3

    def share_for(self, matches_played: int) -> float:
        played = max(0, int(matches_played))

        if played >= 5:
            return self.five_plus_share
        if played == 4:
            return self.four_match_share
        if played >= 1:
            return self.one_to_three_share
        return 0.0


@dataclass(frozen=True)
class StandingsSettings:
    """1試合平均勝点・得失点差による緩やかな補正設定。"""

    points_change_per_unit: float = POINTS_PER_MATCH_CHANGE_PER_UNIT
    points_max_adjustment: float = POINTS_PER_MATCH_MAX_ADJUSTMENT
    goal_difference_change_per_unit: float = (
        GOAL_DIFFERENCE_PER_MATCH_CHANGE_PER_UNIT
    )
    goal_difference_max_adjustment: float = (
        GOAL_DIFFERENCE_PER_MATCH_MAX_ADJUSTMENT
    )
    total_max_adjustment: float = STANDINGS_TOTAL_MAX_ADJUSTMENT


@dataclass(frozen=True)
class ModelSettings:
    """最終期待得点の安全範囲。"""

    expected_goals_minimum: float = EXPECTED_GOALS_MINIMUM
    expected_goals_maximum: float = EXPECTED_GOALS_MAXIMUM


DEFAULT_FORM_SETTINGS = FormSettings()
DEFAULT_VENUE_SETTINGS = VenueSettings()
DEFAULT_STANDINGS_SETTINGS = StandingsSettings()
DEFAULT_MODEL_SETTINGS = ModelSettings()

# 公式試合結果キャッシュは6時間を有効期限とし、取得失敗時だけ7日まで利用する。
OFFICIAL_RESULTS_CACHE_TTL_SECONDS = 6 * 60 * 60
OFFICIAL_RESULTS_CACHE_MAX_STALE_SECONDS = 7 * 24 * 60 * 60

# ファイル形式を変更した場合は値を上げ、古いキャッシュを安全に破棄する。
OFFICIAL_RESULTS_CACHE_VERSION = 2
ELO_CACHE_VERSION = 1
