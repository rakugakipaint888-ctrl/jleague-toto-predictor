"""予測時点の設定を履歴へ保存するためのJSONスナップショットを作る。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from data_loader import JAPAN_TIMEZONE
from draw_predictor import DrawSettings

def prediction_settings_snapshot(
    active_settings: Any,
    *,
    draw_settings: DrawSettings,
    model_options: Mapping[str, Any],
    prediction_time: Optional[datetime] = None,
    strategy_backtest_cutoff_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """予測時点のVersion・係数・画面スイッチを履歴用に固定する。"""

    draw_settings.validate()
    snapshot = {
        "schema_version": 1,
        "prediction_version": active_settings.version_label,
        "adopted": active_settings.adopted,
        "adopted_at": active_settings.adopted_at,
        "draw_override": active_settings.draw_override,
        "model_parameters": active_settings.parameters.model.as_dict(),
        "draw_parameters": draw_settings.as_dict(),
        "model_options": {
            str(key): bool(value) for key, value in model_options.items()
        },
    }
    if prediction_time is not None:
        snapshot["prediction_generated_at"] = prediction_time.astimezone(
            JAPAN_TIMEZONE
        ).isoformat()
    if strategy_backtest_cutoff_at is not None:
        cutoff = strategy_backtest_cutoff_at.astimezone(JAPAN_TIMEZONE)
        snapshot["strategy_backtest_cutoff_at"] = cutoff.isoformat()
        snapshot["strategy_backtest_eligible"] = bool(
            prediction_time is not None
            and prediction_time.astimezone(JAPAN_TIMEZONE) < cutoff
        )
    return snapshot
