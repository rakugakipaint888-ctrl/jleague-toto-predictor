"""Version7-Bの正規化Score、過学習・引分保護・安定性評価。"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from version7b_config import (
    VERSION7B_DEFAULT_EVALUATION_WEIGHTS,
    VERSION7B_DRAW_DEGRADATION_TOLERANCES,
    VERSION7B_OVERFIT_THRESHOLDS,
)
from draw_evaluation import DrawEvaluation, evaluate_draw_predictions

EVALUATION_KEYS = (
    "brier_score",
    "log_loss",
    "calibration",
    "accuracy",
    "draw_performance",
    "validation_stability",
)


@dataclass(frozen=True)
class EvaluationWeights:
    brier_score: float = 0.30
    log_loss: float = 0.20
    calibration: float = 0.15
    accuracy: float = 0.15
    draw_performance: float = 0.10
    validation_stability: float = 0.10

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "EvaluationWeights":
        converted = {
            key: float(values.get(key, VERSION7B_DEFAULT_EVALUATION_WEIGHTS[key]))
            for key in EVALUATION_KEYS
        }
        result = cls(**converted)
        result.validate()
        return result

    def validate(self) -> None:
        values = self.as_dict()
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("評価重みは0以上の有限値にしてください。")
        if sum(values.values()) <= 0:
            raise ValueError("評価重みを1項目以上設定してください。")

    def as_dict(self) -> dict[str, float]:
        return {key: float(getattr(self, key)) for key in EVALUATION_KEYS}

    @property
    def total(self) -> float:
        return sum(self.as_dict().values())

    def normalized(self) -> dict[str, float]:
        self.validate()
        total = self.total
        return {key: value / total for key, value in self.as_dict().items()}

    @property
    def totals_one_hundred_percent(self) -> bool:
        # 0～1入力と0～100入力のどちらもUI・APIから受けられる。
        return math.isclose(self.total, 1.0, abs_tol=1e-9) or math.isclose(
            self.total, 100.0, abs_tol=1e-9
        )


DEFAULT_EVALUATION_WEIGHTS = EvaluationWeights.from_mapping(
    VERSION7B_DEFAULT_EVALUATION_WEIGHTS
)


@dataclass(frozen=True)
class PredictionRow:
    round_id: int
    match_number: int
    cutoff_at: datetime
    season: str
    league: str
    prediction: str
    probabilities: Mapping[str, float]
    actual_result: str
    draw_candidate: bool


@dataclass(frozen=True)
class CandidateEvaluation:
    score: float
    draw_evaluation: DrawEvaluation
    component_scores: Mapping[str, float]
    fold_scores: tuple[float, ...]
    stability_quality: float
    rows: tuple[PredictionRow, ...]
    roi: Optional[float] = None

    @property
    def metrics(self):
        return self.draw_evaluation.overall

    @property
    def draw(self):
        return self.draw_evaluation.draw


@dataclass(frozen=True)
class DrawDegradationCheck:
    degraded: bool
    penalty: float
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return "引分性能悪化" if self.degraded else "引分性能維持"


@dataclass(frozen=True)
class OverfittingCheck:
    is_overfitting: bool
    score_gap: float
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return "過学習の可能性" if self.is_overfitting else "過学習の兆候なし"


@dataclass(frozen=True)
class StabilitySummary:
    season_scores: Mapping[str, float]
    league_scores: Mapping[str, float]
    warnings: tuple[str, ...]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _quality(value: Optional[float], maximum: float = 1.0) -> float:
    if value is None or not math.isfinite(float(value)):
        return 0.0
    return _clamp01(1.0 - float(value) / maximum)


def draw_performance_quality(evaluation: DrawEvaluation) -> float:
    draw = evaluation.draw
    # Version7-Aの全引分指標を0～1へ揃え、F1/Recallをやや重くする。
    return _clamp01(
        0.35 * draw.f1_score
        + 0.15 * draw.precision
        + 0.20 * draw.recall
        + 0.15 * _quality(draw.brier_score)
        + 0.15 * _quality(draw.calibration_error)
    )


def _component_scores(
    evaluation: DrawEvaluation,
    stability_quality: float,
) -> dict[str, float]:
    overall = evaluation.overall
    return {
        # 多クラスBrierは0～2。
        "brier_score": _quality(overall.brier_score, maximum=2.0),
        # 上限のないLog Lossはexp(-loss)で単調に0～1へ変換する。
        "log_loss": (
            _clamp01(math.exp(-float(overall.log_loss)))
            if overall.log_loss is not None and math.isfinite(overall.log_loss)
            else 0.0
        ),
        "calibration": _quality(overall.calibration_error),
        "accuracy": _clamp01(float(overall.accuracy or 0.0)),
        "draw_performance": draw_performance_quality(evaluation),
        "validation_stability": _clamp01(stability_quality),
    }


def _score_from_components(
    components: Mapping[str, float],
    weights: EvaluationWeights,
) -> float:
    normalized = weights.normalized()
    return 100.0 * sum(
        _clamp01(float(components[key])) * normalized[key] for key in EVALUATION_KEYS
    )


def _draw_evaluation(rows: Sequence[PredictionRow]) -> DrawEvaluation:
    if not rows:
        raise ValueError("評価対象が0試合です。")
    return evaluate_draw_predictions(
        [row.prediction for row in rows],
        [row.probabilities for row in rows],
        [row.actual_result for row in rows],
        candidate_flags=[row.draw_candidate for row in rows],
    )


def _base_score(
    rows: Sequence[PredictionRow],
    weights: EvaluationWeights,
) -> float:
    evaluation = _draw_evaluation(rows)
    components = _component_scores(evaluation, 1.0)
    return _score_from_components(components, weights)


def stability_quality(
    fold_scores: Sequence[float],
    reference_score: Optional[float] = None,
) -> float:
    """Fold間ばらつきと全Trainingとの差を0～1の品質へ変換する。"""

    finite_scores = [float(value) for value in fold_scores if math.isfinite(value)]
    if not finite_scores:
        return 0.0
    standard_deviation = (
        statistics.pstdev(finite_scores) if len(finite_scores) >= 2 else 0.0
    )
    mean_score = statistics.fmean(finite_scores)
    optimistic_gap = max(0.0, float(reference_score or mean_score) - mean_score)
    return _clamp01(1.0 - standard_deviation / 20.0 - optimistic_gap / 50.0)


def evaluate_candidate_rows(
    rows: Sequence[PredictionRow],
    *,
    weights: EvaluationWeights = DEFAULT_EVALUATION_WEIGHTS,
    fold_rows: Sequence[Sequence[PredictionRow]] = (),
    roi: Optional[float] = None,
) -> CandidateEvaluation:
    """単位の異なる生指標を正規化し、0～100の総合Scoreへ変換する。"""

    evaluation = _draw_evaluation(rows)
    provisional = _component_scores(evaluation, 1.0)
    reference_score = _score_from_components(provisional, weights)
    folds = tuple(_base_score(fold, weights) for fold in fold_rows if fold)
    quality = stability_quality(folds, reference_score) if fold_rows else 1.0
    components = _component_scores(evaluation, quality)
    return CandidateEvaluation(
        score=_score_from_components(components, weights),
        draw_evaluation=evaluation,
        component_scores=components,
        fold_scores=folds,
        stability_quality=quality,
        rows=tuple(rows),
        roi=roi,
    )


def check_draw_degradation(
    baseline: CandidateEvaluation,
    candidate: CandidateEvaluation,
    tolerances: Mapping[str, float] = VERSION7B_DRAW_DEGRADATION_TOLERANCES,
) -> DrawDegradationCheck:
    """Version7-A比の引分悪化を理由付きで検出し、選択Scoreへペナルティ化する。"""

    base = baseline.draw
    current = candidate.draw
    checks = (
        (
            "引分F1",
            max(0.0, base.f1_score - current.f1_score),
            float(tolerances["draw_f1_drop"]),
        ),
        (
            "引分Recall",
            max(0.0, base.recall - current.recall),
            float(tolerances["draw_recall_drop"]),
        ),
        (
            "引分Brier",
            max(
                0.0, float(current.brier_score or 0.0) - float(base.brier_score or 0.0)
            ),
            float(tolerances["draw_brier_increase"]),
        ),
        (
            "引分Calibration",
            max(
                0.0,
                float(current.calibration_error or 0.0)
                - float(base.calibration_error or 0.0),
            ),
            float(tolerances["draw_calibration_increase"]),
        ),
    )
    reasons = []
    penalty = 0.0
    for label, degradation, allowance in checks:
        if allowance < 0:
            raise ValueError("引分性能の許容悪化幅は0以上にしてください。")
        excess = max(0.0, degradation - allowance)
        if excess > 0:
            reasons.append(
                f"{label}の悪化幅{degradation:.4f}が許容幅{allowance:.4f}を超えました。"
            )
            penalty += min(12.5, excess / max(allowance, 0.01) * 5.0)
    return DrawDegradationCheck(bool(reasons), min(50.0, penalty), tuple(reasons))


def _increase(training: Optional[float], validation: Optional[float]) -> float:
    if training is None or validation is None:
        return 0.0
    return float(validation) - float(training)


def check_overfitting(
    training: CandidateEvaluation,
    validation: CandidateEvaluation,
    thresholds: Mapping[str, float] = VERSION7B_OVERFIT_THRESHOLDS,
) -> OverfittingCheck:
    """Trainingだけ良い候補を8指標で警告する。"""

    score_gap = training.score - validation.score
    reasons = []
    if score_gap > float(thresholds["score_gap"]):
        reasons.append(f"TrainingとValidationのScore差が{score_gap:.2f}あります。")
    checks = (
        (
            "Brier Score",
            _increase(training.metrics.brier_score, validation.metrics.brier_score),
            float(thresholds["brier_increase"]),
        ),
        (
            "Log Loss",
            _increase(training.metrics.log_loss, validation.metrics.log_loss),
            float(thresholds["log_loss_increase"]),
        ),
        (
            "Calibration",
            _increase(
                training.metrics.calibration_error,
                validation.metrics.calibration_error,
            ),
            float(thresholds["calibration_increase"]),
        ),
        (
            "全体的中率",
            float(training.metrics.accuracy or 0.0)
            - float(validation.metrics.accuracy or 0.0),
            float(thresholds["accuracy_drop"]),
        ),
        (
            "引分F1",
            training.draw.f1_score - validation.draw.f1_score,
            float(thresholds["draw_f1_drop"]),
        ),
        (
            "引分Brier",
            _increase(training.draw.brier_score, validation.draw.brier_score),
            float(thresholds["draw_brier_increase"]),
        ),
        (
            "引分Calibration",
            _increase(
                training.draw.calibration_error,
                validation.draw.calibration_error,
            ),
            float(thresholds["draw_calibration_increase"]),
        ),
    )
    for label, degradation, threshold in checks:
        if degradation > threshold:
            reasons.append(
                f"Validationの{label}がTrainingから{degradation:.4f}悪化しています。"
            )
    return OverfittingCheck(bool(reasons), score_gap, tuple(reasons))


def grouped_score(
    rows: Sequence[PredictionRow],
    field_name: str,
    weights: EvaluationWeights = DEFAULT_EVALUATION_WEIGHTS,
) -> dict[str, float]:
    groups: dict[str, list[PredictionRow]] = {}
    for row in rows:
        key = str(getattr(row, field_name, "") or "不明")
        groups.setdefault(key, []).append(row)
    return {
        key: evaluate_candidate_rows(group, weights=weights).score
        for key, group in groups.items()
        if group
    }


def build_stability_summary(
    rows: Sequence[PredictionRow],
    weights: EvaluationWeights = DEFAULT_EVALUATION_WEIGHTS,
    *,
    league_rows: Optional[Sequence[PredictionRow]] = None,
    score_range_warning: float = 20.0,
) -> StabilitySummary:
    season_scores = grouped_score(rows, "season", weights)
    league_source = rows if league_rows is None else league_rows
    league_scores = grouped_score(
        [row for row in league_source if row.league in ("J1", "J2", "J3")],
        "league",
        weights,
    )
    warnings = []
    if len(season_scores) >= 2 and (
        max(season_scores.values()) - min(season_scores.values()) > score_range_warning
    ):
        warnings.append("特定シーズンだけ成績が大きく異なります。")
    if len(league_scores) >= 2 and (
        max(league_scores.values()) - min(league_scores.values()) > score_range_warning
    ):
        warnings.append("特定リーグだけ成績が大きく異なります。")
    return StabilitySummary(season_scores, league_scores, tuple(warnings))


def comparison_rows(
    baseline: CandidateEvaluation,
    candidate: CandidateEvaluation,
) -> list[dict[str, Any]]:
    """Version7-A／7-B／差／改善悪化を同一定義で返す。"""

    items = (
        ("総合Score", baseline.score, candidate.score, True),
        (
            "Brier Score",
            baseline.metrics.brier_score,
            candidate.metrics.brier_score,
            False,
        ),
        ("Log Loss", baseline.metrics.log_loss, candidate.metrics.log_loss, False),
        (
            "Calibration",
            baseline.metrics.calibration_error,
            candidate.metrics.calibration_error,
            False,
        ),
        ("全体的中率", baseline.metrics.accuracy, candidate.metrics.accuracy, True),
        (
            "1的中率",
            baseline.metrics.class_accuracy["1"],
            candidate.metrics.class_accuracy["1"],
            True,
        ),
        (
            "0的中率",
            baseline.metrics.class_accuracy["0"],
            candidate.metrics.class_accuracy["0"],
            True,
        ),
        (
            "2的中率",
            baseline.metrics.class_accuracy["2"],
            candidate.metrics.class_accuracy["2"],
            True,
        ),
        ("引分Precision", baseline.draw.precision, candidate.draw.precision, True),
        ("引分Recall", baseline.draw.recall, candidate.draw.recall, True),
        ("引分F1", baseline.draw.f1_score, candidate.draw.f1_score, True),
        ("引分Brier", baseline.draw.brier_score, candidate.draw.brier_score, False),
        (
            "引分Calibration",
            baseline.draw.calibration_error,
            candidate.draw.calibration_error,
            False,
        ),
    )
    rows = []
    for label, before, after, higher_is_better in items:
        difference = (
            float(after) - float(before)
            if before is not None and after is not None
            else None
        )
        if difference is None:
            judgment = "確認できません"
        elif abs(difference) < 1e-12:
            judgment = "同等"
        else:
            improved = difference > 0 if higher_is_better else difference < 0
            judgment = "改善" if improved else "悪化"
        rows.append(
            {
                "項目": label,
                "Version7-A": before,
                "Version7-B候補": after,
                "差": difference,
                "評価": judgment,
            }
        )
    return rows
