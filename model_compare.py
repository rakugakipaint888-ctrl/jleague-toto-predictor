"""Version7-B結果をStreamlit表・CSV・グラフ用DataFrameへ変換する。"""

from __future__ import annotations

import json
from typing import Any, Mapping

import pandas as pd

from bootstrap_evaluation import BootstrapEvaluation
from model_evaluation import (
    CandidateEvaluation,
    check_draw_degradation,
    check_overfitting,
    comparison_rows,
)
from model_optimizer import OptimizationResult

PARAMETER_LABELS = {
    "home_correction": "ホーム補正",
    "elo_correction_rate": "Elo補正率",
    "home_advantage": "Eloホームアドバンテージ",
    "k_factor": "Elo K係数",
    "recent_match_weights": "直近5試合重み",
    "recent_weighted_share": "直近成績混合率",
    "season_average_share": "シーズン平均混合率",
    "venue_mix_rate": "ホーム／アウェイ成績混合率",
    "rank_correction_rate": "順位補正率",
    "points_correction_rate": "勝点補正率",
    "goal_difference_correction_rate": "得失点差補正率",
    "expected_goals_minimum": "期待得点下限",
    "expected_goals_maximum": "期待得点上限",
}


def _metric_row(
    period: str,
    model: str,
    evaluation: CandidateEvaluation,
) -> dict[str, Any]:
    metrics = evaluation.metrics
    draw = evaluation.draw
    return {
        "区分": period,
        "モデル": model,
        "総合Score": evaluation.score,
        "試合数": metrics.match_count,
        "1予測数": sum(row.prediction == "1" for row in evaluation.rows),
        "0予測数": sum(row.prediction == "0" for row in evaluation.rows),
        "2予測数": sum(row.prediction == "2" for row in evaluation.rows),
        "Brier Score": metrics.brier_score,
        "Log Loss": metrics.log_loss,
        "Calibration": metrics.calibration_error,
        "全体的中率": metrics.accuracy,
        "1予測率": metrics.prediction_share["1"],
        "0予測率": metrics.prediction_share["0"],
        "2予測率": metrics.prediction_share["2"],
        "1的中率": metrics.class_accuracy["1"],
        "0的中率": metrics.class_accuracy["0"],
        "2的中率": metrics.class_accuracy["2"],
        "平均1予測確率": (
            sum(row.probabilities["1"] for row in evaluation.rows)
            / len(evaluation.rows)
        ),
        "平均0予測確率": (
            sum(row.probabilities["0"] for row in evaluation.rows)
            / len(evaluation.rows)
        ),
        "平均2予測確率": (
            sum(row.probabilities["2"] for row in evaluation.rows)
            / len(evaluation.rows)
        ),
        "実際の1発生率": metrics.actual_share["1"],
        "実際の0発生率": metrics.actual_share["0"],
        "実際の2発生率": metrics.actual_share["2"],
        "引分Precision": draw.precision,
        "引分Recall": draw.recall,
        "引分F1": draw.f1_score,
        "引分Brier": draw.brier_score,
        "引分Calibration": draw.calibration_error,
        "ROI": evaluation.roi if evaluation.roi is not None else "算出不可",
    }


def training_validation_frame(result: OptimizationResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            _metric_row("Training", "Version7-A", result.baseline_training),
            _metric_row("Training", "Version7-B候補", result.best_training),
            _metric_row(
                "最終Validation",
                "Version7-A",
                result.baseline_final_validation,
            ),
            _metric_row(
                "最終Validation",
                "Version7-B候補",
                result.best_final_validation,
            ),
        ]
    )


def version7a_comparison_frame(result: OptimizationResult) -> pd.DataFrame:
    return pd.DataFrame(
        comparison_rows(
            result.baseline_final_validation,
            result.best_final_validation,
        )
    )


