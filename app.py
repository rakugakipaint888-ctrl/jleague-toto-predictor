import pandas as pd
import streamlit as st

from data_loader import (
    TeamRecentStats,
    VenueRecord,
    get_match_defaults,
    load_matches,
)
from elo_rating import (
    EloCalculationResult,
    adjust_expected_goals,
    get_elo_cache_path,
    get_team_elo,
    load_or_calculate_elo,
)
from prediction import (
    calculate_expected_goals,
    calculate_match_probabilities,
    create_reason,
    get_confidence_label,
    get_toto_prediction,
)
from teams import (
    TEAM_CATEGORY_BY_NAME,
    TEAM_OPTIONS,
    format_team_option,
    normalize_team_name,
)


# --------------------------------------------------
# 基本設定
# --------------------------------------------------

st.set_page_config(
    page_title="Jリーグ toto予想",
    page_icon="⚽",
    layout="centered",
)


# --------------------------------------------------
# 画面補助関数
# --------------------------------------------------


def get_team_option_index(team_name: str):
    """取得データのクラブ名に対応するプルダウン位置を返す。"""

    for option_index, (_, option_team_name) in enumerate(
        TEAM_OPTIONS
    ):
        if option_team_name == team_name:
            return option_index

    # CSVに未登録のクラブ名があっても、未選択として安全に表示する。
    return None


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_match_data():
    """公式サイトへのアクセスを抑えるため6時間キャッシュする。"""

    return load_matches()


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_elo_data(completed_matches):
    """同じ試合履歴のEloはメモリ・ファイルキャッシュから返す。"""

    return load_or_calculate_elo(
        completed_matches,
        team_categories=TEAM_CATEGORY_BY_NAME,
        cache_path=get_elo_cache_path(),
        team_name_normalizer=normalize_team_name,
    )


def create_elo_table(elo_result: EloCalculationResult) -> pd.DataFrame:
    """カテゴリー内順位を付けた全60クラブのElo一覧を作る。"""

    rows = []

    for category in ("J1", "J2", "J3"):
        category_ratings = sorted(
            (
                rating
                for rating in elo_result.ratings.values()
                if rating.category == category
            ),
            key=lambda rating: rating.rating,
            reverse=True,
        )

        for rank, rating in enumerate(category_ratings, start=1):
            rows.append(
                {
                    "カテゴリー": category,
                    "順位": rank,
                    "チーム名": rating.team_name,
                    "Elo": round(rating.rating, 1),
                    "対象試合数": rating.matches_played,
                    "最終更新日": (
                        rating.last_updated.isoformat()
                        if rating.last_updated
                        else "データなし"
                    ),
                }
            )

    return pd.DataFrame(rows)


def format_elo_value(elo_value) -> str:
    """未取得を含む画面表示用のElo文字列を返す。"""

    return f"{float(elo_value):.1f}" if elo_value is not None else "未取得"


def apply_team_stats(
    match_number: int,
    side: str,
    team_stats: dict[str, TeamRecentStats],
) -> None:
    """チーム変更時に平均値・順位・会場別成績を反映する。"""

    selected_team = st.session_state.get(
        f"{side}_team_{match_number}"
    )

    if not selected_team:
        return

    # 選択肢は（カテゴリー、クラブ名）の組。
    team_name = selected_team[1]
    stats = team_stats.get(team_name)

    if not stats:
        return

    # コールバック内で更新するため、数値入力作成前に安全に反映できる。
    st.session_state[f"{side}_scored_{match_number}"] = float(
        stats.average_scored
    )
    st.session_state[f"{side}_conceded_{match_number}"] = float(
        stats.average_conceded
    )

    st.session_state[f"{side}_rank_{match_number}"] = stats.rank

    record = stats.home_record if side == "home" else stats.away_record

    for field_name in (
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
    ):
        st.session_state[f"{side}_{field_name}_{match_number}"] = int(
            getattr(record, field_name)
        )


def get_recent_matches(
    team_name: str,
    team_stats: dict[str, TeamRecentStats],
) -> tuple[str, ...]:
    """選択クラブの自動取得済み直近試合を返す。"""

    stats = team_stats.get(team_name)
    return stats.recent_matches if stats else ()


