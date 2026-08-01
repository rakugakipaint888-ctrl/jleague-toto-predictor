"""J1・J2・J3のElo計算とポアソン期待得点補正を提供する。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from config import DEFAULT_ELO_SETTINGS, ELO_CACHE_VERSION, EloSettings


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ELO_CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "elo_ratings.json"
TeamNameNormalizer = Callable[[Any], str]


def _default_team_name_normalizer(value: Any) -> str:
    """Elo単体利用時の最小限の入力整形を行う。"""

    if value is None:
        return ""

    try:
        if value != value:  # NaN相当
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


@dataclass(frozen=True)
class EloTeamRating:
    """1クラブの現在Eloと更新情報。"""

    team_name: str
    category: str
    rating: float
    matches_played: int = 0
    last_updated: Optional[date] = None


@dataclass(frozen=True)
class EloCalculationResult:
    """全クラブのElo計算結果と利用データ範囲。"""

    ratings: dict[str, EloTeamRating]
    processed_match_count: int
    data_start_date: Optional[date]
    data_end_date: Optional[date]
    from_cache: bool = False
    incremental_match_count: int = 0

    @property
    def is_available(self) -> bool:
        return self.processed_match_count > 0


@dataclass(frozen=True)
class ExpectedGoalsAdjustment:
    """Elo補正前後の期待得点と適用率。"""

    home_before: float
    away_before: float
    home_after: float
    away_after: float
    elo_difference: float
    adjustment_rate: float
    enabled: bool


@dataclass(frozen=True)
class _PreparedMatch:
    match_time: datetime
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    category: str

    @property
    def identity(self) -> str:
        values = (
            self.match_time.isoformat(),
            self.home_team,
            self.away_team,
            self.home_goals,
            self.away_goals,
            self.category,
        )
        return hashlib.sha256(
            json.dumps(values, ensure_ascii=False).encode("utf-8")
        ).hexdigest()


def get_elo_cache_path() -> Path:
    """テストや別環境では環境変数で保存先を差し替えられる。"""

    configured_path = os.getenv("JLEAGUE_ELO_CACHE_PATH")
    return Path(configured_path) if configured_path else DEFAULT_ELO_CACHE_PATH


def calculate_expected_score(rating_a: float, rating_b: float) -> float:
    """標準Elo式でA側の期待スコアを返す。"""

    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def get_goal_difference_multiplier(
    goal_difference: int,
    settings: EloSettings = DEFAULT_ELO_SETTINGS,
) -> float:
    """得失点差に対応するK係数倍率を返す。"""

    if not settings.goal_difference_adjustment_enabled:
        return 1.0

    difference = max(1, abs(int(goal_difference)))
    multiplier = 1.0

    for threshold, configured_multiplier in settings.goal_difference_multipliers:
        if difference >= threshold:
            multiplier = float(configured_multiplier)

    return multiplier


def update_elo_ratings(
    home_rating: float,
    away_rating: float,
    home_goals: int,
    away_goals: int,
    settings: EloSettings = DEFAULT_ELO_SETTINGS,
) -> tuple[float, float]:
    """1試合を反映し、更新後のホーム・アウェイEloを返す。"""

    expected_home = calculate_expected_score(
        float(home_rating) + settings.home_advantage,
        float(away_rating),
    )

    if home_goals > away_goals:
        actual_home = 1.0
    elif home_goals == away_goals:
        actual_home = 0.5
    else:
        actual_home = 0.0

    multiplier = get_goal_difference_multiplier(
        abs(int(home_goals) - int(away_goals)),
        settings,
    )
    change = (
        settings.k_factor
        * multiplier
        * (actual_home - expected_home)
    )

    return float(home_rating) + change, float(away_rating) - change


def elo_difference_to_adjustment(
    elo_difference: float,
    settings: EloSettings = DEFAULT_ELO_SETTINGS,
) -> float:
    """Elo差を期待得点の増減率へ変換し、設定上限で制限する。"""

    raw_adjustment = (
        float(elo_difference)
        / 100.0
        * settings.expected_goals_change_per_100_elo
        * settings.elo_adjustment_strength
    )
    limit = abs(settings.expected_goals_max_adjustment)
    return max(-limit, min(raw_adjustment, limit))


def adjust_expected_goals(
    home_expected: float,
    away_expected: float,
    home_elo: float,
    away_elo: float,
    enabled: bool = True,
    settings: EloSettings = DEFAULT_ELO_SETTINGS,
) -> ExpectedGoalsAdjustment:
    """Version3の期待得点を保持し、Elo差を緩やかに反映する。"""

    home_before = float(home_expected)
    away_before = float(away_expected)
    elo_difference = float(home_elo) - float(away_elo)
    adjustment_rate = (
        elo_difference_to_adjustment(elo_difference, settings)
        if enabled
        else 0.0
    )

    return ExpectedGoalsAdjustment(
        home_before=home_before,
        away_before=away_before,
        home_after=home_before * (1.0 + adjustment_rate),
        away_after=away_before * (1.0 - adjustment_rate),
        elo_difference=elo_difference,
        adjustment_rate=adjustment_rate,
        enabled=bool(enabled),
    )


def _match_value(match: Any, field_name: str) -> Any:
    if isinstance(match, Mapping):
        return match.get(field_name)
    return getattr(match, field_name, None)


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_category(value: Any) -> str:
    normalized = str(value or "").upper().replace("Ｊ", "J")

    if "J1" in normalized and "J2" not in normalized and "J3" not in normalized:
        return "J1"
    if "J2" in normalized and "J3" not in normalized:
        return "J2"
    if "J3" in normalized and "J2" not in normalized:
        return "J3"
    return ""


def _prepare_matches(
    matches: Iterable[Any],
    as_of: Optional[datetime] = None,
    team_name_normalizer: TeamNameNormalizer = _default_team_name_normalizer,
) -> list[_PreparedMatch]:
    reference_time = _as_datetime(as_of or datetime.now(timezone.utc))
    prepared_matches = []

    for match in matches:
        match_time = _as_datetime(_match_value(match, "match_time"))
        home_team = team_name_normalizer(_match_value(match, "home_team"))
        away_team = team_name_normalizer(_match_value(match, "away_team"))
        home_goals = _match_value(match, "home_goals")
        away_goals = _match_value(match, "away_goals")

        if (
            match_time is None
            or reference_time is None
            or match_time > reference_time
            or not home_team
            or not away_team
            or home_goals is None
            or away_goals is None
        ):
            continue

        try:
            home_goals = int(home_goals)
            away_goals = int(away_goals)
        except (TypeError, ValueError):
            continue

        if home_goals < 0 or away_goals < 0:
            continue

        prepared_matches.append(
            _PreparedMatch(
                match_time=match_time,
                home_team=home_team,
                away_team=away_team,
                home_goals=home_goals,
                away_goals=away_goals,
                category=_normalize_category(
                    _match_value(match, "category")
                ),
            )
        )

    return sorted(
        prepared_matches,
        key=lambda match: (
            match.match_time,
            match.home_team,
            match.away_team,
        ),
    )


def _prepare_team_categories(
    team_categories: Optional[Mapping[str, str]],
    prepared_matches: Sequence[_PreparedMatch],
    team_name_normalizer: TeamNameNormalizer,
) -> dict[str, str]:
    """呼び出し元のクラブ構成をElo内部の入力形式へそろえる。"""

    if team_categories is not None:
        normalized_categories = {}

        for team_name, category in team_categories.items():
            normalized_name = team_name_normalizer(team_name)

            if normalized_name:
                normalized_categories[normalized_name] = (
                    _normalize_category(category) or str(category)
                )

        return normalized_categories

    # 単体利用では、履歴に含まれるクラブと最新カテゴリーから構成を作る。
    derived_categories = {}

    for match in prepared_matches:
        derived_categories[match.home_team] = match.category
        derived_categories[match.away_team] = match.category

    return derived_categories


def _settings_fingerprint(
    settings: EloSettings,
    team_categories: Mapping[str, str],
) -> str:
    payload = {
        "settings": settings.as_serializable_dict(),
        "team_categories": sorted(team_categories.items()),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _initial_category_for_team(
    team_name: str,
    matches: Sequence[_PreparedMatch],
    team_categories: Mapping[str, str],
) -> str:
    for match in matches:
        if (
            team_name in (match.home_team, match.away_team)
            and match.category in ("J1", "J2", "J3")
        ):
            return match.category
    return team_categories.get(team_name, "")


def _initial_state(
    matches: Sequence[_PreparedMatch],
    team_categories: Mapping[str, str],
    settings: EloSettings,
) -> tuple[dict[str, float], dict[str, int], dict[str, Optional[str]]]:
    ratings = {}
    match_counts = {}
    last_updated = {}

    for canonical_name, current_category in team_categories.items():
        initial_category = _initial_category_for_team(
            canonical_name,
            matches,
            team_categories,
        )
        ratings[canonical_name] = settings.initial_rating_for(
            initial_category or current_category
        )
        match_counts[canonical_name] = 0
        last_updated[canonical_name] = None

    return ratings, match_counts, last_updated


def _ensure_team(
    team_name: str,
    match_category: str,
    ratings: dict[str, float],
    match_counts: dict[str, int],
    last_updated: dict[str, Optional[str]],
    team_categories: Mapping[str, str],
    settings: EloSettings,
) -> None:
    if team_name in ratings:
        return

    category = team_categories.get(team_name) or match_category
    ratings[team_name] = settings.initial_rating_for(category)
    match_counts[team_name] = 0
    last_updated[team_name] = None


def _apply_matches(
    matches: Sequence[_PreparedMatch],
    ratings: dict[str, float],
    match_counts: dict[str, int],
    last_updated: dict[str, Optional[str]],
    team_categories: Mapping[str, str],
    settings: EloSettings,
) -> None:
    for match in matches:
        _ensure_team(
            match.home_team,
            match.category,
            ratings,
            match_counts,
            last_updated,
            team_categories,
            settings,
        )
        _ensure_team(
            match.away_team,
            match.category,
            ratings,
            match_counts,
            last_updated,
            team_categories,
            settings,
        )

        home_rating, away_rating = update_elo_ratings(
            ratings[match.home_team],
            ratings[match.away_team],
            match.home_goals,
            match.away_goals,
            settings,
        )
        ratings[match.home_team] = home_rating
        ratings[match.away_team] = away_rating
        match_counts[match.home_team] += 1
        match_counts[match.away_team] += 1
        updated_date = match.match_time.date().isoformat()
        last_updated[match.home_team] = updated_date
        last_updated[match.away_team] = updated_date


def _read_cache(cache_path: Path) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if payload.get("version") != ELO_CACHE_VERSION:
        return None
    return payload


def _write_cache(cache_path: Path, payload: dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(cache_path)
    except OSError:
        # キャッシュ保存失敗で予測自体を停止させない。
        return


def _result_from_state(
    prepared_matches: Sequence[_PreparedMatch],
    ratings: Mapping[str, float],
    match_counts: Mapping[str, int],
    last_updated: Mapping[str, Optional[str]],
    team_categories: Mapping[str, str],
    from_cache: bool,
    incremental_match_count: int,
) -> EloCalculationResult:
    public_ratings = {}

    for canonical_name, category in team_categories.items():
        updated_text = last_updated.get(canonical_name)
        public_ratings[canonical_name] = EloTeamRating(
            team_name=canonical_name,
            category=category,
            rating=float(ratings[canonical_name]),
            matches_played=int(match_counts.get(canonical_name, 0)),
            last_updated=(
                date.fromisoformat(updated_text)
                if updated_text
                else None
            ),
        )

    return EloCalculationResult(
        ratings=public_ratings,
        processed_match_count=len(prepared_matches),
        data_start_date=(
            prepared_matches[0].match_time.date()
            if prepared_matches
            else None
        ),
        data_end_date=(
            prepared_matches[-1].match_time.date()
            if prepared_matches
            else None
        ),
        from_cache=from_cache,
        incremental_match_count=incremental_match_count,
    )


def generate_elo_ratings(
    matches: Iterable[Any],
    team_categories: Optional[Mapping[str, str]] = None,
    settings: EloSettings = DEFAULT_ELO_SETTINGS,
    as_of: Optional[datetime] = None,
    team_name_normalizer: TeamNameNormalizer = _default_team_name_normalizer,
) -> EloCalculationResult:
    """キャッシュを使わず全対象試合からEloを生成する。"""

    prepared_matches = _prepare_matches(
        matches,
        as_of,
        team_name_normalizer,
    )
    prepared_team_categories = _prepare_team_categories(
        team_categories,
        prepared_matches,
        team_name_normalizer,
    )
    ratings, match_counts, last_updated = _initial_state(
        prepared_matches,
        prepared_team_categories,
        settings,
    )
    _apply_matches(
        prepared_matches,
        ratings,
        match_counts,
        last_updated,
        prepared_team_categories,
        settings,
    )
    return _result_from_state(
        prepared_matches,
        ratings,
        match_counts,
        last_updated,
        prepared_team_categories,
        from_cache=False,
        incremental_match_count=len(prepared_matches),
    )


def load_or_calculate_elo(
    matches: Iterable[Any],
    team_categories: Optional[Mapping[str, str]] = None,
    settings: EloSettings = DEFAULT_ELO_SETTINGS,
    cache_path: Optional[Path] = None,
    as_of: Optional[datetime] = None,
    team_name_normalizer: TeamNameNormalizer = _default_team_name_normalizer,
) -> EloCalculationResult:
    """同じ履歴はキャッシュを返し、追加入力時は新規試合だけを更新する。"""

    prepared_matches = _prepare_matches(
        matches,
        as_of,
        team_name_normalizer,
    )
    prepared_team_categories = _prepare_team_categories(
        team_categories,
        prepared_matches,
        team_name_normalizer,
    )
    cache_path = cache_path or get_elo_cache_path()
    current_match_ids = [match.identity for match in prepared_matches]
    fingerprint = _settings_fingerprint(settings, prepared_team_categories)
    cache = _read_cache(cache_path)

    can_increment = bool(
        cache
        and cache.get("settings_fingerprint") == fingerprint
        and cache.get("processed_match_ids", [])
        == current_match_ids[: len(cache.get("processed_match_ids", []))]
    )

    if can_increment:
        try:
            processed_count = len(cache.get("processed_match_ids", []))
            ratings = {
                str(team_name): float(rating)
                for team_name, rating in cache.get("ratings", {}).items()
            }
            match_counts = {
                str(team_name): int(count)
                for team_name, count in cache.get("match_counts", {}).items()
            }
            last_updated = {
                str(team_name): updated
                for team_name, updated in cache.get("last_updated", {}).items()
            }
        except (AttributeError, TypeError, ValueError):
            can_increment = False

        if can_increment:
            required_teams = set(prepared_team_categories)
            if not required_teams.issubset(ratings):
                can_increment = False

        # 履歴0件から初めて試合を取得した場合は、最初の所属カテゴリーを
        # 再評価するため一度だけ全初期化する。
        if can_increment and processed_count == 0 and prepared_matches:
            can_increment = False

    if can_increment:
        new_matches = prepared_matches[processed_count:]
        _apply_matches(
            new_matches,
            ratings,
            match_counts,
            last_updated,
            prepared_team_categories,
            settings,
        )
    else:
        processed_count = 0
        ratings, match_counts, last_updated = _initial_state(
            prepared_matches,
            prepared_team_categories,
            settings,
        )
        new_matches = prepared_matches
        _apply_matches(
            new_matches,
            ratings,
            match_counts,
            last_updated,
            prepared_team_categories,
            settings,
        )

    payload = {
        "version": ELO_CACHE_VERSION,
        "settings_fingerprint": fingerprint,
        "processed_match_ids": current_match_ids,
        "ratings": ratings,
        "match_counts": match_counts,
        "last_updated": last_updated,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_cache(cache_path, payload)

    return _result_from_state(
        prepared_matches,
        ratings,
        match_counts,
        last_updated,
        prepared_team_categories,
        from_cache=bool(can_increment and not new_matches),
        incremental_match_count=len(new_matches),
    )


def get_team_elo(
    team_name: Any,
    elo_result: EloCalculationResult,
    team_name_normalizer: TeamNameNormalizer = _default_team_name_normalizer,
) -> Optional[float]:
    """呼び出し元の正規化規則でクラブ別Eloを返す。"""

    normalized_name = team_name_normalizer(team_name)
    team_rating = elo_result.ratings.get(normalized_name)
    return team_rating.rating if team_rating else None
