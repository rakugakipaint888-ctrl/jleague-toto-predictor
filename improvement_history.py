"""Version8-C改善提案結果を専用CSVへ不変・原子的に保存する。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from improvement_recommendations import ImprovementReport


DEFAULT_IMPROVEMENT_HISTORY_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "history"
    / "model_improvement_history.csv"
)
IMPROVEMENT_HISTORY_SCHEMA_VERSION = 1
IMPROVEMENT_HISTORY_COLUMNS = (
    "schema_version",
    "improvement_id",
    "generated_at",
    "diagnostic_id",
    "diagnostic_status",
    "period",
    "period_start",
    "period_end",
    "league",
    "prediction_version",
    "match_count",
    "round_count",
    "data_sufficient",
    "detected_problems_json",
    "recommendation_count",
    "proposal_categories_json",
    "recommendations_json",
    "priority_counts_json",
    "confidence_counts_json",
    "reoptimization_level",
    "reoptimization_reason",
    "recommended_action",
    "notice",
    "text_mode",
    "immutable_hash",
)
IMPROVEMENT_IMMUTABLE_COLUMNS = tuple(
    column for column in IMPROVEMENT_HISTORY_COLUMNS if column != "immutable_hash"
)
_WRITE_LOCK = threading.RLock()


class ImprovementHistoryError(RuntimeError):
    """改善提案履歴を安全に読み書きできない。"""


@dataclass
class ImprovementHistoryManager:
    path: Path = DEFAULT_IMPROVEMENT_HISTORY_PATH
    warnings: list[str] = field(default_factory=list, init=False)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=IMPROVEMENT_HISTORY_COLUMNS)
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
            self.warnings.append(f"改善提案履歴を読み込めません: {error}")
            return pd.DataFrame(columns=IMPROVEMENT_HISTORY_COLUMNS)
        missing = [
            column
            for column in IMPROVEMENT_HISTORY_COLUMNS
            if column not in frame.columns
        ]
        if missing:
            self.warnings.append(
                "改善提案履歴の必須列が不足しています: " + ", ".join(missing)
            )
            return pd.DataFrame(columns=IMPROVEMENT_HISTORY_COLUMNS)
        valid_indexes = [
            index
            for index, row in frame.iterrows()
            if str(row.get("immutable_hash", "")) == _row_hash(row)
        ]
        invalid_count = len(frame) - len(valid_indexes)
        if invalid_count:
            self.warnings.append(
                f"改善提案履歴の不変hashが一致しない{invalid_count}行を除外しました。"
            )
        return (
            frame.loc[valid_indexes, list(IMPROVEMENT_HISTORY_COLUMNS)]
            .sort_values(["generated_at", "improvement_id"], ascending=False)
            .reset_index(drop=True)
        )

    def save(self, report: ImprovementReport) -> bool:
        row = _report_row(report)
        with _WRITE_LOCK:
            current = self._read_strict()
            existing = current.loc[
                current["improvement_id"].astype(str) == report.improvement_id
            ]
            if not existing.empty:
                if len(existing) == 1 and str(
                    existing.iloc[0]["immutable_hash"]
                ) == str(row["immutable_hash"]):
                    return False
                raise ImprovementHistoryError(
                    "同じimprovement_idに異なる提案結果が保存されています。"
                )
            next_frame = pd.concat(
                [
                    current,
                    pd.DataFrame([row], columns=IMPROVEMENT_HISTORY_COLUMNS),
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
            return pd.DataFrame(columns=IMPROVEMENT_HISTORY_COLUMNS)
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
            raise ImprovementHistoryError(
                f"既存改善提案履歴を読み込めないため上書きしません: {error}"
            ) from error
        missing = [
            column
            for column in IMPROVEMENT_HISTORY_COLUMNS
            if column not in frame.columns
        ]
        if missing:
            raise ImprovementHistoryError(
                "既存改善提案履歴の必須列が不足しているため上書きしません: "
                + ", ".join(missing)
            )
        for _, row in frame.iterrows():
            if str(row.get("immutable_hash", "")) != _row_hash(row):
                raise ImprovementHistoryError(
                    "既存改善提案履歴の不変hashが一致しないため上書きしません。"
                )
        return frame[list(IMPROVEMENT_HISTORY_COLUMNS)].copy()

    def _atomic_write(self, frame: pd.DataFrame) -> None:
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            frame[list(IMPROVEMENT_HISTORY_COLUMNS)].to_csv(
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
            raise ImprovementHistoryError(
                f"改善提案履歴を原子的に保存できません: {error}"
            ) from error


def _report_row(report: ImprovementReport) -> dict[str, Any]:
    recommendations = []
    for item in report.recommendations:
        recommendations.append(
            {
                "rank": item.rank,
                "code": item.code,
                "category": item.category,
                "related_categories": list(item.related_categories),
                "title": item.title,
                "evidence": [
                    {
                        "metric": evidence.metric,
                        "current_value": evidence.current_value,
                        "baseline_value": evidence.baseline_value,
                        "difference": evidence.difference,
                        "unit": evidence.unit,
                        "source": evidence.source,
                    }
                    for evidence in item.evidence
                ],
                "diagnosis": item.diagnosis,
                "possible_causes": list(item.possible_causes),
                "improvement_candidates": list(item.improvement_candidates),
                "recommended_action": item.recommended_action,
                "priority": item.priority,
                "priority_reason": item.priority_reason,
                "confidence": item.confidence,
                "confidence_reason": item.confidence_reason,
                "anomaly_codes": list(item.anomaly_codes),
                "narrative": item.narrative,
            }
        )
    priorities = {
        level: sum(item.priority == level for item in report.recommendations)
        for level in ("高", "中", "低")
    }
    confidences = {
        level: sum(item.confidence == level for item in report.recommendations)
        for level in ("高", "中", "低")
    }
    row = {
        "schema_version": IMPROVEMENT_HISTORY_SCHEMA_VERSION,
        "improvement_id": report.improvement_id,
        "generated_at": report.generated_at.isoformat(),
        "diagnostic_id": report.diagnostic_id,
        "diagnostic_status": report.diagnostic_status,
        "period": report.period,
        "period_start": report.period_start,
        "period_end": report.period_end,
        "league": report.league,
        "prediction_version": report.prediction_version,
        "match_count": report.match_count,
        "round_count": report.round_count,
        "data_sufficient": str(report.data_sufficient).lower(),
        "detected_problems_json": _stable_json(report.detected_problems),
        "recommendation_count": len(report.recommendations),
        "proposal_categories_json": _stable_json(
            list(dict.fromkeys(item.category for item in report.recommendations))
        ),
        "recommendations_json": _stable_json(recommendations),
        "priority_counts_json": _stable_json(priorities),
        "confidence_counts_json": _stable_json(confidences),
        "reoptimization_level": report.reoptimization_level,
        "reoptimization_reason": report.reoptimization_reason,
        "recommended_action": report.recommended_action,
        "notice": report.notice,
        "text_mode": report.text_mode,
    }
    row["immutable_hash"] = _row_hash(row)
    return row


def _row_hash(row: Mapping[str, Any]) -> str:
    payload = {
        column: _hash_value(row.get(column, ""))
        for column in IMPROVEMENT_IMMUTABLE_COLUMNS
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _hash_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else ""
    return str(value)


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "DEFAULT_IMPROVEMENT_HISTORY_PATH",
    "IMPROVEMENT_HISTORY_COLUMNS",
    "ImprovementHistoryError",
    "ImprovementHistoryManager",
]
