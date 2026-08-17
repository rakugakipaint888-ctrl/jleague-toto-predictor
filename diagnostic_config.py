"""Version8-Bの説明可能な診断閾値を一元管理する。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticThresholds:
    """自動変更しない固定診断閾値。

    値は比率（0.10 = 10ポイント）で表す。Version8-Cの改善提案や
    最適化設定とは分離し、Version8-Bの判定にだけ使用する。
    """

    minimum_match_count: int = 26
    minimum_round_count: int = 2
    minimum_class_support: int = 5
    minimum_high_probability_count: int = 10
    minimum_coverage_evaluated_count: int = 5
    probability_sum_tolerance: float = 1e-9

    accuracy_drop_attention: float = 0.05
    accuracy_drop_warning: float = 0.10
    brier_increase_attention: float = 0.05
    brier_increase_warning: float = 0.10
    log_loss_increase_attention: float = 0.08
    log_loss_increase_warning: float = 0.15
    calibration_increase_attention: float = 0.04
    calibration_increase_warning: float = 0.08
    draw_f1_drop_attention: float = 0.08
    draw_f1_drop_warning: float = 0.15

    draw_favorite_gap_attention: float = 0.15
    draw_favorite_gap_warning: float = 0.25
    low_recall_attention: float = 0.20
    low_recall_warning: float = 0.10
    high_probability_threshold: float = 0.60
    high_probability_gap_attention: float = 0.15
    high_probability_gap_warning: float = 0.25

    league_accuracy_gap_attention: float = 0.10
    league_accuracy_gap_warning: float = 0.15
    league_brier_gap_attention: float = 0.08
    league_brier_gap_warning: float = 0.15
    league_log_loss_gap_attention: float = 0.12
    league_log_loss_gap_warning: float = 0.20
    league_calibration_gap_attention: float = 0.05
    league_calibration_gap_warning: float = 0.10
    league_draw_f1_gap_attention: float = 0.12
    league_draw_f1_gap_warning: float = 0.20


DEFAULT_DIAGNOSTIC_THRESHOLDS = DiagnosticThresholds()

PERIOD_OPTIONS = (
    "全実戦履歴",
    "直近5開催",
    "直近10開催",
    "直近20開催",
    "今シーズン",
    "任意期間",
)
LEAGUE_OPTIONS = ("全リーグ", "J1", "J2", "J3")
DIAGNOSTIC_STATUSES = ("正常", "注意", "警告", "データ不足")
ANOMALY_LEVELS = ("情報", "注意", "警告")


__all__ = [
    "ANOMALY_LEVELS",
    "DEFAULT_DIAGNOSTIC_THRESHOLDS",
    "DIAGNOSTIC_STATUSES",
    "DiagnosticThresholds",
    "LEAGUE_OPTIONS",
    "PERIOD_OPTIONS",
]
