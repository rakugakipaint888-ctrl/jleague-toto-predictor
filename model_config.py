"""EloとVersion5モデルの設定値を一元管理する。

汎用的な ``config`` というモジュール名は、Streamlitの再実行時に古い
モジュールが ``sys.modules`` へ残ると新旧設定が混在しやすい。そのため、
アプリ本体はこの固有名のモジュールだけを参照する。
"""

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


# Version7-A: Version6を変えずに重ねる引分専用モデルの初期値。
# 係数0・Poisson重み1.0の初期状態はVersion6の3結果確率を完全再現する。
VERSION7A_MODEL_VERSION = "Version7-A"
VERSION7A_DEFAULT_DRAW_PARAMETERS = {
    "base_draw_logit_bias": 0.0,
    "poisson_draw_weight": 1.0,
    "elo_closeness_weight": 0.0,
    "expected_goal_closeness_weight": 0.0,
    "team_draw_rate_weight": 0.0,
    "recent_draw_rate_weight": 0.0,
    "low_score_weight": 0.0,
    "standing_closeness_weight": 0.0,
    "candidate_threshold": 0.25,
    "candidate_margin": 0.05,
}

# Version7-Aは引分関連だけを小規模探索する。Version7-Bで全体へ拡張する。
VERSION7A_DRAW_SEARCH_SPACE = {
    "base_draw_logit_bias": {"low": -0.50, "high": 0.80, "step": 0.05},
    "poisson_draw_weight": {"low": 0.60, "high": 1.40, "step": 0.05},
    "elo_closeness_weight": {"low": 0.0, "high": 1.20, "step": 0.10},
    "expected_goal_closeness_weight": {
        "low": 0.0,
        "high": 1.50,
        "step": 0.10,
    },
    "team_draw_rate_weight": {"low": 0.0, "high": 1.50, "step": 0.10},
    "recent_draw_rate_weight": {"low": 0.0, "high": 1.00, "step": 0.10},
    "low_score_weight": {"low": 0.0, "high": 1.00, "step": 0.10},
    "standing_closeness_weight": {"low": 0.0, "high": 1.00, "step": 0.10},
    "candidate_threshold": {"choices": (0.20, 0.25, 0.30, 0.35, 0.40)},
    "candidate_margin": {"low": 0.02, "high": 0.15, "step": 0.01},
}
VERSION7A_TRIAL_COUNT_DEFAULT = 30
VERSION7A_TRIAL_COUNT_CHOICES = (10, 30, 50, 100)
VERSION7A_RANDOM_SEED = 20260808
VERSION7A_OVERFIT_SCORE_GAP_THRESHOLD = 10.0
# 引分F1だけを上げるTrialを避けるため、Version6比の許容悪化を小さく固定する。
VERSION7A_OVERALL_BRIER_ALLOWANCE = 0.01
VERSION7A_LOG_LOSS_ALLOWANCE = 0.02
VERSION7A_CALIBRATION_ALLOWANCE = 0.015
VERSION7A_ACCURACY_ALLOWANCE = 0.01
