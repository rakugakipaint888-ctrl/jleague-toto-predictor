"""保存済み予測確率を使うVersion7-C買い目戦略バックテスト。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from bet_config import TOTO_TICKET_PRICE_YEN
from bet_optimizer import (
    BetPlan,
    BetOptimizationError,
    build_match_predictions,
    calculate_ticket_count,
    optimize_bet_plan,
    target_source_match_numbers,
)
from history_manager import normalize_toto_payouts


BET_BACKTEST_REQUIRED_COLUMNS = frozenset(
    {
        "toto_round",
        "toto_match_number",
        "prediction_version",
        "probability_1",
        "probability_0",
        "probability_2",
        "actual_result",
    }
)
BET_BACKTEST_OPTIONAL_COLUMNS = frozenset(
    {
        "prediction_date",
        "home_team",
        "away_team",
        "prediction",
        "stake_yen",
        "payout_yen",
        "roi",
    }
)


@dataclass(frozen=True)
class BetStrategyBacktest:
    strategy: str
    target: str
    prediction_version: str
    double_count: int
    triple_count: int
    evaluated_rounds: int
    ticket_count_per_round: int
    total_ticket_count: int
    total_purchase_yen: int
    full_hit_count: int
    full_hit_rate: Optional[float]
    payout_data_available: bool
    total_payout_yen: Optional[int]
    profit_yen: Optional[int]
    roi: Optional[float]
    evaluated_round_ids: tuple[int, ...]


def backtest_bet_strategy(
    history: pd.DataFrame,
    *,
    strategy: str,
    target: str,
    prediction_version: str,
    double_count: int,
    triple_count: int,
    draw_candidate_threshold: float,
    draw_candidate_margin: float,
    payouts_by_round: Optional[Mapping[int, Any]] = None,
    verified_round_ids: Optional[Sequence[int]] = None,
) -> BetStrategyBacktest:
    """開催回ごとに同じ配置ルールを適用し全試合Coverage的中を評価する。

    払戻はtotoの公式1～3等金が全評価回で取得できた場合だけ算出する。mini totoの
    配当は現行保存形式にないため、呼出側が明示的に渡さない限り算出しない。
    """

    if not isinstance(history, pd.DataFrame) or history.empty:
        return _empty_backtest(
            strategy,
            target,
            prediction_version,
            double_count,
            triple_count,
        )
    missing_columns = BET_BACKTEST_REQUIRED_COLUMNS - set(history.columns)
    if missing_columns:
        raise BetOptimizationError(
            "買い目バックテストに必要な予想履歴列が不足しています："
            + "、".join(sorted(missing_columns))
        )

    selected = history.loc[
        history["prediction_version"].astype(str) == str(prediction_version)
    ].copy()
    if selected.empty:
        return _empty_backtest(
            strategy,
            target,
            prediction_version,
            double_count,
            triple_count,
        )
    selected["_round"] = pd.to_numeric(selected["toto_round"], errors="coerce")
    selected["_match"] = pd.to_numeric(
        selected["toto_match_number"], errors="coerce"
    )
    selected = selected.dropna(subset=["_round", "_match"])
    if verified_round_ids is not None:
        allowed_round_ids = {int(value) for value in verified_round_ids}
        selected = selected.loc[
            selected["_round"].astype(int).isin(allowed_round_ids)
        ]
    if "prediction_date" in selected.columns:
        selected = selected.sort_values("prediction_date")
    selected = selected.drop_duplicates(
        ["_round", "_match", "prediction_version"],
        keep="last",
    )

    required_numbers = set(target_source_match_numbers(target))
    round_results = []
    for round_value, group in selected.groupby("_round"):
        round_id = int(round_value)
        group_numbers = {int(value) for value in group["_match"]}
        if not required_numbers.issubset(group_numbers):
            continue
        target_rows = group.loc[group["_match"].astype(int).isin(required_numbers)]
        actuals = {
            int(row["_match"]): _actual_label(row.get("actual_result"))
            for _, row in target_rows.iterrows()
        }
        if any(actuals.get(number) not in ("1", "0", "2") for number in required_numbers):
            continue
        predictions = build_match_predictions(target_rows, target)
        plan = optimize_bet_plan(
            predictions,
            target=target,
            double_count=double_count,
            triple_count=triple_count,
            draw_candidate_threshold=draw_candidate_threshold,
            draw_candidate_margin=draw_candidate_margin,
        )
        hit_distribution = _ticket_hit_distribution(plan, actuals)
        full_hit = hit_distribution.get(plan.match_count, 0) > 0
        round_results.append(
            (round_id, plan.ticket_count, bool(full_hit), hit_distribution)
        )

    evaluated_rounds = len(round_results)
    if not round_results:
        return _empty_backtest(
            strategy,
            target,
            prediction_version,
            double_count,
            triple_count,
        )
    ticket_count_per_round = round_results[0][1]
    total_ticket_count = sum(item[1] for item in round_results)
    total_purchase_yen = total_ticket_count * TOTO_TICKET_PRICE_YEN
    full_hit_count = sum(item[2] for item in round_results)
    round_ids = tuple(item[0] for item in round_results)
    payouts = payouts_by_round if isinstance(payouts_by_round, Mapping) else {}
    normalized_payouts = {
        round_id: normalize_toto_payouts(payouts.get(round_id))
        for round_id in round_ids
    }
    payout_values = {
        round_id: normalized_payouts[round_id].as_tuple()
        for round_id in round_ids
    }
    payout_data_available = bool(
        target == "toto"
        and all(
            payout_values[round_id] is not None
            for round_id in round_ids
        )
    )
    if payout_data_available:
        total_payout = sum(
            _round_payout(
                hit_distribution,
                payout_values[round_id],
            )
            for round_id, _, _, hit_distribution in round_results
        )
        profit = total_payout - total_purchase_yen
        roi = (
            total_payout / total_purchase_yen * 100.0
            if total_purchase_yen > 0
            else None
        )
    else:
        total_payout = None
        profit = None
        roi = None

    return BetStrategyBacktest(
        strategy=strategy,
        target=target,
        prediction_version=prediction_version,
        double_count=int(double_count),
        triple_count=int(triple_count),
        evaluated_rounds=evaluated_rounds,
        ticket_count_per_round=ticket_count_per_round,
        total_ticket_count=total_ticket_count,
        total_purchase_yen=total_purchase_yen,
        full_hit_count=full_hit_count,
        full_hit_rate=full_hit_count / evaluated_rounds,
        payout_data_available=payout_data_available,
        total_payout_yen=total_payout,
        profit_yen=profit,
        roi=roi,
        evaluated_round_ids=round_ids,
    )


def compare_bet_strategies(
    history: pd.DataFrame,
    *,
    target: str,
    prediction_version: str,
    double_count: int,
    triple_count: int,
    draw_candidate_threshold: float,
    draw_candidate_margin: float,
    payouts_by_round: Optional[Mapping[int, Any]] = None,
    verified_round_ids: Optional[Sequence[int]] = None,
) -> tuple[BetStrategyBacktest, ...]:
    specifications = (
        ("A：全試合シングル", 0, 0),
        ("B：指定数ダブル", int(double_count), 0),
        ("C：指定数ダブル＋トリプル", int(double_count), int(triple_count)),
    )
    return tuple(
        backtest_bet_strategy(
            history,
            strategy=label,
            target=target,
            prediction_version=prediction_version,
            double_count=doubles,
            triple_count=triples,
            draw_candidate_threshold=draw_candidate_threshold,
            draw_candidate_margin=draw_candidate_margin,
            payouts_by_round=payouts_by_round,
            verified_round_ids=verified_round_ids,
        )
        for label, doubles, triples in specifications
    )


def backtest_frame(results: tuple[BetStrategyBacktest, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "戦略": result.strategy,
                "ダブル": result.double_count,
                "トリプル": result.triple_count,
                "対象開催回": result.evaluated_rounds,
                "1回の口数": result.ticket_count_per_round,
                "総口数": result.total_ticket_count,
                "購入金額": result.total_purchase_yen,
                "全試合的中回数": result.full_hit_count,
                "全試合的中率": (
                    result.full_hit_rate * 100.0
                    if result.full_hit_rate is not None
                    else None
                ),
                "払戻金": result.total_payout_yen,
                "収支": result.profit_yen,
                "ROI": result.roi,
            }
            for result in results
        ]
    )


def _empty_backtest(
    strategy: str,
    target: str,
    prediction_version: str,
    double_count: int,
    triple_count: int,
) -> BetStrategyBacktest:
    return BetStrategyBacktest(
        strategy=strategy,
        target=target,
        prediction_version=prediction_version,
        double_count=int(double_count),
        triple_count=int(triple_count),
        evaluated_rounds=0,
        ticket_count_per_round=calculate_ticket_count(double_count, triple_count),
        total_ticket_count=0,
        total_purchase_yen=0,
        full_hit_count=0,
        full_hit_rate=None,
        payout_data_available=False,
        total_payout_yen=None,
        profit_yen=None,
        roi=None,
        evaluated_round_ids=(),
    )


def _actual_label(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if text in ("1", "0", "2"):
        return text
    number = pd.to_numeric(value, errors="coerce")
    if not pd.isna(number) and float(number).is_integer():
        normalized = str(int(number))
        return normalized if normalized in ("1", "0", "2") else ""
    return ""


def _ticket_hit_distribution(
    plan: BetPlan,
    actuals_by_source_number: Mapping[int, str],
) -> dict[int, int]:
    """全組み合わせを展開せず、的中数ごとの券数を数える。"""

    distribution = {0: 1}
    for recommendation in plan.recommendations:
        source_number = recommendation.analysis.prediction.source_match_number
        actual = actuals_by_source_number[source_number]
        selected_count = len(recommendation.outcomes)
        correct_options = int(actual in recommendation.outcomes)
        incorrect_options = selected_count - correct_options
        next_distribution: dict[int, int] = {}
        for hit_count, ticket_count in distribution.items():
            if correct_options:
                next_distribution[hit_count + 1] = (
                    next_distribution.get(hit_count + 1, 0) + ticket_count
                )
            if incorrect_options:
                next_distribution[hit_count] = (
                    next_distribution.get(hit_count, 0)
                    + ticket_count * incorrect_options
                )
        distribution = next_distribution
    if sum(distribution.values()) != plan.ticket_count:
        raise BetOptimizationError("買い目の的中数分布と総口数が一致しません。")
    return distribution


def _round_payout(
    hit_distribution: Mapping[int, int],
    payouts: tuple[int, int, int],
) -> int:
    return (
        int(hit_distribution.get(13, 0)) * payouts[0]
        + int(hit_distribution.get(12, 0)) * payouts[1]
        + int(hit_distribution.get(11, 0)) * payouts[2]
    )
