import math

import pandas as pd
import streamlit as st

from data_loader import get_match_defaults, load_matches
from teams import TEAM_OPTIONS, format_team_option


# --------------------------------------------------
# 基本設定
# --------------------------------------------------

st.set_page_config(
    page_title="Jリーグ toto予想",
    page_icon="⚽",
    layout="centered",
)


# --------------------------------------------------
# 計算用関数
# --------------------------------------------------

def poisson_probability(goals: int, expected_goals: float) -> float:
    """
    ポアソン分布を使って、指定得点になる確率を計算する。
    """
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
    """
    両チームの直近成績から期待得点を計算する。

    Version 1では、ホーム側に8％のホーム補正を加える。
    """

    home_expected = (
        (home_scored + away_conceded) / 2
    ) * 1.08

    away_expected = (
        away_scored + home_conceded
    ) / 2

    # 極端な数値を防ぐ
    home_expected = max(0.15, min(home_expected, 4.0))
    away_expected = max(0.15, min(away_expected, 4.0))

    return home_expected, away_expected


def calculate_match_probabilities(
    home_expected: float,
    away_expected: float,
) -> dict:
    """
    0～6得点までのスコア確率を計算し、
    ホーム勝ち・引き分け・アウェイ勝ちを集計する。
    """

    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    score_probabilities = []

    for home_goals in range(7):
        home_probability = poisson_probability(
            home_goals,
            home_expected,
        )

        for away_goals in range(7):
            away_probability = poisson_probability(
                away_goals,
                away_expected,
            )

            probability = (
                home_probability * away_probability
            )

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
        raise ValueError(
            "勝敗確率を計算できませんでした。"
        )

    # 合計を100％に調整
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
    """
    最も確率が高い結果をtoto表記で返す。
    """

    probabilities = {
        "1": home_win,
        "0": draw,
        "2": away_win,
    }

    prediction = max(
        probabilities,
        key=probabilities.get,
    )

    return prediction, probabilities[prediction]


def get_confidence_label(
    probabilities: list[float],
) -> str:
    """
    1位と2位の確率差から予想の信頼度を判定する。
    """

    sorted_probabilities = sorted(
        probabilities,
        reverse=True,
    )

    difference = (
        sorted_probabilities[0]
        - sorted_probabilities[1]
    )

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
    """
    計算結果について簡単な説明を作る。
    """

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


def get_team_option_index(team_name: str):
    """CSVのクラブ名に対応するプルダウン位置を返す。"""

    for option_index, (_, option_team_name) in enumerate(
        TEAM_OPTIONS
    ):
        if option_team_name == team_name:
            return option_index

    # CSVに未登録のクラブ名があっても、未選択として安全に表示する。
    return None


# --------------------------------------------------
# 画面
# --------------------------------------------------

st.title("⚽ Jリーグ toto予想")

st.caption(
    "直近5試合の平均得点・平均失点から、"
    "13試合の勝敗確率を計算します。"
)

st.warning(
    "このアプリはVersion 2の試作モデルです。"
    "的中や利益を保証するものではありません。"
)

# app.pyは保存場所を直接扱わず、data_loader.pyから受け取る。
# CSVがない場合も空データが返るため、手入力でそのまま利用できる。
match_data_result = load_matches()

if match_data_result.is_loaded:
    st.success(match_data_result.message)
elif match_data_result.status == "error":
    st.warning(match_data_result.message)
else:
    st.info(match_data_result.message)

with st.expander("入力方法を見る"):
    st.write(
        """
        各チームについて、直近5試合の得点と失点を
        合計し、5で割った数字を入力してください。

        例：直近5試合の得点が
        2、1、0、3、1なら、平均得点は1.4です。
        """
    )


# --------------------------------------------------
# 13試合分の入力
# --------------------------------------------------

match_inputs = []