def get_team_detail_defaults(
    match_number: int,
    side: str,
    team_name: str,
    match_defaults: dict,
    team_stats: dict[str, TeamRecentStats],
) -> dict:
    """表示・編集に使う順位と会場別成績を返す。"""

    stats = team_stats.get(team_name)

    if stats:
        record = stats.home_record if side == "home" else stats.away_record
        defaults = {
            "rank": stats.rank,
            "wins": record.wins,
            "draws": record.draws,
            "losses": record.losses,
            "goals_for": record.goals_for,
            "goals_against": record.goals_against,
        }
    else:
        defaults = {
            "rank": match_defaults[f"{side}_rank"],
            "wins": match_defaults[f"{side}_wins"],
            "draws": match_defaults[f"{side}_draws"],
            "losses": match_defaults[f"{side}_losses"],
            "goals_for": match_defaults[f"{side}_goals_for"],
            "goals_against": match_defaults[f"{side}_goals_against"],
        }

    # 一度ユーザーが修正した値は、同じチームの間は維持する。
    for field_name in defaults:
        session_key = f"{side}_{field_name}_{match_number}"
        if session_key in st.session_state:
            defaults[field_name] = st.session_state[session_key]

    return defaults


def detail_values_to_record(detail_values: dict) -> VenueRecord:
    """画面用の辞書を構造化された会場別成績へ変換する。"""

    wins = int(detail_values["wins"])
    draws = int(detail_values["draws"])
    losses = int(detail_values["losses"])

    return VenueRecord(
        played=wins + draws + losses,
        wins=wins,
        draws=draws,
        losses=losses,
        goals_for=int(detail_values["goals_for"]),
        goals_against=int(detail_values["goals_against"]),
    )


def format_detail_summary(
    rank,
    record: VenueRecord,
    venue_label: str,
) -> str:
    """順位と会場別成績を1行で表示する。"""

    rank_label = f"{int(rank)}位" if rank is not None else "順位未確定"
    return f"{rank_label}｜{venue_label}：{record.label}"


def create_detail_inputs(
    match_number: int,
    side: str,
    defaults: dict,
) -> dict:
    """選択中の1試合だけ、順位と会場別成績を編集可能にする。"""

    rank_key = f"{side}_rank_{match_number}"
    rank_options = {
        "label": "順位",
        "min_value": 1,
        "max_value": 60,
        "step": 1,
        "key": rank_key,
        "placeholder": "未確定",
    }

    if rank_key not in st.session_state:
        rank_options["value"] = (
            int(defaults["rank"])
            if defaults["rank"] is not None
            else None
        )

    rank = st.number_input(**rank_options)

    values = {"rank": rank}
    labels = {
        "wins": "勝",
        "draws": "分",
        "losses": "敗",
        "goals_for": "得点",
        "goals_against": "失点",
    }

    result_columns = st.columns(3)

    for field_index, field_name in enumerate(("wins", "draws", "losses")):
        with result_columns[field_index]:
            input_key = f"{side}_{field_name}_{match_number}"
            input_options = {
                "label": labels[field_name],
                "min_value": 0,
                "max_value": 99,
                "step": 1,
                "key": input_key,
            }
            if input_key not in st.session_state:
                input_options["value"] = int(defaults[field_name])
            values[field_name] = st.number_input(**input_options)

    score_columns = st.columns(2)

    for field_index, field_name in enumerate(("goals_for", "goals_against")):
        with score_columns[field_index]:
            input_key = f"{side}_{field_name}_{match_number}"
            input_options = {
                "label": labels[field_name],
                "min_value": 0,
                "max_value": 999,
                "step": 1,
                "key": input_key,
            }
            if input_key not in st.session_state:
                input_options["value"] = int(defaults[field_name])
            values[field_name] = st.number_input(**input_options)

    return values


def create_average_input(
    label: str,
    key: str,
    default_value: float,
) -> float:
    """初回値とチーム変更時のSession Stateを警告なく両立する。"""

    input_options = {
        "label": label,
        "min_value": 0.0,
        "max_value": 5.0,
        "step": 0.1,
        "key": key,
    }

    # コールバックで値が入っている場合は、valueを重ねて指定しない。
    if key not in st.session_state:
        input_options["value"] = float(default_value)

    return st.number_input(**input_options)


