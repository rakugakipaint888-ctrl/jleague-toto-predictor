"""Version8-B診断結果からVersion8-Cの改善候補を構造化する。

Version8-Aの生データ再評価、設定変更、最適化実行、モデル採用は行わない。
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Optional, Protocol, Sequence

import pandas as pd

from history_manager import JAPAN_TIMEZONE
from improvement_config import (
    DEFAULT_IMPROVEMENT_THRESHOLDS,
    IMPROVEMENT_CATEGORIES,
    ImprovementThresholds,
)
from model_diagnostics import DiagnosticAnomaly, DiagnosticReport


@dataclass(frozen=True)
class RecommendationEvidence:
    """Version8-Bの現在値・基準値・差を保持する。"""

    metric: str
    current_value: Optional[float]
    baseline_value: Optional[float]
    difference: Optional[float]
    unit: str
    source: str


@dataclass(frozen=True)
class ImprovementRecommendation:
    """自動適用されない1件の改善候補。"""

    rank: int
    code: str
    category: str
    related_categories: tuple[str, ...]
    title: str
    evidence: tuple[RecommendationEvidence, ...]
    diagnosis: str
    possible_causes: tuple[str, ...]
    improvement_candidates: tuple[str, ...]
    recommended_action: str
    priority: str
    priority_reason: str
    confidence: str
    confidence_reason: str
    anomaly_codes: tuple[str, ...]
    narrative: str


@dataclass(frozen=True)
class ImprovementReport:
    """1つのVersion8-B診断に対応するVersion8-C提案結果。"""

    improvement_id: str
    generated_at: datetime
    diagnostic_id: str
    diagnostic_status: str
    period: str
    period_start: str
    period_end: str
    league: str
    prediction_version: str
    match_count: int
    round_count: int
    data_sufficient: bool
    detected_problems: tuple[str, ...]
    recommendations: tuple[ImprovementRecommendation, ...]
    reoptimization_level: str
    reoptimization_reason: str
    recommended_action: str
    notice: str
    text_mode: str


class RecommendationTextFormatter(Protocol):
    """構造化済み説明だけを自然な日本語へ整える任意formatter。"""

    def format(self, structured_text: str) -> str:
        """数値・原因・アクションを追加せず表現だけを整える。"""


@dataclass(frozen=True)
class _Draft:
    code: str
    category: str
    related_categories: tuple[str, ...]
    title: str
    evidence: tuple[RecommendationEvidence, ...]
    diagnosis: str
    possible_causes: tuple[str, ...]
    improvement_candidates: tuple[str, ...]
    recommended_action: str
    levels: tuple[str, ...]
    anomaly_codes: tuple[str, ...]
    global_scope: bool = False


def generate_improvement_recommendations(
    diagnostic: DiagnosticReport,
    *,
    thresholds: ImprovementThresholds = DEFAULT_IMPROVEMENT_THRESHOLDS,
    generated_at: Optional[datetime] = None,
    text_formatter: Optional[RecommendationTextFormatter] = None,
) -> ImprovementReport:
    """Version8-Bの確定済み診断値だけから提案を生成する。"""

    now = generated_at or datetime.now(JAPAN_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JAPAN_TIMEZONE)

    drafts: list[_Draft] = []
    quality = _quality_draft(diagnostic)
    if quality is not None:
        drafts.append(quality)

    if diagnostic.data_sufficient:
        for draft in (
            _overall_draft(diagnostic),
            _draw_draft(diagnostic, thresholds),
            _probability_quality_draft(diagnostic),
            _class_draft(diagnostic, "1"),
            _class_draft(diagnostic, "2"),
            _bet_draft(diagnostic, thresholds),
        ):
            if draft is not None:
                drafts.append(draft)
        drafts.extend(_league_drafts(diagnostic))

    drafts = _deduplicate_drafts(drafts)
    recommendations: list[ImprovementRecommendation] = []
    ai_success_count = 0
    for draft in drafts:
        priority, priority_reason = _priority(draft, thresholds)
        confidence, confidence_reason = _confidence(
            diagnostic,
            draft,
            thresholds,
        )
        template = _template_narrative(draft)
        narrative, used_ai = _format_narrative(template, text_formatter)
        ai_success_count += int(used_ai)
        recommendations.append(
            ImprovementRecommendation(
                rank=0,
                code=draft.code,
                category=draft.category,
                related_categories=draft.related_categories,
                title=draft.title,
                evidence=draft.evidence,
                diagnosis=draft.diagnosis,
                possible_causes=draft.possible_causes,
                improvement_candidates=draft.improvement_candidates,
                recommended_action=draft.recommended_action,
                priority=priority,
                priority_reason=priority_reason,
                confidence=confidence,
                confidence_reason=confidence_reason,
                anomaly_codes=draft.anomaly_codes,
                narrative=narrative,
            )
        )

    recommendations.sort(key=_recommendation_sort_key)
    ranked = tuple(
        replace(item, rank=index)
        for index, item in enumerate(recommendations, start=1)
    )
    reoptimization_level, reoptimization_reason = _reoptimization(
        diagnostic,
        thresholds,
    )
    detected = _detected_problems(diagnostic, ranked)
    if not diagnostic.data_sufficient:
        action = (
            "データ不足のため設定変更は推奨しません。"
            "結果確定済み実戦データの蓄積を継続してください。"
        )
        notice = (
            f"Version8-Bの最低基準は{diagnostic.thresholds.minimum_match_count}試合かつ"
            f"{diagnostic.thresholds.minimum_round_count}開催です。"
            "参考値から係数や買い目ルールを変更しないでください。"
        )
    elif ranked:
        action = " / ".join(
            f"{item.rank}位：{item.recommended_action}" for item in ranked[:3]
        )
        notice = (
            "表示内容は検証候補です。設定、config.py、Coverage、買い目ルール、"
            "モデルは自動変更されません。"
        )
    else:
        action = "現行モデルの監視と実戦データ蓄積を継続してください。"
        notice = (
            "現在のVersion8-B診断から、設定変更を検討する異常は確認されませんでした。"
        )

    if not recommendations or ai_success_count == 0:
        text_mode = "template"
    elif ai_success_count == len(recommendations):
        text_mode = "ai"
    else:
        text_mode = "mixed"

    selection = diagnostic.selection
    return ImprovementReport(
        improvement_id=(
            f"improve_{now.astimezone(JAPAN_TIMEZONE).strftime('%Y%m%dT%H%M%S%f')}_"
            f"{uuid.uuid4().hex}"
        ),
        generated_at=now.astimezone(JAPAN_TIMEZONE),
        diagnostic_id=diagnostic.diagnostic_id,
        diagnostic_status=diagnostic.status,
        period=selection.period,
        period_start=(selection.start_date.isoformat() if selection.start_date else ""),
        period_end=(selection.end_date.isoformat() if selection.end_date else ""),
        league=selection.league,
        prediction_version=selection.version,
        match_count=diagnostic.counts.match_count,
        round_count=diagnostic.counts.round_count,
        data_sufficient=diagnostic.data_sufficient,
        detected_problems=detected,
        recommendations=ranked,
        reoptimization_level=reoptimization_level,
        reoptimization_reason=reoptimization_reason,
        recommended_action=action,
        notice=notice,
        text_mode=text_mode,
    )


def _quality_draft(report: DiagnosticReport) -> Optional[_Draft]:
    if not report.quality_issues:
        return None
    evidence = tuple(
        RecommendationEvidence(
            metric=item.name,
            current_value=float(item.count),
            baseline_value=0.0,
            difference=float(item.count),
            unit="件",
            source=f"Version8-Bデータ品質:{item.code}",
        )
        for item in report.quality_issues
    )
    return _Draft(
        code="data_quality_review",
        category="データ品質",
        related_categories=(),
        title="診断対象データの品質確認",
        evidence=evidence,
        diagnosis=(
            f"Version8-Bがデータ品質問題を{len(report.quality_issues)}種類検知し、"
            f"{report.excluded_match_count}試合行を診断計算から除外しています。"
        ),
        possible_causes=("原因は診断結果だけでは特定できません。",),
        improvement_candidates=(
            "Version8-A保存元と該当CSV行の整合性確認",
            "欠損・不正値・重複が発生した処理経路の確認",
        ),
        recommended_action="モデル設定を変える前にデータ品質問題を確認",
        levels=tuple(item.level for item in report.quality_issues),
        anomaly_codes=tuple(item.code for item in report.quality_issues),
    )


def _overall_draft(report: DiagnosticReport) -> Optional[_Draft]:
    anomalies = _anomalies(report, ("accuracy_drop_",))
    if not anomalies:
        return None
    return _Draft(
        code="overall_model_review",
        category="モデル全体",
        related_categories=(),
        title="モデル全体の直近性能を再確認",
        evidence=_anomaly_evidence(anomalies),
        diagnosis="直近の的中率が全期間値より低下しています。",
        possible_causes=(
            "直近開催の対戦構成やリーグ構成が全期間と異なる可能性があります。",
            "複数の補正・特徴量の組み合わせが直近データへ適合していない可能性があります。",
        ),
        improvement_candidates=(
            "結果別・リーグ別の誤り分布を優先確認",
            "Version7-Bの既存探索範囲を使った再最適化の検討",
        ),
        recommended_action="モデル全体の直近性能と偏りを再検証",
        levels=_levels(anomalies),
        anomaly_codes=_codes(anomalies),
        global_scope=True,
    )


def _draw_draft(
    report: DiagnosticReport,
    thresholds: ImprovementThresholds,
) -> Optional[_Draft]:
    prefixes = (
        "draw_favorite_rate_gap",
        "low_recall_0",
        "draw_f1_drop_",
        "draw_brier_increase_",
        "draw_calibration_increase_",
    )
    anomalies = _anomalies(report, prefixes)
    draw = report.draw
    zero = report.class_metrics.get("0")
    direct_codes: list[str] = []
    direct_levels: list[str] = []
    inclusion_gap = (
        draw.recommended_draw_inclusion_rate - draw.favorite_draw_rate
        if draw.recommended_draw_inclusion_rate is not None
        and draw.favorite_draw_rate is not None
        else None
    )
    if (
        inclusion_gap is not None
        and inclusion_gap >= thresholds.draw_inclusion_gap_attention
    ):
        direct_codes.append("draw_included_but_not_favorite")
        direct_levels.append("注意")
    covered_rate = (
        draw.recommended_draw_covered_count / draw.actual_draw_count
        if draw.actual_draw_count >= report.thresholds.minimum_class_support
        else None
    )
    if (
        covered_rate is not None
        and draw.recommended_draw_inclusion_rate is not None
        and draw.recommended_draw_inclusion_rate > 0.0
        and covered_rate < thresholds.draw_coverage_rate_attention
    ):
        direct_codes.append("draw_inclusion_coverage_low")
        direct_levels.append("注意")
    if not anomalies and not direct_codes:
        return None
    evidence = list(_anomaly_evidence(anomalies))
    evidence.extend(
        (
            _evidence("実引分率", draw.actual_draw_rate, None, "pt", "Version8-B引分診断"),
            _evidence("本命0率", draw.favorite_draw_rate, draw.actual_draw_rate, "pt", "Version8-B引分診断"),
            _evidence("平均P(0)", draw.mean_probability_0, draw.actual_draw_rate, "pt", "Version8-B引分診断"),
            _evidence("引分Recall", draw.recall, None, "pt", "Version8-B引分診断"),
            _evidence("引分F1", draw.f1_score, None, "pt", "Version8-B引分診断"),
            _evidence("引分Brier", draw.brier_score, None, "", "Version8-B引分診断"),
            _evidence("引分Calibration", draw.calibration_error, None, "pt", "Version8-B引分診断"),
            _evidence(
                "AI推奨での実引分カバー率",
                covered_rate,
                thresholds.draw_coverage_rate_attention,
                "pt",
                "Version8-B引分・買い目診断",
            ),
        )
    )
    causes: list[str] = []
    if (
        draw.mean_probability_0 is not None
        and draw.actual_draw_rate is not None
        and draw.favorite_draw_rate is not None
        and abs(draw.mean_probability_0 - draw.actual_draw_rate)
        <= thresholds.draw_probability_close_tolerance
        and draw.actual_draw_rate - draw.favorite_draw_rate
        >= report.thresholds.draw_favorite_gap_attention
    ):
        causes.append(
            "P(0)の平均水準は実引分率に近い一方、argmaxで0が本命になりにくい可能性があります。"
        )
    else:
        causes.append(
            "引分確率と1・2確率の相対関係に偏りがある可能性があります。"
        )
    if (
        draw.recommended_draw_inclusion_rate is not None
        and draw.favorite_draw_rate is not None
        and draw.recommended_draw_inclusion_rate - draw.favorite_draw_rate
        >= thresholds.draw_inclusion_gap_attention
    ):
        causes.append(
            "0は買い目候補へ入っていても、本命判定には反映されにくい可能性があります。"
        )
    if (
        draw.actual_draw_count > 0
        and draw.recommended_draw_inclusion_rate is not None
        and draw.recommended_draw_covered_count / draw.actual_draw_count
        < thresholds.draw_coverage_rate_attention
    ):
        causes.append(
            "0を含む買い目の選択位置が、実際の引分試合と一致していない可能性があります。"
        )
    if zero is not None and zero.calibration_error is not None:
        causes.append(
            "引分クラスの確率較正が本命選択へ影響している可能性があります。"
        )
    return _Draft(
        code="draw_performance_review",
        category="引分性能",
        related_categories=("0予測",),
        title="引分性能の優先再検証",
        evidence=_unique_evidence(evidence),
        diagnosis="引分の本命選択、Recall、F1または確率品質に低下・乖離があります。",
        possible_causes=_unique(causes),
        improvement_candidates=(
            "引分関連パラメータの再検証",
            "Version7-Bで引分関連パラメータを含む再最適化の検討",
            "引分候補閾値の再検証",
            "Draw Inclusion条件の再検証",
        ),
        recommended_action="引分関連パラメータと候補判定を優先検証",
        levels=(*_levels(anomalies), *direct_levels),
        anomaly_codes=(*_codes(anomalies), *direct_codes),
        global_scope=True,
    )


def _probability_quality_draft(report: DiagnosticReport) -> Optional[_Draft]:
    families = {
        "Brier Score": _anomalies(report, ("brier_increase_",)),
        "Log Loss": _anomalies(report, ("log_loss_increase_",)),
        "Calibration": _anomalies(
            report,
            ("calibration_increase_", "high_probability_accuracy_low"),
        ),
    }
    active = {name: values for name, values in families.items() if values}
    if not active:
        return None
    anomalies = tuple(item for values in active.values() for item in values)
    related = tuple(active)
    category = related[0] if len(related) == 1 else "モデル全体"
    diagnosis_parts = []
    improvements = []
    causes = []
    if "Brier Score" in active:
        diagnosis_parts.append("確率予測全体の精度が悪化しています")
        improvements.extend(("モデル確率精度の再確認", "特定結果・リーグの偏り確認"))
    if "Log Loss" in active:
        diagnosis_parts.append("誤った高信頼予測が増えている可能性があります")
        causes.append("誤り試合へ高い確率を割り当てている可能性があります。")
        improvements.append("高信頼予測の誤り分布確認")
    evidence = list(_anomaly_evidence(anomalies))
    if "Calibration" in active:
        diagnosis_parts.append("予測確率と実発生率の対応が悪化しています")
        calibration_causes, calibration_evidence = _calibration_details(report)
        causes.extend(calibration_causes)
        evidence.extend(calibration_evidence)
        improvements.extend(("Calibrationの再確認", "特定クラスの補正再確認"))
    causes.append("補正係数の組み合わせが直近データへ適合していない可能性があります。")
    improvements.append("Version7-Bの既存範囲による補正係数再最適化の検討")
    return _Draft(
        code="probability_quality_review",
        category=category,
        related_categories=related,
        title="モデル確率精度の再検証",
        evidence=_unique_evidence(evidence),
        diagnosis="。".join(diagnosis_parts) + "。",
        possible_causes=_unique(causes),
        improvement_candidates=_unique(improvements),
        recommended_action="確率品質と高信頼予測をまとめて再検証",
        levels=_levels(anomalies),
        anomaly_codes=_codes(anomalies),
        global_scope=True,
    )


def _class_draft(report: DiagnosticReport, outcome: str) -> Optional[_Draft]:
    anomalies = _anomalies(report, (f"low_recall_{outcome}",))
    if not anomalies:
        return None
    item = report.class_metrics[outcome]
    evidence = list(_anomaly_evidence(anomalies))
    evidence.extend(
        (
            _evidence(f"{outcome} Precision", item.precision, None, "pt", "Version8-B結果別診断"),
            _evidence(f"{outcome} Recall", item.recall, None, "pt", "Version8-B結果別診断"),
            _evidence(f"{outcome} F1", item.f1_score, None, "pt", "Version8-B結果別診断"),
            _evidence(f"{outcome} Brier", item.brier_score, None, "", "Version8-B結果別診断"),
            _evidence(f"{outcome} Calibration", item.calibration_error, None, "pt", "Version8-B結果別診断"),
        )
    )
    return _Draft(
        code=f"class_{outcome}_review",
        category=f"{outcome}予測",
        related_categories=(),
        title=f"結果{outcome}クラスの再検証",
        evidence=_unique_evidence(evidence),
        diagnosis=f"実際に結果{outcome}となった試合を十分に本命選択できていません。",
        possible_causes=(
            f"結果{outcome}の確率が他クラスよりargmaxになりにくい可能性があります。",
            f"結果{outcome}に関係する補正・特徴量が直近試合へ適合していない可能性があります。",
        ),
        improvement_candidates=(
            f"結果{outcome}クラスの補正・特徴量の優先確認",
            f"結果{outcome}を含むVersion7-B再最適化の検討",
        ),
        recommended_action=f"結果{outcome}クラスの誤り分布を優先検証",
        levels=_levels(anomalies),
        anomaly_codes=_codes(anomalies),
    )


def _league_drafts(report: DiagnosticReport) -> list[_Draft]:
    grouped: dict[str, list[DiagnosticAnomaly]] = {}
    for item in report.anomalies:
        if not item.code.startswith("league_") or "_gap_" not in item.code:
            continue
        league = item.code.rsplit("_", 1)[-1]
        if league in ("J1", "J2", "J3"):
            grouped.setdefault(league, []).append(item)
    result = []
    for league, values in sorted(grouped.items()):
        anomalies = tuple(values)
        metrics = "、".join(dict.fromkeys(item.metric for item in anomalies))
        result.append(
            _Draft(
                code=f"league_{league}_review",
                category="リーグ別性能",
                related_categories=(),
                title=f"{league}の性能を限定再検証",
                evidence=_anomaly_evidence(anomalies),
                diagnosis=f"{league}の{metrics}が全リーグ基準より悪い状態です。",
                possible_causes=(
                    f"{league}の対象期間・試合構成が他リーグと異なる可能性があります。",
                    f"共通補正が{league}の実戦データへ適合していない可能性があります。",
                ),
                improvement_candidates=(
                    f"{league}データに限定した誤り分布の再確認",
                    f"{league}に限定した既存バックテストの実行検討",
                    "リーグ別補正の必要性確認",
                ),
                recommended_action=f"{league}を優先して限定バックテストを検討",
                levels=_levels(anomalies),
                anomaly_codes=_codes(anomalies),
            )
        )
    return result


def _bet_draft(
    report: DiagnosticReport,
    thresholds: ImprovementThresholds,
) -> Optional[_Draft]:
    recommended = _bet_row(report.bet_summary, "AI推奨（simulation）")
    purchased = _bet_row(report.bet_summary, "実購入（actual）")
    evidence: list[RecommendationEvidence] = []
    diagnoses: list[str] = []
    causes: list[str] = []
    improvements: list[str] = []
    levels: list[str] = []
    codes: list[str] = []
    coverage_signal = False

    if recommended is not None:
        evaluated = _number(recommended.get("評価済み買い目数")) or 0.0
        average_coverage = _number(recommended.get("平均Coverage"))
        if (
            evaluated >= thresholds.minimum_bet_evaluated_run_count
            and average_coverage is not None
            and average_coverage < thresholds.low_average_coverage_attention
        ):
            coverage_signal = True
            codes.append("low_average_coverage")
            levels.append("注意")
            diagnoses.append("AI推奨買い目の平均Coverageが低い状態です")
            evidence.append(
                _evidence(
                    "AI推奨平均Coverage",
                    average_coverage,
                    thresholds.low_average_coverage_attention,
                    "pt",
                    "Version8-B買い目診断",
                )
            )
            improvements.append("Coverage効率の再検証")

    coverage_efficiency = _coverage_efficiency(report.coverage_summary, thresholds)
    if coverage_efficiency is not None:
        coverage_signal = True
        codes.append("coverage_efficiency_low")
        levels.append("注意")
        diagnoses.append("Coverage帯を上げても完全カバー率の改善が限定的です")
        evidence.append(coverage_efficiency)
        causes.append("口数の増加先と実結果の分岐位置が一致していない可能性があります。")
        improvements.extend(("ダブル配分の再確認", "トリプル配分の再確認"))

    actual = report.purchase_performance
    simulation = report.simulation_performance
    actual_roi = _number(actual.get("roi")) if actual.get("has_evaluated_records") else None
    actual_runs = _number(actual.get("evaluated_run_count")) or 0.0
    simulation_roi = (
        _number(simulation.get("roi"))
        if simulation.get("has_evaluated_records")
        else None
    )
    if (
        actual_roi is not None
        and actual_runs >= thresholds.minimum_bet_evaluated_run_count
        and actual_roi < thresholds.roi_break_even
    ):
        codes.append("actual_roi_low")
        levels.append("警告" if actual_roi < thresholds.roi_warning else "注意")
        diagnoses.append("実購入ROIが損益分岐を下回っています")
        evidence.append(
            _evidence(
                "実購入ROI",
                actual_roi,
                thresholds.roi_break_even,
                "pt",
                "Version8-B実購入成績",
            )
        )
        improvements.append("買い目戦略バックテストの再実行検討")
    if (
        actual_roi is not None
        and simulation_roi is not None
        and simulation_roi - actual_roi
        >= thresholds.simulation_actual_roi_gap_attention
    ):
        codes.append("simulation_actual_roi_gap")
        levels.append("注意")
        diagnoses.append("simulationと実購入のROIに差があります")
        evidence.append(
            _evidence(
                "実購入ROI",
                actual_roi,
                simulation_roi,
                "pt",
                "Version8-B推奨・実購入比較",
            )
        )
        causes.append("AI推奨から実購入への選択差が成績差へ影響している可能性があります。")

    if recommended is not None and purchased is not None:
        recommended_coverage = _number(recommended.get("平均Coverage"))
        actual_coverage = _number(purchased.get("平均Coverage"))
        recommended_tickets = _number(recommended.get("平均口数"))
        actual_tickets = _number(purchased.get("平均口数"))
        coverage_gap = (
            abs(recommended_coverage - actual_coverage)
            if recommended_coverage is not None and actual_coverage is not None
            else 0.0
        )
        ticket_gap = (
            abs(recommended_tickets - actual_tickets)
            if recommended_tickets is not None and actual_tickets is not None
            else 0.0
        )
        if (
            coverage_gap >= thresholds.recommended_actual_coverage_gap_attention
            or ticket_gap >= thresholds.recommended_actual_ticket_gap_attention
        ):
            codes.append("recommended_actual_plan_gap")
            levels.append("注意")
            diagnoses.append("AI推奨と実購入の買い目構成に差があります")
            if recommended_coverage is not None and actual_coverage is not None:
                evidence.append(
                    _evidence(
                        "実購入平均Coverage",
                        actual_coverage,
                        recommended_coverage,
                        "pt",
                        "Version8-B推奨・実購入比較",
                    )
                )
            causes.append("購入時の手動調整がCoverageや結果分岐へ影響している可能性があります。")

    if not codes:
        return None
    improvements.extend(
        (
            "引分を含める優先順位の再確認",
            "買い目戦略の保存済みsimulation比較",
        )
    )
    category = "Coverage" if coverage_signal and not any("roi" in code for code in codes) else "買い目戦略"
    related = ("Coverage",) if category == "買い目戦略" and coverage_signal else ()
    return _Draft(
        code="bet_strategy_review",
        category=category,
        related_categories=related,
        title="買い目とCoverage効率の再検証",
        evidence=_unique_evidence(evidence),
        diagnosis="。".join(diagnoses) + "。",
        possible_causes=_unique(causes or ["原因は診断結果だけでは特定できません。"]),
        improvement_candidates=_unique(improvements),
        recommended_action="買い目戦略バックテストとCoverage効率を再検証",
        levels=tuple(levels),
        anomaly_codes=tuple(codes),
    )


def _calibration_details(
    report: DiagnosticReport,
) -> tuple[tuple[str, ...], tuple[RecommendationEvidence, ...]]:
    frame = report.calibration_table
    if frame.empty:
        return ("原因は診断結果だけでは特定できません。",), ()
    valid = frame.copy()
    valid["_gap"] = pd.to_numeric(valid.get("Calibration差"), errors="coerce")
    valid["_mean"] = pd.to_numeric(valid.get("平均予測確率"), errors="coerce")
    valid["_actual"] = pd.to_numeric(valid.get("実発生率"), errors="coerce")
    valid = valid.dropna(subset=["_gap", "_mean", "_actual"])
    if valid.empty:
        return ("原因は診断結果だけでは特定できません。",), ()
    row = valid.sort_values("_gap", ascending=False).iloc[0]
    outcome = str(row.get("結果", ""))
    band = str(row.get("確率帯", ""))
    mean = float(row["_mean"])
    actual = float(row["_actual"])
    if mean > actual:
        cause = f"結果{outcome}の{band}帯で過信が影響している可能性があります。"
    else:
        cause = f"結果{outcome}の{band}帯で過小評価が影響している可能性があります。"
    return (
        (cause,),
        (
            RecommendationEvidence(
                metric=f"結果{outcome} {band} Calibration",
                current_value=mean,
                baseline_value=actual,
                difference=mean - actual,
                unit="pt",
                source="Version8-B確率帯診断",
            ),
        ),
    )


def _coverage_efficiency(
    frame: pd.DataFrame,
    thresholds: ImprovementThresholds,
) -> Optional[RecommendationEvidence]:
    if frame.empty:
        return None
    recommended = frame.loc[
        (frame["区分"].astype(str) == "AI推奨")
        & (frame["状態"].astype(str) == "診断可能")
    ].copy()
    recommended["_rate"] = pd.to_numeric(
        recommended.get("完全カバー率"),
        errors="coerce",
    )
    recommended = recommended.dropna(subset=["_rate"])
    if len(recommended) < 2:
        return None
    low = float(recommended.iloc[0]["_rate"])
    high = float(recommended.iloc[-1]["_rate"])
    if high - low >= thresholds.coverage_efficiency_minimum_gain:
        return None
    return RecommendationEvidence(
        metric="高Coverage帯の完全カバー率",
        current_value=high,
        baseline_value=low,
        difference=high - low,
        unit="pt",
        source="Version8-B Coverage帯診断",
    )


def _priority(
    draft: _Draft,
    thresholds: ImprovementThresholds,
) -> tuple[str, str]:
    level_score = max(
        ({"情報": 1, "注意": 2, "警告": 3}.get(level, 1) for level in draft.levels),
        default=1,
    )
    agreement = 1 if len(set(draft.anomaly_codes)) >= 2 else 0
    scope = 1 if draft.global_scope else 0
    score = level_score + agreement + scope
    if score >= thresholds.priority_high_score:
        level = "高"
    elif score >= thresholds.priority_medium_score:
        level = "中"
    else:
        level = "低"
    reason = (
        f"最大異常レベル={_highest_level(draft.levels)}、"
        f"関連指標={len(set(draft.anomaly_codes))}件、"
        f"影響範囲={'全体' if draft.global_scope else '限定'}の固定ルールです。"
    )
    return level, reason


def _confidence(
    report: DiagnosticReport,
    draft: _Draft,
    thresholds: ImprovementThresholds,
) -> tuple[str, str]:
    if not report.data_sufficient:
        return "低", "Version8-Bがデータ不足と判定しているため低です。"
    if (
        report.counts.match_count >= thresholds.high_confidence_match_count
        and report.counts.round_count >= thresholds.high_confidence_round_count
    ):
        sample_score = 2
        sample_label = "高サンプル"
    else:
        sample_score = 1
        sample_label = "最低基準充足"
    severity_score = int("警告" in draft.levels)
    agreement_score = int(len(set(draft.anomaly_codes)) >= 2)
    rolling_score = int(_rolling_agreement(draft.anomaly_codes))
    score = sample_score + severity_score + agreement_score + rolling_score
    if score >= thresholds.confidence_high_score:
        level = "高"
    elif score >= thresholds.confidence_medium_score:
        level = "中"
    else:
        level = "低"
    reason = (
        f"{report.counts.match_count}試合・{report.counts.round_count}開催（{sample_label}）、"
        f"警告一致={severity_score}、複数指標一致={agreement_score}、"
        f"直近5/10一致={rolling_score}の固定ルールです。"
    )
    return level, reason


def _reoptimization(
    report: DiagnosticReport,
    thresholds: ImprovementThresholds,
) -> tuple[str, str]:
    if not report.data_sufficient:
        return (
            "データ不足",
            f"{report.counts.match_count}試合・{report.counts.round_count}開催のため、"
            "Version8-Bの最低基準を満たしていません。",
        )
    performance = [
        item for item in report.anomalies if item.category == "モデル性能"
    ]
    warning_count = sum(item.level == "警告" for item in performance)
    attention_count = sum(item.level == "注意" for item in performance)
    families = {_anomaly_family(item.code) for item in performance}
    if (
        warning_count >= thresholds.reoptimization_recommended_warning_count
        or len(families) >= thresholds.reoptimization_recommended_family_count
    ):
        return (
            "推奨",
            f"モデル性能の警告{warning_count}件、異常系統{len(families)}種類が同時に確認されています。",
        )
    if (
        warning_count >= 1
        or attention_count >= thresholds.reoptimization_consider_attention_count
    ):
        return (
            "検討",
            f"モデル性能の警告{warning_count}件、注意{attention_count}件を確認しています。",
        )
    return "不要", "再最適化を検討する複数のモデル性能悪化は確認されませんでした。"


def _detected_problems(
    report: DiagnosticReport,
    recommendations: Sequence[ImprovementRecommendation],
) -> tuple[str, ...]:
    values = [
        f"{item.name}：{item.message}"
        for item in report.anomalies
        if item.category == "モデル性能"
        and item.level in ("注意", "警告")
    ]
    values.extend(
        f"データ品質：{item.message}" for item in report.quality_issues
    )
    if not report.data_sufficient:
        values.insert(0, f"データ不足：{report.status_reason}")
    diagnostic_codes = {item.code for item in report.anomalies}
    values.extend(
        f"{item.title}：{item.diagnosis}"
        for item in recommendations
        if item.diagnosis
        and any(code not in diagnostic_codes for code in item.anomaly_codes)
    )
    return _unique(values)


def _anomalies(
    report: DiagnosticReport,
    prefixes: Sequence[str],
) -> tuple[DiagnosticAnomaly, ...]:
    return tuple(
        item
        for item in report.anomalies
        if item.category == "モデル性能"
        and any(item.code == prefix or item.code.startswith(prefix) for prefix in prefixes)
    )


def _anomaly_evidence(
    anomalies: Sequence[DiagnosticAnomaly],
) -> tuple[RecommendationEvidence, ...]:
    return tuple(
        RecommendationEvidence(
            metric=item.metric,
            current_value=_number(item.current_value),
            baseline_value=_number(item.baseline_value),
            difference=_number(item.difference),
            unit=item.unit,
            source=f"Version8-B異常:{item.code}",
        )
        for item in anomalies
    )


def _evidence(
    metric: str,
    current: Any,
    baseline: Any,
    unit: str,
    source: str,
) -> RecommendationEvidence:
    current_value = _number(current)
    baseline_value = _number(baseline)
    return RecommendationEvidence(
        metric=metric,
        current_value=current_value,
        baseline_value=baseline_value,
        difference=(
            current_value - baseline_value
            if current_value is not None and baseline_value is not None
            else None
        ),
        unit=unit,
        source=source,
    )


def _bet_row(frame: pd.DataFrame, label: str) -> Optional[pd.Series]:
    if frame.empty or "区分" not in frame.columns:
        return None
    selected = frame.loc[frame["区分"].astype(str) == label]
    return selected.iloc[0] if not selected.empty else None


def _deduplicate_drafts(drafts: Sequence[_Draft]) -> list[_Draft]:
    result = []
    seen = set()
    for draft in drafts:
        if draft.code in seen:
            continue
        seen.add(draft.code)
        result.append(draft)
    return result


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if str(value).strip()))


def _unique_evidence(
    values: Sequence[RecommendationEvidence],
) -> tuple[RecommendationEvidence, ...]:
    result = []
    seen = set()
    for item in values:
        key = (item.metric, item.source, item.current_value, item.baseline_value)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _levels(anomalies: Sequence[DiagnosticAnomaly]) -> tuple[str, ...]:
    return tuple(item.level for item in anomalies)


def _codes(anomalies: Sequence[DiagnosticAnomaly]) -> tuple[str, ...]:
    return tuple(item.code for item in anomalies)


def _highest_level(levels: Sequence[str]) -> str:
    order = {"情報": 0, "注意": 1, "警告": 2}
    return max(levels, key=lambda value: order.get(value, 0), default="情報")


def _rolling_agreement(codes: Sequence[str]) -> bool:
    prefixes = set()
    for code in codes:
        if code.endswith("_直近5開催"):
            prefixes.add(code.removesuffix("_直近5開催"))
    return any(f"{prefix}_直近10開催" in codes for prefix in prefixes)


def _anomaly_family(code: str) -> str:
    for prefix in (
        "accuracy_drop",
        "brier_increase",
        "log_loss_increase",
        "calibration_increase",
        "draw_f1_drop",
        "draw_favorite_rate_gap",
        "low_recall",
        "high_probability_accuracy_low",
        "league_",
    ):
        if code.startswith(prefix):
            return prefix.rstrip("_")
    return code


def _recommendation_sort_key(item: ImprovementRecommendation) -> tuple[int, int, int, str]:
    priority = {"高": 0, "中": 1, "低": 2}.get(item.priority, 3)
    confidence = {"高": 0, "中": 1, "低": 2}.get(item.confidence, 3)
    categories = {
        category: index for index, category in enumerate(IMPROVEMENT_CATEGORIES)
    }
    return priority, confidence, categories.get(item.category, 99), item.code


def _template_narrative(draft: _Draft) -> str:
    causes = " / ".join(draft.possible_causes)
    candidates = " / ".join(draft.improvement_candidates)
    return (
        f"診断：{draft.diagnosis} 原因候補：{causes} "
        f"改善候補：{candidates} 推奨アクション：{draft.recommended_action}"
    )


def _format_narrative(
    template: str,
    formatter: Optional[RecommendationTextFormatter],
) -> tuple[str, bool]:
    if formatter is None:
        return template, False
    try:
        candidate = formatter.format(template)
    except Exception:
        return template, False
    if not isinstance(candidate, str):
        return template, False
    candidate = candidate.strip()
    if not candidate or len(candidate) > 1200:
        return template, False
    allowed_numbers = set(re.findall(r"[+-]?\d+(?:\.\d+)?%?", template))
    generated_numbers = set(re.findall(r"[+-]?\d+(?:\.\d+)?%?", candidate))
    if generated_numbers - allowed_numbers:
        return template, False
    forbidden = ("変更しました", "適用しました", "実行しました", "採用しました")
    if any(value in candidate for value in forbidden):
        return template, False
    return candidate, True


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


__all__ = [
    "ImprovementRecommendation",
    "ImprovementReport",
    "RecommendationEvidence",
    "RecommendationTextFormatter",
    "generate_improvement_recommendations",
]
