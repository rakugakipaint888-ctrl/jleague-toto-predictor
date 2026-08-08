"""Version7-Aの時系列データ準備、小規模Optuna探索、保存・採用。"""

from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from backtest import (
    BacktestDataLeakError,
    BacktestError,
    _completed_before,
    _team_input,
    backtest_cutoff,
    calculate_team_stats_as_of,
    fetch_historical_matches,
)
from data_loader import JAPAN_TIMEZONE, OfficialMatch
from draw_evaluation import (
    DrawEvaluation,
    DrawScore,
    evaluate_draw_predictions,
    normalize_toto_label,
    score_draw_evaluation,
)
from draw_predictor import (
    DEFAULT_DRAW_SETTINGS,
    DrawContext,
    DrawSettings,
    build_draw_context,
    predict_draw_aware,
)
from elo_rating import generate_elo_ratings, get_team_elo
from history_manager import TotoHistoryManager, TotoRound
from model_config import (
    VERSION7A_DRAW_SEARCH_SPACE,
    VERSION7A_MODEL_VERSION,
    VERSION7A_OVERFIT_SCORE_GAP_THRESHOLD,
    VERSION7A_RANDOM_SEED,
)
from model_pipeline import ModelOptions, TeamModelInput, predict_match
from teams import get_team_category, normalize_team_name


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OPTIMIZATION_HISTORY_PATH = (
    PROJECT_ROOT / "data" / "history" / "version7a_optimization_history.csv"
)
DEFAULT_ACTIVE_SETTINGS_PATH = (
    PROJECT_ROOT / "data" / "config" / "version7a_draw_settings.json"
)
DEFAULT_SETTINGS_BACKUP_DIRECTORY = (
    PROJECT_ROOT / "data" / "config" / "version7a_backups"
)
OPTIMIZATION_HISTORY_COLUMNS = (
    "executed_at",
    "version",
    "trial_count",
    "training_period",
    "validation_period",
    "training_match_count",
    "validation_match_count",
    "best_trial",
    "best_score",
    "best_parameters",
    "overall_brier_score",
    "log_loss",
    "calibration",
    "accuracy",
    "draw_precision",
    "draw_recall",
    "draw_f1",
    "draw_brier",
    "draw_calibration",
    "version6_comparison",
    "random_seed",
)


class DrawOptimizationError(RuntimeError):
    """Version7-Aのデータ準備・探索を安全に続行できない場合。"""


class OptunaUnavailableError(DrawOptimizationError):
    """Optunaがインストールされていない。"""


@dataclass(frozen=True)
class PreparedDrawRow:
    round_id: int
    match_number: int
    cutoff_at: datetime
    actual_result: str
    base_prediction: str
    base_probabilities: Mapping[str, float]
    home_expected_goals: float
    away_expected_goals: float
    home_input: TeamModelInput
    away_input: TeamModelInput
    context: DrawContext
    latest_source_time: datetime


@dataclass(frozen=True)
class PreparedDrawRound:
    toto_round: TotoRound
    cutoff_at: datetime
    historical_match_count: int
    rows: tuple[PreparedDrawRow, ...]

    @property
    def year(self) -> int:
        return self.cutoff_at.astimezone(JAPAN_TIMEZONE).year


@dataclass(frozen=True)
class DrawOptimizationDataset:
    training_rounds: tuple[PreparedDrawRound, ...]
    validation_rounds: tuple[PreparedDrawRound, ...]

    @property
    def training_rows(self) -> tuple[PreparedDrawRow, ...]:
        return tuple(row for item in self.training_rounds for row in item.rows)

    @property
    def validation_rows(self) -> tuple[PreparedDrawRow, ...]:
        return tuple(row for item in self.validation_rounds for row in item.rows)

    @property
    def training_period(self) -> str:
        return _period_label(self.training_rounds)

    @property
    def validation_period(self) -> str:
        return _period_label(self.validation_rounds)


@dataclass(frozen=True)
class DrawTrialRecord:
    trial_number: int
    score: float
    settings: DrawSettings
    evaluation: DrawEvaluation
    degradation_penalty: float


