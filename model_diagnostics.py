"""Version8-A実戦履歴だけを使うVersion8-Bモデル診断。

現在モデルによる過去予測の再生成、設定変更、最適化、改善提案は行わない。
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from diagnostic_config import (
    DEFAULT_DIAGNOSTIC_THRESHOLDS,
    LEAGUE_OPTIONS,
    PERIOD_OPTIONS,
    DiagnosticThresholds,
)
from draw_evaluation import evaluate_draw_predictions
from history_manager import JAPAN_TIMEZONE
from live_history import (
    BET_COLUMNS,
    MATCH_COLUMNS,
    ROUND_COLUMNS,
    LiveHistoryManager,
)
from metrics import (
    TOTO_OUTCOMES,
    ModelMetrics,
    OneVsRestMetrics,
    evaluate_model,
    evaluate_one_vs_rest,
    normalize_toto_outcome,
)


ALL_VERSIONS = "全Version"
CONFIRMED_STATUSES = ("result_confirmed", "evaluated")
PENDING_STATUSES = ("predicted", "purchased", "pending_result")
RECENT_PERIOD_ROUNDS = {
    "直近5開催": 5,
    "直近10開催": 10,
    "直近20開催": 20,
}
COVERAGE_BANDS = (
    (0.00, 0.01),
    (0.01, 0.05),
    (0.05, 0.10),
    (0.10, 1.00),
)


@dataclass(frozen=True)
class DiagnosticFilter:
    period: str = "全実戦履歴"
    league: str = "全リーグ"
    version: str = ALL_VERSIONS
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    def __post_init__(self) -> None:
        if self.period not in PERIOD_OPTIONS:
            raise ValueError("診断対象期間が不正です。")
        if self.league not in LEAGUE_OPTIONS:
            raise ValueError("診断対象リーグが不正です。")
        if self.period == "任意期間":
            if self.start_date is None or self.end_date is None:
                raise ValueError("任意期間の開始日と終了日が必要です。")
            if self.start_date > self.end_date:
                raise ValueError("任意期間の開始日は終了日以前にしてください。")


@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    name: str
    level: str
    count: int
    excluded_count: int
    message: str


@dataclass(frozen=True)
class DiagnosticAnomaly:
    code: str
    category: str
    name: str
    level: str
    metric: str
    current_value: Optional[float]
    baseline_value: Optional[float]
    difference: Optional[float]
    unit: str
    judgement: str
    message: str


@dataclass(frozen=True)
class DiagnosticCounts:
    predicted_run_count: int = 0
    pending_run_count: int = 0
    confirmed_run_count: int = 0
    evaluated_run_count: int = 0
    purchased_run_count: int = 0
    unpurchased_run_count: int = 0
    round_count: int = 0
    match_count: int = 0


@dataclass(frozen=True)
class DrawDiagnostic:
    actual_draw_rate: Optional[float] = None
    favorite_draw_rate: Optional[float] = None
    candidate_rate: Optional[float] = None
    actual_draw_count: int = 0
    favorite_draw_hit_count: int = 0
    candidate_hit_count: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    brier_score: Optional[float] = None
    calibration_error: Optional[float] = None
    mean_probability_0: Optional[float] = None
    probability_actual_gap: Optional[float] = None
    recommended_draw_inclusion_rate: Optional[float] = None
    purchased_draw_inclusion_rate: Optional[float] = None
    recommended_draw_covered_count: int = 0
    purchased_draw_covered_count: int = 0
    draw_inclusion_score_mean: Optional[float] = None


@dataclass
class DiagnosticReport:
    diagnostic_id: str
    diagnosed_at: datetime
    selection: DiagnosticFilter
    thresholds: DiagnosticThresholds
    status: str
    status_reason: str
    data_sufficient: bool
    counts: DiagnosticCounts
    overall: Optional[ModelMetrics]
    average_predicted_probability: Optional[float]
    average_max_probability: Optional[float]
    class_metrics: Mapping[str, OneVsRestMetrics]
    draw: DrawDiagnostic
    anomalies: tuple[DiagnosticAnomaly, ...]
    quality_issues: tuple[DataQualityIssue, ...]
    excluded_match_count: int
    period_shortage: bool
    period_available_round_count: int
    calibration_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    timeline: pd.DataFrame = field(default_factory=pd.DataFrame)
    rolling_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    league_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    version_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    settings_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    bet_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    purchase_performance: Mapping[str, Any] = field(default_factory=dict)
    simulation_performance: Mapping[str, Any] = field(default_factory=dict)
    coverage_summary: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def has_enough_data(self) -> bool:
        return self.data_sufficient


@dataclass
class _DiagnosticData:
    rounds: pd.DataFrame
    matches: pd.DataFrame
    bets: pd.DataFrame
    raw_rounds: pd.DataFrame
    raw_matches: pd.DataFrame
    raw_bets: pd.DataFrame
    quality_issues: list[DataQualityIssue]
    excluded_match_count: int


def run_model_diagnostics(
    manager: LiveHistoryManager,
    selection: Optional[DiagnosticFilter] = None,
    *,
    thresholds: DiagnosticThresholds = DEFAULT_DIAGNOSTIC_THRESHOLDS,
    diagnosed_at: Optional[datetime] = None,
) -> DiagnosticReport:
    """保存済み実戦履歴を読み取り、説明可能な診断結果を返す。"""

    selected_filter = selection or DiagnosticFilter()
    now = diagnosed_at or datetime.now(JAPAN_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JAPAN_TIMEZONE)
    data = _load_diagnostic_data(manager, thresholds)

    selected_rounds, period_shortage, available_rounds = _filter_rounds(
        data.rounds,
        selected_filter,
        now=now,
    )
    selected_matches = data.matches.loc[
        data.matches["prediction_run_id"].astype(str).isin(
            set(selected_rounds["prediction_run_id"].astype(str))
        )
    ].copy()
    period_version_rounds = selected_rounds.copy()
    period_version_matches = selected_matches.copy()
    if selected_filter.league != "全リーグ":
        selected_matches = selected_matches.loc[
            selected_matches["league"].astype(str) == selected_filter.league
        ].copy()
        league_run_ids = set(selected_matches["prediction_run_id"].astype(str))
        selected_rounds = selected_rounds.loc[
            selected_rounds["prediction_run_id"].astype(str).isin(league_run_ids)
        ].copy()
    selected_run_ids = set(selected_rounds["prediction_run_id"].astype(str))
    selected_bets = data.bets.loc[
        data.bets["prediction_run_id"].astype(str).isin(selected_run_ids)
    ].copy()

    confirmed_ids = _confirmed_run_ids(selected_rounds, data.matches)
    evaluated_matches = selected_matches.loc[
        selected_matches["prediction_run_id"].astype(str).isin(confirmed_ids)
    ].copy()
    predictions, probabilities, actuals, candidates = _metric_inputs(
        evaluated_matches
    )
    overall = (
        evaluate_model(predictions, probabilities, actuals)
        if predictions
        else None
    )
    class_metrics = {
        outcome: evaluate_one_vs_rest(
            predictions,
            probabilities,
            actuals,
            outcome=outcome,
        )
        for outcome in TOTO_OUTCOMES
    }
    average_predicted_probability = (
        sum(
            probability[prediction]
            for prediction, probability in zip(predictions, probabilities)
        )
        / len(predictions)
        if predictions
        else None
    )
    average_max_probability = (
        sum(max(probability.values()) for probability in probabilities)
        / len(probabilities)
        if probabilities
        else None
    )
    draw_evaluation = (
        evaluate_draw_predictions(
            predictions,
            probabilities,
            actuals,
            candidate_flags=candidates,
        )
        if predictions
        else None
    )
    bet_draw = _draw_bet_statistics(selected_bets, evaluated_matches)
    draw = _draw_diagnostic(
        class_metrics["0"],
        draw_evaluation,
        candidates,
        actuals,
        bet_draw,
    )

    purchased_run_ids = set(
        selected_bets.loc[
            selected_bets["record_type"].astype(str) == "purchased",
            "prediction_run_id",
        ].astype(str)
    )
    counts = DiagnosticCounts(
        predicted_run_count=len(selected_rounds),
        pending_run_count=int(
            selected_rounds["round_status"].astype(str).isin(PENDING_STATUSES).sum()
        ),
        confirmed_run_count=len(confirmed_ids),
        evaluated_run_count=int(
            selected_rounds.loc[
                selected_rounds["prediction_run_id"].astype(str).isin(confirmed_ids),
                "round_status",
            ].astype(str).eq("evaluated").sum()
        ),
        purchased_run_count=len(purchased_run_ids),
        unpurchased_run_count=max(0, len(selected_rounds) - len(purchased_run_ids)),
        round_count=int(
            evaluated_matches["round_id"].astype(str).nunique()
            if not evaluated_matches.empty
            else 0
        ),
        match_count=len(evaluated_matches),
    )

    calibration_table = _calibration_table(class_metrics)
    comparison_rounds = _filter_version_only(data.rounds, selected_filter.version)
    comparison_matches = data.matches.loc[
        data.matches["prediction_run_id"].astype(str).isin(
            set(comparison_rounds["prediction_run_id"].astype(str))
        )
    ].copy()
    if selected_filter.league != "全リーグ":
        comparison_matches = comparison_matches.loc[
            comparison_matches["league"].astype(str) == selected_filter.league
        ].copy()
    timeline = _timeline(selected_rounds, selected_matches)
    rolling_summary = _rolling_summary(
        comparison_rounds,
        comparison_matches,
        thresholds,
    )
    league_groups = (
        ("J1", "J2", "J3")
        if selected_filter.league == "全リーグ"
        else (selected_filter.league,)
    )
    league_summary = _group_summary(
        period_version_rounds,
        period_version_matches,
        group_column="league",
        groups=league_groups,
        thresholds=thresholds,
    )
    version_period_filter = DiagnosticFilter(
        period=selected_filter.period,
        league=selected_filter.league,
        version=ALL_VERSIONS,
        start_date=selected_filter.start_date,
        end_date=selected_filter.end_date,
    )
    version_rounds, _, _ = _filter_rounds(
        data.rounds,
        version_period_filter,
        now=now,
    )
    version_matches = data.matches.loc[
        data.matches["prediction_run_id"].astype(str).isin(
            set(version_rounds["prediction_run_id"].astype(str))
        )
    ].copy()
    if selected_filter.league != "全リーグ":
        version_matches = version_matches.loc[
            version_matches["league"].astype(str) == selected_filter.league
        ].copy()
    version_values = tuple(
        sorted(
            value
            for value in version_rounds["prediction_version"].astype(str).unique()
            if value
            and (
                selected_filter.version == ALL_VERSIONS
                or value == selected_filter.version
            )
        )
    )
    version_summary = _group_summary(
        version_rounds,
        version_matches,
        group_column="prediction_version",
        groups=version_values,
        thresholds=thresholds,
    )
    settings_summary = _settings_group_summary(
        selected_rounds,
        selected_matches,
        thresholds,
    )
    (
        bet_summary,
        purchase_performance,
        simulation_performance,
        coverage_summary,
    ) = _bet_diagnostics(
        selected_bets,
        confirmed_run_ids=confirmed_ids,
        thresholds=thresholds,
    )

    enough_data = (
        counts.match_count >= thresholds.minimum_match_count
        and counts.round_count >= thresholds.minimum_round_count
        and not period_shortage
    )
    anomalies = _detect_anomalies(
        overall=overall,
        class_metrics=class_metrics,
        draw=draw,
        evaluated_matches=evaluated_matches,
        rolling_summary=rolling_summary,
        league_summary=league_summary,
        quality_issues=data.quality_issues,
        enough_data=enough_data,
        thresholds=thresholds,
    )
    status, reason = _overall_status(
        anomalies,
        data.quality_issues,
        enough_data=enough_data,
        counts=counts,
        period_shortage=period_shortage,
        available_rounds=available_rounds,
        selection=selected_filter,
        thresholds=thresholds,
    )
    return DiagnosticReport(
        diagnostic_id=f"diag_{now.astimezone(JAPAN_TIMEZONE).strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex}",
        diagnosed_at=now.astimezone(JAPAN_TIMEZONE),
        selection=selected_filter,
        thresholds=thresholds,
        status=status,
        status_reason=reason,
        data_sufficient=enough_data,
        counts=counts,
        overall=overall,
        average_predicted_probability=average_predicted_probability,
        average_max_probability=average_max_probability,
        class_metrics=class_metrics,
        draw=draw,
        anomalies=tuple(anomalies),
        quality_issues=tuple(data.quality_issues),
        excluded_match_count=data.excluded_match_count,
        period_shortage=period_shortage,
        period_available_round_count=available_rounds,
        calibration_table=calibration_table,
        timeline=timeline,
        rolling_summary=rolling_summary,
        league_summary=league_summary,
        version_summary=version_summary,
        settings_summary=settings_summary,
        bet_summary=bet_summary,
        purchase_performance=purchase_performance,
        simulation_performance=simulation_performance,
        coverage_summary=coverage_summary,
    )


def available_versions(manager: LiveHistoryManager) -> tuple[str, ...]:
    """画面選択用に保存済みVersionだけを返す。"""

    rounds = manager.load_rounds()
    if rounds.empty:
        return ()
    return tuple(
        sorted(
            value
            for value in rounds["prediction_version"].astype(str).unique()
            if value
        )
    )


def _load_diagnostic_data(
    manager: LiveHistoryManager,
    thresholds: DiagnosticThresholds,
) -> _DiagnosticData:
    issues: list[DataQualityIssue] = []
    raw_rounds = _read_raw_csv(
        manager.round_path,
        ROUND_COLUMNS,
        "開催回履歴",
        issues,
    )
    raw_matches = _read_raw_csv(
        manager.match_path,
        MATCH_COLUMNS,
        "試合履歴",
        issues,
    )
    raw_bets = _read_raw_csv(
        manager.bet_path,
        BET_COLUMNS,
        "買い目履歴",
        issues,
    )

    manager.warnings.clear()
    rounds = manager.load_rounds()
    matches = manager.load_matches()
    bets = manager.load_bets()
    for index, warning in enumerate(dict.fromkeys(manager.warnings), start=1):
        issues.append(
            DataQualityIssue(
                code=f"live_history_integrity_{index}",
                name="実戦履歴の整合性異常",
                level="警告",
                count=1,
                excluded_count=1,
                message=str(warning),
            )
        )
    manager.warnings.clear()

    duplicate_round_ids = _duplicate_values(
        raw_rounds,
        ("prediction_run_id",),
    )
    if duplicate_round_ids:
        issues.append(
            DataQualityIssue(
                code="duplicate_prediction_run_id",
                name="prediction_run_id重複",
                level="警告",
                count=len(duplicate_round_ids),
                excluded_count=len(duplicate_round_ids),
                message=(
                    "開催回履歴で重複したprediction_run_idを診断対象外にしました。"
                ),
            )
        )

    duplicate_match_keys = _duplicate_values(
        raw_matches,
        ("prediction_run_id", "toto_match_number"),
    )
    duplicate_match_run_ids = {value[0] for value in duplicate_match_keys}
    if duplicate_match_keys:
        issues.append(
            DataQualityIssue(
                code="duplicate_match_key",
                name="同一run内の試合番号重複",
                level="警告",
                count=len(duplicate_match_keys),
                excluded_count=len(duplicate_match_keys),
                message="重複試合を含むrunを診断対象外にしました。",
            )
        )

    missing_version_rounds = set(
        raw_rounds.loc[
            raw_rounds["prediction_version"].astype(str).str.strip().eq(""),
            "prediction_run_id",
        ].astype(str)
    )
    missing_version_matches = int(
        raw_matches["prediction_version"].astype(str).str.strip().eq("").sum()
    )
    missing_version_match_run_ids = set(
        raw_matches.loc[
            raw_matches["prediction_version"].astype(str).str.strip().eq(""),
            "prediction_run_id",
        ].astype(str)
    )
    if missing_version_rounds or missing_version_matches:
        issues.append(
            DataQualityIssue(
                code="missing_prediction_version",
                name="Version欠損",
                level="警告",
                count=len(missing_version_rounds) + missing_version_matches,
                excluded_count=len(missing_version_rounds) + missing_version_matches,
                message="使用Versionを確認できない行を診断対象外にしました。",
            )
        )

    invalid_snapshot_ids: set[str] = set()
    for _, row in raw_rounds.iterrows():
        run_id = str(row.get("prediction_run_id", ""))
        value = str(row.get("settings_snapshot_json", "")).strip()
        try:
            parsed = json.loads(value) if value else None
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if not isinstance(parsed, Mapping) or not parsed:
            invalid_snapshot_ids.add(run_id)
    if invalid_snapshot_ids:
        issues.append(
            DataQualityIssue(
                code="missing_settings_snapshot",
                name="設定スナップショット欠損",
                level="警告",
                count=len(invalid_snapshot_ids),
                excluded_count=len(invalid_snapshot_ids),
                message=(
                    "設定スナップショットを確認できないrunを診断対象外にしました。"
                ),
            )
        )

    raw_probability_values = {
        outcome: pd.to_numeric(
            raw_matches[f"probability_{outcome}"], errors="coerce"
        )
        for outcome in TOTO_OUTCOMES
    }
    missing_probability_mask = pd.Series(False, index=raw_matches.index)
    invalid_probability_mask = pd.Series(False, index=raw_matches.index)
    for outcome, values in raw_probability_values.items():
        original = raw_matches[f"probability_{outcome}"].astype(str).str.strip()
        missing_probability_mask |= original.eq("")
        invalid_probability_mask |= (
            (original.ne("") & values.isna())
            | values.map(
                lambda value: (
                    not math.isfinite(value) if pd.notna(value) else False
                )
            )
            | values.lt(0.0)
            | values.gt(1.0)
        ).fillna(False)
    missing_probability_count = int(missing_probability_mask.sum())
    if missing_probability_count:
        issues.append(
            DataQualityIssue(
                code="missing_probabilities",
                name="P(1)/P(0)/P(2)欠損",
                level="警告",
                count=missing_probability_count,
                excluded_count=missing_probability_count,
                message="3クラス確率が欠損した試合を診断対象外にしました。",
            )
        )
    invalid_probability_count = int(invalid_probability_mask.sum())
    if invalid_probability_count:
        issues.append(
            DataQualityIssue(
                code="invalid_probabilities",
                name="確率のNaN・Infinity・範囲外",
                level="警告",
                count=invalid_probability_count,
                excluded_count=invalid_probability_count,
                message="有限な0～1の確率ではない試合を診断対象外にしました。",
            )
        )

    probability_sum = sum(raw_probability_values.values())
    probability_sum_mask = (
        ~(missing_probability_mask | invalid_probability_mask)
        & probability_sum.sub(1.0).abs().gt(thresholds.probability_sum_tolerance)
    )
    probability_sum_count = int(probability_sum_mask.sum())
    if probability_sum_count:
        issues.append(
            DataQualityIssue(
                code="probability_sum_anomaly",
                name="P(1)+P(0)+P(2)合計異常",
                level="警告",
                count=probability_sum_count,
                excluded_count=probability_sum_count,
                message=(
                    "許容誤差"
                    f"{thresholds.probability_sum_tolerance:g}を超える試合を除外しました。"
                ),
            )
        )

    predicted_mask = ~raw_matches["predicted_result"].astype(str).isin(
        TOTO_OUTCOMES
    )
    predicted_count = int(predicted_mask.sum())
    if predicted_count:
        issues.append(
            DataQualityIssue(
                code="invalid_predicted_result",
                name="本命結果不正",
                level="警告",
                count=predicted_count,
                excluded_count=predicted_count,
                message="本命が1/0/2ではない試合を診断対象外にしました。",
            )
        )

    actual_text = raw_matches["actual_result"].astype(str).str.strip()
    valid_actual_labels = actual_text.map(
        lambda value: normalize_toto_outcome(value) in TOTO_OUTCOMES
    ).astype(bool)
    invalid_actual_mask = actual_text.ne("") & ~valid_actual_labels
    invalid_actual_count = int(invalid_actual_mask.sum())
    if invalid_actual_count:
        issues.append(
            DataQualityIssue(
                code="invalid_actual_result",
                name="actual_result不正",
                level="警告",
                count=invalid_actual_count,
                excluded_count=invalid_actual_count,
                message="実結果が1/0/2ではない試合を診断対象外にしました。",
            )
        )

    match_numbers = pd.to_numeric(
        raw_matches["toto_match_number"], errors="coerce"
    )
    invalid_number_mask = (
        match_numbers.isna()
        | ~match_numbers.isin(range(1, 14))
        | match_numbers.map(
            lambda value: not float(value).is_integer() if pd.notna(value) else False
        )
    )
    invalid_number_count = int(invalid_number_mask.sum())
    if invalid_number_count:
        issues.append(
            DataQualityIssue(
                code="invalid_match_number",
                name="試合番号不正",
                level="警告",
                count=invalid_number_count,
                excluded_count=invalid_number_count,
                message="試合番号1～13ではない行を診断対象外にしました。",
            )
        )

    bad_count_run_ids: set[str] = set()
    for run_id in raw_rounds["prediction_run_id"].astype(str):
        run_rows = raw_matches.loc[
            raw_matches["prediction_run_id"].astype(str) == run_id
        ]
        numbers = pd.to_numeric(
            run_rows["toto_match_number"], errors="coerce"
        )
        valid_numbers = {
            int(value)
            for value in numbers.dropna()
            if float(value).is_integer() and 1 <= int(value) <= 13
        }
        if len(run_rows) != 13 or valid_numbers != set(range(1, 14)):
            bad_count_run_ids.add(run_id)
    if bad_count_run_ids:
        issues.append(
            DataQualityIssue(
                code="incomplete_run_matches",
                name="13試合未満または試合番号不足run",
                level="警告",
                count=len(bad_count_run_ids),
                excluded_count=len(bad_count_run_ids),
                message="13試合と試合番号1～13を確認できないrunを除外しました。",
            )
        )

    incomplete_confirmed_ids: set[str] = set()
    for _, round_row in raw_rounds.iterrows():
        if str(round_row.get("round_status", "")) not in CONFIRMED_STATUSES:
            continue
        run_id = str(round_row.get("prediction_run_id", ""))
        run_actuals = raw_matches.loc[
            raw_matches["prediction_run_id"].astype(str) == run_id,
            "actual_result",
        ]
        normalized_actuals = [normalize_toto_outcome(value) for value in run_actuals]
        if len(normalized_actuals) != 13 or not all(
            value in TOTO_OUTCOMES for value in normalized_actuals
        ):
            incomplete_confirmed_ids.add(run_id)
    if incomplete_confirmed_ids:
        issues.append(
            DataQualityIssue(
                code="confirmed_run_incomplete",
                name="結果確定状態と試合結果の不一致",
                level="警告",
                count=len(incomplete_confirmed_ids),
                excluded_count=len(incomplete_confirmed_ids),
                message=(
                    "結果確定済み表示でも13試合の実結果を確認できないrunを除外しました。"
                ),
            )
        )

    invalid_run_ids = (
        {value[0] for value in duplicate_round_ids}
        | duplicate_match_run_ids
        | missing_version_rounds
        | missing_version_match_run_ids
        | invalid_snapshot_ids
        | bad_count_run_ids
        | incomplete_confirmed_ids
    )
    invalid_match_run_ids = set(
        raw_matches.loc[
            missing_probability_mask
            | invalid_probability_mask
            | probability_sum_mask
            | predicted_mask
            | invalid_actual_mask
            | invalid_number_mask,
            "prediction_run_id",
        ].astype(str)
    )
    invalid_run_ids |= invalid_match_run_ids

    if not rounds.empty:
        rounds = rounds.loc[
            ~rounds["prediction_run_id"].astype(str).isin(invalid_run_ids)
        ].copy()
    valid_run_ids = set(rounds["prediction_run_id"].astype(str))
    if not matches.empty:
        matches = matches.loc[
            matches["prediction_run_id"].astype(str).isin(valid_run_ids)
        ].copy()
    if not bets.empty:
        bets = bets.loc[
            bets["prediction_run_id"].astype(str).isin(valid_run_ids)
        ].copy()

    excluded_match_count = max(0, len(raw_matches) - len(matches))
    return _DiagnosticData(
        rounds=rounds.reset_index(drop=True),
        matches=matches.reset_index(drop=True),
        bets=bets.reset_index(drop=True),
        raw_rounds=raw_rounds,
        raw_matches=raw_matches,
        raw_bets=raw_bets,
        quality_issues=issues,
        excluded_match_count=excluded_match_count,
    )


def _read_raw_csv(
    path: Path,
    columns: Sequence[str],
    label: str,
    issues: list[DataQualityIssue],
) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.read_csv(
            path,
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
        issues.append(
            DataQualityIssue(
                code=f"unreadable_{path.stem}",
                name=f"{label}読込異常",
                level="警告",
                count=1,
                excluded_count=1,
                message=f"{path.name}を読み込めません: {error}",
            )
        )
        return pd.DataFrame(columns=columns)
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        issues.append(
            DataQualityIssue(
                code=f"missing_columns_{path.stem}",
                name=f"{label}の必須列不足",
                level="警告",
                count=len(missing),
                excluded_count=len(frame),
                message=f"不足列: {', '.join(missing)}",
            )
        )
        for column in missing:
            frame[column] = ""
    return frame[list(columns)].copy()


def _duplicate_values(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> set[tuple[str, ...]]:
    if frame.empty or any(column not in frame.columns for column in columns):
        return set()
    values = frame[list(columns)].astype(str)
    duplicated = values.duplicated(keep=False)
    return {
        tuple(str(row[column]) for column in columns)
        for _, row in values.loc[duplicated].iterrows()
    }


def _filter_rounds(
    rounds: pd.DataFrame,
    selection: DiagnosticFilter,
    *,
    now: datetime,
) -> tuple[pd.DataFrame, bool, int]:
    filtered = _filter_version_only(rounds, selection.version)
    if filtered.empty:
        return filtered, selection.period in RECENT_PERIOD_ROUNDS, 0
    filtered = filtered.assign(_diagnostic_time=_round_times(filtered))
    available_round_count = int(filtered["round_id"].astype(str).nunique())
    period_shortage = False

    if selection.period in RECENT_PERIOD_ROUNDS:
        requested = RECENT_PERIOD_ROUNDS[selection.period]
        period_shortage = available_round_count < requested
        round_order = (
            filtered.groupby(filtered["round_id"].astype(str), dropna=False)[
                "_diagnostic_time"
            ]
            .max()
            .sort_values()
        )
        selected_round_ids = set(round_order.tail(requested).index.astype(str))
        filtered = filtered.loc[
            filtered["round_id"].astype(str).isin(selected_round_ids)
        ]
    elif selection.period == "今シーズン":
        current_season = now.astimezone(JAPAN_TIMEZONE).year
        seasons = pd.to_numeric(filtered["season"], errors="coerce")
        filtered = filtered.loc[seasons.eq(current_season)]
    elif selection.period == "任意期間":
        dates = filtered["_diagnostic_time"].dt.date
        filtered = filtered.loc[
            dates.map(
                lambda value: (
                    pd.notna(value)
                    and selection.start_date <= value <= selection.end_date
                )
            )
        ]
    return (
        filtered.drop(columns="_diagnostic_time").reset_index(drop=True),
        period_shortage,
        available_round_count,
    )


def _filter_version_only(rounds: pd.DataFrame, version: str) -> pd.DataFrame:
    if rounds.empty:
        return rounds.copy()
    if version == ALL_VERSIONS:
        return rounds.copy()
    return rounds.loc[
        rounds["prediction_version"].astype(str) == str(version)
    ].copy()


def _round_times(rounds: pd.DataFrame) -> pd.Series:
    primary = pd.to_datetime(rounds["round_start_at"], errors="coerce", utc=True)
    fallback = pd.to_datetime(rounds["predicted_at"], errors="coerce", utc=True)
    return primary.fillna(fallback).dt.tz_convert(JAPAN_TIMEZONE)


def _confirmed_run_ids(
    rounds: pd.DataFrame,
    all_matches: pd.DataFrame,
) -> set[str]:
    if rounds.empty or all_matches.empty:
        return set()
    status_ids = set(
        rounds.loc[
            rounds["round_status"].astype(str).isin(CONFIRMED_STATUSES),
            "prediction_run_id",
        ].astype(str)
    )
    confirmed = set()
    for run_id in status_ids:
        run_matches = all_matches.loc[
            all_matches["prediction_run_id"].astype(str) == run_id
        ]
        numbers = pd.to_numeric(
            run_matches["toto_match_number"], errors="coerce"
        )
        actuals = [
            normalize_toto_outcome(value)
            for value in run_matches["actual_result"]
        ]
        if (
            len(run_matches) == 13
            and set(int(value) for value in numbers.dropna()) == set(range(1, 14))
            and all(actual in TOTO_OUTCOMES for actual in actuals)
        ):
            confirmed.add(run_id)
    return confirmed


def _metric_inputs(
    matches: pd.DataFrame,
) -> tuple[
    list[str],
    list[dict[str, float]],
    list[str],
    list[bool],
]:
    if matches.empty:
        return [], [], [], []
    ordered = matches.assign(
        _match_time=pd.to_datetime(matches["match_time"], errors="coerce", utc=True),
        _match_number=pd.to_numeric(
            matches["toto_match_number"], errors="coerce"
        ),
    ).sort_values(
        ["_match_time", "round_id", "prediction_run_id", "_match_number"],
        kind="stable",
    )
    predictions: list[str] = []
    probabilities: list[dict[str, float]] = []
    actuals: list[str] = []
    candidates: list[bool] = []
    for _, row in ordered.iterrows():
        prediction = normalize_toto_outcome(row.get("predicted_result"))
        actual = normalize_toto_outcome(row.get("actual_result"))
        if prediction not in TOTO_OUTCOMES or actual not in TOTO_OUTCOMES:
            continue
        values = {
            outcome: _finite_float(row.get(f"probability_{outcome}"))
            for outcome in TOTO_OUTCOMES
        }
        if any(value is None for value in values.values()):
            continue
        predictions.append(prediction)
        probabilities.append(
            {outcome: float(values[outcome]) for outcome in TOTO_OUTCOMES}
        )
        actuals.append(actual)
        candidates.append(_as_bool(row.get("draw_candidate")))
    return predictions, probabilities, actuals, candidates


def _draw_diagnostic(
    class_zero: OneVsRestMetrics,
    draw_evaluation: Any,
    candidates: Sequence[bool],
    actuals: Sequence[str],
    bet_draw: Mapping[str, Any],
) -> DrawDiagnostic:
    candidate_count = sum(bool(value) for value in candidates)
    candidate_hit_count = sum(
        bool(candidate) and actual == "0"
        for candidate, actual in zip(candidates, actuals)
    )
    mean_p0 = class_zero.mean_probability
    actual_rate = class_zero.actual_rate
    return DrawDiagnostic(
        actual_draw_rate=actual_rate,
        favorite_draw_rate=(
            class_zero.predicted_count / class_zero.match_count
            if class_zero.match_count
            else None
        ),
        candidate_rate=(
            candidate_count / class_zero.match_count
            if class_zero.match_count
            else None
        ),
        actual_draw_count=class_zero.actual_count,
        favorite_draw_hit_count=class_zero.hit_count,
        candidate_hit_count=candidate_hit_count,
        precision=(
            draw_evaluation.draw.precision if draw_evaluation is not None else 0.0
        ),
        recall=(
            draw_evaluation.draw.recall if draw_evaluation is not None else 0.0
        ),
        f1_score=(
            draw_evaluation.draw.f1_score if draw_evaluation is not None else 0.0
        ),
        brier_score=class_zero.brier_score,
        calibration_error=class_zero.calibration_error,
        mean_probability_0=mean_p0,
        probability_actual_gap=(
            mean_p0 - actual_rate
            if mean_p0 is not None and actual_rate is not None
            else None
        ),
        recommended_draw_inclusion_rate=bet_draw.get(
            "recommended_draw_inclusion_rate"
        ),
        purchased_draw_inclusion_rate=bet_draw.get(
            "purchased_draw_inclusion_rate"
        ),
        recommended_draw_covered_count=int(
            bet_draw.get("recommended_draw_covered_count", 0)
        ),
        purchased_draw_covered_count=int(
            bet_draw.get("purchased_draw_covered_count", 0)
        ),
        draw_inclusion_score_mean=bet_draw.get("draw_inclusion_score_mean"),
    )


def _calibration_table(
    class_metrics: Mapping[str, OneVsRestMetrics],
) -> pd.DataFrame:
    rows = []
    for outcome in TOTO_OUTCOMES:
        for item in class_metrics[outcome].calibration_bins:
            rows.append(
                {
                    "結果": outcome,
                    "確率帯": item.label,
                    "試合数": item.count,
                    "平均予測確率": item.mean_probability,
                    "実発生率": item.actual_rate,
                    "Calibration差": item.calibration_gap,
                }
            )
    return pd.DataFrame(rows)


def _evaluate_subset(
    rounds: pd.DataFrame,
    matches: pd.DataFrame,
) -> tuple[Optional[ModelMetrics], OneVsRestMetrics, int, int]:
    # matchesはリーグ絞り込み後で13行未満になり得る。runの確定状態は
    # Version8-A開催回行で判定し、各試合は有効なactualだけを共通入力へ渡す。
    confirmed_ids = set(
        rounds.loc[
            rounds["round_status"].astype(str).isin(CONFIRMED_STATUSES),
            "prediction_run_id",
        ].astype(str)
    )
    selected = matches.loc[
        matches["prediction_run_id"].astype(str).isin(confirmed_ids)
    ]
    predictions, probabilities, actuals, _ = _metric_inputs(selected)
    overall = (
        evaluate_model(predictions, probabilities, actuals)
        if predictions
        else None
    )
    draw = evaluate_one_vs_rest(
        predictions,
        probabilities,
        actuals,
        outcome="0",
    )
    round_count = int(
        selected["round_id"].astype(str).nunique() if not selected.empty else 0
    )
    return overall, draw, len(selected), round_count


def _timeline(rounds: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    columns = (
        "開催日時",
        "開催回",
        "試合数",
        "的中率",
        "Brier Score",
        "Log Loss",
        "Calibration",
        "引分F1",
        "本命0率",
        "実引分率",
    )
    if rounds.empty or matches.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    timed = rounds.assign(_diagnostic_time=_round_times(rounds))
    round_order = (
        timed.groupby(timed["round_id"].astype(str), dropna=False)[
            "_diagnostic_time"
        ]
        .max()
        .sort_values()
    )
    for round_id, timestamp in round_order.items():
        group_rounds = rounds.loc[rounds["round_id"].astype(str) == round_id]
        run_ids = set(group_rounds["prediction_run_id"].astype(str))
        group_matches = matches.loc[
            matches["prediction_run_id"].astype(str).isin(run_ids)
        ]
        overall, draw, match_count, _ = _evaluate_subset(
            group_rounds,
            group_matches,
        )
        if overall is None or match_count <= 0:
            rows.append(
                {
                    "開催日時": timestamp,
                    "開催回": f"第{round_id}回",
                    "試合数": 0,
                    "的中率": None,
                    "Brier Score": None,
                    "Log Loss": None,
                    "Calibration": None,
                    "引分F1": None,
                    "本命0率": None,
                    "実引分率": None,
                }
            )
            continue
        rows.append(
            {
                "開催日時": timestamp,
                "開催回": f"第{round_id}回",
                "試合数": match_count,
                "的中率": overall.accuracy,
                "Brier Score": overall.brier_score,
                "Log Loss": overall.log_loss,
                "Calibration": overall.calibration_error,
                "引分F1": draw.f1_score,
                "本命0率": (
                    draw.predicted_count / draw.match_count
                    if draw.match_count
                    else None
                ),
                "実引分率": draw.actual_rate,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _rolling_summary(
    rounds: pd.DataFrame,
    matches: pd.DataFrame,
    thresholds: DiagnosticThresholds,
) -> pd.DataFrame:
    columns = (
        "期間",
        "状態",
        "開催回数",
        "試合数",
        "的中率",
        "Brier Score",
        "Log Loss",
        "Calibration",
        "引分F1",
        "全期間的中率",
        "全期間Brier",
        "全期間Log Loss",
        "全期間Calibration",
        "全期間引分F1",
        "的中率差",
        "Brier差",
        "Log Loss差",
        "Calibration差",
        "引分F1差",
    )
    all_overall, all_draw, all_matches, all_rounds = _evaluate_subset(
        rounds,
        matches,
    )
    if rounds.empty:
        return pd.DataFrame(
            [
                {"期間": "直近5開催", "状態": "データ不足"},
                {"期間": "直近10開催", "状態": "データ不足"},
            ],
            columns=columns,
        )
    timed = rounds.assign(_diagnostic_time=_round_times(rounds))
    order = (
        timed.groupby(timed["round_id"].astype(str), dropna=False)[
            "_diagnostic_time"
        ]
        .max()
        .sort_values()
    )
    rows = []
    for window in (5, 10):
        enough_round_window = len(order) >= window
        selected_ids = set(order.tail(window).index.astype(str))
        window_rounds = rounds.loc[
            rounds["round_id"].astype(str).isin(selected_ids)
        ]
        run_ids = set(window_rounds["prediction_run_id"].astype(str))
        window_matches = matches.loc[
            matches["prediction_run_id"].astype(str).isin(run_ids)
        ]
        current, draw, match_count, round_count = _evaluate_subset(
            window_rounds,
            window_matches,
        )
        enough = (
            enough_round_window
            and match_count >= thresholds.minimum_match_count
            and round_count >= thresholds.minimum_round_count
        )
        rows.append(
            {
                "期間": f"直近{window}開催",
                "状態": "診断可能" if enough else "データ不足",
                "開催回数": round_count,
                "試合数": match_count,
                "的中率": current.accuracy if current else None,
                "Brier Score": current.brier_score if current else None,
                "Log Loss": current.log_loss if current else None,
                "Calibration": current.calibration_error if current else None,
                "引分F1": draw.f1_score if current else None,
                "全期間的中率": all_overall.accuracy if all_overall else None,
                "全期間Brier": all_overall.brier_score if all_overall else None,
                "全期間Log Loss": all_overall.log_loss if all_overall else None,
                "全期間Calibration": (
                    all_overall.calibration_error if all_overall else None
                ),
                "全期間引分F1": all_draw.f1_score if all_overall else None,
                "的中率差": _difference(
                    current.accuracy if current else None,
                    all_overall.accuracy if all_overall else None,
                ),
                "Brier差": _difference(
                    current.brier_score if current else None,
                    all_overall.brier_score if all_overall else None,
                ),
                "Log Loss差": _difference(
                    current.log_loss if current else None,
                    all_overall.log_loss if all_overall else None,
                ),
                "Calibration差": _difference(
                    current.calibration_error if current else None,
                    all_overall.calibration_error if all_overall else None,
                ),
                "引分F1差": _difference(
                    draw.f1_score if current else None,
                    all_draw.f1_score if all_overall else None,
                ),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _group_summary(
    rounds: pd.DataFrame,
    matches: pd.DataFrame,
    *,
    group_column: str,
    groups: Sequence[str],
    thresholds: DiagnosticThresholds,
) -> pd.DataFrame:
    label = "リーグ" if group_column == "league" else "Version"
    columns = (
        label,
        "状態",
        "開催回数",
        "試合数",
        "的中率",
        "Brier Score",
        "Log Loss",
        "Calibration",
        "引分F1",
    )
    rows = []
    for group in groups:
        if group_column == "league":
            group_matches = matches.loc[
                matches["league"].astype(str) == str(group)
            ]
            run_ids = set(group_matches["prediction_run_id"].astype(str))
            group_rounds = rounds.loc[
                rounds["prediction_run_id"].astype(str).isin(run_ids)
            ]
        else:
            group_rounds = rounds.loc[
                rounds["prediction_version"].astype(str) == str(group)
            ]
            run_ids = set(group_rounds["prediction_run_id"].astype(str))
            group_matches = matches.loc[
                matches["prediction_run_id"].astype(str).isin(run_ids)
            ]
        overall, draw, match_count, round_count = _evaluate_subset(
            group_rounds,
            group_matches,
        )
        enough = (
            match_count >= thresholds.minimum_match_count
            and round_count >= thresholds.minimum_round_count
        )
        rows.append(
            {
                label: group,
                "状態": "診断可能" if enough else "データ不足",
                "開催回数": round_count,
                "試合数": match_count,
                "的中率": overall.accuracy if overall else None,
                "Brier Score": overall.brier_score if overall else None,
                "Log Loss": overall.log_loss if overall else None,
                "Calibration": overall.calibration_error if overall else None,
                "引分F1": draw.f1_score if overall else None,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _settings_group_summary(
    rounds: pd.DataFrame,
    matches: pd.DataFrame,
    thresholds: DiagnosticThresholds,
) -> pd.DataFrame:
    columns = (
        "設定group",
        "状態",
        "開催回数",
        "run数",
        "試合数",
        "的中率",
        "Brier Score",
        "Log Loss",
        "Calibration",
        "引分F1",
        "最初の予測日時",
        "最後の予測日時",
    )
    if rounds.empty:
        return pd.DataFrame(columns=columns)
    grouped_rounds = rounds.copy()
    grouped_rounds["_settings_group"] = grouped_rounds[
        "settings_snapshot_json"
    ].map(_settings_group_id)
    rows = []
    for group_id, group_rounds in grouped_rounds.groupby(
        "_settings_group",
        dropna=False,
    ):
        run_ids = set(group_rounds["prediction_run_id"].astype(str))
        group_matches = matches.loc[
            matches["prediction_run_id"].astype(str).isin(run_ids)
        ]
        overall, draw, match_count, round_count = _evaluate_subset(
            group_rounds,
            group_matches,
        )
        enough = (
            match_count >= thresholds.minimum_match_count
            and round_count >= thresholds.minimum_round_count
        )
        times = pd.to_datetime(group_rounds["predicted_at"], errors="coerce")
        rows.append(
            {
                "設定group": group_id,
                "状態": "診断可能" if enough else "データ不足",
                "開催回数": round_count,
                "run数": len(group_rounds),
                "試合数": match_count,
                "的中率": overall.accuracy if overall else None,
                "Brier Score": overall.brier_score if overall else None,
                "Log Loss": overall.log_loss if overall else None,
                "Calibration": overall.calibration_error if overall else None,
                "引分F1": draw.f1_score if overall else None,
                "最初の予測日時": times.min(),
                "最後の予測日時": times.max(),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _settings_group_id(value: Any) -> str:
    text = str(value or "")
    try:
        parsed = json.loads(text)
        stable = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return "設定不明"
    return "setting_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12]


def _draw_bet_statistics(
    bets: pd.DataFrame,
    evaluated_matches: pd.DataFrame,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "recommended_draw_inclusion_rate": None,
        "purchased_draw_inclusion_rate": None,
        "recommended_draw_covered_count": 0,
        "purchased_draw_covered_count": 0,
        "draw_inclusion_score_mean": None,
    }
    if bets.empty:
        return result
    actual_by_match = {
        (str(row["prediction_run_id"]), int(float(row["toto_match_number"]))): (
            normalize_toto_outcome(row.get("actual_result"))
        )
        for _, row in evaluated_matches.iterrows()
        if _finite_float(row.get("toto_match_number")) is not None
    }
    score_values: list[float] = []
    for record_type in ("recommended", "purchased"):
        selected_count = 0
        draw_included_count = 0
        covered_draw_count = 0
        typed = bets.loc[bets["record_type"].astype(str) == record_type]
        for _, bet in typed.iterrows():
            run_id = str(bet.get("prediction_run_id", ""))
            for item in _json_list(bet.get("selections_json")):
                if not isinstance(item, Mapping):
                    continue
                number = _positive_int(item.get("source_match_number"))
                outcomes = {
                    normalize_toto_outcome(value)
                    for value in item.get("outcomes", [])
                }
                if number is None:
                    continue
                selected_count += 1
                includes_draw = "0" in outcomes
                draw_included_count += int(includes_draw)
                covered_draw_count += int(
                    includes_draw and actual_by_match.get((run_id, number)) == "0"
                )
            for item in _json_list(bet.get("draw_inclusion_json")):
                if not isinstance(item, Mapping):
                    continue
                score = _finite_float(item.get("draw_inclusion_score"))
                if score is not None:
                    score_values.append(score)
        result[f"{record_type}_draw_inclusion_rate"] = (
            draw_included_count / selected_count if selected_count else None
        )
        result[f"{record_type}_draw_covered_count"] = covered_draw_count
    result["draw_inclusion_score_mean"] = (
        sum(score_values) / len(score_values) if score_values else None
    )
    return result


def _bet_diagnostics(
    bets: pd.DataFrame,
    *,
    confirmed_run_ids: set[str],
    thresholds: DiagnosticThresholds,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], pd.DataFrame]:
    summary_columns = (
        "区分",
        "買い目数",
        "run数",
        "平均口数",
        "平均購入金額",
        "平均Coverage",
        "評価済み買い目数",
        "全結果カバー率",
        "toto完全カバー数",
        "mini toto A的中数",
        "mini toto B的中数",
    )
    summary_rows = []
    for record_type, label in (
        ("recommended", "AI推奨（simulation）"),
        ("purchased", "実購入（actual）"),
    ):
        typed = (
            bets.loc[bets["record_type"].astype(str) == record_type]
            if not bets.empty
            else pd.DataFrame(columns=BET_COLUMNS)
        )
        coverage_values = _numeric_column(typed, "coverage")
        ticket_values = _numeric_column(typed, "ticket_count")
        amount_column = (
            "actual_purchase_amount_yen"
            if record_type == "purchased"
            else "planned_purchase_amount_yen"
        )
        amount_values = _numeric_column(typed, amount_column)
        evaluated = (
            typed.loc[
                typed["prediction_run_id"].astype(str).isin(confirmed_run_ids)
                & typed["all_matches_covered"].map(_optional_bool).notna()
            ].copy()
            if not typed.empty
            else typed
        )
        hit_flags = [
            value
            for value in evaluated["all_matches_covered"].map(_optional_bool)
            if value is not None
        ]
        summary_rows.append(
            {
                "区分": label,
                "買い目数": len(typed),
                "run数": int(
                    typed["prediction_run_id"].astype(str).nunique()
                    if not typed.empty
                    else 0
                ),
                "平均口数": _mean(ticket_values),
                "平均購入金額": _mean(amount_values),
                "平均Coverage": _mean(coverage_values),
                "評価済み買い目数": len(hit_flags),
                "全結果カバー率": (
                    sum(bool(value) for value in hit_flags) / len(hit_flags)
                    if hit_flags
                    else None
                ),
                "toto完全カバー数": _target_hit_count(evaluated, "toto"),
                "mini toto A的中数": _target_hit_count(evaluated, "mini_a"),
                "mini toto B的中数": _target_hit_count(evaluated, "mini_b"),
            }
        )
    bet_summary = pd.DataFrame(summary_rows, columns=summary_columns)

    purchased = (
        bets.loc[bets["record_type"].astype(str) == "purchased"]
        if not bets.empty
        else pd.DataFrame(columns=BET_COLUMNS)
    )
    purchase_performance = _financial_performance(
        purchased,
        amount_column="actual_purchase_amount_yen",
        return_column="actual_return_yen",
        data_label="実購入",
        confirmed_run_ids=confirmed_run_ids,
    )
    recommended = (
        bets.loc[bets["record_type"].astype(str) == "recommended"]
        if not bets.empty
        else pd.DataFrame(columns=BET_COLUMNS)
    )
    simulation_performance = _financial_performance(
        recommended,
        amount_column="planned_purchase_amount_yen",
        return_column="simulation_return_yen",
        data_label="AI推奨シミュレーション",
        confirmed_run_ids=confirmed_run_ids,
    )

    coverage_rows = []
    for record_type, label in (
        ("recommended", "AI推奨"),
        ("purchased", "実購入"),
    ):
        typed = (
            bets.loc[bets["record_type"].astype(str) == record_type]
            if not bets.empty
            else pd.DataFrame(columns=BET_COLUMNS)
        )
        for lower, upper in COVERAGE_BANDS:
            selected_indexes = []
            for index, row in typed.iterrows():
                coverage = _finite_float(row.get("coverage"))
                if coverage is None:
                    continue
                if coverage >= lower and (
                    coverage < upper or (upper >= 1.0 and coverage <= 1.0)
                ):
                    selected_indexes.append(index)
            selected = typed.loc[selected_indexes]
            evaluated = selected.loc[
                selected["prediction_run_id"].astype(str).isin(
                    confirmed_run_ids
                )
            ]
            hit_flags = [
                value
                for value in evaluated["all_matches_covered"].map(_optional_bool)
                if value is not None
            ]
            coverage_rows.append(
                {
                    "区分": label,
                    "Coverage帯": _coverage_label(lower, upper),
                    "状態": (
                        "診断可能"
                        if len(hit_flags)
                        >= thresholds.minimum_coverage_evaluated_count
                        else "データ不足"
                    ),
                    "買い目数": len(selected),
                    "評価済み数": len(hit_flags),
                    "完全カバー数": sum(bool(value) for value in hit_flags),
                    "完全カバー率": (
                        sum(bool(value) for value in hit_flags) / len(hit_flags)
                        if hit_flags
                        else None
                    ),
                }
            )
    return (
        bet_summary,
        purchase_performance,
        simulation_performance,
        pd.DataFrame(coverage_rows),
    )


def _financial_performance(
    frame: pd.DataFrame,
    *,
    amount_column: str,
    return_column: str,
    data_label: str,
    confirmed_run_ids: set[str],
) -> dict[str, Any]:
    if frame.empty:
        return {
            "label": data_label,
            "has_records": False,
            "has_evaluated_records": False,
            "record_count": 0,
            "run_count": 0,
            "evaluated_record_count": 0,
            "evaluated_run_count": 0,
            "pending_count": 0,
            "total_amount_yen": None,
            "total_return_yen": None,
            "profit_yen": None,
            "roi": None,
            "highest_return_yen": None,
            "maximum_loss_yen": None,
        }
    evaluated_rows: list[tuple[float, float, str]] = []
    for _, row in frame.iterrows():
        run_id = str(row.get("prediction_run_id", ""))
        if run_id not in confirmed_run_ids:
            continue
        amount = _finite_float(row.get(amount_column))
        returned = _finite_float(row.get(return_column))
        if amount is None or amount < 0.0 or returned is None or returned < 0.0:
            continue
        evaluated_rows.append(
            (amount, returned, run_id)
        )
    total_amount = sum(row[0] for row in evaluated_rows) if evaluated_rows else None
    total_return = sum(row[1] for row in evaluated_rows) if evaluated_rows else None
    profit = (
        total_return - total_amount
        if total_return is not None and total_amount is not None
        else None
    )
    run_totals: dict[str, list[float]] = {}
    for amount, returned, run_id in evaluated_rows:
        values = run_totals.setdefault(run_id, [0.0, 0.0])
        values[0] += amount
        values[1] += returned
    return {
        "label": data_label,
        "has_records": True,
        "has_evaluated_records": bool(evaluated_rows),
        "record_count": len(frame),
        "run_count": int(frame["prediction_run_id"].astype(str).nunique()),
        "evaluated_record_count": len(evaluated_rows),
        "evaluated_run_count": len({row[2] for row in evaluated_rows}),
        "pending_count": len(frame) - len(evaluated_rows),
        "total_amount_yen": total_amount,
        "total_return_yen": total_return,
        "profit_yen": profit,
        "roi": (
            total_return / total_amount
            if total_return is not None and total_amount not in (None, 0.0)
            else None
        ),
        "highest_return_yen": (
            max(values[1] for values in run_totals.values())
            if run_totals
            else None
        ),
        "maximum_loss_yen": (
            min(
                0.0,
                min(values[1] - values[0] for values in run_totals.values()),
            )
            if run_totals
            else None
        ),
    }


def _target_hit_count(frame: pd.DataFrame, target: str) -> int:
    if frame.empty:
        return 0
    return sum(
        str(row.get("target", "")) == target
        and _optional_bool(row.get("all_matches_covered")) is True
        for _, row in frame.iterrows()
    )


def _coverage_label(lower: float, upper: float) -> str:
    return (
        f"{lower:.0%}以上"
        if upper >= 1.0
        else f"{lower:.0%}以上{upper:.0%}未満"
    )


def _detect_anomalies(
    *,
    overall: Optional[ModelMetrics],
    class_metrics: Mapping[str, OneVsRestMetrics],
    draw: DrawDiagnostic,
    evaluated_matches: pd.DataFrame,
    rolling_summary: pd.DataFrame,
    league_summary: pd.DataFrame,
    quality_issues: Sequence[DataQualityIssue],
    enough_data: bool,
    thresholds: DiagnosticThresholds,
) -> list[DiagnosticAnomaly]:
    anomalies: list[DiagnosticAnomaly] = []
    for issue in quality_issues:
        anomalies.append(
            DiagnosticAnomaly(
                code=issue.code,
                category="データ品質",
                name=issue.name,
                level=issue.level,
                metric="異常件数",
                current_value=float(issue.count),
                baseline_value=0.0,
                difference=float(issue.count),
                unit="件",
                judgement=issue.level,
                message=issue.message,
            )
        )

    if not enough_data or overall is None:
        return anomalies

    degradation_rules = (
        (
            "accuracy_drop",
            "的中率低下",
            "的中率",
            "全期間的中率",
            True,
            thresholds.accuracy_drop_attention,
            thresholds.accuracy_drop_warning,
            "pt",
        ),
        (
            "brier_increase",
            "Brier Score悪化",
            "Brier Score",
            "全期間Brier",
            False,
            thresholds.brier_increase_attention,
            thresholds.brier_increase_warning,
            "",
        ),
        (
            "log_loss_increase",
            "Log Loss悪化",
            "Log Loss",
            "全期間Log Loss",
            False,
            thresholds.log_loss_increase_attention,
            thresholds.log_loss_increase_warning,
            "",
        ),
        (
            "calibration_increase",
            "Calibration悪化",
            "Calibration",
            "全期間Calibration",
            False,
            thresholds.calibration_increase_attention,
            thresholds.calibration_increase_warning,
            "pt",
        ),
        (
            "draw_f1_drop",
            "引分F1低下",
            "引分F1",
            "全期間引分F1",
            True,
            thresholds.draw_f1_drop_attention,
            thresholds.draw_f1_drop_warning,
            "pt",
        ),
    )
    for _, row in rolling_summary.iterrows():
        window = str(row.get("期間", ""))
        if str(row.get("状態", "")) != "診断可能":
            anomalies.append(
                DiagnosticAnomaly(
                    code=f"rolling_insufficient_{window}",
                    category="モデル性能",
                    name=f"{window}診断",
                    level="情報",
                    metric="開催回数",
                    current_value=_finite_float(row.get("開催回数")),
                    baseline_value=_finite_float(
                        window.replace("直近", "").replace("開催", "")
                    ),
                    difference=None,
                    unit="開催",
                    judgement="データ不足",
                    message=f"{window}を評価する開催回数が不足しています。",
                )
            )
            continue
        for (
            code,
            name,
            current_column,
            baseline_column,
            lower_is_worse,
            attention,
            warning,
            unit,
        ) in degradation_rules:
            current = _finite_float(row.get(current_column))
            baseline = _finite_float(row.get(baseline_column))
            if current is None or baseline is None:
                continue
            degradation = (
                baseline - current if lower_is_worse else current - baseline
            )
            level = _increase_level(degradation, attention, warning)
            if level is None:
                continue
            anomalies.append(
                DiagnosticAnomaly(
                    code=f"{code}_{window}",
                    category="モデル性能",
                    name=f"{window}の{name}",
                    level=level,
                    metric=current_column,
                    current_value=current,
                    baseline_value=baseline,
                    difference=current - baseline,
                    unit=unit,
                    judgement="悪化を検知",
                    message=(
                        f"{window}の{current_column}が全期間値より"
                        f"{abs(current - baseline):.4f}悪化しています。"
                    ),
                )
            )

    if (
        draw.actual_draw_rate is not None
        and draw.favorite_draw_rate is not None
    ):
        gap = draw.actual_draw_rate - draw.favorite_draw_rate
        level = _increase_level(
            gap,
            thresholds.draw_favorite_gap_attention,
            thresholds.draw_favorite_gap_warning,
        )
        if level is not None:
            anomalies.append(
                DiagnosticAnomaly(
                    code="draw_favorite_rate_gap",
                    category="モデル性能",
                    name="実引分率と本命0率の乖離",
                    level=level,
                    metric="本命0率",
                    current_value=draw.favorite_draw_rate,
                    baseline_value=draw.actual_draw_rate,
                    difference=draw.favorite_draw_rate - draw.actual_draw_rate,
                    unit="pt",
                    judgement=level,
                    message="本命0率が実引分率に対して低い状態です。",
                )
            )

    for outcome in TOTO_OUTCOMES:
        metrics = class_metrics[outcome]
        if metrics.actual_count < thresholds.minimum_class_support:
            continue
        level = _low_value_level(
            metrics.recall,
            thresholds.low_recall_attention,
            thresholds.low_recall_warning,
        )
        if level is not None:
            anomalies.append(
                DiagnosticAnomaly(
                    code=f"low_recall_{outcome}",
                    category="モデル性能",
                    name=f"結果{outcome}のRecall低下",
                    level=level,
                    metric=f"{outcome} Recall",
                    current_value=metrics.recall,
                    baseline_value=thresholds.low_recall_attention,
                    difference=metrics.recall - thresholds.low_recall_attention,
                    unit="pt",
                    judgement=level,
                    message=(
                        f"結果{outcome}の実発生{metrics.actual_count}件に対する"
                        f"Recallが{metrics.recall:.1%}です。"
                    ),
                )
            )

    high_confidence = _high_probability_diagnostic(
        evaluated_matches,
        thresholds.high_probability_threshold,
    )
    if high_confidence["count"] >= thresholds.minimum_high_probability_count:
        gap = high_confidence["mean_confidence"] - high_confidence["accuracy"]
        level = _increase_level(
            gap,
            thresholds.high_probability_gap_attention,
            thresholds.high_probability_gap_warning,
        )
        if level is not None:
            anomalies.append(
                DiagnosticAnomaly(
                    code="high_probability_accuracy_low",
                    category="モデル性能",
                    name="高確率予測の的中率低下",
                    level=level,
                    metric="高確率予測的中率",
                    current_value=high_confidence["accuracy"],
                    baseline_value=high_confidence["mean_confidence"],
                    difference=-gap,
                    unit="pt",
                    judgement=level,
                    message=(
                        f"最大確率{thresholds.high_probability_threshold:.0%}以上"
                        f"{high_confidence['count']}件の的中率が平均最大確率より低い状態です。"
                    ),
                )
            )

    league_rows = (
        league_summary.loc[
            league_summary["状態"].astype(str) == "診断可能"
        ]
        if not league_summary.empty
        else league_summary
    )
    if len(league_rows) >= 2:
        weights = pd.to_numeric(league_rows["試合数"], errors="coerce").fillna(0.0)
        total_weight = float(weights.sum())
        if total_weight > 0.0:
            league_rules = (
                (
                    "accuracy",
                    "的中率",
                    True,
                    thresholds.league_accuracy_gap_attention,
                    thresholds.league_accuracy_gap_warning,
                    "pt",
                ),
                (
                    "brier",
                    "Brier Score",
                    False,
                    thresholds.league_brier_gap_attention,
                    thresholds.league_brier_gap_warning,
                    "",
                ),
                (
                    "log_loss",
                    "Log Loss",
                    False,
                    thresholds.league_log_loss_gap_attention,
                    thresholds.league_log_loss_gap_warning,
                    "",
                ),
                (
                    "calibration",
                    "Calibration",
                    False,
                    thresholds.league_calibration_gap_attention,
                    thresholds.league_calibration_gap_warning,
                    "pt",
                ),
                (
                    "draw_f1",
                    "引分F1",
                    True,
                    thresholds.league_draw_f1_gap_attention,
                    thresholds.league_draw_f1_gap_warning,
                    "pt",
                ),
            )
            baselines = {
                column: float(
                    (
                        pd.to_numeric(league_rows[column], errors="coerce")
                        * weights
                    ).sum()
                    / total_weight
                )
                for _, column, _, _, _, _ in league_rules
            }
            for _, row in league_rows.iterrows():
                league = str(row.get("リーグ", ""))
                for (
                    code,
                    column,
                    lower_is_worse,
                    attention,
                    warning,
                    unit,
                ) in league_rules:
                    current = _finite_float(row.get(column))
                    baseline = baselines[column]
                    if current is None or not math.isfinite(baseline):
                        continue
                    degradation = (
                        baseline - current
                        if lower_is_worse
                        else current - baseline
                    )
                    level = _increase_level(
                        degradation,
                        attention,
                        warning,
                    )
                    if level is None:
                        continue
                    anomalies.append(
                        DiagnosticAnomaly(
                            code=f"league_{code}_gap_{league}",
                            category="モデル性能",
                            name=f"{league}のリーグ別{column}差",
                            level=level,
                            metric=column,
                            current_value=current,
                            baseline_value=baseline,
                            difference=current - baseline,
                            unit=unit,
                            judgement="リーグ別性能差あり",
                            message=(
                                f"{league}の{column}が全リーグ加重平均より"
                                f"{abs(current - baseline):.4f}悪い状態です。"
                            ),
                        )
                    )
    return anomalies


def _overall_status(
    anomalies: Sequence[DiagnosticAnomaly],
    quality_issues: Sequence[DataQualityIssue],
    *,
    enough_data: bool,
    counts: DiagnosticCounts,
    period_shortage: bool,
    available_rounds: int,
    selection: DiagnosticFilter,
    thresholds: DiagnosticThresholds,
) -> tuple[str, str]:
    warning_count = sum(item.level == "警告" for item in anomalies)
    performance_warning_count = sum(
        item.level == "警告" and item.category == "モデル性能"
        for item in anomalies
    )
    quality_warning_count = sum(item.level == "警告" for item in quality_issues)
    attention_count = sum(
        item.level == "注意" and item.category == "モデル性能"
        for item in anomalies
    )
    if warning_count:
        if period_shortage:
            requested = RECENT_PERIOD_ROUNDS.get(selection.period, 0)
            shortage_note = (
                f" モデル性能は{selection.period}に必要な{requested}開催に対して"
                f"保存済み{available_rounds}開催のためデータ不足です。"
            )
        elif not enough_data:
            shortage_note = (
                f" モデル性能は{counts.match_count}試合・{counts.round_count}開催で"
                "データ不足です。"
            )
        else:
            shortage_note = ""
        return (
            "警告",
            f"モデル性能警告{performance_warning_count}件、"
            f"データ品質警告{quality_warning_count}件を検知しました。"
            f"{shortage_note} 数値と基準値を確認してください。",
        )
    if not enough_data:
        if period_shortage:
            requested = RECENT_PERIOD_ROUNDS.get(selection.period, 0)
            return (
                "データ不足",
                f"{selection.period}には{requested}開催必要ですが、保存済みは"
                f"{available_rounds}開催です。参考値として表示します。",
            )
        return (
            "データ不足",
            f"診断には{thresholds.minimum_match_count}試合・"
            f"{thresholds.minimum_round_count}開催以上必要です。現在は"
            f"{counts.match_count}試合・{counts.round_count}開催です。",
        )
    if attention_count:
        return (
            "注意",
            f"注意{attention_count}件を検知しました。数値と基準値を確認してください。",
        )
    return (
        "正常",
        "設定済み閾値を超える性能悪化・偏り・データ品質異常は検知されませんでした。",
    )


def _high_probability_diagnostic(
    matches: pd.DataFrame,
    threshold: float,
) -> dict[str, Any]:
    confidences: list[float] = []
    hits: list[bool] = []
    for _, row in matches.iterrows():
        probabilities = [
            _finite_float(row.get(f"probability_{outcome}"))
            for outcome in TOTO_OUTCOMES
        ]
        if any(value is None for value in probabilities):
            continue
        confidence = max(float(value) for value in probabilities)
        if confidence < threshold:
            continue
        prediction = normalize_toto_outcome(row.get("predicted_result"))
        actual = normalize_toto_outcome(row.get("actual_result"))
        if prediction not in TOTO_OUTCOMES or actual not in TOTO_OUTCOMES:
            continue
        confidences.append(confidence)
        hits.append(prediction == actual)
    return {
        "count": len(confidences),
        "mean_confidence": _mean(confidences),
        "accuracy": (
            sum(hits) / len(hits) if hits else None
        ),
    }


def _increase_level(
    value: float,
    attention_threshold: float,
    warning_threshold: float,
) -> Optional[str]:
    if value >= warning_threshold:
        return "警告"
    if value >= attention_threshold:
        return "注意"
    return None


def _low_value_level(
    value: float,
    attention_threshold: float,
    warning_threshold: float,
) -> Optional[str]:
    if value <= warning_threshold:
        return "警告"
    if value <= attention_threshold:
        return "注意"
    return None


def _difference(current: Any, baseline: Any) -> Optional[float]:
    current_number = _finite_float(current)
    baseline_number = _finite_float(baseline)
    if current_number is None or baseline_number is None:
        return None
    return current_number - baseline_number


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> Optional[int]:
    number = _finite_float(value)
    if number is None or not number.is_integer() or number <= 0:
        return None
    return int(number)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return None


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _numeric_column(frame: pd.DataFrame, column: str) -> list[float]:
    if frame.empty or column not in frame.columns:
        return []
    return [
        number
        for value in frame[column]
        if (number := _finite_float(value)) is not None
    ]


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


__all__ = [
    "ALL_VERSIONS",
    "DataQualityIssue",
    "DiagnosticAnomaly",
    "DiagnosticCounts",
    "DiagnosticFilter",
    "DiagnosticReport",
    "DrawDiagnostic",
    "available_versions",
    "run_model_diagnostics",
]
