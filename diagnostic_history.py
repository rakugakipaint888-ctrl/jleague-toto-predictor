"""Version8-B診断実行結果を専用CSVへ原子的に保存する。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from model_diagnostics import DiagnosticReport


DEFAULT_DIAGNOSTIC_HISTORY_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "history"
    / "model_diagnostic_history.csv"
)
DIAGNOSTIC_HISTORY_SCHEMA_VERSION = 1
DIAGNOSTIC_HISTORY_COLUMNS = (
    "schema_version",
    "diagnostic_id",
    "diagnosed_at",
    "period",
    "period_start",
    "period_end",
    "league",
    "prediction_version",
    "predicted_run_count",
    "confirmed_run_count",
    "purchased_run_count",
    "round_count",
    "match_count",
    "model_status",
    "status_reason",
    "accuracy",
    "brier_score",
    "log_loss",
    "calibration_error",
    "draw_precision",
    "draw_recall",
    "draw_f1",
    "draw_brier",
    "draw_calibration",
    "anomaly_count",
    "attention_count",
    "warning_count",
    "quality_issue_count",
    "excluded_match_count",
    "anomalies_json",
    "quality_issues_json",
    "thresholds_json",
    "immutable_hash",
)
DIAGNOSTIC_IMMUTABLE_COLUMNS = tuple(
    column for column in DIAGNOSTIC_HISTORY_COLUMNS if column != "immutable_hash"
)
_WRITE_LOCK = threading.RLock()


class DiagnosticHistoryError(RuntimeError):
    """診断履歴を安全に読書きできない。"""


@dataclass
class DiagnosticHistoryManager:
    path: Path = DEFAULT_DIAGNOSTIC_HISTORY_PATH
    warnings: list[str] = field(default_factory=list, init=False)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=DIAGNOSTIC_HISTORY_COLUMNS)
        try:
            frame = pd.read_csv(
                self.path,
                encoding="utf-8-sig",
                dtype=str,
                keep_default_na=False,
            ).astype("object")
        except (
            OSError,
            UnicodeError,
            pd.errors.ParserError,
            pd.errors.EmptyDataError,
        ) as error:
            self.warnings.append(f"診断履歴を読み込めません: {error}")
            return pd.DataFrame(columns=DIAGNOSTIC_HISTORY_COLUMNS)
        missing = [
            column
            for column in DIAGNOSTIC_HISTORY_COLUMNS
            if column not in frame.columns
        ]
        if missing:
            self.warnings.append(
                "診断履歴の必須列が不足しています: " + ", ".join(missing)
            )
            return pd.DataFrame(columns=DIAGNOSTIC_HISTORY_COLUMNS)
        valid_indexes = []
        for index, row in frame.iterrows():
            if str(row.get("immutable_hash", "")) == _row_hash(row):
                valid_indexes.append(index)
        invalid_count = len(frame) - len(valid_indexes)
        if invalid_count:
            self.warnings.append(
                f"診断履歴の不変hashが一致しない{invalid_count}行を除外しました。"
            )
        return (
            frame.loc[valid_indexes, list(DIAGNOSTIC_HISTORY_COLUMNS)]
            .sort_values(["diagnosed_at", "diagnostic_id"], ascending=False)
            .reset_index(drop=True)
        )

    def save(self, report: DiagnosticReport) -> bool:
        row = _report_row(report)
        with _WRITE_LOCK:
            current = self._read_strict()
            existing = current.loc[
                current["diagnostic_id"].astype(str) == report.diagnostic_id
            ]
            if not existing.empty:
                if len(existing) == 1 and str(
                    existing.iloc[0]["immutable_hash"]
                ) == str(row["immutable_hash"]):
                    return False
                raise DiagnosticHistoryError(
                    "同じdiagnostic_idに異なる診断結果が保存されています。"
                )
            next_frame = pd.concat(
                [
                    current,
                    pd.DataFrame([row], columns=DIAGNOSTIC_HISTORY_COLUMNS),
                ],
                ignore_index=True,
            )
            self._atomic_write(next_frame)
        return True

    def export_csv(self) -> bytes:
        return self.load().to_csv(
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        ).encode("utf-8-sig")

    def _read_strict(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=DIAGNOSTIC_HISTORY_COLUMNS)
        try:
            frame = pd.read_csv(
                self.path,
                encoding="utf-8-sig",
                dtype=str,
                keep_default_na=False,
            ).astype("object")
        except (
            OSError,
            UnicodeError,
            pd.errors.ParserError,
            pd.errors.EmptyDataError,
        ) as error:
            raise DiagnosticHistoryError(
                f"既存診断履歴を読み込めないため上書きしません: {error}"
            ) from error
        missing = [
            column
            for column in DIAGNOSTIC_HISTORY_COLUMNS
            if column not in frame.columns
        ]
        if missing:
            raise DiagnosticHistoryError(
                "既存診断履歴の必須列が不足しているため上書きしません: "
                + ", ".join(missing)
            )
        for _, row in frame.iterrows():
            if str(row.get("immutable_hash", "")) != _row_hash(row):
                raise DiagnosticHistoryError(
                    "既存診断履歴の不変hashが一致しないため上書きしません。"
                )
        return frame[list(DIAGNOSTIC_HISTORY_COLUMNS)].copy()

    def _atomic_write(self, frame: pd.DataFrame) -> None:
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            frame[list(DIAGNOSTIC_HISTORY_COLUMNS)].to_csv(
                temporary,
                index=False,
                encoding="utf-8-sig",
                lineterminator="\n",
            )
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, UnicodeError, TypeError, ValueError) as error:
            temporary.unlink(missing_ok=True)
            raise DiagnosticHistoryError(
                f"診断履歴を原子的に保存できません: {error}"
            ) from error


def _report_row(report: DiagnosticReport) -> dict[str, Any]:
    anomalies = [
        {
            "code": item.code,
            "category": item.category,
            "name": item.name,
            "level": item.level,
            "metric": item.metric,
            "current_value": item.current_value,
            "baseline_value": item.baseline_value,
            "difference": item.difference,
            "unit": item.unit,
            "judgement": item.judgement,
            "message": item.message,
        }
        for item in report.anomalies
    ]
    quality = [
        {
            "code": item.code,
            "name": item.name,
            "level": item.level,
            "count": item.count,
            "excluded_count": item.excluded_count,
            "message": item.message,
        }
        for item in report.quality_issues
    ]
    thresholds = {
        name: getattr(report.thresholds, name)
        for name in report.thresholds.__dataclass_fields__
    }
    row = {
        "schema_version": DIAGNOSTIC_HISTORY_SCHEMA_VERSION,
        "diagnostic_id": report.diagnostic_id,
        "diagnosed_at": report.diagnosed_at.isoformat(),
        "period": report.selection.period,
        "period_start": (
            report.selection.start_date.isoformat()
            if report.selection.start_date
            else ""
        ),
        "period_end": (
            report.selection.end_date.isoformat()
            if report.selection.end_date
            else ""
        ),
        "league": report.selection.league,
        "prediction_version": report.selection.version,
        "predicted_run_count": report.counts.predicted_run_count,
        "confirmed_run_count": report.counts.confirmed_run_count,
        "purchased_run_count": report.counts.purchased_run_count,
        "round_count": report.counts.round_count,
        "match_count": report.counts.match_count,
        "model_status": report.status,
        "status_reason": report.status_reason,
        "accuracy": _csv_value(
            report.overall.accuracy if report.overall else None
        ),
        "brier_score": _csv_value(
            report.overall.brier_score if report.overall else None
        ),
        "log_loss": _csv_value(
            report.overall.log_loss if report.overall else None
        ),
        "calibration_error": _csv_value(
            report.overall.calibration_error if report.overall else None
        ),
        "draw_precision": report.draw.precision,
        "draw_recall": report.draw.recall,
        "draw_f1": report.draw.f1_score,
        "draw_brier": _csv_value(report.draw.brier_score),
        "draw_calibration": _csv_value(report.draw.calibration_error),
        "anomaly_count": len(report.anomalies),
        "attention_count": sum(item.level == "注意" for item in report.anomalies),
        "warning_count": sum(item.level == "警告" for item in report.anomalies),
        "quality_issue_count": len(report.quality_issues),
        "excluded_match_count": report.excluded_match_count,
        "anomalies_json": _stable_json(anomalies),
        "quality_issues_json": _stable_json(quality),
        "thresholds_json": _stable_json(thresholds),
    }
    row["immutable_hash"] = _row_hash(row)
    return row


def _row_hash(row: Mapping[str, Any]) -> str:
    payload = {
        column: _hash_value(row.get(column, ""))
        for column in DIAGNOSTIC_IMMUTABLE_COLUMNS
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _hash_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else ""
    return str(value)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "DEFAULT_DIAGNOSTIC_HISTORY_PATH",
    "DIAGNOSTIC_HISTORY_COLUMNS",
    "DiagnosticHistoryError",
    "DiagnosticHistoryManager",
]
