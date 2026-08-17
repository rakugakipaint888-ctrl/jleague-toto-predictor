"""Version8-C改善提案ルール・履歴・読取専用性を確認する。"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pandas as pd

from diagnostic_config import DEFAULT_DIAGNOSTIC_THRESHOLDS
from diagnostic_metrics import evaluate_one_vs_rest
from history_manager import JAPAN_TIMEZONE
from improvement_history import ImprovementHistoryManager
from improvement_recommendations import generate_improvement_recommendations
from metrics import evaluate_model
from model_diagnostics import (
    DataQualityIssue,
    DiagnosticAnomaly,
    DiagnosticCounts,
    DiagnosticFilter,
    DiagnosticReport,
    DrawDiagnostic,
)


BASE_TIME = datetime(2026, 8, 17, 12, 0, tzinfo=JAPAN_TIMEZONE)


def anomaly(
    code: str,
    *,
    level: str = "注意",
    metric: str = "指標",
    current: float = 0.30,
    baseline: float = 0.50,
    difference: float = -0.20,
) -> DiagnosticAnomaly:
    return DiagnosticAnomaly(
        code=code,
        category="モデル性能",
        name=f"{code}異常",
        level=level,
        metric=metric,
        current_value=current,
        baseline_value=baseline,
        difference=difference,
        unit="pt",
        judgement=f"差{difference:+.1%}",
        message=f"{metric}を固定閾値で検知しました。",
    )


def base_report(
    *,
    anomalies: tuple[DiagnosticAnomaly, ...] = (),
    data_sufficient: bool = True,
    match_count: int = 65,
    round_count: int = 5,
) -> DiagnosticReport:
    predictions = tuple(("1", "0", "2")[index % 3] for index in range(65))
    actuals = predictions
    probabilities = tuple(
        {
            "1": 0.70 if outcome == "1" else 0.15,
            "0": 0.70 if outcome == "0" else 0.15,
            "2": 0.70 if outcome == "2" else 0.15,
        }
        for outcome in predictions
    )
    overall = evaluate_model(predictions, probabilities, actuals)
    class_metrics = {
        outcome: evaluate_one_vs_rest(
            predictions,
            probabilities,
            actuals,
            outcome=outcome,
        )
        for outcome in ("1", "0", "2")
    }
    draw = DrawDiagnostic(
        actual_draw_rate=22 / 65,
        favorite_draw_rate=22 / 65,
        candidate_rate=22 / 65,
        actual_draw_count=22,
        favorite_draw_hit_count=22,
        candidate_hit_count=22,
        precision=1.0,
        recall=1.0,
        f1_score=1.0,
        brier_score=class_metrics["0"].brier_score,
        calibration_error=class_metrics["0"].calibration_error,
        mean_probability_0=class_metrics["0"].mean_probability,
        probability_actual_gap=(
            class_metrics["0"].mean_probability - 22 / 65
        ),
        recommended_draw_inclusion_rate=22 / 65,
        purchased_draw_inclusion_rate=None,
        recommended_draw_covered_count=22,
        purchased_draw_covered_count=0,
        draw_inclusion_score_mean=0.5,
    )
    return DiagnosticReport(
        diagnostic_id="diagnostic-test-1",
        diagnosed_at=BASE_TIME,
        selection=DiagnosticFilter(),
        thresholds=DEFAULT_DIAGNOSTIC_THRESHOLDS,
        status=(
            "データ不足"
            if not data_sufficient
            else ("警告" if any(item.level == "警告" for item in anomalies) else (
                "注意" if anomalies else "正常"
            ))
        ),
        status_reason=(
            "最低サンプル基準を満たしていません。"
            if not data_sufficient
            else "固定ルールによる診断です。"
        ),
        data_sufficient=data_sufficient,
        counts=DiagnosticCounts(
            predicted_run_count=round_count,
            confirmed_run_count=round_count,
            evaluated_run_count=round_count,
            unpurchased_run_count=round_count,
            round_count=round_count,
            match_count=match_count,
        ),
        overall=overall,
        average_predicted_probability=0.70,
        average_max_probability=0.70,
        class_metrics=class_metrics,
        draw=draw,
        anomalies=anomalies,
        quality_issues=(),
        excluded_match_count=0,
        period_shortage=False,
        period_available_round_count=round_count,
        calibration_table=pd.DataFrame(
            [
                {
                    "結果": "0",
                    "確率帯": "60%以上",
                    "試合数": 10,
                    "平均予測確率": 0.75,
                    "実発生率": 0.40,
                    "Calibration差": 0.35,
                }
            ]
        ),
        purchase_performance={
            "has_records": False,
            "has_evaluated_records": False,
        },
        simulation_performance={
            "has_records": False,
            "has_evaluated_records": False,
        },
    )


def codes(report) -> set[str]:
    return {item.code for item in report.recommendations}


class RaisingFormatter:
    def format(self, structured_text: str) -> str:
        raise RuntimeError("AI unavailable")


class SafeFormatter:
    def format(self, structured_text: str) -> str:
        return structured_text.replace("診断：", "診断結果：")


class HallucinatingFormatter:
    def format(self, structured_text: str) -> str:
        return structured_text + " candidate_thresholdを0.22へ変更しました"


class ImprovementRecommendationsTest(unittest.TestCase):
    def test_no_diagnostic_data_is_data_shortage_without_proposal(self) -> None:
        source = base_report(
            data_sufficient=False,
            match_count=0,
            round_count=0,
        )
        result = generate_improvement_recommendations(source)
        self.assertEqual(result.recommendations, ())
        self.assertEqual(result.reoptimization_level, "データ不足")
        self.assertIn("設定変更は推奨しません", result.recommended_action)

    def test_normal_model_has_no_change_proposal(self) -> None:
        result = generate_improvement_recommendations(base_report())
        self.assertEqual(result.recommendations, ())
        self.assertEqual(result.reoptimization_level, "不要")
        self.assertIn("監視", result.recommended_action)

    def test_draw_recall_f1_and_favorite_gap_are_one_draw_proposal(self) -> None:
        source = base_report(
            anomalies=(
                anomaly("low_recall_0", metric="0 Recall", current=0.08),
                anomaly("draw_f1_drop_直近5開催", metric="引分F1"),
                anomaly(
                    "draw_favorite_rate_gap",
                    metric="実引分率－本命0率",
                    current=0.21,
                    baseline=0.15,
                    difference=0.06,
                ),
            )
        )
        source.draw = replace(
            source.draw,
            actual_draw_rate=0.24,
            favorite_draw_rate=0.03,
            mean_probability_0=0.23,
            recall=0.08,
            f1_score=0.12,
            recommended_draw_inclusion_rate=0.40,
            recommended_draw_covered_count=2,
            actual_draw_count=16,
        )
        result = generate_improvement_recommendations(source)
        draw_items = [
            item for item in result.recommendations
            if item.code == "draw_performance_review"
        ]
        self.assertEqual(len(draw_items), 1)
        self.assertIn("引分候補閾値の再検証", draw_items[0].improvement_candidates)
        self.assertTrue(any("argmax" in value for value in draw_items[0].possible_causes))
        self.assertTrue(any("買い目" in value for value in draw_items[0].possible_causes))

    def test_draw_inclusion_without_coverage_is_detected_from_version8b_summary(self) -> None:
        source = base_report()
        source.draw = replace(
            source.draw,
            actual_draw_rate=0.24,
            favorite_draw_rate=0.20,
            recommended_draw_inclusion_rate=0.45,
            actual_draw_count=16,
            recommended_draw_covered_count=3,
        )
        result = generate_improvement_recommendations(source)
        item = next(
            recommendation
            for recommendation in result.recommendations
            if recommendation.code == "draw_performance_review"
        )
        self.assertTrue(
            {
                "draw_included_but_not_favorite",
                "draw_inclusion_coverage_low",
            }.issubset(set(item.anomaly_codes))
        )
        self.assertTrue(any("実引分カバー率" in value.metric for value in item.evidence))
        self.assertTrue(any("引分性能" in value for value in result.detected_problems))

    def test_calibration_brier_log_loss_are_consolidated(self) -> None:
        source = base_report(
            anomalies=(
                anomaly("brier_increase_直近5開催", metric="Brier Score"),
                anomaly("log_loss_increase_直近5開催", metric="Log Loss"),
                anomaly("calibration_increase_直近5開催", metric="Calibration"),
            )
        )
        result = generate_improvement_recommendations(source)
        probability_items = [
            item for item in result.recommendations
            if item.code == "probability_quality_review"
        ]
        self.assertEqual(len(probability_items), 1)
        self.assertEqual(
            set(probability_items[0].related_categories),
            {"Brier Score", "Log Loss", "Calibration"},
        )
        self.assertTrue(any("過信" in value for value in probability_items[0].possible_causes))
        self.assertIn("高信頼予測", probability_items[0].diagnosis)

    def test_each_probability_metric_can_trigger_its_category(self) -> None:
        cases = (
            ("brier_increase_直近5開催", "Brier Score"),
            ("log_loss_increase_直近5開催", "Log Loss"),
            ("calibration_increase_直近5開催", "Calibration"),
        )
        for anomaly_code, expected_category in cases:
            with self.subTest(anomaly_code=anomaly_code):
                result = generate_improvement_recommendations(
                    base_report(anomalies=(anomaly(anomaly_code),))
                )
                item = next(
                    recommendation
                    for recommendation in result.recommendations
                    if recommendation.code == "probability_quality_review"
                )
                self.assertEqual(item.category, expected_category)

    def test_class_one_and_two_are_separate_proposals(self) -> None:
        source = base_report(
            anomalies=(
                anomaly("low_recall_1", metric="1 Recall", current=0.10),
                anomaly("low_recall_2", metric="2 Recall", current=0.09),
            )
        )
        result = generate_improvement_recommendations(source)
        self.assertTrue({"class_1_review", "class_2_review"}.issubset(codes(result)))
        categories = {item.category for item in result.recommendations}
        self.assertTrue({"1予測", "2予測"}.issubset(categories))

    def test_j2_only_degradation_is_j2_proposal(self) -> None:
        source = base_report(
            anomalies=(
                anomaly(
                    "league_calibration_gap_J2",
                    metric="J2 Calibration",
                    current=0.18,
                    baseline=0.06,
                    difference=0.12,
                ),
            )
        )
        result = generate_improvement_recommendations(source)
        item = next(item for item in result.recommendations if item.code == "league_J2_review")
        self.assertEqual(item.category, "リーグ別性能")
        self.assertIn("J2", item.recommended_action)

    def test_coverage_and_roi_create_one_bet_strategy_proposal(self) -> None:
        source = base_report()
        source.bet_summary = pd.DataFrame(
            [
                {
                    "区分": "AI推奨（simulation）",
                    "評価済み買い目数": 3,
                    "平均Coverage": 0.02,
                    "平均口数": 8.0,
                },
                {
                    "区分": "実購入（actual）",
                    "評価済み買い目数": 3,
                    "平均Coverage": 0.10,
                    "平均口数": 3.0,
                },
            ]
        )
        source.coverage_summary = pd.DataFrame(
            [
                {"区分": "AI推奨", "状態": "診断可能", "Coverage帯": "0〜1%", "完全カバー率": 0.10},
                {"区分": "AI推奨", "状態": "診断可能", "Coverage帯": "10%以上", "完全カバー率": 0.12},
            ]
        )
        source.purchase_performance = {
            "has_records": True,
            "has_evaluated_records": True,
            "evaluated_run_count": 3,
            "roi": 0.30,
        }
        source.simulation_performance = {
            "has_records": True,
            "has_evaluated_records": True,
            "evaluated_run_count": 3,
            "roi": 1.20,
        }
        result = generate_improvement_recommendations(source)
        items = [item for item in result.recommendations if item.code == "bet_strategy_review"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].category, "買い目戦略")
        self.assertTrue(
            {"low_average_coverage", "coverage_efficiency_low", "actual_roi_low"}
            .issubset(set(items[0].anomaly_codes))
        )
        self.assertIn("Coverage", items[0].related_categories)

    def test_data_shortage_suppresses_performance_proposals(self) -> None:
        source = base_report(
            anomalies=(anomaly("low_recall_0"),),
            data_sufficient=False,
            match_count=13,
            round_count=1,
        )
        result = generate_improvement_recommendations(source)
        self.assertNotIn("draw_performance_review", codes(result))
        self.assertEqual(result.reoptimization_level, "データ不足")

    def test_reoptimization_levels_are_explainable(self) -> None:
        no_issue = generate_improvement_recommendations(base_report())
        consider = generate_improvement_recommendations(
            base_report(anomalies=(anomaly("low_recall_1", level="警告"),))
        )
        recommended = generate_improvement_recommendations(
            base_report(
                anomalies=(
                    anomaly("brier_increase_直近5開催", level="警告"),
                    anomaly("log_loss_increase_直近5開催", level="警告"),
                )
            )
        )
        self.assertEqual(no_issue.reoptimization_level, "不要")
        self.assertEqual(consider.reoptimization_level, "検討")
        self.assertEqual(recommended.reoptimization_level, "推奨")
        self.assertIn("警告2件", recommended.reoptimization_reason)

    def test_priority_confidence_and_ranking_are_fixed_rule_outputs(self) -> None:
        source = base_report(
            anomalies=(
                anomaly("brier_increase_直近5開催", level="警告"),
                anomaly("brier_increase_直近10開催", level="警告"),
                anomaly("low_recall_1", level="注意"),
            )
        )
        result = generate_improvement_recommendations(source)
        self.assertEqual([item.rank for item in result.recommendations], list(range(1, len(result.recommendations) + 1)))
        self.assertEqual(result.recommendations[0].priority, "高")
        self.assertEqual(result.recommendations[0].confidence, "高")
        self.assertIn("固定ルール", result.recommendations[0].priority_reason)
        self.assertIn("65試合・5開催", result.recommendations[0].confidence_reason)

    def test_ai_unavailable_or_invalid_uses_template_fallback(self) -> None:
        source = base_report(anomalies=(anomaly("low_recall_1"),))
        unavailable = generate_improvement_recommendations(
            source,
            text_formatter=RaisingFormatter(),
        )
        hallucinated = generate_improvement_recommendations(
            source,
            text_formatter=HallucinatingFormatter(),
        )
        self.assertEqual(unavailable.text_mode, "template")
        self.assertEqual(hallucinated.text_mode, "template")
        self.assertNotIn("0.22", hallucinated.recommendations[0].narrative)

    def test_constrained_formatter_only_changes_language(self) -> None:
        result = generate_improvement_recommendations(
            base_report(anomalies=(anomaly("low_recall_1"),)),
            text_formatter=SafeFormatter(),
        )
        self.assertEqual(result.text_mode, "ai")
        self.assertIn("診断結果：", result.recommendations[0].narrative)

    def test_quality_issue_is_separate_and_does_not_force_reoptimization(self) -> None:
        source = base_report()
        source.quality_issues = (
            DataQualityIssue(
                code="probability_missing",
                name="予測確率欠損",
                level="警告",
                count=2,
                excluded_count=2,
                message="P(0)欠損を2件検知しました。",
            ),
        )
        source.excluded_match_count = 2
        result = generate_improvement_recommendations(source)
        self.assertIn("data_quality_review", codes(result))
        self.assertEqual(result.reoptimization_level, "不要")

    def test_improvement_history_saves_without_mutating_diagnostic(self) -> None:
        source = base_report(anomalies=(anomaly("low_recall_2"),))
        calibration_before = source.calibration_table.copy(deep=True)
        anomalies_before = source.anomalies
        result = generate_improvement_recommendations(source, generated_at=BASE_TIME)
        with tempfile.TemporaryDirectory() as directory:
            manager = ImprovementHistoryManager(Path(directory) / "history.csv")
            self.assertTrue(manager.save(result))
            self.assertFalse(manager.save(result))
            loaded = manager.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded.iloc[0]["diagnostic_id"], source.diagnostic_id)
            self.assertEqual(loaded.iloc[0]["reoptimization_level"], "不要")
        pd.testing.assert_frame_equal(source.calibration_table, calibration_before)
        self.assertIs(source.anomalies, anomalies_before)


if __name__ == "__main__":
    unittest.main()