@dataclass(frozen=True)
class DrawOverfittingCheck:
    is_overfitting: bool
    score_gap: float
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return "過学習の可能性" if self.is_overfitting else "過学習の兆候なし"


@dataclass(frozen=True)
class DrawOptimizationResult:
    executed_at: datetime
    trial_count: int
    random_seed: int
    elapsed_seconds: float
    dataset: DrawOptimizationDataset
    trials: tuple[DrawTrialRecord, ...]
    best_trial: int
    best_settings: DrawSettings
    training_version6: DrawEvaluation
    training_best: DrawEvaluation
    training_score: DrawScore
    validation_version6: DrawEvaluation
    validation_best: DrawEvaluation
    validation_score: DrawScore
    overfitting: DrawOverfittingCheck

    @property
    def best_score(self) -> float:
        return self.training_score.score


@dataclass(frozen=True)
class TrialProgress:
    current_trial: int
    trial_count: int
    best_trial: int
    best_score: float
    elapsed_seconds: float

    @property
    def remaining_trials(self) -> int:
        return max(0, self.trial_count - self.current_trial)


@dataclass(frozen=True)
class AdoptionResult:
    adopted: bool
    message: str
    settings_path: Path
    backup_path: Optional[Path] = None


ProgressCallback = Callable[[TrialProgress], None]
RoundProgressCallback = Callable[[int, int, str], None]


def _period_label(rounds: Sequence[PreparedDrawRound]) -> str:
    if not rounds:
        return "確認できません"
    dates = sorted(item.cutoff_at.astimezone(JAPAN_TIMEZONE).date() for item in rounds)
    return f"{dates[0].isoformat()}～{dates[-1].isoformat()}"


def _round_year(toto_round: TotoRound) -> int:
    if toto_round.start_time is None:
        raise DrawOptimizationError("開催回の開始日時を確認できません。")
    return toto_round.start_time.astimezone(JAPAN_TIMEZONE).year


def _historical_category(team_name: str, matches: Sequence[OfficialMatch]) -> str:
    for match in reversed(matches):
        if team_name in (match.home_team, match.away_team) and match.category:
            return str(match.category).split("/")[0]
    return get_team_category(team_name) or ""


def _validate_round(toto_round: TotoRound) -> None:
    if not toto_round.is_official_order_complete:
        raise DrawOptimizationError("toto公式第1～13試合の順序が不正です。")
    if not toto_round.is_complete:
        raise DrawOptimizationError(
            f"第{toto_round.round_id}回は実結果が確定していません。"
        )
    if not toto_round.is_jleague_round:
        raise DrawOptimizationError(
            f"第{toto_round.round_id}回はJリーグ13試合だけではありません。"
        )


