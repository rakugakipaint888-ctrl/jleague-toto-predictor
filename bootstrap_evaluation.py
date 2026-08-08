"""Version7-B上位モデルをブートストラップ再評価し95%信頼区間を返す。"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

from draw_evaluation import evaluate_draw_predictions
from model_evaluation import CandidateEvaluation, PredictionRow

BOOTSTRAP_METRICS = (
    "brier_score",
    "log_loss",
    "calibration",
    "accuracy",
    "draw_f1",
)


@dataclass(frozen=True)
class MetricDistribution:
    mean: float
    median: float
    standard_deviation: float
    confidence_lower: float
    confidence_upper: float


@dataclass(frozen=True)
class BootstrapEvaluation:
    iterations: int
    random_seed: int
    sample_size: int
    metrics: Mapping[str, MetricDistribution]


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = max(0.0, min(1.0, probability)) * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return (
        float(sorted_values[lower]) * (1.0 - fraction)
        + float(sorted_values[upper]) * fraction
    )


def _distribution(values: Sequence[float]) -> MetricDistribution:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        nan = math.nan
        return MetricDistribution(nan, nan, nan, nan, nan)
    return MetricDistribution(
        mean=statistics.fmean(finite),
        median=statistics.median(finite),
        standard_deviation=(statistics.pstdev(finite) if len(finite) >= 2 else 0.0),
        confidence_lower=_percentile(finite, 0.025),
        confidence_upper=_percentile(finite, 0.975),
    )


def bootstrap_evaluate_rows(
    rows: Sequence[PredictionRow],
    iterations: int,
    *,
    random_seed: int,
) -> BootstrapEvaluation:
    """試合行を復元抽出し、指定5指標の分布を算出する。"""

    count = int(iterations)
    if count <= 0:
        raise ValueError("ブートストラップ回数は1以上にしてください。")
    source = tuple(rows)
    if not source:
        raise ValueError("ブートストラップ対象が0試合です。")
    rng = random.Random(int(random_seed))
    collected = {key: [] for key in BOOTSTRAP_METRICS}
    for _ in range(count):
        sample = [source[rng.randrange(len(source))] for _ in range(len(source))]
        evaluation = evaluate_draw_predictions(
            [row.prediction for row in sample],
            [row.probabilities for row in sample],
            [row.actual_result for row in sample],
            candidate_flags=[row.draw_candidate for row in sample],
        )
        metrics = evaluation.overall
        values = {
            "brier_score": metrics.brier_score,
            "log_loss": metrics.log_loss,
            "calibration": metrics.calibration_error,
            "accuracy": metrics.accuracy,
            "draw_f1": evaluation.draw.f1_score,
        }
        for key, value in values.items():
            if value is not None and math.isfinite(float(value)):
                collected[key].append(float(value))
    return BootstrapEvaluation(
        iterations=count,
        random_seed=int(random_seed),
        sample_size=len(source),
        metrics={key: _distribution(values) for key, values in collected.items()},
    )


def bootstrap_evaluate_candidate(
    evaluation: CandidateEvaluation,
    iterations: int,
    *,
    random_seed: int,
) -> BootstrapEvaluation:
    return bootstrap_evaluate_rows(
        evaluation.rows,
        iterations,
        random_seed=random_seed,
    )


def bootstrap_top_models(
    ranking,
    iterations: int,
    *,
    random_seed: int,
    limit: int = 10,
) -> dict[int, BootstrapEvaluation]:
    """順位を変えず、最終Validation済み上位10モデルまで再評価する。"""

    if int(iterations) <= 0:
        return {}
    results = {}
    for index, record in enumerate(tuple(ranking)[: max(0, int(limit))]):
        evaluation = getattr(record, "final_validation", None)
        if evaluation is None:
            continue
        results[int(record.trial_number)] = bootstrap_evaluate_candidate(
            evaluation,
            int(iterations),
            random_seed=int(random_seed) + index,
        )
    return results
