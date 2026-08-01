"""試合データの読み込みを担当するモジュール。

app.py はデータの保存場所を意識せず、このモジュールだけを呼び出す。
現在は CSV を使用するが、将来は同じ ``MatchDataSource`` の形で
Jリーグ公式データや無料APIの読み込みクラスへ差し替えられる。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import pandas as pd


# --------------------------------------------------
# CSVの場所と、アプリ内で共通利用する列
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MATCHES_PATH = PROJECT_ROOT / "data" / "matches.csv"

MATCH_COLUMNS = [
    "match_number",
    "home_team",
    "away_team",
    "home_scored",
    "home_conceded",
    "away_scored",
    "away_conceded",
]

# CSVに平均値の列がない場合は、Version 1と同じ初期値を使う。
DEFAULT_MATCH_VALUES = {
    "home_team": "",
    "away_team": "",
    "home_scored": 1.4,
    "home_conceded": 1.2,
    "away_scored": 1.2,
    "away_conceded": 1.4,
}


# --------------------------------------------------
# データ取得元の共通インターフェース
# --------------------------------------------------

class MatchDataSource(Protocol):
    """CSVやAPIなど、試合データ取得元が備える共通の形。"""

    @property
    def name(self) -> str:
        """画面表示用のデータ取得元名を返す。"""

    def load(self) -> pd.DataFrame:
        """試合データをDataFrameとして返す。"""


class MatchDataSourceError(RuntimeError):
    """データ取得元の読み込みに失敗した場合の共通エラー。"""


class MatchDataNotFoundError(MatchDataSourceError):
    """データファイルなどが存在しない場合のエラー。"""


class MatchDataFormatError(MatchDataSourceError):
    """データの形式が想定と異なる場合のエラー。"""


@dataclass(frozen=True)
class CsvMatchDataSource:
    """data/matches.csv から試合データを読み込む。"""

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
            return pd.read_csv(
                self.path,
                encoding="utf-8-sig",
            )
        except pd.errors.EmptyDataError as error:
            raise MatchDataFormatError(
                "matches.csv が空です。"
            ) from error
        except (
            OSError,
            UnicodeError,
            pd.errors.ParserError,
        ) as error:
            raise MatchDataSourceError(
                f"matches.csv を読み込めませんでした：{error}"
            ) from error


@dataclass(frozen=True)
class MatchDataLoadResult:
    """読み込み結果と画面表示用メッセージをまとめる。"""

    matches: pd.DataFrame
    source_name: str
    status: str
    message: str

    @property
    def is_loaded(self) -> bool:
        return self.status == "loaded"


def get_default_data_source() -> MatchDataSource:
    """現在利用する取得元を返す。将来のAPI切り替えはここで行う。"""

    return CsvMatchDataSource()


# --------------------------------------------------
# 読み込んだデータをアプリ共通形式へ変換する
# --------------------------------------------------

def create_empty_matches() -> pd.DataFrame:
    """CSVがない場合に使う、列だけを持った空データを返す。"""

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
        raise MatchDataFormatError(
            f"必須列がありません：{missing_text}"
        )

    matches = raw_matches.copy()

    # match_number がなければ、CSVの上から1～13試合として扱う。
    if "match_number" not in matches.columns:
        matches.insert(
            0,
            "match_number",
            range(1, len(matches) + 1),
        )

    match_numbers = pd.to_numeric(
        matches["match_number"],
        errors="coerce",
    )

    # totoは13試合なので、1～13の整数だけを利用する。
    valid_numbers = (
        match_numbers.notna()
        & (match_numbers % 1 == 0)
        & match_numbers.between(1, 13)
    )
    matches = matches.loc[valid_numbers].copy()
    matches["match_number"] = (
        match_numbers.loc[valid_numbers].astype(int)
    )

    for team_column in ("home_team", "away_team"):
        matches[team_column] = (
            matches[team_column]
            .fillna("")
            .astype(str)
            .str.strip()
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

    # 同じ試合番号が複数ある場合は、CSVで先に書かれた行を使う。
    return (
        matches[MATCH_COLUMNS]
        .drop_duplicates(subset="match_number", keep="first")
        .sort_values("match_number")
        .reset_index(drop=True)
    )


def load_matches(
    data_source: Optional[MatchDataSource] = None,
) -> MatchDataLoadResult:
    """試合データを安全に読み込み、CSVがなくても空データを返す。

    将来APIへ移行するときは、``MatchDataSource`` と同じ ``name`` と
    ``load()`` を持つクラスを作り、この関数へ渡す。app.pyの変更は不要。
    """

    source = data_source or get_default_data_source()

    try:
        matches = normalize_matches(source.load())
    except MatchDataNotFoundError:
        return MatchDataLoadResult(
            matches=create_empty_matches(),
            source_name=source.name,
            status="missing",
            message=(
                f"{source.name}が見つからないため、"
                "手入力モードで起動しました。"
            ),
        )
    except MatchDataSourceError as error:
        return MatchDataLoadResult(
            matches=create_empty_matches(),
            source_name=source.name,
            status="error",
            message=(
                f"{source.name}を利用できないため、"
                f"手入力モードで起動しました：{error}"
            ),
        )

    if matches.empty:
        return MatchDataLoadResult(
            matches=matches,
            source_name=source.name,
            status="empty",
            message=(
                f"{source.name}に利用できる試合がないため、"
                "手入力モードで起動しました。"
            ),
        )

    return MatchDataLoadResult(
        matches=matches,
        source_name=source.name,
        status="loaded",
        message=(
            f"{source.name}から{len(matches)}試合を読み込みました。"
        ),
    )


def get_match_defaults(
    matches: pd.DataFrame,
    match_number: int,
) -> dict:
    """指定試合の初期入力値を返す。データがなければVersion 1と同じ値。"""

    defaults = {
        "match_number": match_number,
        **DEFAULT_MATCH_VALUES,
    }

    if matches.empty:
        return defaults

    selected_match = matches.loc[
        matches["match_number"] == match_number
    ]

    if selected_match.empty:
        return defaults

    match_values = selected_match.iloc[0]

    for column in DEFAULT_MATCH_VALUES:
        defaults[column] = match_values[column]

    return defaults