# --------------------------------------------------
# 画面
# --------------------------------------------------

st.title("⚽ Jリーグ toto予想")

st.caption(
    "Jリーグ公式データの直近5試合から、"
    "13試合の勝敗確率を計算します。"
)

st.warning(
    "このアプリはVersion 4の試作モデルです。"
    "的中や利益を保証するものではありません。"
)

# app.pyは取得元を直接扱わず、data_loader.pyから共通形式で受け取る。
# 公式データとCSVが利用できなくても空データが返り、手入力で利用できる。
match_data_result = load_match_data()

if match_data_result.is_loaded:
    st.success(match_data_result.message)
else:
    # 技術的なエラー内容は出さず、そのまま利用できる方法だけを案内する。
    st.info(match_data_result.message)

elo_result = None

if match_data_result.completed_matches:
    try:
        elo_result = load_elo_data(match_data_result.completed_matches)
    except Exception:
        # キャッシュ破損や想定外データでもVersion3の予測は継続する。
        elo_result = None

elo_available = bool(elo_result and elo_result.is_available)

use_elo_adjustment = st.toggle(
    "Elo補正を使用する",
    value=True,
    help=(
        "ONはVersion4、OFFはVersion3と同じ期待得点で計算します。"
    ),
    key="use_elo_adjustment",
)

elo_adjustment_enabled = bool(use_elo_adjustment and elo_available)

if not elo_available:
    st.warning(
        "Eloデータを取得できないため、Elo補正なしで計算しました。"
    )

with st.expander("入力方法を見る"):
    st.write(
        """
        チームを選ぶと、取得できた直近5試合から
        平均得点・平均失点・順位・ホーム／アウェイ成績を自動入力します。
        自動入力後の数字は自由に修正できます。

        Elo補正をONにすると、公式試合結果から計算した実力差を
        Version3の期待得点へ最大±15%の範囲で反映します。

        Jリーグ公式データを取得できない場合はCSV、CSVもない場合は
        手入力へ自動で切り替わります。
        """
    )

edit_detail_stats = st.toggle(
    "順位・ホーム／アウェイ成績を修正する",
    value=False,
    help=(
        "予測計算は平均得点・平均失点と、ONの場合はEloを使用します。"
        "順位と会場別成績はVersion5以降の分析機能用です。"
    ),
    key="edit_detail_stats",
)

editable_match_number = None

if edit_detail_stats:
    editable_match_number = st.selectbox(
        "詳細データを修正する試合",
        options=range(1, 14),
        format_func=lambda number: f"第{number}試合",
    )


# --------------------------------------------------
# 13試合分の入力
# --------------------------------------------------

match_inputs = []

