"""toto公式順・開催回・結果・CSVフォールバックを確認する。"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from history_manager import (
    JAPAN_TIMEZONE,
    TotoHistoryManager,
    TotoOfficialDataSource,
    create_matches_from_toto_round,
    parse_round_catalog,
    parse_toto_info_page,
    parse_toto_result_page,
)


def info_html(round_id: int = 1644) -> str:
    match_rows = "".join(
        f"<tr><td>{number}</td><td>08/08</td><td>19:00</td>"
        f"<td>会場{number}</td><td>FC東京</td><td>VS</td><td>町田</td></tr>"
        for number in range(1, 14)
    )
    return f"""
    <html><body><h1>第{round_id}回 toto くじ情報</h1>
    <table><tr><td>販売開始日</td><td>2026年08月01日（土）08：00</td></tr>
    <tr><td>販売終了日</td><td>2026年08月08日（土）14:35</td></tr>
    <tr><td>結果発表日</td><td>2026年08月09日（日）</td></tr></table>
    <table><thead><tr><th>No</th><th>開催日</th><th>試合開始予定時間</th>
    <th>競技場</th><th>指定試合（ホームvsアウェイ）</th>
    <th>指定試合（ホームvsアウェイ）.1</th>
    <th>指定試合（ホームvsアウェイ）.2</th></tr></thead>
    <tbody>{match_rows}</tbody></table></body></html>
    """


def result_html(round_id: int = 1548) -> str:
    result_values = ("1", "0", "2")
    match_rows = "".join(
        f"<tr><td>06/21</td><td>会場{number}</td><td>{number}</td>"
        f"<td>FC東京</td><td>2-1</td><td>町田</td>"
        f"<td>{result_values[(number - 1) % 3]}</td></tr>"
        for number in range(1, 14)
    )
    return f"""
    <html><body><h1>第{round_id}回 toto くじ結果</h1>
    <table><thead><tr><th>開催日</th><th>競技場</th><th>No</th><th>ホーム</th>
    <th>試合結果</th><th>アウェイ</th><th>くじ結果</th></tr></thead>
    <tbody>{match_rows}</tbody></table>
    <table><thead><tr><th>第{round_id}回</th><th>1等</th><th>2等</th><th>3等</th></tr></thead>
    <tbody><tr><td>当せん金</td><td>1,000円</td><td>200円</td><td>50円</td></tr></tbody></table>
    <table><thead><tr><th>販売期間</th><th>販売開始日</th><th>販売終了日</th><th>結果発表日</th></tr></thead>
    <tbody><tr><td>販売期間</td><td>2025年06月14日(土)</td>
    <td>2025年06月21日(土)</td><td>2025年06月22日(日)</td></tr></tbody></table>
    </body></html>
    """


class HistoryManagerTest(unittest.TestCase):
    def test_current_round_is_parsed_in_official_one_to_thirteen_order(self) -> None:
        toto_round = parse_toto_info_page(info_html())

        self.assertEqual(toto_round.round_id, 1644)
        self.assertTrue(toto_round.is_official_order_complete)
        self.assertEqual(toto_round.matches[0].home_team, "ＦＣ東京")
        self.assertEqual(toto_round.matches[0].away_team, "ＦＣ町田ゼルビア")
        self.assertEqual(toto_round.matches[-1].match_number, 13)

        frame = create_matches_from_toto_round(toto_round)
        self.assertEqual(frame["toto_match_number"].tolist(), list(range(1, 14)))
        self.assertEqual(frame["match_number"].tolist(), list(range(1, 14)))

    def test_completed_round_results_and_payouts_are_parsed(self) -> None:
        toto_round = parse_toto_result_page(result_html())

        self.assertTrue(toto_round.is_complete)
        self.assertTrue(toto_round.is_jleague_round)
        self.assertEqual(toto_round.matches[1].actual_result, "0")
        self.assertEqual(toto_round.payouts.first_prize_yen, 1000)
        self.assertEqual(toto_round.payouts.second_prize_yen, 200)
        self.assertEqual(toto_round.payouts.third_prize_yen, 50)

    def test_catalog_contains_round_key_and_fiscal_year(self) -> None:
        catalog = parse_round_catalog(
            '<a href="./result.form?holdCntId=1548">第1548回 (06/22)</a>',
            2025,
        )
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0].round_id, 1548)
        self.assertEqual(catalog[0].fiscal_year, 2025)

    def test_failed_official_fetch_falls_back_to_saved_csv_then_current(self) -> None:
        def fail_request(*args, **kwargs):
            raise requests.ConnectionError("offline")

        now = datetime(2026, 8, 1, 12, 0, tzinfo=JAPAN_TIMEZONE)
        source = TotoOfficialDataSource(now=now, request_get=fail_request)
        with tempfile.TemporaryDirectory() as temporary_directory:
            csv_path = Path(temporary_directory) / "toto_rounds.csv"
            manager = TotoHistoryManager(source, csv_path)
            saved_round = parse_toto_info_page(info_html())
            self.assertTrue(manager.save_round(saved_round))

            result = manager.load_current_round()
            self.assertTrue(result.is_loaded)
            self.assertEqual(result.source_name, "保存CSV")
            self.assertEqual(result.toto_round.round_id, 1644)

            csv_path.unlink()
            current = pd.DataFrame(
                {
                    "match_number": list(range(1, 14)),
                    "match_date": ["2026-08-08"] * 13,
                    "home_team": ["ＦＣ東京"] * 13,
                    "away_team": ["ＦＣ町田ゼルビア"] * 13,
                }
            )
            current_result = manager.load_current_round(current)
            self.assertTrue(current_result.is_loaded)
            self.assertEqual(current_result.source_name, "現在データ")


if __name__ == "__main__":
    unittest.main()
