"""Version4～Version6の予想履歴を開催回キーでCSV保存する。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from history_manager import TotoPayouts, TotoRound
from metrics import (
    DEFAULT_TOTO_STAKE_YEN,
    TOTO_OUTCOMES,
    evaluate_model,
    toto_payout_for_hits,
)


JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PREDICTION_HISTORY_PATH = (
    PROJECT_ROOT / "data" / "history" / "prediction_history.csv"
)

HISTORY_COLUMNS = (
    "toto_round",
    "toto_match_number",
    "prediction_version",
    "prediction_date",
    "home_team",
    "away_team",
    "prediction",
    "probability_1",
    "probability_0",
    "probability_2",
    "home_expected_goals",
    "away_expected_goals",
    "actual_result",
    "hit",
    "total_hits",
    "accuracy",
    "brier_score",
    "log_loss",
    "calibration",
    "expected_hits",
    "stake_yen",
    "payout_yen",
    "roi",
)


@dataclass(frozen=True)
class PredictionHistoryRecord:
    """履歴CSVの1試合・1Version分。確率は0～1で保存する。"""

    toto_round: int
    toto_match_number: int
    prediction_version: str
    prediction_date: str
    home_team: str
    away_team: str
    prediction: str
    probability_1: float
    probability_0: float
    probability_2: float
    home_expected_goals: float
    away_expected_goals: float
    actual_result: str = ""
    hit: Optional[bool] = None
    total_hits: Optional[int] = None
    accuracy: Optional[float] = None
    brier_score: Optional[float] = None
    log_loss: Optional[float] = None
    calibration: Optional[float] = None
    expected_hits: Optional[float] = None
    stake_yen: Optional[int] = None
    payout_yen: Optional[int] = None
    roi: Optional[float] = None


def _as_probability(value: Any) -> float:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return 1.0 / 3.0
    probability = float(number)
    if probability > 1.0:
        probability /= 100.0
    return min(1.0, max(0.0, probability))


def _as_float(value: Any, default: float = 0.0) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(number) else float(number)


def _as_toto_label(value: Any) -> str:
    """CSVで1/0/2が数値化されても正規ラベルへ戻す。"""

    if value is None:
        return ""
    text_value = str(value).strip()
    if text_value in TOTO_OUTCOMES:
        return text_value
    number = pd.to_numeric(value, errors="coerce")
    if not pd.isna(number) and float(number).is_integer():
        normalized = str(int(number))
        return normalized if normalized in TOTO_OUTCOMES else ""
    return ""


def _actual_results_by_number(toto_round: TotoRound) -> dict[int, str]:
    return {
        match.match_number: match.actual_result or ""
        for match in toto_round.matches
    }


def _expected_hits_for_rows(rows: pd.DataFrame) -> float:
    total = 0.0
    for _, row in rows.iterrows():
        prediction = str(row.get("prediction", ""))
        if prediction not in TOTO_OUTCOMES:
            continue
        total += _as_probability(row.get(f"probability_{prediction}"))
    return total


def finalize_prediction_results(result_df: pd.DataFrame) -> pd.DataFrame:
    """画面用13行へ実結果の一致・的中数・的中率を付与する。"""

    if not isinstance(result_df, pd.DataFrame) or result_df.empty:
        return pd.DataFrame() if result_df is None else result_df.copy()
    result = result_df.copy()
    if not {"actual_result", "本命"}.issubset(result.columns):
        return result
    actuals = result["actual_result"].fillna("").astype(str)
    predictions = result["本命"].fillna("").astype(str)
    result["hit"] = [
        prediction == actual if actual in TOTO_OUTCOMES else None
        for prediction, actual in zip(predictions, actuals)
    ]
    if len(result) == 13 and actuals.isin(TOTO_OUTCOMES).all():
        total_hits = int(pd.Series(result["hit"], dtype="bool").sum())
        result["total_hits"] = total_hits
        result["accuracy"] = total_hits / 13
    return result


def apply_round_metrics(
    frame: pd.DataFrame,
    payouts_by_round: Optional[Mapping[int, TotoPayouts]] = None,
) -> pd.DataFrame:
    """開催回・Versionごとの指標を各行へ反映する。"""

    if frame.empty:
        return frame.copy()

    result = frame.copy()
    # 未確定回をCSVから読むとhit列はfloat(NaN)になる。確定回のboolを
    # 後から安全に代入できるよう、開催回をまたいだ結合前提でobjectへそろえる。
    if "hit" not in result.columns:
        result["hit"] = None
    result["hit"] = result["hit"].astype("object")
    payouts_by_round = payouts_by_round or {}

    for (round_value, version), indexes in result.groupby(
        ["toto_round", "prediction_version"],
        dropna=False,
    ).groups.items():
        group = result.loc[indexes].sort_values("toto_match_number")
        expected_hits = _expected_hits_for_rows(group)
        result.loc[indexes, "expected_hits"] = expected_hits

        actuals = [str(value) for value in group["actual_result"].fillna("")]
        if len(group) != 13 or not all(
            actual in TOTO_OUTCOMES for actual in actuals
        ):
            continue

        predictions = [str(value) for value in group["prediction"]]
        probabilities = [
            {
                "1": _as_probability(row.get("probability_1")),
                "0": _as_probability(row.get("probability_0")),
                "2": _as_probability(row.get("probability_2")),
            }
            for _, row in group.iterrows()
        ]
        hit_count = sum(
            prediction == actual
            for prediction, actual in zip(predictions, actuals)
        )
        try:
            round_id = int(round_value)
        except (TypeError, ValueError):
            round_id = 0
        payouts = payouts_by_round.get(round_id)
        if payouts is not None:
            payout_yen = toto_payout_for_hits(
                hit_count,
                payouts.first_prize_yen,
                payouts.second_prize_yen,
                payouts.third_prize_yen,
            )
        else:
            stored_payout = pd.to_numeric(
                group.iloc[0].get("payout_yen"), errors="coerce"
            )
            payout_yen = (
                int(stored_payout) if not pd.isna(stored_payout) else 0
            )
        stored_stake = pd.to_numeric(
            group.iloc[0].get("stake_yen"), errors="coerce"
        )
        metrics = evaluate_model(
            predictions,
            probabilities,
            actuals,
            stake_yen=(
                int(stored_stake)
                if not pd.isna(stored_stake)
                else DEFAULT_TOTO_STAKE_YEN
            ),
            payout_yen=payout_yen,
        )

        result.loc[indexes, "hit"] = [
            prediction == actual
            for prediction, actual in zip(predictions, actuals)
        ]
        result.loc[indexes, "total_hits"] = metrics.hit_count
        result.loc[indexes, "accuracy"] = metrics.accuracy
        result.loc[indexes, "brier_score"] = metrics.brier_score
        result.loc[indexes, "log_loss"] = metrics.log_loss
        result.loc[indexes, "calibration"] = metrics.calibration_error
        result.loc[indexes, "expected_hits"] = metrics.expected_hits
        result.loc[indexes, "stake_yen"] = metrics.stake_yen
        result.loc[indexes, "payout_yen"] = metrics.payout_yen
        result.loc[indexes, "roi"] = metrics.roi

    return result


def records_from_prediction_results(
    result_df: pd.DataFrame,
    toto_round: TotoRound,
    prediction_date: Optional[datetime] = None,
) -> list[PredictionHistoryRecord]:
    """画面の13行結果をVersion4・5・6の39履歴行へ変換する。"""

    if not isinstance(result_df, pd.DataFrame) or result_df.empty:
        return []

    prediction_time = prediction_date or datetime.now(JAPAN_TIMEZONE)
    if prediction_time.tzinfo is None:
        prediction_time = prediction_time.replace(tzinfo=JAPAN_TIMEZONE)
    prediction_time_text = prediction_time.astimezone(
        JAPAN_TIMEZONE
    ).isoformat()
    actuals = _actual_results_by_number(toto_round)
    records = []

    version_fields = {
        "Version4": {
            "prediction": "version4_prediction",
            "probability_1": "version4_home_win",
            "probability_0": "version4_draw",
            "probability_2": "version4_away_win",
            "home_expected": "home_expected_before_version5",
            "away_expected": "away_expected_before_version5",
        },
        "Version5": {
            "prediction": "version5_prediction",
            "probability_1": "1",
            "probability_0": "0",
            "probability_2": "2",
            "home_expected": "home_expected_after_version5",
            "away_expected": "away_expected_after_version5",
        },
        # Version6は評価基盤の追加で、予測式はVersion5をそのまま使用する。
        "Version6": {
            "prediction": "version6_prediction",
            "probability_1": "version6_home_win",
            "probability_0": "version6_draw",
            "probability_2": "version6_away_win",
            "home_expected": "home_expected_after_version6",
            "away_expected": "away_expected_after_version6",
        },
    }

    order_column = (
        "toto_match_number"
        if "toto_match_number" in result_df.columns
        else "試合"
    )
    for _, row in result_df.sort_values(order_column).iterrows():
        match_number = int(
            pd.to_numeric(row.get("toto_match_number", row.get("試合")))
        )
        card = str(row.get("対戦カード", ""))
        home_team, _, away_team = card.partition(" vs ")

        for version, fields in version_fields.items():
            prediction = str(
                row.get(
                    fields["prediction"],
                    row.get("version5_prediction", row.get("本命", "")),
                )
            )
            probabilities = {
                outcome: _as_probability(
                    row.get(
                        fields[f"probability_{outcome}"],
                        row.get(outcome),
                    )
                )
                for outcome in TOTO_OUTCOMES
            }
            actual_result = actuals.get(match_number, "")
            records.append(
                PredictionHistoryRecord(
                    toto_round=toto_round.round_id,
                    toto_match_number=match_number,
                    prediction_version=version,
                    prediction_date=prediction_time_text,
                    home_team=home_team,
                    away_team=away_team,
                    prediction=prediction,
                    probability_1=probabilities["1"],
                    probability_0=probabilities["0"],
                    probability_2=probabilities["2"],
                    home_expected_goals=_as_float(
                        row.get(
                            fields["home_expected"],
                            row.get("home_expected_after_version5"),
                        )
                    ),
                    away_expected_goals=_as_float(
                        row.get(
                            fields["away_expected"],
                            row.get("away_expected_after_version5"),
                        )
                    ),
                    actual_result=actual_result,
                    hit=(
                        prediction == actual_result
                        if actual_result in TOTO_OUTCOMES
                        else None
                    ),
                )
            )

    frame = apply_round_metrics(
        pd.DataFrame(asdict(record) for record in records),
        payouts_by_round={toto_round.round_id: toto_round.payouts},
    )
    return [
        PredictionHistoryRecord(
            **{
                column: (
                    None
                    if pd.isna(row.get(column))
                    else row.get(column)
                )
                for column in HISTORY_COLUMNS
            }
        )
        for _, row in frame.iterrows()
    ]


@dataclass
class PredictionHistoryManager:
    """履歴CSVの読込、開催回単位の更新、実結果照合を行う。"""

    path: Path = DEFAULT_PREDICTION_HISTORY_PATH

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=HISTORY_COLUMNS)
        try:
            frame = pd.read_csv(self.path, encoding="utf-8-sig")
        except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
            return pd.DataFrame(columns=HISTORY_COLUMNS)
        for column in HISTORY_COLUMNS:
            if column not in frame.columns:
                frame[column] = None
        for text_column in (
            "prediction_version",
            "prediction_date",
            "home_team",
            "away_team",
        ):
            frame[text_column] = (
                frame[text_column].fillna("").astype(str)
            )
        frame["prediction"] = frame["prediction"].map(_as_toto_label)
        frame["actual_result"] = frame["actual_result"].map(_as_toto_label)
        return frame[list(HISTORY_COLUMNS)].copy()

    def save_records(
        self,
        records: Iterable[PredictionHistoryRecord | Mapping[str, Any]],
        *,
        payouts_by_round: Optional[Mapping[int, TotoPayouts]] = None,
    ) -> bool:
        new_rows = []
        for record in records:
            row = asdict(record) if isinstance(record, PredictionHistoryRecord) else dict(record)
            new_rows.append(row)
        if not new_rows:
            return False
        try:
            existing = self.load()
            additions = pd.DataFrame(new_rows)
            for column in HISTORY_COLUMNS:
                if column not in additions.columns:
                    additions[column] = None

            # 同一開催回・試合・Versionは最新予想へ置換し、分析の二重計上を防ぐ。
            keys = {
                (
                    int(pd.to_numeric(row.get("toto_round"))),
                    int(pd.to_numeric(row.get("toto_match_number"))),
                    str(row.get("prediction_version")),
                )
                for _, row in additions.iterrows()
            }
            if not existing.empty:
                keep = []
                for _, row in existing.iterrows():
                    try:
                        key = (
                            int(pd.to_numeric(row.get("toto_round"))),
                            int(pd.to_numeric(row.get("toto_match_number"))),
                            str(row.get("prediction_version")),
                        )
                    except (TypeError, ValueError):
                        keep.append(True)
                        continue
                    keep.append(key not in keys)
                existing = existing.loc[keep]

            combined = pd.concat(
                [existing, additions[list(HISTORY_COLUMNS)]],
                ignore_index=True,
            )
            combined = apply_round_metrics(combined, payouts_by_round)
            combined = combined.sort_values(
                ["toto_round", "prediction_version", "toto_match_number"],
                ascending=[False, True, True],
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_suffix(".tmp")
            combined.to_csv(
                temporary_path,
                index=False,
                encoding="utf-8-sig",
            )
            temporary_path.replace(self.path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def save_prediction_results(
        self,
        result_df: pd.DataFrame,
        toto_round: TotoRound,
        prediction_date: Optional[datetime] = None,
    ) -> bool:
        records = records_from_prediction_results(
            result_df,
            toto_round,
            prediction_date,
        )
        return self.save_records(
            records,
            payouts_by_round={toto_round.round_id: toto_round.payouts},
        )

    def reconcile_actual_results(self, toto_round: TotoRound) -> bool:
        """保存済み予想へ公式実結果を付与し、指標を再計算する。"""

        frame = self.load()
        if frame.empty or not toto_round.is_complete:
            return False
        frame["actual_result"] = frame["actual_result"].astype("object")
        frame["hit"] = frame["hit"].astype("object")
        round_numbers = pd.to_numeric(frame["toto_round"], errors="coerce")
        selected_mask = round_numbers == toto_round.round_id
        if not selected_mask.any():
            return False
        actuals = _actual_results_by_number(toto_round)
        for index in frame.index[selected_mask]:
            match_number = int(
                pd.to_numeric(frame.at[index, "toto_match_number"])
            )
            actual = actuals.get(match_number, "")
            prediction = str(frame.at[index, "prediction"])
            frame.at[index, "actual_result"] = actual
            frame.at[index, "hit"] = (
                prediction == actual if actual in TOTO_OUTCOMES else None
            )
        frame = apply_round_metrics(
            frame,
            payouts_by_round={toto_round.round_id: toto_round.payouts},
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_suffix(".tmp")
            frame.to_csv(
                temporary_path,
                index=False,
                encoding="utf-8-sig",
            )
            temporary_path.replace(self.path)
            return True
        except OSError:
            return False


def history_csv_bytes(frame: pd.DataFrame) -> bytes:
    """履歴DataFrameをダウンロード用UTF-8 BOM付きCSVへ変換する。"""

    buffer = StringIO()
    frame.to_csv(buffer, index=False)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
