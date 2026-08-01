"""Version4のElo設定を一元管理する。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EloSettings:
    """Elo更新と期待得点補正に使う変更可能な設定値。"""

    default_initial_rating: float = 1500.0
    use_category_initial_ratings: bool = True
    j1_initial_rating: float = 1500.0
    j2_initial_rating: float = 1450.0
    j3_initial_rating: float = 1400.0
    k_factor: float = 20.0
    home_advantage: float = 65.0
    goal_difference_adjustment_enabled: bool = True
    goal_difference_multipliers: tuple[tuple[int, float], ...] = (
        (1, 1.00),
        (2, 1.25),
        (3, 1.50),
        (4, 1.75),
    )
    expected_goals_change_per_100_elo: float = 0.05
    expected_goals_max_adjustment: float = 0.15
    elo_adjustment_strength: float = 1.0

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
