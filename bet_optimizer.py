"""Version7-Cの確率ベース買い目最適化。

Version7-A／Version7-Bが出力したP(1)・P(0)・P(2)と既存引分候補情報だけを
入力に使い、予測モデルや確率そのものは変更しない。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import pandas as pd

from bet_config import (
    BET_TARGETS,
    DEFAULT_DRAW_CANDIDATE_MARGIN,
    DEFAULT_DRAW_CANDIDATE_THRESHOLD,
    DOUBLE_SCORE_WEIGHTS,
    DRAW_INCLUSION_COVERAGE_LOSS_SCALE,
    DRAW_INCLUSION_MAX_COVERAGE_LOSS,
    DRAW_INCLUSION_MIN_SCORE,
    DRAW_INCLUSION_PROBABILITY_SCALE,
    DRAW_INCLUSION_SCORE_WEIGHTS,
    DRAW_INCLUSION_THRESHOLD_EXCESS_SCALE,
    DRAW_INCLUSION_TOP_GAP_SCALE,
    DRAW_CLOSENESS_SCALE,
    DRAW_SIGNAL_CLOSENESS_WEIGHT,
    DRAW_SIGNAL_THRESHOLD_WEIGHT,
    MIN_DRAW_CANDIDATE_PROBABILITY,
    MODEL_DRAW_SIGNAL_FLOOR,
    PROBABILITY_SUM_TOLERANCE,
    SCORE_TIE_TOLERANCE,
    SECOND_PROBABILITY_SCALE,
    SINGLE_CONFIDENCE_HIGH,
    SINGLE_CONFIDENCE_MEDIUM,
    SINGLE_CONFIDENCE_WEIGHTS,
    SINGLE_MARGIN_SCALE,
    THIRD_PROBABILITY_SCALE,
    TOP_THREE_MARGIN_SCALE,
    TOP_TWO_MARGIN_SCALE,
    TOTO_OUTCOMES,
    TOTO_TICKET_PRICE_YEN,
    TRIPLE_SCORE_WEIGHTS,
    UNCERTAINTY_SCORE_WEIGHTS,
)


BET_TYPE_SINGLE = "single"
BET_TYPE_DOUBLE = "double"
BET_TYPE_TRIPLE = "triple"
BET_TYPE_LABELS = {
    BET_TYPE_SINGLE: "シングル",
    BET_TYPE_DOUBLE: "ダブル",
    BET_TYPE_TRIPLE: "トリプル",
}
BET_TYPE_COUNTS = {
    BET_TYPE_SINGLE: 1,
    BET_TYPE_DOUBLE: 2,
    BET_TYPE_TRIPLE: 3,
}


class BetOptimizationError(ValueError):
    """入力不正時に画面へ安全な理由を返す。"""


@dataclass(frozen=True)
class MatchPrediction:
    """1試合の厳密に検証済みの3クラス確率。"""

    match_number: int
    source_match_number: int
    home_team: str
    away_team: str
    probability_1: float
    probability_0: float
    probability_2: float
    model_draw_candidate: bool = False
    model_draw_candidate_reasons: tuple[str, ...] = ()

    @property
    def probabilities(self) -> dict[str, float]:
        return {
            "1": self.probability_1,
            "0": self.probability_0,
            "2": self.probability_2,
        }


@dataclass(frozen=True)
class MatchBetAnalysis:
    """配置前に計算する試合別の不確実性と信頼度。"""

    prediction: MatchPrediction
    ranked_outcomes: tuple[str, str, str]
    top_probability: float
    second_probability: float
    third_probability: float
    top_two_gap: float
    top_three_gap: float
    normalized_entropy: float
    draw_candidate: bool
    draw_signal: float
    draw_candidate_threshold: float
    draw_inclusion_evaluated: bool
    draw_inclusion_score: float | None
    draw_inclusion_coverage_loss: float
    draw_inclusion_recommended: bool
    uncertainty_score: float
    double_candidate_score: float
    triple_candidate_score: float
    single_confidence_score: float
    single_confidence: str

    @property
    def top_outcome(self) -> str:
        return self.ranked_outcomes[0]

    @property
    def second_outcome(self) -> str:
        return self.ranked_outcomes[1]


@dataclass(frozen=True)
class BetRecommendation:
    """1試合の区分・買い目・Coverage。"""

    analysis: MatchBetAnalysis
    bet_type: str
    outcomes: tuple[str, ...]
    reason: str

    @property
    def coverage(self) -> float:
        probabilities = self.analysis.prediction.probabilities
        return sum(probabilities[outcome] for outcome in self.outcomes)


@dataclass(frozen=True)
class BetPlan:
    """開催回またはmini toto 1組分の買い目。"""

    target: str
    recommendations: tuple[BetRecommendation, ...]
    draw_candidate_threshold: float
    draw_candidate_margin: float

    @property
    def match_count(self) -> int:
        return len(self.recommendations)

    @property
    def double_count(self) -> int:
        return sum(
            recommendation.bet_type == BET_TYPE_DOUBLE
            for recommendation in self.recommendations
        )

    @property
    def triple_count(self) -> int:
        return sum(
            recommendation.bet_type == BET_TYPE_TRIPLE
            for recommendation in self.recommendations
        )

    @property
    def ticket_count(self) -> int:
        return math.prod(
            len(recommendation.outcomes)
            for recommendation in self.recommendations
        )

    @property
    def purchase_amount_yen(self) -> int:
        return self.ticket_count * TOTO_TICKET_PRICE_YEN

    @property
    def estimated_full_coverage(self) -> float:
        """各試合を独立と仮定した、全試合Coverageの積。"""

        return math.prod(
            recommendation.coverage
            for recommendation in self.recommendations
        )


def calculate_ticket_count(double_count: int, triple_count: int) -> int:
    """2^ダブル数×3^トリプル数を返す。"""

    doubles = _nonnegative_integer(double_count, "ダブル試合数")
    triples = _nonnegative_integer(triple_count, "トリプル試合数")
    return (2 ** doubles) * (3 ** triples)


def calculate_purchase_amount(double_count: int, triple_count: int) -> int:
    return calculate_ticket_count(double_count, triple_count) * TOTO_TICKET_PRICE_YEN


def is_budget_exceeded(
    double_count: int,
    triple_count: int,
    budget_yen: int | None,
) -> bool:
    if budget_yen is None:
        return False
    budget = _nonnegative_integer(budget_yen, "予算")
    return calculate_purchase_amount(double_count, triple_count) > budget


def target_label(target: str) -> str:
    return str(_target_definition(target)["label"])


def target_source_match_numbers(target: str) -> tuple[int, ...]:
    return tuple(_target_definition(target)["source_match_numbers"])


def build_match_predictions(
    frame: pd.DataFrame,
    target: str,
) -> tuple[MatchPrediction, ...]:
    """既存予測結果／履歴DataFrameを共通形式へ変換する。"""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise BetOptimizationError("予測データが0件です。先に通常予想を実行してください。")

    number_column = _first_column(frame, "toto_match_number", "試合", "match_number")
    probability_columns = _probability_columns(frame)
    source_numbers = target_source_match_numbers(target)
    rows_by_number: dict[int, pd.Series] = {}

    for _, row in frame.iterrows():
        source_number = _strict_integer(row.get(number_column), "試合番号")
        if source_number not in source_numbers:
            continue
        if source_number in rows_by_number:
            raise BetOptimizationError(f"第{source_number}試合の予測が重複しています。")
        rows_by_number[source_number] = row

    missing = [number for number in source_numbers if number not in rows_by_number]
    if missing:
        missing_text = "、".join(str(number) for number in missing)
        raise BetOptimizationError(f"対象試合が不足しています：第{missing_text}試合")

    predictions = []
    for local_number, source_number in enumerate(source_numbers, start=1):
        row = rows_by_number[source_number]
        probabilities = _strict_probabilities(
            {
                outcome: row.get(column_name)
                for outcome, column_name in probability_columns.items()
            }
        )
        home_team, away_team = _teams_from_row(row)
        predictions.append(
            MatchPrediction(
                match_number=local_number,
                source_match_number=source_number,
                home_team=home_team,
                away_team=away_team,
                probability_1=probabilities["1"],
                probability_0=probabilities["0"],
                probability_2=probabilities["2"],
                model_draw_candidate=_as_boolean(row.get("draw_candidate", False)),
                model_draw_candidate_reasons=_as_reason_tuple(
                    row.get("draw_candidate_reasons", ())
                ),
            )
        )
    return tuple(predictions)


def analyze_match_prediction(
    prediction: MatchPrediction,
    *,
    draw_candidate_threshold: float = DEFAULT_DRAW_CANDIDATE_THRESHOLD,
    draw_candidate_margin: float = DEFAULT_DRAW_CANDIDATE_MARGIN,
) -> MatchBetAnalysis:
    """確率分布から保険価値とシングル信頼度を算出する。

    Uncertainty Score = 100 × (0.35×正規化Entropy
      + 0.25×上位2結果の近さ + 0.20×最大確率の低さ
      + 0.10×引分Signal + 0.10×1位と3位の近さ)
    """

    threshold = _unit_interval(draw_candidate_threshold, "引分候補閾値")
    margin = _unit_interval(draw_candidate_margin, "引分候補の確率差")
    probabilities = _strict_probabilities(prediction.probabilities)
    order_index = {outcome: index for index, outcome in enumerate(TOTO_OUTCOMES)}
    ranked = tuple(
        sorted(
            TOTO_OUTCOMES,
            key=lambda outcome: (-probabilities[outcome], order_index[outcome]),
        )
    )
    top, second, third = ranked
    top_probability = probabilities[top]
    second_probability = probabilities[second]
    third_probability = probabilities[third]
    top_two_gap = top_probability - second_probability
    top_three_gap = top_probability - third_probability
    entropy = -sum(
        probability * math.log(probability)
        for probability in probabilities.values()
        if probability > 0.0
    ) / math.log(3.0)
    entropy = _clamp(entropy)
    maximum_uncertainty = _clamp((1.0 - top_probability) / (2.0 / 3.0))
    top_two_closeness = 1.0 - _clamp(top_two_gap / TOP_TWO_MARGIN_SCALE)
    top_three_closeness = 1.0 - _clamp(top_three_gap / TOP_THREE_MARGIN_SCALE)
    threshold_denominator = max(threshold, PROBABILITY_SUM_TOLERANCE)
    threshold_signal = _clamp(probabilities["0"] / threshold_denominator)
    draw_closeness = 1.0 - _clamp(
        abs(top_probability - probabilities["0"]) / DRAW_CLOSENESS_SCALE
    )
    draw_signal = (
        DRAW_SIGNAL_THRESHOLD_WEIGHT * threshold_signal
        + DRAW_SIGNAL_CLOSENESS_WEIGHT * draw_closeness
    )
    model_draw_signal = bool(
        prediction.model_draw_candidate
        and probabilities["0"] >= min(MIN_DRAW_CANDIDATE_PROBABILITY, threshold)
    )
    if model_draw_signal:
        draw_signal = max(draw_signal, MODEL_DRAW_SIGNAL_FLOOR)
    draw_signal = _clamp(draw_signal)
    draw_candidate = bool(
        probabilities["0"] >= threshold
        or (
            top_probability - probabilities["0"] <= margin
            and probabilities["0"]
            >= min(MIN_DRAW_CANDIDATE_PROBABILITY, threshold)
        )
        or model_draw_signal
    )

    features = {
        "entropy": entropy,
        "top_two_closeness": top_two_closeness,
        "maximum_uncertainty": maximum_uncertainty,
        "draw_signal": draw_signal,
        "top_three_closeness": top_three_closeness,
        "second_probability": _clamp(
            second_probability / SECOND_PROBABILITY_SCALE
        ),
        "third_probability": _clamp(
            third_probability / THIRD_PROBABILITY_SCALE
        ),
    }
    uncertainty_score = 100.0 * _weighted_score(
        UNCERTAINTY_SCORE_WEIGHTS,
        features,
    )
    # Double Score = 100 × (0.40×上位2結果の近さ + 0.25×Entropy
    #   + 0.15×最大確率の低さ + 0.10×引分Signal + 0.10×2位確率品質)
    double_score = 100.0 * _weighted_score(DOUBLE_SCORE_WEIGHTS, features)
    # Triple Score = 100 × (0.30×Entropy + 0.20×1位と3位の近さ
    #   + 0.20×最大確率の低さ + 0.15×上位2結果の近さ
    #   + 0.10×引分Signal + 0.05×3位確率品質)
    triple_score = 100.0 * _weighted_score(TRIPLE_SCORE_WEIGHTS, features)

    # 0が確率3位のときだけ、通常2位との入替を独立評価する。ここで使う
    # coverage_loss = P(通常2位) - P(0) は、1・2から1・0等へ変えることで
    # 失う試合別Coverageそのものであり、Version7-A／7-Bの確率は変更しない。
    draw_probability = probabilities["0"]
    draw_is_third = third == "0"
    draw_inclusion_evaluated = bool(
        draw_is_third and draw_probability >= threshold
    )
    draw_inclusion_coverage_loss = (
        max(0.0, second_probability - draw_probability)
        if draw_is_third
        else 0.0
    )
    draw_inclusion_score: float | None = None
    draw_inclusion_recommended = False
    if draw_inclusion_evaluated:
        draw_inclusion_features = {
            "draw_probability": _clamp(
                draw_probability / DRAW_INCLUSION_PROBABILITY_SCALE
            ),
            "threshold_excess": _clamp(
                (draw_probability - threshold)
                / DRAW_INCLUSION_THRESHOLD_EXCESS_SCALE
            ),
            "coverage_retention": 1.0
            - _clamp(
                draw_inclusion_coverage_loss
                / DRAW_INCLUSION_COVERAGE_LOSS_SCALE
            ),
            "top_closeness": 1.0
            - _clamp(
                (top_probability - draw_probability)
                / DRAW_INCLUSION_TOP_GAP_SCALE
            ),
            "entropy": entropy,
            # 既存モデル候補なら最大の補助根拠とし、それ以外でも現在の
            # P(0)と1位との差から作った連続draw_signalを0にはしない。
            "model_draw_evidence": max(
                draw_signal,
                1.0 if prediction.model_draw_candidate else 0.0,
            ),
        }
        draw_inclusion_score = 100.0 * _weighted_score(
            DRAW_INCLUSION_SCORE_WEIGHTS,
            draw_inclusion_features,
        )
        draw_inclusion_recommended = bool(
            draw_inclusion_score >= DRAW_INCLUSION_MIN_SCORE
            and draw_inclusion_coverage_loss
            < DRAW_INCLUSION_MAX_COVERAGE_LOSS
        )

    confidence_features = {
        "maximum_certainty": _clamp(
            (top_probability - 1.0 / 3.0) / (2.0 / 3.0)
        ),
        "margin_certainty": _clamp(top_two_gap / SINGLE_MARGIN_SCALE),
        "distribution_certainty": 1.0 - entropy,
        # 本命が0なら引分確率はリスクではなく本命根拠として扱う。
        "draw_safety": 1.0 if top == "0" else 1.0 - draw_signal,
    }
    # Confidence = 100 × (0.40×最大確信度 + 0.30×上位差確信度
    #   + 0.20×分布集中度 + 0.10×引分安全度)
    confidence_score = 100.0 * _weighted_score(
        SINGLE_CONFIDENCE_WEIGHTS,
        confidence_features,
    )
    if confidence_score >= SINGLE_CONFIDENCE_HIGH:
        confidence_label = "高"
    elif confidence_score >= SINGLE_CONFIDENCE_MEDIUM:
        confidence_label = "中"
    else:
        confidence_label = "低"

    return MatchBetAnalysis(
        prediction=prediction,
        ranked_outcomes=(ranked[0], ranked[1], ranked[2]),
        top_probability=top_probability,
        second_probability=second_probability,
        third_probability=third_probability,
        top_two_gap=top_two_gap,
        top_three_gap=top_three_gap,
        normalized_entropy=entropy,
        draw_candidate=draw_candidate,
        draw_signal=draw_signal,
        draw_candidate_threshold=threshold,
        draw_inclusion_evaluated=draw_inclusion_evaluated,
        draw_inclusion_score=draw_inclusion_score,
        draw_inclusion_coverage_loss=draw_inclusion_coverage_loss,
        draw_inclusion_recommended=draw_inclusion_recommended,
        uncertainty_score=uncertainty_score,
        double_candidate_score=double_score,
        triple_candidate_score=triple_score,
        single_confidence_score=confidence_score,
        single_confidence=confidence_label,
    )


def optimize_bet_plan(
    predictions: Sequence[MatchPrediction],
    *,
    target: str,
    double_count: int,
    triple_count: int,
    draw_candidate_threshold: float = DEFAULT_DRAW_CANDIDATE_THRESHOLD,
    draw_candidate_margin: float = DEFAULT_DRAW_CANDIDATE_MARGIN,
) -> BetPlan:
    """指定数を満たすダブル・トリプル配置を重複なしで最適化する。"""

    _target_definition(target)
    doubles = _nonnegative_integer(double_count, "ダブル試合数")
    triples = _nonnegative_integer(triple_count, "トリプル試合数")
    ordered_predictions = tuple(sorted(predictions, key=lambda item: item.match_number))
    expected_count = len(target_source_match_numbers(target))
    if len(ordered_predictions) != expected_count:
        raise BetOptimizationError(
            f"{target_label(target)}には{expected_count}試合の予測が必要です。"
        )
    if len({item.match_number for item in ordered_predictions}) != len(
        ordered_predictions
    ):
        raise BetOptimizationError("試合番号が重複しています。")
    if doubles + triples > len(ordered_predictions):
        raise BetOptimizationError(
            "ダブル試合数とトリプル試合数の合計が対象試合数を超えています。"
        )

    analyses = tuple(
        analyze_match_prediction(
            prediction,
            draw_candidate_threshold=draw_candidate_threshold,
            draw_candidate_margin=draw_candidate_margin,
        )
        for prediction in ordered_predictions
    )
    assignments = _optimal_assignments(analyses, doubles, triples)
    recommendations = tuple(
        _recommendation(analysis, bet_type)
        for analysis, bet_type in zip(analyses, assignments)
    )
    plan = BetPlan(
        target=target,
        recommendations=recommendations,
        draw_candidate_threshold=float(draw_candidate_threshold),
        draw_candidate_margin=float(draw_candidate_margin),
    )
    if plan.double_count != doubles or plan.triple_count != triples:
        raise BetOptimizationError("指定した買い目数と最適化結果が一致しません。")
    return plan


def apply_manual_selections(
    plan: BetPlan,
    selections: Mapping[int, Sequence[str]],
) -> BetPlan:
    """AI案を保持したまま、試合番号ごとの手動買い目で別Planを作る。"""

    recommendations = []
    known_numbers = {
        recommendation.analysis.prediction.match_number
        for recommendation in plan.recommendations
    }
    unknown_numbers = set(selections) - known_numbers
    if unknown_numbers:
        raise BetOptimizationError("対象外の試合番号が手動買い目に含まれています。")

    for recommendation in plan.recommendations:
        match_number = recommendation.analysis.prediction.match_number
        raw_outcomes = selections.get(match_number, recommendation.outcomes)
        outcomes = _validated_outcome_selection(raw_outcomes)
        bet_type = {
            1: BET_TYPE_SINGLE,
            2: BET_TYPE_DOUBLE,
            3: BET_TYPE_TRIPLE,
        }[len(outcomes)]
        recommendations.append(
            replace(
                recommendation,
                bet_type=bet_type,
                outcomes=outcomes,
                reason="手動調整",
            )
        )
    return replace(plan, recommendations=tuple(recommendations))


def plan_fingerprint(plan: BetPlan) -> str:
    """Session Stateの手動入力を買い目案ごとに分離する短い識別子。"""

    payload = {
        "target": plan.target,
        "threshold": plan.draw_candidate_threshold,
        "margin": plan.draw_candidate_margin,
        "matches": [
            {
                "source": item.analysis.prediction.source_match_number,
                "probabilities": item.analysis.prediction.probabilities,
                "type": item.bet_type,
                "outcomes": item.outcomes,
            }
            for item in plan.recommendations
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]


def _optimal_assignments(
    analyses: Sequence[MatchBetAnalysis],
    double_count: int,
    triple_count: int,
) -> tuple[str, ...]:
    """O(試合数×ダブル数×トリプル数)で全体配置を同時最適化する。"""

    type_code = {
        BET_TYPE_SINGLE: 0,
        BET_TYPE_DOUBLE: 1,
        BET_TYPE_TRIPLE: 2,
    }
    states: dict[tuple[int, int], tuple[float, tuple[str, ...]]] = {
        (0, 0): (0.0, ())
    }
    for analysis in analyses:
        next_states: dict[tuple[int, int], tuple[float, tuple[str, ...]]] = {}
        options = (
            (BET_TYPE_SINGLE, 0, 0, 0.0),
            (BET_TYPE_DOUBLE, 1, 0, analysis.double_candidate_score),
            (BET_TYPE_TRIPLE, 0, 1, analysis.triple_candidate_score),
        )
        for (used_doubles, used_triples), (score, assignment) in states.items():
            for bet_type, add_double, add_triple, added_score in options:
                new_doubles = used_doubles + add_double
                new_triples = used_triples + add_triple
                if new_doubles > double_count or new_triples > triple_count:
                    continue
                candidate = (score + added_score, (*assignment, bet_type))
                state_key = (new_doubles, new_triples)
                current = next_states.get(state_key)
                if current is None or _assignment_is_better(
                    candidate,
                    current,
                    type_code,
                ):
                    next_states[state_key] = candidate
        states = next_states

    selected = states.get((double_count, triple_count))
    if selected is None:
        raise BetOptimizationError("指定数を満たす買い目を作成できませんでした。")
    return selected[1]


def _assignment_is_better(
    candidate: tuple[float, tuple[str, ...]],
    current: tuple[float, tuple[str, ...]],
    type_code: Mapping[str, int],
) -> bool:
    if candidate[0] > current[0] + SCORE_TIE_TOLERANCE:
        return True
    if abs(candidate[0] - current[0]) > SCORE_TIE_TOLERANCE:
        return False
    # 完全同点時は試合番号の小さい方へ広い買い目を置き、再現性を固定する。
    return tuple(type_code[item] for item in candidate[1]) > tuple(
        type_code[item] for item in current[1]
    )


def _recommendation(
    analysis: MatchBetAnalysis,
    bet_type: str,
) -> BetRecommendation:
    if bet_type == BET_TYPE_SINGLE:
        outcomes = (analysis.top_outcome,)
        reason = (
            f"最大確率{analysis.top_probability:.1%}、"
            f"上位差{analysis.top_two_gap:.1%}"
        )
    elif bet_type == BET_TYPE_DOUBLE:
        outcomes = select_double_outcomes(analysis)
        reason = _double_selection_reason(analysis, outcomes)
    elif bet_type == BET_TYPE_TRIPLE:
        outcomes = TOTO_OUTCOMES
        reason = (
            f"トリプル候補Score {analysis.triple_candidate_score:.1f}、"
            f"Entropy {analysis.normalized_entropy:.3f}"
        )
    else:
        raise BetOptimizationError("買い目区分が不正です。")
    return BetRecommendation(
        analysis=analysis,
        bet_type=bet_type,
        outcomes=tuple(outcomes),
        reason=reason,
    )


def select_double_outcomes(
    analysis: MatchBetAnalysis,
) -> tuple[str, str]:
    """ダブル対象試合の2結果だけを選ぶ（試合配置ロジックとは独立）。

    通常は確率上位2結果を維持する。0が3位でもP(0)が引分候補閾値以上で、
    Draw Inclusion Scoreが採用基準を満たし、Coverage低下が10ポイント未満なら、
    通常2位を0へ入れ替える。閾値超過だけでは0を採用しない。
    """

    standard = analysis.ranked_outcomes[:2]
    if "0" in standard or not analysis.draw_inclusion_recommended:
        return standard
    return (analysis.top_outcome, "0")


def _double_selection_reason(
    analysis: MatchBetAnalysis,
    outcomes: tuple[str, str],
) -> str:
    probabilities = analysis.prediction.probabilities
    draw_probability = probabilities["0"]
    standard = analysis.ranked_outcomes[:2]
    base = f"ダブル候補Score {analysis.double_candidate_score:.1f}。"
    if "0" in standard:
        return (
            base
            + f"P(0)={draw_probability:.1%}が確率上位2結果に入るため、"
            f"通常順位どおり{'・'.join(outcomes)}を採用"
        )
    second_outcome = analysis.second_outcome
    second_probability = probabilities[second_outcome]
    gap_points = analysis.draw_inclusion_coverage_loss * 100.0
    threshold = analysis.draw_candidate_threshold
    if not analysis.draw_inclusion_evaluated:
        return (
            base
            + f"P(0)={draw_probability:.1%}が引分候補閾値"
            f"{threshold:.1%}未満のため、確率上位2結果"
            f"{'・'.join(standard)}を維持"
        )

    score = float(analysis.draw_inclusion_score or 0.0)
    if analysis.prediction.model_draw_candidate:
        reason_detail = "／".join(
            analysis.prediction.model_draw_candidate_reasons
        )
        model_reason = (
            f"Version7-A／7-B引分候補（{reason_detail}）も成立。"
            if reason_detail
            else "Version7-A／7-B引分候補も成立。"
        )
    else:
        model_reason = ""
    if outcomes != standard:
        return (
            base
            + f"P(0)={draw_probability:.1%}、通常2位{second_outcome}="
            f"{second_probability:.1%}との差{gap_points:.1f}pt。"
            + model_reason
            + f"閾値{threshold:.1%}以上かつDraw Inclusion Score "
            f"{score:.1f}点のため0を採用（Coverage低下{gap_points:.1f}pt）"
        )

    if analysis.draw_inclusion_coverage_loss >= DRAW_INCLUSION_MAX_COVERAGE_LOSS:
        conclusion = (
            f"Coverage低下{gap_points:.1f}ptが上限"
            f"{DRAW_INCLUSION_MAX_COVERAGE_LOSS * 100.0:.1f}pt以上"
        )
    else:
        conclusion = (
            f"Draw Inclusion Score {score:.1f}点が採用基準"
            f"{DRAW_INCLUSION_MIN_SCORE:.1f}点未満"
        )
    return (
        base
        + f"引分評価対象だが、P(0)={draw_probability:.1%}は通常2位"
        f"{second_outcome}={second_probability:.1%}より{gap_points:.1f}pt低く、"
        + model_reason
        + conclusion
        + f"のため確率上位2結果{'・'.join(standard)}を維持"
    )


def _strict_probabilities(values: Mapping[str, Any]) -> dict[str, float]:
    converted = {}
    for outcome in TOTO_OUTCOMES:
        try:
            number = float(values.get(outcome))
        except (TypeError, ValueError):
            raise BetOptimizationError(f"P({outcome})が欠損しています。") from None
        if not math.isfinite(number):
            raise BetOptimizationError(f"P({outcome})は有限値にしてください。")
        if number < 0.0:
            raise BetOptimizationError(f"P({outcome})は0以上にしてください。")
        converted[outcome] = number

    total = sum(converted.values())
    percentage_mode = any(
        value > 1.0 + PROBABILITY_SUM_TOLERANCE
        for value in converted.values()
    )
    expected_total = 100.0 if percentage_mode else 1.0
    tolerance = PROBABILITY_SUM_TOLERANCE * expected_total
    if abs(total - expected_total) > tolerance:
        raise BetOptimizationError(
            f"P(1)・P(0)・P(2)の合計が{expected_total:g}ではありません。"
        )
    if percentage_mode:
        converted = {
            outcome: value / 100.0 for outcome, value in converted.items()
        }
    # 許容した浮動小数誤差だけを吸収し、実質的な不正値は上で拒否する。
    normalized_total = sum(converted.values())
    return {
        outcome: value / normalized_total
        for outcome, value in converted.items()
    }


def _validated_outcome_selection(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = (values,)
    else:
        raw_values = tuple(str(value) for value in values)
    if not raw_values:
        raise BetOptimizationError("各試合で1つ以上の買い目を選択してください。")
    if len(raw_values) > 3 or len(set(raw_values)) != len(raw_values):
        raise BetOptimizationError("同じ結果を重複して選択できません。")
    if any(value not in TOTO_OUTCOMES for value in raw_values):
        raise BetOptimizationError("買い目は1・0・2から選択してください。")
    selected = set(raw_values)
    return tuple(outcome for outcome in TOTO_OUTCOMES if outcome in selected)


def _probability_columns(frame: pd.DataFrame) -> dict[str, str]:
    candidates = (
        {"1": "1", "0": "0", "2": "2"},
        {
            "1": "probability_1",
            "0": "probability_0",
            "2": "probability_2",
        },
    )
    for columns in candidates:
        if all(column in frame.columns for column in columns.values()):
            return columns
    raise BetOptimizationError("P(1)・P(0)・P(2)の列を確認できません。")


def _teams_from_row(row: pd.Series) -> tuple[str, str]:
    home_team = str(row.get("home_team", "") or "").strip()
    away_team = str(row.get("away_team", "") or "").strip()
    if not home_team and not away_team:
        card = str(row.get("対戦カード", "") or "")
        home_team, separator, away_team = card.partition(" vs ")
        if not separator:
            home_team = card
            away_team = ""
    return home_team.strip(), away_team.strip()


def _first_column(frame: pd.DataFrame, *names: str) -> str:
    for name in names:
        if name in frame.columns:
            return name
    raise BetOptimizationError("試合番号列を確認できません。")


def _target_definition(target: str) -> Mapping[str, Any]:
    if target not in BET_TARGETS:
        raise BetOptimizationError("対象くじを確認できません。")
    return BET_TARGETS[target]


def _weighted_score(weights: Mapping[str, float], features: Mapping[str, float]) -> float:
    return _clamp(
        sum(float(weight) * _clamp(features[name]) for name, weight in weights.items())
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _unit_interval(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BetOptimizationError(f"{label}を数値で指定してください。") from None
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise BetOptimizationError(f"{label}は0～1で指定してください。")
    return number


def _strict_integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise BetOptimizationError(f"{label}を整数で指定してください。")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BetOptimizationError(f"{label}を整数で指定してください。") from None
    if not math.isfinite(number) or not number.is_integer():
        raise BetOptimizationError(f"{label}を整数で指定してください。")
    return int(number)


def _nonnegative_integer(value: Any, label: str) -> int:
    number = _strict_integer(value, label)
    if number < 0:
        raise BetOptimizationError(f"{label}は0以上にしてください。")
    return number


def _as_boolean(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "候補")
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def _as_reason_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        if pd.isna(value):
            return ()
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        normalized = value.replace("／", "|")
        return tuple(
            reason.strip()
            for reason in normalized.split("|")
            if reason.strip()
        )
    if isinstance(value, Sequence):
        return tuple(str(reason).strip() for reason in value if str(reason).strip())
    text = str(value).strip()
    return (text,) if text else ()
