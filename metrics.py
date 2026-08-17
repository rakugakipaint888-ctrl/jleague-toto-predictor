"""Version6の確率予測・的中・回収率を評価する。

画面、履歴保存、バックテストから独立させ、同じ定義の指標を再利用する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence


TOTO_OUTCOMES = ("1", "0", "2")
DEFAULT_TOTO_STAKE_YEN = 100
PROBABILITY_EPSILON = 1e-15


def normalize_toto_outcome(value: Any) -> str:
    """CSVやpandasで数値化された実結果を1・0・2へ正規化する。"""

    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if text in TOTO_OUTCOMES:
        return text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number) or not number.is_integer():
        return ""
    normalized = str(int(number))
    return normalized if normalized in TOTO_OUTCOMES else ""


@dataclass(frozen=True)
class CalibrationBin:
    """本命確率を10%刻みで集計した信頼度区間。"""

    lower: float
    upper: float
    count: int
    mean_confidence: float
    actual_accuracy: float

    @property
    def gap(self) -> float:
        return abs(self.mean_confidence - self.actual_accuracy)


@dataclass(frozen=True)
class ModelMetrics:
    """1モデル・1件以上の予測に対する共通評価結果。"""

    match_count: int
    hit_count: int
    accuracy: Optional[float]
    class_accuracy: Mapping[str, Optional[float]]
    class_support: Mapping[str, int]
    prediction_share: Mapping[str, float]
    actual_share: Mapping[str, float]
    brier_score: Optional[float]
    log_loss: Optional[float]
    calibration_error: Optional[float]
    calibration_bins: tuple[CalibrationBin, ...]
    expected_hits: float
    stake_yen: int
    payout_yen: int
    roi: Optional[float]


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


def _normalize_probabilities(
    probabilities: Mapping[str, float],
) -> dict[str, float]:
    """1・0・2の有限な確率を合計1へ正規化する。"""

    values: dict[str, float] = {}

    for outcome in TOTO_OUTCOMES:
        try:
            value = float(probabilities.get(outcome, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        values[outcome] = value if math.isfinite(value) and value >= 0 else 0.0

    total = sum(values.values())
    if total <= 0:
        return {outcome: 1.0 / 3.0 for outcome in TOTO_OUTCOMES}

    return {outcome: value / total for outcome, value in values.items()}


def multiclass_brier_score(
    probability_rows: Sequence[Mapping[str, float]],
    actual_results: Sequence[str],
) -> Optional[float]:
    """多クラスBrier Scoreを返す。0が最良、最大は2。"""

    scores = []

    for probabilities, actual in zip(probability_rows, actual_results):
        if actual not in TOTO_OUTCOMES:
            continue
        normalized = _normalize_probabilities(probabilities)
        scores.append(
            sum(
                (normalized[outcome] - float(actual == outcome)) ** 2
                for outcome in TOTO_OUTCOMES
            )
        )

    return sum(scores) / len(scores) if scores else None


def multiclass_log_loss(
    probability_rows: Sequence[Mapping[str, float]],
    actual_results: Sequence[str],
) -> Optional[float]:
    """実結果へ割り当てた確率の平均負対数を返す。"""

    losses = []

    for probabilities, actual in zip(probability_rows, actual_results):
        if actual not in TOTO_OUTCOMES:
            continue
        normalized = _normalize_probabilities(probabilities)
        actual_probability = min(
            1.0 - PROBABILITY_EPSILON,
            max(PROBABILITY_EPSILON, normalized[actual]),
        )
        losses.append(-math.log(actual_probability))

    return sum(losses) / len(losses) if losses else None


def calibration_by_confidence(
    predictions: Sequence[str],
    probability_rows: Sequence[Mapping[str, float]],
    actual_results: Sequence[str],
    bin_count: int = 10,
) -> tuple[tuple[CalibrationBin, ...], Optional[float]]:
    """本命確率のCalibration表とECE（小さいほど良い）を返す。"""

    if bin_count <= 0:
        raise ValueError("bin_countは1以上にしてください。")

    buckets: list[list[tuple[float, float]]] = [
        [] for _ in range(bin_count)
    ]

    for prediction, probabilities, actual in zip(
        predictions,
        probability_rows,
        actual_results,
    ):
        if prediction not in TOTO_OUTCOMES or actual not in TOTO_OUTCOMES:
            continue
        normalized = _normalize_probabilities(probabilities)
        confidence = normalized[prediction]
        bucket_index = min(int(confidence * bin_count), bin_count - 1)
        buckets[bucket_index].append(
            (confidence, float(prediction == actual))
        )

    total = sum(len(bucket) for bucket in buckets)
    if total <= 0:
        return (), None

    result = []
    weighted_gap = 0.0

    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        count = len(bucket)
        mean_confidence = sum(item[0] for item in bucket) / count
        actual_accuracy = sum(item[1] for item in bucket) / count
        calibration_bin = CalibrationBin(
            lower=index / bin_count,
            upper=(index + 1) / bin_count,
            count=count,
            mean_confidence=mean_confidence,
            actual_accuracy=actual_accuracy,
        )
        result.append(calibration_bin)
        weighted_gap += calibration_bin.gap * count / total

    return tuple(result), weighted_gap


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


def toto_payout_for_hits(
    hit_count: int,
    first_prize_yen: int = 0,
    second_prize_yen: int = 0,
    third_prize_yen: int = 0,
) -> int:
    """totoシングル1口の的中数に対応する公式配当を返す。"""

    return {
        13: max(0, int(first_prize_yen)),
        12: max(0, int(second_prize_yen)),
        11: max(0, int(third_prize_yen)),
    }.get(int(hit_count), 0)


def calculate_roi(
    payout_yen: int,
    stake_yen: int,
) -> Optional[float]:
    """回収率（払戻÷購入額×100）を返す。"""

    if stake_yen <= 0:
        return None
    return max(0, int(payout_yen)) / int(stake_yen) * 100.0


def evaluate_model(
    predictions: Sequence[str],
    probability_rows: Sequence[Mapping[str, float]],
    actual_results: Sequence[str],
    *,
    stake_yen: int = DEFAULT_TOTO_STAKE_YEN,
    payout_yen: int = 0,
) -> ModelMetrics:
    """的中率、クラス別正答率、確率指標、期待的中数、ROIを返す。"""

    rows = [
        (prediction, _normalize_probabilities(probabilities), actual)
        for prediction, probabilities, actual in zip(
            predictions,
            probability_rows,
            actual_results,
        )
        if prediction in TOTO_OUTCOMES and actual in TOTO_OUTCOMES
    ]
    valid_predictions = [row[0] for row in rows]
    valid_probabilities = [row[1] for row in rows]
    valid_actuals = [row[2] for row in rows]
    match_count = len(rows)
    hit_count = sum(
        prediction == actual
        for prediction, _, actual in rows
    )

    class_support = {
        outcome: sum(actual == outcome for actual in valid_actuals)
        for outcome in TOTO_OUTCOMES
    }
    class_accuracy = {
        outcome: (
            sum(
                prediction == actual == outcome
                for prediction, actual in zip(
                    valid_predictions,
                    valid_actuals,
                )
            )
            / class_support[outcome]
            if class_support[outcome] > 0
            else None
        )
        for outcome in TOTO_OUTCOMES
    }
    prediction_share = {
        outcome: (
            sum(prediction == outcome for prediction in valid_predictions)
            / match_count
            if match_count > 0
            else 0.0
        )
        for outcome in TOTO_OUTCOMES
    }
    actual_share = {
        outcome: (
            class_support[outcome] / match_count
            if match_count > 0
            else 0.0
        )
        for outcome in TOTO_OUTCOMES
    }
    bins, calibration_error = calibration_by_confidence(
        valid_predictions,
        valid_probabilities,
        valid_actuals,
    )
    expected_hits = sum(
        probabilities[prediction]
        for prediction, probabilities, _ in rows
    )

    return ModelMetrics(
        match_count=match_count,
        hit_count=hit_count,
        accuracy=(hit_count / match_count if match_count > 0 else None),
        class_accuracy=class_accuracy,
        class_support=class_support,
        prediction_share=prediction_share,
        actual_share=actual_share,
        brier_score=multiclass_brier_score(
            valid_probabilities,
            valid_actuals,
        ),
        log_loss=multiclass_log_loss(
            valid_probabilities,
            valid_actuals,
        ),
        calibration_error=calibration_error,
        calibration_bins=bins,
        expected_hits=expected_hits,
        stake_yen=max(0, int(stake_yen)),
        payout_yen=max(0, int(payout_yen)),
        roi=calculate_roi(payout_yen, stake_yen),
    )


def aggregate_roi(
    payouts_yen: Iterable[int],
    stakes_yen: Iterable[int],
) -> Optional[float]:
    """複数開催回の払戻額と購入額から累積回収率を返す。"""

    total_payout = sum(max(0, int(value)) for value in payouts_yen)
    total_stake = sum(max(0, int(value)) for value in stakes_yen)
    return calculate_roi(total_payout, total_stake)