def prepare_draw_round(
    toto_round: TotoRound,
    historical_matches: Sequence[OfficialMatch],
) -> PreparedDrawRound:
    """開催初日0:00より前だけでVersion6予測と引分特徴量を固定する。"""

    _validate_round(toto_round)
    cutoff_at = backtest_cutoff(toto_round)
    completed = tuple(_completed_before(historical_matches, cutoff_at))
    if not completed:
        raise DrawOptimizationError(
            f"第{toto_round.round_id}回より前の確定済みJリーグ履歴がありません。"
        )
    if any(
        match.match_time.astimezone(JAPAN_TIMEZONE) >= cutoff_at
        for match in completed
    ):
        raise BacktestDataLeakError("Version7-Aへ未来の試合結果が混入しました。")

    target_teams = tuple(
        sorted(
            {
                team_name
                for match in toto_round.matches
                for team_name in (match.home_team, match.away_team)
            }
        )
    )
    team_stats = calculate_team_stats_as_of(completed, cutoff_at, target_teams)
    team_categories = {
        team_name: _historical_category(team_name, completed)
        for team_name in target_teams
    }
    elo_result = generate_elo_ratings(
        completed,
        team_categories=team_categories,
        as_of=cutoff_at,
        team_name_normalizer=normalize_team_name,
    )
    options = ModelOptions(
        use_elo=True,
        use_venue=True,
        use_recent_weighting=True,
        use_standings=True,
    )
    contexts: dict[str, DrawContext] = {}
    rows = []
    latest_source_time = max(
        match.match_time.astimezone(JAPAN_TIMEZONE) for match in completed
    )

    for toto_match in sorted(toto_round.matches, key=lambda item: item.match_number):
        home_elo = get_team_elo(
            toto_match.home_team,
            elo_result,
            team_name_normalizer=normalize_team_name,
        )
        away_elo = get_team_elo(
            toto_match.away_team,
            elo_result,
            team_name_normalizer=normalize_team_name,
        )
        home_input = _team_input(
            toto_match.home_team,
            team_stats.get(toto_match.home_team),
            home_elo,
            is_home=True,
        )
        away_input = _team_input(
            toto_match.away_team,
            team_stats.get(toto_match.away_team),
            away_elo,
            is_home=False,
        )
        pipeline = predict_match(home_input, away_input, options=options)
        base_probabilities = {
            "1": float(pipeline.version5_probabilities["home_win"]),
            "0": float(pipeline.version5_probabilities["draw"]),
            "2": float(pipeline.version5_probabilities["away_win"]),
        }
        categories = {
            team_categories.get(toto_match.home_team, ""),
            team_categories.get(toto_match.away_team, ""),
        }
        category = next(iter(categories)) if len(categories) == 1 else ""
        if category not in contexts:
            contexts[category] = build_draw_context(
                completed,
                cutoff_at,
                category=category,
            )
        actual_result = normalize_toto_label(toto_match.actual_result)
        if actual_result not in ("1", "0", "2"):
            raise DrawOptimizationError(
                f"第{toto_round.round_id}回第{toto_match.match_number}試合の"
                "実結果を確認できません。"
            )
        rows.append(
            PreparedDrawRow(
                round_id=toto_round.round_id,
                match_number=toto_match.match_number,
                cutoff_at=cutoff_at,
                actual_result=actual_result,
                base_prediction=pipeline.version5_prediction,
                base_probabilities=base_probabilities,
                home_expected_goals=pipeline.expected_final.home,
                away_expected_goals=pipeline.expected_final.away,
                home_input=home_input,
                away_input=away_input,
                context=contexts[category],
                latest_source_time=latest_source_time,
            )
        )

    return PreparedDrawRound(
        toto_round=toto_round,
        cutoff_at=cutoff_at,
        historical_match_count=len(completed),
        rows=tuple(rows),
    )


def prepare_draw_dataset(
    rounds: Sequence[TotoRound],
    historical_matches: Sequence[OfficialMatch],
    *,
    training_years: Optional[Sequence[int]] = None,
    validation_years: Optional[Sequence[int]] = None,
) -> DrawOptimizationDataset:
    """シーズン分離を優先し、不可なら時系列80/20で分ける。"""

    unique_rounds = {item.round_id: item for item in rounds}
    ordered = sorted(
        unique_rounds.values(),
        key=lambda item: backtest_cutoff(item),
    )
    if len(ordered) < 2:
        raise DrawOptimizationError("TrainingとValidationに2開催回以上必要です。")

    if training_years is not None or validation_years is not None:
        training_set = {int(value) for value in training_years or ()}
        validation_set = {int(value) for value in validation_years or ()}
        if not training_set or not validation_set:
            raise DrawOptimizationError("Training年とValidation年を指定してください。")
        if training_set & validation_set:
            raise DrawOptimizationError("TrainingとValidationに同じ年は使えません。")
        training_raw = [item for item in ordered if _round_year(item) in training_set]
        validation_raw = [item for item in ordered if _round_year(item) in validation_set]
    else:
        years = sorted({_round_year(item) for item in ordered})
        if len(years) >= 2:
            validation_year = years[-1]
            training_raw = [item for item in ordered if _round_year(item) < validation_year]
            validation_raw = [item for item in ordered if _round_year(item) == validation_year]
        else:
            split_index = max(1, min(len(ordered) - 1, math.floor(len(ordered) * 0.8)))
            training_raw = ordered[:split_index]
            validation_raw = ordered[split_index:]

    if not training_raw or not validation_raw:
        raise DrawOptimizationError("TrainingとValidationの両方に開催回が必要です。")
    latest_training = max(backtest_cutoff(item) for item in training_raw)
    earliest_validation = min(backtest_cutoff(item) for item in validation_raw)
    if latest_training >= earliest_validation:
        raise DrawOptimizationError(
            "Validationより後の開催回がTrainingへ含まれています。"
        )

    training = tuple(prepare_draw_round(item, historical_matches) for item in training_raw)
    validation = tuple(prepare_draw_round(item, historical_matches) for item in validation_raw)
    dataset = DrawOptimizationDataset(training, validation)
    if not dataset.training_rows or not dataset.validation_rows:
        raise DrawOptimizationError("TrainingまたはValidationが0試合です。")
    if any(row.latest_source_time >= row.cutoff_at for row in dataset.training_rows):
        raise BacktestDataLeakError("Trainingへ未来データが混入しました。")
    if any(row.latest_source_time >= row.cutoff_at for row in dataset.validation_rows):
        raise BacktestDataLeakError("Validationへ未来データが混入しました。")
    return dataset


