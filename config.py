"""Version4のElo設定を一元管理する。"""

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

# 公式試合結果キャッシュは6時間を有効期限とし、取得失敗時だけ7日まで利用する。
OFFICIAL_RESULTS_CACHE_TTL_SECONDS = 6 * 60 * 60
OFFICIAL_RESULTS_CACHE_MAX_STALE_SECONDS = 7 * 24 * 60 * 60

# ファイル形式を変更した場合は値を上げ、古いキャッシュを安全に破棄する。
OFFICIAL_RESULTS_CACHE_VERSION = 1
ELO_CACHE_VERSION = 1
