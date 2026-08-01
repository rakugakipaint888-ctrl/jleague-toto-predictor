"""toto予想の計算ロジックをまとめる。

画面やデータ取得から独立させ、将来ELO・ホーム補正・AI分析を追加するときに
変更範囲を限定できるようにする。Version 2までの計算式は変更していない。
"""

import math


def poisson_probability(goals: int, expected_goals: float) -> float:
    """ポアソン分布で指定得点になる確率を計算する。"""

    return (
        math.exp(-expected_goals)
        * expected_goals**goals
        / math.factorial(goals)
    )


def calculate_expected_goals(
    home_scored: float,
    home_conceded: float,
    away_scored: float,
    away_conceded: float,
) -> tuple[float, float]:
    """両チームの直近成績から期待得点を計算する。"""

    # Version 1から使っている8％のホーム補正を維持する。
    home_expected = ((home_scored + away_conceded) / 2) * 1.08
    away_expected = (away_scored + home_conceded) / 2

    # 極端な数値を防ぐ。
    home_expected = max(0.15, min(home_expected, 4.0))
    away_expected = max(0.15, min(away_expected, 4.0))

    return home_expected, away_expected


def calculate_match_probabilities(
    home_expected: float,
    away_expected: float,
) -> dict:
    """0～6得点のスコア確率から1・0・2を集計する。"""

    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    score_probabilities = []

    for home_goals in range(7):
        home_probability = poisson_probability(home_goals, home_expected)

        for away_goals in range(7):
            away_probability = poisson_probability(away_goals, away_expected)
            probability = home_probability * away_probability

            score_probabilities.append(
                {
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "probability": probability,
                }
            )

            if home_goals > away_goals:
                home_win += probability
            elif home_goals == away_goals:
                draw += probability
            else:
                away_win += probability

    total = home_win + draw + away_win

    if total <= 0:
        raise ValueError("勝敗確率を計算できませんでした。")

    # 合計を100％に調整する。
    home_win /= total
    draw /= total
    away_win /= total

    most_likely_score = max(
        score_probabilities,
        key=lambda item: item["probability"],
    )

    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "home_goals": most_likely_score["home_goals"],
        "away_goals": most_likely_score["away_goals"],
    }


def get_toto_prediction(
    home_win: float,
    draw: float,
    away_win: float,
) -> tuple[str, float]:
    """最も確率が高い結果をtoto表記で返す。"""

    probabilities = {
        "1": home_win,
        "0": draw,
        "2": away_win,
    }
    prediction = max(probabilities, key=probabilities.get)

    return prediction, probabilities[prediction]


def get_confidence_label(probabilities: list[float]) -> str:
    """1位と2位の確率差から予想の信頼度を判定する。"""

    sorted_probabilities = sorted(probabilities, reverse=True)
    difference = sorted_probabilities[0] - sorted_probabilities[1]
    top_probability = sorted_probabilities[0]

    if top_probability >= 0.60 and difference >= 0.20:
        return "鉄板候補"

    if top_probability >= 0.48 and difference >= 0.10:
        return "本命"

    if difference <= 0.05:
        return "大接戦"

    return "接戦"


def create_reason(
    home_expected: float,
    away_expected: float,
    home_win: float,
    draw: float,
    away_win: float,
) -> str:
    """計算結果について簡単な説明を作る。"""

    if draw >= home_win and draw >= away_win:
        return (
            "両チームの期待得点が近く、"
            "引き分け確率が最も高くなっています。"
        )

    difference = home_expected - away_expected

    if difference >= 0.70:
        return (
            "ホーム側の期待得点がアウェイ側を"
            "大きく上回っています。"
        )

    if difference <= -0.70:
        return (
            "アウェイ側の期待得点がホーム側を"
            "大きく上回っています。"
        )

    return (
        "両チームの期待得点差が小さく、"
        "結果が分かれやすい試合です。"
    )
