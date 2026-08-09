"""Version7-Cが読む開催回・払戻入力の形式差を確認する。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from history_manager import (
    JAPAN_TIMEZONE,
    ROUND_CSV_COLUMNS,
    ROUND_CSV_OPTIONAL_COLUMNS,
    TotoDataFormatError,
    TotoHistoryManager,
    TotoMatch,
    TotoPayouts,
    TotoRound,
    get_saved_toto_payouts,
    normalize_saved_round_frame,
    normalize_toto_payouts,
    safe_get_first_prize,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def history_frame(round_id: int = 1701) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "toto_round": [round_id],
            "toto_match_number": [1],
            "prediction_version": ["Version7-A"],
        }
    )


class StaticHistoryManager:
    def __init__(self, saved_round):
        self.saved_round = saved_round

    def load_saved_round(self, round_id: int):
        return self.saved_round


class LegacyPayoutRecord:
    """旧キーのSeriesを属性adapterが保持する再現用形式。"""

    def __init__(self) -> None:
        self.row = pd.Series(
            {
                "first_prize": "1,000円",
                "second_prize": 200,
                "third_prize": 50,
            }
        )

    @property
    def first_prize_yen(self):
        # 修正前のUI属性参照では、ここからIndex.get_locのKeyErrorになった。
        return self.row["first_prize_yen"]


class LegacyRound:
    payouts = LegacyPayoutRecord()


class PayoutWithoutFirstPrize:
    second_prize_yen = 200
    third_prize_yen = 50


class Version7CDataSchemaTest(unittest.TestCase):
    def test_ui_does_not_read_raw_payout_fields(self) -> None:
        source = (PROJECT_ROOT / "bet_optimization_ui.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("get_saved_toto_payouts", source)
        self.assertNotIn("def _saved_toto_payouts(", source)
        self.assertNotIn("first_prize_yen", source)

    def test_normal_payout_data_is_available(self) -> None:
        normalized = normalize_toto_payouts(TotoPayouts(1000, 200, 50))

        self.assertEqual(normalized.as_tuple(), (1000, 200, 50))
        self.assertEqual(safe_get_first_prize(normalized), 1000)

    def test_json_shaped_nested_mapping_is_normalized(self) -> None:
        payload = json.loads(
            '{"payouts":{"1等":"1,000円","2等":200,"3等":50}}'
        )

        self.assertEqual(
            normalize_toto_payouts(payload).as_tuple(),
            (1000, 200, 50),
        )

    def test_none_payout_data_is_unavailable(self) -> None:
        normalized = normalize_toto_payouts(None)

        self.assertIsNone(normalized.first_prize_yen)
        self.assertFalse(normalized.is_available_for_roi)

    def test_empty_dataframe_and_series_are_unavailable(self) -> None:
        for value in (pd.DataFrame(), pd.Series(dtype=object)):
            with self.subTest(value_type=type(value).__name__):
                normalized = normalize_toto_payouts(value)
                self.assertFalse(normalized.is_available_for_roi)
                self.assertIsNone(normalized.first_prize_yen)

    def test_missing_first_prize_column_does_not_zero_fill(self) -> None:
        normalized = normalize_toto_payouts(
            pd.DataFrame(
                {
                    "second_prize_yen": [200],
                    "third_prize_yen": [50],
                }
            )
        )

        self.assertIsNone(normalized.first_prize_yen)
        self.assertEqual(normalized.second_prize_yen, 200)
        self.assertFalse(normalized.is_available_for_roi)

    def test_missing_first_prize_attribute_is_unavailable(self) -> None:
        normalized = normalize_toto_payouts(PayoutWithoutFirstPrize())

        self.assertIsNone(normalized.first_prize_yen)
        self.assertEqual(normalized.third_prize_yen, 50)
        self.assertFalse(normalized.is_available_for_roi)

    def test_nan_is_preserved_as_missing(self) -> None:
        normalized = normalize_toto_payouts(
            {
                "first_prize_yen": float("nan"),
                "second_prize_yen": pd.NA,
                "third_prize_yen": None,
            }
        )

        self.assertEqual(
            (
                normalized.first_prize_yen,
                normalized.second_prize_yen,
                normalized.third_prize_yen,
            ),
            (None, None, None),
        )

    def test_legacy_saved_round_aliases_are_normalized(self) -> None:
        legacy = pd.DataFrame(
            {
                "toto_round": [1701],
                "toto_match_number": [1],
                "ホーム": ["鹿島アントラーズ"],
                "アウェイ": ["浦和レッズ"],
                "match_date": ["2026-08-09"],
                "first_prize": ["1,000円"],
                "second_prize": [200],
                "third_prize": [50],
            }
        )

        normalized_frame = normalize_saved_round_frame(legacy)
        normalized_payouts = normalize_toto_payouts(normalized_frame)

        self.assertEqual(tuple(normalized_frame.columns), ROUND_CSV_COLUMNS)
        self.assertEqual(normalized_payouts.as_tuple(), (1000, 200, 50))
        self.assertTrue(
            normalized_frame[list(ROUND_CSV_OPTIONAL_COLUMNS)]
            .isna()
            .any()
            .any()
        )

    def test_legacy_saved_round_csv_loads_through_manager(self) -> None:
        legacy_rows = pd.DataFrame(
            [
                {
                    "toto_round": 1701,
                    "toto_match_number": number,
                    "ホーム": "鹿島アントラーズ",
                    "アウェイ": "浦和レッズ",
                    "match_date": "2026-08-09T15:00:00+09:00",
                    "actual_result": ("1", "0", "2")[(number - 1) % 3],
                    "first_prize": "1,000円",
                    "second_prize": 200,
                    "third_prize": 50,
                }
                for number in range(1, 14)
            ]
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "legacy_rounds.csv"
            legacy_rows.to_csv(path, index=False, encoding="utf-8-sig")
            loaded = TotoHistoryManager(csv_path=path).load_saved_round(1701)

        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.is_complete)
        self.assertEqual(loaded.payouts, TotoPayouts(1000, 200, 50))

    def test_saved_round_missing_required_columns_has_explicit_error(self) -> None:
        with self.assertRaisesRegex(
            TotoDataFormatError,
            "必須列が不足しています",
        ):
            normalize_saved_round_frame(pd.DataFrame({"round_id": [1701]}))

    def test_unplayed_round_without_payout_does_not_block_strategy(self) -> None:
        toto_round = TotoRound(round_id=1701, matches=())

        payouts = get_saved_toto_payouts(
            StaticHistoryManager(toto_round),
            history_frame(),
            target="toto",
        )

        self.assertEqual(payouts, {})

    def test_completed_round_without_payout_is_unavailable(self) -> None:
        toto_round = TotoRound(
            round_id=1701,
            matches=tuple(
                TotoMatch(
                    round_id=1701,
                    match_number=number,
                    home_team="鹿島アントラーズ",
                    away_team="浦和レッズ",
                    match_time=datetime(2026, 8, 9, tzinfo=JAPAN_TIMEZONE),
                    actual_result=("1", "0", "2")[(number - 1) % 3],
                )
                for number in range(1, 14)
            ),
        )

        payouts = get_saved_toto_payouts(
            StaticHistoryManager(toto_round),
            history_frame(),
            target="toto",
        )

        self.assertTrue(toto_round.is_complete)
        self.assertEqual(payouts, {})

    def test_legacy_series_adapter_no_longer_raises_key_error(self) -> None:
        payouts = get_saved_toto_payouts(
            StaticHistoryManager(LegacyRound()),
            history_frame(),
            target="toto",
        )

        self.assertEqual(payouts[1701].as_tuple(), (1000, 200, 50))

    def test_partial_rounds_do_not_disable_complete_round_payouts(self) -> None:
        class MixedManager:
            def load_saved_round(self, round_id: int):
                if round_id == 1701:
                    return {"payouts": TotoPayouts(1000, 200, 50)}
                return {"payouts": None}

        history = pd.concat(
            [history_frame(1701), history_frame(1702)],
            ignore_index=True,
        )

        payouts = get_saved_toto_payouts(
            MixedManager(),
            history,
            target="toto",
        )

        self.assertEqual(set(payouts), {1701})
        self.assertEqual(payouts[1701].first_prize_yen, 1000)


if __name__ == "__main__":
    unittest.main()
