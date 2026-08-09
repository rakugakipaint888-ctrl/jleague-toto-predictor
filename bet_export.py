"""Version7-C買い目の画面表・CSV出力。"""

from __future__ import annotations

import itertools

import pandas as pd

from bet_config import MAX_COMBINATION_EXPORT
from bet_optimizer import BET_TYPE_LABELS, BetOptimizationError, BetPlan


class CombinationLimitError(BetOptimizationError):
    """全組み合わせの安全な展開上限を超えた。"""


class BetPlanSchemaError(BetOptimizationError):
    """買い目表の生成スキーマと表示スキーマが一致しない。"""


# Streamlitのhot rerunで旧moduleが残った場合も契約差を検出できるよう、
# 買い目表の正式な列順をこのmoduleだけで管理する。
BET_PLAN_DISPLAY_SCHEMA_VERSION = 1
BET_PLAN_FRAME_COLUMNS = (
    "試合番号",
    "toto試合番号",
    "ホーム",
    "アウェイ",
    "P(1)",
    "P(0)",
    "P(2)",
    "1位予測",
    "2位予測",
    "確率差",
    "引分候補",
    "Entropy",
    "不確実性Score",
    "ダブル候補Score",
    "トリプル候補Score",
    "Draw Inclusion Score",
    "Draw Inclusion判定",
    "0入替Coverage低下",
    "シングル信頼度",
    "シングル信頼度Score",
    "推奨区分",
    "推奨買い目",
    "Coverage",
    "判定理由",
)
BET_PLAN_DISPLAY_COLUMNS = (
    "試合番号",
    "toto試合番号",
    "ホーム",
    "アウェイ",
    "P(1)",
    "P(0)",
    "P(2)",
    "1位予測",
    "2位予測",
    "確率差",
    "引分候補",
    "不確実性Score",
    "Draw Inclusion Score",
    "Draw Inclusion判定",
    "シングル信頼度",
    "推奨区分",
    "推奨買い目",
    "Coverage",
    "判定理由",
)
BET_PLAN_DISPLAY_ROUNDING = {
    "P(1)": 1,
    "P(0)": 1,
    "P(2)": 1,
    "確率差": 1,
    "不確実性Score": 1,
    "Coverage": 1,
}


def bet_plan_frame(plan: BetPlan) -> pd.DataFrame:
    """購入入力と監査に必要な試合別情報を1行ずつ返す。"""

    rows = []
    for recommendation in plan.recommendations:
        analysis = recommendation.analysis
        prediction = analysis.prediction
        rows.append(
            {
                "試合番号": prediction.match_number,
                "toto試合番号": prediction.source_match_number,
                "ホーム": prediction.home_team,
                "アウェイ": prediction.away_team,
                "P(1)": prediction.probability_1 * 100.0,
                "P(0)": prediction.probability_0 * 100.0,
                "P(2)": prediction.probability_2 * 100.0,
                "1位予測": analysis.top_outcome,
                "2位予測": analysis.second_outcome,
                "確率差": analysis.top_two_gap * 100.0,
                "引分候補": "候補" if analysis.draw_candidate else "—",
                "Entropy": analysis.normalized_entropy,
                "不確実性Score": analysis.uncertainty_score,
                "ダブル候補Score": analysis.double_candidate_score,
                "トリプル候補Score": analysis.triple_candidate_score,
                "Draw Inclusion Score": (
                    f"{analysis.draw_inclusion_score:.1f}"
                    if analysis.draw_inclusion_score is not None
                    else "—"
                ),
                "Draw Inclusion判定": (
                    "0採用候補"
                    if analysis.draw_inclusion_recommended
                    else (
                        "上位2維持"
                        if analysis.draw_inclusion_evaluated
                        else "対象外"
                    )
                ),
                "0入替Coverage低下": (
                    analysis.draw_inclusion_coverage_loss * 100.0
                ),
                "シングル信頼度": analysis.single_confidence,
                "シングル信頼度Score": analysis.single_confidence_score,
                "推奨区分": BET_TYPE_LABELS[recommendation.bet_type],
                "推奨買い目": "・".join(recommendation.outcomes),
                "Coverage": recommendation.coverage * 100.0,
                "判定理由": recommendation.reason,
            }
        )
    return pd.DataFrame(rows, columns=BET_PLAN_FRAME_COLUMNS)


def bet_plan_display_frame(plan: BetPlan) -> pd.DataFrame:
    """正式な表示列を検証し、toto／mini／手動案を同じ形で返す。"""

    frame = bet_plan_frame(plan)
    missing_columns = tuple(
        column
        for column in BET_PLAN_DISPLAY_COLUMNS
        if column not in frame.columns
    )
    if missing_columns:
        raise BetPlanSchemaError(
            "買い目表示スキーマの必須列が不足しています："
            + "、".join(missing_columns)
        )
    return frame.loc[:, list(BET_PLAN_DISPLAY_COLUMNS)].round(
        BET_PLAN_DISPLAY_ROUNDING
    )


def combination_frame(
    plan: BetPlan,
    *,
    max_combinations: int = MAX_COMBINATION_EXPORT,
) -> pd.DataFrame:
    """実際の全購入組み合わせを1口1行で展開する。"""

    if max_combinations <= 0:
        raise CombinationLimitError("組み合わせ展開上限は1以上にしてください。")
    if plan.ticket_count > max_combinations:
        raise CombinationLimitError(
            f"{plan.ticket_count:,}口は安全な展開上限"
            f"{max_combinations:,}口を超えています。"
        )
    columns = [
        f"第{recommendation.analysis.prediction.match_number}試合"
        for recommendation in plan.recommendations
    ]
    rows = (
        {"口番号": index, **dict(zip(columns, outcomes))}
        for index, outcomes in enumerate(
            itertools.product(
                *(recommendation.outcomes for recommendation in plan.recommendations)
            ),
            start=1,
        )
    )
    return pd.DataFrame(rows, columns=["口番号", *columns])


def bet_plan_csv_bytes(plan: BetPlan) -> bytes:
    return bet_plan_frame(plan).to_csv(index=False).encode("utf-8-sig")


def combination_csv_bytes(
    plan: BetPlan,
    *,
    max_combinations: int = MAX_COMBINATION_EXPORT,
) -> bytes:
    return combination_frame(
        plan,
        max_combinations=max_combinations,
    ).to_csv(index=False).encode("utf-8-sig")


def purchase_entry_text(plan: BetPlan) -> str:
    """公式購入画面へ転記しやすい試合順の文字列。"""

    return "\n".join(
        f"{recommendation.analysis.prediction.match_number}試合目 "
        f"{'・'.join(recommendation.outcomes)}"
        for recommendation in plan.recommendations
    )