with st.form("prediction_form"):

    for match_number in range(1, 14):

        st.subheader(f"第{match_number}試合")

        match_defaults = get_match_defaults(
            match_data_result.matches,
            match_number,
        )

        selected_home_team = st.selectbox(
            "ホームチーム",
            options=TEAM_OPTIONS,
            index=get_team_option_index(
                match_defaults["home_team"]
            ),
            format_func=format_team_option,
            placeholder="カテゴリーからチームを選択",
            key=f"home_team_{match_number}",
        )

        selected_away_team = st.selectbox(
            "アウェイチーム",
            options=TEAM_OPTIONS,
            index=get_team_option_index(
                match_defaults["away_team"]
            ),
            format_func=format_team_option,
            placeholder="カテゴリーからチームを選択",
            key=f"away_team_{match_number}",
        )

        # 選択肢は（カテゴリー、クラブ名）の組。
        # 計算結果やCSVには、従来どおりクラブ名だけを渡す。
        home_team = (
            selected_home_team[1]
            if selected_home_team
            else ""
        )

        away_team = (
            selected_away_team[1]
            if selected_away_team
            else ""
        )

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**ホーム直近5試合**")

            home_scored = st.number_input(
                "平均得点",
                min_value=0.0,
                max_value=5.0,
                value=float(match_defaults["home_scored"]),
                step=0.1,
                key=f"home_scored_{match_number}",
            )

            home_conceded = st.number_input(
                "平均失点",
                min_value=0.0,
                max_value=5.0,
                value=float(match_defaults["home_conceded"]),
                step=0.1,
                key=f"home_conceded_{match_number}",
            )

        with col2:
            st.markdown("**アウェイ直近5試合**")

            away_scored = st.number_input(
                "平均得点",
                min_value=0.0,
                max_value=5.0,
                value=float(match_defaults["away_scored"]),
                step=0.1,
                key=f"away_scored_{match_number}",
            )

            away_conceded = st.number_input(
                "平均失点",
                min_value=0.0,
                max_value=5.0,
                value=float(match_defaults["away_conceded"]),
                step=0.1,
                key=f"away_conceded_{match_number}",
            )

        match_inputs.append(
            {
                "match_number": match_number,
                "home_team": home_team.strip(),
                "away_team": away_team.strip(),
                "home_scored": home_scored,
                "home_conceded": home_conceded,
                "away_scored": away_scored,
                "away_conceded": away_conceded,
            }
        )

        st.divider()

    submitted = st.form_submit_button(
        "13試合を予想する",
        type="primary",
        use_container_width=True,
    )


# --------------------------------------------------
# 予想結果
# --------------------------------------------------

if submitted:

    results = []

    for match in match_inputs:

        try:
            (
                home_expected,
                away_expected,
            ) = calculate_expected_goals(
                home_scored=match["home_scored"],
                home_conceded=match["home_conceded"],
                away_scored=match["away_scored"],
                away_conceded=match["away_conceded"],
            )

            probabilities = calculate_match_probabilities(
                home_expected=home_expected,
                away_expected=away_expected,
            )

            prediction, top_probability = get_toto_prediction(
                home_win=probabilities["home_win"],
                draw=probabilities["draw"],
                away_win=probabilities["away_win"],
            )

            confidence = get_confidence_label(
                [
                    probabilities["home_win"],
                    probabilities["draw"],
                    probabilities["away_win"],
                ]
            )

            reason = create_reason(
                home_expected=home_expected,
                away_expected=away_expected,
                home_win=probabilities["home_win"],
                draw=probabilities["draw"],
                away_win=probabilities["away_win"],
            )

            results.append(
                {
                    "試合": match["match_number"],
                    "対戦カード": (
                        f'{match["home_team"]}'
                        f' vs '
                        f'{match["away_team"]}'
                    ),
                    "1": round(
                        probabilities["home_win"] * 100,
                        1,
                    ),
                    "0": round(
                        probabilities["draw"] * 100,
                        1,
                    ),
                    "2": round(
                        probabilities["away_win"] * 100,
                        1,
                    ),
                    "本命": prediction,
                    "最高確率": round(
                        top_probability * 100,
                        1,
                    ),
                    "判定": confidence,
                    "予想スコア": (
                        f'{probabilities["home_goals"]}'
                        f'−'
                        f'{probabilities["away_goals"]}'
                    ),
                    "予想理由": reason,
                }
            )

        except (ValueError, OverflowError) as error:
            st.error(
                f'第{match["match_number"]}試合で'
                f'計算エラーが発生しました：{error}'
            )

    if results:

        result_df = pd.DataFrame(results)

        st.success("13試合の予想が完了しました。")

        st.header("本命予想")

        toto_prediction = "・".join(
            result_df["本命"].astype(str).tolist()
        )

        st.code(toto_prediction)

        st.caption(
            "左から第1試合、第2試合…第13試合の順です。"
        )

        st.header("予想一覧")

        st.dataframe(
            result_df[
                [
                    "試合",
                    "対戦カード",
                    "1",
                    "0",
                    "2",
                    "本命",
                    "判定",
                    "予想スコア",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.header("試合別の詳細")

        for result in results:

            with st.expander(
                f'第{result["試合"]}試合 '
                f'{result["対戦カード"]}'
            ):
                col1, col2, col3 = st.columns(3)

                col1.metric(
                    "1・ホーム勝ち",
                    f'{result["1"]:.1f}%',
                )

                col2.metric(
                    "0・引き分け",
                    f'{result["0"]:.1f}%',
                )

                col3.metric(
                    "2・アウェイ勝ち",
                    f'{result["2"]:.1f}%',
                )

                st.write(
                    f'**本命予想：{result["本命"]}**'
                )

                st.write(
                    f'予想スコア：'
                    f'{result["予想スコア"]}'
                )

                st.write(
                    f'判定：{result["判定"]}'
                )

                st.info(result["予想理由"])

        st.header("CSV保存")

        csv_data = result_df.to_csv(
            index=False,
        ).encode("utf-8-sig")

        st.download_button(
            label="予想結果をCSVで保存",
            data=csv_data,
            file_name="toto_prediction.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.caption(
            "確率は統計モデルによる推定値です。"
            "実際の結果や的中を保証するものではありません。"
        )
