from dataclasses import replace
from datetime import datetime

import pandas as pd
import streamlit as st

# Streamlit再実行時の旧model_configを、他のプロジェクトmoduleより先に検査する。
from version7b_config import ensure_version7b_model_config
from analysis import render_analysis_tab
from draw_analysis import render_draw_analysis_tab
from draw_optimizer import load_active_draw_settings
from draw_predictor import (
    build_draw_context,
    predict_draw_aware,
    probability_percentages,
)
from data_loader import (
    TeamRecentStats,
    VenueRecord,
    get_match_defaults,
    load_matches,
)
from history_manager import (
    JAPAN_TIMEZONE,
    TOTO_ROUND_CACHE_VERSION,
    TotoHistoryManager,
    create_matches_from_toto_round,
)
from elo_rating import (
    EloCalculationResult,
    get_elo_cache_path,
    get_team_elo,
    load_or_calculate_elo,
)
from model_config import DEFAULT_ELO_SETTINGS, OFFICIAL_RESULTS_CACHE_VERSION
from model_optimization_ui import render_model_optimization_tab
from model_pipeline import (
    ModelOptions,
    TeamModelInput,
    predict_match,
)
from parameter_manager import (
    load_active_version7b_settings,
    to_runtime_settings,
)
from prediction import (
    create_reason,
    get_confidence_label,
)
from prediction_history import (
    PredictionHistoryManager,
    finalize_prediction_results,
)
from teams import (
    TEAM_CATEGORY_BY_NAME,
    TEAM_OPTIONS,
    format_team_option,
    normalize_team_name,
)

ensure_version7b_model_config()


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
def load_match_data(
    cache_version: int = OFFICIAL_RESULTS_CACHE_VERSION,
):
    """スキーマ版をキーに含めて公式データを6時間キャッシュする。"""

    _ = cache_version
    return load_matches()


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_elo_data(completed_matches, elo_settings):
    """同じ試合履歴のEloはメモリ・ファイルキャッシュから返す。"""

    return load_or_calculate_elo(
        completed_matches,
        team_categories=TEAM_CATEGORY_BY_NAME,
        settings=elo_settings,
        cache_path=get_elo_cache_path(),
        team_name_normalizer=normalize_team_name,
    )


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_current_toto_round(current_matches, cache_version: int):
    """toto公式回次・第1～13試合を6時間キャッシュする。"""

    _ = cache_version
    return TotoHistoryManager().load_current_round(current_matches)


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


def round_optional(value, digits: int = 4):
    """欠損値を保持したままCSV用に丸める。"""

    return (
        round(float(value), digits)
        if not is_missing_value(value)
        else None
    )


def is_missing_value(value) -> bool:
    """NoneとpandasのNaNを同じ欠損として扱う。"""

    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def format_optional(value, digits: int = 2, signed: bool = False) -> str:
    """詳細画面で欠損値を安全に表示する。"""

    if is_missing_value(value):
        return "未取得"
    format_spec = f"+.{digits}f" if signed else f".{digits}f"
    return format(float(value), format_spec)


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
    st.session_state[f"{side}_points_{match_number}"] = stats.points
    st.session_state[f"{side}_season_played_{match_number}"] = stats.played
    st.session_state[f"{side}_season_wins_{match_number}"] = stats.wins
    st.session_state[f"{side}_season_draws_{match_number}"] = stats.draws
    st.session_state[f"{side}_season_losses_{match_number}"] = stats.losses
    st.session_state[f"{side}_season_goals_for_{match_number}"] = (
        stats.goals_for
    )
    st.session_state[f"{side}_season_goals_against_{match_number}"] = (
        stats.goals_against
    )
    st.session_state[f"{side}_goal_difference_{match_number}"] = (
        stats.goal_difference
    )
    st.session_state[f"{side}_standings_available_{match_number}"] = (
        stats.standings_available
    )

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


def get_team_stats(
    team_name: str,
    team_stats: dict[str, TeamRecentStats],
) -> TeamRecentStats | None:
    """選択クラブの構造化統計を返す。"""

    return team_stats.get(team_name)


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
            "points": stats.points,
            "season_played": stats.played,
            "season_wins": stats.wins,
            "season_draws": stats.draws,
            "season_losses": stats.losses,
            "season_goals_for": stats.goals_for,
            "season_goals_against": stats.goals_against,
            "goal_difference": stats.goal_difference,
            "standings_available": stats.standings_available,
            "wins": record.wins,
            "draws": record.draws,
            "losses": record.losses,
            "goals_for": record.goals_for,
            "goals_against": record.goals_against,
        }
    else:
        defaults = {
            "rank": match_defaults[f"{side}_rank"],
            "points": match_defaults[f"{side}_points"],
            "season_played": match_defaults[f"{side}_season_played"],
            "season_wins": match_defaults[f"{side}_season_wins"],
            "season_draws": match_defaults[f"{side}_season_draws"],
            "season_losses": match_defaults[f"{side}_season_losses"],
            "season_goals_for": match_defaults[
                f"{side}_season_goals_for"
            ],
            "season_goals_against": match_defaults[
                f"{side}_season_goals_against"
            ],
            "goal_difference": match_defaults[
                f"{side}_goal_difference"
            ],
            "standings_available": bool(
                match_defaults[f"{side}_season_played"] > 0
                and match_defaults[f"{side}_points"] is not None
                and match_defaults[f"{side}_goal_difference"] is not None
            ),
            "wins": match_defaults[f"{side}_wins"],
            "draws": match_defaults[f"{side}_draws"],
            "losses": match_defaults[f"{side}_losses"],
            "goals_for": match_defaults[f"{side}_goals_for"],
            "goals_against": match_defaults[f"{side}_goals_against"],
        }

    original_standings_available = bool(defaults["standings_available"])
    availability_key = f"{side}_standings_available_{match_number}"

    # 一度ユーザーが修正した値は、同じチームの間は維持する。
    for field_name in defaults:
        if field_name == "standings_available":
            continue
        session_key = f"{side}_{field_name}_{match_number}"
        if session_key in st.session_state:
            defaults[field_name] = st.session_state[session_key]

    if availability_key in st.session_state:
        defaults["standings_available"] = bool(
            st.session_state[availability_key]
        )
    else:
        defaults["standings_available"] = original_standings_available

    for field_name in ("rank", "points", "goal_difference"):
        if is_missing_value(defaults[field_name]):
            defaults[field_name] = None
    for field_name in (
        "season_played",
        "season_wins",
        "season_draws",
        "season_losses",
        "season_goals_for",
        "season_goals_against",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
    ):
        if is_missing_value(defaults[field_name]):
            defaults[field_name] = 0
    if (
        defaults["season_played"] <= 0
        or defaults["points"] is None
        or defaults["goal_difference"] is None
    ):
        defaults["standings_available"] = False

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

    rank_label = (
        f"{int(rank)}位"
        if not is_missing_value(rank)
        else "順位未確定"
    )
    return f"{rank_label}｜{venue_label}：{record.label}"