def evaluate_draw_settings(
    rows: Sequence[PreparedDrawRow],
    settings: DrawSettings,
) -> DrawEvaluation:
    if not rows:
        raise DrawOptimizationError("評価対象が0試合です。")
    predictions = []
    probabilities = []
    actuals = []
    candidates = []
    for row in rows:
        result = predict_draw_aware(
            row.base_probabilities,
            row.home_expected_goals,
            row.away_expected_goals,
            row.home_input,
            row.away_input,
            context=row.context,
            settings=settings,
        )
        predictions.append(result.prediction)
        probabilities.append(result.probabilities)
        actuals.append(row.actual_result)
        candidates.append(result.is_draw_candidate)
    return evaluate_draw_predictions(
        predictions,
        probabilities,
        actuals,
        candidate_flags=candidates,
    )


def _suggest_settings(trial: Any) -> DrawSettings:
    values = {}
    for name, specification in VERSION7A_DRAW_SEARCH_SPACE.items():
        if "choices" in specification:
            values[name] = trial.suggest_categorical(name, list(specification["choices"]))
        else:
            values[name] = trial.suggest_float(
                name,
                float(specification["low"]),
                float(specification["high"]),
                step=float(specification["step"]),
            )
    return DrawSettings.from_mapping(values)


def _check_overfitting(
    training: DrawEvaluation,
    validation: DrawEvaluation,
    training_score: DrawScore,
    validation_score: DrawScore,
) -> DrawOverfittingCheck:
    score_gap = training_score.score - validation_score.score
    reasons = []
    if score_gap > VERSION7A_OVERFIT_SCORE_GAP_THRESHOLD:
        reasons.append(f"TrainingとValidationのScore差が{score_gap:.2f}あります。")
    if training.draw.f1_score - validation.draw.f1_score > 0.20:
        reasons.append("Validationの引分F1がTrainingより20ポイント超低下しています。")
    for label, training_value, validation_value, allowance in (
        ("Brier Score", training.overall.brier_score, validation.overall.brier_score, 0.10),
        ("Log Loss", training.overall.log_loss, validation.overall.log_loss, 0.15),
        (
            "Calibration",
            training.overall.calibration_error,
            validation.overall.calibration_error,
            0.10,
        ),
    ):
        if (
            training_value is not None
            and validation_value is not None
            and validation_value - training_value > allowance
        ):
            reasons.append(f"Validationの{label}がTrainingより大きく悪化しています。")
    return DrawOverfittingCheck(bool(reasons), score_gap, tuple(reasons))


