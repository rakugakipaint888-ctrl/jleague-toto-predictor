"""Jリーグ公式データ・CSVを共通形式で読み込む。

``app.py`` は取得元を意識せず ``load_matches`` だけを呼び出す。
公式サイト固有のURLとHTML解析は ``JLeagueOfficialDataSource`` に閉じ込め、
取得元を将来変更するときも画面と予測ロジックへ影響させない。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from model_config import (
    OFFICIAL_RESULTS_CACHE_MAX_STALE_SECONDS,
    OFFICIAL_RESULTS_CACHE_TTL_SECONDS,
    OFFICIAL_RESULTS_CACHE_VERSION,
)
from teams import (
    J1,
    J2,
    J3,
    normalize_team_key,
    normalize_team_name,
)


# --------------------------------------------------
# 共通設定
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MATCHES_PATH = PROJECT_ROOT / "data" / "matches.csv"
DEFAULT_OFFICIAL_RESULTS_CACHE_PATH = (
    PROJECT_ROOT / "data" / "cache" / "official_match_results.json"
)

JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")

JLEAGUE_DATA_BASE_URL = "https://data.j-league.or.jp"
JLEAGUE_SITE_BASE_URL = "https://www.jleague.jp"

# 2026/27以降の通常シーズンで使われる大会区分ID。
LEAGUE_FRAME_IDS = {"J1": 1, "J2": 2, "J3": 3}
STANDINGS_SLUGS = {"J1": "j1", "J2": "j2", "J3": "j3"}

# 2026年上半期だけ開催された百年構想リーグ。
# 2026/27開幕直後の直近5試合を補うために利用する。
VISION_LEAGUE_YEAR_ID = "20261"
VISION_LEAGUE_FRAME_IDS = (35, 36)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; JLeagueTotoPersonalApp/5.0; personal-use)"
    ),
    "Accept-Language": "ja,en;q=0.5",
}

ALL_TEAM_NAMES = tuple(J1 + J2 + J3)
ALL_TEAM_NAME_SET = set(ALL_TEAM_NAMES)


# --------------------------------------------------
# app.pyへ渡す列と初期値
# --------------------------------------------------

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
    "home_rank",
    "away_rank",
    "home_points",
    "away_points",
    "home_goal_difference",
    "away_goal_difference",
    "home_season_played",
    "home_season_wins",
    "home_season_draws",
    "home_season_losses",
    "home_season_goals_for",
    "home_season_goals_against",
    "away_season_played",
    "away_season_wins",
    "away_season_draws",
    "away_season_losses",
    "away_season_goals_for",
    "away_season_goals_against",
    "home_played",
    "home_wins",
    "home_draws",
    "home_losses",
    "home_goals_for",
    "home_goals_against",
    "away_played",
    "away_wins",
    "away_draws",
    "away_losses",
    "away_goals_for",
    "away_goals_against",
]

# データがない場合はVersion 1と同じ平均値を使う。
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

DEFAULT_MATCH_DETAILS = {
    "home_rank": None,
    "away_rank": None,
    "home_points": None,
    "away_points": None,
    "home_goal_difference": None,
    "away_goal_difference": None,
    "home_season_played": 0,
    "home_season_wins": 0,
    "home_season_draws": 0,
    "home_season_losses": 0,
    "home_season_goals_for": 0,
    "home_season_goals_against": 0,
    "away_season_played": 0,
    "away_season_wins": 0,
    "away_season_draws": 0,
    "away_season_losses": 0,
    "away_season_goals_for": 0,
    "away_season_goals_against": 0,
    "home_played": 0,
    "home_wins": 0,
    "home_draws": 0,
    "home_losses": 0,
    "home_goals_for": 0,
    "home_goals_against": 0,
    "away_played": 0,
    "away_wins": 0,
    "away_draws": 0,
    "away_losses": 0,
    "away_goals_for": 0,
    "away_goals_against": 0,
}

RANK_COLUMNS = ("home_rank", "away_rank")
OPTIONAL_NONNEGATIVE_COLUMNS = ("home_points", "away_points")
OPTIONAL_INTEGER_COLUMNS = (
    "home_goal_difference",
    "away_goal_difference",
)
SEASON_RECORD_COLUMNS = tuple(
    f"{side}_season_{field_name}"
    for side in ("home", "away")
    for field_name in (
        "played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
    )
)
VENUE_RECORD_COLUMNS = tuple(
    f"{side}_{field_name}"
    for side in ("home", "away")
    for field_name in (
        "played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
    )
)
RECORD_COLUMNS = SEASON_RECORD_COLUMNS + VENUE_RECORD_COLUMNS


# --------------------------------------------------
# J. League Data Siteの略称をteams.pyへそろえる
# --------------------------------------------------

OFFICIAL_TEAM_ABBREVIATIONS = {
    # J1
    "鹿島": "鹿島アントラーズ",
    "水戸": "水戸ホーリーホック",
    "浦和": "浦和レッズ",
    "千葉": "ジェフユナイテッド千葉",
    "柏": "柏レイソル",
    "FC東京": "ＦＣ東京",
    "東京Ｖ": "東京ヴェルディ",
    "町田": "ＦＣ町田ゼルビア",
    "川崎Ｆ": "川崎フロンターレ",
    "横浜FM": "横浜Ｆ・マリノス",
    "清水": "清水エスパルス",
    "名古屋": "名古屋グランパス",
    "京都": "京都サンガF.C.",
    "Ｇ大阪": "ガンバ大阪",
    "Ｃ大阪": "セレッソ大阪",
    "神戸": "ヴィッセル神戸",
    "岡山": "ファジアーノ岡山",
    "広島": "サンフレッチェ広島",
    "福岡": "アビスパ福岡",
    "長崎": "Ｖ・ファーレン長崎",
    # J2
    "札幌": "北海道コンサドーレ札幌",
    "八戸": "ヴァンラーレ八戸",
    "仙台": "ベガルタ仙台",
    "秋田": "ブラウブリッツ秋田",
    "山形": "モンテディオ山形",
    "いわき": "いわきＦＣ",
    "栃木Ｃ": "栃木シティ",
    "大宮": "ＲＢ大宮アルディージャ",
    "横浜FC": "横浜ＦＣ",
    "湘南": "湘南ベルマーレ",
    "甲府": "ヴァンフォーレ甲府",
    "新潟": "アルビレックス新潟",
    "富山": "カターレ富山",
    "磐田": "ジュビロ磐田",
    "藤枝": "藤枝ＭＹＦＣ",
    "徳島": "徳島ヴォルティス",
    "今治": "ＦＣ今治",
    "鳥栖": "サガン鳥栖",
    "大分": "大分トリニータ",
    "宮崎": "テゲバジャーロ宮崎",
    # J3
    "福島": "福島ユナイテッドＦＣ",
    "栃木SC": "栃木ＳＣ",
    "群馬": "ザスパ群馬",
    "相模原": "ＳＣ相模原",
    "松本": "松本山雅ＦＣ",
    "長野": "ＡＣ長野パルセイロ",
    "金沢": "ツエーゲン金沢",
    "岐阜": "ＦＣ岐阜",
    "滋賀": "レイラック滋賀ＦＣ",
    "FC大阪": "ＦＣ大阪",
    "奈良": "奈良クラブ",
    "鳥取": "ガイナーレ鳥取",
    "山口": "レノファ山口ＦＣ",
    "讃岐": "カマタマーレ讃岐",
    "愛媛": "愛媛ＦＣ",
    "高知": "高知ユナイテッドＳＣ",
    "北九州": "ギラヴァンツ北九州",
    "熊本": "ロアッソ熊本",
    "鹿児島": "鹿児島ユナイテッドＦＣ",
    "琉球": "ＦＣ琉球",
}


def normalize_text(value: Any) -> str:
    """表記ゆれ比較用に空白を除いたNFKC文字列へそろえる。"""

    if value is None or pd.isna(value):
        return ""

    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return "".join(character for character in normalized if not character.isspace())


OFFICIAL_TEAM_NAME_MAP = {
    normalize_team_key(alias): canonical_name
    for alias, canonical_name in (
        *OFFICIAL_TEAM_ABBREVIATIONS.items(),
        *((team_name, team_name) for team_name in ALL_TEAM_NAMES),
    )
}


def translate_official_team_name(team_name: Any) -> str:
    """公式サイトの略称・正式名をteams.pyのクラブ名へ変換する。"""

    return normalize_team_name(team_name, OFFICIAL_TEAM_ABBREVIATIONS)


# --------------------------------------------------
# データ取得元の共通インターフェース
# --------------------------------------------------


class MatchDataSource(Protocol):
    """CSV・公式サイトなど、すべての取得元が備える共通形式。"""

    @property
    def name(self) -> str:
        """画面表示用の取得元名を返す。"""

    def load(self) -> pd.DataFrame:
        """試合データをDataFrameとして返す。"""


class MatchDataSourceError(RuntimeError):
    """データ取得元の読み込みに失敗した場合の共通エラー。"""


class MatchDataNotFoundError(MatchDataSourceError):
    """CSVや利用できる公式試合が存在しない場合のエラー。"""


class MatchDataFormatError(MatchDataSourceError):
    """取得データの形式が想定と異なる場合のエラー。"""


@dataclass(frozen=True)
class VenueRecord:
    """ホームまたはアウェイに限定した勝敗・得失点。"""

    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def label(self) -> str:
        """画面表示用の短い成績文字列を返す。"""

        if self.played <= 0:
            return "未取得"

        return (
            f"{self.played}試合 {self.wins}勝{self.draws}分{self.losses}敗 "
            f"{self.goals_for}得点{self.goals_against}失点"
        )

    @property
    def average_scored(self) -> Optional[float]:
        return self.goals_for / self.played if self.played > 0 else None

    @property
    def average_conceded(self) -> Optional[float]:
        return self.goals_against / self.played if self.played > 0 else None


@dataclass(frozen=True)
class RecentMatchRecord:
    """直近試合の時系列計算と画面表示に使う構造化データ。"""

    match_date: date
    opponent: str
    venue: str
    scored: int
    conceded: int
    result: str

    @property
    def label(self) -> str:
        return (
            f"{self.match_date.isoformat()} {self.venue} vs {self.opponent} "
            f"{self.scored}-{self.conceded}（{self.result}）"
        )


@dataclass(frozen=True)
class StandingsRecord:
    """Jリーグ公式順位表の1クラブ分。未掲載値はNoneで保持する。"""

    rank: Optional[int] = None
    points: Optional[int] = None
    played: Optional[int] = None
    wins: Optional[int] = None
    draws: Optional[int] = None
    losses: Optional[int] = None
    goals_for: Optional[int] = None
    goals_against: Optional[int] = None
    goal_difference: Optional[int] = None

    @property
    def is_available(self) -> bool:
        return bool(
            self.played is not None
            and self.played > 0
            and self.points is not None
            and self.goal_difference is not None
        )


@dataclass(frozen=True)
class TeamRecentStats:
    """1クラブの直近成績・順位・会場別成績。"""

    average_scored: float
    average_conceded: float
    recent_matches: tuple[str, ...] = ()
    recent_results: tuple[RecentMatchRecord, ...] = ()
    rank: Optional[int] = None
    points: Optional[int] = None
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    goal_difference: Optional[int] = None
    standings_available: bool = False
    home_record: VenueRecord = field(default_factory=VenueRecord)
    away_record: VenueRecord = field(default_factory=VenueRecord)

    @property
    def season_average_scored(self) -> Optional[float]:
        return self.goals_for / self.played if self.played > 0 else None

    @property
    def season_average_conceded(self) -> Optional[float]:
        return self.goals_against / self.played if self.played > 0 else None

    @property
    def points_per_match(self) -> Optional[float]:
        if self.played <= 0 or self.points is None:
            return None
        return self.points / self.played

    @property
    def goal_difference_per_match(self) -> Optional[float]:
        if self.played <= 0 or self.goal_difference is None:
            return None
        return self.goal_difference / self.played


@dataclass(frozen=True)
class OfficialMatch:
    """公式HTMLから取り出した1試合。"""

    match_time: datetime
    home_team: str
    away_team: str
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    category: str = ""

    @property
    def is_completed(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None


@dataclass(frozen=True)
class OfficialSchedulePage:
    """J. League Data Siteの日程・結果ページ指定。"""

    year_id: str
    frame_ids: tuple[int, ...]

    @property
    def url(self) -> str:
        frame_parameters = "&".join(
            f"competition_frame_ids={frame_id}"
            for frame_id in self.frame_ids
        )
        return (
            f"{JLEAGUE_DATA_BASE_URL}/SFMS01/search"
            f"?competition_years={self.year_id}"
            f"&{frame_parameters}"
        )


@dataclass(frozen=True)
class OfficialDataBundle:
    """公式取得した画面用データとElo用の完了試合履歴。"""

    matches: pd.DataFrame
    completed_matches: tuple[OfficialMatch, ...]
    fetched_at: datetime
    from_cache: bool = False
    team_stats: dict[str, TeamRecentStats] = field(default_factory=dict)

    @property
    def data_start_date(self) -> Optional[date]:
        if not self.completed_matches:
            return None
        return min(match.match_time for match in self.completed_matches).date()

    @property
    def data_end_date(self) -> Optional[date]:
        if not self.completed_matches:
            return None
        return max(match.match_time for match in self.completed_matches).date()


def _serialize_official_match(match: OfficialMatch) -> dict[str, Any]:
    return {
        "match_time": match.match_time.isoformat(),
        "home_team": match.home_team,
        "away_team": match.away_team,
        "home_goals": match.home_goals,
        "away_goals": match.away_goals,
        "category": match.category,
    }


def _deserialize_official_matches(
    rows: Sequence[dict[str, Any]],
) -> list[OfficialMatch]:
    matches = []

    for row in rows:
        try:
            match_time = datetime.fromisoformat(str(row["match_time"]))
            if match_time.tzinfo is None:
                match_time = match_time.replace(tzinfo=JAPAN_TIMEZONE)

            home_team = normalize_team_name(row["home_team"])
            away_team = normalize_team_name(row["away_team"])
            home_goals = row.get("home_goals")
            away_goals = row.get("away_goals")

            matches.append(
                OfficialMatch(
                    match_time=match_time.astimezone(JAPAN_TIMEZONE),
                    home_team=home_team,
                    away_team=away_team,
                    home_goals=(
                        int(home_goals)
                        if home_goals is not None
                        else None
                    ),
                    away_goals=(
                        int(away_goals)
                        if away_goals is not None
                        else None
                    ),
                    category=str(row.get("category", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return matches


def _serialize_venue_record(record: VenueRecord) -> dict[str, int]:
    return {
        "played": record.played,
        "wins": record.wins,
        "draws": record.draws,
        "losses": record.losses,
        "goals_for": record.goals_for,
        "goals_against": record.goals_against,
    }


def _deserialize_venue_record(value: Any) -> VenueRecord:
    if not isinstance(value, Mapping):
        return VenueRecord()

    try:
        return VenueRecord(
            played=max(0, int(value.get("played", 0))),
            wins=max(0, int(value.get("wins", 0))),
            draws=max(0, int(value.get("draws", 0))),
            losses=max(0, int(value.get("losses", 0))),
            goals_for=max(0, int(value.get("goals_for", 0))),
            goals_against=max(0, int(value.get("goals_against", 0))),
        )
    except (TypeError, ValueError):
        return VenueRecord()


def _serialize_standings_record(record: StandingsRecord) -> dict[str, Any]:
    return {
        "rank": record.rank,
        "points": record.points,
        "played": record.played,
        "wins": record.wins,
        "draws": record.draws,
        "losses": record.losses,
        "goals_for": record.goals_for,
        "goals_against": record.goals_against,
        "goal_difference": record.goal_difference,
    }


def _deserialize_standings_records(
    values: Any,
) -> dict[str, StandingsRecord]:
    if not isinstance(values, Mapping):
        return {}

    records = {}

    for team_name, value in values.items():
        if not isinstance(value, Mapping):
            continue

        canonical_name = normalize_team_name(team_name)
        if canonical_name not in ALL_TEAM_NAME_SET:
            continue

        def optional_int(field_name: str) -> Optional[int]:
            raw_value = value.get(field_name)
            if raw_value is None:
                return None
            try:
                return int(raw_value)
            except (TypeError, ValueError):
                return None

        records[canonical_name] = StandingsRecord(
            rank=optional_int("rank"),
            points=optional_int("points"),
            played=optional_int("played"),
            wins=optional_int("wins"),
            draws=optional_int("draws"),
            losses=optional_int("losses"),
            goals_for=optional_int("goals_for"),
            goals_against=optional_int("goals_against"),
            goal_difference=optional_int("goal_difference"),
        )

    return records


def _serialize_team_stats(
    team_stats: Mapping[str, TeamRecentStats],
) -> dict[str, Any]:
    return {
        team_name: {
            "average_scored": stats.average_scored,
            "average_conceded": stats.average_conceded,
            "recent_results": [
                {
                    "match_date": recent.match_date.isoformat(),
                    "opponent": recent.opponent,
                    "venue": recent.venue,
                    "scored": recent.scored,
                    "conceded": recent.conceded,
                    "result": recent.result,
                }
                for recent in stats.recent_results
            ],
            "rank": stats.rank,
            "points": stats.points,
            "played": stats.played,
            "wins": stats.wins,
            "draws": stats.draws,
            "losses": stats.losses,
            "goals_for": stats.goals_for,
            "goals_against": stats.goals_against,
            "goal_difference": stats.goal_difference,
            "standings_available": stats.standings_available,
            "home_record": _serialize_venue_record(stats.home_record),
            "away_record": _serialize_venue_record(stats.away_record),
        }
        for team_name, stats in team_stats.items()
    }


def _deserialize_team_stats(values: Any) -> dict[str, TeamRecentStats]:
    if not isinstance(values, Mapping):
        return {}

    result = {}

    for team_name, value in values.items():
        if not isinstance(value, Mapping):
            continue

        canonical_name = normalize_team_name(team_name)
        if canonical_name not in ALL_TEAM_NAME_SET:
            continue

        recent_results = []
        for recent in value.get("recent_results", []):
            try:
                recent_results.append(
                    RecentMatchRecord(
                        match_date=date.fromisoformat(str(recent["match_date"])),
                        opponent=normalize_team_name(recent["opponent"]),
                        venue=str(recent["venue"]),
                        scored=max(0, int(recent["scored"])),
                        conceded=max(0, int(recent["conceded"])),
                        result=str(recent["result"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        try:
            stats = TeamRecentStats(
                average_scored=float(value["average_scored"]),
                average_conceded=float(value["average_conceded"]),
                recent_matches=tuple(item.label for item in recent_results),
                recent_results=tuple(recent_results),
                rank=(
                    int(value["rank"])
                    if value.get("rank") is not None
                    else None
                ),
                points=(
                    int(value["points"])
                    if value.get("points") is not None
                    else None
                ),
                played=max(0, int(value.get("played", 0))),
                wins=max(0, int(value.get("wins", 0))),
                draws=max(0, int(value.get("draws", 0))),
                losses=max(0, int(value.get("losses", 0))),
                goals_for=max(0, int(value.get("goals_for", 0))),
                goals_against=max(0, int(value.get("goals_against", 0))),
                goal_difference=(
                    int(value["goal_difference"])
                    if value.get("goal_difference") is not None
                    else None
                ),
                standings_available=bool(value.get("standings_available")),
                home_record=_deserialize_venue_record(value.get("home_record")),
                away_record=_deserialize_venue_record(value.get("away_record")),
            )
        except (KeyError, TypeError, ValueError):
            continue

        result[canonical_name] = stats

    return result


def _read_official_results_cache(
    cache_path: Path,
    season_start_year: int,
) -> Optional[dict[str, Any]]:
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if (
        cache.get("version") != OFFICIAL_RESULTS_CACHE_VERSION
        or cache.get("season_start_year") != season_start_year
        or not cache.get("fetched_at")
    ):
        return None

    return cache


def _official_cache_age_seconds(
    cache: Optional[dict[str, Any]],
    reference_time: datetime,
) -> Optional[float]:
    if cache is None:
        return None

    try:
        fetched_at = datetime.fromisoformat(str(cache["fetched_at"]))
    except (KeyError, TypeError, ValueError):
        return None

    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=JAPAN_TIMEZONE)

    return max(
        0.0,
        (reference_time - fetched_at.astimezone(JAPAN_TIMEZONE)).total_seconds(),
    )


def _write_official_results_cache(
    cache_path: Path,
    season_start_year: int,
    fetched_at: datetime,
    current_matches: Sequence[OfficialMatch],
    history_matches: Sequence[OfficialMatch],
    standings: Mapping[str, StandingsRecord],
    team_stats: Mapping[str, TeamRecentStats],
) -> None:
    payload = {
        "version": OFFICIAL_RESULTS_CACHE_VERSION,
        "season_start_year": season_start_year,
        "fetched_at": fetched_at.isoformat(),
        "current_matches": [
            _serialize_official_match(match)
            for match in current_matches
        ],
        "history_matches": [
            _serialize_official_match(match)
            for match in history_matches
        ],
        "standings": {
            team_name: _serialize_standings_record(record)
            for team_name, record in standings.items()
        },
        "team_stats": _serialize_team_stats(team_stats),
    }

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(cache_path)
    except OSError:
        # キャッシュ保存失敗で公式取得や予測を停止させない。
        return


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
            return pd.read_csv(self.path, encoding="utf-8-sig")
        except pd.errors.EmptyDataError as error:
            raise MatchDataFormatError("matches.csv が空です。") from error
        except (OSError, UnicodeError, pd.errors.ParserError) as error:
            raise MatchDataSourceError(
                f"matches.csv を読み込めませんでした：{error}"
            ) from error


@dataclass(frozen=True)
class JLeagueOfficialDataSource:
    """Jリーグ公式の公開ページから試合・順位データを取得する。

    日程・結果は ``J. League Data Site``、順位は ``J.LEAGUE.jp`` を使う。
    認証情報や有料APIは使用しない。HTML固有処理はすべてこのクラス内に置く。
    """

    timeout_seconds: float = 15.0
    now: Optional[datetime] = None
    cache_path: Path = DEFAULT_OFFICIAL_RESULTS_CACHE_PATH
    cache_ttl_seconds: int = OFFICIAL_RESULTS_CACHE_TTL_SECONDS
    cache_max_stale_seconds: int = OFFICIAL_RESULTS_CACHE_MAX_STALE_SECONDS

    @property
    def name(self) -> str:
        return "Jリーグ公式データ"

    def _reference_time(self) -> datetime:
        reference = self.now or datetime.now(JAPAN_TIMEZONE)

        if reference.tzinfo is None:
            return reference.replace(tzinfo=JAPAN_TIMEZONE)

        return reference.astimezone(JAPAN_TIMEZONE)

    def _current_season_start_year(self) -> int:
        """秋春制シーズンの開始年を返す。"""

        reference = self._reference_time()
        return reference.year if reference.month >= 7 else reference.year - 1

    def _current_schedule_pages(self) -> tuple[OfficialSchedulePage, ...]:
        season_year = str(self._current_season_start_year())
        return (
            OfficialSchedulePage(
                season_year,
                tuple(LEAGUE_FRAME_IDS.values()),
            ),
        )

    def _history_schedule_pages(self) -> tuple[OfficialSchedulePage, ...]:
        season_year = self._current_season_start_year()

        if season_year == 2026:
            return (
                OfficialSchedulePage(
                    VISION_LEAGUE_YEAR_ID,
                    VISION_LEAGUE_FRAME_IDS,
                ),
            )

        previous_year = str(season_year - 1)
        return (
            OfficialSchedulePage(
                previous_year,
                tuple(LEAGUE_FRAME_IDS.values()),
            ),
        )

    def _request_html(self, url: str) -> str:
        try:
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise MatchDataSourceError(
                "Jリーグ公式サイトへ接続できませんでした。"
            ) from error

        if not response.text.strip():
            raise MatchDataFormatError("Jリーグ公式ページが空です。")

        return response.text

    def _read_html_tables(self, html: str) -> list[pd.DataFrame]:
        try:
            return pd.read_html(StringIO(html))
        except (ImportError, ValueError) as error:
            raise MatchDataFormatError(
                "Jリーグ公式ページの表を解析できませんでした。"
            ) from error

    def _fetch_schedule_page(
        self,
        page: OfficialSchedulePage,
    ) -> list[OfficialMatch]:
        tables = self._read_html_tables(self._request_html(page.url))
        schedule_table = _find_table(
            tables,
            required_headers=("試合日", "ホーム", "スコア", "アウェイ"),
        )

        if schedule_table is None:
            raise MatchDataFormatError(
                "J. League Data Siteの日程表が見つかりません。"
            )

        return _parse_schedule_table(schedule_table)

    def _fetch_standings(self) -> dict[str, StandingsRecord]:
        """順位・勝点・得失点を取得し、失敗カテゴリーだけ欠損とする。"""

        standings: dict[str, StandingsRecord] = {}

        for category, slug in STANDINGS_SLUGS.items():
            url = f"{JLEAGUE_SITE_BASE_URL}/{slug}/standings/"

            try:
                tables = self._read_html_tables(self._request_html(url))
                standings_table = _find_table(
                    tables,
                    required_headers=("順位", "クラブ"),
                )
                if standings_table is not None:
                    standings.update(_parse_standings_table(standings_table))
            except MatchDataSourceError:
                # 順位表は補助情報。試合と直近成績を取得できれば継続する。
                continue

        return standings

    def _create_bundle(
        self,
        current_matches: Sequence[OfficialMatch],
        history_matches: Sequence[OfficialMatch],
        standings: dict[str, StandingsRecord],
        fetched_at: datetime,
        from_cache: bool,
        cached_team_stats: Optional[dict[str, TeamRecentStats]] = None,
    ) -> OfficialDataBundle:
        reference_time = self._reference_time()
        current_completed_matches = _deduplicate_matches(
            match
            for match in current_matches
            if match.is_completed and match.match_time <= reference_time
        )
        completed_matches = _deduplicate_matches(
            match
            for match in (*current_matches, *history_matches)
            if match.is_completed and match.match_time <= reference_time
        )
        team_stats = cached_team_stats or _calculate_team_stats(
            completed_matches,
            standings,
            season_matches=current_completed_matches,
        )

        return OfficialDataBundle(
            matches=_create_upcoming_matches(
                current_matches,
                team_stats,
                reference_time,
            ),
            completed_matches=tuple(
                sorted(
                    completed_matches,
                    key=lambda match: match.match_time,
                )
            ),
            fetched_at=fetched_at,
            from_cache=from_cache,
            team_stats=team_stats,
        )

    def _fetch_live_bundle(self) -> OfficialDataBundle:
        current_matches = [
            match
            for page in self._current_schedule_pages()
            for match in self._fetch_schedule_page(page)
        ]

        if not current_matches:
            raise MatchDataNotFoundError(
                "Jリーグ公式サイトに試合データがありません。"
            )

        # Eloはシーズンをまたいで引き継ぐため、前シーズン相当も常に取得する。
        history_matches = [
            match
            for page in self._history_schedule_pages()
            for match in self._fetch_schedule_page(page)
        ]
        standings = self._fetch_standings()
        fetched_at = self._reference_time()

        bundle = self._create_bundle(
            current_matches,
            history_matches,
            standings,
            fetched_at,
            from_cache=False,
        )

        _write_official_results_cache(
            self.cache_path,
            season_start_year=self._current_season_start_year(),
            fetched_at=fetched_at,
            current_matches=current_matches,
            history_matches=history_matches,
            standings=standings,
            team_stats=bundle.team_stats,
        )

        return bundle

    def _bundle_from_cache(
        self,
        cache: dict[str, Any],
    ) -> OfficialDataBundle:
        return self._create_bundle(
            _deserialize_official_matches(cache.get("current_matches", [])),
            _deserialize_official_matches(cache.get("history_matches", [])),
            _deserialize_standings_records(cache.get("standings", {})),
            datetime.fromisoformat(cache["fetched_at"]),
            from_cache=True,
            cached_team_stats=_deserialize_team_stats(
                cache.get("team_stats", {})
            ),
        )

    def load_bundle(self) -> OfficialDataBundle:
        """画面用データとElo用履歴を、永続キャッシュ込みで返す。"""

        cache = _read_official_results_cache(
            self.cache_path,
            self._current_season_start_year(),
        )
        cache_age_seconds = _official_cache_age_seconds(
            cache,
            self._reference_time(),
        )

        if (
            cache is not None
            and cache_age_seconds is not None
            and cache_age_seconds <= self.cache_ttl_seconds
        ):
            return self._bundle_from_cache(cache)

        try:
            return self._fetch_live_bundle()
        except MatchDataSourceError:
            if (
                cache is not None
                and cache_age_seconds is not None
                and cache_age_seconds <= self.cache_max_stale_seconds
            ):
                return self._bundle_from_cache(cache)
            raise
        except (KeyError, TypeError, ValueError) as error:
            if (
                cache is not None
                and cache_age_seconds is not None
                and cache_age_seconds <= self.cache_max_stale_seconds
            ):
                return self._bundle_from_cache(cache)
            raise MatchDataFormatError(
                "Jリーグ公式データの形式が想定と異なります。"
            ) from error

    def load(self) -> pd.DataFrame:
        """既存MatchDataSourceインターフェースを維持する。"""

        return self.load_bundle().matches


# --------------------------------------------------
# 公式HTMLの変換
# --------------------------------------------------


def _normalized_headers(table: pd.DataFrame) -> dict[str, Any]:
    return {normalize_text(column): column for column in table.columns}


def _find_column(table: pd.DataFrame, keyword: str) -> Optional[Any]:
    normalized_keyword = normalize_text(keyword)

    for normalized_header, original_header in _normalized_headers(table).items():
        if normalized_keyword in normalized_header:
            return original_header

    return None


def _find_table(
    tables: Sequence[pd.DataFrame],
    required_headers: Sequence[str],
) -> Optional[pd.DataFrame]:
    for table in tables:
        if all(_find_column(table, header) is not None for header in required_headers):
            return table

    return None


def _parse_official_datetime(date_value: Any, kickoff_value: Any) -> datetime:
    date_text = normalize_text(date_value)
    date_match = re.search(r"(\d{2})/(\d{2})/(\d{2})", date_text)

    if not date_match:
        raise MatchDataFormatError("公式データの試合日を解析できません。")

    year, month, day = (int(part) for part in date_match.groups())
    year += 2000

    kickoff_text = normalize_text(kickoff_value)
    kickoff_match = re.search(r"(\d{1,2}):(\d{2})", kickoff_text)

    if kickoff_match:
        hour, minute = (int(part) for part in kickoff_match.groups())
    else:
        # 未定の試合は同日の確定時刻より後ろへ並べる。
        hour, minute = 23, 59

    return datetime(year, month, day, hour, minute, tzinfo=JAPAN_TIMEZONE)


def _parse_score(score_value: Any) -> Optional[tuple[int, int]]:
    score_text = normalize_text(score_value)
    score_match = re.match(r"^(\d+)\s*[-−ー]\s*(\d+)", score_text)

    if not score_match:
        return None

    return tuple(int(part) for part in score_match.groups())


def _parse_competition_category(competition_value: Any) -> str:
    """公式大会名からJ1・J2・J3または合同カテゴリーを返す。"""

    competition_text = normalize_text(competition_value).upper()
    includes_j1 = "J1" in competition_text
    includes_j2 = "J2" in competition_text
    includes_j3 = "J3" in competition_text

    if includes_j1 and not includes_j2 and not includes_j3:
        return "J1"
    if includes_j2 and includes_j3:
        return "J2/J3"
    if includes_j2:
        return "J2"
    if includes_j3:
        return "J3"
    return ""


def _parse_schedule_table(table: pd.DataFrame) -> list[OfficialMatch]:
    competition_column = _find_column(table, "大会")
    date_column = _find_column(table, "試合日")
    kickoff_column = _find_column(table, "K/O時刻")
    home_column = _find_column(table, "ホーム")
    score_column = _find_column(table, "スコア")
    away_column = _find_column(table, "アウェイ")

    if None in (date_column, home_column, score_column, away_column):
        raise MatchDataFormatError("日程表の必須列が見つかりません。")

    matches = []

    for _, row in table.iterrows():
        home_team = translate_official_team_name(row.get(home_column, ""))
        away_team = translate_official_team_name(row.get(away_column, ""))

        # 前シーズンにJリーグを離れたクラブとの結果も、現所属クラブの
        # Elo引継ぎには必要。両方とも現行60クラブ外の試合だけ除外する。
        if home_team not in ALL_TEAM_NAME_SET and away_team not in ALL_TEAM_NAME_SET:
            continue

        try:
            match_time = _parse_official_datetime(
                row.get(date_column, ""),
                row.get(kickoff_column, "") if kickoff_column is not None else "",
            )
        except MatchDataFormatError:
            continue

        score = _parse_score(row.get(score_column, ""))
        home_goals, away_goals = score if score is not None else (None, None)

        matches.append(
            OfficialMatch(
                match_time=match_time,
                home_team=home_team,
                away_team=away_team,
                home_goals=home_goals,
                away_goals=away_goals,
                category=_parse_competition_category(
                    row.get(competition_column, "")
                    if competition_column is not None
                    else ""
                ),
            )
        )

    return matches


def _find_standings_column(
    table: pd.DataFrame,
    candidates: Sequence[str],
) -> Optional[Any]:
    """勝点と勝数などを取り違えないよう順位表列を完全一致で探す。"""

    headers = _normalized_headers(table)

    for candidate in candidates:
        normalized_candidate = normalize_text(candidate)
        for normalized_header, original_header in headers.items():
            if normalized_header in (
                normalized_candidate,
                normalized_candidate * 2,
            ):
                return original_header

    return None


def _parse_optional_integer(value: Any) -> Optional[int]:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_standings_table(
    table: pd.DataFrame,
) -> dict[str, StandingsRecord]:
    rank_column = _find_standings_column(table, ("順位順位", "順位"))
    club_column = _find_standings_column(table, ("クラブ",))

    if rank_column is None or club_column is None:
        return {}

    columns = {
        "points": _find_standings_column(table, ("勝点勝点", "勝点")),
        "played": _find_standings_column(
            table,
            ("試合試合数", "試合数"),
        ),
        "wins": _find_standings_column(table, ("勝勝", "勝")),
        "draws": _find_standings_column(table, ("分分", "分")),
        "losses": _find_standings_column(table, ("負負", "負")),
        "goals_for": _find_standings_column(table, ("得点得点", "得点")),
        "goals_against": _find_standings_column(table, ("失点失点", "失点")),
        "goal_difference": _find_standings_column(
            table,
            ("得失得失点", "得失点"),
        ),
    }
    standings = {}

    for _, row in table.iterrows():
        team_name = translate_official_team_name(row.get(club_column, ""))
        if team_name not in ALL_TEAM_NAME_SET:
            continue

        values = {
            field_name: (
                _parse_optional_integer(row.get(column))
                if column is not None
                else None
            )
            for field_name, column in columns.items()
        }
        standings[team_name] = StandingsRecord(
            rank=_parse_optional_integer(row.get(rank_column)),
            **values,
        )

    return standings


# --------------------------------------------------
# 公式試合からクラブ統計を計算
# --------------------------------------------------


def _match_identity(match: OfficialMatch) -> tuple[Any, ...]:
    return (
        match.match_time,
        match.home_team,
        match.away_team,
        match.home_goals,
        match.away_goals,
        match.category,
    )


def _deduplicate_matches(matches: Sequence[OfficialMatch]) -> list[OfficialMatch]:
    unique_matches = {}

    for match in matches:
        unique_matches[_match_identity(match)] = match

    return list(unique_matches.values())


def _needs_history(completed_matches: Sequence[OfficialMatch]) -> bool:
    match_counts = {team_name: 0 for team_name in ALL_TEAM_NAMES}

    for match in completed_matches:
        match_counts[match.home_team] += 1
        match_counts[match.away_team] += 1

    return any(match_count < 5 for match_count in match_counts.values())


def _build_venue_record(
    matches: Sequence[OfficialMatch],
    team_name: str,
    venue: str,
) -> VenueRecord:
    wins = draws = losses = goals_for = goals_against = 0

    for match in matches:
        if venue in ("home", "all") and match.home_team == team_name:
            scored = int(match.home_goals or 0)
            conceded = int(match.away_goals or 0)
        elif venue in ("away", "all") and match.away_team == team_name:
            scored = int(match.away_goals or 0)
            conceded = int(match.home_goals or 0)
        else:
            continue

        goals_for += scored
        goals_against += conceded

        if scored > conceded:
            wins += 1
        elif scored == conceded:
            draws += 1
        else:
            losses += 1

    return VenueRecord(
        played=wins + draws + losses,
        wins=wins,
        draws=draws,
        losses=losses,
        goals_for=goals_for,
        goals_against=goals_against,
    )


def _format_recent_match(
    match: OfficialMatch,
    team_name: str,
) -> str:
    is_home = match.home_team == team_name
    opponent = match.away_team if is_home else match.home_team
    scored = match.home_goals if is_home else match.away_goals
    conceded = match.away_goals if is_home else match.home_goals
    venue = "H" if is_home else "A"

    return (
        f"{match.match_time.strftime('%Y-%m-%d')} {venue} vs {opponent} "
        f"{int(scored or 0)}-{int(conceded or 0)}"
    )


def _create_recent_match_record(
    match: OfficialMatch,
    team_name: str,
) -> RecentMatchRecord:
    is_home = match.home_team == team_name
    opponent = match.away_team if is_home else match.home_team
    scored = int(
        (match.home_goals if is_home else match.away_goals) or 0
    )
    conceded = int(
        (match.away_goals if is_home else match.home_goals) or 0
    )
    result = "勝" if scored > conceded else "分" if scored == conceded else "敗"
    return RecentMatchRecord(
        match_date=match.match_time.date(),
        opponent=opponent,
        venue="H" if is_home else "A",
        scored=scored,
        conceded=conceded,
        result=result,
    )


def _calculate_team_stats(
    completed_matches: Sequence[OfficialMatch],
    standings: Mapping[str, StandingsRecord],
    season_matches: Optional[Sequence[OfficialMatch]] = None,
) -> dict[str, TeamRecentStats]:
    team_stats = {}
    season_matches = (
        completed_matches if season_matches is None else season_matches
    )

    for team_name in ALL_TEAM_NAMES:
        team_matches = [
            match
            for match in completed_matches
            if team_name in (match.home_team, match.away_team)
        ]

        recent_matches = sorted(
            team_matches,
            key=lambda match: match.match_time,
            reverse=True,
        )[:5]

        standing = standings.get(team_name)
        season_record = _build_venue_record(
            season_matches,
            team_name,
            "all",
        )

        if not recent_matches and standing is None and season_record.played <= 0:
            continue

        scored_values = []
        conceded_values = []

        for match in recent_matches:
            if match.home_team == team_name:
                scored_values.append(int(match.home_goals or 0))
                conceded_values.append(int(match.away_goals or 0))
            else:
                scored_values.append(int(match.away_goals or 0))
                conceded_values.append(int(match.home_goals or 0))

        recent_results = tuple(
            _create_recent_match_record(match, team_name)
            for match in recent_matches
        )
        played = (
            standing.played
            if standing and standing.played is not None
            else season_record.played
        )
        wins = (
            standing.wins
            if standing and standing.wins is not None
            else season_record.wins
        )
        draws = (
            standing.draws
            if standing and standing.draws is not None
            else season_record.draws
        )
        losses = (
            standing.losses
            if standing and standing.losses is not None
            else season_record.losses
        )
        goals_for = (
            standing.goals_for
            if standing and standing.goals_for is not None
            else season_record.goals_for
        )
        goals_against = (
            standing.goals_against
            if standing and standing.goals_against is not None
            else season_record.goals_against
        )
        points = (
            standing.points
            if standing and standing.points is not None
            else (
                season_record.wins * 3 + season_record.draws
                if season_record.played > 0
                else None
            )
        )
        goal_difference = (
            standing.goal_difference
            if standing and standing.goal_difference is not None
            else (
                season_record.goals_for - season_record.goals_against
                if season_record.played > 0
                else None
            )
        )
        average_scored = (
            sum(scored_values) / len(scored_values)
            if scored_values
            else goals_for / played
            if played > 0
            else 1.3
        )
        average_conceded = (
            sum(conceded_values) / len(conceded_values)
            if conceded_values
            else goals_against / played
            if played > 0
            else 1.3
        )

        team_stats[team_name] = TeamRecentStats(
            average_scored=round(average_scored, 2),
            average_conceded=round(average_conceded, 2),
            recent_matches=tuple(
                _format_recent_match(match, team_name)
                for match in recent_matches
            ),
            recent_results=recent_results,
            rank=standing.rank if standing else None,
            points=points,
            played=int(played),
            wins=int(wins),
            draws=int(draws),
            losses=int(losses),
            goals_for=int(goals_for),
            goals_against=int(goals_against),
            goal_difference=goal_difference,
            standings_available=bool(standing and standing.is_available),
            home_record=_build_venue_record(
                completed_matches,
                team_name,
                "home",
            ),
            away_record=_build_venue_record(
                completed_matches,
                team_name,
                "away",
            ),
        )

    return team_stats


def _record_values(prefix: str, record: VenueRecord) -> dict[str, int]:
    return {
        f"{prefix}_played": record.played,
        f"{prefix}_wins": record.wins,
        f"{prefix}_draws": record.draws,
        f"{prefix}_losses": record.losses,
        f"{prefix}_goals_for": record.goals_for,
        f"{prefix}_goals_against": record.goals_against,
    }


def _standing_values(
    prefix: str,
    stats: Optional[TeamRecentStats],
) -> dict[str, Any]:
    return {
        f"{prefix}_points": stats.points if stats else None,
        f"{prefix}_goal_difference": (
            stats.goal_difference if stats else None
        ),
        f"{prefix}_season_played": stats.played if stats else 0,
        f"{prefix}_season_wins": stats.wins if stats else 0,
        f"{prefix}_season_draws": stats.draws if stats else 0,
        f"{prefix}_season_losses": stats.losses if stats else 0,
        f"{prefix}_season_goals_for": stats.goals_for if stats else 0,
        f"{prefix}_season_goals_against": (
            stats.goals_against if stats else 0
        ),
    }


def _join_recent_matches(stats: Optional[TeamRecentStats]) -> str:
    if not stats:
        return ""

    return " / ".join(stats.recent_matches)


def _create_upcoming_matches(
    current_matches: Sequence[OfficialMatch],
    team_stats: dict[str, TeamRecentStats],
    reference_time: datetime,
) -> pd.DataFrame:
    upcoming_matches = sorted(
        (
            match
            for match in current_matches
            if not match.is_completed and match.match_time >= reference_time
        ),
        key=lambda match: match.match_time,
    )

    rows = []

    for match_number, match in enumerate(upcoming_matches, start=1):
        home_stats = team_stats.get(match.home_team)
        away_stats = team_stats.get(match.away_team)

        home_record = home_stats.home_record if home_stats else VenueRecord()
        away_record = away_stats.away_record if away_stats else VenueRecord()

        rows.append(
            {
                "match_number": match_number,
                "match_date": match.match_time.strftime("%Y-%m-%d"),
                "home_team": match.home_team,
                "away_team": match.away_team,
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
                "home_recent_matches": _join_recent_matches(home_stats),
                "away_recent_matches": _join_recent_matches(away_stats),
                "home_rank": home_stats.rank if home_stats else None,
                "away_rank": away_stats.rank if away_stats else None,
                **_standing_values("home", home_stats),
                **_standing_values("away", away_stats),
                **_record_values("home", home_record),
                **_record_values("away", away_record),
            }
        )

    return pd.DataFrame(rows, columns=MATCH_COLUMNS)


# --------------------------------------------------
# 読み込み結果・CSV正規化・フォールバック
# --------------------------------------------------


@dataclass(frozen=True)
class MatchDataLoadResult:
    """読み込み結果とクラブ別統計をまとめる。"""

    matches: pd.DataFrame
    source_name: str
    status: str
    message: str
    team_stats: dict[str, TeamRecentStats] = field(default_factory=dict)
    completed_matches: tuple[OfficialMatch, ...] = ()
    data_start_date: Optional[date] = None
    data_end_date: Optional[date] = None
    official_cache_used: bool = False

    @property
    def is_loaded(self) -> bool:
        return self.status == "loaded"


def get_default_data_sources() -> tuple[MatchDataSource, ...]:
    """公式→CSVの優先順を返す。最後はload_matchesが手入力へ切り替える。"""

    return (JLeagueOfficialDataSource(), CsvMatchDataSource())


def get_default_data_source() -> MatchDataSource:
    """Version 2との互換用。第一取得元を返す。"""

    return get_default_data_sources()[0]


def create_empty_matches() -> pd.DataFrame:
    """手入力時に使う、列だけを持った空データを返す。"""

    return pd.DataFrame(columns=MATCH_COLUMNS)


def _normalize_rank(value: Any) -> Optional[int]:
    rank = pd.to_numeric(value, errors="coerce")

    if pd.isna(rank) or not 1 <= float(rank) <= 60:
        return None

    return int(rank)


def _normalize_nonnegative_integer(value: Any) -> int:
    number = pd.to_numeric(value, errors="coerce")

    if pd.isna(number) or float(number) < 0:
        return 0

    return int(number)


def _normalize_optional_nonnegative_integer(value: Any) -> Optional[int]:
    number = pd.to_numeric(value, errors="coerce")

    if pd.isna(number) or float(number) < 0:
        return None

    return int(number)


def _normalize_optional_integer(value: Any) -> Optional[int]:
    number = pd.to_numeric(value, errors="coerce")

    if pd.isna(number):
        return None

    return int(number)


def normalize_matches(raw_matches: pd.DataFrame) -> pd.DataFrame:
    """CSV・公式データをapp.pyが使う共通形式へそろえる。"""

    if not isinstance(raw_matches, pd.DataFrame):
        raise MatchDataFormatError("試合データはDataFrameで返してください。")

    if raw_matches.empty:
        return create_empty_matches()

    required_columns = {"home_team", "away_team"}
    missing_columns = required_columns - set(raw_matches.columns)

    if missing_columns:
        missing_text = "、".join(sorted(missing_columns))
        raise MatchDataFormatError(f"必須列がありません：{missing_text}")

    matches = raw_matches.copy()

    if "match_number" not in matches.columns:
        matches.insert(0, "match_number", range(1, len(matches) + 1))

    match_numbers = pd.to_numeric(matches["match_number"], errors="coerce")
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

    for value_column, default_value in DEFAULT_MATCH_VALUES.items():
        if value_column in ("home_team", "away_team"):
            continue

        if value_column not in matches.columns:
            matches[value_column] = default_value

        numeric_values = pd.to_numeric(matches[value_column], errors="coerce")
        valid_values = numeric_values.between(0.0, 5.0)
        matches[value_column] = (
            numeric_values.where(valid_values, default_value)
            .fillna(default_value)
            .astype(float)
        )

    for rank_column in RANK_COLUMNS:
        if rank_column not in matches.columns:
            matches[rank_column] = None
        matches[rank_column] = matches[rank_column].map(_normalize_rank)

    for optional_column in OPTIONAL_NONNEGATIVE_COLUMNS:
        if optional_column not in matches.columns:
            matches[optional_column] = None
        matches[optional_column] = matches[optional_column].map(
            _normalize_optional_nonnegative_integer
        )

    for optional_column in OPTIONAL_INTEGER_COLUMNS:
        if optional_column not in matches.columns:
            matches[optional_column] = None
        matches[optional_column] = matches[optional_column].map(
            _normalize_optional_integer
        )

    for record_column in RECORD_COLUMNS:
        if record_column not in matches.columns:
            matches[record_column] = 0
        matches[record_column] = matches[record_column].map(
            _normalize_nonnegative_integer
        )

    return (
        matches[MATCH_COLUMNS]
        .drop_duplicates(subset="match_number", keep="first")
        .sort_values("match_number")
        .reset_index(drop=True)
    )


def _row_venue_record(row: pd.Series, prefix: str) -> VenueRecord:
    return VenueRecord(
        played=_normalize_nonnegative_integer(row.get(f"{prefix}_played")),
        wins=_normalize_nonnegative_integer(row.get(f"{prefix}_wins")),
        draws=_normalize_nonnegative_integer(row.get(f"{prefix}_draws")),
        losses=_normalize_nonnegative_integer(row.get(f"{prefix}_losses")),
        goals_for=_normalize_nonnegative_integer(row.get(f"{prefix}_goals_for")),
        goals_against=_normalize_nonnegative_integer(
            row.get(f"{prefix}_goals_against")
        ),
    )


def _parse_recent_match_text(value: str) -> Optional[RecentMatchRecord]:
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})\s+([HA])\s+vs\s+(.+?)\s+"
        r"(\d+)-(\d+)(?:（([勝分敗])）)?$",
        str(value).strip(),
    )

    if not match:
        return None

    date_text, venue, opponent, scored_text, conceded_text, result = (
        match.groups()
    )
    try:
        scored = int(scored_text)
        conceded = int(conceded_text)
        match_date = date.fromisoformat(date_text)
    except (TypeError, ValueError):
        return None

    if not result:
        result = "勝" if scored > conceded else "分" if scored == conceded else "敗"

    return RecentMatchRecord(
        match_date=match_date,
        opponent=normalize_team_name(opponent),
        venue=venue,
        scored=scored,
        conceded=conceded,
        result=result,
    )


def extract_team_stats(raw_matches: pd.DataFrame) -> dict[str, TeamRecentStats]:
    """全行から選択連動用のクラブ別統計を作る。"""

    if not isinstance(raw_matches, pd.DataFrame) or raw_matches.empty:
        return {}

    team_stats: dict[str, TeamRecentStats] = {}

    side_columns = {
        "home": ("home_team", "home_scored", "home_conceded", "home_recent_matches"),
        "away": ("away_team", "away_scored", "away_conceded", "away_recent_matches"),
    }

    for side, columns in side_columns.items():
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
            recent_text = "" if pd.isna(recent_value) else str(recent_value).strip()
            recent_matches = tuple(
                item.strip()
                for item in recent_text.split(" / ")
                if item.strip()
            )
            recent_results = tuple(
                parsed
                for parsed in (
                    _parse_recent_match_text(item)
                    for item in recent_matches
                )
                if parsed is not None
            )

            existing = team_stats.get(team_name)
            rank = _normalize_rank(row.get(f"{side}_rank"))
            points = _normalize_optional_nonnegative_integer(
                row.get(f"{side}_points")
            )
            played_value = row.get(f"{side}_season_played")
            season_played = _normalize_nonnegative_integer(played_value)
            season_wins = _normalize_nonnegative_integer(
                row.get(f"{side}_season_wins")
            )
            season_draws = _normalize_nonnegative_integer(
                row.get(f"{side}_season_draws")
            )
            season_losses = _normalize_nonnegative_integer(
                row.get(f"{side}_season_losses")
            )
            season_goals_for = _normalize_nonnegative_integer(
                row.get(f"{side}_season_goals_for")
            )
            season_goals_against = _normalize_nonnegative_integer(
                row.get(f"{side}_season_goals_against")
            )
            goal_difference = _normalize_optional_integer(
                row.get(f"{side}_goal_difference")
            )

            if existing and season_played <= 0:
                season_played = existing.played
                season_wins = existing.wins
                season_draws = existing.draws
                season_losses = existing.losses
                season_goals_for = existing.goals_for
                season_goals_against = existing.goals_against
            if points is None and existing:
                points = existing.points
            if goal_difference is None and existing:
                goal_difference = existing.goal_difference
            if goal_difference is None and season_played > 0:
                goal_difference = season_goals_for - season_goals_against

            home_record = existing.home_record if existing else VenueRecord()
            away_record = existing.away_record if existing else VenueRecord()

            if side == "home":
                home_record = _row_venue_record(row, "home")
            else:
                away_record = _row_venue_record(row, "away")

            team_stats[team_name] = TeamRecentStats(
                average_scored=float(scored),
                average_conceded=float(conceded),
                recent_matches=recent_matches or (
                    existing.recent_matches if existing else ()
                ),
                recent_results=recent_results or (
                    existing.recent_results if existing else ()
                ),
                rank=rank if rank is not None else (existing.rank if existing else None),
                points=points,
                played=season_played,
                wins=season_wins,
                draws=season_draws,
                losses=season_losses,
                goals_for=season_goals_for,
                goals_against=season_goals_against,
                goal_difference=goal_difference,
                standings_available=bool(
                    season_played > 0
                    and points is not None
                    and goal_difference is not None
                ),
                home_record=home_record,
                away_record=away_record,
            )

    return team_stats


def _load_single_source(source: MatchDataSource) -> MatchDataLoadResult:
    """1取得元を読み込み、例外を画面用の安全な結果へ変換する。"""

    try:
        bundle_loader = getattr(source, "load_bundle", None)

        if callable(bundle_loader):
            bundle = bundle_loader()
            raw_matches = bundle.matches
            completed_matches = tuple(bundle.completed_matches)
            data_start_date = bundle.data_start_date
            data_end_date = bundle.data_end_date
            official_cache_used = bool(bundle.from_cache)
            bundle_team_stats = dict(getattr(bundle, "team_stats", {}) or {})
        else:
            raw_matches = source.load()
            completed_matches = ()
            data_start_date = None
            data_end_date = None
            official_cache_used = False
            bundle_team_stats = {}

        team_stats = bundle_team_stats or extract_team_stats(raw_matches)
        matches = normalize_matches(raw_matches)
    except MatchDataNotFoundError:
        return MatchDataLoadResult(
            matches=create_empty_matches(),
            source_name=source.name,
            status="missing",
            message="利用できるデータがありません。",
        )
    except MatchDataSourceError:
        return MatchDataLoadResult(
            matches=create_empty_matches(),
            source_name=source.name,
            status="error",
            message="データを取得できませんでした。",
        )
    except Exception:
        # 取得元の予期しない仕様変更もStreamlitのエラー画面へ出さない。
        # 次のCSV取得元、または手入力へ安全に切り替える。
        return MatchDataLoadResult(
            matches=create_empty_matches(),
            source_name=source.name,
            status="error",
            message="データを取得できませんでした。",
        )

    if matches.empty and not completed_matches:
        return MatchDataLoadResult(
            matches=matches,
            source_name=source.name,
            status="empty",
            message="利用できる試合がありません。",
            team_stats=team_stats,
        )

    if matches.empty:
        return MatchDataLoadResult(
            matches=matches,
            source_name=source.name,
            status="loaded",
            message=(
                f"{source.name}の試合履歴を読み込みました。"
                "対戦カードは手入力できます。"
            ),
            team_stats=team_stats,
            completed_matches=completed_matches,
            data_start_date=data_start_date,
            data_end_date=data_end_date,
            official_cache_used=official_cache_used,
        )

    return MatchDataLoadResult(
        matches=matches,
        source_name=source.name,
        status="loaded",
        message=f"{source.name}から{len(matches)}試合を読み込みました。",
        team_stats=team_stats,
        completed_matches=completed_matches,
        data_start_date=data_start_date,
        data_end_date=data_end_date,
        official_cache_used=official_cache_used,
    )


def load_matches(
    data_source: Optional[MatchDataSource] = None,
) -> MatchDataLoadResult:
    """公式→CSV→手入力の順で、画面に例外を出さず読み込む。"""

    if data_source is not None:
        return _load_single_source(data_source)

    for source in get_default_data_sources():
        result = _load_single_source(source)

        if result.is_loaded:
            # 前の取得元の技術エラーは画面へ出さない。
            return result

    return MatchDataLoadResult(
        matches=create_empty_matches(),
        source_name="手入力",
        status="manual",
        message="手入力モードで起動しました。",
    )


def get_match_defaults(matches: pd.DataFrame, match_number: int) -> dict:
    """指定試合の初期値を返す。なければVersion 1と同じ値。"""

    defaults = {
        "match_number": match_number,
        **DEFAULT_MATCH_METADATA,
        **DEFAULT_MATCH_VALUES,
        **DEFAULT_MATCH_DETAILS,
    }

    if matches.empty:
        return defaults

    selected_match = matches.loc[matches["match_number"] == match_number]

    if selected_match.empty:
        return defaults

    match_values = selected_match.iloc[0]

    for column in (
        *DEFAULT_MATCH_METADATA,
        *DEFAULT_MATCH_VALUES,
        *DEFAULT_MATCH_DETAILS,
    ):
        defaults[column] = match_values[column]

    return defaults
