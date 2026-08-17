"""Version7-C買い目最適化タブのStreamlit UI。"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd
import streamlit as st

from analysis import (
    Version7AHistoryGenerationResult,
    ensure_version7a_strategy_history,
    reconcile_saved_strategy_history,
    reconcile_saved_version7b_strategy_history,
)

from bet_config import (
    BET_TARGETS,
    BUDGET_PRESETS_YEN,
    DEFAULT_BUDGET_YEN,
    DOUBLE_COUNT_PRESETS,
    MAX_COMBINATION_DISPLAY,
    MAX_COMBINATION_EXPORT,
    MAX_CUSTOM_BUDGET_YEN,
    TOTO_OUTCOMES,
    TRIPLE_COUNT_PRESETS,
)
from bet_evaluation import (
    backtest_frame,
    compare_bet_strategies,
)
from bet_export import (
    BET_PLAN_DISPLAY_COLUMNS,
    BET_PLAN_DISPLAY_SCHEMA_VERSION,
    CombinationLimitError,
    bet_plan_csv_bytes,
    bet_plan_display_frame,
    combination_csv_bytes,
    combination_frame,
    purchase_entry_text,
)
from bet_optimizer import (
    BET_TYPE_COUNTS,
    BET_TYPE_DOUBLE,
    BET_TYPE_LABELS,
    BET_TYPE_SINGLE,
    BET_TYPE_TRIPLE,
    BetOptimizationError,
    BetPlan,
    apply_manual_selections,
    build_match_predictions,
    calculate_purchase_amount,
    calculate_ticket_count,
    is_budget_exceeded,
    optimize_bet_plan,
    plan_fingerprint,
    target_label,
)
from history_manager import JAPAN_TIMEZONE, get_saved_toto_payouts
from model_config import VERSION7A_MODEL_VERSION, VERSION7B_MODEL_VERSION


TARGET_LABEL_TO_KEY = {
    str(definition["label"]): key
    for key, definition in BET_TARGETS.items()
}
TARGET_KEY_TO_LABEL = {
    key: str(definition["label"])
    for key, definition in BET_TARGETS.items()
}


def _normalize_choice_state(
    key: str,
    options: Sequence[Any],
    default: Any,
) -> Any:
    """古いrerun由来の不正なwidget値を既定値へ戻す。"""

    value = st.session_state.get(key, default)
    valid = False
    if isinstance(value, str):
        valid = value in options
    elif isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        valid = int(value) in options
    if not valid:
        value = default
        st.session_state[key] = default
    return value


def _bounded_int(value: Any, *, default: int, maximum: int) -> int:
    """session_stateの任意入力を有限な範囲内の整数へ正規化する。"""

    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or not number.is_integer():
        return default
    return max(0, min(int(number), int(maximum)))


def _bounded_float(
    value: Any,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """None・NaN・Infinityを既定値へ戻し、有限範囲へ収める。"""

    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(minimum, min(number, maximum))


def _valid_manual_outcomes(value: Any, expected_count: int) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != expected_count:
        return False
    try:
        return (
            len(set(value)) == expected_count
            and all(outcome in TOTO_OUTCOMES for outcome in value)
        )
    except TypeError:
        return False


def render_bet_optimization_tab(
    *,
    prediction_history_manager: Any,
    history_manager: Any,
    active_draw_settings: Any,
    fallback_matches: Sequence[Any] = (),
) -> None:
    """通常予想の最新確率を買い目へ変換する。"""

    st.subheader("Version7-C 買い目最適化")
    st.caption(
        "通常予想で算出したP(1)・P(0)・P(2)を再利用し、指定数の"
        "シングル・ダブル・トリプルを提案します。予測モデルは再計算しません。"
    )
    st.warning(
        "買い目は参考提案です。各試合を独立と仮定したCoverageであり、"
        "的中・払戻・利益を保証しません。自動購入や決済は行いません。"
    )

    latest_results = st.session_state.get("latest_prediction_results")
    if not isinstance(latest_results, pd.DataFrame) or latest_results.empty:
        st.info("先に「予想」タブで通常の13試合予想を実行してください。")
        return

    current_settings = _render_current_optimizer(
        latest_results,
        active_draw_settings,
    )
    if current_settings is not None:
        _render_strategy_backtest(
            prediction_history_manager=prediction_history_manager,
            history_manager=history_manager,
            settings=current_settings,
            fallback_matches=fallback_matches,
        )


def _render_current_optimizer(
    latest_results: pd.DataFrame,
    active_draw_settings: Any,
) -> Optional[dict[str, Any]]:
    round_label = _round_label(latest_results)
    version_label = _prediction_version(latest_results)
    info_columns = st.columns(2)
    info_columns[0].metric("開催回", round_label)
    info_columns[1].metric("使用確率", version_label)

    target_options = tuple(TARGET_LABEL_TO_KEY)
    _normalize_choice_state(
        "version7c_target",
        target_options,
        target_options[0],
    )
    target_label_value = st.selectbox(
        "対象くじ",
        options=target_options,
        key="version7c_target",
    )
    target = TARGET_LABEL_TO_KEY[target_label_value]
    match_count = len(BET_TARGETS[target]["source_match_numbers"])

    setting_columns = st.columns(2)
    with setting_columns[0]:
        double_count = _count_input(
            "ダブル試合数",
            DOUBLE_COUNT_PRESETS,
            target=target,
            default=min(3, match_count),
            maximum=match_count,
            key_prefix="version7c_double",
        )
    with setting_columns[1]:
        triple_count = _count_input(
            "トリプル試合数",
            TRIPLE_COUNT_PRESETS,
            target=target,
            default=0,
            maximum=match_count,
            key_prefix="version7c_triple",
        )

    ticket_count = calculate_ticket_count(double_count, triple_count)
    purchase_amount = calculate_purchase_amount(double_count, triple_count)
    amount_columns = st.columns(2)
    amount_columns[0].metric("総口数", f"{ticket_count:,}口")
    amount_columns[1].metric("購入金額", f"{purchase_amount:,}円")

    budget_yen = _budget_input(target)
    active_threshold = _bounded_float(
        getattr(active_draw_settings, "candidate_threshold", 0.25),
        default=0.25,
        minimum=0.0,
        maximum=1.0,
    )
    threshold_default = _bounded_float(
        st.session_state.get("latest_prediction_draw_threshold"),
        default=active_threshold,
        minimum=0.0,
        maximum=1.0,
    )
    draw_threshold = _draw_threshold_input(target, threshold_default)
    draw_margin = float(getattr(active_draw_settings, "candidate_margin", 0.05))

    valid_counts = double_count + triple_count <= match_count
    if not valid_counts:
        st.error(
            "ダブル試合数とトリプル試合数の合計が対象試合数を超えています。"
        )
    budget_exceeded = is_budget_exceeded(
        double_count,
        triple_count,
        budget_yen,
    )
    if budget_exceeded:
        st.warning(
            f"購入金額{purchase_amount:,}円が予算上限"
            f"{int(budget_yen):,}円を超えています。"
        )

    request_signature = _request_signature(
        latest_results,
        target=target,
        double_count=double_count,
        triple_count=triple_count,
        draw_threshold=draw_threshold,
        draw_margin=draw_margin,
    )
    current_prediction_run_id = str(
        st.session_state.get("latest_prediction_run_id", "") or ""
    )
    if st.button(
        "買い目最適化を実行",
        type="primary",
        width="stretch",
        key="version7c_optimize",
        disabled=not valid_counts,
    ):
        try:
            predictions = build_match_predictions(latest_results, target)
            plan = optimize_bet_plan(
                predictions,
                target=target,
                double_count=double_count,
                triple_count=triple_count,
                draw_candidate_threshold=draw_threshold,
                draw_candidate_margin=draw_margin,
                source_prediction_run_id=current_prediction_run_id,
            )
            st.session_state["version7c_ai_plan"] = plan
            st.session_state["version7c_source_prediction_run_id"] = (
                current_prediction_run_id
            )
            st.session_state["version7c_plan_request"] = request_signature
            st.session_state["version7c_plan_generated_at"] = datetime.now(
                JAPAN_TIMEZONE
            )
            _initialize_manual_state(plan)
        except BetOptimizationError as error:
            st.session_state.pop("version7c_ai_plan", None)
            st.session_state.pop("version7c_source_prediction_run_id", None)
            st.session_state.pop("version7c_plan_request", None)
            st.error(str(error))
        except (TypeError, ValueError):
            st.session_state.pop("version7c_ai_plan", None)
            st.session_state.pop("version7c_source_prediction_run_id", None)
            st.session_state.pop("version7c_plan_request", None)
            st.error("予測確率または対象試合を確認できず、買い目を作成できませんでした。")

    plan = st.session_state.get("version7c_ai_plan")
    stored_request = st.session_state.get("version7c_plan_request")
    stored_source_run_id = str(
        st.session_state.get("version7c_source_prediction_run_id", "") or ""
    )
    if isinstance(plan, BetPlan):
        source_matches = (
            bool(current_prediction_run_id)
            and stored_source_run_id == current_prediction_run_id
            and plan.source_prediction_run_id == current_prediction_run_id
        )
        if stored_request == request_signature and source_matches:
            _render_plan(
                plan,
                budget_yen=budget_yen,
                source_prediction_run_id=current_prediction_run_id,
            )
        else:
            st.info("設定または予測結果が変わりました。買い目最適化を再実行してください。")

    return {
        "target": target,
        "double_count": double_count,
        "triple_count": triple_count,
        "draw_threshold": draw_threshold,
        "draw_margin": draw_margin,
        "prediction_version": version_label,
        "request_signature": request_signature,
    }


def _render_plan(
    plan: BetPlan,
    *,
    budget_yen: Optional[int],
    source_prediction_run_id: str,
) -> None:
    st.success(
        f"{target_label(plan.target)}：ダブル{plan.double_count}試合、"
        f"トリプル{plan.triple_count}試合を提案しました。"
    )
    _plan_metrics(plan, budget_yen=budget_yen, prefix="AI提案")
    st.caption(
        "開催回Coverageは各試合を独立と仮定して試合別Coverageを掛けた参考値です。"
        "絶対的な実当選確率ではありません。"
    )
    st.dataframe(
        _display_plan_frame(plan),
        width="stretch",
        hide_index=True,
    )

    st.subheader("手動調整")
    st.caption(
        "AI提案後も区分と1・0・2を変更できます。区分の選択数と買い目数が"
        "一致しない場合は最終案へ反映しません。"
    )
    fingerprint = plan_fingerprint(plan)
    selections: dict[int, tuple[str, ...]] = {}
    manual_valid = True

    for recommendation in plan.recommendations:
        analysis = recommendation.analysis
        prediction = analysis.prediction
        type_key = f"version7c_type_{fingerprint}_{prediction.match_number}"
        outcomes_key = f"version7c_outcomes_{fingerprint}_{prediction.match_number}"
        if type_key not in st.session_state:
            st.session_state[type_key] = recommendation.bet_type
        if outcomes_key not in st.session_state:
            st.session_state[outcomes_key] = list(recommendation.outcomes)
        bet_type = _normalize_choice_state(
            type_key,
            (BET_TYPE_SINGLE, BET_TYPE_DOUBLE, BET_TYPE_TRIPLE),
            recommendation.bet_type,
        )
        expected_count = BET_TYPE_COUNTS[bet_type]
        raw_outcomes = st.session_state.get(outcomes_key)
        if not _valid_manual_outcomes(raw_outcomes, expected_count):
            st.session_state[outcomes_key] = list(
                TOTO_OUTCOMES
                if expected_count == 3
                else analysis.ranked_outcomes[:expected_count]
            )

        with st.expander(
            f"第{prediction.match_number}試合 "
            f"{prediction.home_team} vs {prediction.away_team}"
        ):
            st.selectbox(
                "区分",
                options=(BET_TYPE_SINGLE, BET_TYPE_DOUBLE, BET_TYPE_TRIPLE),
                format_func=lambda value: BET_TYPE_LABELS[value],
                key=type_key,
                on_change=_sync_manual_outcomes,
                args=(type_key, outcomes_key, analysis.ranked_outcomes),
            )
            selected = tuple(
                st.multiselect(
                    "買い目",
                    options=TOTO_OUTCOMES,
                    key=outcomes_key,
                )
            )
            bet_type = _normalize_choice_state(
                type_key,
                (BET_TYPE_SINGLE, BET_TYPE_DOUBLE, BET_TYPE_TRIPLE),
                recommendation.bet_type,
            )
            expected_count = BET_TYPE_COUNTS[bet_type]
            if len(selected) != expected_count:
                manual_valid = False
                st.error(
                    f"{BET_TYPE_LABELS[bet_type]}は"
                    f"{expected_count}結果を選択してください。"
                )
            else:
                selections[prediction.match_number] = selected
                coverage = sum(
                    prediction.probabilities[outcome] for outcome in selected
                )
                st.caption(f"変更後Coverage：{coverage:.1%}")

    if not manual_valid:
        return
    try:
        manual_plan = apply_manual_selections(plan, selections)
    except BetOptimizationError as error:
        st.error(str(error))
        return
    st.session_state["version7c_manual_plan"] = manual_plan
    st.session_state["version7c_manual_plan_source_prediction_run_id"] = (
        source_prediction_run_id
    )
    st.subheader("最終買い目")
    _plan_metrics(manual_plan, budget_yen=budget_yen, prefix="手動調整後")
    st.code(purchase_entry_text(manual_plan), language=None)
    st.dataframe(
        _display_plan_frame(manual_plan),
        width="stretch",
        hide_index=True,
    )
    _render_exports(manual_plan, fingerprint)


def _plan_metrics(
    plan: BetPlan,
    *,
    budget_yen: Optional[int],
    prefix: str,
) -> None:
    columns = st.columns(3)
    columns[0].metric(f"{prefix} 総口数", f"{plan.ticket_count:,}口")
    columns[1].metric(
        f"{prefix} 購入金額",
        f"{plan.purchase_amount_yen:,}円",
    )
    columns[2].metric(
        f"{prefix} 推定Coverage",
        _coverage_label(plan.estimated_full_coverage),
    )
    if budget_yen is not None and plan.purchase_amount_yen > budget_yen:
        st.warning(
            f"手動調整後の購入金額が予算上限{budget_yen:,}円を超えています。"
        )


def _render_exports(plan: BetPlan, fingerprint: str) -> None:
    safe_target = plan.target.replace("_", "-")
    try:
        st.download_button(
            "試合別買い目をCSVで保存",
            data=bet_plan_csv_bytes(plan),
            file_name=f"version7c_{safe_target}_bet_plan.csv",
            mime="text/csv",
            width="stretch",
            key=f"version7c_plan_csv_{fingerprint}",
        )
    except (OSError, TypeError, ValueError):
        st.error("試合別買い目CSVを作成できませんでした。")

    if plan.ticket_count <= MAX_COMBINATION_DISPLAY:
        try:
            st.subheader("全購入組み合わせ")
            st.dataframe(
                combination_frame(
                    plan,
                    max_combinations=MAX_COMBINATION_DISPLAY,
                ),
                width="stretch",
                hide_index=True,
            )
        except (CombinationLimitError, TypeError, ValueError):
            st.warning("全組み合わせを画面表示できませんでした。")
    else:
        st.warning(
            f"{plan.ticket_count:,}口のため、画面での全組み合わせ表示は"
            f"{MAX_COMBINATION_DISPLAY:,}口上限を超えています。"
        )

    if plan.ticket_count <= MAX_COMBINATION_EXPORT:
        try:
            st.download_button(
                "全購入組み合わせをCSVで保存",
                data=combination_csv_bytes(plan),
                file_name=f"version7c_{safe_target}_combinations.csv",
                mime="text/csv",
                width="stretch",
                key=f"version7c_combinations_csv_{fingerprint}",
            )
        except (CombinationLimitError, OSError, TypeError, ValueError):
            st.error("全購入組み合わせCSVを作成できませんでした。")
    else:
        st.warning(
            f"全組み合わせCSVは安全上{MAX_COMBINATION_EXPORT:,}口までです。"
            "試合別買い目CSVは保存できます。"
        )


def _render_strategy_backtest(
    *,
    prediction_history_manager: Any,
    history_manager: Any,
    settings: Mapping[str, Any],
    fallback_matches: Sequence[Any] = (),
) -> None:
    try:
        history = prediction_history_manager.load()
    except (OSError, TypeError, ValueError):
        history = pd.DataFrame()
    if not isinstance(history, pd.DataFrame):
        history = pd.DataFrame()

    saved_versions = tuple(
        str(value)
        for value in history.get("prediction_version", pd.Series(dtype=str))
        if str(value).strip()
    )
    available_versions = tuple(
        dict.fromkeys(
            (*saved_versions, VERSION7A_MODEL_VERSION, VERSION7B_MODEL_VERSION)
        )
    )
    preferred = settings["prediction_version"]
    default_version = (
        preferred if preferred in available_versions else available_versions[-1]
    )
    _normalize_choice_state(
        "version7c_backtest_version",
        available_versions,
        default_version,
    )
    default_index = available_versions.index(default_version)

    with st.expander("過去データで買い目戦略を比較"):
        version = st.selectbox(
            "バックテスト対象Version",
            options=available_versions,
            index=default_index,
            key="version7c_backtest_version",
        )
        st.caption(
            "A＝全試合シングル、B＝指定数ダブル、"
            "C＝指定数ダブル＋トリプルを同じ保存済み確率で比較します。"
        )
        signature = (
            settings["request_signature"],
            version,
        )
        if st.button(
            "買い目戦略をバックテスト",
            key="version7c_backtest",
            width="stretch",
        ):
            try:
                generation_result = None
                version7b_result = None
                verified_round_ids: tuple[int, ...] = ()
                if version == VERSION7A_MODEL_VERSION:
                    progress_area = st.empty()

                    def update_progress(
                        current: int,
                        total: int,
                        message: str,
                    ) -> None:
                        progress_area.info(
                            "Version7-Aの過去予測履歴を生成しています "
                            f"（{current}/{max(1, total)}）\n\n{message}"
                        )

                    update_progress(0, 1, "確定済み開催回を確認しています。")
                    generation_result = ensure_version7a_strategy_history(
                        prediction_history_manager=prediction_history_manager,
                        history_manager=history_manager,
                        fallback_matches=fallback_matches,
                        progress_callback=update_progress,
                    )
                    progress_area.empty()
                    history = prediction_history_manager.load()
                    st.session_state[
                        "version7c_version7a_history_generation"
                    ] = generation_result
                    st.session_state[
                        "version7c_version7a_history_generation_request"
                    ] = signature
                    verified_round_ids = generation_result.target_round_ids
                    for message in generation_result.messages:
                        st.warning(message)
                elif version == VERSION7B_MODEL_VERSION:
                    version7b_result = reconcile_saved_version7b_strategy_history(
                        prediction_history_manager=prediction_history_manager,
                        history_manager=history_manager,
                    )
                    history = prediction_history_manager.load()
                    st.session_state[
                        "version7c_version7b_history_reconciliation"
                    ] = version7b_result
                    st.session_state[
                        "version7c_version7b_history_reconciliation_request"
                    ] = signature
                    verified_round_ids = version7b_result.evaluable_round_ids
                    for message in version7b_result.messages:
                        st.warning(message)
                else:
                    verification_result = reconcile_saved_strategy_history(
                        prediction_history_manager=prediction_history_manager,
                        history_manager=history_manager,
                        prediction_version=version,
                    )
                    history = prediction_history_manager.load()
                    verified_round_ids = verification_result.evaluable_round_ids
                    for message in verification_result.messages:
                        st.warning(message)
                payouts = get_saved_toto_payouts(
                    history_manager,
                    history,
                    target=settings["target"],
                )
                results = compare_bet_strategies(
                    history,
                    target=settings["target"],
                    prediction_version=version,
                    double_count=settings["double_count"],
                    triple_count=settings["triple_count"],
                    draw_candidate_threshold=settings["draw_threshold"],
                    draw_candidate_margin=settings["draw_margin"],
                    payouts_by_round=payouts,
                    verified_round_ids=verified_round_ids,
                )
                st.session_state["version7c_backtest_results"] = results
                st.session_state["version7c_backtest_request"] = signature
                st.session_state["version7c_backtest_round_ids"] = (
                    verified_round_ids
                )
                st.session_state["version7c_backtest_round_ids_request"] = (
                    signature
                )
            except BetOptimizationError as error:
                st.error(str(error))
            except (OSError, TypeError, ValueError):
                st.error("保存済み予想履歴を使った戦略比較を実行できませんでした。")

        generation_result = st.session_state.get(
            "version7c_version7a_history_generation"
        )
        if (
            version == VERSION7A_MODEL_VERSION
            and isinstance(generation_result, Version7AHistoryGenerationResult)
            and st.session_state.get(
                "version7c_version7a_history_generation_request"
            )
            == signature
        ):
            st.caption("Version7-A履歴の準備結果")
            generation_columns = st.columns(3)
            generation_columns[0].metric(
                "対象開催回数",
                generation_result.target_round_count,
            )
            generation_columns[1].metric(
                "生成した開催回数",
                generation_result.generated_round_count,
            )
            generation_columns[2].metric(
                "生成した試合数",
                generation_result.generated_match_count,
            )
            st.caption(
                "公式確認済みactual_result："
                f"{generation_result.actual_result_count}件"
            )
            if generation_result.target_round_ids:
                st.caption(
                    "評価対象開催回："
                    + "、".join(
                        f"第{round_id}回"
                        for round_id in generation_result.target_round_ids
                    )
                )
            if generation_result.failed_round_ids:
                st.caption(
                    "評価対象外開催回："
                    + "、".join(
                        f"第{round_id}回"
                        for round_id in generation_result.failed_round_ids
                    )
                )

        version7b_result = st.session_state.get(
            "version7c_version7b_history_reconciliation"
        )
        if (
            version == VERSION7B_MODEL_VERSION
            and st.session_state.get(
                "version7c_version7b_history_reconciliation_request"
            )
            == signature
            and version7b_result is not None
            and not getattr(version7b_result, "evaluable_round_ids", ())
        ):
            st.warning(
                "Version7-Bは当時保存された予測履歴が必要です。"
                "現在設定で過去予測は再生成しません。"
            )

        results = st.session_state.get("version7c_backtest_results")
        evaluated_round_ids = st.session_state.get(
            "version7c_backtest_round_ids",
            (),
        )
        if (
            isinstance(results, tuple)
            and st.session_state.get("version7c_backtest_request") == signature
        ):
            if not results or all(result.evaluated_rounds == 0 for result in results):
                if version == VERSION7B_MODEL_VERSION:
                    # Version7-Bは上で保存履歴が必要であることを明示する。
                    pass
                elif (
                    version == VERSION7A_MODEL_VERSION
                    and isinstance(
                        generation_result,
                        Version7AHistoryGenerationResult,
                    )
                    and generation_result.target_round_count == 0
                ):
                    st.warning(
                        "Version7-Aを再生成できる確定済みJリーグtoto開催回を"
                        "確認できませんでした。通信状態と公式データを確認してください。"
                    )
                else:
                    st.warning("実結果まで揃った対象開催回を確認できませんでした。")
            else:
                if (
                    st.session_state.get(
                        "version7c_backtest_round_ids_request"
                    )
                    == signature
                    and evaluated_round_ids
                ):
                    st.caption(
                        "戦略A/B/Cの評価対象："
                        + "、".join(
                            f"第{int(round_id)}回"
                            for round_id in evaluated_round_ids
                        )
                    )
                st.dataframe(
                    backtest_frame(results),
                    width="stretch",
                    hide_index=True,
                )
                if all(result.payout_data_available for result in results):
                    st.caption(
                        "保存済み公式1～3等金と各買い目の13・12・11的中券数を使って"
                        "払戻金・収支・ROIを算出しました。"
                    )
                else:
                    st.info(
                        "払戻データなし：払戻金・収支・ROIは推測せず算出していません。"
                    )


def _count_input(
    label: str,
    presets: tuple[int, ...],
    *,
    target: str,
    default: int,
    maximum: int,
    key_prefix: str,
) -> int:
    options: tuple[int | str, ...] = (*presets, "任意指定")
    choice_key = f"{key_prefix}_choice_{target}"
    _normalize_choice_state(choice_key, options, default)
    choice = st.selectbox(
        label,
        options=options,
        index=options.index(default),
        key=choice_key,
    )
    if choice != "任意指定":
        return int(choice)
    custom_key = f"{key_prefix}_custom_{target}"
    if custom_key in st.session_state:
        st.session_state[custom_key] = _bounded_int(
            st.session_state[custom_key],
            default=0,
            maximum=maximum,
        )
    input_options: dict[str, Any] = {
        "label": f"{label}（任意）",
        "min_value": 0,
        "max_value": maximum,
        "step": 1,
        "key": custom_key,
    }
    if custom_key not in st.session_state:
        input_options["value"] = 0
    return int(st.number_input(**input_options))


def _budget_input(target: str) -> Optional[int]:
    options: tuple[int | str, ...] = (
        "上限なし",
        *BUDGET_PRESETS_YEN,
        "任意指定",
    )
    choice_key = f"version7c_budget_choice_{target}"
    _normalize_choice_state(choice_key, options, DEFAULT_BUDGET_YEN)
    choice = st.selectbox(
        "予算上限",
        options=options,
        index=options.index(DEFAULT_BUDGET_YEN),
        format_func=lambda value: (
            f"{value:,}円" if isinstance(value, int) else str(value)
        ),
        key=choice_key,
    )
    if choice == "上限なし":
        return None
    if choice != "任意指定":
        return int(choice)
    custom_key = f"version7c_budget_custom_{target}"
    if custom_key in st.session_state:
        st.session_state[custom_key] = _bounded_int(
            st.session_state[custom_key],
            default=DEFAULT_BUDGET_YEN,
            maximum=MAX_CUSTOM_BUDGET_YEN,
        )
    options_for_input: dict[str, Any] = {
        "label": "予算上限（任意・円）",
        "min_value": 0,
        "max_value": MAX_CUSTOM_BUDGET_YEN,
        "step": 100,
        "key": custom_key,
    }
    if custom_key not in st.session_state:
        options_for_input["value"] = DEFAULT_BUDGET_YEN
    return int(st.number_input(**options_for_input))


def _draw_threshold_input(target: str, default: float) -> float:
    default_percent = max(0.0, min(100.0, float(default) * 100.0))
    choices: tuple[int | str, ...] = (20, 25, 30, 35, 40, "任意指定")
    integer_default = int(round(default_percent))
    initial = integer_default if integer_default in choices else "任意指定"
    choice_key = f"version7c_draw_threshold_choice_{target}"
    _normalize_choice_state(choice_key, choices, initial)
    choice = st.selectbox(
        "引分候補閾値",
        options=choices,
        index=choices.index(initial),
        format_func=lambda value: (
            f"{value}%" if isinstance(value, int) else str(value)
        ),
        key=choice_key,
        help=(
            "P(0)が閾値未満なら通常の確率上位2結果を使います。閾値以上でも"
            "0を強制せず、0が確率3位の場合だけDraw Inclusion Scoreで"
            "通常2位との入替価値を比較します。"
        ),
    )
    if choice != "任意指定":
        return float(choice) / 100.0
    custom_key = f"version7c_draw_threshold_custom_{target}"
    if custom_key in st.session_state:
        st.session_state[custom_key] = _bounded_float(
            st.session_state[custom_key],
            default=default_percent,
            minimum=0.0,
            maximum=100.0,
        )
    options_for_input: dict[str, Any] = {
        "label": "引分候補閾値（任意・%）",
        "min_value": 0.0,
        "max_value": 100.0,
        "step": 1.0,
        "key": custom_key,
    }
    if custom_key not in st.session_state:
        options_for_input["value"] = float(default_percent)
    return float(st.number_input(**options_for_input)) / 100.0


def _initialize_manual_state(plan: BetPlan) -> None:
    fingerprint = plan_fingerprint(plan)
    current_prefixes = (
        f"version7c_type_{fingerprint}_",
        f"version7c_outcomes_{fingerprint}_",
    )
    for key in list(st.session_state):
        text_key = str(key)
        if text_key.startswith(
            ("version7c_type_", "version7c_outcomes_")
        ) and not text_key.startswith(current_prefixes):
            del st.session_state[key]
    for recommendation in plan.recommendations:
        match_number = recommendation.analysis.prediction.match_number
        st.session_state[
            f"version7c_type_{fingerprint}_{match_number}"
        ] = recommendation.bet_type
        st.session_state[
            f"version7c_outcomes_{fingerprint}_{match_number}"
        ] = list(recommendation.outcomes)


def _sync_manual_outcomes(
    type_key: str,
    outcomes_key: str,
    ranked_outcomes: tuple[str, str, str],
) -> None:
    bet_type = st.session_state.get(type_key, BET_TYPE_SINGLE)
    if bet_type not in BET_TYPE_COUNTS:
        bet_type = BET_TYPE_SINGLE
        st.session_state[type_key] = bet_type
    count = BET_TYPE_COUNTS[bet_type]
    st.session_state[outcomes_key] = list(
        TOTO_OUTCOMES if count == 3 else ranked_outcomes[:count]
    )


def _display_plan_frame(plan: BetPlan) -> pd.DataFrame:
    return bet_plan_display_frame(plan)


def _round_label(frame: pd.DataFrame) -> str:
    if "toto_round" not in frame.columns:
        return "未取得"
    values = pd.to_numeric(frame["toto_round"], errors="coerce").dropna()
    positive = sorted({int(value) for value in values if int(value) > 0})
    return f"第{positive[-1]}回" if positive else "手入力"


def _first_scalar(values: Any) -> Any:
    """先頭値を、pandasのindex labelに依存せず位置で取得する。"""

    if values is None:
        return ""
    if isinstance(values, pd.DataFrame):
        return "" if values.empty else values.iloc[0, 0]
    if isinstance(values, pd.Series):
        if values.empty:
            return ""
        return values.iloc[0]
    if isinstance(values, np.ndarray):
        return "" if values.size == 0 else values.reshape(-1).item(0)
    if isinstance(values, (list, tuple)):
        return next(iter(values), "")
    return values


def _prediction_version(frame: pd.DataFrame) -> str:
    if "prediction_version" not in frame.columns:
        return "Version7-A"
    values = frame["prediction_version"]
    if isinstance(values, pd.DataFrame):
        values = values.iloc[:, 0]
    if isinstance(values, pd.Series):
        values = values.loc[
            values.notna() & values.astype(str).str.strip().ne("")
        ]
    first_value = _first_scalar(values)
    if first_value is None:
        return "Version7-A"
    try:
        if bool(pd.isna(first_value)):
            return "Version7-A"
    except (TypeError, ValueError):
        return "Version7-A"
    return str(first_value).strip() or "Version7-A"


def _request_signature(
    frame: pd.DataFrame,
    *,
    target: str,
    double_count: int,
    triple_count: int,
    draw_threshold: float,
    draw_margin: float,
) -> str:
    relevant_columns = [
        column
        for column in (
            "toto_round",
            "toto_match_number",
            "試合",
            "対戦カード",
            "1",
            "0",
            "2",
            "draw_candidate",
            "draw_candidate_reasons",
            "prediction_version",
        )
        if column in frame.columns
    ]
    payload = {
        "target": target,
        "double": int(double_count),
        "triple": int(triple_count),
        "threshold": float(draw_threshold),
        "margin": float(draw_margin),
        "predictions": frame[relevant_columns].fillna("").astype(str).to_dict("records"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _coverage_label(probability: float) -> str:
    percentage = max(0.0, min(1.0, float(probability))) * 100.0
    if percentage >= 0.01:
        return f"{percentage:.2f}%"
    if percentage > 0.0:
        return f"{percentage:.6f}%"
    return "0%"