def format_standings_summary(detail_values: dict) -> str:
    """順位表の勝点・試合数・得失点差を安全に表示する。"""

    points = detail_values.get("points")
    goal_difference = detail_values.get("goal_difference")
    points_label = (
        str(int(points))
        if not is_missing_value(points)
        else "未取得"
    )
    goal_difference_label = (
        f"{int(goal_difference):+d}"
        if not is_missing_value(goal_difference)
        else "未取得"
    )
    return (
        f"勝点 {points_label}｜{int(detail_values['season_played'])}試合｜"
        f"得失点差 {goal_difference_label}"
    )


def season_average(detail_values: dict, field_name: str):
    """順位表の試合数と得失点からシーズン平均を返す。"""

    played = int(detail_values.get("season_played", 0))
    if played <= 0:
        return None
    return float(detail_values[field_name]) / played


def create_detail_inputs(
    match_number: int,
    side: str,
    defaults: dict,
) -> dict:
    """選択中の1試合だけ、順位表と会場別成績を編集可能にする。"""

    values = {}

    st.caption("順位表（シーズン全体）")
    standing_columns = st.columns(4)
    standing_fields = (
        ("rank", "順位", 1, 60, True),
        ("points", "勝点", 0, 999, True),
        ("season_played", "試合数", 0, 99, False),
        ("goal_difference", "得失点差", -999, 999, True),
    )

    for field_index, (
        field_name,
        label,
        minimum,
        maximum,
        optional,
    ) in enumerate(standing_fields):
        with standing_columns[field_index]:
            input_key = f"{side}_{field_name}_{match_number}"
            input_options = {
                "label": label,
                "min_value": minimum,
                "max_value": maximum,
                "step": 1,
                "key": input_key,
            }
            if optional:
                input_options["placeholder"] = "未取得"
            if input_key not in st.session_state:
                default_value = defaults[field_name]
                input_options["value"] = (
                    int(default_value)
                    if default_value is not None
                    else None
                )
            values[field_name] = st.number_input(**input_options)

    season_result_columns = st.columns(3)
    for field_index, (field_name, label) in enumerate(
        (
            ("season_wins", "全体・勝"),
            ("season_draws", "全体・分"),
            ("season_losses", "全体・敗"),
        )
    ):
        with season_result_columns[field_index]:
            input_key = f"{side}_{field_name}_{match_number}"
            input_options = {
                "label": label,
                "min_value": 0,
                "max_value": 99,
                "step": 1,
                "key": input_key,
            }
            if input_key not in st.session_state:
                input_options["value"] = int(defaults[field_name])
            values[field_name] = st.number_input(**input_options)

    season_score_columns = st.columns(2)
    for field_index, (field_name, label) in enumerate(
        (
            ("season_goals_for", "全体・得点"),
            ("season_goals_against", "全体・失点"),
        )
    ):
        with season_score_columns[field_index]:
            input_key = f"{side}_{field_name}_{match_number}"
            input_options = {
                "label": label,
                "min_value": 0,
                "max_value": 999,
                "step": 1,
                "key": input_key,
            }
            if input_key not in st.session_state:
                input_options["value"] = int(defaults[field_name])
            values[field_name] = st.number_input(**input_options)

    st.caption("会場別成績")
    venue_labels = {
        "wins": "会場別・勝",
        "draws": "会場別・分",
        "losses": "会場別・敗",
        "goals_for": "会場別・得点",
        "goals_against": "会場別・失点",
    }
    venue_result_columns = st.columns(3)

    for field_index, field_name in enumerate(("wins", "draws", "losses")):
        with venue_result_columns[field_index]:
            input_key = f"{side}_{field_name}_{match_number}"
            input_options = {
                "label": venue_labels[field_name],
                "min_value": 0,
                "max_value": 99,
                "step": 1,
                "key": input_key,
            }
            if input_key not in st.session_state:
                input_options["value"] = int(defaults[field_name])
            values[field_name] = st.number_input(**input_options)

    venue_score_columns = st.columns(2)

    for field_index, field_name in enumerate(("goals_for", "goals_against")):
        with venue_score_columns[field_index]:
            input_key = f"{side}_{field_name}_{match_number}"
            input_options = {
                "label": venue_labels[field_name],
                "min_value": 0,
                "max_value": 999,
                "step": 1,
                "key": input_key,
            }
            if input_key not in st.session_state:
                input_options["value"] = int(defaults[field_name])
            values[field_name] = st.number_input(**input_options)

    values["standings_available"] = bool(
        values["season_played"] > 0
        and values["points"] is not None
        and values["goal_difference"] is not None
    )
    st.session_state[f"{side}_standings_available_{match_number}"] = values[
        "standings_available"
    ]
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

prediction_tab, analysis_tab, draw_analysis_tab, model_optimization_tab = st.tabs(
    ["予想", "分析", "引分分析", "モデル最適化"]
)