for match_number in range(1, 14):

    st.subheader(f"第{match_number}試合")

    match_defaults = get_match_defaults(
        match_data_result.matches,
        match_number,
    )

    if match_defaults["match_date"]:
        st.caption(f'試合日：{match_defaults["match_date"]}')

    selected_home_team = st.selectbox(
        "ホームチーム",
        options=TEAM_OPTIONS,
        index=get_team_option_index(
            match_defaults["home_team"]
        ),
        format_func=format_team_option,
        placeholder="カテゴリーからチームを選択",
        key=f"home_team_{match_number}",
        on_change=apply_team_stats,
        args=(
            match_number,
            "home",
            match_data_result.team_stats,
        ),
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
        on_change=apply_team_stats,
        args=(
            match_number,
            "away",
            match_data_result.team_stats,
        ),
    )

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

    home_elo = (
        get_team_elo(
            home_team,
            elo_result,
            team_name_normalizer=normalize_team_name,
        )
        if elo_available
        else None
    )
    away_elo = (
        get_team_elo(
            away_team,
            elo_result,
            team_name_normalizer=normalize_team_name,
        )
        if elo_available
        else None
    )
    elo_difference = (
        home_elo - away_elo
        if home_elo is not None and away_elo is not None
        else None
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**ホーム直近5試合**")

        home_scored = create_average_input(
            label="平均得点",
            key=f"home_scored_{match_number}",
            default_value=match_defaults["home_scored"],
        )

        home_conceded = create_average_input(
            label="平均失点",
            key=f"home_conceded_{match_number}",
            default_value=match_defaults["home_conceded"],
        )

    with col2:
        st.markdown("**アウェイ直近5試合**")

        away_scored = create_average_input(
            label="平均得点",
            key=f"away_scored_{match_number}",
            default_value=match_defaults["away_scored"],
        )

        away_conceded = create_average_input(
            label="平均失点",
            key=f"away_conceded_{match_number}",
            default_value=match_defaults["away_conceded"],
        )

    home_detail_values = get_team_detail_defaults(
        match_number=match_number,
        side="home",
        team_name=home_team,
        match_defaults=match_defaults,
        team_stats=match_data_result.team_stats,
    )
    away_detail_values = get_team_detail_defaults(
        match_number=match_number,
        side="away",
        team_name=away_team,
        match_defaults=match_defaults,
        team_stats=match_data_result.team_stats,
    )

    if editable_match_number == match_number:
        with st.expander("順位・会場別成績を修正", expanded=True):
            detail_col1, detail_col2 = st.columns(2)

            with detail_col1:
                st.markdown("**ホームチーム**")
                home_detail_values = create_detail_inputs(
                    match_number,
                    "home",
                    home_detail_values,
                )

            with detail_col2:
                st.markdown("**アウェイチーム**")
                away_detail_values = create_detail_inputs(
                    match_number,
                    "away",
                    away_detail_values,
                )

    home_record = detail_values_to_record(home_detail_values)
    away_record = detail_values_to_record(away_detail_values)

    detail_summary_col1, detail_summary_col2 = st.columns(2)

    with detail_summary_col1:
        st.caption(
            format_detail_summary(
                home_detail_values["rank"],
                home_record,
                "ホーム成績",
            )
        )
        st.caption(f"ホームElo：{format_elo_value(home_elo)}")

    with detail_summary_col2:
        st.caption(
            format_detail_summary(
                away_detail_values["rank"],
                away_record,
                "アウェイ成績",
            )
        )
        st.caption(f"アウェイElo：{format_elo_value(away_elo)}")

    if elo_difference is not None:
        st.caption(f"Elo差（ホーム－アウェイ）：{elo_difference:+.1f}")

    home_recent_matches = get_recent_matches(
        home_team,
        match_data_result.team_stats,
    )
    away_recent_matches = get_recent_matches(
        away_team,
        match_data_result.team_stats,
    )

    if home_recent_matches or away_recent_matches:
        with st.expander("自動取得した直近5試合を見る"):
            if home_recent_matches:
                st.write(f"**{home_team}**")
                for recent_match in home_recent_matches:
                    st.caption(recent_match)
            if away_recent_matches:
                st.write(f"**{away_team}**")
                for recent_match in away_recent_matches:
                    st.caption(recent_match)

    match_inputs.append(
        {
            "match_number": match_number,
            "home_team": home_team.strip(),
            "away_team": away_team.strip(),
            "home_scored": home_scored,
            "home_conceded": home_conceded,
            "away_scored": away_scored,
            "away_conceded": away_conceded,
            "home_rank": home_detail_values["rank"],
            "away_rank": away_detail_values["rank"],
            "home_record": home_record,
            "away_record": away_record,
            "home_elo": home_elo,
            "away_elo": away_elo,
            "elo_difference": elo_difference,
        }
    )

    st.divider()

submitted = st.button(
    "13試合を予想する",
    type="primary",
    width="stretch",
)


# --------------------------------------------------
# 予想結果
# --------------------------------------------------

if submitted:

    results = []

    for match in match_inputs:

        try:
            (
                home_expected_before_elo,
                away_expected_before_elo,
            ) = calculate_expected_goals(
                home_scored=match["home_scored"],
                home_conceded=match["home_conceded"],
                away_scored=match["away_scored"],
                away_conceded=match["away_conceded"],
            )

            match_elo_enabled = bool(
                elo_adjustment_enabled
                and match["home_elo"] is not None
                and match["away_elo"] is not None
            )

            if (
                match["home_elo"] is not None
                and match["away_elo"] is not None
            ):
                expected_goals = adjust_expected_goals(
                    home_expected=home_expected_before_elo,
                    away_expected=away_expected_before_elo,
                    home_elo=match["home_elo"],
                    away_elo=match["away_elo"],
                    enabled=match_elo_enabled,
                )
                home_expected_after_elo = expected_goals.home_after
                away_expected_after_elo = expected_goals.away_after
            else:
                home_expected_after_elo = home_expected_before_elo
                away_expected_after_elo = away_expected_before_elo

            probabilities = calculate_match_probabilities(
                home_expected=home_expected_after_elo,
                away_expected=away_expected_after_elo,
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
                home_expected=home_expected_after_elo,
                away_expected=away_expected_after_elo,
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
                    "home_elo": (
                        round(match["home_elo"], 2)
                        if match["home_elo"] is not None
                        else None
                    ),
                    "away_elo": (
                        round(match["away_elo"], 2)
                        if match["away_elo"] is not None
                        else None
                    ),
                    "elo_difference": (
                        round(match["elo_difference"], 2)
                        if match["elo_difference"] is not None
                        else None
                    ),
                    "home_expected_before_elo": round(
                        home_expected_before_elo,
                        4,
                    ),
                    "away_expected_before_elo": round(
                        away_expected_before_elo,
                        4,
                    ),
                    "home_expected_after_elo": round(
                        home_expected_after_elo,
                        4,
                    ),
                    "away_expected_after_elo": round(
                        away_expected_after_elo,
                        4,
                    ),
                    "elo_adjustment_enabled": match_elo_enabled,
                }
            )

        except (ValueError, OverflowError) as error:
            st.error(
                f'第{match["match_number"]}試合で'
                f'計算エラーが発生しました：{error}'
            )

    if results:

        result_df = pd.DataFrame(results)
        st.session_state["latest_prediction_results"] = result_df.copy()

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
            width="stretch",
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

                st.write(
                    "Elo："
                    f'ホーム {format_elo_value(result["home_elo"])} ／ '
                    f'アウェイ {format_elo_value(result["away_elo"])} ／ '
                    "差 "
                    + (
                        f'{result["elo_difference"]:+.1f}'
                        if result["elo_difference"] is not None
                        else "未取得"
                    )
                )

                st.write(
                    "期待得点："
                    f'補正前 {result["home_expected_before_elo"]:.2f}'
                    f'－{result["away_expected_before_elo"]:.2f} ／ '
                    f'補正後 {result["home_expected_after_elo"]:.2f}'
                    f'－{result["away_expected_after_elo"]:.2f} ／ '
                    "Elo補正 "
                    f'{"ON" if result["elo_adjustment_enabled"] else "OFF"}'
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
            width="stretch",
        )

        st.caption(
            "確率は統計モデルによる推定値です。"
            "実際の結果や的中を保証するものではありません。"
        )


