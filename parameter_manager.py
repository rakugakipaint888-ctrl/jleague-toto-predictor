"""Version7-Bの候補設定、実行時設定、採用・復元を一元管理する。"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from version7b_config import (
    VERSION7B_DEFAULT_MODEL_PARAMETERS,
    VERSION7B_MODEL_VERSION,
)
from data_loader import JAPAN_TIMEZONE
from draw_predictor import DEFAULT_DRAW_SETTINGS, DrawSettings
from model_config import (
    DEFAULT_ELO_SETTINGS,
    DEFAULT_FORM_SETTINGS,
    DEFAULT_MODEL_SETTINGS,
    DEFAULT_STANDINGS_SETTINGS,
    DEFAULT_VENUE_SETTINGS,
    EloSettings,
    FormSettings,
    ModelSettings,
    StandingsSettings,
    VenueSettings,
)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ACTIVE_SETTINGS_PATH = (
    PROJECT_ROOT / "data" / "config" / "version7b_model_settings.json"
)
DEFAULT_SETTINGS_BACKUP_DIRECTORY = (
    PROJECT_ROOT / "data" / "config" / "version7b_backups"
)

RECENT_WEIGHT_PROFILES = {
    "flat": (1.0, 1.0, 1.0, 1.0, 1.0),
    "linear": (5.0, 4.0, 3.0, 2.0, 1.0),
    "steep": (6.0, 4.0, 2.5, 1.5, 0.5),
}


def _finite(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name}を数値へ変換できません。") from error
    if not math.isfinite(number):
        raise ValueError(f"{field_name}は有限値にしてください。")
    return number


@dataclass(frozen=True)
class ModelParameters:
    """Version7-Bが探索する、既存統計モデルへ実際に渡す設定値。"""

    home_correction: float = 1.08
    elo_correction_rate: float = 0.05
    home_advantage: float = 65.0
    k_factor: float = 20.0
    recent_match_weights: tuple[float, float, float, float, float] = (
        5.0,
        4.0,
        3.0,
        2.0,
        1.0,
    )
    recent_weighted_share: float = 0.60
    season_average_share: float = 0.40
    venue_mix_rate: float = 0.70
    rank_correction_rate: float = 0.0
    points_correction_rate: float = 0.05
    goal_difference_correction_rate: float = 0.03
    expected_goals_minimum: float = 0.15
    expected_goals_maximum: float = 4.00

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ModelParameters":
        defaults = dict(VERSION7B_DEFAULT_MODEL_PARAMETERS)
        defaults.update(dict(values))
        try:
            raw_weights = defaults["recent_match_weights"]
            weights = tuple(float(value) for value in raw_weights)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("直近5試合の重みを確認できません。") from error
        if len(weights) != 5:
            raise ValueError("直近試合の重みは5件必要です。")
        parameters = cls(
            home_correction=_finite(defaults["home_correction"], "home_correction"),
            elo_correction_rate=_finite(
                defaults["elo_correction_rate"], "elo_correction_rate"
            ),
            home_advantage=_finite(defaults["home_advantage"], "home_advantage"),
            k_factor=_finite(defaults["k_factor"], "k_factor"),
            recent_match_weights=weights,
            recent_weighted_share=_finite(
                defaults["recent_weighted_share"], "recent_weighted_share"
            ),
            season_average_share=_finite(
                defaults["season_average_share"], "season_average_share"
            ),
            venue_mix_rate=_finite(defaults["venue_mix_rate"], "venue_mix_rate"),
            rank_correction_rate=_finite(
                defaults["rank_correction_rate"], "rank_correction_rate"
            ),
            points_correction_rate=_finite(
                defaults["points_correction_rate"], "points_correction_rate"
            ),
            goal_difference_correction_rate=_finite(
                defaults["goal_difference_correction_rate"],
                "goal_difference_correction_rate",
            ),
            expected_goals_minimum=_finite(
                defaults["expected_goals_minimum"], "expected_goals_minimum"
            ),
            expected_goals_maximum=_finite(
                defaults["expected_goals_maximum"], "expected_goals_maximum"
            ),
        )
        parameters.validate()
        return parameters

    def validate(self) -> None:
        scalar_values = {
            key: value
            for key, value in asdict(self).items()
            if key != "recent_match_weights"
        }
        if any(not math.isfinite(float(value)) for value in scalar_values.values()):
            raise ValueError("Version7-B設定に有限でない値があります。")
        if len(self.recent_match_weights) != 5 or any(
            not math.isfinite(float(value)) or float(value) <= 0
            for value in self.recent_match_weights
        ):
            raise ValueError("直近5試合の重みは正の有限値にしてください。")
        if any(
            self.recent_match_weights[index] < self.recent_match_weights[index + 1]
            for index in range(4)
        ):
            raise ValueError("直近試合の重みは最新順に同値または降順にしてください。")
        if self.home_correction <= 0 or self.k_factor <= 0:
            raise ValueError("ホーム補正とElo K係数は正の値にしてください。")
        if self.elo_correction_rate < 0 or self.home_advantage < 0:
            raise ValueError("Elo補正率とホームアドバンテージは0以上です。")
        if not 0 <= self.recent_weighted_share <= 1:
            raise ValueError("直近成績混合率は0～1にしてください。")
        if not 0 <= self.season_average_share <= 1:
            raise ValueError("シーズン平均混合率は0～1にしてください。")
        if self.recent_weighted_share + self.season_average_share <= 0:
            raise ValueError("直近成績とシーズン平均の混合率が両方0です。")
        if not 0 <= self.venue_mix_rate <= 1:
            raise ValueError("会場別成績混合率は0～1にしてください。")
        for field_name in (
            "rank_correction_rate",
            "points_correction_rate",
            "goal_difference_correction_rate",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name}は0以上にしてください。")
        if self.expected_goals_minimum < 0:
            raise ValueError("期待得点下限は0以上にしてください。")
        if self.expected_goals_maximum <= self.expected_goals_minimum:
            raise ValueError("期待得点上限は下限より大きくしてください。")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["recent_match_weights"] = list(self.recent_match_weights)
        return result


DEFAULT_MODEL_PARAMETERS = ModelParameters.from_mapping(
    VERSION7B_DEFAULT_MODEL_PARAMETERS
)


@dataclass(frozen=True)
class Version7BParameters:
    """全体モデルとVersion7-A引分モデルを一つの候補として保持する。"""

    model: ModelParameters = DEFAULT_MODEL_PARAMETERS
    draw: DrawSettings = DEFAULT_DRAW_SETTINGS

    def validate(self) -> None:
        self.model.validate()
        self.draw.validate()

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.as_dict(),
            "draw": self.draw.as_dict(),
        }

    def as_flat_dict(self) -> dict[str, Any]:
        values = self.model.as_dict()
        values.update(
            {f"draw_{key}": value for key, value in self.draw.as_dict().items()}
        )
        return values

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        *,
        base: Optional["Version7BParameters"] = None,
    ) -> "Version7BParameters":
        current = base or cls()
        model_values = dict(current.model.as_dict())
        draw_values = dict(current.draw.as_dict())
        nested_model = values.get("model")
        nested_draw = values.get("draw")
        if isinstance(nested_model, Mapping):
            model_values.update(nested_model)
        if isinstance(nested_draw, Mapping):
            draw_values.update(nested_draw)
        for key, value in values.items():
            if key in model_values:
                model_values[key] = value
            elif key.startswith("draw_") and key[5:] in draw_values:
                draw_values[key[5:]] = value
        if all(f"recent_match_weight_{index}" in values for index in range(1, 6)):
            model_values["recent_match_weights"] = tuple(
                sorted(
                    (
                        _finite(values[f"recent_match_weight_{index}"], "recent weight")
                        for index in range(1, 6)
                    ),
                    reverse=True,
                )
            )
        profile = values.get("recent_weight_profile")
        if profile is not None:
            if str(profile) not in RECENT_WEIGHT_PROFILES:
                raise ValueError("直近試合重みプロファイルが不正です。")
            model_values["recent_match_weights"] = RECENT_WEIGHT_PROFILES[str(profile)]
        if "recent_weighted_share" in model_values:
            recent_share = _finite(
                model_values["recent_weighted_share"], "recent_weighted_share"
            )
            model_values["recent_weighted_share"] = recent_share
            model_values["season_average_share"] = max(0.0, 1.0 - recent_share)
        result = cls(
            model=ModelParameters.from_mapping(model_values),
            draw=DrawSettings.from_mapping(draw_values),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class RuntimeModelSettings:
    elo: EloSettings
    form: FormSettings
    venue: VenueSettings
    standings: StandingsSettings
    model: ModelSettings


def to_runtime_settings(parameters: ModelParameters) -> RuntimeModelSettings:
    """候補値を既存モジュールの型へ変換し、全係数を実計算へ接続する。"""

    parameters.validate()
    venue_ratio = parameters.venue_mix_rate / 0.70
    return RuntimeModelSettings(
        elo=replace(
            DEFAULT_ELO_SETTINGS,
            k_factor=parameters.k_factor,
            home_advantage=parameters.home_advantage,
            expected_goals_change_per_100_elo=parameters.elo_correction_rate,
        ),
        form=replace(
            DEFAULT_FORM_SETTINGS,
            recent_match_weights=parameters.recent_match_weights,
            recent_weighted_share=parameters.recent_weighted_share,
            season_average_share=parameters.season_average_share,
        ),
        venue=replace(
            DEFAULT_VENUE_SETTINGS,
            five_plus_share=min(1.0, 0.70 * venue_ratio),
            four_match_share=min(1.0, 0.60 * venue_ratio),
            one_to_three_share=min(1.0, 0.40 * venue_ratio),
        ),
        standings=replace(
            DEFAULT_STANDINGS_SETTINGS,
            rank_change_per_position=parameters.rank_correction_rate,
            points_change_per_unit=parameters.points_correction_rate,
            goal_difference_change_per_unit=(
                parameters.goal_difference_correction_rate
            ),
        ),
        model=replace(
            DEFAULT_MODEL_SETTINGS,
            home_correction=parameters.home_correction,
            expected_goals_minimum=parameters.expected_goals_minimum,
            expected_goals_maximum=parameters.expected_goals_maximum,
        ),
    )


@dataclass(frozen=True)
class ActiveVersion7BSettings:
    parameters: Version7BParameters
    adopted: bool
    draw_override: bool
    adopted_at: Optional[str] = None
    warning: str = ""

    @property
    def version_label(self) -> str:
        return VERSION7B_MODEL_VERSION if self.adopted else "Version7-A"


def prediction_settings_snapshot(
    active_settings: ActiveVersion7BSettings,
    *,
    draw_settings: DrawSettings,
    model_options: Mapping[str, Any],
    prediction_time: Optional[datetime] = None,
    strategy_backtest_cutoff_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """旧import入口を保ち、独立した履歴スナップショット実装へ委譲する。"""

    from prediction_settings_snapshot import (
        prediction_settings_snapshot as build_snapshot,
    )

    return build_snapshot(
        active_settings,
        draw_settings=draw_settings,
        model_options=model_options,
        prediction_time=prediction_time,
        strategy_backtest_cutoff_at=strategy_backtest_cutoff_at,
    )


def _active_version7a_draw_settings() -> DrawSettings:
    # 遅延importにより、Version7-A最適化モジュールとの循環importを避ける。
    try:
        from draw_optimizer import load_active_draw_settings

        return load_active_draw_settings()
    except Exception:
        return DEFAULT_DRAW_SETTINGS


def default_active_settings() -> ActiveVersion7BSettings:
    return ActiveVersion7BSettings(
        parameters=Version7BParameters(
            model=DEFAULT_MODEL_PARAMETERS,
            draw=_active_version7a_draw_settings(),
        ),
        adopted=False,
        draw_override=False,
    )


def load_active_version7b_settings(
    path: Path = DEFAULT_ACTIVE_SETTINGS_PATH,
) -> ActiveVersion7BSettings:
    """採用JSONを検証し、欠損・破損時はVersion7-A互換設定へ戻す。"""

    fallback = default_active_settings()
    if not path.exists():
        return fallback
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        model = ModelParameters.from_mapping(payload["model_parameters"])
        draw_override = bool(payload.get("draw_override", False))
        draw = (
            DrawSettings.from_mapping(payload["draw_parameters"])
            if draw_override
            else _active_version7a_draw_settings()
        )
        return ActiveVersion7BSettings(
            parameters=Version7BParameters(model=model, draw=draw),
            adopted=bool(payload.get("adopted", True)),
            draw_override=draw_override,
            adopted_at=str(payload.get("adopted_at") or "") or None,
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        return replace(
            fallback,
            warning=f"Version7-B設定を読み込めないためVersion7-Aを使用します: {error}",
        )


@dataclass(frozen=True)
class AdoptionResult:
    adopted: bool
    message: str
    settings_path: Path
    backup_path: Optional[Path] = None


def _settings_payload(
    parameters: Version7BParameters,
    *,
    draw_override: bool,
    adopted_at: datetime,
    adopted: bool = True,
) -> dict[str, Any]:
    return {
        "version": VERSION7B_MODEL_VERSION,
        "adopted": bool(adopted),
        "adopted_at": adopted_at.astimezone(JAPAN_TIMEZONE).isoformat(),
        "draw_override": bool(draw_override),
        "model_parameters": parameters.model.as_dict(),
        "draw_parameters": parameters.draw.as_dict(),
    }


def adopt_version7b_settings(
    parameters: Version7BParameters,
    *,
    confirmed: bool,
    include_draw_parameters: bool,
    path: Path = DEFAULT_ACTIVE_SETTINGS_PATH,
    backup_directory: Path = DEFAULT_SETTINGS_BACKUP_DIRECTORY,
) -> AdoptionResult:
    """YESの場合だけ、検証済み候補をバックアップ後に原子的に採用する。"""

    if not confirmed:
        return AdoptionResult(
            False,
            "NOが選択されたため現在設定を維持しました。",
            path,
        )
    parameters.validate()
    now = datetime.now(JAPAN_TIMEZONE)
    backup_path = backup_directory / (
        "version7b_model_settings_" + now.strftime("%Y%m%dT%H%M%S%f") + ".json"
    )
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_directory.mkdir(parents=True, exist_ok=True)
        if path.exists():
            previous_text = path.read_text(encoding="utf-8")
            json.loads(previous_text)
        else:
            fallback = default_active_settings()
            previous_text = json.dumps(
                _settings_payload(
                    fallback.parameters,
                    draw_override=False,
                    adopted_at=now,
                    adopted=False,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        backup_path.write_text(previous_text, encoding="utf-8")
        temporary_path.write_text(
            json.dumps(
                _settings_payload(
                    parameters,
                    draw_override=include_draw_parameters,
                    adopted_at=now,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        # 書き込んだ候補を採用前にもう一度検証する。
        written = json.loads(temporary_path.read_text(encoding="utf-8"))
        ModelParameters.from_mapping(written["model_parameters"])
        DrawSettings.from_mapping(written["draw_parameters"])
        temporary_path.replace(path)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        temporary_path.unlink(missing_ok=True)
        return AdoptionResult(
            False,
            f"Version7-B設定を採用できませんでした: {error}",
            path,
            backup_path if backup_path.exists() else None,
        )
    return AdoptionResult(
        True,
        "Version7-B最適設定を採用し、直前設定をバックアップしました。",
        path,
        backup_path,
    )


def restore_latest_version7b_settings(
    *,
    path: Path = DEFAULT_ACTIVE_SETTINGS_PATH,
    backup_directory: Path = DEFAULT_SETTINGS_BACKUP_DIRECTORY,
) -> AdoptionResult:
    """最新バックアップを検証してから直前設定へ戻す。"""

    backups = sorted(backup_directory.glob("version7b_model_settings_*.json"))
    if not backups:
        return AdoptionResult(False, "復元できるVersion7-B設定がありません。", path)
    latest = backups[-1]
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
        ModelParameters.from_mapping(payload["model_parameters"])
        DrawSettings.from_mapping(payload["draw_parameters"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        temporary_path.unlink(missing_ok=True)
        return AdoptionResult(
            False,
            f"Version7-B設定を復元できませんでした: {error}",
            path,
            latest,
        )
    return AdoptionResult(True, "直前のVersion7-B設定へ戻しました。", path, latest)
