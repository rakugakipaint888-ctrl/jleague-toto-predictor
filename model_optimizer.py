"""Version7-Bのデータ準備、4探索方式、ランキング・履歴保存。"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import os
import random
import statistics
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from version7b_config import (
    VERSION7B_DRAW_DEGRADATION_TOLERANCES,
    VERSION7B_DRAW_GRID_SPACE,
    VERSION7B_MODEL_GRID_SPACE,
    VERSION7B_MODEL_SEARCH_SPACE,
    VERSION7B_MODEL_VERSION,
    VERSION7B_OVERFIT_THRESHOLDS,
    VERSION7B_RANDOM_SEED,
    VERSION7B_RANKING_LIMIT,
)
from backtest import (
    BacktestDataLeakError,
    BacktestError,
    _completed_before,
    _team_input,
    backtest_cutoff,
    calculate_team_stats_as_of,
    fetch_historical_matches,
)
from data_loader import JAPAN_TIMEZONE, OfficialMatch, TeamRecentStats
from draw_evaluation import normalize_toto_label
from draw_predictor import DrawContext, build_draw_context, predict_draw_aware
from elo_rating import generate_elo_ratings, get_team_elo
from history_manager import TotoHistoryManager, TotoMatch, TotoRound
from metrics import DEFAULT_TOTO_STAKE_YEN, toto_payout_for_hits
from model_config import VERSION7A_DRAW_SEARCH_SPACE
from model_evaluation import (
    DEFAULT_EVALUATION_WEIGHTS,
    CandidateEvaluation,
    DrawDegradationCheck,
    EvaluationWeights,
    OverfittingCheck,
    PredictionRow,
    StabilitySummary,
    build_stability_summary,
    check_draw_degradation,
    check_overfitting,
    comparison_rows,
    evaluate_candidate_rows,
)
from model_pipeline import ModelOptions, predict_match
from parameter_manager import (
    ActiveVersion7BSettings,
    Version7BParameters,
    default_active_settings,
    to_runtime_settings,
)
from teams import normalize_team_name
from walk_forward_validator import (
    SEASON_WALK_FORWARD,
    ValidationDataError,
    ValidationSplit,
    create_validation_split,
)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PARTIAL_TRIALS_PATH = (
    PROJECT_ROOT / "data" / "history" / "version7b_partial_trials.csv"
)
DEFAULT_OPTIMIZATION_HISTORY_PATH = (
    PROJECT_ROOT / "data" / "history" / "version7b_optimization_history.csv"
)
DEFAULT_MODEL_RANKING_PATH = (
    PROJECT_ROOT / "data" / "history" / "version7b_model_ranking.csv"
)

OPTUNA_SEARCH = "optuna"
RANDOM_SEARCH = "random"
GRID_SEARCH = "grid"
TWO_STAGE_SEARCH = "two_stage"
SEARCH_METHODS = (OPTUNA_SEARCH, RANDOM_SEARCH, GRID_SEARCH, TWO_STAGE_SEARCH)
ALL_LEAGUES = "全リーグ"
TARGET_LEAGUES = (ALL_LEAGUES, "J1", "J2", "J3")


class ModelOptimizationError(RuntimeError):
    """Version7-Bを安全に実行できない場合の共通例外。"""


class OptunaUnavailableError(ModelOptimizationError):
    """Optunaがインストールされていない。"""


@dataclass(frozen=True)
class PreparedModelMatch:
    toto_match: TotoMatch
    league: str
    context: DrawContext

    @property
    def actual_result(self) -> str:
        return normalize_toto_label(self.toto_match.actual_result)


@dataclass(frozen=True)
class PreparedModelRound:
    toto_round: TotoRound
    cutoff_at: datetime
    season: str
    completed_matches: tuple[OfficialMatch, ...]
    team_stats: Mapping[str, TeamRecentStats]
    team_categories: Mapping[str, str]
    matches: tuple[PreparedModelMatch, ...]
    latest_source_time: datetime

    @property
    def round_id(self) -> int:
        return self.toto_round.round_id

    def match_count(self, target_league: str) -> int:
        return sum(
            target_league == ALL_LEAGUES or item.league == target_league
            for item in self.matches
        )


@dataclass(frozen=True)
class ModelOptimizationDataset:
    split: ValidationSplit
    target_league: str
    requested_period: str
    available_leagues: tuple[str, ...]
    unavailable_leagues: tuple[str, ...]

    @property
    def training_rounds(self) -> tuple[PreparedModelRound, ...]:
        return self.split.training_rounds

    @property
    def validation_rounds(self) -> tuple[PreparedModelRound, ...]:
        return self.split.final_validation_rounds

    @property
    def training_match_count(self) -> int:
        return sum(
            item.match_count(self.target_league) for item in self.training_rounds
        )

    @property
    def validation_match_count(self) -> int:
        return sum(
            item.match_count(self.target_league) for item in self.validation_rounds
        )

    @property
    def training_period(self) -> str:
        return self.split.training_period

    @property
    def validation_period(self) -> str:
        return self.split.validation_period

    @property
    def actual_period(self) -> str:
        return self.split.actual_period


@dataclass(frozen=True)
class RoundCollection:
    rounds: tuple[TotoRound, ...]
    requested_years: tuple[int, ...]
    used_years: tuple[int, ...]
    missing_years: tuple[int, ...]


@dataclass(frozen=True)
class SearchConfiguration:
    method: str = OPTUNA_SEARCH
    trial_count: int = 100
    model_limit: int = 10000
    include_draw_parameters: bool = False
    random_seed: int = VERSION7B_RANDOM_SEED
    evaluation_weights: EvaluationWeights = DEFAULT_EVALUATION_WEIGHTS
    draw_tolerances: Mapping[str, float] = None
    overfit_thresholds: Mapping[str, float] = None
    truncate_grid_to_limit: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "draw_tolerances",
            dict(self.draw_tolerances or VERSION7B_DRAW_DEGRADATION_TOLERANCES),
        )
        object.__setattr__(
            self,
            "overfit_thresholds",
            dict(self.overfit_thresholds or VERSION7B_OVERFIT_THRESHOLDS),
        )
        self.validate()

    def validate(self) -> None:
        if self.method not in SEARCH_METHODS:
            raise ValueError(f"未対応の探索方式です: {self.method}")
        if int(self.trial_count) <= 0:
            raise ValueError("Trial数は1以上にしてください。")
        if int(self.model_limit) <= 0 or int(self.model_limit) > 50000:
            raise ValueError("探索モデル上限は1～50,000にしてください。")
        if int(self.random_seed) < 0:
            raise ValueError("ランダムシードは0以上にしてください。")
        self.evaluation_weights.validate()


@dataclass(frozen=True)
class SearchPlan:
    method: str
    planned_models: int
    executable_models: int
    model_limit: int
    grid_combination_count: Optional[int]
    executable: bool
    reason: str = ""


@dataclass(frozen=True)
class TrialRecord:
    trial_number: int
    search_stage: str
    parameters: Version7BParameters
    training: CandidateEvaluation
    selection_validation: CandidateEvaluation
    raw_validation_score: float
    selection_score: float
    draw_degradation: DrawDegradationCheck
    duration_seconds: float
    final_validation: Optional[CandidateEvaluation] = None


@dataclass(frozen=True)
class TrialProgress:
    current_trial: int
    total_trials: int
    elapsed_seconds: float
    best_score: float
    best_validation_score: float
    best_brier: Optional[float]
    best_log_loss: Optional[float]
    best_draw_f1: float
    current_parameters: Version7BParameters

    @property
    def completed_trials(self) -> int:
        return self.current_trial

    @property
    def remaining_trials(self) -> int:
        return max(0, self.total_trials - self.current_trial)

    @property
    def progress_rate(self) -> float:
        return self.current_trial / max(1, self.total_trials)

    @property
    def estimated_remaining_seconds(self) -> float:
        if self.current_trial <= 0:
            return 0.0
        return max(
            0.0,
            self.elapsed_seconds / self.current_trial * self.remaining_trials,
        )


@dataclass(frozen=True)
class OptimizationResult:
    run_id: str
    started_at: datetime
    completed_at: datetime
    configuration: SearchConfiguration
    search_plan: SearchPlan
    dataset: ModelOptimizationDataset
    current_settings: ActiveVersion7BSettings
    ranking: tuple[TrialRecord, ...]
    all_trials: tuple[TrialRecord, ...]
    baseline_training: CandidateEvaluation
    baseline_selection_validation: CandidateEvaluation
    baseline_final_validation: CandidateEvaluation
    best_training: CandidateEvaluation
    best_selection_validation: CandidateEvaluation
    best_final_validation: CandidateEvaluation
    best_parameters: Version7BParameters
    overfitting: OverfittingCheck
    draw_degradation: DrawDegradationCheck
    stability: StabilitySummary
    parameter_importance: Mapping[str, float]

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, (self.completed_at - self.started_at).total_seconds())

    @property
    def best_score(self) -> float:
        return self.ranking[0].selection_score

    @property
    def best_validation_score(self) -> float:
        return self.best_final_validation.score

    @property
    def comparison(self) -> list[dict[str, Any]]:
        return comparison_rows(
            self.baseline_final_validation,
            self.best_final_validation,
        )


ProgressCallback = Callable[[TrialProgress], None]
StopCallback = Callable[[], bool]


def _season_label(cutoff_at: datetime) -> str:
    local_date = cutoff_at.astimezone(JAPAN_TIMEZONE).date()
    if local_date.year >= 2026 and local_date.month >= 7:
        return f"{local_date.year}/{str(local_date.year + 1)[-2:]}"
    return str(local_date.year)


def _historical_category(team_name: str, matches: Sequence[OfficialMatch]) -> str:
    for match in reversed(matches):
        if team_name in (match.home_team, match.away_team) and match.category:
            return str(match.category).split("/")[0]
    return ""


def _validate_toto_round(toto_round: TotoRound) -> None:
    if not toto_round.is_official_order_complete:
        raise ModelOptimizationError("toto公式第1～13試合の順序が不正です。")
    if not toto_round.is_complete:
        raise ModelOptimizationError(
            f"第{toto_round.round_id}回は実結果が確定していません。"
        )
    if not toto_round.is_jleague_round:
        raise ModelOptimizationError(
            f"第{toto_round.round_id}回はJリーグ13試合だけではありません。"
        )


def prepare_model_round(
    toto_round: TotoRound,
    historical_matches: Sequence[OfficialMatch],
) -> PreparedModelRound:
    """開催初日0:00より前の確定データだけをTrial共通入力へ固定する。"""

    _validate_toto_round(toto_round)
    cutoff_at = backtest_cutoff(toto_round)
    completed = tuple(_completed_before(historical_matches, cutoff_at))
    if not completed:
        raise ModelOptimizationError(
            f"第{toto_round.round_id}回より前の確定済みJリーグ履歴がありません。"
        )
    if any(
        match.match_time.astimezone(JAPAN_TIMEZONE) >= cutoff_at for match in completed
    ):
        raise BacktestDataLeakError("Version7-Bへ未来の試合結果が混入しました。")
    target_teams = tuple(
        sorted(
            {
                team_name
                for item in toto_round.matches
                for team_name in (item.home_team, item.away_team)
            }
        )
    )
    team_stats = calculate_team_stats_as_of(completed, cutoff_at, target_teams)
    team_categories = {
        team_name: _historical_category(team_name, completed)
        for team_name in target_teams
    }
    contexts: dict[str, DrawContext] = {}
    prepared_matches = []
    for toto_match in sorted(toto_round.matches, key=lambda item: item.match_number):
        categories = {
            team_categories.get(toto_match.home_team, ""),
            team_categories.get(toto_match.away_team, ""),
        }
        league = next(iter(categories)) if len(categories) == 1 else ""
        if league not in ("J1", "J2", "J3"):
            league = ""
        if league not in contexts:
            contexts[league] = build_draw_context(
                completed,
                cutoff_at,
                category=league,
            )
        actual = normalize_toto_label(toto_match.actual_result)
        if actual not in ("1", "0", "2"):
            raise ModelOptimizationError(
                f"第{toto_round.round_id}回第{toto_match.match_number}試合の"
                "実結果を確認できません。"
            )
        prepared_matches.append(
            PreparedModelMatch(toto_match, league, contexts[league])
        )
    latest_source_time = max(
        item.match_time.astimezone(JAPAN_TIMEZONE) for item in completed
    )
    if latest_source_time >= cutoff_at:
        raise BacktestDataLeakError("開催日時以後の情報が特徴量へ混入しました。")
    return PreparedModelRound(
        toto_round=toto_round,
        cutoff_at=cutoff_at,
        season=_season_label(cutoff_at),
        completed_matches=completed,
        team_stats=team_stats,
        team_categories=team_categories,
        matches=tuple(prepared_matches),
        latest_source_time=latest_source_time,
    )


def prepare_model_dataset(
    rounds: Sequence[TotoRound],
    historical_matches: Sequence[OfficialMatch],
    *,
    validation_method: str = SEASON_WALK_FORWARD,
    target_league: str = ALL_LEAGUES,
    requested_period: str = "直近5シーズン",
) -> ModelOptimizationDataset:
    if target_league not in TARGET_LEAGUES:
        raise ModelOptimizationError(f"対象リーグが不正です: {target_league}")
    prepared = tuple(
        prepare_model_round(item, historical_matches)
        for item in sorted(rounds, key=backtest_cutoff)
    )
    try:
        split = create_validation_split(prepared, validation_method)
    except ValidationDataError as error:
        raise ModelOptimizationError(str(error)) from error
    available = tuple(
        league
        for league in ("J1", "J2", "J3")
        if any(item.match_count(league) > 0 for item in prepared)
    )
    unavailable = tuple(
        league for league in ("J1", "J2", "J3") if league not in available
    )
    dataset = ModelOptimizationDataset(
        split=split,
        target_league=target_league,
        requested_period=requested_period,
        available_leagues=available,
        unavailable_leagues=unavailable,
    )
    if dataset.training_match_count <= 0:
        raise ModelOptimizationError(
            "Trainingが0試合です。対象リーグを確認してください。"
        )
    if dataset.validation_match_count <= 0:
        raise ModelOptimizationError(
            "Validationが0試合です。対象リーグを確認してください。"
        )
    return dataset


def collect_available_completed_rounds(
    history_manager: TotoHistoryManager,
    years: Sequence[int],
    *,
    rounds_per_year: int = 10,
) -> RoundCollection:
    """存在する保存・公式開催回だけを使い、欠損年を推測生成しない。"""

    requested = tuple(sorted({int(value) for value in years}))
    if not requested:
        raise ModelOptimizationError("バックテスト対象年を指定してください。")
    catalog = history_manager.load_catalog(requested)
    selected = []
    used_years = []
    for year in requested:
        summaries = [item for item in catalog if item.fiscal_year == year]
        count = 0
        for summary in summaries:
            if count >= max(1, int(rounds_per_year)):
                break
            loaded = history_manager.load_saved_round(summary.round_id)
            if loaded is None:
                loaded = history_manager.load_round(summary.round_id).toto_round
            if loaded is None or not loaded.is_complete or not loaded.is_jleague_round:
                continue
            selected.append(loaded)
            count += 1
        if count > 0:
            used_years.append(year)
    if not selected:
        raise ModelOptimizationError("確定済みJリーグtoto開催回を取得できません。")
    return RoundCollection(
        rounds=tuple(sorted(selected, key=backtest_cutoff)),
        requested_years=requested,
        used_years=tuple(used_years),
        missing_years=tuple(year for year in requested if year not in used_years),
    )


def collect_historical_matches(
    rounds: Sequence[TotoRound],
    fallback_matches: Sequence[OfficialMatch] = (),
) -> tuple[OfficialMatch, ...]:
    representatives: dict[int, TotoRound] = {}
    for toto_round in rounds:
        if toto_round.start_time is not None:
            representatives.setdefault(
                toto_round.start_time.astimezone(JAPAN_TIMEZONE).year,
                toto_round,
            )
    unique: dict[tuple[Any, ...], OfficialMatch] = {}
    for toto_round in representatives.values():
        try:
            loaded = fetch_historical_matches(
                toto_round,
                fallback_matches=fallback_matches,
            )
        except (BacktestError, OSError, ValueError) as error:
            raise ModelOptimizationError(str(error)) from error
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
        raise ModelOptimizationError("バックテスト用Jリーグ履歴を取得できません。")
    return tuple(sorted(unique.values(), key=lambda item: item.match_time))


def _target_matches(
    prepared_round: PreparedModelRound,
    target_league: str,
) -> tuple[PreparedModelMatch, ...]:
    return tuple(
        item
        for item in prepared_round.matches
        if target_league == ALL_LEAGUES or item.league == target_league
    )


def predict_round_rows(
    prepared_round: PreparedModelRound,
    parameters: Version7BParameters,
    *,
    target_league: str,
) -> tuple[PredictionRow, ...]:
    """候補ごとにEloを再計算し、全補正後へVersion7-A引分モデルを重ねる。"""

    parameters.validate()
    runtime = to_runtime_settings(parameters.model)
    elo_result = generate_elo_ratings(
        prepared_round.completed_matches,
        team_categories=prepared_round.team_categories,
        settings=runtime.elo,
        as_of=prepared_round.cutoff_at,
        team_name_normalizer=normalize_team_name,
    )
    options = ModelOptions(True, True, True, True)
    rows = []
    for item in _target_matches(prepared_round, target_league):
        toto_match = item.toto_match
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
            prepared_round.team_stats.get(toto_match.home_team),
            home_elo,
            is_home=True,
        )
        away_input = _team_input(
            toto_match.away_team,
            prepared_round.team_stats.get(toto_match.away_team),
            away_elo,
            is_home=False,
        )
        pipeline = predict_match(
            home_input,
            away_input,
            options=options,
            form_settings=runtime.form,
            venue_settings=runtime.venue,
            standings_settings=runtime.standings,
            model_settings=runtime.model,
            elo_settings=runtime.elo,
        )
        draw_prediction = predict_draw_aware(
            {
                "1": pipeline.version5_probabilities["home_win"],
                "0": pipeline.version5_probabilities["draw"],
                "2": pipeline.version5_probabilities["away_win"],
            },
            pipeline.expected_final.home,
            pipeline.expected_final.away,
            home_input,
            away_input,
            context=item.context,
            settings=parameters.draw,
        )
        rows.append(
            PredictionRow(
                round_id=prepared_round.round_id,
                match_number=toto_match.match_number,
                cutoff_at=prepared_round.cutoff_at,
                season=prepared_round.season,
                league=item.league or "不明",
                prediction=draw_prediction.prediction,
                probabilities=draw_prediction.probabilities,
                actual_result=item.actual_result,
                draw_candidate=draw_prediction.is_draw_candidate,
            )
        )
    return tuple(rows)


def _roi_for_rows(
    rows_by_round: Mapping[int, Sequence[PredictionRow]],
    prepared_rounds: Sequence[PreparedModelRound],
    target_league: str,
) -> Optional[float]:
    if target_league != ALL_LEAGUES:
        return None
    payouts = 0
    stakes = 0
    round_map = {item.round_id: item for item in prepared_rounds}
    for round_id, rows in rows_by_round.items():
        if len(rows) != 13 or round_id not in round_map:
            return None
        hit_count = sum(row.prediction == row.actual_result for row in rows)
        prize = round_map[round_id].toto_round.payouts
        payout = toto_payout_for_hits(
            hit_count,
            prize.first_prize_yen,
            prize.second_prize_yen,
            prize.third_prize_yen,
        )
        if hit_count >= 11 and payout <= 0:
            # 当せん条件に達したのに配当未取得なら推測で0円としない。
            return None
        payouts += payout
        stakes += DEFAULT_TOTO_STAKE_YEN
    return payouts / stakes * 100.0 if stakes > 0 else None


def evaluate_parameter_set(
    rounds: Sequence[PreparedModelRound],
    parameters: Version7BParameters,
    *,
    target_league: str,
    weights: EvaluationWeights,
    fold_validation_rounds: Sequence[Sequence[PreparedModelRound]] = (),
) -> CandidateEvaluation:
    rows_by_round = {
        item.round_id: predict_round_rows(
            item,
            parameters,
            target_league=target_league,
        )
        for item in rounds
    }
    rows = tuple(row for item in rounds for row in rows_by_round.get(item.round_id, ()))
    if not rows:
        raise ModelOptimizationError("候補モデルの評価対象が0試合です。")
    fold_rows = tuple(
        tuple(row for item in fold for row in rows_by_round.get(item.round_id, ()))
        for fold in fold_validation_rounds
    )
    return evaluate_candidate_rows(
        rows,
        weights=weights,
        fold_rows=fold_rows,
        roi=_roi_for_rows(rows_by_round, rounds, target_league),
    )


def _objective_rounds(
    dataset: ModelOptimizationDataset,
) -> tuple[PreparedModelRound, ...]:
    ordered: dict[int, PreparedModelRound] = {}
    for fold in dataset.split.folds:
        for item in fold.validation_rounds:
            ordered[item.round_id] = item
    return tuple(sorted(ordered.values(), key=lambda item: item.cutoff_at))


def _fold_validation_groups(
    dataset: ModelOptimizationDataset,
) -> tuple[tuple[PreparedModelRound, ...], ...]:
    return tuple(tuple(fold.validation_rounds) for fold in dataset.split.folds)


def grid_combination_count(include_draw_parameters: bool) -> int:
    spaces: list[Mapping[str, Sequence[Any]]] = [VERSION7B_MODEL_GRID_SPACE]
    if include_draw_parameters:
        spaces.append(VERSION7B_DRAW_GRID_SPACE)
    count = 1
    for space in spaces:
        for values in space.values():
            count *= len(tuple(values))
    return count


def build_search_plan(configuration: SearchConfiguration) -> SearchPlan:
    configuration.validate()
    if configuration.method == GRID_SEARCH:
        combinations = grid_combination_count(configuration.include_draw_parameters)
        requested = min(combinations, int(configuration.trial_count))
        if (
            combinations > configuration.model_limit
            and not configuration.truncate_grid_to_limit
        ):
            return SearchPlan(
                configuration.method,
                combinations,
                0,
                configuration.model_limit,
                combinations,
                False,
                f"予定組み合わせ数{combinations:,}が設定上限"
                f"{configuration.model_limit:,}を超えるため実行できません。",
            )
        executable = min(requested, int(configuration.model_limit))
        reason = ""
        if executable < combinations:
            reason = (
                f"予定{combinations:,}組のうち、Trial数・上限により"
                f"{executable:,}モデルへ制限します。"
            )
        return SearchPlan(
            configuration.method,
            combinations,
            executable,
            configuration.model_limit,
            combinations,
            executable > 0,
            reason,
        )
    planned = int(configuration.trial_count)
    executable = min(planned, int(configuration.model_limit))
    reason = (
        f"Trial数{planned:,}を探索モデル上限{configuration.model_limit:,}へ制限します。"
        if executable < planned
        else ""
    )
    return SearchPlan(
        configuration.method,
        planned,
        executable,
        configuration.model_limit,
        None,
        executable > 0,
        reason,
    )


def _discrete_values(specification: Mapping[str, Any]) -> tuple[float, ...]:
    low = float(specification["low"])
    high = float(specification["high"])
    step = float(specification["step"])
    count = int(round((high - low) / step))
    return tuple(round(low + index * step, 12) for index in range(count + 1))


def _random_values(
    rng: random.Random,
    *,
    include_draw_parameters: bool,
) -> dict[str, Any]:
    values = {
        key: rng.choice(_discrete_values(specification))
        for key, specification in VERSION7B_MODEL_SEARCH_SPACE.items()
    }
    if include_draw_parameters:
        for key, specification in VERSION7A_DRAW_SEARCH_SPACE.items():
            choices = specification.get("choices")
            value = (
                rng.choice(tuple(choices))
                if choices is not None
                else rng.choice(_discrete_values(specification))
            )
            values[f"draw_{key}"] = value
    return values


def _suggest_optuna_values(
    trial: Any,
    *,
    include_draw_parameters: bool,
) -> dict[str, Any]:
    values = {}
    for key, specification in VERSION7B_MODEL_SEARCH_SPACE.items():
        values[key] = trial.suggest_float(
            key,
            float(specification["low"]),
            float(specification["high"]),
            step=float(specification["step"]),
        )
    if include_draw_parameters:
        for key, specification in VERSION7A_DRAW_SEARCH_SPACE.items():
            optuna_key = f"draw_{key}"
            if "choices" in specification:
                values[optuna_key] = trial.suggest_categorical(
                    optuna_key,
                    list(specification["choices"]),
                )
            else:
                values[optuna_key] = trial.suggest_float(
                    optuna_key,
                    float(specification["low"]),
                    float(specification["high"]),
                    step=float(specification["step"]),
                )
    return values


def _grid_values(include_draw_parameters: bool) -> Iterable[dict[str, Any]]:
    combined: dict[str, Sequence[Any]] = dict(VERSION7B_MODEL_GRID_SPACE)
    if include_draw_parameters:
        combined.update(
            {f"draw_{key}": values for key, values in VERSION7B_DRAW_GRID_SPACE.items()}
        )
    keys = tuple(combined)
    for combination in itertools.product(*(combined[key] for key in keys)):
        yield dict(zip(keys, combination))


def _parameter_signature(parameters: Version7BParameters) -> str:
    return json.dumps(
        parameters.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _run_id(
    dataset: ModelOptimizationDataset, configuration: SearchConfiguration
) -> str:
    payload = {
        "period": dataset.actual_period,
        "training": dataset.training_period,
        "validation": dataset.validation_period,
        "league": dataset.target_league,
        "method": configuration.method,
        "trials": configuration.trial_count,
        "seed": configuration.random_seed,
        "draw": configuration.include_draw_parameters,
        "weights": configuration.evaluation_weights.as_dict(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


PARTIAL_TRIAL_COLUMNS = (
    "run_id",
    "saved_at",
    "trial_number",
    "search_stage",
    "selection_score",
    "raw_validation_score",
    "training_score",
    "brier_score",
    "log_loss",
    "calibration",
    "accuracy",
    "draw_f1",
    "draw_degradation",
    "parameters",
)


def save_partial_trial(
    run_id: str,
    record: TrialRecord,
    path: Path = DEFAULT_PARTIAL_TRIALS_PATH,
) -> None:
    """各Trial完了直後に追記し、中断済みTrialを失わない。"""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        if exists:
            with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.reader(csv_file)
                header = tuple(next(reader, ()))
            if header != PARTIAL_TRIAL_COLUMNS:
                raise ModelOptimizationError(
                    "Version7-B途中履歴CSVの列が壊れています。"
                )
        with path.open(
            "a", encoding="utf-8-sig" if not exists else "utf-8", newline=""
        ) as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=PARTIAL_TRIAL_COLUMNS,
                lineterminator="\n",
            )
            if not exists:
                writer.writeheader()
            metrics = record.selection_validation.metrics
            writer.writerow(
                {
                    "run_id": run_id,
                    "saved_at": datetime.now(JAPAN_TIMEZONE).isoformat(),
                    "trial_number": record.trial_number,
                    "search_stage": record.search_stage,
                    "selection_score": record.selection_score,
                    "raw_validation_score": record.raw_validation_score,
                    "training_score": record.training.score,
                    "brier_score": metrics.brier_score,
                    "log_loss": metrics.log_loss,
                    "calibration": metrics.calibration_error,
                    "accuracy": metrics.accuracy,
                    "draw_f1": record.selection_validation.draw.f1_score,
                    "draw_degradation": record.draw_degradation.label,
                    "parameters": _parameter_signature(record.parameters),
                }
            )
            csv_file.flush()
            os.fsync(csv_file.fileno())
    except ModelOptimizationError:
        raise
    except (OSError, UnicodeError, csv.Error, TypeError, ValueError) as error:
        raise ModelOptimizationError(
            f"途中Trialを保存できませんでした: {error}"
        ) from error


def _evaluate_trial(
    trial_number: int,
    stage: str,
    parameters: Version7BParameters,
    dataset: ModelOptimizationDataset,
    configuration: SearchConfiguration,
    baseline_selection: CandidateEvaluation,
) -> TrialRecord:
    started = time.monotonic()
    training = evaluate_parameter_set(
        dataset.training_rounds,
        parameters,
        target_league=dataset.target_league,
        weights=configuration.evaluation_weights,
    )
    objective_rounds = _objective_rounds(dataset)
    selection = evaluate_parameter_set(
        objective_rounds,
        parameters,
        target_league=dataset.target_league,
        weights=configuration.evaluation_weights,
        fold_validation_rounds=_fold_validation_groups(dataset),
    )
    degradation = check_draw_degradation(
        baseline_selection,
        selection,
        configuration.draw_tolerances,
    )
    return TrialRecord(
        trial_number=trial_number,
        search_stage=stage,
        parameters=parameters,
        training=training,
        selection_validation=selection,
        raw_validation_score=selection.score,
        selection_score=max(0.0, selection.score - degradation.penalty),
        draw_degradation=degradation,
        duration_seconds=time.monotonic() - started,
    )


def _importance(records: Sequence[TrialRecord]) -> dict[str, float]:
    if len(records) < 2:
        return {}
    flattened = []
    for record in records:
        values = record.parameters.as_flat_dict()
        weights = values.pop("recent_match_weights", [])
        for index, value in enumerate(weights, start=1):
            values[f"recent_match_weight_{index}"] = value
        flattened.append((values, record.selection_score))
    importance = {}
    for key in sorted(set().union(*(values for values, _ in flattened))):
        pairs = [
            (float(values[key]), score)
            for values, score in flattened
            if isinstance(values.get(key), (int, float))
            and math.isfinite(float(values[key]))
        ]
        if len(pairs) < 2:
            continue
        xs = [item[0] for item in pairs]
        ys = [item[1] for item in pairs]
        if max(xs) == min(xs) or max(ys) == min(ys):
            importance[key] = 0.0
            continue
        mean_x = statistics.fmean(xs)
        mean_y = statistics.fmean(ys)
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
        denominator = math.sqrt(
            sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
        )
        importance[key] = abs(numerator / denominator) if denominator > 0 else 0.0
    total = sum(importance.values())
    return (
        {key: value / total for key, value in importance.items()}
        if total > 0
        else importance
    )


def run_model_optimization(
    dataset: ModelOptimizationDataset,
    configuration: SearchConfiguration = SearchConfiguration(),
    *,
    current_settings: Optional[ActiveVersion7BSettings] = None,
    progress_callback: Optional[ProgressCallback] = None,
    should_stop: Optional[StopCallback] = None,
    partial_path: Path = DEFAULT_PARTIAL_TRIALS_PATH,
) -> OptimizationResult:
    """探索内Validationで順位を確定し、最終Validationはその後だけ評価する。"""

    configuration.validate()
    plan = build_search_plan(configuration)
    if not plan.executable:
        raise ModelOptimizationError(plan.reason)
    current = current_settings or default_active_settings()
    # 過去にVersion7-Bを採用済みでも、比較・引分保護の基準は常に
    # Version7-A（既存全体係数 + 現在のVersion7-A引分設定）へ固定する。
    version7a_baseline = default_active_settings()
    objective_rounds = _objective_rounds(dataset)
    fold_groups = _fold_validation_groups(dataset)
    baseline_training = evaluate_parameter_set(
        dataset.training_rounds,
        version7a_baseline.parameters,
        target_league=dataset.target_league,
        weights=configuration.evaluation_weights,
    )
    baseline_selection = evaluate_parameter_set(
        objective_rounds,
        version7a_baseline.parameters,
        target_league=dataset.target_league,
        weights=configuration.evaluation_weights,
        fold_validation_rounds=fold_groups,
    )
    baseline_final = evaluate_parameter_set(
        dataset.validation_rounds,
        version7a_baseline.parameters,
        target_league=dataset.target_league,
        weights=configuration.evaluation_weights,
    )
    run_id = _run_id(dataset, configuration)
    started_at = datetime.now(JAPAN_TIMEZONE)
    monotonic_started = time.monotonic()
    records: list[TrialRecord] = []
    signatures: set[str] = set()

    def add_candidate(parameters: Version7BParameters, stage: str) -> float:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt("ユーザー操作により探索を停止しました。")
        signature = _parameter_signature(parameters)
        if signature in signatures:
            return -1.0
        record = _evaluate_trial(
            len(records) + 1,
            stage,
            parameters,
            dataset,
            configuration,
            baseline_selection,
        )
        records.append(record)
        signatures.add(signature)
        save_partial_trial(run_id, record, partial_path)
        best = max(records, key=lambda item: item.selection_score)
        if progress_callback is not None:
            progress_callback(
                TrialProgress(
                    current_trial=len(records),
                    total_trials=plan.executable_models,
                    elapsed_seconds=time.monotonic() - monotonic_started,
                    best_score=best.selection_score,
                    best_validation_score=best.raw_validation_score,
                    best_brier=best.selection_validation.metrics.brier_score,
                    best_log_loss=best.selection_validation.metrics.log_loss,
                    best_draw_f1=best.selection_validation.draw.f1_score,
                    current_parameters=parameters,
                )
            )
        return record.selection_score

    # 現在設定を必ず候補へ含め、探索が悪化した場合も比較できるようにする。
    add_candidate(current.parameters, "current")
    if configuration.method in (OPTUNA_SEARCH, TWO_STAGE_SEARCH):
        try:
            import optuna
        except ImportError as error:
            raise OptunaUnavailableError(
                "Optunaが未インストールです。requirements.txtを導入してください。"
            ) from error
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        stage1_target = (
            max(1, math.ceil(plan.executable_models * 0.70))
            if configuration.method == TWO_STAGE_SEARCH
            else plan.executable_models
        )
        sampler = optuna.samplers.TPESampler(seed=configuration.random_seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)

        def objective(trial: Any) -> float:
            values = _suggest_optuna_values(
                trial,
                include_draw_parameters=configuration.include_draw_parameters,
            )
            parameters = Version7BParameters.from_mapping(
                values,
                base=current.parameters,
            )
            return add_candidate(parameters, "stage1_optuna")

        # TPEが既評価済みの組み合わせを再提案する場合がある。重複はモデル数へ
        # 数えず、指定した一意モデル数へ到達するまで追加提案する。
        optuna_attempts = 0
        maximum_optuna_attempts = max(20, stage1_target * 20)
        while (
            len(records) < stage1_target and optuna_attempts < maximum_optuna_attempts
        ):
            missing = stage1_target - len(records)
            study.optimize(objective, n_trials=missing, show_progress_bar=False)
            optuna_attempts += missing
        if len(records) < stage1_target:
            raise ModelOptimizationError(
                "Optunaが一意な候補を十分に生成できませんでした。"
                f"完了{len(records):,}モデル / 予定{stage1_target:,}モデル"
            )

        if (
            configuration.method == TWO_STAGE_SEARCH
            and len(records) < plan.executable_models
        ):
            top = sorted(records, key=lambda item: item.selection_score, reverse=True)[
                :3
            ]
            refine_fields = (
                "home_correction",
                "elo_correction_rate",
                "venue_mix_rate",
            )
            for base_record in top:
                base_values = base_record.parameters.model.as_dict()
                grids = []
                for field_name in refine_fields:
                    spec = VERSION7B_MODEL_SEARCH_SPACE[field_name]
                    step = float(spec["step"])
                    center = float(base_values[field_name])
                    grids.append(
                        tuple(
                            max(
                                float(spec["low"]),
                                min(float(spec["high"]), center + offset * step),
                            )
                            for offset in (-1, 0, 1)
                        )
                    )
                for combination in itertools.product(*grids):
                    if len(records) >= plan.executable_models:
                        break
                    values = dict(zip(refine_fields, combination))
                    parameters = Version7BParameters.from_mapping(
                        values,
                        base=base_record.parameters,
                    )
                    add_candidate(parameters, "stage2_grid")
                if len(records) >= plan.executable_models:
                    break

    elif configuration.method == RANDOM_SEARCH:
        rng = random.Random(configuration.random_seed)
        attempts = 0
        while len(records) < plan.executable_models:
            attempts += 1
            if attempts > plan.executable_models * 20:
                break
            parameters = Version7BParameters.from_mapping(
                _random_values(
                    rng,
                    include_draw_parameters=configuration.include_draw_parameters,
                ),
                base=current.parameters,
            )
            add_candidate(parameters, "random")

    elif configuration.method == GRID_SEARCH:
        for values in _grid_values(configuration.include_draw_parameters):
            if len(records) >= plan.executable_models:
                break
            parameters = Version7BParameters.from_mapping(
                values, base=current.parameters
            )
            add_candidate(parameters, "grid")

    if not records:
        raise ModelOptimizationError("正常に完了した探索モデルがありません。")
    ordered = tuple(
        sorted(
            records,
            key=lambda item: (
                item.selection_score,
                -(item.selection_validation.metrics.brier_score or 2.0),
                -item.trial_number,
            ),
            reverse=True,
        )
    )
    # 最終Validationは順位確定後に上位20件だけ評価し、順位変更には使わない。
    ranking_records = []
    for record in ordered[:VERSION7B_RANKING_LIMIT]:
        final_validation = evaluate_parameter_set(
            dataset.validation_rounds,
            record.parameters,
            target_league=dataset.target_league,
            weights=configuration.evaluation_weights,
        )
        ranking_records.append(replace(record, final_validation=final_validation))
    ranking = tuple(ranking_records)
    best = ranking[0]
    best_final = best.final_validation
    if best_final is None:
        raise ModelOptimizationError("最良候補のValidationを評価できませんでした。")
    overfitting = check_overfitting(
        best.training,
        best_final,
        configuration.overfit_thresholds,
    )
    final_draw_check = check_draw_degradation(
        baseline_final,
        best_final,
        configuration.draw_tolerances,
    )
    stability = build_stability_summary(
        (*best.training.rows, *best_final.rows),
        configuration.evaluation_weights,
        league_rows=best_final.rows,
    )
    completed_at = datetime.now(JAPAN_TIMEZONE)
    return OptimizationResult(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        configuration=configuration,
        search_plan=plan,
        dataset=dataset,
        current_settings=current,
        ranking=ranking,
        all_trials=ordered,
        baseline_training=baseline_training,
        baseline_selection_validation=baseline_selection,
        baseline_final_validation=baseline_final,
        best_training=best.training,
        best_selection_validation=best.selection_validation,
        best_final_validation=best_final,
        best_parameters=best.parameters,
        overfitting=overfitting,
        draw_degradation=final_draw_check,
        stability=stability,
        parameter_importance=_importance(ordered),
    )


OPTIMIZATION_HISTORY_COLUMNS = (
    "run_id",
    "executed_at",
    "version",
    "search_method",
    "trial_count",
    "explored_models",
    "random_seed",
    "requested_period",
    "actual_period",
    "target_league",
    "validation_method",
    "training_period",
    "validation_period",
    "training_match_count",
    "validation_match_count",
    "evaluation_weights",
    "include_draw_parameters",
    "best_score",
    "best_validation_score",
    "brier_score",
    "log_loss",
    "calibration",
    "accuracy",
    "draw_precision",
    "draw_recall",
    "draw_f1",
    "draw_brier",
    "draw_calibration",
    "overfitting",
    "draw_degradation",
    "best_parameters",
    "adopted",
)

MODEL_RANKING_COLUMNS = (
    "run_id",
    "executed_at",
    "rank",
    "trial_number",
    "search_stage",
    "score",
    "validation_score",
    "training_score",
    "brier_score",
    "log_loss",
    "calibration",
    "accuracy",
    "draw_precision",
    "draw_recall",
    "draw_f1",
    "draw_brier",
    "draw_calibration",
    "prediction_share_1",
    "prediction_share_0",
    "prediction_share_2",
    "overfitting",
    "draw_degradation",
    "parameters",
)


def _read_csv_rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if tuple(reader.fieldnames or ()) != columns:
                raise ModelOptimizationError(f"{path.name}の列が壊れています。")
            return list(reader)
    except ModelOptimizationError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise ModelOptimizationError(f"{path.name}を読み込めません: {error}") from error


def _write_csv_rows(
    path: Path,
    columns: tuple[str, ...],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in columns})
        temporary_path.replace(path)
    except (OSError, UnicodeError, csv.Error, TypeError, ValueError) as error:
        temporary_path.unlink(missing_ok=True)
        raise ModelOptimizationError(f"{path.name}を保存できません: {error}") from error


def _history_row(result: OptimizationResult, adopted: bool) -> dict[str, Any]:
    evaluation = result.best_final_validation
    metrics = evaluation.metrics
    draw = evaluation.draw
    return {
        "run_id": result.run_id,
        "executed_at": result.completed_at.isoformat(),
        "version": VERSION7B_MODEL_VERSION,
        "search_method": result.configuration.method,
        "trial_count": result.configuration.trial_count,
        "explored_models": len(result.all_trials),
        "random_seed": result.configuration.random_seed,
        "requested_period": result.dataset.requested_period,
        "actual_period": result.dataset.actual_period,
        "target_league": result.dataset.target_league,
        "validation_method": result.dataset.split.method,
        "training_period": result.dataset.training_period,
        "validation_period": result.dataset.validation_period,
        "training_match_count": result.dataset.training_match_count,
        "validation_match_count": result.dataset.validation_match_count,
        "evaluation_weights": json.dumps(
            result.configuration.evaluation_weights.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
        ),
        "include_draw_parameters": result.configuration.include_draw_parameters,
        "best_score": result.best_score,
        "best_validation_score": result.best_validation_score,
        "brier_score": metrics.brier_score,
        "log_loss": metrics.log_loss,
        "calibration": metrics.calibration_error,
        "accuracy": metrics.accuracy,
        "draw_precision": draw.precision,
        "draw_recall": draw.recall,
        "draw_f1": draw.f1_score,
        "draw_brier": draw.brier_score,
        "draw_calibration": draw.calibration_error,
        "overfitting": result.overfitting.label,
        "draw_degradation": result.draw_degradation.label,
        "best_parameters": _parameter_signature(result.best_parameters),
        "adopted": bool(adopted),
    }


def save_optimization_history(
    result: OptimizationResult,
    *,
    adopted: bool = False,
    path: Path = DEFAULT_OPTIMIZATION_HISTORY_PATH,
) -> bool:
    """1実行1行で保存し、同一run_idの再保存は安全に置換する。"""

    rows = _read_csv_rows(path, OPTIMIZATION_HISTORY_COLUMNS)
    rows = [row for row in rows if row.get("run_id") != result.run_id]
    rows.append(_history_row(result, adopted))
    _write_csv_rows(path, OPTIMIZATION_HISTORY_COLUMNS, rows)
    return True


def mark_optimization_adopted(
    run_id: str,
    *,
    path: Path = DEFAULT_OPTIMIZATION_HISTORY_PATH,
) -> bool:
    rows = _read_csv_rows(path, OPTIMIZATION_HISTORY_COLUMNS)
    found = False
    for row in rows:
        if row.get("run_id") == str(run_id):
            row["adopted"] = "True"
            found = True
    if not found:
        return False
    _write_csv_rows(path, OPTIMIZATION_HISTORY_COLUMNS, rows)
    return True


def load_optimization_history(
    path: Path = DEFAULT_OPTIMIZATION_HISTORY_PATH,
) -> tuple[dict[str, str], ...]:
    """過去のVersion7-B実行を画面から確認できる形で安全に読み込む。"""

    return tuple(_read_csv_rows(path, OPTIMIZATION_HISTORY_COLUMNS))


def save_model_ranking(
    result: OptimizationResult,
    path: Path = DEFAULT_MODEL_RANKING_PATH,
) -> bool:
    """上位20モデルをrun_id付きで追記し、既存実行分だけ置換する。"""

    rows = _read_csv_rows(path, MODEL_RANKING_COLUMNS)
    rows = [row for row in rows if row.get("run_id") != result.run_id]
    for rank, record in enumerate(result.ranking, start=1):
        final = record.final_validation or record.selection_validation
        metrics = final.metrics
        draw = final.draw
        candidate_overfit = check_overfitting(
            record.training,
            final,
            result.configuration.overfit_thresholds,
        )
        candidate_draw_check = check_draw_degradation(
            result.baseline_final_validation,
            final,
            result.configuration.draw_tolerances,
        )
        rows.append(
            {
                "run_id": result.run_id,
                "executed_at": result.completed_at.isoformat(),
                "rank": rank,
                "trial_number": record.trial_number,
                "search_stage": record.search_stage,
                "score": record.selection_score,
                "validation_score": final.score,
                "training_score": record.training.score,
                "brier_score": metrics.brier_score,
                "log_loss": metrics.log_loss,
                "calibration": metrics.calibration_error,
                "accuracy": metrics.accuracy,
                "draw_precision": draw.precision,
                "draw_recall": draw.recall,
                "draw_f1": draw.f1_score,
                "draw_brier": draw.brier_score,
                "draw_calibration": draw.calibration_error,
                "prediction_share_1": metrics.prediction_share["1"],
                "prediction_share_0": metrics.prediction_share["0"],
                "prediction_share_2": metrics.prediction_share["2"],
                "overfitting": candidate_overfit.label,
                "draw_degradation": candidate_draw_check.label,
                "parameters": _parameter_signature(record.parameters),
            }
        )
    _write_csv_rows(path, MODEL_RANKING_COLUMNS, rows)
    return True