def run_draw_optimization(
    dataset: DrawOptimizationDataset,
    trial_count: int = 30,
    *,
    random_seed: int = VERSION7A_RANDOM_SEED,
    progress_callback: Optional[ProgressCallback] = None,
) -> DrawOptimizationResult:
    """Trainingだけで探索し、最良設定確定後にValidationを一度評価する。"""

    try:
        import optuna
    except ImportError as error:
        raise OptunaUnavailableError(
            "Optunaが未インストールです。requirements.txtから依存関係を"
            "インストールしてください。"
        ) from error

    count = int(trial_count)
    if count <= 0:
        raise ValueError("Trial数は1以上にしてください。")
    if not dataset.training_rows or not dataset.validation_rows:
        raise DrawOptimizationError("TrainingまたはValidationが0試合です。")

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    started = time.monotonic()
    training_version6 = evaluate_draw_settings(
        dataset.training_rows,
        DEFAULT_DRAW_SETTINGS,
    )
    trial_records: list[DrawTrialRecord] = []
    sampler = optuna.samplers.TPESampler(seed=int(random_seed))
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.enqueue_trial(DEFAULT_DRAW_SETTINGS.as_dict())

    def objective(trial: Any) -> float:
        settings = _suggest_settings(trial)
        evaluation = evaluate_draw_settings(dataset.training_rows, settings)
        scored = score_draw_evaluation(
            evaluation,
            version6_baseline=training_version6,
        )
        trial_records.append(
            DrawTrialRecord(
                trial_number=trial.number,
                score=scored.score,
                settings=settings,
                evaluation=evaluation,
                degradation_penalty=scored.degradation_penalty,
            )
        )
        return scored.score

    def callback(study_value: Any, frozen_trial: Any) -> None:
        if progress_callback is None:
            return
        progress_callback(
            TrialProgress(
                current_trial=len(study_value.trials),
                trial_count=count,
                best_trial=int(study_value.best_trial.number),
                best_score=float(study_value.best_value),
                elapsed_seconds=time.monotonic() - started,
            )
        )

    study.optimize(
        objective,
        n_trials=count,
        callbacks=[callback],
        show_progress_bar=False,
    )
    if not trial_records:
        raise DrawOptimizationError("Optuna Trialが完了しませんでした。")
    best_trial_number = int(study.best_trial.number)
    best_record = next(
        record for record in trial_records if record.trial_number == best_trial_number
    )
    training_score = score_draw_evaluation(
        best_record.evaluation,
        version6_baseline=training_version6,
    )

    # Validationは探索・Trial選択に一切渡さず、ここで初めて評価する。
    validation_version6 = evaluate_draw_settings(
        dataset.validation_rows,
        DEFAULT_DRAW_SETTINGS,
    )
    validation_best = evaluate_draw_settings(
        dataset.validation_rows,
        best_record.settings,
    )
    validation_score = score_draw_evaluation(
        validation_best,
        version6_baseline=validation_version6,
    )
    overfitting = _check_overfitting(
        best_record.evaluation,
        validation_best,
        training_score,
        validation_score,
    )
    return DrawOptimizationResult(
        executed_at=datetime.now(JAPAN_TIMEZONE),
        trial_count=count,
        random_seed=int(random_seed),
        elapsed_seconds=time.monotonic() - started,
        dataset=dataset,
        trials=tuple(sorted(trial_records, key=lambda item: item.trial_number)),
        best_trial=best_trial_number,
        best_settings=best_record.settings,
        training_version6=training_version6,
        training_best=best_record.evaluation,
        training_score=training_score,
        validation_version6=validation_version6,
        validation_best=validation_best,
        validation_score=validation_score,
        overfitting=overfitting,
    )


def collect_completed_rounds(
    history_manager: TotoHistoryManager,
    years: Sequence[int],
    *,
    rounds_per_year: int = 5,
    progress_callback: Optional[RoundProgressCallback] = None,
) -> tuple[TotoRound, ...]:
    """Version6開催回CSVを優先利用し、年別の直近確定回を集める。"""

    selected_years = tuple(sorted({int(year) for year in years}))
    if not selected_years:
        raise DrawOptimizationError("対象年を指定してください。")
    catalog = history_manager.load_catalog(selected_years)
    selected = []
    target_total = max(1, int(rounds_per_year)) * len(selected_years)
    for year in selected_years:
        year_rounds = [item for item in catalog if item.fiscal_year == year]
        year_count = 0
        for summary in year_rounds:
            if year_count >= int(rounds_per_year):
                break
            loaded = history_manager.load_round(summary.round_id).toto_round
            if loaded is None or not loaded.is_complete or not loaded.is_jleague_round:
                continue
            selected.append(loaded)
            year_count += 1
            if progress_callback is not None:
                progress_callback(
                    len(selected),
                    target_total,
                    f"第{loaded.round_id}回を読み込みました。",
                )
        if year_count == 0:
            raise DrawOptimizationError(
                f"{year}年の確定済みJリーグtoto開催回を取得できません。"
            )
    return tuple(sorted(selected, key=lambda item: backtest_cutoff(item)))


