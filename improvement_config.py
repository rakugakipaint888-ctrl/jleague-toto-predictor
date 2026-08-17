"""Version8-Cの改善提案・優先度・信頼度ルールを一元管理する。"""

from __future__ import annotations

from dataclasses import dataclass


IMPROVEMENT_CATEGORIES = (
    "モデル全体",
    "引分性能",
    "1予測",
    "2予測",
    "Calibration",
    "Brier Score",
    "Log Loss",
    "リーグ別性能",
    "買い目戦略",
    "Coverage",
    "データ品質",
    "再最適化タイミング",
)
PRIORITY_LEVELS = ("低", "中", "高")
CONFIDENCE_LEVELS = ("低", "中", "高")
REOPTIMIZATION_LEVELS = ("不要", "検討", "推奨", "データ不足")


@dataclass(frozen=True)
class ImprovementThresholds:
    """自動変更・自動最適化に使用しない固定提案ルール。"""

    high_confidence_match_count: int = 65
    high_confidence_round_count: int = 5
    minimum_bet_evaluated_run_count: int = 2

    draw_probability_close_tolerance: float = 0.05
    draw_inclusion_gap_attention: float = 0.10
    draw_coverage_rate_attention: float = 0.50

    low_average_coverage_attention: float = 0.05
    coverage_efficiency_minimum_gain: float = 0.05
    recommended_actual_coverage_gap_attention: float = 0.05
    recommended_actual_ticket_gap_attention: float = 2.0
    roi_break_even: float = 1.00
    roi_warning: float = 0.50
    simulation_actual_roi_gap_attention: float = 0.20

    priority_high_score: int = 4
    priority_medium_score: int = 2
    confidence_high_score: int = 4
    confidence_medium_score: int = 2

    reoptimization_recommended_warning_count: int = 2
    reoptimization_recommended_family_count: int = 3
    reoptimization_consider_attention_count: int = 2


DEFAULT_IMPROVEMENT_THRESHOLDS = ImprovementThresholds()


__all__ = [
    "CONFIDENCE_LEVELS",
    "DEFAULT_IMPROVEMENT_THRESHOLDS",
    "IMPROVEMENT_CATEGORIES",
    "ImprovementThresholds",
    "PRIORITY_LEVELS",
    "REOPTIMIZATION_LEVELS",
]