with prediction_tab:

    st.caption(
        "toto公式の第1～13試合順で、Jリーグ公式の直近成績・"
        "会場別成績・順位表とEloから勝敗確率を計算します。"
    )

    active_version7b_settings = load_active_version7b_settings()
    active_runtime_settings = to_runtime_settings(
        active_version7b_settings.parameters.model
    )
    st.warning(
        f"このアプリは{active_version7b_settings.version_label}の検証モデルです。"
        "的中や利益を保証するものではありません。"
    )
    if active_version7b_settings.warning:
        st.warning(active_version7b_settings.warning)

    # app.pyは取得元を直接扱わず、data_loader.pyから共通形式で受け取る。
    # 公式データとCSVが利用できなくても空データが返り、手入力で利用できる。
    match_data_result = load_match_data(OFFICIAL_RESULTS_CACHE_VERSION)

    toto_history_manager = TotoHistoryManager()
    prediction_history_manager = PredictionHistoryManager()
    toto_round_result = load_current_toto_round(
        match_data_result.matches,
        TOTO_ROUND_CACHE_VERSION,
    )
    current_toto_round = toto_round_result.toto_round
    version7a_draw_settings = load_active_draw_settings()
    active_draw_settings = (
        active_version7b_settings.parameters.draw
        if active_version7b_settings.adopted
        else version7a_draw_settings
    )

    if current_toto_round is not None:
        prediction_matches = create_matches_from_toto_round(
            current_toto_round,
            match_data_result.team_stats,
        )
        toto_match_by_number = {
            match.match_number: match
            for match in current_toto_round.matches
        }
    else:
        prediction_matches = match_data_result.matches
        toto_match_by_number = {}

    if toto_round_result.source_name in ("toto公式", "保存CSV"):
        st.success(toto_round_result.message)
    elif toto_round_result.is_loaded:
        st.warning(toto_round_result.message)
    else:
        st.warning(toto_round_result.message)

    if match_data_result.is_loaded:
        st.success(match_data_result.message)
    else:
        # 技術的なエラー内容は出さず、そのまま利用できる方法だけを案内する。
        st.info(match_data_result.message)

    elo_result = None
    active_elo_result = None

    if match_data_result.completed_matches:
        try:
            elo_result = load_elo_data(
                match_data_result.completed_matches,
                DEFAULT_ELO_SETTINGS,
            )
        except Exception:
            # キャッシュ破損や想定外データでもVersion3の予測は継続する。
            elo_result = None
        if active_version7b_settings.adopted:
            try:
                active_elo_result = load_elo_data(
                    match_data_result.completed_matches,
                    active_runtime_settings.elo,
                )
            except Exception:
                active_elo_result = None
        else:
            active_elo_result = elo_result

    elo_available = bool(elo_result and elo_result.is_available)

    use_elo_adjustment = st.toggle(
        "Elo補正を使用する",
        value=True,
        help=(
            "ONはVersion4、OFFはVersion3と同じ期待得点で計算します。"
        ),
        key="use_elo_adjustment",
    )

    use_venue_adjustment = st.toggle(
        "ホーム／アウェイ成績を使用する",
        value=True,
        help="会場別試合数に応じて40%・60%・70%で全体成績へ混合します。",
        key="use_venue_adjustment",
    )

    use_recent_weighting = st.toggle(
        "直近成績の時系列重み付けを使用する",
        value=True,
        help="最新順に5・4・3・2・1で重み付けし、シーズン平均と混合します。",
        key="use_recent_weighting",
    )

    use_standings_adjustment = st.toggle(
        "順位・勝点・得失点差補正を使用する",
        value=True,
        help="1試合平均勝点と得失点差を合計最大±8%で反映します。",
        key="use_standings_adjustment",
    )

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

            Version6の予測式はVersion5と同一です。最新試合ほど強い時系列重み、
            ホーム／アウェイ別平均、
            Elo、1試合平均勝点・得失点差を順番に期待得点へ反映します。
            4つのスイッチは独立しており、Version4～Version7-A比較も表示します。

            Version6ではtoto開催回、公式試合順、履歴、バックテスト、
            Brier Score、Log Loss、Calibration、ROIを追加しました。

            Version7-AではVersion6のPoisson引分確率を基準に、期待得点差、
            Elo差、引分率、ロースコア傾向を補正し、1・0・2を合計100%で
            表示します。本命と引分候補は別に判定します。最適化設定は
            引分分析タブでYESを選んだ場合だけ反映されます。

            Jリーグ公式データを取得できない場合はCSV、CSVもない場合は
            手入力へ自動で切り替わります。
            """
        )

    edit_detail_stats = st.toggle(
        "順位表・ホーム／アウェイ成績を修正する",
        value=False,
        help=(
            "自動取得した勝点・シーズン成績・会場別成績を1試合ずつ修正できます。"
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
            prediction_matches,
            match_number,
        )

        toto_match = toto_match_by_number.get(match_number)

        if current_toto_round is not None and current_toto_round.round_id > 0:
            st.caption(
                f"第{current_toto_round.round_id}回 toto・"
                f"公式第{match_number}試合"
            )

        if match_defaults["match_date"]:
            st.caption(f'試合日時：{match_defaults["match_date"]}')

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
        active_home_elo = (
            get_team_elo(
                home_team,
                active_elo_result,
                team_name_normalizer=normalize_team_name,
            )
            if active_elo_result and active_elo_result.is_available
            else None
        )
        active_away_elo = (
            get_team_elo(
                away_team,
                active_elo_result,
                team_name_normalizer=normalize_team_name,
            )
            if active_elo_result and active_elo_result.is_available
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
            with st.expander("順位表・会場別成績を修正", expanded=True):
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
            st.caption(format_standings_summary(home_detail_values))
            st.caption(f"ホームElo：{format_elo_value(home_elo)}")

        with detail_summary_col2:
            st.caption(
                format_detail_summary(
                    away_detail_values["rank"],
                    away_record,
                    "アウェイ成績",
                )
            )
            st.caption(format_standings_summary(away_detail_values))
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
        home_stats = get_team_stats(home_team, match_data_result.team_stats)
        away_stats = get_team_stats(away_team, match_data_result.team_stats)
        home_recent_results = home_stats.recent_results if home_stats else ()
        away_recent_results = away_stats.recent_results if away_stats else ()

        if home_recent_matches or away_recent_matches:
            with st.expander("自動取得した直近5試合を見る"):
                if home_recent_matches:
                    st.write(f"**{home_team}**")
                    displayed_home_matches = (
                        tuple(item.label for item in home_recent_results)
                        or home_recent_matches
                    )
                    for recent_match in displayed_home_matches:
                        st.caption(recent_match)
                if away_recent_matches:
                    st.write(f"**{away_team}**")
                    displayed_away_matches = (
                        tuple(item.label for item in away_recent_results)
                        or away_recent_matches
                    )
                    for recent_match in displayed_away_matches:
                        st.caption(recent_match)

        match_inputs.append(
            {
                "match_number": match_number,
                "toto_round": (
                    current_toto_round.round_id
                    if current_toto_round is not None
                    else None
                ),
                "toto_match_number": match_number,
                "match_datetime": (
                    toto_match.match_time.isoformat()
                    if toto_match is not None
                    else match_defaults["match_date"]
                ),
                "actual_result": (
                    toto_match.actual_result or ""
                    if toto_match is not None
                    else ""
                ),
                "home_team": home_team.strip(),
                "away_team": away_team.strip(),
                "home_scored": home_scored,
                "home_conceded": home_conceded,
                "away_scored": away_scored,
                "away_conceded": away_conceded,
                "home_rank": home_detail_values["rank"],
                "away_rank": away_detail_values["rank"],
                "home_points": home_detail_values["points"],
                "away_points": away_detail_values["points"],
                "home_played": home_detail_values["season_played"],
                "away_played": away_detail_values["season_played"],
                "home_season_draws": home_detail_values["season_draws"],
                "away_season_draws": away_detail_values["season_draws"],
                "home_goal_difference": home_detail_values["goal_difference"],
                "away_goal_difference": away_detail_values["goal_difference"],
                "home_standings_available": home_detail_values[
                    "standings_available"
                ],
                "away_standings_available": away_detail_values[
                    "standings_available"
                ],
                "home_season_scored": season_average(
                    home_detail_values,
                    "season_goals_for",
                ),
                "home_season_conceded": season_average(
                    home_detail_values,
                    "season_goals_against",
                ),
                "away_season_scored": season_average(
                    away_detail_values,
                    "season_goals_for",
                ),
                "away_season_conceded": season_average(
                    away_detail_values,
                    "season_goals_against",
                ),
                "home_recent_results": home_recent_results,
                "away_recent_results": away_recent_results,
                "home_record": home_record,
                "away_record": away_record,
                "home_elo": home_elo,
                "away_elo": away_elo,
                "elo_difference": elo_difference,
                "active_home_elo": active_home_elo,
                "active_away_elo": active_away_elo,
            }
        )

        st.divider()

    threshold_choices = (20, 25, 30, 35, 40)
    active_threshold_percent = round(
        active_draw_settings.candidate_threshold * 100
    )
    threshold_default = (
        active_threshold_percent
        if active_threshold_percent in threshold_choices
        else "任意指定"
    )
    draw_candidate_threshold_choice = st.selectbox(
        "引分候補閾値",
        options=(*threshold_choices, "任意指定"),
        index=(*threshold_choices, "任意指定").index(threshold_default),
        format_func=lambda value: (
            f"{value}%" if isinstance(value, int) else str(value)
        ),
        key="draw_candidate_threshold_choice",
    )
    if draw_candidate_threshold_choice == "任意指定":
        draw_candidate_threshold_percent = st.number_input(
            "引分候補閾値（%）",
            min_value=0.0,
            max_value=100.0,
            value=float(active_threshold_percent),
            step=1.0,
            key="draw_candidate_threshold_custom",
        )
    else:
        draw_candidate_threshold_percent = float(draw_candidate_threshold_choice)
    prediction_draw_settings = replace(
        active_draw_settings,
        candidate_threshold=draw_candidate_threshold_percent / 100.0,
    )
    version7a_prediction_draw_settings = replace(
        version7a_draw_settings,
        candidate_threshold=draw_candidate_threshold_percent / 100.0,
    )

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
        prediction_date = datetime.now(JAPAN_TIMEZONE).isoformat()
        model_options = ModelOptions(
            use_elo=bool(use_elo_adjustment and elo_available),
            use_venue=use_venue_adjustment,
            use_recent_weighting=use_recent_weighting,
            use_standings=use_standings_adjustment,
        )
        draw_context_cutoff = datetime.now(JAPAN_TIMEZONE)
        draw_contexts = {}

        for match in match_inputs:
            try:
                home_input = TeamModelInput(
                    team_name=match["home_team"],
                    recent_scored_average=match["home_scored"],
                    recent_conceded_average=match["home_conceded"],
                    recent_matches=match["home_recent_results"],
                    season_scored_average=match["home_season_scored"],
                    season_conceded_average=match["home_season_conceded"],
                    venue_record=match["home_record"],
                    rank=match["home_rank"],
                    points=(
                        match["home_points"]
                        if match["home_standings_available"]
                        else None
                    ),
                    played=(
                        match["home_played"]
                        if match["home_standings_available"]
                        else None
                    ),
                    season_draws=match["home_season_draws"],
                    goal_difference=(
                        match["home_goal_difference"]
                        if match["home_standings_available"]
                        else None
                    ),
                    elo=match["home_elo"],
                )
                away_input = TeamModelInput(
                    team_name=match["away_team"],
                    recent_scored_average=match["away_scored"],
                    recent_conceded_average=match["away_conceded"],
                    recent_matches=match["away_recent_results"],
                    season_scored_average=match["away_season_scored"],
                    season_conceded_average=match["away_season_conceded"],
                    venue_record=match["away_record"],
                    rank=match["away_rank"],
                    points=(
                        match["away_points"]
                        if match["away_standings_available"]
                        else None
                    ),
                    played=(
                        match["away_played"]
                        if match["away_standings_available"]
                        else None
                    ),
                    season_draws=match["away_season_draws"],
                    goal_difference=(
                        match["away_goal_difference"]
                        if match["away_standings_available"]
                        else None
                    ),
                    elo=match["away_elo"],
                )
                # Version4～Version7-Aは従来設定で固定し、採用済みVersion7-Bは
                # 別パイプラインで計算する。旧Version比較を候補係数で書き換えない。
                pipeline = predict_match(
                    home_input,
                    away_input,
                    options=model_options,
                )
                active_home_input = replace(
                    home_input,
                    elo=match["active_home_elo"],
                )
                active_away_input = replace(
                    away_input,
                    elo=match["active_away_elo"],
                )
                active_pipeline = (
                    predict_match(
                        active_home_input,
                        active_away_input,
                        options=model_options,
                        form_settings=active_runtime_settings.form,
                        venue_settings=active_runtime_settings.venue,
                        standings_settings=active_runtime_settings.standings,
                        model_settings=active_runtime_settings.model,
                        elo_settings=active_runtime_settings.elo,
                    )
                    if active_version7b_settings.adopted
                    else pipeline
                )
                version6_probabilities = pipeline.version5_probabilities
                home_category = TEAM_CATEGORY_BY_NAME.get(match["home_team"], "")
                away_category = TEAM_CATEGORY_BY_NAME.get(match["away_team"], "")
                context_category = (
                    home_category if home_category == away_category else ""
                )
                if context_category not in draw_contexts:
                    draw_contexts[context_category] = build_draw_context(
                        match_data_result.completed_matches,
                        draw_context_cutoff,
                        category=context_category,
                    )
                version7a_draw_prediction = predict_draw_aware(
                    version6_probabilities,
                    pipeline.expected_final.home,
                    pipeline.expected_final.away,
                    home_input,
                    away_input,
                    context=draw_contexts[context_category],
                    settings=version7a_prediction_draw_settings,
                )
                active_probabilities = active_pipeline.version5_probabilities
                draw_prediction = (
                    predict_draw_aware(
                        active_probabilities,
                        active_pipeline.expected_final.home,
                        active_pipeline.expected_final.away,
                        active_home_input,
                        active_away_input,
                        context=draw_contexts[context_category],
                        settings=prediction_draw_settings,
                    )
                    if active_version7b_settings.adopted
                    else version7a_draw_prediction
                )
                probabilities = {
                    "home_win": draw_prediction.probabilities["1"],
                    "draw": draw_prediction.probabilities["0"],
                    "away_win": draw_prediction.probabilities["2"],
                    "home_goals": active_probabilities["home_goals"],
                    "away_goals": active_probabilities["away_goals"],
                }

                confidence = get_confidence_label(
                    [
                        probabilities["home_win"],
                        probabilities["draw"],
                        probabilities["away_win"],
                    ]
                )

                reason = create_reason(
                    home_expected=active_pipeline.expected_final.home,
                    away_expected=active_pipeline.expected_final.away,
                    home_win=probabilities["home_win"],
                    draw=probabilities["draw"],
                    away_win=probabilities["away_win"],
                )
                version7a_percentages = probability_percentages(
                    version7a_draw_prediction.probabilities
                )
                active_percentages = probability_percentages(
                    draw_prediction.probabilities
                )
                version6_percentages = probability_percentages(
                    version6_probabilities
                )
                version4_percentages = probability_percentages(
                    pipeline.version4.probabilities
                )

                results.append(
                    {
                        "試合": match["match_number"],
                        "toto_round": match["toto_round"],
                        "toto_match_number": match["toto_match_number"],
                        "prediction_version": active_version7b_settings.version_label,
                        "actual_result": match["actual_result"],
                        "hit": (
                            draw_prediction.prediction == match["actual_result"]
                            if match["actual_result"] in ("1", "0", "2")
                            else None
                        ),
                        "total_hits": None,
                        "accuracy": None,
                        "prediction_date": prediction_date,
                        "対戦カード": (
                            f'{match["home_team"]}'
                            f' vs '
                            f'{match["away_team"]}'
                        ),
                        "1": active_percentages["1"],
                        "0": active_percentages["0"],
                        "2": active_percentages["2"],
                        "本命": draw_prediction.prediction,
                        "最高確率": round(
                            draw_prediction.top_probability * 100,
                            1,
                        ),
                        "引分候補": (
                            "候補" if draw_prediction.is_draw_candidate else "—"
                        ),
                        "draw_candidate": draw_prediction.is_draw_candidate,
                        "draw_candidate_reasons": "／".join(
                            draw_prediction.candidate_reasons
                        ),
                        "判定": confidence,
                        "予想スコア": (
                            f'{probabilities["home_goals"]}'
                            f'−'
                            f'{probabilities["away_goals"]}'
                        ),
                        "予想理由": reason,
                        "home_rank": match["home_rank"],
                        "away_rank": match["away_rank"],
                        "home_points": match["home_points"],
                        "away_points": match["away_points"],
                        "home_goal_difference": match["home_goal_difference"],
                        "away_goal_difference": match["away_goal_difference"],
                        "home_points_per_match": round_optional(
                            pipeline.standings.home_points_per_match
                        ),
                        "away_points_per_match": round_optional(
                            pipeline.standings.away_points_per_match
                        ),
                        "home_recent_scored_average": round(
                            match["home_scored"],
                            4,
                        ),
                        "home_recent_conceded_average": round(
                            match["home_conceded"],
                            4,
                        ),
                        "away_recent_scored_average": round(
                            match["away_scored"],
                            4,
                        ),
                        "away_recent_conceded_average": round(
                            match["away_conceded"],
                            4,
                        ),
                        "home_recent_weighted_scored": round_optional(
                            pipeline.home_form.weighted_scored
                        ),
                        "home_recent_weighted_conceded": round_optional(
                            pipeline.home_form.weighted_conceded
                        ),
                        "away_recent_weighted_scored": round_optional(
                            pipeline.away_form.weighted_scored
                        ),
                        "away_recent_weighted_conceded": round_optional(
                            pipeline.away_form.weighted_conceded
                        ),
                        "home_home_scored_average": round_optional(
                            pipeline.venue.home.venue_scored
                        ),
                        "home_home_conceded_average": round_optional(
                            pipeline.venue.home.venue_conceded
                        ),
                        "away_away_scored_average": round_optional(
                            pipeline.venue.away.venue_scored
                        ),
                        "away_away_conceded_average": round_optional(
                            pipeline.venue.away.venue_conceded
                        ),
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
                        "version7b_home_elo": (
                            round(match["active_home_elo"], 2)
                            if match["active_home_elo"] is not None
                            else None
                        ),
                        "version7b_away_elo": (
                            round(match["active_away_elo"], 2)
                            if match["active_away_elo"] is not None
                            else None
                        ),
                        "version7b_elo_difference": (
                            round(
                                match["active_home_elo"]
                                - match["active_away_elo"],
                                2,
                            )
                            if match["active_home_elo"] is not None
                            and match["active_away_elo"] is not None
                            else None
                        ),
                        "elo_difference": (
                            round(match["elo_difference"], 2)
                            if match["elo_difference"] is not None
                            else None
                        ),
                        "home_expected_before_elo": round(
                            pipeline.expected_after_venue.home,
                            4,
                        ),
                        "away_expected_before_elo": round(
                            pipeline.expected_after_venue.away,
                            4,
                        ),
                        "home_expected_after_elo": round(
                            pipeline.expected_after_elo.home,
                            4,
                        ),
                        "away_expected_after_elo": round(
                            pipeline.expected_after_elo.away,
                            4,
                        ),
                        "elo_adjustment_enabled": pipeline.elo_adjustment_enabled,
                        "home_expected_before_version5": round(
                            pipeline.version4.expected_after_elo.home,
                            4,
                        ),
                        "away_expected_before_version5": round(
                            pipeline.version4.expected_after_elo.away,
                            4,
                        ),
                        "home_expected_after_version5": round(
                            pipeline.expected_final.home,
                            4,
                        ),
                        "away_expected_after_version5": round(
                            pipeline.expected_final.away,
                            4,
                        ),
                        "home_expected_after_version6": round(
                            pipeline.expected_final.home,
                            4,
                        ),
                        "away_expected_after_version6": round(
                            pipeline.expected_final.away,
                            4,
                        ),
                        "home_expected_after_version7b": round(
                            active_pipeline.expected_final.home,
                            4,
                        ),
                        "away_expected_after_version7b": round(
                            active_pipeline.expected_final.away,
                            4,
                        ),
                        "venue_adjustment_enabled": (
                            pipeline.venue_adjustment_enabled
                        ),
                        "recent_weighting_enabled": (
                            pipeline.recent_weighting_enabled
                        ),
                        "standings_adjustment_enabled": (
                            pipeline.standings_adjustment_enabled
                        ),
                        "version4_prediction": pipeline.version4.prediction,
                        "version5_prediction": pipeline.version5_prediction,
                        "version6_prediction": pipeline.version5_prediction,
                        "version7a_prediction": (
                            version7a_draw_prediction.prediction
                        ),
                        "version7b_prediction": draw_prediction.prediction,
                        "version6_home_win": version6_percentages["1"],
                        "version6_draw": version6_percentages["0"],
                        "version6_away_win": version6_percentages["2"],
                        "version6_top_probability": round(
                            pipeline.version5_top_probability * 100,
                            1,
                        ),
                        "version7a_home_win": version7a_percentages["1"],
                        "version7a_draw": version7a_percentages["0"],
                        "version7a_away_win": version7a_percentages["2"],
                        "version7a_top_probability": round(
                            version7a_draw_prediction.top_probability * 100,
                            1,
                        ),
                        "version7b_home_win": active_percentages["1"],
                        "version7b_draw": active_percentages["0"],
                        "version7b_away_win": active_percentages["2"],
                        "version7b_top_probability": round(
                            draw_prediction.top_probability * 100,
                            1,
                        ),
                        "poisson_draw_probability": round(
                            version7a_draw_prediction.poisson_draw_probability * 100,
                            1,
                        ),
                        "adjusted_draw_probability": round(
                            version7a_draw_prediction.adjusted_draw_probability * 100,
                            1,
                        ),
                        "version7b_poisson_draw_probability": round(
                            draw_prediction.poisson_draw_probability * 100,
                            1,
                        ),
                        "version7b_adjusted_draw_probability": round(
                            draw_prediction.adjusted_draw_probability * 100,
                            1,
                        ),
                        "prediction_changed": pipeline.prediction_changed,
                        "version7a_prediction_changed": (
                            version7a_draw_prediction.prediction
                            != pipeline.version5_prediction
                        ),
                        "version7b_prediction_changed": (
                            draw_prediction.prediction
                            != version7a_draw_prediction.prediction
                        ),
                        "version4_home_win": version4_percentages["1"],
                        "version4_draw": version4_percentages["0"],
                        "version4_away_win": version4_percentages["2"],
                        "version4_top_probability": round(
                            pipeline.version4.top_probability * 100,
                            1,
                        ),
                        "home_expected_basic": round(
                            pipeline.expected_basic.home,
                            4,
                        ),
                        "away_expected_basic": round(
                            pipeline.expected_basic.away,
                            4,
                        ),
                        "home_expected_after_venue": round(
                            pipeline.expected_after_venue.home,
                            4,
                        ),
                        "away_expected_after_venue": round(
                            pipeline.expected_after_venue.away,
                            4,
                        ),
                        "home_expected_after_standings": round(
                            pipeline.expected_after_standings.home,
                            4,
                        ),
                        "away_expected_after_standings": round(
                            pipeline.expected_after_standings.away,
                            4,
                        ),
                        "elo_adjustment_rate": round(
                            pipeline.elo_adjustment_rate,
                            6,
                        ),
                        "home_venue_adjustment_rate": round(
                            pipeline.home_venue_adjustment_rate,
                            6,
                        ),
                        "away_venue_adjustment_rate": round(
                            pipeline.away_venue_adjustment_rate,
                            6,
                        ),
                        "points_adjustment_rate": round(
                            pipeline.standings.points_adjustment_rate,
                            6,
                        ),
                        "rank_adjustment_rate": round(
                            pipeline.standings.rank_adjustment_rate,
                            6,
                        ),
                        "goal_difference_adjustment_rate": round(
                            pipeline.standings.goal_difference_adjustment_rate,
                            6,
                        ),
                        "version7b_rank_adjustment_rate": round(
                            active_pipeline.standings.rank_adjustment_rate,
                            6,
                        ),
                        "fallback_used": pipeline.fallback_used,
                        "fallback_reason": pipeline.fallback_reason,
                    }
                )

            except Exception:
                st.warning(
                    f'第{match["match_number"]}試合は入力データを確認できず、'
                    "予想を作成できませんでした。"
                )

        if results:

            result_df = pd.DataFrame(results).sort_values(
                "toto_match_number"
            ).reset_index(drop=True)
            result_df = finalize_prediction_results(result_df)

            st.session_state["latest_prediction_results"] = result_df.copy()

            if (
                current_toto_round is not None
                and current_toto_round.round_id > 0
                and prediction_history_manager.save_prediction_results(
                    result_df,
                    current_toto_round,
                    datetime.fromisoformat(prediction_date),
                )
            ):
                st.caption(
                    f"第{current_toto_round.round_id}回の"
                    f"Version4～{active_version7b_settings.version_label}予想を"
                    "履歴CSVへ保存しました。"
                )

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
                        "引分候補",
                        "判定",
                        "予想スコア",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

            st.header(
                f"Version4～{active_version7b_settings.version_label}比較"
            )

            comparison_df = pd.DataFrame(
                [
                    {
                        "試合番号": result["試合"],
                        "対戦カード": result["対戦カード"],
                        "Version4本命": result["version4_prediction"],
                        "Version5本命": result["version5_prediction"],
                        "Version6本命": result["version6_prediction"],
                        "Version7-A本命": result["version7a_prediction"],
                        "Version4勝率": result["version4_top_probability"],
                        "Version5勝率": result["version6_top_probability"],
                        "Version6勝率": result["version6_top_probability"],
                        "Version7-A勝率": result["version7a_top_probability"],
                        "Version4期待得点": (
                            f'{result["home_expected_before_version5"]:.2f}-'
                            f'{result["away_expected_before_version5"]:.2f}'
                        ),
                        "Version5期待得点": (
                            f'{result["home_expected_after_version5"]:.2f}-'
                            f'{result["away_expected_after_version5"]:.2f}'
                        ),
                        "Version6期待得点": (
                            f'{result["home_expected_after_version6"]:.2f}-'
                            f'{result["away_expected_after_version6"]:.2f}'
                        ),
                        "Version7-A期待得点": (
                            f'{result["home_expected_after_version6"]:.2f}-'
                            f'{result["away_expected_after_version6"]:.2f}'
                        ),
                        "V4→V5変更": (
                            "● 変更あり"
                            if result["prediction_changed"]
                            else "変更なし"
                        ),
                        "V5→V6変更": (
                            "● 変更あり"
                            if result["version5_prediction"]
                            != result["version6_prediction"]
                            else "変更なし"
                        ),
                        "V6→V7-A変更": (
                            "● 変更あり"
                            if result["version7a_prediction_changed"]
                            else "変更なし"
                        ),
                    }
                    for result in results
                ]
            )
            if active_version7b_settings.adopted:
                comparison_df["Version7-B本命"] = result_df[
                    "version7b_prediction"
                ]
                comparison_df["Version7-B勝率"] = result_df[
                    "version7b_top_probability"
                ]
                comparison_df["Version7-B期待得点"] = [
                    f'{result["home_expected_after_version7b"]:.2f}-'
                    f'{result["away_expected_after_version7b"]:.2f}'
                    for result in results
                ]
                comparison_df["V7-A→V7-B変更"] = [
                    "● 変更あり"
                    if result["version7b_prediction_changed"]
                    else "変更なし"
                    for result in results
                ]
            changed_match_count = int(result_df["prediction_changed"].sum())
            version7a_changed_match_count = int(
                result_df["version7a_prediction_changed"].sum()
            )
            st.session_state["version5_changed_match_count"] = changed_match_count
            st.session_state["version7a_changed_match_count"] = (
                version7a_changed_match_count
            )
            version7b_changed_match_count = int(
                result_df["version7b_prediction_changed"].sum()
            )
            st.session_state["version7b_changed_match_count"] = (
                version7b_changed_match_count
            )

            if changed_match_count:
                st.warning(
                    f"Version5で本命が変化した試合：{changed_match_count}試合"
                )
            else:
                st.info("Version5で本命が変化した試合はありません。")
            if version7a_changed_match_count:
                st.warning(
                    "Version7-Aで本命が変化した試合："
                    f"{version7a_changed_match_count}試合"
                )
            else:
                st.info("現在設定ではVersion6から本命が変化した試合はありません。")
            if active_version7b_settings.adopted:
                if version7b_changed_match_count:
                    st.warning(
                        "Version7-BでVersion7-Aから本命が変化した試合："
                        f"{version7b_changed_match_count}試合"
                    )
                else:
                    st.info(
                        "Version7-BでVersion7-Aから本命が変化した試合は"
                        "ありません。"
                    )

            st.dataframe(
                comparison_df,
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

                    prediction_detail = (
                        f'**Version7-B本命：{result["version7b_prediction"]}** ／ '
                        if active_version7b_settings.adopted
                        else ""
                    )
                    st.write(
                        prediction_detail
                        + f'Version7-A本命：{result["version7a_prediction"]} ／ '
                        f'Version6本命：{result["version6_prediction"]} ／ '
                        f'Version5本命：{result["version5_prediction"]} ／ '
                        f'Version4本命：{result["version4_prediction"]}'
                    )

                    st.write(
                        f'予想スコア：'
                        f'{result["予想スコア"]}'
                    )

                    st.write(
                        "引分候補："
                        f'{result["引分候補"]}'
                        + (
                            f'（{result["draw_candidate_reasons"]}）'
                            if result["draw_candidate_reasons"]
                            else ""
                        )
                    )

                    if active_version7b_settings.adopted:
                        st.caption(
                            "引分確率：Poisson "
                            f'{result["version7b_poisson_draw_probability"]:.1f}% '
                            "→ Version7-B "
                            f'{result["version7b_adjusted_draw_probability"]:.1f}%'
                        )
                    else:
                        st.caption(
                            "引分確率：Poisson "
                            f'{result["poisson_draw_probability"]:.1f}% → '
                            "Version7-A "
                            f'{result["adjusted_draw_probability"]:.1f}%'
                        )

                    st.write(
                        f'判定：{result["判定"]}'
                    )

                    st.write(
                        "**基本データ**  "
                        f'ホーム順位 {format_optional(result["home_rank"], 0)} ／ '
                        f'アウェイ順位 {format_optional(result["away_rank"], 0)} ／ '
                        f'ホーム勝点 {format_optional(result["home_points"], 0)} ／ '
                        f'アウェイ勝点 {format_optional(result["away_points"], 0)} ／ '
                        "得失点差 "
                        f'{format_optional(result["home_goal_difference"], 0, True)}'
                        " ／ "
                        f'{format_optional(result["away_goal_difference"], 0, True)}'
                    )

                    st.write(
                        "**平均値（ホーム／アウェイ）**  "
                        "通常直近5試合 得点 "
                        f'{result["home_recent_scored_average"]:.2f}／'
                        f'{result["away_recent_scored_average"]:.2f}、失点 '
                        f'{result["home_recent_conceded_average"]:.2f}／'
                        f'{result["away_recent_conceded_average"]:.2f}  '
                        "加重平均 得点 "
                        f'{format_optional(result["home_recent_weighted_scored"])}／'
                        f'{format_optional(result["away_recent_weighted_scored"])}、'
                        "失点 "
                        f'{format_optional(result["home_recent_weighted_conceded"])}／'
                        f'{format_optional(result["away_recent_weighted_conceded"])}'
                    )

                    st.write(
                        "**会場別平均**  "
                        "ホームチーム（ホーム）得点／失点 "
                        f'{format_optional(result["home_home_scored_average"])}／'
                        f'{format_optional(result["home_home_conceded_average"])}  '
                        "アウェイチーム（アウェイ）得点／失点 "
                        f'{format_optional(result["away_away_scored_average"])}／'
                        f'{format_optional(result["away_away_conceded_average"])}'
                    )

                    st.write(
                        "**期待得点（ホーム－アウェイ）**  "
                        f'基本 {result["home_expected_basic"]:.2f}－'
                        f'{result["away_expected_basic"]:.2f} ／ '
                        f'会場別後 {result["home_expected_after_venue"]:.2f}－'
                        f'{result["away_expected_after_venue"]:.2f} ／ '
                        f'Elo後 {result["home_expected_after_elo"]:.2f}－'
                        f'{result["away_expected_after_elo"]:.2f} ／ '
                        f'順位等後 {result["home_expected_after_standings"]:.2f}－'
                        f'{result["away_expected_after_standings"]:.2f} ／ '
                        f'Version6最終 '
                        f'{result["home_expected_after_version6"]:.2f}－'
                        f'{result["away_expected_after_version6"]:.2f}'
                    )
                    if active_version7b_settings.adopted:
                        st.write(
                            "**Version7-B最終期待得点（ホーム－アウェイ）**  "
                            f'{result["home_expected_after_version7b"]:.2f}－'
                            f'{result["away_expected_after_version7b"]:.2f}'
                        )

                    st.write(
                        "**補正率（ホーム側／アウェイ側）**  "
                        f'Elo {result["elo_adjustment_rate"]:+.1%}／'
                        f'{-result["elo_adjustment_rate"]:+.1%}、'
                        "会場別 "
                        f'{result["home_venue_adjustment_rate"]:+.1%}／'
                        f'{result["away_venue_adjustment_rate"]:+.1%}、'
                        f'勝点 {result["points_adjustment_rate"]:+.1%}／'
                        f'{-result["points_adjustment_rate"]:+.1%}、'
                        "得失点差 "
                        f'{result["goal_difference_adjustment_rate"]:+.1%}／'
                        f'{-result["goal_difference_adjustment_rate"]:+.1%}'
                    )

                    st.caption(
                        "適用状態："
                        f'Elo {"ON" if result["elo_adjustment_enabled"] else "OFF"} ／ '
                        "会場別 "
                        f'{"ON" if result["venue_adjustment_enabled"] else "OFF"} ／ '
                        "直近重み "
                        f'{"ON" if result["recent_weighting_enabled"] else "OFF"} ／ '
                        "順位等 "
                        f'{"ON" if result["standings_adjustment_enabled"] else "OFF"}'
                    )

                    if result["fallback_used"]:
                        st.caption(result["fallback_reason"])

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


with analysis_tab:
    render_analysis_tab(
        history_manager=toto_history_manager,
        prediction_history_manager=prediction_history_manager,
        fallback_matches=match_data_result.completed_matches,
    )


with draw_analysis_tab:
    render_draw_analysis_tab(
        history_manager=toto_history_manager,
        fallback_matches=match_data_result.completed_matches,
    )


with model_optimization_tab:
    render_model_optimization_tab(
        history_manager=toto_history_manager,
        fallback_matches=match_data_result.completed_matches,
    )