def collect_historical_matches(
    rounds: Sequence[TotoRound],
    fallback_matches: Sequence[OfficialMatch] = (),
) -> tuple[OfficialMatch, ...]:
    """Version6の公式→保存→同梱フォールバックで年度履歴を統合する。"""

    representatives: dict[int, TotoRound] = {}
    for toto_round in rounds:
        representatives.setdefault(_round_year(toto_round), toto_round)
    unique: dict[tuple[Any, ...], OfficialMatch] = {}
    for toto_round in representatives.values():
        try:
            loaded = fetch_historical_matches(
                toto_round,
                fallback_matches=fallback_matches,
            )
        except (BacktestError, OSError, ValueError) as error:
            raise DrawOptimizationError(str(error)) from error
        for match in loaded:
            key = (
                match.match_time.isoformat(),
                normalize_team_name(match.home_team),
                normalize_team_name(match.away_team),
                match.home_goals,
                match.away_goals,
                match.category,
            )
            unique[key] = match
    if not unique:
        raise DrawOptimizationError("Version6のJリーグ履歴を取得できません。")
    return tuple(sorted(unique.values(), key=lambda item: item.match_time))


def load_active_draw_settings(
    path: Path = DEFAULT_ACTIVE_SETTINGS_PATH,
) -> DrawSettings:
    """採用済みJSONを読み、欠損・破損時はVersion6互換初期値へ戻す。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        parameters = payload.get("parameters", payload)
        return DrawSettings.from_mapping(parameters)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return DEFAULT_DRAW_SETTINGS


def _settings_payload(settings: DrawSettings, adopted_at: datetime) -> dict[str, Any]:
    return {
        "version": VERSION7A_MODEL_VERSION,
        "adopted_at": adopted_at.astimezone(JAPAN_TIMEZONE).isoformat(),
        "parameters": settings.as_dict(),
    }


def adopt_draw_settings(
    settings: DrawSettings,
    *,
    confirmed: bool,
    path: Path = DEFAULT_ACTIVE_SETTINGS_PATH,
    backup_directory: Path = DEFAULT_SETTINGS_BACKUP_DIRECTORY,
) -> AdoptionResult:
    """YESの場合だけ直前設定をバックアップし、原子的に採用する。"""

    if not confirmed:
        return AdoptionResult(
            False,
            "NOが選択されたため現在設定を維持しました。",
            path,
        )
    settings.validate()
    now = datetime.now(JAPAN_TIMEZONE)
    backup_path = backup_directory / (
        "version7a_draw_settings_"
        + now.strftime("%Y%m%dT%H%M%S%f")
        + ".json"
    )
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_directory.mkdir(parents=True, exist_ok=True)
        previous = load_active_draw_settings(path)
        backup_path.write_text(
            json.dumps(
                _settings_payload(previous, now),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary_path.write_text(
            json.dumps(
                _settings_payload(settings, now),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        return AdoptionResult(
            False,
            f"設定を保存できませんでした: {error}",
            path,
            backup_path if backup_path.exists() else None,
        )
    return AdoptionResult(
        True,
        "Version7-A最適設定を採用し、直前設定をバックアップしました。",
        path,
        backup_path,
    )


def restore_latest_draw_settings(
    *,
    path: Path = DEFAULT_ACTIVE_SETTINGS_PATH,
    backup_directory: Path = DEFAULT_SETTINGS_BACKUP_DIRECTORY,
) -> AdoptionResult:
    """最新バックアップを検証してから現在設定へ戻す。"""

    backups = sorted(backup_directory.glob("version7a_draw_settings_*.json"))
    if not backups:
        return AdoptionResult(False, "復元できる設定バックアップがありません。", path)
    latest = backups[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
        settings = DrawSettings.from_mapping(payload["parameters"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(
                _settings_payload(settings, datetime.now(JAPAN_TIMEZONE)),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        return AdoptionResult(False, f"設定を復元できませんでした: {error}", path, latest)
    return AdoptionResult(True, "直前のVersion7-A設定へ戻しました。", path, latest)


def _evaluation_summary(evaluation: DrawEvaluation) -> dict[str, Any]:
    return {
        "accuracy": evaluation.overall.accuracy,
        "brier_score": evaluation.overall.brier_score,
        "log_loss": evaluation.overall.log_loss,
        "calibration": evaluation.overall.calibration_error,
        "draw_precision": evaluation.draw.precision,
        "draw_recall": evaluation.draw.recall,
        "draw_f1": evaluation.draw.f1_score,
        "draw_brier": evaluation.draw.brier_score,
        "draw_calibration": evaluation.draw.calibration_error,
        "draw_mean_probability_when_predicted": (
            evaluation.draw.mean_probability_when_predicted
        ),
        "actual_draw_rate": evaluation.draw.actual_draw_rate,
        "predicted_draw_rate": evaluation.draw.predicted_draw_rate,
        "class_accuracy_1": evaluation.overall.class_accuracy["1"],
        "class_accuracy_0": evaluation.overall.class_accuracy["0"],
        "class_accuracy_2": evaluation.overall.class_accuracy["2"],
        "actual_draw_count": evaluation.draw.actual_draw_count,
        "predicted_draw_count": evaluation.draw.predicted_draw_count,
        "draw_hit_count": evaluation.draw.draw_hit_count,
    }


def version6_comparison(result: DrawOptimizationResult) -> dict[str, Any]:
    baseline = _evaluation_summary(result.validation_version6)
    version7a = _evaluation_summary(result.validation_best)
    return {
        key: {
            "version6": baseline[key],
            "version7a": version7a[key],
            "difference": (
                version7a[key] - baseline[key]
                if isinstance(version7a[key], (int, float))
                and isinstance(baseline[key], (int, float))
                else None
            ),
        }
        for key in baseline
    }


def save_optimization_result(
    result: DrawOptimizationResult,
    path: Path = DEFAULT_OPTIMIZATION_HISTORY_PATH,
) -> bool:
    """Version7-A専用CSVへ1実行1行で追記し、既存CSVは破壊しない。"""

    validation = result.validation_best
    row = {
        "executed_at": result.executed_at.isoformat(),
        "version": VERSION7A_MODEL_VERSION,
        "trial_count": result.trial_count,
        "training_period": result.dataset.training_period,
        "validation_period": result.dataset.validation_period,
        "training_match_count": len(result.dataset.training_rows),
        "validation_match_count": len(result.dataset.validation_rows),
        "best_trial": result.best_trial,
        "best_score": result.best_score,
        "best_parameters": json.dumps(
            result.best_settings.as_dict(), ensure_ascii=False, sort_keys=True
        ),
        "overall_brier_score": validation.overall.brier_score,
        "log_loss": validation.overall.log_loss,
        "calibration": validation.overall.calibration_error,
        "accuracy": validation.overall.accuracy,
        "draw_precision": validation.draw.precision,
        "draw_recall": validation.draw.recall,
        "draw_f1": validation.draw.f1_score,
        "draw_brier": validation.draw.brier_score,
        "draw_calibration": validation.draw.calibration_error,
        "version6_comparison": json.dumps(
            version6_comparison(result), ensure_ascii=False, sort_keys=True
        ),
        "random_seed": result.random_seed,
    }
    existing_rows = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                if tuple(reader.fieldnames or ()) != OPTIMIZATION_HISTORY_COLUMNS:
                    return False
                existing_rows = list(reader)
        except (OSError, UnicodeError, csv.Error):
            return False
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=OPTIMIZATION_HISTORY_COLUMNS,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerow(row)
        temporary_path.replace(path)
        return True
    except (OSError, TypeError, ValueError, csv.Error):
        temporary_path.unlink(missing_ok=True)
        return False
