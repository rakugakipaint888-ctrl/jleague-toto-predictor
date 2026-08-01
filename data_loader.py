"""Jリーグ試合データの取得と共通形式への変換を担当する。

``app.py`` は取得元を意識せず、このモジュールの ``load_matches`` だけを
呼び出す。API固有のURL・認証・JSON変換は ``ApiDataSource`` に閉じ込め、
APIを変更するときも予想画面やポアソン計算を変更しない構造にしている。
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import requests


# --------------------------------------------------
# 取得元と共通データ形式
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MATCHES_PATH = PROJECT_ROOT / "data" / "matches.csv"

API_BASE_URL = "https://v3.football.api-sports.io"
API_KEY_ENV_NAME = "API_FOOTBALL_KEY"
API_LEAGUE_NAMES = ("J1 League", "J2 League", "J3 League")
JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")

FINISHED_STATUSES = {"FT", "AET", "PEN"}
UPCOMING_STATUSES = {"NS", "TBD"}

MATCH_COLUMNS = [
    "match_number",
    "match_date",
    "home_team",
    "away_team",
    "home_scored",
    "home_conceded",
    "away_scored",
    "away_conceded",
    "home_recent_matches",
    "away_recent_matches",
]

# CSVやAPIに値がない場合は、Version 1と同じ初期値を使う。
DEFAULT_MATCH_VALUES = {
    "home_team": "",
    "away_team": "",
    "home_scored": 1.4,
    "home_conceded": 1.2,
    "away_scored": 1.2,
    "away_conceded": 1.4,
}

DEFAULT_MATCH_METADATA = {
    "match_date": "",
    "home_recent_matches": "",
    "away_recent_matches": "",
}


# --------------------------------------------------
# API-Football名からteams.pyの日本語名への変換
# --------------------------------------------------

TEAM_NAME_ALIASES = {
    # J1
    "鹿島アントラーズ": ("Kashima Antlers", "Kashima"),
    "水戸ホーリーホック": ("Mito Hollyhock",),
    "浦和レッズ": ("Urawa", "Urawa Red Diamonds", "Urawa Reds"),
    "ジェフユナイテッド千葉": (
        "JEF United Chiba",
        "JEF United",
        "JEF Chiba",
    ),
    "柏レイソル": ("Kashiwa Reysol",),
    "ＦＣ東京": ("FC Tokyo",),
    "東京ヴェルディ": ("Tokyo Verdy",),
    "ＦＣ町田ゼルビア": ("Machida Zelvia", "FC Machida Zelvia"),
    "川崎フロンターレ": ("Kawasaki Frontale",),
    "横浜Ｆ・マリノス": (
        "Yokohama F. Marinos",
        "Yokohama F Marinos",
        "Yokohama Marinos",
    ),
    "清水エスパルス": ("Shimizu S-pulse", "Shimizu S-Pulse"),
    "名古屋グランパス": ("Nagoya Grampus", "Nagoya Grampus Eight"),
    "京都サンガF.C.": ("Kyoto Sanga", "Kyoto Sanga FC"),
    "ガンバ大阪": ("Gamba Osaka",),
    "セレッソ大阪": ("Cerezo Osaka",),
    "ヴィッセル神戸": ("Vissel Kobe",),
    "ファジアーノ岡山": ("Fagiano Okayama",),
    "サンフレッチェ広島": ("Sanfrecce Hiroshima",),
    "アビスパ福岡": ("Avispa Fukuoka",),
    "Ｖ・ファーレン長崎": ("V-Varen Nagasaki", "V Varen Nagasaki"),
    # J2
    "北海道コンサドーレ札幌": (
        "Consadole Sapporo",
        "Hokkaido Consadole Sapporo",
    ),
    "ヴァンラーレ八戸": ("Vanraure Hachinohe",),
    "ベガルタ仙台": ("Vegalta Sendai",),
    "ブラウブリッツ秋田": ("Blaublitz Akita",),
    "モンテディオ山形": ("Montedio Yamagata",),
    "いわきＦＣ": ("Iwaki", "Iwaki FC"),
    "栃木シティ": ("Tochigi City", "Tochigi City FC"),
    "ＲＢ大宮アルディージャ": (
        "RB Omiya Ardija",
        "Omiya Ardija",
    ),
    "横浜ＦＣ": ("Yokohama FC",),
    "湘南ベルマーレ": ("Shonan Bellmare",),
    "ヴァンフォーレ甲府": ("Ventforet Kofu",),
    "アルビレックス新潟": ("Albirex Niigata",),
    "カターレ富山": ("Kataller Toyama",),
    "ジュビロ磐田": ("Jubilo Iwata", "Júbilo Iwata"),
    "藤枝ＭＹＦＣ": ("Fujieda MYFC",),
    "徳島ヴォルティス": ("Tokushima Vortis",),
    "ＦＣ今治": ("FC Imabari",),
    "サガン鳥栖": ("Sagan Tosu",),
    "大分トリニータ": ("Oita Trinita",),
    "テゲバジャーロ宮崎": ("Tegevajaro Miyazaki",),
    # J3
    "福島ユナイテッドＦＣ": ("Fukushima United", "Fukushima United FC"),
    "栃木ＳＣ": ("Tochigi SC",),
    "ザスパ群馬": (
        "Thespa Gunma",
        "Thespakusatsu Gunma",
        "ThespaKusatsu Gunma",
    ),
    "ＳＣ相模原": ("SC Sagamihara",),
    "松本山雅ＦＣ": ("Matsumoto Yamaga", "Matsumoto Yamaga FC"),
    "ＡＣ長野パルセイロ": ("AC Nagano Parceiro", "Nagano Parceiro"),
    "ツエーゲン金沢": ("Zweigen Kanazawa",),
    "ＦＣ岐阜": ("FC Gifu",),
    "レイラック滋賀ＦＣ": (
        "Reilac Shiga",
        "Reilac Shiga FC",
        "MIO Biwako Shiga",
    ),
    "ＦＣ大阪": ("FC Osaka",),
    "奈良クラブ": ("Nara Club",),
    "ガイナーレ鳥取": ("Gainare Tottori",),
    "レノファ山口ＦＣ": ("Renofa Yamaguchi", "Renofa Yamaguchi FC"),
    "カマタマーレ讃岐": ("Kamatamare Sanuki",),
    "愛媛ＦＣ": ("Ehime FC",),
    "高知ユナイテッドＳＣ": ("Kochi United", "Kochi United SC"),
    "ギラヴァンツ北九州": ("Giravanz Kitakyushu",),
    "ロアッソ熊本": ("Roasso Kumamoto",),
    "鹿児島ユナイテッドＦＣ": (
        "Kagoshima United",
        "Kagoshima United FC",
    ),
    "ＦＣ琉球": ("FC Ryukyu",),
}


def normalize_team_name(team_name: str) -> str:
    """表記ゆれ比較用に、英数字と文字だけの小文字へそろえる。"""

    normalized = unicodedata.normalize("NFKD", str(team_name)).casefold()
    return "".join(character for character in normalized if character.isalnum())


API_TEAM_NAME_MAP = {
    normalize_team_name(alias): japanese_name
    for japanese_name, aliases in TEAM_NAME_ALIASES.items()
    for alias in (japanese_name, *aliases)
}


def translate_api_team_name(team_name: str) -> str:
    """API-Footballのクラブ名をteams.pyと同じ日本語名へ変換する。"""

    cleaned_name = str(team_name).strip()
    return API_TEAM_NAME_MAP.get(
        normalize_team_name(cleaned_name),
        cleaned_name,
    )


# --------------------------------------------------
# データ取得元の共通インターフェース
# --------------------------------------------------

class MatchDataSource(Protocol):
    """CSV・APIなど、すべての取得元が備える共通インターフェース。"""

    @property
    def name(self) -> str:
        """画面表示用の取得元名を返す。"""

    def load(self) -> pd.DataFrame:
        """試合データをDataFrameとして返す。"""


class MatchDataSourceError(RuntimeError):
    """データ取得元の読み込みに失敗した場合の共通エラー。"""


class MatchDataNotFoundError(MatchDataSourceError):
    """APIキーやCSVなどが存在しない場合のエラー。"""


class MatchDataFormatError(MatchDataSourceError):
    """取得データの形式が想定と異なる場合のエラー。"""


@dataclass(frozen=True)
class TeamRecentStats:
    """1クラブの直近試合と、そこから算出した平均値。"""

    average_scored: float
    average_conceded: float
    recent_matches: tuple[str, ...] = ()


@dataclass(frozen=True)
class CsvMatchDataSource:
    """``data/matches.csv`` から試合データを読み込む。"""

    path: Path = DEFAULT_MATCHES_PATH

    @property
    def name(self) -> str:
        return f"CSV（{self.path.name}）"

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            raise MatchDataNotFoundError(
                f"{self.path.as_posix()} が見つかりません。"
            )

        try:
            # Excelで保存したUTF-8 CSVのBOMにも対応する。
            return pd.read_csv(self.path, encoding="utf-8-sig")
        except pd.errors.EmptyDataError as error:
            raise MatchDataFormatError("matches.csv が空です。") from error
        except (
            OSError,
            UnicodeError,
            pd.errors.ParserError,
        ) as error:
            raise MatchDataSourceError(
                f"matches.csv を読み込めませんでした：{error}"
            ) from error


@dataclass(frozen=True)
class ApiDataSource:
    """API-FootballからJ1・J2・J3の試合データを取得する。

    API固有の認証、URL、JSON項目名、クラブ名変換はこのクラス内に限定する。
    別APIへ変更するときは、このクラスを同じ ``name`` と ``load()`` を持つ
    実装へ差し替えれば、``app.py`` と計算処理はそのまま利用できる。
    """

    api_key: Optional[str] = None
    base_url: str = API_BASE_URL
    timeout_seconds: float = 10.0
    now: Optional[datetime] = None

    @property
    def name(self) -> str:
        return "API-Football"

    def _get_api_key(self) -> str:
        api_key = (self.api_key or os.getenv(API_KEY_ENV_NAME, "")).strip()

        if not api_key:
            raise MatchDataNotFoundError(
                f"環境変数 {API_KEY_ENV_NAME} が設定されていません。"
            )

        return api_key

    def _request(self, endpoint: str, params: dict[str, Any]) -> list[dict]:
        """APIを1回呼び出し、``response`` 配列だけを返す。"""

        try:
            response = requests.get(
                f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}",
                headers={"x-apisports-key": self._get_api_key()},
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise MatchDataSourceError(
                "API-Footballへ接続できませんでした。"
            ) from error

        try:
            payload = response.json()
        except ValueError as error:
            raise MatchDataFormatError(
                "API-Footballの応答がJSONではありません。"
            ) from error

        if not isinstance(payload, dict):
            raise MatchDataFormatError(
                "API-Footballの応答形式が想定と異なります。"
            )

        api_errors = payload.get("errors")
        if api_errors:
            raise MatchDataSourceError(
                "API-Footballがリクエストを受け付けませんでした。"
            )

        response_items = payload.get("response")
        if not isinstance(response_items, list):
            raise MatchDataFormatError(
                "API-Footballのresponse形式が想定と異なります。"
            )

        return response_items

    def _get_current_leagues(self) -> list[tuple[int, int]]:
        """J1・J2・J3の（リーグID、現在シーズン年）を返す。"""

        league_items = self._request(
            "leagues",
            {"country": "Japan", "current": "true"},
        )

        leagues = []

        for item in league_items:
            league = item.get("league", {})
            league_name = league.get("name")

            if league_name not in API_LEAGUE_NAMES:
                continue

            current_seasons = [
                season
                for season in item.get("seasons", [])
                if season.get("current") is True
            ]

            if not current_seasons:
                continue

            league_id = league.get("id")
            season_year = current_seasons[-1].get("year")

            if isinstance(league_id, int) and isinstance(season_year, int):
                leagues.append((league_id, season_year))

        if not leagues:
            raise MatchDataNotFoundError(
                "API-Footballに現在のJ1・J2・J3が見つかりません。"
            )

        return leagues

    def _get_reference_time(self) -> datetime:
        reference_time = self.now or datetime.now(timezone.utc)

        if reference_time.tzinfo is None:
            return reference_time.replace(tzinfo=timezone.utc)

        return reference_time

    def load(self) -> pd.DataFrame:
        """次の試合と、全クラブの直近5試合平均を返す。"""

        try:
            leagues = self._get_current_leagues()
            current_fixtures: list[dict] = []
            historical_fixtures: list[dict] = []

            # シーズン切替直後にも5試合を確保しやすいよう、当季と前季を取得。
            for league_id, season_year in leagues:
                current_season_items = self._request(
                    "fixtures",
                    {"league": league_id, "season": season_year},
                )
                try:
                    previous_season_items = self._request(
                        "fixtures",
                        {"league": league_id, "season": season_year - 1},
                    )
                except MatchDataSourceError:
                    # 無料枠で前季を取得できなくても、当季データは利用する。
                    previous_season_items = []

                current_fixtures.extend(current_season_items)
                historical_fixtures.extend(current_season_items)
                historical_fixtures.extend(previous_season_items)

            team_stats = self._calculate_team_stats(historical_fixtures)
            return self._create_upcoming_matches(current_fixtures, team_stats)
        except MatchDataSourceError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise MatchDataFormatError(
                "API-Footballの試合データ形式が想定と異なります。"
            ) from error

    def _calculate_team_stats(
        self,
        fixture_items: Sequence[dict],
    ) -> dict[str, TeamRecentStats]:
        """完了済み試合をクラブ別に並べ、直近5試合平均を算出する。"""

        completed_matches: dict[str, list[dict[str, Any]]] = {}

        for item in fixture_items:
            fixture = item.get("fixture", {})
            status = fixture.get("status", {}).get("short")
            goals = item.get("goals", {})
            home_goals = goals.get("home")
            away_goals = goals.get("away")

            if (
                status not in FINISHED_STATUSES
                or not isinstance(home_goals, (int, float))
                or not isinstance(away_goals, (int, float))
            ):
                continue

            match_time = parse_api_datetime(fixture.get("date"))
            teams = item.get("teams", {})
            home_team = translate_api_team_name(
                teams.get("home", {}).get("name", "")
            )
            away_team = translate_api_team_name(
                teams.get("away", {}).get("name", "")
            )

            if not home_team or not away_team:
                continue

            completed_matches.setdefault(home_team, []).append(
                {
                    "date": match_time,
                    "opponent": away_team,
                    "venue": "H",
                    "scored": float(home_goals),
                    "conceded": float(away_goals),
                }
            )
            completed_matches.setdefault(away_team, []).append(
                {
                    "date": match_time,
                    "opponent": home_team,
                    "venue": "A",
                    "scored": float(away_goals),
                    "conceded": float(home_goals),
                }
            )

        team_stats: dict[str, TeamRecentStats] = {}

        for team_name, matches in completed_matches.items():
            recent_matches = sorted(
                matches,
                key=lambda match: match["date"],
                reverse=True,
            )[:5]

            if not recent_matches:
                continue

            match_count = len(recent_matches)
            average_scored = round(
                sum(match["scored"] for match in recent_matches) / match_count,
                2,
            )
            average_conceded = round(
                sum(match["conceded"] for match in recent_matches) / match_count,
                2,
            )
            descriptions = tuple(
                format_recent_match(match) for match in recent_matches
            )

            team_stats[team_name] = TeamRecentStats(
                average_scored=average_scored,
                average_conceded=average_conceded,
                recent_matches=descriptions,
            )

        return team_stats

    def _create_upcoming_matches(
        self,
        fixture_items: Sequence[dict],
        team_stats: dict[str, TeamRecentStats],
    ) -> pd.DataFrame:
        """未開催試合を日付順に並べ、共通列のDataFrameへ変換する。"""

        reference_time = self._get_reference_time()
        upcoming = []

        for item in fixture_items:
            fixture = item.get("fixture", {})
            status = fixture.get("status", {}).get("short")

            if status not in UPCOMING_STATUSES:
                continue

            match_time = parse_api_datetime(fixture.get("date"))
            if match_time < reference_time:
                continue

            teams = item.get("teams", {})
            home_team = translate_api_team_name(
                teams.get("home", {}).get("name", "")
            )
            away_team = translate_api_team_name(
                teams.get("away", {}).get("name", "")
            )

            if not home_team or not away_team:
                continue

            upcoming.append((match_time, home_team, away_team))

        rows = []

        for match_number, (match_time, home_team, away_team) in enumerate(
            sorted(upcoming, key=lambda match: match[0]),
            start=1,
        ):
            home_stats = team_stats.get(home_team)
            away_stats = team_stats.get(away_team)

            rows.append(
                {
                    "match_number": match_number,
                    "match_date": match_time.astimezone(
                        JAPAN_TIMEZONE
                    ).strftime("%Y-%m-%d"),
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_scored": (
                        home_stats.average_scored
                        if home_stats
                        else DEFAULT_MATCH_VALUES["home_scored"]
                    ),
                    "home_conceded": (
                        home_stats.average_conceded
                        if home_stats
                        else DEFAULT_MATCH_VALUES["home_conceded"]
                    ),
                    "away_scored": (
                        away_stats.average_scored
                        if away_stats
                        else DEFAULT_MATCH_VALUES["away_scored"]
                    ),
                    "away_conceded": (
                        away_stats.average_conceded
                        if away_stats
                        else DEFAULT_MATCH_VALUES["away_conceded"]
                    ),
                    "home_recent_matches": join_recent_matches(home_stats),
                    "away_recent_matches": join_recent_matches(away_stats),
                }
            )

        return pd.DataFrame(rows, columns=MATCH_COLUMNS)


# --------------------------------------------------
# APIデータの小さな変換関数
# --------------------------------------------------

def parse_api_datetime(value: Any) -> datetime:
    """APIのISO 8601日時をタイムゾーン付きdatetimeへ変換する。"""

    if not isinstance(value, str) or not value.strip():
        raise MatchDataFormatError("試合日時がありません。")

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def format_recent_match(match: dict[str, Any]) -> str:
    """1試合を日本時間の日付・会場・相手・スコアに整形する。"""

    match_date = match["date"].astimezone(JAPAN_TIMEZONE).strftime("%Y-%m-%d")
    scored = int(match["scored"])
    conceded = int(match["conceded"])
    return (
        f'{match_date} {match["venue"]} vs {match["opponent"]} '
        f"{scored}-{conceded}"
    )


def join_recent_matches(stats: Optional[TeamRecentStats]) -> str:
    """CSVでも扱いやすいよう、直近試合を1つの文字列へまとめる。"""

    if not stats:
        return ""

    return " / ".join(stats.recent_matches)


# --------------------------------------------------
# 読み込み結果とフォールバック
# --------------------------------------------------

@dataclass(frozen=True)
class MatchDataLoadResult:
    """読み込み結果、画面メッセージ、クラブ別平均値をまとめる。"""

    matches: pd.DataFrame
    source_name: str
    status: str
    message: str
    team_stats: dict[str, TeamRecentStats] = field(default_factory=dict)

    @property
    def is_loaded(self) -> bool:
        return self.status == "loaded"


def get_default_data_sources() -> tuple[MatchDataSource, ...]:
    """優先順にAPI、CSVを返す。最後はload_matchesが手入力へ切り替える。"""

    return (ApiDataSource(), CsvMatchDataSource())


def get_default_data_source() -> MatchDataSource:
    """Version 2との互換用。現在の第一取得元を返す。"""

    return get_default_data_sources()[0]


def create_empty_matches() -> pd.DataFrame:
    """手入力時に使う、列だけを持った空データを返す。"""

    return pd.DataFrame(columns=MATCH_COLUMNS)


def normalize_matches(raw_matches: pd.DataFrame) -> pd.DataFrame:
    """CSVやAPIのデータを、app.pyが使う共通形式へそろえる。"""

    if not isinstance(raw_matches, pd.DataFrame):
        raise MatchDataFormatError(
            "試合データはDataFrameで返してください。"
        )

    if raw_matches.empty:
        return create_empty_matches()

    required_columns = {"home_team", "away_team"}
    missing_columns = required_columns - set(raw_matches.columns)

    if missing_columns:
        missing_text = "、".join(sorted(missing_columns))
        raise MatchDataFormatError(f"必須列がありません：{missing_text}")

    matches = raw_matches.copy()

    # match_number がなければ、上から第1試合、第2試合として扱う。
    if "match_number" not in matches.columns:
        matches.insert(0, "match_number", range(1, len(matches) + 1))

    match_numbers = pd.to_numeric(matches["match_number"], errors="coerce")

    # totoは13試合なので、1～13の整数だけを画面へ渡す。
    valid_numbers = (
        match_numbers.notna()
        & (match_numbers % 1 == 0)
        & match_numbers.between(1, 13)
    )
    matches = matches.loc[valid_numbers].copy()
    matches["match_number"] = match_numbers.loc[valid_numbers].astype(int)

    for team_column in ("home_team", "away_team"):
        matches[team_column] = (
            matches[team_column].fillna("").astype(str).str.strip()
        )

    for text_column, default_value in DEFAULT_MATCH_METADATA.items():
        if text_column not in matches.columns:
            matches[text_column] = default_value
        matches[text_column] = (
            matches[text_column].fillna(default_value).astype(str).str.strip()
        )

    # 平均値の列がない・空欄・範囲外の場合はVersion 1の初期値へ戻す。
    for value_column, default_value in DEFAULT_MATCH_VALUES.items():
        if value_column in ("home_team", "away_team"):
            continue

        if value_column not in matches.columns:
            matches[value_column] = default_value

        numeric_values = pd.to_numeric(
            matches[value_column],
            errors="coerce",
        )
        valid_values = numeric_values.between(0.0, 5.0)
        matches[value_column] = (
            numeric_values.where(valid_values, default_value)
            .fillna(default_value)
            .astype(float)
        )

    return (
        matches[MATCH_COLUMNS]
        .drop_duplicates(subset="match_number", keep="first")
        .sort_values("match_number")
        .reset_index(drop=True)
    )


def extract_team_stats(raw_matches: pd.DataFrame) -> dict[str, TeamRecentStats]:
    """全行からクラブ別平均を作る。APIの13試合以降も選択時に利用できる。"""

    if not isinstance(raw_matches, pd.DataFrame) or raw_matches.empty:
        return {}

    team_stats: dict[str, TeamRecentStats] = {}

    side_columns = {
        "home": ("home_team", "home_scored", "home_conceded", "home_recent_matches"),
        "away": ("away_team", "away_scored", "away_conceded", "away_recent_matches"),
    }

    for columns in side_columns.values():
        team_column, scored_column, conceded_column, recent_column = columns

        if not {team_column, scored_column, conceded_column}.issubset(
            raw_matches.columns
        ):
            continue

        for _, row in raw_matches.iterrows():
            team_name = str(row.get(team_column, "")).strip()
            scored = pd.to_numeric(row.get(scored_column), errors="coerce")
            conceded = pd.to_numeric(row.get(conceded_column), errors="coerce")

            if (
                not team_name
                or pd.isna(scored)
                or pd.isna(conceded)
                or not 0.0 <= float(scored) <= 5.0
                or not 0.0 <= float(conceded) <= 5.0
            ):
                continue

            recent_value = row.get(recent_column, "")
            recent_text = (
                ""
                if pd.isna(recent_value)
                else str(recent_value).strip()
            )
            recent_matches = tuple(
                item.strip()
                for item in recent_text.split(" / ")
                if item.strip()
            )
            team_stats[team_name] = TeamRecentStats(
                average_scored=float(scored),
                average_conceded=float(conceded),
                recent_matches=recent_matches,
            )

    return team_stats


def _load_single_source(source: MatchDataSource) -> MatchDataLoadResult:
    """1取得元を読み込み、例外を画面用の安全な結果へ変換する。"""

    try:
        raw_matches = source.load()
        team_stats = extract_team_stats(raw_matches)
        matches = normalize_matches(raw_matches)
    except MatchDataNotFoundError:
        return MatchDataLoadResult(
            matches=create_empty_matches(),
            source_name=source.name,
            status="missing",
            message=f"{source.name}に利用できるデータがありません。",
        )
    except MatchDataSourceError:
        return MatchDataLoadResult(
            matches=create_empty_matches(),
            source_name=source.name,
            status="error",
            message=f"{source.name}を現在利用できません。",
        )

    if matches.empty:
        return MatchDataLoadResult(
            matches=matches,
            source_name=source.name,
            status="empty",
            message=f"{source.name}に利用できる試合がありません。",
            team_stats=team_stats,
        )

    return MatchDataLoadResult(
        matches=matches,
        source_name=source.name,
        status="loaded",
        message=f"{source.name}から{len(matches)}試合を読み込みました。",
        team_stats=team_stats,
    )


def load_matches(
    data_source: Optional[MatchDataSource] = None,
) -> MatchDataLoadResult:
    """API→CSV→手入力の順で、画面に例外を出さず読み込む。

    ``data_source`` を渡した場合はVersion 2と同じく、その取得元だけを
    テスト・利用できる。省略時だけ自動フォールバックを行う。
    """

    if data_source is not None:
        return _load_single_source(data_source)

    unavailable_sources = []

    for source in get_default_data_sources():
        result = _load_single_source(source)

        if result.is_loaded:
            if unavailable_sources:
                skipped = "、".join(unavailable_sources)
                return MatchDataLoadResult(
                    matches=result.matches,
                    source_name=result.source_name,
                    status=result.status,
                    message=(
                        f"{skipped}を利用できなかったため、"
                        f"{result.source_name}から{len(result.matches)}試合を"
                        "読み込みました。"
                    ),
                    team_stats=result.team_stats,
                )
            return result

        unavailable_sources.append(source.name)

    return MatchDataLoadResult(
        matches=create_empty_matches(),
        source_name="手入力",
        status="manual",
        message="APIとCSVを利用できないため、手入力モードで起動しました。",
    )


def get_match_defaults(matches: pd.DataFrame, match_number: int) -> dict:
    """指定試合の初期値を返す。データがなければVersion 1と同じ値。"""

    defaults = {
        "match_number": match_number,
        **DEFAULT_MATCH_METADATA,
        **DEFAULT_MATCH_VALUES,
    }

    if matches.empty:
        return defaults

    selected_match = matches.loc[matches["match_number"] == match_number]

    if selected_match.empty:
        return defaults

    match_values = selected_match.iloc[0]

    for column in (*DEFAULT_MATCH_METADATA, *DEFAULT_MATCH_VALUES):
        defaults[column] = match_values[column]

    return defaults
