"""Version8-Bモデル診断専用の1対その他評価指標。

Version6から継続している共通指標はmetricsから再利用し、診断専用の型と
集計だけをこのモジュールに置く。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from metrics import (
    TOTO_OUTCOMES,
    _normalize_probabilities,
    normalize_toto_outcome,
)


@dataclass(frozen=True)
class ProbabilityBandMetrics:
    """1結果対その他の確率帯別Calibration。"""

    lower: float
    upper: float
    count: int
    mean_probability: Optional[float]
    actual_count: int
    actual_rate: Optional[float]

    @property
    def calibration_gap(self) -> Optional[float]:
        if self.mean_probability is None or self.actual_rate is None:
            return None
        return abs(self.mean_probability - self.actual_rate)

    @property
    def label(self) -> str:
        return (
            f"{self.lower:.0%}以上"
            if self.upper >= 1.0
            else f"{self.lower:.0%}以上{self.upper:.0%}未満"
        )


@dataclass(frozen=True)
class OneVsRestMetrics:
    """totoの1結果を陽性としたPrecision/Recall/F1と確率品質。"""

    outcome: str
    match_count: int
    predicted_count: int
    actual_count: int
    hit_count: int
    precision: float
    recall: float
    f1_score: float
    brier_score: Optional[float]
    calibration_error: Optional[float]
    mean_probability: Optional[float]
    actual_rate: Optional[float]
    calibration_bins: tuple[ProbabilityBandMetrics, ...]


DEFAULT_CLASS_PROBABILITY_BANDS = (
    (0.00, 0.20),
    (0.20, 0.30),
    (0.30, 0.40),
    (0.40, 0.50),
    (0.50, 0.60),
    (0.60, 1.00),
)


def probability_band_calibration(
    probabilities: Sequence[float],
    actual_results: Sequence[str],
    *,
    outcome: str,
    bands: Sequence[tuple[float, float]] = DEFAULT_CLASS_PROBABILITY_BANDS,
) -> tuple[tuple[ProbabilityBandMetrics, ...], Optional[float]]:
    """指定結果の確率帯別実発生率と加重Calibration Errorを返す。"""

    if outcome not in TOTO_OUTCOMES:
        raise ValueError("outcomeは1・0・2のいずれかにしてください。")
    valid_rows: list[tuple[float, str]] = []
    for probability_value, actual_value in zip(probabilities, actual_results):
        try:
            probability = float(probability_value)
        except (TypeError, ValueError):
            continue
        actual = normalize_toto_outcome(actual_value)
        if (
            not math.isfinite(probability)
            or probability < 0.0
            or probability > 1.0
            or actual not in TOTO_OUTCOMES
        ):
            continue
        valid_rows.append((probability, actual))

    bins: list[ProbabilityBandMetrics] = []
    weighted_gap = 0.0
    for lower, upper in bands:
        if lower < 0.0 or upper <= lower or upper > 1.0:
            raise ValueError("確率帯は0～1の昇順で指定してください。")
        selected = [
            row
            for row in valid_rows
            if row[0] >= lower
            and (row[0] < upper or (upper >= 1.0 and row[0] <= 1.0))
        ]
        count = len(selected)
        if count:
            mean_probability = sum(row[0] for row in selected) / count
            actual_count = sum(row[1] == outcome for row in selected)
            actual_rate = actual_count / count
            weighted_gap += abs(mean_probability - actual_rate) * count
        else:
            mean_probability = None
            actual_count = 0
            actual_rate = None
        bins.append(
            ProbabilityBandMetrics(
                lower=float(lower),
                upper=float(upper),
                count=count,
                mean_probability=mean_probability,
                actual_count=actual_count,
                actual_rate=actual_rate,
            )
        )
    return (
        tuple(bins),
        weighted_gap / len(valid_rows) if valid_rows else None,
    )


def evaluate_one_vs_rest(
    predictions: Sequence[str],
    probability_rows: Sequence[Mapping[str, float]],
    actual_results: Sequence[str],
    *,
    outcome: str,
    bands: Sequence[tuple[float, float]] = DEFAULT_CLASS_PROBABILITY_BANDS,
) -> OneVsRestMetrics:
    """1/0/2の1結果について二値分類指標を共通定義で返す。"""

    if outcome not in TOTO_OUTCOMES:
        raise ValueError("outcomeは1・0・2のいずれかにしてください。")
    rows = []
    for prediction_value, probability_values, actual_value in zip(
        predictions,
        probability_rows,
        actual_results,
    ):
        prediction = normalize_toto_outcome(prediction_value)
        actual = normalize_toto_outcome(actual_value)
        if prediction not in TOTO_OUTCOMES or actual not in TOTO_OUTCOMES:
            continue
        normalized = _normalize_probabilities(probability_values)
        rows.append((prediction, normalized[outcome], actual))

    match_count = len(rows)
    predicted_count = sum(row[0] == outcome for row in rows)
    actual_count = sum(row[2] == outcome for row in rows)
    hit_count = sum(row[0] == row[2] == outcome for row in rows)
    precision = hit_count / predicted_count if predicted_count else 0.0
    recall = hit_count / actual_count if actual_count else 0.0
    f1_score = (
        2.0 * precision * recall / (precision + recall)
        if precision + recall > 0.0
        else 0.0
    )
    class_probabilities = [row[1] for row in rows]
    class_actuals = [row[2] for row in rows]
    brier_values = [
        (probability - float(actual == outcome)) ** 2
        for _, probability, actual in rows
    ]
    calibration_bins, calibration_error = probability_band_calibration(
        class_probabilities,
        class_actuals,
        outcome=outcome,
        bands=bands,
    )
    return OneVsRestMetrics(
        outcome=outcome,
        match_count=match_count,
        predicted_count=predicted_count,
        actual_count=actual_count,
        hit_count=hit_count,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        brier_score=(
            sum(brier_values) / len(brier_values) if brier_values else None
        ),
        calibration_error=calibration_error,
        mean_probability=(
            sum(class_probabilities) / len(class_probabilities)
            if class_probabilities
            else None
        ),
        actual_rate=(actual_count / match_count if match_count else None),
        calibration_bins=calibration_bins,
    )


__all__ = [
    "DEFAULT_CLASS_PROBABILITY_BANDS",
    "OneVsRestMetrics",
    "ProbabilityBandMetrics",
    "evaluate_one_vs_rest",
    "probability_band_calibration",
]