# --------------------------------------------------
# 全クラブElo一覧
# --------------------------------------------------

if elo_available:
    with st.expander("J1・J2・J3 全クラブの現在Elo一覧"):
        elo_sort_mode = st.selectbox(
            "並べ替え",
            options=("Elo順（高い順）", "カテゴリー・順位順"),
            key="elo_sort_mode",
        )
        elo_table = create_elo_table(elo_result)

        if elo_sort_mode == "Elo順（高い順）":
            elo_table = elo_table.sort_values(
                ["Elo", "チーム名"],
                ascending=[False, True],
            )
        else:
            category_order = {"J1": 1, "J2": 2, "J3": 3}
            elo_table = (
                elo_table.assign(
                    _category_order=elo_table["カテゴリー"].map(
                        category_order
                    )
                )
                .sort_values(["_category_order", "順位"])
                .drop(columns="_category_order")
            )

        st.dataframe(
            elo_table.reset_index(drop=True),
            width="stretch",
            hide_index=True,
        )

        if elo_result.data_start_date and elo_result.data_end_date:
            st.caption(
                "対象期間："
                f"{elo_result.data_start_date.isoformat()}～"
                f"{elo_result.data_end_date.isoformat()} ／ "
                f"完了試合 {elo_result.processed_match_count}件"
            )
