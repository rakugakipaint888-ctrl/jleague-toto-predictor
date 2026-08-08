"""Version7-Aの引分専用指標、確率帯評価、総合Score。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from draw_predictor import TOTO_OUTCOMES, normalize_three_way_probabilities
from metrics import ModelMetrics, evaluate_model
from model_config import (
    VERSION7A_ACCURACY_ALLOWANCE,
    VERSION7A_CALIBRATION_ALLOWANCE,
    VERSION7A_LOG_LOSS_ALLOWANCE,
    VERSION7A_OVERALL_BRIER_ALLOWANCE,
)


DRAW_PROBABILITY_BANDS = (
    (0.00, 0.20),
    (0.20, 0.25),
    (0.25, 0.30),
    (0.30, 0.35),
    (0.35, 0.40),
    (0.40, 1.00),
)


@dataclass(frozen=True)
class DrawCalibrationBin:
    lower: float
    upper: float
    count: int
    mean_probability: Optional[float]
    actual_draw_count: int
    actual_draw_rate: Optional[float]
    calibration_gap: Optional[float]

    @property
    def label(self) -> str:
        return (
            f"{self.lower:.0%}以上"
            if self.upper >= 1.0
            else f"{self.lower:.0%}以上{self.upper:.0%}未満"
        )


@dataclass(frozen=True)
class DrawMetrics:
    match_count: int
    actual_draw_count: int
    predicted_draw_count: int
    draw_hit_count: int
    precision: float
    recall: float
    f1_score: float
    brier_score: Optional[float]
    calibration_error: Optional[float]
    mean_probability_when_predicted: Optional[float]
    actual_draw_rate: float
    predicted_draw_rate: float
    candidate_count: int
    candidate_hit_count: int
    candidate_precision: float
    candidate_recall: float
    candidate_f1_score: float
    calibration_bins: tuple[DrawCalibrationBin, ...]


@dataclass(frozen=True)
class DrawEvaluation:
    overall: ModelMetrics
    draw: DrawMetrics
    predictions: tuple[str, ...]
    probabilities: tuple[Mapping[str, float], ...]
    actual_results: tuple[str, ...]
    candidate_flags: tuple[bool, ...]


@dataclass(frozen=True)
class DrawScore:
    score: float
    components: Mapping[str, float]
    degradation_penalty: float


def normalize_toto_label(value: Any) -> str:
    """文字列・整数・CSVの浮動小数から1/0/2を失わず復元する。"""

    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if text in TOTO_OUTCOMES:
        return text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isfinite(number) and number.is_integer():
        label = str(int(number))
        return label if label in TOTO_OUTCOMES else ""
    return ""


def _safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _f1(precision: float, recall: float) -> float:
    total = precision + recall
    return 2.0 * precision * recall / total if total > 0 else 0.0


def _safe_probability(value: Any) -> Optional[float]:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(probability):
        return None
    return min(1.0, max(0.0, probability))


def draw_calibration_bins(
    draw_probabilities: Sequence[float],
    actual_results: Sequence[str],
) -> tuple[tuple[DrawCalibrationBin, ...], Optional[float]]:
    """指定6確率帯を全て返し、非空帯の加重Calibration差を計算する。"""

    valid_rows = []
    for probability_value, actual_value in zip(
        draw_probabilities,
        actual_results,
    ):
        probability = _safe_probability(probability_value)
        actual = normalize_toto_label(actual_value)
        if probability is None or actual not in TOTO_OUTCOMES:
            continue
        valid_rows.append((probability, actual))
    bins = []
    weighted_gap = 0.0
    for lower, upper in DRAW_PROBABILITY_BANDS:
        rows = [
            row
            for row in valid_rows
            if row[0] >= lower
            and (row[0] < upper or (upper >= 1.0 and row[0] <= 1.0))
        ]
        count = len(rows)
        if count:
            mean_probability = sum(row[0] for row in rows) / count
            actual_draw_count = sum(row[1] == "0" for row in rows)
            actual_draw_rate = actual_draw_count / count
            gap = abs(mean_probability - actual_draw_rate)
            weighted_gap += gap * count
        else:
            mean_probability = None
            actual_draw_count = 0
            actual_draw_rate = None
            gap = None
        bins.append(
            DrawCalibrationBin(
                lower=lower,
                upper=upper,
                count=count,
                mean_probability=mean_probability,
                actual_draw_count=actual_draw_count,
                actual_draw_rate=actual_draw_rate,
                calibration_gap=gap,
            )
        )
    return (
        tuple(bins),
        weighted_gap / len(valid_rows) if valid_rows else None,
    )


def evaluate_draw_predictions(
    predictions: Sequence[Any],
    probability_rows: Sequence[Mapping[str, Any]],
    actual_results: Sequence[Any],
    *,
    candidate_flags: Optional[Sequence[bool]] = None,
) -> DrawEvaluation:
    """全体指標と引分Precision/Recall/F1/Brier/Calibrationを返す。"""

    supplied_candidates = list(candidate_flags or ())
    rows = []
    for index, (prediction_value, probabilities, actual_value) in enumerate(
        zip(predictions, probability_rows, actual_results)
    ):
        prediction = normalize_toto_label(prediction_value)
        actual = normalize_toto_label(actual_value)
        if prediction not in TOTO_OUTCOMES or actual not in TOTO_OUTCOMES:
            continue
        normalized = normalize_three_way_probabilities(probabilities)
        candidate = (
            bool(supplied_candidates[index])
            if index < len(supplied_candidates)
            else prediction == "0"
        )
        rows.append((prediction, normalized, actual, candidate))

    valid_predictions = [row[0] for row in rows]
    valid_probabilities = [row[1] for row in rows]
    valid_actuals = [row[2] for row in rows]
    valid_candidates = [row[3] for row in rows]
    overall = evaluate_model(
        valid_predictions,
        valid_probabilities,
        valid_actuals,
    )
    match_count = len(rows)
    actual_draw_count = sum(actual == "0" for actual in valid_actuals)
    predicted_draw_count = sum(prediction == "0" for prediction in valid_predictions)
    draw_hit_count = sum(
        prediction == actual == "0"
        for prediction, actual in zip(valid_predictions, valid_actuals)
    )
    precision = _safe_divide(draw_hit_count, predicted_draw_count)
    recall = _safe_divide(draw_hit_count, actual_draw_count)
    draw_probabilities = [row[1]["0"] for row in rows]
    binary_brier_values = [
        (probability - float(actual == "0")) ** 2
        for probability, actual in zip(draw_probabilities, valid_actuals)
    ]
    bins, calibration_error = draw_calibration_bins(
        draw_probabilities,
        valid_actuals,
    )
    predicted_probabilities = [
        probability
        for probability, prediction in zip(draw_probabilities, valid_predictions)
        if prediction == "0"
    ]
    candidate_count = sum(valid_candidates)
    candidate_hit_count = sum(
        candidate and actual == "0"
        for candidate, actual in zip(valid_candidates, valid_actuals)
    )
    candidate_precision = _safe_divide(candidate_hit_count, candidate_count)
    candidate_recall = _safe_divide(candidate_hit_count, actual_draw_count)

    return DrawEvaluation(
        overall=overall,
        draw=DrawMetrics(
            match_count=match_count,
            actual_draw_count=actual_draw_count,
            predicted_draw_count=predicted_draw_count,
            draw_hit_count=draw_hit_count,
            precision=precision,
            recall=recall,
            f1_score=_f1(precision, recall),
            brier_score=(
                sum(binary_brier_values) / len(binary_brier_values)
                if binary_brier_values
                else None
            ),
            calibration_error=calibration_error,
            mean_probability_when_predicted=(
                sum(predicted_probabilities) / len(predicted_probabilities)
                if predicted_probabilities
                else None
            ),
            actual_draw_rate=_safe_divide(actual_draw_count, match_count),
            predicted_draw_rate=_safe_divide(predicted_draw_count, match_count),
            candidate_count=candidate_count,
            candidate_hit_count=candidate_hit_count,
            candidate_precision=candidate_precision,
            candidate_recall=candidate_recall,
            candidate_f1_score=_f1(candidate_precision, candidate_recall),
            calibration_bins=bins,
        ),
        predictions=tuple(valid_predictions),
        probabilities=tuple(valid_probabilities),
        actual_results=tuple(valid_actuals),
        candidate_flags=tuple(valid_candidates),
    )


def _quality(value: Optional[float], *, maximum: float = 1.0) -> float:
    if value is None or not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, 1.0 - float(value) / maximum))


def score_draw_evaluation(
    evaluation: DrawEvaluation,
    *,
    version6_baseline: Optional[DrawEvaluation] = None,
) -> DrawScore:
    """引分性能と全体品質を両立する0～100 Scoreを返す。"""

    overall = evaluation.overall
    draw = evaluation.draw
    components = {
        "overall_brier": _quality(overall.brier_score, maximum=2.0),
        "overall_log_loss": (
            math.exp(-overall.log_loss)
            if overall.log_loss is not None and math.isfinite(overall.log_loss)
            else 0.0
        ),
        "overall_calibration": _quality(overall.calibration_error),
        "overall_accuracy": float(overall.accuracy or 0.0),
        "draw_f1": draw.f1_score,
        "draw_brier": _quality(draw.brier_score),
        "draw_calibration": _quality(draw.calibration_error),
        # 候補数そのものではなくPrecisionとRecallの調和平均だけを小さく評価する。
        "candidate_f1": draw.candidate_f1_score,
    }
    weights = {
        "overall_brier": 0.18,
        "overall_log_loss": 0.14,
        "overall_calibration": 0.08,
        "overall_accuracy": 0.14,
        "draw_f1": 0.22,
        "draw_brier": 0.10,
        "draw_calibration": 0.09,
        "candidate_f1": 0.05,
    }
    raw_score = 100.0 * sum(
        components[name] * weights[name]
        for name in weights
    )
    penalty = 0.0
    if version6_baseline is not None:
        baseline = version6_baseline.overall
        # F1だけを上げて確率品質を大きく落とすTrialを最良にしない。
        for current, original, allowance, multiplier in (
            (
                overall.brier_score,
                baseline.brier_score,
                VERSION7A_OVERALL_BRIER_ALLOWANCE,
                250.0,
            ),
            (
                overall.log_loss,
                baseline.log_loss,
                VERSION7A_LOG_LOSS_ALLOWANCE,
                120.0,
            ),
            (
                overall.calibration_error,
                baseline.calibration_error,
                VERSION7A_CALIBRATION_ALLOWANCE,
                100.0,
            ),
        ):
            if current is not None and original is not None:
                penalty += max(0.0, current - original - allowance) * multiplier
        if overall.accuracy is not None and baseline.accuracy is not None:
            penalty += max(
                0.0,
                baseline.accuracy
                - overall.accuracy
                - VERSION7A_ACCURACY_ALLOWANCE,
            ) * 100.0
    return DrawScore(
        score=max(0.0, min(100.0, raw_score - penalty)),
        components=components,
        degradation_penalty=penalty,
    )
