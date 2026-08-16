"""Version8-Aの実戦予測・買い目・公式結果を不変履歴として保存する。

既存の ``prediction_history.csv`` は学習・バックテスト用のVersion別履歴であり、
同一開催回・同一Versionを最新行へ置換する。本moduleはその契約を変更せず、実際に
予測した瞬間を ``prediction_run_id`` 単位で別ファイルへ追記する。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from bet_optimizer import BetPlan, plan_fingerprint, target_source_match_numbers
from history_manager import JAPAN_TIMEZONE, TotoRound, normalize_toto_payouts


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_LIVE_HISTORY_DIRECTORY = PROJECT_ROOT / "data" / "history"
DEFAULT_LIVE_ROUND_HISTORY_PATH = (
    DEFAULT_LIVE_HISTORY_DIRECTORY / "live_round_history.csv"
)
DEFAULT_LIVE_MATCH_HISTORY_PATH = (
    DEFAULT_LIVE_HISTORY_DIRECTORY / "live_match_history.csv"
)
DEFAULT_LIVE_BET_HISTORY_PATH = (
    DEFAULT_LIVE_HISTORY_DIRECTORY / "live_bet_history.csv"
)

LIVE_HISTORY_SCHEMA_VERSION = 1
PROBABILITY_TOLERANCE = 1e-9
# Version7-Cは画面の小数1桁%を買い目へ渡すため、実戦履歴のフル精度確率との
# 所属確認だけは最大剰余丸め1単位（0.1%）の半分を許容する。
BET_PLAN_PROBABILITY_TOLERANCE = 0.0005000001
TOTO_OUTCOMES = ("1", "0", "2")
ROUND_STATUSES = (
    "predicted",
    "purchased",
    "pending_result",
    "result_confirmed",
    "evaluated",
)
BET_RECORD_TYPES = ("recommended", "purchased")
TARGETS = ("toto", "mini_a", "mini_b")

ROUND_COLUMNS = (
    "schema_version",
    "prediction_run_id",
    "round_id",
    "prediction_version",
    "predicted_at",
    "saved_at",
    "settings_snapshot_json",
    "prediction_match_count",
    "season",
    "round_start_at",
    "round_end_at",
    "source_name",
    "round_status",
    "purchased",
    "purchased_at",
    "result_confirmed_at",
    "evaluated_at",
    "actual_result_count",
    "favorite_hit_count",
    "favorite_hit_count_1",
    "favorite_hit_count_0",
    "favorite_hit_count_2",
    "recommended_bet_count",
    "purchased_bet_count",
    "first_prize_yen",
    "second_prize_yen",
    "third_prize_yen",
    "optimization_run_id",
    "best_trial",
    "best_score",
    "immutable_hash",
)
ROUND_IMMUTABLE_COLUMNS = (
    "schema_version",
    "prediction_run_id",
    "round_id",
    "prediction_version",
    "predicted_at",
    "settings_snapshot_json",
    "prediction_match_count",
    "season",
    "round_start_at",
    "round_end_at",
    "source_name",
    "optimization_run_id",
    "best_trial",
    "best_score",
)

MATCH_IMMUTABLE_COLUMNS = (
    "schema_version",
    "prediction_run_id",
    "round_id",
    "toto_match_number",
    "league",
    "season",
    "match_time",
    "home_team",
    "away_team",
    "prediction_version",
    "probability_1",
    "probability_0",
    "probability_2",
    "predicted_result",
    "predicted_score",
    "home_expected_goals",
    "away_expected_goals",
    "home_elo",
    "away_elo",
    "elo_difference",
    "draw_candidate",
    "draw_probability",
    "draw_confidence",
    "draw_candidate_threshold",
    "draw_candidate_reasons",
    "predicted_at",
)
MATCH_RESULT_COLUMNS = (
    "actual_result",
    "actual_home_goals",
    "actual_away_goals",
    "result_confirmed_at",
    "predicted_hit",
)
MATCH_COLUMNS = (
    *MATCH_IMMUTABLE_COLUMNS,
    *MATCH_RESULT_COLUMNS,
    "immutable_hash",
)

BET_IMMUTABLE_COLUMNS = (
    "schema_version",
    "bet_record_id",
    "prediction_run_id",
    "round_id",
    "target",
    "prediction_version",
    "record_type",
    "recommended",
    "purchased",
    "source_recommendation_id",
    "double_count",
    "triple_count",
    "selections_json",
    "ticket_count",
    "planned_purchase_amount_yen",
    "actual_purchase_amount_yen",
    "coverage",
    "draw_candidate_threshold",
    "draw_candidate_margin",
    "draw_inclusion_json",
    "draw_included_match_count",
    "draw_included_ticket_count",
    "generated_at",
    "purchased_at",
)
BET_RESULT_COLUMNS = (
    "covered_match_count",
    "all_matches_covered",
    "winning_rank",
    "winning_ticket_count",
    "simulation_return_yen",
    "actual_return_yen",
    "simulation_profit_yen",
    "actual_profit_yen",
    "simulation_roi",
    "actual_roi",
    "evaluated_at",
)
BET_COLUMNS = (
    *BET_IMMUTABLE_COLUMNS,
    *BET_RESULT_COLUMNS,
    "immutable_hash",
)

_RUN_ID_PATTERN = re.compile(
    r"^run_[0-9]{8}T[0-9]{12}_[0-9a-f]{32}$"
)
_PROCESS_WRITE_LOCK = threading.RLock()


class LiveHistoryError(RuntimeError):
    """実戦履歴の安全な保存・読込に失敗した。"""


class LiveHistoryValidationError(LiveHistoryError):
    """保存対象が実戦履歴の契約を満たさない。"""


class LiveHistoryConflictError(LiveHistoryError):
    """同じIDに異なる不変データまたは異なる公式結果が渡された。"""


class LiveHistoryStorageError(LiveHistoryError):
    """履歴ファイルを安全に読書きできない。"""


@dataclass(frozen=True)
class SavePredictionOutcome:
    prediction_run_id: str
    created: bool


@dataclass(frozen=True)
class ResultUpdateOutcome:
    prediction_run_id: str
    updated_match_count: int
    actual_result_count: int
    round_status: str


@dataclass
class LiveHistoryManager:
    """3階層の実戦履歴をCSVへ原子的に保存する。"""

    round_path: Path = DEFAULT_LIVE_ROUND_HISTORY_PATH
    match_path: Path = DEFAULT_LIVE_MATCH_HISTORY_PATH
    bet_path: Path = DEFAULT_LIVE_BET_HISTORY_PATH
    warnings: list[str] = field(default_factory=list, init=False)

    def load_rounds(self) -> pd.DataFrame:
        frame = self._read(self.round_path, ROUND_COLUMNS, strict=False)
        if frame.empty:
            return frame
        frame = self._verified_for_display(
            frame,
            ROUND_IMMUTABLE_COLUMNS,
            "開催回run",
        )
        if frame.empty:
            return pd.DataFrame(columns=ROUND_COLUMNS)
        return frame.sort_values(
            ["predicted_at", "prediction_run_id"], ascending=[False, False]
        ).reset_index(drop=True)

    def load_matches(self, prediction_run_id: Optional[str] = None) -> pd.DataFrame:
        frame = self._read(self.match_path, MATCH_COLUMNS, strict=False)
        committed_ids = set(self.load_rounds()["prediction_run_id"].astype(str))
        if not frame.empty:
            frame = frame.loc[
                frame["prediction_run_id"].astype(str).isin(committed_ids)
            ]
            frame = self._verified_for_display(
                frame,
                MATCH_IMMUTABLE_COLUMNS,
                "試合予測",
            )
        if prediction_run_id is not None:
            _validate_run_id(prediction_run_id)
            frame = frame.loc[
                frame["prediction_run_id"].astype(str) == prediction_run_id
            ]
        if frame.empty:
            return pd.DataFrame(columns=MATCH_COLUMNS)
        numbers = pd.to_numeric(frame["toto_match_number"], errors="coerce")
        return (
            frame.assign(_match_number=numbers)
            .sort_values(["prediction_run_id", "_match_number"])
            .drop(columns="_match_number")
            .reset_index(drop=True)
        )

    def load_bets(self, prediction_run_id: Optional[str] = None) -> pd.DataFrame:
        frame = self._read(self.bet_path, BET_COLUMNS, strict=False)
        committed_ids = set(self.load_rounds()["prediction_run_id"].astype(str))
        if not frame.empty:
            frame = frame.loc[
                frame["prediction_run_id"].astype(str).isin(committed_ids)
            ]
            frame = self._verified_for_display(
                frame,
                BET_IMMUTABLE_COLUMNS,
                "買い目",
            )
        if prediction_run_id is not None:
            _validate_run_id(prediction_run_id)
            frame = frame.loc[
                frame["prediction_run_id"].astype(str) == prediction_run_id
            ]
        if frame.empty:
            return pd.DataFrame(columns=BET_COLUMNS)
        return frame.sort_values(
            ["generated_at", "bet_record_id"], ascending=[True, True]
        ).reset_index(drop=True)

    def save_prediction(
        self,
        result_df: pd.DataFrame,
        toto_round: TotoRound,
        *,
        settings_snapshot: Mapping[str, Any],
        prediction_time: datetime,
        source_name: str,
        prediction_run_id: Optional[str] = None,
    ) -> SavePredictionOutcome:
        """13試合の予測を新しいrunとして保存し、以後の予測列を不変にする。"""

        run_id = prediction_run_id or generate_prediction_run_id(prediction_time)
        _validate_run_id(run_id)
        predicted_at = _jst_iso(prediction_time)
        settings_json = _stable_json(settings_snapshot, "設定スナップショット")
        match_rows = _build_match_rows(
            result_df,
            toto_round,
            run_id=run_id,
            predicted_at=predicted_at,
        )
        round_row = _build_round_row(
            match_rows,
            toto_round,
            run_id=run_id,
            predicted_at=predicted_at,
            settings_json=settings_json,
            source_name=source_name,
        )

        with _PROCESS_WRITE_LOCK:
            rounds = self._read(self.round_path, ROUND_COLUMNS, strict=True)
            _assert_immutable_integrity(
                rounds,
                ROUND_IMMUTABLE_COLUMNS,
                "開催回run",
            )
            existing = rounds.loc[
                rounds["prediction_run_id"].astype(str) == run_id
            ]
            if not existing.empty:
                if len(existing) != 1 or str(existing.iloc[0]["immutable_hash"]) != str(
                    round_row["immutable_hash"]
                ):
                    raise LiveHistoryConflictError(
                        "同じprediction_run_idに異なる予測が保存されています。"
                    )
                existing_matches = self._read(
                    self.match_path, MATCH_COLUMNS, strict=True
                )
                _assert_immutable_integrity(
                    existing_matches,
                    MATCH_IMMUTABLE_COLUMNS,
                    "試合予測",
                )
                selected = existing_matches.loc[
                    existing_matches["prediction_run_id"].astype(str) == run_id
                ]
                expected_hashes = {row["immutable_hash"] for row in match_rows}
                if len(selected) != 13 or set(
                    selected["immutable_hash"].astype(str)
                ) != expected_hashes:
                    raise LiveHistoryConflictError(
                        "同じprediction_run_idの試合予測が一致しません。"
                    )
                return SavePredictionOutcome(run_id, False)

            matches = self._read(self.match_path, MATCH_COLUMNS, strict=True)
            _assert_immutable_integrity(
                matches,
                MATCH_IMMUTABLE_COLUMNS,
                "試合予測",
            )
            # 前回停止時の未commit行だけを除き、同じrunの再試行を安全にする。
            matches = matches.loc[
                matches["prediction_run_id"].astype(str) != run_id
            ]
            next_matches = pd.concat(
                [matches, pd.DataFrame(match_rows, columns=MATCH_COLUMNS)],
                ignore_index=True,
            )
            self._atomic_write(self.match_path, next_matches, MATCH_COLUMNS)
            next_rounds = pd.concat(
                [rounds, pd.DataFrame([round_row], columns=ROUND_COLUMNS)],
                ignore_index=True,
            )
            try:
                self._atomic_write(self.round_path, next_rounds, ROUND_COLUMNS)
            except LiveHistoryStorageError:
                # 試合側に残る未commit行はload時に見えず、次回retryで置換される。
                raise
        return SavePredictionOutcome(run_id, True)

    def save_recommended_bet(
        self,
        prediction_run_id: str,
        plan: BetPlan,
        *,
        generated_at: Optional[datetime] = None,
    ) -> str:
        """AI提案をrecommended=True / purchased=Falseで保存する。"""

        return self._save_bet(
            prediction_run_id,
            plan,
            record_type="recommended",
            generated_at=generated_at,
            purchased_at=None,
            actual_purchase_amount_yen=None,
            source_recommendation_id="",
        )

    def record_purchase(
        self,
        prediction_run_id: str,
        final_plan: BetPlan,
        *,
        actual_purchase_amount_yen: int,
        purchased_at: Optional[datetime] = None,
        source_recommendation_id: str = "",
    ) -> str:
        """手動変更後を含む最終買い目を実購入記録として保存する。"""

        if isinstance(actual_purchase_amount_yen, bool):
            raise LiveHistoryValidationError("実購入金額は0円以上の整数にしてください。")
        try:
            amount = int(actual_purchase_amount_yen)
        except (TypeError, ValueError) as error:
            raise LiveHistoryValidationError(
                "実購入金額は0円以上の整数にしてください。"
            ) from error
        if amount < 0 or amount != actual_purchase_amount_yen:
            raise LiveHistoryValidationError("実購入金額は0円以上の整数にしてください。")
        now = purchased_at or datetime.now(JAPAN_TIMEZONE)
        return self._save_bet(
            prediction_run_id,
            final_plan,
            record_type="purchased",
            generated_at=now,
            purchased_at=now,
            actual_purchase_amount_yen=amount,
            source_recommendation_id=source_recommendation_id,
        )

    def update_actual_results(
        self,
        prediction_run_id: str,
        toto_round: TotoRound,
        *,
        source_name: str,
        confirmed_at: Optional[datetime] = None,
    ) -> ResultUpdateOutcome:
        """信頼済み経路の実結果だけを更新し、予測列は一切書き換えない。"""

        _validate_run_id(prediction_run_id)
        if source_name not in ("toto公式", "保存CSV"):
            raise LiveHistoryValidationError(
                "実結果はtoto公式または公式由来の保存CSVからだけ更新できます。"
            )
        timestamp = _jst_iso(confirmed_at or datetime.now(JAPAN_TIMEZONE))
        official_by_number = {match.match_number: match for match in toto_round.matches}
        if set(official_by_number) != set(range(1, 14)):
            raise LiveHistoryValidationError(
                "公式試合番号1～13を確認できないため結果を更新しません。"
            )

        with _PROCESS_WRITE_LOCK:
            rounds = self._read(self.round_path, ROUND_COLUMNS, strict=True)
            _assert_immutable_integrity(
                rounds,
                ROUND_IMMUTABLE_COLUMNS,
                "開催回run",
            )
            round_indexes = rounds.index[
                rounds["prediction_run_id"].astype(str) == prediction_run_id
            ].tolist()
            if len(round_indexes) != 1:
                raise LiveHistoryValidationError(
                    "prediction_run_idに対応する実戦予測がありません。"
                )
            round_index = round_indexes[0]
            stored_round_id = _strict_positive_int(
                rounds.at[round_index, "round_id"], "開催回ID"
            )
            if stored_round_id != int(toto_round.round_id):
                raise LiveHistoryConflictError("開催回IDが保存済み予測と一致しません。")

            matches = self._read(self.match_path, MATCH_COLUMNS, strict=True)
            _assert_immutable_integrity(
                matches,
                MATCH_IMMUTABLE_COLUMNS,
                "試合予測",
            )
            indexes = matches.index[
                matches["prediction_run_id"].astype(str) == prediction_run_id
            ].tolist()
            if len(indexes) != 13:
                raise LiveHistoryStorageError("保存済みの13試合を確認できません。")
            updated = 0
            for index in indexes:
                number = _strict_positive_int(
                    matches.at[index, "toto_match_number"], "試合番号"
                )
                official = official_by_number.get(number)
                if official is None or official.actual_result not in TOTO_OUTCOMES:
                    continue
                if (
                    str(matches.at[index, "home_team"]) != official.home_team
                    or str(matches.at[index, "away_team"]) != official.away_team
                ):
                    raise LiveHistoryConflictError(
                        f"第{number}試合の対戦カードが保存済み予測と一致しません。"
                    )
                saved_actual = _optional_outcome(matches.at[index, "actual_result"])
                if saved_actual and saved_actual != official.actual_result:
                    raise LiveHistoryConflictError(
                        f"第{number}試合の保存済み実結果と公式結果が競合しています。"
                    )
                saved_home = _optional_int(matches.at[index, "actual_home_goals"])
                saved_away = _optional_int(matches.at[index, "actual_away_goals"])
                if saved_home is not None and official.home_goals is not None and (
                    saved_home != official.home_goals
                ):
                    raise LiveHistoryConflictError(
                        f"第{number}試合の保存済みホーム得点と公式結果が競合しています。"
                    )
                if saved_away is not None and official.away_goals is not None and (
                    saved_away != official.away_goals
                ):
                    raise LiveHistoryConflictError(
                        f"第{number}試合の保存済みアウェイ得点と公式結果が競合しています。"
                    )
                before = saved_actual
                matches.at[index, "actual_result"] = official.actual_result
                matches.at[index, "actual_home_goals"] = _csv_value(
                    official.home_goals
                )
                matches.at[index, "actual_away_goals"] = _csv_value(
                    official.away_goals
                )
                matches.at[index, "result_confirmed_at"] = timestamp
                matches.at[index, "predicted_hit"] = str(
                    str(matches.at[index, "predicted_result"])
                    == official.actual_result
                )
                if before != official.actual_result:
                    updated += 1

            selected = matches.loc[indexes]
            actual_count = sum(
                _optional_outcome(value) in TOTO_OUTCOMES
                for value in selected["actual_result"]
            )
            status = "result_confirmed" if actual_count == 13 else "pending_result"
            rounds.at[round_index, "actual_result_count"] = actual_count
            rounds.at[round_index, "round_status"] = status
            rounds.at[round_index, "result_confirmed_at"] = (
                timestamp if actual_count == 13 else ""
            )
            payouts = normalize_toto_payouts(toto_round.payouts)
            rounds.at[round_index, "first_prize_yen"] = _csv_value(
                payouts.first_prize_yen
            )
            rounds.at[round_index, "second_prize_yen"] = _csv_value(
                payouts.second_prize_yen
            )
            rounds.at[round_index, "third_prize_yen"] = _csv_value(
                payouts.third_prize_yen
            )
            self._atomic_write(self.match_path, matches, MATCH_COLUMNS)
            self._atomic_write(self.round_path, rounds, ROUND_COLUMNS)
        return ResultUpdateOutcome(
            prediction_run_id,
            updated,
            actual_count,
            status,
        )

    def evaluate_run(
        self,
        prediction_run_id: str,
        *,
        evaluated_at: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """13結果確定後に本命・買い目を評価し、未確定回は評価しない。"""

        _validate_run_id(prediction_run_id)
        timestamp = _jst_iso(evaluated_at or datetime.now(JAPAN_TIMEZONE))
        with _PROCESS_WRITE_LOCK:
            rounds = self._read(self.round_path, ROUND_COLUMNS, strict=True)
            _assert_immutable_integrity(
                rounds,
                ROUND_IMMUTABLE_COLUMNS,
                "開催回run",
            )
            round_indexes = rounds.index[
                rounds["prediction_run_id"].astype(str) == prediction_run_id
            ].tolist()
            if len(round_indexes) != 1:
                raise LiveHistoryValidationError("実戦予測がありません。")
            round_index = round_indexes[0]
            matches = self._read(self.match_path, MATCH_COLUMNS, strict=True)
            _assert_immutable_integrity(
                matches,
                MATCH_IMMUTABLE_COLUMNS,
                "試合予測",
            )
            match_indexes = matches.index[
                matches["prediction_run_id"].astype(str) == prediction_run_id
            ].tolist()
            if len(match_indexes) != 13:
                raise LiveHistoryStorageError("保存済みの13試合を確認できません。")
            selected_matches = matches.loc[match_indexes]
            actuals = {
                _strict_positive_int(row["toto_match_number"], "試合番号"): (
                    _optional_outcome(row["actual_result"])
                )
                for _, row in selected_matches.iterrows()
            }
            if len(actuals) != 13 or any(
                actual not in TOTO_OUTCOMES for actual in actuals.values()
            ):
                raise LiveHistoryValidationError(
                    "13試合すべての公式実結果が確定するまで評価しません。"
                )

            hit_by_class = {outcome: 0 for outcome in TOTO_OUTCOMES}
            total_hits = 0
            for _, row in selected_matches.iterrows():
                number = _strict_positive_int(row["toto_match_number"], "試合番号")
                actual = actuals[number]
                hit = str(row["predicted_result"]) == actual
                total_hits += int(hit)
                if hit:
                    hit_by_class[str(actual)] += 1

            bets = self._read(self.bet_path, BET_COLUMNS, strict=True)
            _assert_immutable_integrity(
                bets,
                BET_IMMUTABLE_COLUMNS,
                "買い目",
            )
            bet_indexes = bets.index[
                bets["prediction_run_id"].astype(str) == prediction_run_id
            ].tolist()
            payouts = _round_payouts(rounds.loc[round_index])
            for index in bet_indexes:
                evaluation = _evaluate_bet_row(bets.loc[index], actuals, payouts)
                for column, value in evaluation.items():
                    bets.at[index, column] = _csv_value(value)
                bets.at[index, "evaluated_at"] = timestamp

            rounds.at[round_index, "favorite_hit_count"] = total_hits
            rounds.at[round_index, "favorite_hit_count_1"] = hit_by_class["1"]
            rounds.at[round_index, "favorite_hit_count_0"] = hit_by_class["0"]
            rounds.at[round_index, "favorite_hit_count_2"] = hit_by_class["2"]
            rounds.at[round_index, "evaluated_at"] = timestamp
            rounds.at[round_index, "round_status"] = "evaluated"
            self._atomic_write(self.bet_path, bets, BET_COLUMNS)
            self._atomic_write(self.round_path, rounds, ROUND_COLUMNS)
        return {
            "prediction_run_id": prediction_run_id,
            "favorite_hit_count": total_hits,
            "favorite_hit_count_1": hit_by_class["1"],
            "favorite_hit_count_0": hit_by_class["0"],
            "favorite_hit_count_2": hit_by_class["2"],
        }

    def export_rounds_csv(self) -> bytes:
        return _csv_bytes(self.load_rounds())

    def export_matches_csv(self) -> bytes:
        return _csv_bytes(self.load_matches())

    def export_bets_csv(self) -> bytes:
        return _csv_bytes(self.load_bets())

    def _save_bet(
        self,
        prediction_run_id: str,
        plan: BetPlan,
        *,
        record_type: str,
        generated_at: Optional[datetime],
        purchased_at: Optional[datetime],
        actual_purchase_amount_yen: Optional[int],
        source_recommendation_id: str,
    ) -> str:
        _validate_run_id(prediction_run_id)
        if record_type not in BET_RECORD_TYPES:
            raise LiveHistoryValidationError("買い目履歴区分が不正です。")
        if plan.target not in TARGETS:
            raise LiveHistoryValidationError("対象商品が不正です。")
        now = generated_at or datetime.now(JAPAN_TIMEZONE)
        with _PROCESS_WRITE_LOCK:
            rounds = self._read(self.round_path, ROUND_COLUMNS, strict=True)
            _assert_immutable_integrity(
                rounds,
                ROUND_IMMUTABLE_COLUMNS,
                "開催回run",
            )
            round_indexes = rounds.index[
                rounds["prediction_run_id"].astype(str) == prediction_run_id
            ].tolist()
            if len(round_indexes) != 1:
                raise LiveHistoryValidationError(
                    "先に実戦予測として13試合を保存してください。"
                )
            round_index = round_indexes[0]
            round_id = _strict_positive_int(
                rounds.at[round_index, "round_id"], "開催回ID"
            )
            prediction_version = str(rounds.at[round_index, "prediction_version"])
            stored_matches = self._read(self.match_path, MATCH_COLUMNS, strict=True)
            _assert_immutable_integrity(
                stored_matches,
                MATCH_IMMUTABLE_COLUMNS,
                "試合予測",
            )
            selected_matches = stored_matches.loc[
                stored_matches["prediction_run_id"].astype(str)
                == prediction_run_id
            ]
            _validate_plan_against_matches(plan, selected_matches)
            row = _build_bet_row(
                prediction_run_id,
                round_id,
                prediction_version,
                plan,
                record_type=record_type,
                generated_at=now,
                purchased_at=purchased_at,
                actual_purchase_amount_yen=actual_purchase_amount_yen,
                source_recommendation_id=source_recommendation_id,
            )
            bets = self._read(self.bet_path, BET_COLUMNS, strict=True)
            _assert_immutable_integrity(
                bets,
                BET_IMMUTABLE_COLUMNS,
                "買い目",
            )
            existing = bets.loc[
                bets["bet_record_id"].astype(str) == row["bet_record_id"]
            ]
            if not existing.empty:
                if len(existing) != 1 or str(existing.iloc[0]["immutable_hash"]) != str(
                    row["immutable_hash"]
                ):
                    raise LiveHistoryConflictError(
                        "同じ買い目IDに異なる内容が保存されています。"
                    )
                return str(row["bet_record_id"])
            bets = pd.concat(
                [bets, pd.DataFrame([row], columns=BET_COLUMNS)], ignore_index=True
            )
            self._atomic_write(self.bet_path, bets, BET_COLUMNS)

            run_bets = bets.loc[
                bets["prediction_run_id"].astype(str) == prediction_run_id
            ]
            rounds.at[round_index, "recommended_bet_count"] = int(
                (run_bets["record_type"].astype(str) == "recommended").sum()
            )
            rounds.at[round_index, "purchased_bet_count"] = int(
                (run_bets["record_type"].astype(str) == "purchased").sum()
            )
            if record_type == "purchased":
                rounds.at[round_index, "purchased"] = "True"
                rounds.at[round_index, "purchased_at"] = _jst_iso(
                    purchased_at or now
                )
                if str(rounds.at[round_index, "round_status"]) in (
                    "predicted",
                    "purchased",
                ):
                    rounds.at[round_index, "round_status"] = "purchased"
            self._atomic_write(self.round_path, rounds, ROUND_COLUMNS)
        return str(row["bet_record_id"])

    def _read(
        self,
        path: Path,
        columns: Sequence[str],
        *,
        strict: bool,
    ) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=columns)
        try:
            frame = pd.read_csv(
                path,
                encoding="utf-8-sig",
                dtype=str,
                keep_default_na=False,
            )
        except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as error:
            message = f"{path.name}を読み込めません: {error}"
            if strict:
                raise LiveHistoryStorageError(message) from error
            self.warnings.append(message)
            return pd.DataFrame(columns=columns)
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            message = f"{path.name}の必須列が不足しています: {', '.join(missing)}"
            if strict:
                raise LiveHistoryStorageError(message)
            self.warnings.append(message)
            return pd.DataFrame(columns=columns)
        # pandas 3系のArrowStringArrayはint/floatの結果更新を拒否するため、
        # CSV境界では文字列を維持しつつ更新用DataFrameだけobject dtypeへする。
        return frame[list(columns)].astype("object").copy()

    def _verified_for_display(
        self,
        frame: pd.DataFrame,
        immutable_columns: Sequence[str],
        label: str,
    ) -> pd.DataFrame:
        if frame.empty:
            return frame
        valid_indexes = []
        invalid_count = 0
        for index, row in frame.iterrows():
            expected = _immutable_hash(row, immutable_columns)
            if str(row.get("immutable_hash", "")) == expected:
                valid_indexes.append(index)
            else:
                invalid_count += 1
        if invalid_count:
            self.warnings.append(
                f"{label}の不変hashが一致しない{invalid_count}行を表示対象外にしました。"
            )
        return frame.loc[valid_indexes].copy()

    @staticmethod
    def _atomic_write(
        path: Path,
        frame: pd.DataFrame,
        columns: Sequence[str],
    ) -> None:
        temporary_path = path.with_name(
            f".{path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            normalized = frame.copy()
            for column in columns:
                if column not in normalized.columns:
                    normalized[column] = ""
            normalized[list(columns)].to_csv(
                temporary_path,
                index=False,
                encoding="utf-8-sig",
                lineterminator="\n",
            )
            with temporary_path.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        except (OSError, UnicodeError, TypeError, ValueError) as error:
            temporary_path.unlink(missing_ok=True)
            raise LiveHistoryStorageError(
                f"{path.name}を原子的に保存できません: {error}"
            ) from error


def generate_prediction_run_id(prediction_time: Optional[datetime] = None) -> str:
    """明示的な予測保存ごとに衝突しないrun IDを作る。"""

    timestamp = prediction_time or datetime.now(JAPAN_TIMEZONE)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=JAPAN_TIMEZONE)
    return (
        "run_"
        + timestamp.astimezone(JAPAN_TIMEZONE).strftime("%Y%m%dT%H%M%S%f")
        + "_"
        + uuid.uuid4().hex
    )


def _validate_run_id(value: Any) -> str:
    text = str(value or "")
    if not _RUN_ID_PATTERN.fullmatch(text):
        raise LiveHistoryValidationError("prediction_run_idが不正です。")
    return text


def _build_match_rows(
    result_df: pd.DataFrame,
    toto_round: TotoRound,
    *,
    run_id: str,
    predicted_at: str,
) -> list[dict[str, Any]]:
    if not isinstance(result_df, pd.DataFrame) or len(result_df) != 13:
        raise LiveHistoryValidationError("実戦予測は13試合すべて必要です。")
    if not toto_round.is_official_order_complete or toto_round.round_id <= 0:
        raise LiveHistoryValidationError(
            "公式試合順と開催回IDを確認できる13試合だけ保存できます。"
        )
    number_column = (
        "toto_match_number" if "toto_match_number" in result_df.columns else "試合"
    )
    official_by_number = {match.match_number: match for match in toto_round.matches}
    rows_by_number: dict[int, pd.Series] = {}
    for _, row in result_df.iterrows():
        number = _strict_positive_int(row.get(number_column), "試合番号")
        if number in rows_by_number:
            raise LiveHistoryValidationError(f"第{number}試合が重複しています。")
        rows_by_number[number] = row
    if set(rows_by_number) != set(range(1, 14)):
        raise LiveHistoryValidationError("試合番号1～13をすべて確認できません。")

    rows: list[dict[str, Any]] = []
    versions: set[str] = set()
    for number in range(1, 14):
        source = rows_by_number[number]
        official = official_by_number[number]
        probability_1 = _strict_probability(
            source.get("live_probability_1", source.get("1")), "P(1)"
        )
        probability_0 = _strict_probability(
            source.get("live_probability_0", source.get("0")), "P(0)"
        )
        probability_2 = _strict_probability(
            source.get("live_probability_2", source.get("2")), "P(2)"
        )
        if not math.isclose(
            probability_1 + probability_0 + probability_2,
            1.0,
            rel_tol=0.0,
            abs_tol=PROBABILITY_TOLERANCE,
        ):
            raise LiveHistoryValidationError(
                f"第{number}試合のP(1)+P(0)+P(2)が100%ではありません。"
            )
        version = str(source.get("prediction_version") or "").strip()
        if not version:
            raise LiveHistoryValidationError("使用Versionを確認できません。")
        versions.add(version)
        predicted_result = str(source.get("本命") or "").strip()
        if predicted_result not in TOTO_OUTCOMES:
            raise LiveHistoryValidationError(f"第{number}試合の本命が不正です。")
        match_time = _jst_iso(official.match_time)
        active_home_elo = _optional_finite(
            source.get(
                "live_home_elo",
                source.get("version7b_home_elo", source.get("home_elo")),
            )
        )
        active_away_elo = _optional_finite(
            source.get(
                "live_away_elo",
                source.get("version7b_away_elo", source.get("away_elo")),
            )
        )
        elo_difference = _optional_finite(
            source.get(
                "live_elo_difference",
                source.get("version7b_elo_difference", source.get("elo_difference")),
            )
        )
        row = {
            "schema_version": LIVE_HISTORY_SCHEMA_VERSION,
            "prediction_run_id": run_id,
            "round_id": toto_round.round_id,
            "toto_match_number": number,
            "league": _optional_text(source.get("league")),
            "season": official.match_time.astimezone(JAPAN_TIMEZONE).year,
            "match_time": match_time,
            "home_team": official.home_team,
            "away_team": official.away_team,
            "prediction_version": version,
            "probability_1": probability_1,
            "probability_0": probability_0,
            "probability_2": probability_2,
            "predicted_result": predicted_result,
            "predicted_score": _optional_text(source.get("予想スコア")),
            "home_expected_goals": _optional_finite(
                source.get(
                    "live_home_expected_goals",
                    source.get("home_expected_after_version7b"),
                )
            ),
            "away_expected_goals": _optional_finite(
                source.get(
                    "live_away_expected_goals",
                    source.get("away_expected_after_version7b"),
                )
            ),
            "home_elo": active_home_elo,
            "away_elo": active_away_elo,
            "elo_difference": elo_difference,
            "draw_candidate": _strict_bool(source.get("draw_candidate", False)),
            "draw_probability": probability_0,
            # Version7-A/Bは独立した「引分信頼度」を出力しない。推測せず空欄。
            "draw_confidence": _optional_text(source.get("draw_confidence")),
            "draw_candidate_threshold": _optional_finite(
                source.get("draw_candidate_threshold")
            ),
            "draw_candidate_reasons": _optional_text(
                source.get("draw_candidate_reasons")
            ),
            "predicted_at": predicted_at,
            "actual_result": "",
            "actual_home_goals": "",
            "actual_away_goals": "",
            "result_confirmed_at": "",
            "predicted_hit": "",
        }
        row["immutable_hash"] = _immutable_hash(row, MATCH_IMMUTABLE_COLUMNS)
        rows.append(row)
    if len(versions) != 1:
        raise LiveHistoryValidationError("1回の実戦予測に複数Versionが混在しています。")
    return rows


def _build_round_row(
    match_rows: Sequence[Mapping[str, Any]],
    toto_round: TotoRound,
    *,
    run_id: str,
    predicted_at: str,
    settings_json: str,
    source_name: str,
) -> dict[str, Any]:
    starts = [match.match_time for match in toto_round.matches]
    snapshot = json.loads(settings_json)
    optimization = snapshot.get("optimization_reference", {})
    if not isinstance(optimization, Mapping):
        optimization = {}
    row = {
        "schema_version": LIVE_HISTORY_SCHEMA_VERSION,
        "prediction_run_id": run_id,
        "round_id": toto_round.round_id,
        "prediction_version": match_rows[0]["prediction_version"],
        "predicted_at": predicted_at,
        "saved_at": _jst_iso(datetime.now(JAPAN_TIMEZONE)),
        "settings_snapshot_json": settings_json,
        "prediction_match_count": 13,
        "season": min(starts).astimezone(JAPAN_TIMEZONE).year,
        "round_start_at": _jst_iso(min(starts)),
        "round_end_at": _jst_iso(max(starts)),
        "source_name": str(source_name or ""),
        "round_status": "predicted",
        "purchased": False,
        "purchased_at": "",
        "result_confirmed_at": "",
        "evaluated_at": "",
        "actual_result_count": 0,
        "favorite_hit_count": "",
        "favorite_hit_count_1": "",
        "favorite_hit_count_0": "",
        "favorite_hit_count_2": "",
        "recommended_bet_count": 0,
        "purchased_bet_count": 0,
        "first_prize_yen": "",
        "second_prize_yen": "",
        "third_prize_yen": "",
        "optimization_run_id": _optional_text(optimization.get("run_id")),
        "best_trial": _csv_value(optimization.get("best_trial")),
        "best_score": _csv_value(optimization.get("best_score")),
    }
    row["immutable_hash"] = _immutable_hash(row, ROUND_IMMUTABLE_COLUMNS)
    return row


def _build_bet_row(
    prediction_run_id: str,
    round_id: int,
    prediction_version: str,
    plan: BetPlan,
    *,
    record_type: str,
    generated_at: datetime,
    purchased_at: Optional[datetime],
    actual_purchase_amount_yen: Optional[int],
    source_recommendation_id: str,
) -> dict[str, Any]:
    selections = []
    draw_details = []
    draw_included_matches = 0
    non_draw_ticket_count = 1
    for recommendation in plan.recommendations:
        analysis = recommendation.analysis
        prediction = analysis.prediction
        includes_draw = "0" in recommendation.outcomes
        draw_included_matches += int(includes_draw)
        non_draw_ticket_count *= sum(
            outcome != "0" for outcome in recommendation.outcomes
        )
        selections.append(
            {
                "match_number": prediction.match_number,
                "source_match_number": prediction.source_match_number,
                "home_team": prediction.home_team,
                "away_team": prediction.away_team,
                "bet_type": recommendation.bet_type,
                "outcomes": list(recommendation.outcomes),
                "includes_draw": includes_draw,
                "coverage": recommendation.coverage,
            }
        )
        draw_details.append(
            {
                "source_match_number": prediction.source_match_number,
                "p0": prediction.probability_0,
                "model_draw_candidate": prediction.model_draw_candidate,
                "optimizer_draw_candidate": analysis.draw_candidate,
                "draw_candidate_threshold": analysis.draw_candidate_threshold,
                "draw_signal": analysis.draw_signal,
                "draw_inclusion_evaluated": analysis.draw_inclusion_evaluated,
                "draw_inclusion_score": analysis.draw_inclusion_score,
                "draw_inclusion_coverage_loss": (
                    analysis.draw_inclusion_coverage_loss
                ),
                "draw_inclusion_recommended": (
                    analysis.draw_inclusion_recommended
                ),
                "included_draw": includes_draw,
            }
        )
    generated_text = _jst_iso(generated_at)
    purchased_text = _jst_iso(purchased_at) if purchased_at else ""
    immutable_payload = {
        "prediction_run_id": prediction_run_id,
        "target": plan.target,
        "record_type": record_type,
        "plan_fingerprint": plan_fingerprint(plan),
        "selections": selections,
        "generated_at": generated_text,
        "actual_purchase_amount_yen": actual_purchase_amount_yen,
        "purchased_at": purchased_text,
    }
    digest = hashlib.sha256(
        _stable_json(immutable_payload, "買い目").encode("utf-8")
    ).hexdigest()
    bet_record_id = f"bet_{record_type}_{digest[:32]}"
    is_recommended = record_type == "recommended"
    is_purchased = record_type == "purchased"
    row = {
        "schema_version": LIVE_HISTORY_SCHEMA_VERSION,
        "bet_record_id": bet_record_id,
        "prediction_run_id": prediction_run_id,
        "round_id": round_id,
        "target": plan.target,
        "prediction_version": prediction_version,
        "record_type": record_type,
        "recommended": is_recommended,
        "purchased": is_purchased,
        "source_recommendation_id": str(source_recommendation_id or ""),
        "double_count": plan.double_count,
        "triple_count": plan.triple_count,
        "selections_json": _stable_json(selections, "買い目選択"),
        "ticket_count": plan.ticket_count,
        "planned_purchase_amount_yen": plan.purchase_amount_yen,
        "actual_purchase_amount_yen": _csv_value(actual_purchase_amount_yen),
        "coverage": plan.estimated_full_coverage,
        "draw_candidate_threshold": plan.draw_candidate_threshold,
        "draw_candidate_margin": plan.draw_candidate_margin,
        "draw_inclusion_json": _stable_json(draw_details, "Draw Inclusion"),
        "draw_included_match_count": draw_included_matches,
        "draw_included_ticket_count": plan.ticket_count - non_draw_ticket_count,
        "generated_at": generated_text,
        "purchased_at": purchased_text,
        "covered_match_count": "",
        "all_matches_covered": "",
        "winning_rank": "",
        "winning_ticket_count": "",
        "simulation_return_yen": "",
        "actual_return_yen": "",
        "simulation_profit_yen": "",
        "actual_profit_yen": "",
        "simulation_roi": "",
        "actual_roi": "",
        "evaluated_at": "",
    }
    row["immutable_hash"] = _immutable_hash(row, BET_IMMUTABLE_COLUMNS)
    return row


def _validate_plan_against_matches(plan: BetPlan, matches: pd.DataFrame) -> None:
    if len(matches) != 13:
        raise LiveHistoryStorageError("保存済みの13試合を確認できません。")
    expected_numbers = set(target_source_match_numbers(plan.target))
    plan_numbers = {
        item.analysis.prediction.source_match_number for item in plan.recommendations
    }
    if plan_numbers != expected_numbers:
        raise LiveHistoryValidationError("買い目の対象試合が不正です。")
    by_number = {
        _strict_positive_int(row["toto_match_number"], "試合番号"): row
        for _, row in matches.iterrows()
    }
    for item in plan.recommendations:
        prediction = item.analysis.prediction
        stored = by_number.get(prediction.source_match_number)
        if stored is None:
            raise LiveHistoryValidationError("買い目と保存済み予測が一致しません。")
        stored_probabilities = (
            float(stored["probability_1"]),
            float(stored["probability_0"]),
            float(stored["probability_2"]),
        )
        plan_probabilities = (
            prediction.probability_1,
            prediction.probability_0,
            prediction.probability_2,
        )
        if any(
            not math.isclose(
                left,
                right,
                rel_tol=0.0,
                abs_tol=BET_PLAN_PROBABILITY_TOLERANCE,
            )
            for left, right in zip(stored_probabilities, plan_probabilities)
        ):
            raise LiveHistoryValidationError(
                "買い目は別の予測runから生成されています。再度最適化してください。"
            )
        if (
            str(stored["home_team"]) != prediction.home_team
            or str(stored["away_team"]) != prediction.away_team
        ):
            raise LiveHistoryValidationError(
                "買い目は別の予測runから生成されています。再度最適化してください。"
            )


def _evaluate_bet_row(
    row: pd.Series,
    actuals: Mapping[int, str],
    payouts: Optional[tuple[int, int, int]],
) -> dict[str, Any]:
    try:
        selections = json.loads(str(row["selections_json"]))
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise LiveHistoryStorageError("買い目JSONが破損しています。") from error
    if not isinstance(selections, list):
        raise LiveHistoryStorageError("買い目JSONが不正です。")
    covered = 0
    selected_by_number: dict[int, tuple[str, ...]] = {}
    for item in selections:
        if not isinstance(item, Mapping):
            raise LiveHistoryStorageError("買い目JSONが不正です。")
        number = _strict_positive_int(item.get("source_match_number"), "試合番号")
        outcomes = tuple(str(value) for value in item.get("outcomes", ()))
        if not outcomes or any(value not in TOTO_OUTCOMES for value in outcomes):
            raise LiveHistoryStorageError("買い目JSONの結果が不正です。")
        selected_by_number[number] = outcomes
        covered += int(actuals.get(number) in outcomes)
    all_covered = covered == len(selected_by_number)
    target = str(row["target"])
    winning_rank = ""
    winning_ticket_count: Optional[int] = 0
    simulated_return: Optional[int] = None
    actual_return: Optional[int] = None
    if target == "toto":
        hit_counts = _ticket_hit_counts(selected_by_number, actuals)
        if hit_counts.get(13, 0):
            winning_rank = "1等"
        elif hit_counts.get(12, 0):
            winning_rank = "2等"
        elif hit_counts.get(11, 0):
            winning_rank = "3等"
        winning_ticket_count = sum(hit_counts.get(hits, 0) for hits in (13, 12, 11))
        if payouts is not None:
            calculated_return = sum(
                hit_counts.get(hits, 0) * prize
                for hits, prize in zip((13, 12, 11), payouts)
            )
            if _as_bool(row["recommended"]):
                simulated_return = calculated_return
            if _as_bool(row["purchased"]):
                actual_return = calculated_return
    else:
        winning_rank = "的中" if all_covered else ""
        winning_ticket_count = 1 if all_covered else 0

    simulated_amount = _optional_int(row["planned_purchase_amount_yen"])
    actual_amount = _optional_int(row["actual_purchase_amount_yen"])
    simulation_profit = (
        simulated_return - simulated_amount
        if simulated_return is not None and simulated_amount is not None
        else None
    )
    actual_profit = (
        actual_return - actual_amount
        if actual_return is not None and actual_amount is not None
        else None
    )
    simulation_roi = (
        simulated_return / simulated_amount
        if simulated_return is not None and simulated_amount not in (None, 0)
        else None
    )
    actual_roi = (
        actual_return / actual_amount
        if actual_return is not None and actual_amount not in (None, 0)
        else None
    )
    return {
        "covered_match_count": covered,
        "all_matches_covered": all_covered,
        "winning_rank": winning_rank,
        "winning_ticket_count": winning_ticket_count,
        "simulation_return_yen": simulated_return,
        "actual_return_yen": actual_return,
        "simulation_profit_yen": simulation_profit,
        "actual_profit_yen": actual_profit,
        "simulation_roi": simulation_roi,
        "actual_roi": actual_roi,
    }


def _ticket_hit_counts(
    selected_by_number: Mapping[int, Sequence[str]],
    actuals: Mapping[int, str],
) -> dict[int, int]:
    counts = {0: 1}
    for number in range(1, 14):
        outcomes = selected_by_number.get(number)
        if not outcomes:
            raise LiveHistoryStorageError("toto買い目に13試合が揃っていません。")
        correct_count = sum(outcome == actuals[number] for outcome in outcomes)
        incorrect_count = len(outcomes) - correct_count
        next_counts: dict[int, int] = {}
        for hits, ticket_count in counts.items():
            next_counts[hits + 1] = (
                next_counts.get(hits + 1, 0) + ticket_count * correct_count
            )
            next_counts[hits] = (
                next_counts.get(hits, 0) + ticket_count * incorrect_count
            )
        counts = next_counts
    return counts


def _round_payouts(row: pd.Series) -> Optional[tuple[int, int, int]]:
    values = tuple(
        _optional_int(row[column])
        for column in ("first_prize_yen", "second_prize_yen", "third_prize_yen")
    )
    if any(value is None for value in values) or not values[0]:
        return None
    return int(values[0]), int(values[1]), int(values[2])


def _strict_probability(value: Any, name: str) -> float:
    number = _optional_finite(value)
    if number is None:
        raise LiveHistoryValidationError(f"{name}を確認できません。")
    if number > 1.0:
        number /= 100.0
    if not 0.0 <= number <= 1.0:
        raise LiveHistoryValidationError(f"{name}は0～100%で指定してください。")
    return number


def _optional_finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _strict_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise LiveHistoryValidationError(f"{name}が不正です。")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise LiveHistoryValidationError(f"{name}が不正です。") from error
    if not math.isfinite(number) or not number.is_integer() or number <= 0:
        raise LiveHistoryValidationError(f"{name}が不正です。")
    return int(number)


def _optional_int(value: Any) -> Optional[int]:
    number = _optional_finite(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _strict_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no", ""):
            return False
    if value in (0, 1):
        return bool(value)
    raise LiveHistoryValidationError("bool項目が不正です。")


def _as_bool(value: Any) -> bool:
    try:
        return _strict_bool(value)
    except LiveHistoryValidationError:
        return False


def _optional_outcome(value: Any) -> str:
    text = str(value or "").strip()
    if text in TOTO_OUTCOMES:
        return text
    number = _optional_finite(value)
    if number is not None and number.is_integer() and str(int(number)) in TOTO_OUTCOMES:
        return str(int(number))
    return ""


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def _jst_iso(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise LiveHistoryValidationError("日時が不正です。")
    if value.tzinfo is None:
        value = value.replace(tzinfo=JAPAN_TIMEZONE)
    return value.astimezone(JAPAN_TIMEZONE).isoformat()


def _stable_json(value: Any, name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise LiveHistoryValidationError(f"{name}をJSONへ保存できません。") from error


def _immutable_hash(row: Mapping[str, Any], columns: Sequence[str]) -> str:
    # CSVへ書かれた後の文字列表現でも同じhashになるようscalarを文字列化する。
    payload = {
        column: str(_csv_value(row.get(column)))
        for column in columns
    }
    return hashlib.sha256(
        _stable_json(payload, "不変項目").encode("utf-8")
    ).hexdigest()


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    buffer = StringIO()
    frame.to_csv(buffer, index=False, lineterminator="\n")
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _assert_immutable_integrity(
    frame: pd.DataFrame,
    immutable_columns: Sequence[str],
    label: str,
) -> None:
    for _, row in frame.iterrows():
        expected = _immutable_hash(row, immutable_columns)
        if str(row.get("immutable_hash", "")) != expected:
            raise LiveHistoryConflictError(
                f"{label}の不変項目が変更されています。更新を中止しました。"
            )


__all__ = (
    "BET_COLUMNS",
    "LIVE_HISTORY_SCHEMA_VERSION",
    "MATCH_COLUMNS",
    "MATCH_IMMUTABLE_COLUMNS",
    "ROUND_COLUMNS",
    "ROUND_IMMUTABLE_COLUMNS",
    "LiveHistoryConflictError",
    "LiveHistoryError",
    "LiveHistoryManager",
    "LiveHistoryStorageError",
    "LiveHistoryValidationError",
    "ResultUpdateOutcome",
    "SavePredictionOutcome",
    "generate_prediction_run_id",
)
