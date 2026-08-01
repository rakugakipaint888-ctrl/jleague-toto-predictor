"""開催日時点より前のデータだけでVersion5モデルを再実行する。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from data_loader import (
    JAPAN_TIMEZONE,
    LEAGUE_FRAME_IDS,
    VISION_LEAGUE_FRAME_IDS,
    VISION_LEAGUE_YEAR_ID,
    JLeagueOfficialDataSource,
    OfficialMatch,
    OfficialSchedulePage,
    RecentMatchRecord,
    TeamRecentStats,
    VenueRecord,
)
from elo_rating import generate_elo_ratings, get_team_elo
from history_manager import TotoMatch, TotoRound
from metrics import (
    DEFAULT_TOTO_STAKE_YEN,
    ModelMetrics,
    evaluate_model,
    toto_payout_for_hits,
)
from model_pipeline import ModelOptions, TeamModelInput, predict_match
from prediction_history import PredictionHistoryRecord
from teams import TEAM_CATEGORY_BY_NAME, get_team_category, normalize_team_name


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_BACKTEST_HISTORY_CACHE_PATH = (
    PROJECT_ROOT / "data" / "cache" / "backtest_match_history.csv"
)
DEFAULT_BACKTEST_HISTORY_SEED_PATH = (
    PROJECT_ROOT
    / "data"
    / "reference"
    / "jleague_history_2024_2025.csv"
)
BACKTEST_HISTORY_COLUMNS = (
    "match_time",
    "home_team",
    "away_team",
    "home_goals",
    "away_goals",
    "category",
)


class BacktestError(RuntimeError):
    """バックテストを安全に続行できない場合の共通例外。"""


class BacktestDataLeakError(BacktestError):
    """基準日時以後のデータがモデル入力へ混入した。"""


@dataclass(frozen=True)
class VersionPrediction:
    """1試合・1Versionの予測値。"""

    version: str
    prediction: str
    probabilities: Mapping[str, float]
    home_expected_goals: float
    away_expected_goals: float

    @property
    def top_probability(self) -> float:
        return float(self.probabilities.get(self.prediction, 0.0))


@dataclass(frozen=True)
class BacktestMatchResult:
    """toto公式試合順で保持するVersion4～6と実結果。"""

    toto_match: TotoMatch
    versions: Mapping[str, VersionPrediction]

    @property
    def actual_result(self) -> str:
        return self.toto_match.actual_result or ""


@dataclass(frozen=True)
class BacktestResult:
    """1開催回のバックテスト結果とモデル評価。"""

    toto_round: TotoRound
    cutoff_at: datetime
    historical_match_count: int
    matches: tuple[BacktestMatchResult, ...]
    metrics_by_version: Mapping[str, ModelMetrics]
    generated_at: datetime

    def history_records(self) -> list[PredictionHistoryRecord]:
        records = []
        generated_text = self.generated_at.isoformat()
        for match_result in self.matches:
            for version, prediction in match_result.versions.items():
                metrics = self.metrics_by_version[version]
                records.append(
                    PredictionHistoryRecord(
                        toto_round=self.toto_round.round_id,
                        toto_match_number=match_result.toto_match.match_number,
                        prediction_version=version,
                        prediction_date=generated_text,
                        home_team=match_result.toto_match.home_team,
                        away_team=match_result.toto_match.away_team,
                        prediction=prediction.prediction,
                        probability_1=prediction.probabilities["1"],
                        probability_0=prediction.probabilities["0"],
                        probability_2=prediction.probabilities["2"],
                        home_expected_goals=prediction.home_expected_goals,
                        away_expected_goals=prediction.away_expected_goals,
                        actual_result=match_result.actual_result,
                        hit=(
                            prediction.prediction == match_result.actual_result
                        ),
                        total_hits=metrics.hit_count,
                        accuracy=metrics.accuracy,
                        brier_score=metrics.brier_score,
                        log_loss=metrics.log_loss,
                        calibration=metrics.calibration_error,
                        expected_hits=metrics.expected_hits,
                        stake_yen=metrics.stake_yen,
                        payout_yen=metrics.payout_yen,
                        roi=metrics.roi,
                    )
                )
        return records


def _match_identity(match: OfficialMatch) -> tuple[Any, ...]:
    return (
        match.match_time.isoformat(),
        normalize_team_name(match.home_team),
        normalize_team_name(match.away_team),
        match.home_goals,
        match.away_goals,
        match.category,
    )


def _deduplicate_matches(
    matches: Iterable[OfficialMatch],
) -> list[OfficialMatch]:
    unique: dict[tuple[Any, ...], OfficialMatch] = {}
    for match in matches:
        unique[_match_identity(match)] = match
    return sorted(unique.values(), key=lambda match: match.match_time)


def save_historical_matches_csv(
    matches: Sequence[OfficialMatch],
    path: Path = DEFAULT_BACKTEST_HISTORY_CACHE_PATH,
) -> bool:
    """公式履歴を次回取得失敗時のCSVへ原子的に保存する。"""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        with temporary_path.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=BACKTEST_HISTORY_COLUMNS,
                lineterminator="\n",
            )
            writer.writeheader()
            for match in _deduplicate_matches(matches):
                writer.writerow(
                    {
                        "match_time": match.match_time.isoformat(),
                        "home_team": match.home_team,
                        "away_team": match.away_team,
                        "home_goals": (
                            "" if match.home_goals is None else match.home_goals
                        ),
                        "away_goals": (
                            "" if match.away_goals is None else match.away_goals
                        ),
                        "category": match.category,
                    }
                )
        temporary_path.replace(path)
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_historical_matches_csv(path: Path) -> tuple[OfficialMatch, ...]:
    """保存CSVを読み、壊れた行は無視して公式試合形式へ戻す。"""

    if not path.exists():
        return ()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if not set(BACKTEST_HISTORY_COLUMNS).issubset(
                set(reader.fieldnames or ())
            ):
                return ()
            matches = []
            for row in reader:
                try:
                    match_time = datetime.fromisoformat(row["match_time"])
                    if match_time.tzinfo is None:
                        match_time = match_time.replace(tzinfo=JAPAN_TIMEZONE)
                    home_team = normalize_team_name(row["home_team"])
                    away_team = normalize_team_name(row["away_team"])
                    home_goals = (
                        int(row["home_goals"])
                        if str(row["home_goals"]).strip()
                        else None
                    )
                    away_goals = (
                        int(row["away_goals"])
                        if str(row["away_goals"]).strip()
                        else None
                    )
                    matches.append(
                        OfficialMatch(
                            match_time=match_time,
                            home_team=home_team,
                            away_team=away_team,
                            home_goals=home_goals,
                            away_goals=away_goals,
                            category=str(row["category"]).strip(),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
    except (OSError, UnicodeError, csv.Error):
        return ()
    return tuple(_deduplicate_matches(matches))


def historical_schedule_pages(toto_round: TotoRound) -> tuple[OfficialSchedulePage, ...]:
    """対象日時点の現行大会と直前大会だけを返す。"""

    start_time = toto_round.start_time
    if start_time is None:
        raise BacktestError("開催日時を確認できません。")
    target_date = start_time.astimezone(JAPAN_TIMEZONE).date()

    if target_date >= date(2026, 7, 1):
        return (
            OfficialSchedulePage(
                VISION_LEAGUE_YEAR_ID,
                tuple(VISION_LEAGUE_FRAME_IDS),
            ),
            OfficialSchedulePage(
                str(target_date.year),
                tuple(LEAGUE_FRAME_IDS.values()),
            ),
        )
    if target_date.year == 2026:
        return (
            OfficialSchedulePage(
                "2025",
                tuple(LEAGUE_FRAME_IDS.values()),
            ),
            OfficialSchedulePage(
                VISION_LEAGUE_YEAR_ID,
                tuple(VISION_LEAGUE_FRAME_IDS),
            ),
        )
    return (
        OfficialSchedulePage(
            str(target_date.year - 1),
            tuple(LEAGUE_FRAME_IDS.values()),
        ),
        OfficialSchedulePage(
            str(target_date.year),
            tuple(LEAGUE_FRAME_IDS.values()),
        ),
    )


def fetch_historical_matches(
    toto_round: TotoRound,
    *,
    data_source: Optional[JLeagueOfficialDataSource] = None,
    fallback_matches: Sequence[OfficialMatch] = (),
    cache_path: Path = DEFAULT_BACKTEST_HISTORY_CACHE_PATH,
    seed_path: Path = DEFAULT_BACKTEST_HISTORY_SEED_PATH,
) -> tuple[OfficialMatch, ...]:
    """公式→保存CSV→同梱CSV→現在データの順で履歴を統合する。"""

    source = data_source or JLeagueOfficialDataSource(timeout_seconds=45.0)
    official_matches = []
    failed_page_count = 0
    for page in historical_schedule_pages(toto_round):
        try:
            page_matches = source.fetch_schedule_page(page)
        except Exception:
            # 片方の年度だけ失敗しても、成功した年度は捨てない。
            failed_page_count += 1
            continue
        official_matches.extend(page_matches)

    if official_matches and failed_page_count == 0:
        loaded = tuple(_deduplicate_matches(official_matches))
        save_historical_matches_csv(loaded, cache_path)
        return loaded

    saved_matches = [
        *load_historical_matches_csv(cache_path),
        *load_historical_matches_csv(seed_path),
    ]
    combined_matches = _deduplicate_matches(
        [*official_matches, *saved_matches, *fallback_matches]
    )
    if combined_matches:
        save_historical_matches_csv(combined_matches, cache_path)
        return tuple(combined_matches)
    raise BacktestError("バックテスト用Jリーグ履歴を取得できませんでした。")


def backtest_cutoff(toto_round: TotoRound) -> datetime:
    """同日先行試合も使わない保守的な開催初日0:00を返す。"""

    start_time = toto_round.start_time
    if start_time is None:
        raise BacktestError("開催日時を確認できません。")
    local_start = start_time.astimezone(JAPAN_TIMEZONE)
    return local_start.replace(hour=0, minute=0, second=0, microsecond=0)


def _completed_before(
    matches: Sequence[OfficialMatch],
    cutoff_at: datetime,
) -> list[OfficialMatch]:
    result = []
    for match in matches:
        match_time = match.match_time
        if match_time.tzinfo is None:
            match_time = match_time.replace(tzinfo=JAPAN_TIMEZONE)
        if (
            match.is_completed
            and match_time.astimezone(JAPAN_TIMEZONE) < cutoff_at
        ):
            result.append(match)
    return sorted(result, key=lambda match: match.match_time)


def _season_start(cutoff_at: datetime) -> date:
    local_date = cutoff_at.astimezone(JAPAN_TIMEZONE).date()
    if local_date >= date(2026, 7, 1):
        start_year = local_date.year if local_date.month >= 7 else local_date.year - 1
        return date(start_year, 7, 1)
    return date(local_date.year, 1, 1)


def _team_matches(
    matches: Sequence[OfficialMatch],
    team_name: str,
) -> list[OfficialMatch]:
    return [
        match
        for match in matches
        if team_name in (match.home_team, match.away_team)
    ]


def _venue_record(
    matches: Sequence[OfficialMatch],
    team_name: str,
    venue: str = "all",
) -> VenueRecord:
    played = wins = draws = losses = goals_for = goals_against = 0

    for match in _team_matches(matches, team_name):
        is_home = match.home_team == team_name
        if venue == "home" and not is_home:
            continue
        if venue == "away" and is_home:
            continue
        scored = int(match.home_goals if is_home else match.away_goals)
        conceded = int(match.away_goals if is_home else match.home_goals)
        played += 1
        goals_for += scored
        goals_against += conceded
        if scored > conceded:
            wins += 1
        elif scored == conceded:
            draws += 1
        else:
            losses += 1

    return VenueRecord(
        played=played,
        wins=wins,
        draws=draws,
        losses=losses,
        goals_for=goals_for,
        goals_against=goals_against,
    )


def _recent_record(match: OfficialMatch, team_name: str) -> RecentMatchRecord:
    is_home = match.home_team == team_name
    scored = int(match.home_goals if is_home else match.away_goals)
    conceded = int(match.away_goals if is_home else match.home_goals)
    opponent = match.away_team if is_home else match.home_team
    result = "勝" if scored > conceded else "分" if scored == conceded else "敗"
    return RecentMatchRecord(
        match_date=match.match_time.astimezone(JAPAN_TIMEZONE).date(),
        opponent=opponent,
        venue="H" if is_home else "A",
        scored=scored,
        conceded=conceded,
        result=result,
    )


def _category_for_team(
    team_name: str,
    matches: Sequence[OfficialMatch],
) -> str:
    current_category = get_team_category(team_name)
    if current_category:
        return current_category
    for match in reversed(matches):
        if team_name in (match.home_team, match.away_team) and match.category:
            return match.category.split("/")[0]
    return ""


def _rank_by_category(
    season_matches: Sequence[OfficialMatch],
    team_names: Sequence[str],
) -> dict[str, int]:
    records = {team_name: _venue_record(season_matches, team_name) for team_name in team_names}
    categories = {
        team_name: _category_for_team(team_name, season_matches)
        for team_name in team_names
    }
    ranks: dict[str, int] = {}
    for category in sorted(set(categories.values())):
        category_teams = [
            team_name
            for team_name in team_names
            if categories[team_name] == category and records[team_name].played > 0
        ]
        category_teams.sort(
            key=lambda team_name: (
                -(records[team_name].wins * 3 + records[team_name].draws),
                -(records[team_name].goals_for - records[team_name].goals_against),
                -records[team_name].goals_for,
                team_name,
            )
        )
        for rank, team_name in enumerate(category_teams, start=1):
            ranks[team_name] = rank
    return ranks


def calculate_team_stats_as_of(
    matches: Sequence[OfficialMatch],
    cutoff_at: datetime,
    target_teams: Sequence[str],
) -> dict[str, TeamRecentStats]:
    """順位・直近・会場別成績を未来データなしで再構成する。"""

    completed = _completed_before(matches, cutoff_at)
    season_start = _season_start(cutoff_at)
    season_matches = [
        match
        for match in completed
        if match.match_time.astimezone(JAPAN_TIMEZONE).date() >= season_start
    ]
    all_team_names = sorted(
        set(target_teams)
        | {
            team_name
            for match in season_matches
            for team_name in (match.home_team, match.away_team)
        }
    )
    ranks = _rank_by_category(season_matches, all_team_names)
    stats_by_team = {}

    for team_name in target_teams:
        historical_team_matches = _team_matches(completed, team_name)
        recent_matches = sorted(
            historical_team_matches,
            key=lambda match: match.match_time,
            reverse=True,
        )[:5]
        recent_records = tuple(
            _recent_record(match, team_name) for match in recent_matches
        )
        season_record = _venue_record(season_matches, team_name)
        home_record = _venue_record(completed, team_name, "home")
        away_record = _venue_record(completed, team_name, "away")

        if recent_records:
            average_scored = sum(record.scored for record in recent_records) / len(recent_records)
            average_conceded = sum(record.conceded for record in recent_records) / len(recent_records)
        elif season_record.played > 0:
            average_scored = season_record.goals_for / season_record.played
            average_conceded = season_record.goals_against / season_record.played
        else:
            average_scored = average_conceded = 1.3

        points = (
            season_record.wins * 3 + season_record.draws
            if season_record.played > 0
            else None
        )
        goal_difference = (
            season_record.goals_for - season_record.goals_against
            if season_record.played > 0
            else None
        )
        stats_by_team[team_name] = TeamRecentStats(
            average_scored=round(average_scored, 4),
            average_conceded=round(average_conceded, 4),
            recent_matches=tuple(record.label for record in recent_records),
            recent_results=recent_records,
            rank=ranks.get(team_name),
            points=points,
            played=season_record.played,
            wins=season_record.wins,
            draws=season_record.draws,
            losses=season_record.losses,
            goals_for=season_record.goals_for,
            goals_against=season_record.goals_against,
            goal_difference=goal_difference,
            standings_available=season_record.played > 0,
            home_record=home_record,
            away_record=away_record,
        )

    return stats_by_team


def _team_input(
    team_name: str,
    stats: Optional[TeamRecentStats],
    elo_value: Optional[float],
    *,
    is_home: bool,
) -> TeamModelInput:
    if stats is None:
        return TeamModelInput(
            team_name=team_name,
            recent_scored_average=1.4 if is_home else 1.2,
            recent_conceded_average=1.2 if is_home else 1.4,
            elo=elo_value,
        )
    return TeamModelInput(
        team_name=team_name,
        recent_scored_average=stats.average_scored,
        recent_conceded_average=stats.average_conceded,
        recent_matches=stats.recent_results,
        season_scored_average=stats.season_average_scored,
        season_conceded_average=stats.season_average_conceded,
        venue_record=stats.home_record if is_home else stats.away_record,
        rank=stats.rank,
        points=stats.points if stats.standings_available else None,
        played=stats.played if stats.standings_available else None,
        goal_difference=(
            stats.goal_difference if stats.standings_available else None
        ),
        elo=elo_value,
    )


def _version_probabilities(probabilities: Mapping[str, float]) -> dict[str, float]:
    return {
        "1": float(probabilities["home_win"]),
        "0": float(probabilities["draw"]),
        "2": float(probabilities["away_win"]),
    }


def run_backtest(
    toto_round: TotoRound,
    historical_matches: Sequence[OfficialMatch],
    *,
    generated_at: Optional[datetime] = None,
) -> BacktestResult:
    """Version5を対象開催初日0:00時点へ巻き戻して実行する。"""

    if not toto_round.is_official_order_complete:
        raise BacktestError("toto公式の13試合順を確認できません。")
    if not toto_round.is_complete:
        raise BacktestError("13試合の実結果が確定していません。")
    if not toto_round.is_jleague_round:
        raise BacktestError("Jリーグ以外を含む開催回は対象外です。")

    cutoff_at = backtest_cutoff(toto_round)
    completed = _completed_before(historical_matches, cutoff_at)
    if not completed:
        raise BacktestError(
            "開催日時点より前のJリーグ試合履歴を取得できませんでした。"
            "保存CSVまたは通信状態を確認してください。"
        )
    if any(
        match.match_time.astimezone(JAPAN_TIMEZONE) >= cutoff_at
        for match in completed
    ):
        raise BacktestDataLeakError("未来の試合結果が混入しました。")

    target_teams = sorted(
        {
            team_name
            for match in toto_round.matches
            for team_name in (match.home_team, match.away_team)
        }
    )
    team_stats = calculate_team_stats_as_of(
        completed,
        cutoff_at,
        target_teams,
    )
    team_categories = {
        team_name: TEAM_CATEGORY_BY_NAME.get(team_name, "")
        for team_name in target_teams
    }
    elo_result = generate_elo_ratings(
        completed,
        team_categories=team_categories,
        as_of=cutoff_at,
        team_name_normalizer=normalize_team_name,
    )
    model_options = ModelOptions(
        use_elo=True,
        use_venue=True,
        use_recent_weighting=True,
        use_standings=True,
    )
    match_results = []

    for toto_match in sorted(
        toto_round.matches,
        key=lambda match: match.match_number,
    ):
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
        pipeline = predict_match(
            _team_input(
                toto_match.home_team,
                team_stats.get(toto_match.home_team),
                home_elo,
                is_home=True,
            ),
            _team_input(
                toto_match.away_team,
                team_stats.get(toto_match.away_team),
                away_elo,
                is_home=False,
            ),
            options=model_options,
        )
        version4 = VersionPrediction(
            version="Version4",
            prediction=pipeline.version4.prediction,
            probabilities=_version_probabilities(
                pipeline.version4.probabilities
            ),
            home_expected_goals=pipeline.version4.expected_after_elo.home,
            away_expected_goals=pipeline.version4.expected_after_elo.away,
        )
        version5 = VersionPrediction(
            version="Version5",
            prediction=pipeline.version5_prediction,
            probabilities=_version_probabilities(
                pipeline.version5_probabilities
            ),
            home_expected_goals=pipeline.expected_final.home,
            away_expected_goals=pipeline.expected_final.away,
        )
        version6 = VersionPrediction(
            version="Version6",
            prediction=version5.prediction,
            probabilities=dict(version5.probabilities),
            home_expected_goals=version5.home_expected_goals,
            away_expected_goals=version5.away_expected_goals,
        )
        match_results.append(
            BacktestMatchResult(
                toto_match=toto_match,
                versions={
                    "Version4": version4,
                    "Version5": version5,
                    "Version6": version6,
                },
            )
        )

    metrics_by_version = {}
    actuals = [result.actual_result for result in match_results]
    for version in ("Version4", "Version5", "Version6"):
        predictions = [
            result.versions[version].prediction
            for result in match_results
        ]
        probabilities = [
            result.versions[version].probabilities
            for result in match_results
        ]
        hit_count = sum(
            prediction == actual
            for prediction, actual in zip(predictions, actuals)
        )
        payout_yen = toto_payout_for_hits(
            hit_count,
            toto_round.payouts.first_prize_yen,
            toto_round.payouts.second_prize_yen,
            toto_round.payouts.third_prize_yen,
        )
        metrics_by_version[version] = evaluate_model(
            predictions,
            probabilities,
            actuals,
            stake_yen=DEFAULT_TOTO_STAKE_YEN,
            payout_yen=payout_yen,
        )

    generated = generated_at or datetime.now(JAPAN_TIMEZONE)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=JAPAN_TIMEZONE)
    return BacktestResult(
        toto_round=toto_round,
        cutoff_at=cutoff_at,
        historical_match_count=len(completed),
        matches=tuple(match_results),
        metrics_by_version=metrics_by_version,
        generated_at=generated.astimezone(JAPAN_TIMEZONE),
    )
