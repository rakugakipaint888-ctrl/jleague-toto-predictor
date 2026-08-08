"""toto開催回・公式試合順・実結果の取得とCSVフォールバック。

スポーツくじ公式サイト固有のHTML解析はこのモジュールへ閉じ込める。
画面は ``TotoHistoryManager`` の結果だけを使い、取得失敗時も停止しない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from lxml import html as lxml_html

from data_loader import (
    ALL_TEAM_NAME_SET,
    DEFAULT_MATCH_DETAILS,
    DEFAULT_MATCH_METADATA,
    DEFAULT_MATCH_VALUES,
    MATCH_COLUMNS,
    OFFICIAL_TEAM_ABBREVIATIONS,
    TeamRecentStats,
    VenueRecord,
)
from teams import normalize_team_name


JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")
PROJECT_ROOT = Path(__file__).resolve().parent

TOTO_STORE_BASE_URL = "https://store.toto-dream.com"
TOTO_CURRENT_ROUND_URL = (
    f"{TOTO_STORE_BASE_URL}/dcs/subos/screen/pi01/spin000/"
    "PGSPIN00001DisptotoLotInfo.form"
)
TOTO_ROUND_INFO_URL = TOTO_CURRENT_ROUND_URL + "?holdCntId={round_id}"
TOTO_RESULT_LIST_URL = (
    f"{TOTO_STORE_BASE_URL}/dcs/subos/screen/pi04/spin011/"
    "PGSPIN01101InitLotResultLsttoto.form"
)
TOTO_YEAR_RESULT_LIST_URL = (
    f"{TOTO_STORE_BASE_URL}/dcs/subos/screen/pi04/spin011/"
    "PGSPIN01101LnkSeasonLotResultLsttoto.form?meetingFiscalYear={year}"
)
TOTO_ROUND_RESULT_URL = (
    f"{TOTO_STORE_BASE_URL}/dcs/subos/screen/pi04/spin011/"
    "PGSPIN01101LnkHoldCntLotResultLsttoto.form?holdCntId={round_id}"
)

DEFAULT_TOTO_ROUNDS_CSV_PATH = (
    PROJECT_ROOT / "data" / "cache" / "toto_rounds.csv"
)
TOTO_ROUND_CACHE_VERSION = 1

TOTO_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; JLeagueTotoPersonalApp/6.0; personal-use)"
    ),
    "Accept-Language": "ja,en;q=0.5",
}

ROUND_CSV_COLUMNS = (
    "round_id",
    "match_number",
    "home_team",
    "away_team",
    "match_time",
    "stadium",
    "actual_result",
    "home_goals",
    "away_goals",
    "sale_start",
    "sale_end",
    "result_date",
    "first_prize_yen",
    "second_prize_yen",
    "third_prize_yen",
    "source_url",
    "fetched_at",
)


class TotoDataError(RuntimeError):
    """toto公式情報を取得・解析できない場合の共通例外。"""


class TotoDataNotFoundError(TotoDataError):
    """指定回または13試合の情報が存在しない。"""


class TotoDataFormatError(TotoDataError):
    """公式HTMLまたは保存CSVの形式が想定と異なる。"""


@dataclass(frozen=True)
class TotoPayouts:
    """totoシングルの1～3等公式当せん金。"""

    first_prize_yen: int = 0
    second_prize_yen: int = 0
    third_prize_yen: int = 0


@dataclass(frozen=True)
class TotoMatch:
    """toto公式の試合番号を保持した1試合。"""

    round_id: int
    match_number: int
    home_team: str
    away_team: str
    match_time: datetime
    stadium: str = ""
    actual_result: Optional[str] = None
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None

    @property
    def is_jleague_match(self) -> bool:
        return (
            self.home_team in ALL_TEAM_NAME_SET
            and self.away_team in ALL_TEAM_NAME_SET
        )


@dataclass(frozen=True)
class TotoRound:
    """開催回をキーにしたtoto 13試合と結果・配当。"""

    round_id: int
    matches: tuple[TotoMatch, ...]
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None
    result_date: Optional[date] = None
    payouts: TotoPayouts = field(default_factory=TotoPayouts)
    source_url: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(
            len(self.matches) == 13
            and all(
                match.actual_result in ("1", "0", "2")
                for match in self.matches
            )
        )

    @property
    def is_official_order_complete(self) -> bool:
        return [match.match_number for match in self.matches] == list(
            range(1, 14)
        )

    @property
    def is_jleague_round(self) -> bool:
        return bool(
            self.is_official_order_complete
            and all(match.is_jleague_match for match in self.matches)
        )

    @property
    def start_time(self) -> Optional[datetime]:
        return min(
            (match.match_time for match in self.matches),
            default=None,
        )


@dataclass(frozen=True)
class TotoRoundSummary:
    """年度別結果一覧に掲載された開催回。"""

    round_id: int
    fiscal_year: int
    label: str


@dataclass(frozen=True)
class TotoRoundLoadResult:
    """公式→保存CSV→現在データの取得結果。"""

    toto_round: Optional[TotoRound]
    source_name: str
    status: str
    message: str

    @property
    def is_loaded(self) -> bool:
        return self.toto_round is not None and self.status == "loaded"


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return " ".join(str(value).replace("\u3000", " ").split())


def _page_text(html_text: str) -> str:
    try:
        return " ".join(
            lxml_html.fromstring(html_text).text_content().split()
        )
    except (TypeError, ValueError) as error:
        raise TotoDataFormatError("toto公式ページを解析できません。") from error


def _read_html_tables(html_text: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(StringIO(html_text))
    except (ImportError, ValueError) as error:
        raise TotoDataFormatError(
            "toto公式ページに利用できる表がありません。"
        ) from error


def _parse_round_id(page_text: str, expected_round_id: Optional[int]) -> int:
    round_match = re.search(
        r"第\s*(\d+)\s*回\s*toto(?:\s|　)*(?:くじ情報|くじ結果)",
        page_text,
        flags=re.IGNORECASE,
    )
    if not round_match:
        raise TotoDataFormatError("toto開催回を確認できません。")

    round_id = int(round_match.group(1))
    if expected_round_id is not None and round_id != int(expected_round_id):
        raise TotoDataNotFoundError("指定したtoto開催回が見つかりません。")
    return round_id


def _parse_japanese_datetime(value: Any) -> Optional[datetime]:
    text_value = _normalize_text(value)
    match = re.search(
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日"
        r"(?:[^0-9]*(\d{1,2})[：:](\d{2}))?",
        text_value,
    )
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            tzinfo=JAPAN_TIMEZONE,
        )
    except ValueError:
        return None


def _metadata_from_info_tables(
    tables: Sequence[pd.DataFrame],
) -> tuple[Optional[datetime], Optional[datetime], Optional[date]]:
    for table in tables:
        if table.shape[1] != 2:
            continue
        labels = {_normalize_text(value) for value in table.iloc[:, 0]}
        if not {"販売開始日", "販売終了日", "結果発表日"}.issubset(labels):
            continue
        values = {
            _normalize_text(row.iloc[0]): row.iloc[1]
            for _, row in table.iterrows()
        }
        sale_start = _parse_japanese_datetime(values.get("販売開始日"))
        sale_end = _parse_japanese_datetime(values.get("販売終了日"))
        result_datetime = _parse_japanese_datetime(values.get("結果発表日"))
        return (
            sale_start,
            sale_end,
            result_datetime.date() if result_datetime else None,
        )
    return None, None, None


def _metadata_from_result_tables(
    tables: Sequence[pd.DataFrame],
) -> tuple[Optional[datetime], Optional[datetime], Optional[date]]:
    for table in tables:
        normalized_columns = [_normalize_text(column) for column in table.columns]
        if not {
            "販売開始日",
            "販売終了日",
            "結果発表日",
        }.issubset(normalized_columns):
            continue
        row = table.iloc[0]
        values = {
            _normalize_text(column): row[column]
            for column in table.columns
        }
        sale_start = _parse_japanese_datetime(values.get("販売開始日"))
        sale_end = _parse_japanese_datetime(values.get("販売終了日"))
        result_datetime = _parse_japanese_datetime(values.get("結果発表日"))
        return (
            sale_start,
            sale_end,
            result_datetime.date() if result_datetime else None,
        )
    return None, None, None


def _match_datetime(
    month_day_value: Any,
    kickoff_value: Any,
    reference_date: date,
) -> datetime:
    month_day_match = re.search(
        r"(\d{1,2})\s*/\s*(\d{1,2})",
        _normalize_text(month_day_value),
    )
    if not month_day_match:
        raise TotoDataFormatError("toto対象試合の日付を解析できません。")
    month, day = (int(value) for value in month_day_match.groups())

    kickoff_match = re.search(
        r"(\d{1,2})\s*[:：]\s*(\d{2})",
        _normalize_text(kickoff_value),
    )
    hour, minute = (
        (int(kickoff_match.group(1)), int(kickoff_match.group(2)))
        if kickoff_match
        else (0, 0)
    )
    candidates = []
    for year in (reference_date.year - 1, reference_date.year, reference_date.year + 1):
        try:
            candidate = datetime(
                year,
                month,
                day,
                hour,
                minute,
                tzinfo=JAPAN_TIMEZONE,
            )
        except ValueError:
            continue
        distance = abs((candidate.date() - reference_date).days)
        candidates.append((distance, candidate))

    if not candidates:
        raise TotoDataFormatError("toto対象試合の日付が不正です。")
    return min(candidates, key=lambda item: item[0])[1]


def _canonical_toto_team(value: Any) -> str:
    return normalize_team_name(value, OFFICIAL_TEAM_ABBREVIATIONS)


def _find_info_match_table(tables: Sequence[pd.DataFrame]) -> pd.DataFrame:
    for table in tables:
        if len(table) != 13 or table.shape[1] < 7:
            continue
        headers = " ".join(_normalize_text(column) for column in table.columns)
        if "指定試合" in headers and "開催日" in headers:
            return table
    raise TotoDataNotFoundError("toto公式の13試合を確認できません。")


def _find_result_match_table(tables: Sequence[pd.DataFrame]) -> pd.DataFrame:
    for table in tables:
        if len(table) != 13 or table.shape[1] < 7:
            continue
        headers = " ".join(_normalize_text(column) for column in table.columns)
        if "くじ結果" in headers and "試合結果" in headers:
            return table
    raise TotoDataNotFoundError("toto公式の13試合結果を確認できません。")


def parse_toto_info_page(
    html_text: str,
    *,
    expected_round_id: Optional[int] = None,
    source_url: str = "",
) -> TotoRound:
    """販売中のtoto公式ページから開催回と第1～13試合を返す。"""

    page_text = _page_text(html_text)
    round_id = _parse_round_id(page_text, expected_round_id)
    tables = _read_html_tables(html_text)
    sale_start, sale_end, result_date = _metadata_from_info_tables(tables)
    reference_date = (
        result_date
        or (sale_start.date() if sale_start else datetime.now(JAPAN_TIMEZONE).date())
    )
    table = _find_info_match_table(tables)
    matches = []

    for _, row in table.iterrows():
        try:
            match_number = int(pd.to_numeric(row.iloc[0]))
        except (TypeError, ValueError):
            continue
        matches.append(
            TotoMatch(
                round_id=round_id,
                match_number=match_number,
                match_time=_match_datetime(
                    row.iloc[1],
                    row.iloc[2],
                    reference_date,
                ),
                stadium=_normalize_text(row.iloc[3]),
                home_team=_canonical_toto_team(row.iloc[4]),
                away_team=_canonical_toto_team(row.iloc[6]),
            )
        )

    matches = sorted(matches, key=lambda match: match.match_number)
    toto_round = TotoRound(
        round_id=round_id,
        matches=tuple(matches),
        sale_start=sale_start,
        sale_end=sale_end,
        result_date=result_date,
        source_url=source_url,
    )
    if not toto_round.is_official_order_complete:
        raise TotoDataFormatError("toto公式試合番号が1～13で連続していません。")
    return toto_round


def _parse_score(value: Any) -> tuple[Optional[int], Optional[int]]:
    score_match = re.search(
        r"(\d+)\s*[-−]\s*(\d+)",
        _normalize_text(value),
    )
    if not score_match:
        return None, None
    return int(score_match.group(1)), int(score_match.group(2))


def _parse_money(value: Any) -> int:
    digits = re.sub(r"[^0-9]", "", _normalize_text(value))
    return int(digits) if digits else 0


def _payouts_from_result_tables(
    tables: Sequence[pd.DataFrame],
) -> TotoPayouts:
    for table in tables:
        normalized_columns = [_normalize_text(column) for column in table.columns]
        if not {"1等", "2等", "3等"}.issubset(normalized_columns):
            continue
        first_column = table.columns[0]
        payout_rows = table.loc[
            table[first_column].map(_normalize_text) == "当せん金"
        ]
        if payout_rows.empty:
            continue
        row = payout_rows.iloc[0]
        values = {
            _normalize_text(column): row[column]
            for column in table.columns
        }
        return TotoPayouts(
            first_prize_yen=_parse_money(values.get("1等")),
            second_prize_yen=_parse_money(values.get("2等")),
            third_prize_yen=_parse_money(values.get("3等")),
        )
    return TotoPayouts()


def parse_toto_result_page(
    html_text: str,
    *,
    expected_round_id: Optional[int] = None,
    source_url: str = "",
) -> TotoRound:
    """過去のtoto公式結果ページから13試合・実結果・配当を返す。"""

    page_text = _page_text(html_text)
    round_id = _parse_round_id(page_text, expected_round_id)
    tables = _read_html_tables(html_text)
    sale_start, sale_end, result_date = _metadata_from_result_tables(tables)
    reference_date = (
        result_date
        or (sale_start.date() if sale_start else datetime.now(JAPAN_TIMEZONE).date())
    )
    table = _find_result_match_table(tables)
    matches = []

    for _, row in table.iterrows():
        try:
            match_number = int(pd.to_numeric(row.iloc[2]))
        except (TypeError, ValueError):
            continue
        actual_result = _normalize_text(row.iloc[6])
        home_goals, away_goals = _parse_score(row.iloc[4])
        matches.append(
            TotoMatch(
                round_id=round_id,
                match_number=match_number,
                match_time=_match_datetime(
                    row.iloc[0],
                    "00:00",
                    reference_date,
                ),
                stadium=_normalize_text(row.iloc[1]),
                home_team=_canonical_toto_team(row.iloc[3]),
                away_team=_canonical_toto_team(row.iloc[5]),
                actual_result=(
                    actual_result
                    if actual_result in ("1", "0", "2")
                    else None
                ),
                home_goals=home_goals,
                away_goals=away_goals,
            )
        )

    matches = sorted(matches, key=lambda match: match.match_number)
    toto_round = TotoRound(
        round_id=round_id,
        matches=tuple(matches),
        sale_start=sale_start,
        sale_end=sale_end,
        result_date=result_date,
        payouts=_payouts_from_result_tables(tables),
        source_url=source_url,
    )
    if not toto_round.is_official_order_complete:
        raise TotoDataFormatError("toto公式試合番号が1～13で連続していません。")
    return toto_round


def parse_round_catalog(
    html_text: str,
    fiscal_year: int,
) -> tuple[TotoRoundSummary, ...]:
    """年度別結果一覧から開催回キーを抽出する。"""

    try:
        document = lxml_html.fromstring(html_text)
    except (TypeError, ValueError) as error:
        raise TotoDataFormatError("toto結果一覧を解析できません。") from error

    summaries: dict[int, TotoRoundSummary] = {}
    for anchor in document.xpath('//a[contains(@href, "holdCntId=")]'):
        href = anchor.get("href", "")
        round_match = re.search(r"holdCntId=(\d+)", href)
        label = _normalize_text(anchor.text_content())
        if not round_match or not label.startswith("第"):
            continue
        round_id = int(round_match.group(1))
        summaries[round_id] = TotoRoundSummary(
            round_id=round_id,
            fiscal_year=int(fiscal_year),
            label=label,
        )

    return tuple(
        sorted(summaries.values(), key=lambda item: item.round_id, reverse=True)
    )


@dataclass(frozen=True)
class TotoOfficialDataSource:
    """スポーツくじ公式サイトの公開HTMLを低頻度で取得する。"""

    timeout_seconds: float = 20.0
    now: Optional[datetime] = None
    request_get: Callable[..., Any] = requests.get

    def _reference_time(self) -> datetime:
        reference = self.now or datetime.now(JAPAN_TIMEZONE)
        if reference.tzinfo is None:
            return reference.replace(tzinfo=JAPAN_TIMEZONE)
        return reference.astimezone(JAPAN_TIMEZONE)

    def _request_html(self, url: str) -> str:
        try:
            response = self.request_get(
                url,
                headers=TOTO_REQUEST_HEADERS,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise TotoDataError(
                "スポーツくじ公式サイトへ接続できませんでした。"
            ) from error
        except Exception as error:
            raise TotoDataError(
                "スポーツくじ公式サイトへ接続できませんでした。"
            ) from error
        if not str(response.text).strip():
            raise TotoDataFormatError("toto公式ページが空です。")
        return str(response.text)

    def load_current_round(self) -> TotoRound:
        html_text = self._request_html(TOTO_CURRENT_ROUND_URL)
        return parse_toto_info_page(
            html_text,
            source_url=TOTO_CURRENT_ROUND_URL,
        )

    def load_round(self, round_id: int) -> TotoRound:
        result_url = TOTO_ROUND_RESULT_URL.format(round_id=int(round_id))
        try:
            html_text = self._request_html(result_url)
            return parse_toto_result_page(
                html_text,
                expected_round_id=int(round_id),
                source_url=result_url,
            )
        except TotoDataError:
            info_url = TOTO_ROUND_INFO_URL.format(round_id=int(round_id))
            html_text = self._request_html(info_url)
            return parse_toto_info_page(
                html_text,
                expected_round_id=int(round_id),
                source_url=info_url,
            )

    def load_catalog(
        self,
        years: Optional[Sequence[int]] = None,
    ) -> tuple[TotoRoundSummary, ...]:
        """既定では現在年と前年、最低直近1年分の開催回を返す。"""

        reference_year = self._reference_time().year
        selected_years = tuple(years or (reference_year, reference_year - 1))
        summaries: dict[int, TotoRoundSummary] = {}

        for fiscal_year in selected_years:
            url = (
                TOTO_RESULT_LIST_URL
                if fiscal_year == reference_year
                else TOTO_YEAR_RESULT_LIST_URL.format(year=fiscal_year)
            )
            html_text = self._request_html(url)
            for summary in parse_round_catalog(html_text, fiscal_year):
                summaries[summary.round_id] = summary

        return tuple(
            sorted(
                summaries.values(),
                key=lambda item: item.round_id,
                reverse=True,
            )
        )


def _round_to_rows(toto_round: TotoRound) -> list[dict[str, Any]]:
    fetched_at = datetime.now(JAPAN_TIMEZONE).isoformat()
    rows = []
    for match in toto_round.matches:
        rows.append(
            {
                "round_id": toto_round.round_id,
                "match_number": match.match_number,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "match_time": match.match_time.isoformat(),
                "stadium": match.stadium,
                "actual_result": match.actual_result or "",
                "home_goals": match.home_goals,
                "away_goals": match.away_goals,
                "sale_start": (
                    toto_round.sale_start.isoformat()
                    if toto_round.sale_start
                    else ""
                ),
                "sale_end": (
                    toto_round.sale_end.isoformat()
                    if toto_round.sale_end
                    else ""
                ),
                "result_date": (
                    toto_round.result_date.isoformat()
                    if toto_round.result_date
                    else ""
                ),
                "first_prize_yen": toto_round.payouts.first_prize_yen,
                "second_prize_yen": toto_round.payouts.second_prize_yen,
                "third_prize_yen": toto_round.payouts.third_prize_yen,
                "source_url": toto_round.source_url,
                "fetched_at": fetched_at,
            }
        )
    return rows


def _optional_int(value: Any) -> Optional[int]:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return int(number)


def _saved_toto_result(value: Any) -> Optional[str]:
    """CSVで1.0/0.0/2.0へ数値化された実結果も正規ラベルへ戻す。"""

    if isinstance(value, bool):
        return None
    text_value = _normalize_text(value)
    if text_value in ("1", "0", "2"):
        return text_value
    number = pd.to_numeric(value, errors="coerce")
    if not pd.isna(number) and float(number).is_integer():
        normalized = str(int(number))
        return normalized if normalized in ("1", "0", "2") else None
    return None


def _optional_datetime(value: Any) -> Optional[datetime]:
    if not _normalize_text(value):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JAPAN_TIMEZONE)
    return parsed.astimezone(JAPAN_TIMEZONE)


def _round_from_saved_rows(rows: pd.DataFrame) -> TotoRound:
    if rows.empty:
        raise TotoDataNotFoundError("保存済みtoto開催回がありません。")
    round_id = int(pd.to_numeric(rows.iloc[0]["round_id"]))
    matches = []
    for _, row in rows.sort_values("match_number").iterrows():
        match_time_value = _optional_datetime(row.get("match_time"))
        if match_time_value is None:
            continue
        actual = _saved_toto_result(row.get("actual_result"))
        matches.append(
            TotoMatch(
                round_id=round_id,
                match_number=int(pd.to_numeric(row.get("match_number"))),
                home_team=_canonical_toto_team(row.get("home_team")),
                away_team=_canonical_toto_team(row.get("away_team")),
                match_time=match_time_value,
                stadium=_normalize_text(row.get("stadium")),
                actual_result=actual,
                home_goals=_optional_int(row.get("home_goals")),
                away_goals=_optional_int(row.get("away_goals")),
            )
        )
    result_date_value = _normalize_text(rows.iloc[0].get("result_date"))
    try:
        parsed_result_date = (
            date.fromisoformat(result_date_value)
            if result_date_value
            else None
        )
    except ValueError:
        parsed_result_date = None
    return TotoRound(
        round_id=round_id,
        matches=tuple(matches),
        sale_start=_optional_datetime(rows.iloc[0].get("sale_start")),
        sale_end=_optional_datetime(rows.iloc[0].get("sale_end")),
        result_date=parsed_result_date,
        payouts=TotoPayouts(
            first_prize_yen=_optional_int(
                rows.iloc[0].get("first_prize_yen")
            ) or 0,
            second_prize_yen=_optional_int(
                rows.iloc[0].get("second_prize_yen")
            ) or 0,
            third_prize_yen=_optional_int(
                rows.iloc[0].get("third_prize_yen")
            ) or 0,
        ),
        source_url=_normalize_text(rows.iloc[0].get("source_url")),
    )


@dataclass
class TotoHistoryManager:
    """開催回CSVを更新し、公式取得失敗時の順序を管理する。"""

    official_source: TotoOfficialDataSource = field(
        default_factory=TotoOfficialDataSource
    )
    csv_path: Path = DEFAULT_TOTO_ROUNDS_CSV_PATH

    def _read_saved_frame(self) -> pd.DataFrame:
        if not self.csv_path.exists():
            return pd.DataFrame(columns=ROUND_CSV_COLUMNS)
        try:
            frame = pd.read_csv(self.csv_path, encoding="utf-8-sig")
        except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
            return pd.DataFrame(columns=ROUND_CSV_COLUMNS)
        if not {"round_id", "match_number", "home_team", "away_team"}.issubset(
            frame.columns
        ):
            return pd.DataFrame(columns=ROUND_CSV_COLUMNS)
        return frame

    def save_round(self, toto_round: TotoRound) -> bool:
        """開催回単位で13行を置換し、UTF-8 BOM付きCSVへ保存する。"""

        try:
            existing = self._read_saved_frame()
            if not existing.empty:
                existing_round_ids = pd.to_numeric(
                    existing["round_id"], errors="coerce"
                )
                existing = existing.loc[
                    existing_round_ids != toto_round.round_id
                ]
            combined = pd.concat(
                [existing, pd.DataFrame(_round_to_rows(toto_round))],
                ignore_index=True,
            )
            for column in ROUND_CSV_COLUMNS:
                if column not in combined.columns:
                    combined[column] = ""
            combined = combined[list(ROUND_CSV_COLUMNS)].sort_values(
                ["round_id", "match_number"],
                ascending=[False, True],
            )
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.csv_path.with_suffix(".tmp")
            combined.to_csv(
                temporary_path,
                index=False,
                encoding="utf-8-sig",
            )
            temporary_path.replace(self.csv_path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def load_saved_round(self, round_id: int) -> Optional[TotoRound]:
        frame = self._read_saved_frame()
        if frame.empty:
            return None
        numeric_round_ids = pd.to_numeric(frame["round_id"], errors="coerce")
        selected = frame.loc[numeric_round_ids == int(round_id)]
        try:
            toto_round = _round_from_saved_rows(selected)
        except (TotoDataError, KeyError, TypeError, ValueError):
            return None
        return toto_round if toto_round.is_official_order_complete else None

    def _load_saved_current(self) -> Optional[TotoRound]:
        frame = self._read_saved_frame()
        if frame.empty:
            return None
        round_ids = sorted(
            {
                int(value)
                for value in pd.to_numeric(frame["round_id"], errors="coerce")
                if not pd.isna(value) and int(value) > 0
            },
            reverse=True,
        )
        reference_date = self.official_source._reference_time().date()
        for round_id in round_ids:
            toto_round = self.load_saved_round(round_id)
            if toto_round is None:
                continue
            if (
                not toto_round.is_complete
                or toto_round.result_date is None
                or toto_round.result_date >= reference_date
            ):
                return toto_round
        return None

    def load_current_round(
        self,
        current_matches: Optional[pd.DataFrame] = None,
    ) -> TotoRoundLoadResult:
        """公式→保存CSV→現在データの順で最新回を返す。"""

        try:
            toto_round = self.official_source.load_current_round()
            self.save_round(toto_round)
            return TotoRoundLoadResult(
                toto_round=toto_round,
                source_name="toto公式",
                status="loaded",
                message=(
                    f"toto公式から第{toto_round.round_id}回の"
                    "第1～13試合を読み込みました。"
                ),
            )
        except TotoDataError:
            pass

        saved_round = self._load_saved_current()
        if saved_round is not None:
            return TotoRoundLoadResult(
                toto_round=saved_round,
                source_name="保存CSV",
                status="loaded",
                message=(
                    f"保存CSVから第{saved_round.round_id}回の"
                    "第1～13試合を読み込みました。"
                ),
            )

        fallback_round = create_round_from_current_matches(current_matches)
        if fallback_round is not None:
            return TotoRoundLoadResult(
                toto_round=fallback_round,
                source_name="現在データ",
                status="loaded",
                message=(
                    "toto公式順を取得できないため、現在の試合データ順で"
                    "起動しました。"
                ),
            )

        return TotoRoundLoadResult(
            toto_round=None,
            source_name="エラー",
            status="error",
            message=(
                "toto開催回を取得できませんでした。"
                "13試合は手入力できます。"
            ),
        )

    def load_round(self, round_id: int) -> TotoRoundLoadResult:
        try:
            toto_round = self.official_source.load_round(int(round_id))
            self.save_round(toto_round)
            return TotoRoundLoadResult(
                toto_round=toto_round,
                source_name="toto公式",
                status="loaded",
                message=f"toto公式から第{int(round_id)}回を読み込みました。",
            )
        except TotoDataError:
            saved_round = self.load_saved_round(int(round_id))
            if saved_round is not None:
                return TotoRoundLoadResult(
                    toto_round=saved_round,
                    source_name="保存CSV",
                    status="loaded",
                    message=f"保存CSVから第{int(round_id)}回を読み込みました。",
                )
        return TotoRoundLoadResult(
            toto_round=None,
            source_name="エラー",
            status="error",
            message=f"第{int(round_id)}回を取得できませんでした。",
        )

    def load_catalog(
        self,
        years: Optional[Sequence[int]] = None,
    ) -> tuple[TotoRoundSummary, ...]:
        try:
            return self.official_source.load_catalog(years)
        except TotoDataError:
            frame = self._read_saved_frame()
            if frame.empty:
                return ()
            summaries = []
            for round_id in sorted(
                {
                    int(value)
                    for value in pd.to_numeric(frame["round_id"], errors="coerce")
                    if not pd.isna(value) and int(value) > 0
                },
                reverse=True,
            ):
                selected = frame.loc[
                    pd.to_numeric(frame["round_id"], errors="coerce")
                    == round_id
                ]
                result_date_value = _normalize_text(
                    selected.iloc[0].get("result_date")
                )
                fiscal_year = (
                    int(result_date_value[:4])
                    if re.match(r"^20\d{2}", result_date_value)
                    else self.official_source._reference_time().year
                )
                summaries.append(
                    TotoRoundSummary(
                        round_id=round_id,
                        fiscal_year=fiscal_year,
                        label=f"第{round_id}回（保存CSV）",
                    )
                )
            return tuple(summaries)


def create_round_from_current_matches(
    current_matches: Optional[pd.DataFrame],
) -> Optional[TotoRound]:
    """Jリーグ現在データを最終フォールバックの13試合へ変換する。"""

    if not isinstance(current_matches, pd.DataFrame) or current_matches.empty:
        return None
    if not {"home_team", "away_team"}.issubset(current_matches.columns):
        return None
    matches = []
    ordered = current_matches.copy()
    if "match_number" in ordered.columns:
        ordered = ordered.sort_values("match_number")
    for fallback_number, (_, row) in enumerate(ordered.head(13).iterrows(), start=1):
        match_date_text = _normalize_text(row.get("match_date"))
        try:
            match_date = date.fromisoformat(match_date_text[:10])
        except ValueError:
            match_date = datetime.now(JAPAN_TIMEZONE).date()
        matches.append(
            TotoMatch(
                round_id=0,
                match_number=fallback_number,
                home_team=_canonical_toto_team(row.get("home_team")),
                away_team=_canonical_toto_team(row.get("away_team")),
                match_time=datetime.combine(
                    match_date,
                    time.min,
                    tzinfo=JAPAN_TIMEZONE,
                ),
            )
        )
    if len(matches) != 13:
        return None
    return TotoRound(
        round_id=0,
        matches=tuple(sorted(matches, key=lambda match: match.match_number)),
    )


def _record_columns(prefix: str, record: VenueRecord) -> dict[str, int]:
    return {
        f"{prefix}_played": record.played,
        f"{prefix}_wins": record.wins,
        f"{prefix}_draws": record.draws,
        f"{prefix}_losses": record.losses,
        f"{prefix}_goals_for": record.goals_for,
        f"{prefix}_goals_against": record.goals_against,
    }


def _standing_columns(
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


def create_matches_from_toto_round(
    toto_round: TotoRound,
    team_stats: Optional[dict[str, TeamRecentStats]] = None,
) -> pd.DataFrame:
    """toto公式順を保ったままVersion5入力DataFrameを作る。"""

    team_stats = team_stats or {}
    rows = []
    for toto_match in sorted(
        toto_round.matches,
        key=lambda match: match.match_number,
    ):
        home_stats = team_stats.get(toto_match.home_team)
        away_stats = team_stats.get(toto_match.away_team)
        home_record = home_stats.home_record if home_stats else VenueRecord()
        away_record = away_stats.away_record if away_stats else VenueRecord()
        rows.append(
            {
                "match_number": toto_match.match_number,
                "match_date": toto_match.match_time.strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "home_team": toto_match.home_team,
                "away_team": toto_match.away_team,
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
                "home_recent_matches": (
                    " / ".join(home_stats.recent_matches)
                    if home_stats
                    else DEFAULT_MATCH_METADATA["home_recent_matches"]
                ),
                "away_recent_matches": (
                    " / ".join(away_stats.recent_matches)
                    if away_stats
                    else DEFAULT_MATCH_METADATA["away_recent_matches"]
                ),
                "home_rank": home_stats.rank if home_stats else None,
                "away_rank": away_stats.rank if away_stats else None,
                **_standing_columns("home", home_stats),
                **_standing_columns("away", away_stats),
                **_record_columns("home", home_record),
                **_record_columns("away", away_record),
                "toto_round": toto_round.round_id or None,
                "toto_match_number": toto_match.match_number,
                "actual_result": toto_match.actual_result or "",
            }
        )
    columns = [
        *MATCH_COLUMNS,
        "toto_round",
        "toto_match_number",
        "actual_result",
    ]
    frame = pd.DataFrame(rows)
    for column in columns:
        if column not in frame.columns:
            frame[column] = DEFAULT_MATCH_DETAILS.get(column, "")
    return frame[columns].sort_values("toto_match_number").reset_index(drop=True)