def parameter_comparison_frame(result: OptimizationResult) -> pd.DataFrame:
    current = result.current_settings.parameters.as_flat_dict()
    candidate = result.best_parameters.as_flat_dict()
    rows = []
    for key in sorted(set(current) | set(candidate)):
        before = current.get(key)
        after = candidate.get(key)
        if key == "recent_match_weights":
            before = " / ".join(str(value) for value in before)
            after = " / ".join(str(value) for value in after)
        label = PARAMETER_LABELS.get(
            key.replace("draw_", ""),
            f"引分: {key[5:]}" if key.startswith("draw_") else key,
        )
        rows.append(
            {
                "パラメータ": label,
                # 数値と重み文字列が同じ列へ混在するとStreamlitのArrow変換で
                # 警告になるため、表示列だけ文字列へ統一する。
                "現在設定": str(before),
                "候補設定": str(after),
                "変更": "変更あり" if before != after else "変更なし",
            }
        )
    return pd.DataFrame(rows)


def ranking_frame(result: OptimizationResult) -> pd.DataFrame:
    rows = []
    for rank, record in enumerate(result.ranking, start=1):
        final = record.final_validation or record.selection_validation
        metrics = final.metrics
        draw = final.draw
        overfitting = check_overfitting(
            record.training,
            final,
            result.configuration.overfit_thresholds,
        )
        draw_degradation = check_draw_degradation(
            result.baseline_final_validation,
            final,
            result.configuration.draw_tolerances,
        )
        rows.append(
            {
                "順位": rank,
                "Trial": record.trial_number,
                "総合Score": record.selection_score,
                "Validation Score": final.score,
                "Training Score": record.training.score,
                "Brier Score": metrics.brier_score,
                "Log Loss": metrics.log_loss,
                "Calibration": metrics.calibration_error,
                "全体的中率": metrics.accuracy,
                "引分Precision": draw.precision,
                "引分Recall": draw.recall,
                "引分F1": draw.f1_score,
                "引分Brier": draw.brier_score,
                "引分Calibration": draw.calibration_error,
                "1予測率": metrics.prediction_share["1"],
                "0予測率": metrics.prediction_share["0"],
                "2予測率": metrics.prediction_share["2"],
                "過学習判定": overfitting.label,
                "引分性能悪化判定": draw_degradation.label,
                "パラメータ": json.dumps(
                    record.parameters.as_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
    return pd.DataFrame(rows)


def trial_metrics_frame(result: OptimizationResult) -> pd.DataFrame:
    rows = []
    for record in sorted(result.all_trials, key=lambda item: item.trial_number):
        metrics = record.selection_validation.metrics
        rows.append(
            {
                "Trial": record.trial_number,
                "総合Score": record.selection_score,
                "Validation Score": record.raw_validation_score,
                "Training Score": record.training.score,
                "Brier Score": metrics.brier_score,
                "Log Loss": metrics.log_loss,
                "Calibration": metrics.calibration_error,
                "全体的中率": metrics.accuracy,
                "引分F1": record.selection_validation.draw.f1_score,
            }
        )
    return pd.DataFrame(rows).set_index("Trial") if rows else pd.DataFrame()


def parameter_importance_frame(result: OptimizationResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"パラメータ": key, "重要度": value}
            for key, value in sorted(
                result.parameter_importance.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ]
    )


def stability_frame(result: OptimizationResult) -> pd.DataFrame:
    rows = [
        {"区分": "シーズン", "対象": key, "Score": value}
        for key, value in result.stability.season_scores.items()
    ]
    rows.extend(
        {"区分": "リーグ", "対象": key, "Score": value}
        for key, value in result.stability.league_scores.items()
    )
    return pd.DataFrame(rows)


def bootstrap_frame(
    bootstrap_results: Mapping[int, BootstrapEvaluation],
) -> pd.DataFrame:
    rows = []
    for trial_number, evaluation in bootstrap_results.items():
        for metric, distribution in evaluation.metrics.items():
            rows.append(
                {
                    "Trial": trial_number,
                    "指標": metric,
                    "平均": distribution.mean,
                    "中央値": distribution.median,
                    "標準偏差": distribution.standard_deviation,
                    "95%CI下限": distribution.confidence_lower,
                    "95%CI上限": distribution.confidence_upper,
                    "再評価回数": evaluation.iterations,
                }
            )
    return pd.DataFrame(rows)
