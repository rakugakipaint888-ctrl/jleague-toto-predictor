"""Jリーグのクラブ名・所属カテゴリー・表記正規化を管理する。

クラブの昇降格があった場合は、このファイルだけを更新する。
2026/27シーズンのJリーグ公式クラブ編成に基づく。
"""

from __future__ import annotations

import unicodedata
from typing import Any, Mapping, Optional


# --------------------------------------------------
# J1クラブ（20クラブ）
# --------------------------------------------------

J1 = [
    "鹿島アントラーズ",
    "水戸ホーリーホック",
    "浦和レッズ",
    "ジェフユナイテッド千葉",
    "柏レイソル",
    "ＦＣ東京",
    "東京ヴェルディ",
    "ＦＣ町田ゼルビア",
    "川崎フロンターレ",
    "横浜Ｆ・マリノス",
    "清水エスパルス",
    "名古屋グランパス",
    "京都サンガF.C.",
    "ガンバ大阪",
    "セレッソ大阪",
    "ヴィッセル神戸",
    "ファジアーノ岡山",
    "サンフレッチェ広島",
    "アビスパ福岡",
    "Ｖ・ファーレン長崎",
]


# --------------------------------------------------
# J2クラブ（20クラブ）
# --------------------------------------------------

J2 = [
    "北海道コンサドーレ札幌",
    "ヴァンラーレ八戸",
    "ベガルタ仙台",
    "ブラウブリッツ秋田",
    "モンテディオ山形",
    "いわきＦＣ",
    "栃木シティ",
    "ＲＢ大宮アルディージャ",
    "横浜ＦＣ",
    "湘南ベルマーレ",
    "ヴァンフォーレ甲府",
    "アルビレックス新潟",
    "カターレ富山",
    "ジュビロ磐田",
    "藤枝ＭＹＦＣ",
    "徳島ヴォルティス",
    "ＦＣ今治",
    "サガン鳥栖",
    "大分トリニータ",
    "テゲバジャーロ宮崎",
]


# --------------------------------------------------
# J3クラブ（20クラブ）
# --------------------------------------------------

J3 = [
    "福島ユナイテッドＦＣ",
    "栃木ＳＣ",
    "ザスパ群馬",
    "ＳＣ相模原",
    "松本山雅ＦＣ",
    "ＡＣ長野パルセイロ",
    "ツエーゲン金沢",
    "ＦＣ岐阜",
    "レイラック滋賀ＦＣ",
    "ＦＣ大阪",
    "奈良クラブ",
    "ガイナーレ鳥取",
    "レノファ山口ＦＣ",
    "カマタマーレ讃岐",
    "愛媛ＦＣ",
    "高知ユナイテッドＳＣ",
    "ギラヴァンツ北九州",
    "ロアッソ熊本",
    "鹿児島ユナイテッドＦＣ",
    "ＦＣ琉球",
]


# 辞書の順番が、そのままプルダウンのカテゴリー順になる。
TEAM_CATEGORIES = {
    "J1": J1,
    "J2": J2,
    "J3": J3,
}

TEAM_CATEGORY_BY_NAME = {
    team_name: category
    for category, team_names in TEAM_CATEGORIES.items()
    for team_name in team_names
}


def normalize_team_key(value: Any) -> str:
    """クラブ名比較用に空白を除いたNFKC文字列へそろえる。"""

    if value is None:
        return ""

    try:
        if value != value:  # NaN / pandas.NA相当
            return ""
    except (TypeError, ValueError):
        pass

    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
    )


_CANONICAL_TEAM_NAME_MAP = {
    normalize_team_key(team_name): team_name
    for team_name in TEAM_CATEGORY_BY_NAME
}


def normalize_team_name(
    value: Any,
    aliases: Optional[Mapping[str, str]] = None,
) -> str:
    """正式名・略称・全半角の揺れを現在のクラブ名へそろえる。"""

    normalized_key = normalize_team_key(value)

    if not normalized_key:
        return ""

    canonical_name = _CANONICAL_TEAM_NAME_MAP.get(normalized_key)

    if canonical_name:
        return canonical_name

    if aliases:
        alias_map = {
            normalize_team_key(alias): team_name
            for alias, team_name in aliases.items()
        }
        canonical_name = alias_map.get(normalized_key)

        if canonical_name:
            return canonical_name

    return str(value).strip()


def get_team_category(team_name: Any) -> Optional[str]:
    """表記を正規化して現在の所属カテゴリーを返す。"""

    return TEAM_CATEGORY_BY_NAME.get(normalize_team_name(team_name))


def create_team_options() -> list[tuple[str, str]]:
    """プルダウン用の（カテゴリー、クラブ名）一覧を作る。"""

    return [
        (category, team_name)
        for category, team_names in TEAM_CATEGORIES.items()
        for team_name in team_names
    ]


def format_team_option(team_option: tuple[str, str]) -> str:
    """プルダウン内でカテゴリーとクラブ名を見やすく表示する。"""

    category, team_name = team_option
    return f"{category}｜{team_name}"


# app.py側では、この一覧を読み込むだけで利用できる。
TEAM_OPTIONS = create_team_options()
