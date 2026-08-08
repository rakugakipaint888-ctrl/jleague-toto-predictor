"""Version7-Bモデル最適化タブのStreamlit表示層。"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

import pandas as pd

from version7b_config import (
    VERSION7B_BOOTSTRAP_CHOICES,
    VERSION7B_MODEL_LIMITS,
    VERSION7B_RANDOM_SEED,
    VERSION7B_TRIAL_COUNT_CHOICES,
    VERSION7B_TRIAL_COUNT_DEFAULT,
)
from bootstrap_evaluation import bootstrap_top_models
from data_loader import JAPAN_TIMEZONE, OfficialMatch
from history_manager import TotoHistoryManager
from model_compare import (
    bootstrap_frame,
    parameter_comparison_frame,
    parameter_importance_frame,
    ranking_frame,
    stability_frame,
    training_validation_frame,
    trial_metrics_frame,
    version7a_comparison_frame,
)
from model_evaluation import EvaluationWeights
from model_optimizer import (
    ALL_LEAGUES,
    GRID_SEARCH,
    OPTUNA_SEARCH,
    RANDOM_SEARCH,
    TWO_STAGE_SEARCH,
    ModelOptimizationError,
    OptimizationResult,
    OptunaUnavailableError,
    SearchConfiguration,
    TrialProgress,
    build_search_plan,
    collect_available_completed_rounds,
    collect_historical_matches,
    load_optimization_history,
    mark_optimization_adopted,
    prepare_model_dataset,
    run_model_optimization,
    save_model_ranking,
    save_optimization_history,
)
from parameter_manager import (
    adopt_version7b_settings,
    load_active_version7b_settings,
    restore_latest_version7b_settings,
)
from walk_forward_validator import (
    FIXED_SPLIT,
    ROUND_WALK_FORWARD,
    SEASON_WALK_FORWARD,
)

SEARCH_LABELS = {
    "Optuna（ベイズ最適化）": OPTUNA_SEARCH,
    "ランダムサーチ": RANDOM_SEARCH,
    "グリッドサーチ": GRID_SEARCH,
    "2段階探索": TWO_STAGE_SEARCH,
}
VALIDATION_LABELS = {
    "固定Training／Validation分割": FIXED_SPLIT,
    "シーズン単位ウォークフォワード": SEASON_WALK_FORWARD,
    "開催回単位ウォークフォワード": ROUND_WALK_FORWARD,
}
PERIOD_CHOICES = ("直近3シーズン", "直近5シーズン", "直近10シーズン", "任意期間")


def _custom_or_choice(
    st,
    label: str,
    choices: Sequence[int],
    default: int,
    key: str,
    *,
    maximum: int,
) -> int:
    options = (*choices, "任意指定")
    selected = st.selectbox(
        label,
        options=options,
        index=options.index(default),
        key=f"{key}_choice",
    )
    if selected == "任意指定":
        return int(
            st.number_input(
                f"{label}（任意）",
                min_value=1,
                max_value=maximum,
                value=int(default),
                step=1,
                key=f"{key}_custom",
            )
        )
    return int(selected)


def _period_years(st) -> tuple[str, tuple[int, ...]]:
    current_year = datetime.now(JAPAN_TIMEZONE).year
    choice = st.selectbox(
        "バックテスト期間",
        options=PERIOD_CHOICES,
        index=1,
        key="version7b_backtest_period",
    )
    if choice == "任意期間":
        columns = st.columns(2)
        with columns[0]:
            start_year = int(
                st.number_input(
                    "開始年",
                    min_value=2000,
                    max_value=current_year,
                    value=max(2000, current_year - 4),
                    step=1,
                    key="version7b_start_year",
                )
            )
        with columns[1]:
            end_year = int(
                st.number_input(
                    "終了年",
                    min_value=2000,
                    max_value=current_year,
                    value=current_year,
                    step=1,
                    key="version7b_end_year",
                )
            )
        if start_year > end_year:
            return choice, ()
        return choice, tuple(range(start_year, end_year + 1))
    count = {"直近3シーズン": 3, "直近5シーズン": 5, "直近10シーズン": 10}[choice]
    return choice, tuple(range(current_year - count + 1, current_year + 1))


def _evaluation_weights(st) -> EvaluationWeights:
    st.subheader("総合評価Scoreの重み")
    labels = (
        ("brier_score", "Brier Score", 30.0),
        ("log_loss", "Log Loss", 20.0),
        ("calibration", "Calibration", 15.0),
        ("accuracy", "全体的中率", 15.0),
        ("draw_performance", "引分性能", 10.0),
        ("validation_stability", "Validation安定性", 10.0),
    )
    columns = st.columns(3)
    values = {}
    for index, (key, label, default) in enumerate(labels):
        with columns[index % 3]:
            values[key] = st.number_input(
                f"{label}（%）",
                min_value=0.0,
                max_value=100.0,
                value=default,
                step=1.0,
                key=f"version7b_weight_{key}",
            )
    weights = EvaluationWeights.from_mapping(values)
    if not weights.totals_one_hundred_percent:
        st.warning(
            f"評価重みの合計は{weights.total:.1f}%です。100%へ調整してください。"
            "計算時は比率を正規化します。"
        )
    else:
        st.caption("評価重み合計：100%")
    return weights


def _format_seconds(value: float) -> str:
    seconds = max(0, int(value))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")


def _render_graphs(st, result: OptimizationResult) -> None:
    graph = trial_metrics_frame(result)
    for column, title in (
        ("総合Score", "Trialごとの総合Score"),
        ("Validation Score", "Validation Score推移"),
        ("Brier Score", "Brier Score推移"),
        ("Log Loss", "Log Loss推移"),
        ("Calibration", "Calibration推移"),
        ("全体的中率", "全体的中率推移"),
        ("引分F1", "引分F1推移"),
    ):
        st.subheader(title)
        st.line_chart(graph[[column]])
    st.subheader("Training ScoreとValidation Score比較")
    st.line_chart(graph[["Training Score", "Validation Score"]])
    importance = parameter_importance_frame(result)
    st.subheader("パラメータ重要度")
    if importance.empty:
        st.info("重要度を算出するには2モデル以上必要です。")
    else:
        st.bar_chart(importance.set_index("パラメータ")[["重要度"]])


def _render_result(st, result: OptimizationResult, bootstrap_results) -> None:
    st.header("Version7-B探索結果")
    overview = pd.DataFrame(
        [
            {
                "探索方式": result.configuration.method,
                "探索モデル数": len(result.all_trials),
                "実際の使用期間": result.dataset.actual_period,
                "Training期間": result.dataset.training_period,
                "Validation期間": result.dataset.validation_period,
                "Training試合数": result.dataset.training_match_count,
                "Validation試合数": result.dataset.validation_match_count,
                "対象リーグ": result.dataset.target_league,
                "検証方式": result.dataset.split.method,
                "Best Score": result.best_score,
                "Best Validation Score": result.best_validation_score,
            }
        ]
    )
    st.dataframe(overview, width="stretch", hide_index=True)
    if result.dataset.unavailable_leagues:
        st.info("データ不足リーグ：" + "、".join(result.dataset.unavailable_leagues))

    st.subheader("Training／Validation")
    st.dataframe(training_validation_frame(result), width="stretch", hide_index=True)
    if result.overfitting.is_overfitting:
        st.warning("過学習の可能性：" + " ".join(result.overfitting.reasons))
    else:
        st.success("過学習の兆候なし")
    if result.draw_degradation.degraded:
        st.warning("引分性能悪化：" + " ".join(result.draw_degradation.reasons))
    else:
        st.success("Version7-Aの引分性能を許容幅内で維持")

    st.subheader("Version7-Aとの同一Validation比較")
    comparison = version7a_comparison_frame(result)
    st.dataframe(comparison, width="stretch", hide_index=True)

    st.subheader("モデルランキング（探索内Validation順・上位20件）")
    ranking = ranking_frame(result)
    st.dataframe(ranking, width="stretch", hide_index=True)
    st.download_button(
        "上位20モデルをCSV保存",
        data=_csv_bytes(ranking),
        file_name="version7b_model_ranking.csv",
        mime="text/csv",
        width="stretch",
    )

    st.subheader("現在設定と最適候補")
    parameters = parameter_comparison_frame(result)
    st.dataframe(parameters, width="stretch", hide_index=True)

    stability = stability_frame(result)
    st.subheader("モデル安定性")
    st.caption(
        "シーズン別ScoreはTraining＋最終Validation、リーグ別Scoreは"
        "最終Validationだけで算出します。"
    )
    if stability.empty:
        st.info("シーズン別・リーグ別の十分なデータを確認できません。")
    else:
        st.dataframe(stability, width="stretch", hide_index=True)
        for group in ("シーズン", "リーグ"):
            selected = stability[stability["区分"] == group]
            if not selected.empty:
                st.bar_chart(selected.set_index("対象")[["Score"]])
    for warning in result.stability.warnings:
        st.warning(warning)

    if bootstrap_results:
        st.subheader("ブートストラップ再評価・95%信頼区間")
        boot_frame = bootstrap_frame(bootstrap_results)
        st.dataframe(boot_frame, width="stretch", hide_index=True)
    else:
        st.info("ブートストラップ再評価は実施していません。")

    _render_graphs(st, result)

    st.header("Version7-B最適設定の採用")
    st.write("Version7-B最適設定を採用しますか？")
    st.caption(
        "YESの前に、現在設定・候補設定・Training／Validation結果・"
        "Version7-Aとの差・過学習・引分性能悪化を上の表で確認してください。"
    )
    st.warning(
        "注意事項：Validationの改善は将来の的中を保証しません。"
        "過学習または引分性能悪化がある候補は、採用前に理由を確認してください。"
    )
    yes_column, no_column, restore_column = st.columns(3)
    with yes_column:
        if st.button("YES", type="primary", key="version7b_adopt_yes"):
            adoption = adopt_version7b_settings(
                result.best_parameters,
                confirmed=True,
                include_draw_parameters=result.configuration.include_draw_parameters,
            )
            if adoption.adopted:
                st.success(adoption.message)
                try:
                    if not mark_optimization_adopted(result.run_id):
                        st.warning("採用済みですが、対応する最適化履歴がありません。")
                except ModelOptimizationError as error:
                    st.warning(f"採用済みですが、履歴を更新できません: {error}")
            else:
                st.error(adoption.message)
    with no_column:
        if st.button("NO", key="version7b_adopt_no"):
            adoption = adopt_version7b_settings(
                result.best_parameters,
                confirmed=False,
                include_draw_parameters=result.configuration.include_draw_parameters,
            )
            st.info(adoption.message)
    with restore_column:
        if st.button("直前設定へ戻す", key="version7b_restore"):
            restoration = restore_latest_version7b_settings()
            if restoration.adopted:
                st.success(restoration.message)
            else:
                st.info(restoration.message)


def render_model_optimization_tab(
    *,
    history_manager: TotoHistoryManager,
    fallback_matches: Sequence[OfficialMatch] = (),
) -> None:
    """Version7-Aを維持したまま全体パラメータ探索UIを追加する。"""

    import streamlit as st

    st.header("モデル最適化・Version7-B")
    st.caption(
        "既存統計モデルとVersion7-A引分モデルの係数を探索します。"
        "最終Validationはランキング確定後だけ評価し、自動採用しません。"
    )
    controls = st.columns(3)
    with controls[0]:
        method_label = st.selectbox(
            "探索方式",
            options=tuple(SEARCH_LABELS),
            index=0,
            key="version7b_search_method",
        )
        trial_count = _custom_or_choice(
            st,
            "Trial数",
            VERSION7B_TRIAL_COUNT_CHOICES,
            VERSION7B_TRIAL_COUNT_DEFAULT,
            "version7b_trials",
            maximum=50000,
        )
    with controls[1]:
        limit_label = st.selectbox(
            "探索モデル上限",
            options=(*VERSION7B_MODEL_LIMITS, "任意指定"),
            index=1,
            key="version7b_model_limit_label",
        )
        model_limit = (
            int(
                st.number_input(
                    "探索モデル上限（任意）",
                    min_value=1,
                    max_value=50000,
                    value=10000,
                    step=100,
                    key="version7b_custom_model_limit",
                )
            )
            if limit_label == "任意指定"
            else VERSION7B_MODEL_LIMITS[limit_label]
        )
        include_draw = st.checkbox(
            "引分パラメータを探索対象に含める",
            value=False,
            key="version7b_include_draw",
        )
    with controls[2]:
        target_league = st.selectbox(
            "対象リーグ",
            options=(ALL_LEAGUES, "J1", "J2", "J3"),
            index=0,
            key="version7b_target_league",
        )
        validation_label = st.selectbox(
            "検証方式",
            options=tuple(VALIDATION_LABELS),
            index=1,
            key="version7b_validation_method",
        )

    period_choice, requested_years = _period_years(st)
    rounds_per_year = st.selectbox(
        "各シーズンの最大使用開催回数",
        options=(5, 10, 20, 50),
        index=1,
        key="version7b_rounds_per_year",
    )
    if not requested_years:
        st.error("開始年は終了年以前にしてください。")
    bootstrap_count = _custom_or_choice(
        st,
        "ブートストラップ回数",
        VERSION7B_BOOTSTRAP_CHOICES,
        1000,
        "version7b_bootstrap",
        maximum=100000,
    )
    random_seed = int(
        st.number_input(
            "ランダムシード",
            min_value=0,
            max_value=2_147_483_647,
            value=VERSION7B_RANDOM_SEED,
            step=1,
            key="version7b_random_seed",
        )
    )
    weights = _evaluation_weights(st)

    truncate_grid = False
    if SEARCH_LABELS[method_label] == GRID_SEARCH:
        truncate_grid = st.checkbox(
            "上限内の先頭組み合わせだけ実行する",
            value=False,
            key="version7b_truncate_grid",
        )
    configuration = SearchConfiguration(
        method=SEARCH_LABELS[method_label],
        trial_count=trial_count,
        model_limit=model_limit,
        include_draw_parameters=include_draw,
        random_seed=random_seed,
        evaluation_weights=weights,
        truncate_grid_to_limit=truncate_grid,
    )
    plan = build_search_plan(configuration)
    if plan.grid_combination_count is not None:
        st.write(
            f"グリッド予定組み合わせ数：{plan.grid_combination_count:,} ／ "
            f"設定上限：{plan.model_limit:,}"
        )
    if plan.reason:
        (st.warning if plan.executable else st.error)(plan.reason)
    if trial_count >= 1000 or bootstrap_count >= 10000:
        st.warning(
            "処理に長時間かかる可能性があります。Trial数を増やしても"
            "必ず性能が向上するわけではありません。"
        )
    st.caption(
        "各Trialは完了直後に専用CSVへ保存します。Streamlit実行中の停止ボタンと"
        "完全な自動再開は環境仕様上保証せず、完了済みTrialを失わないことを優先します。"
    )
    st.caption(
        "Trial数を増やしても必ず性能が向上するわけではありません。"
        "処理時間と過学習リスクも確認してください。"
    )

    progress = st.progress(0.0)
    metric_columns = st.columns(6)
    slots = [column.empty() for column in metric_columns]
    slots[0].metric("現在Trial", 0)
    slots[1].metric("完了Trial", 0)
    slots[2].metric("残りTrial", plan.executable_models)
    slots[3].metric("経過時間", "00:00")
    slots[4].metric("推定残り", "—")
    slots[5].metric("Best Score", "—")
    secondary_columns = st.columns(4)
    secondary_slots = [column.empty() for column in secondary_columns]
    secondary_slots[0].metric("Best Validation", "—")
    secondary_slots[1].metric("Best Brier", "—")
    secondary_slots[2].metric("Best Log Loss", "—")
    secondary_slots[3].metric("Best引分F1", "—")
    status = st.empty()
    parameter_status = st.empty()

    run_enabled = bool(
        requested_years and plan.executable and weights.totals_one_hundred_percent
    )
    if st.button(
        "Version7-B最適化を開始",
        type="primary",
        width="stretch",
        disabled=not run_enabled,
        key="version7b_start",
    ):
        try:
            status.info("利用可能なtoto開催回を確認しています。")
            collection = collect_available_completed_rounds(
                history_manager,
                requested_years,
                rounds_per_year=int(rounds_per_year),
            )
            if collection.missing_years:
                st.warning(
                    "保存・公式データがない年は使用していません："
                    + "、".join(str(value) for value in collection.missing_years)
                )
            history = collect_historical_matches(collection.rounds, fallback_matches)
            dataset = prepare_model_dataset(
                collection.rounds,
                history,
                validation_method=VALIDATION_LABELS[validation_label],
                target_league=target_league,
                requested_period=period_choice,
            )
            st.info(
                f"実際の使用期間：{dataset.actual_period} ／ "
                f"Training {dataset.training_match_count}試合 ／ "
                f"Validation {dataset.validation_match_count}試合"
            )

            def on_progress(item: TrialProgress) -> None:
                progress.progress(min(1.0, item.progress_rate))
                slots[0].metric("現在Trial", item.current_trial)
                slots[1].metric("完了Trial", item.completed_trials)
                slots[2].metric("残りTrial", item.remaining_trials)
                slots[3].metric("経過時間", _format_seconds(item.elapsed_seconds))
                slots[4].metric(
                    "推定残り",
                    _format_seconds(item.estimated_remaining_seconds),
                )
                slots[5].metric("Best Score", f"{item.best_score:.4f}")
                secondary_slots[0].metric(
                    "Best Validation",
                    f"{item.best_validation_score:.4f}",
                )
                secondary_slots[1].metric(
                    "Best Brier",
                    "—" if item.best_brier is None else f"{item.best_brier:.5f}",
                )
                secondary_slots[2].metric(
                    "Best Log Loss",
                    "—" if item.best_log_loss is None else f"{item.best_log_loss:.5f}",
                )
                secondary_slots[3].metric("Best引分F1", f"{item.best_draw_f1:.4f}")
                status.info(
                    f"進捗率 {item.progress_rate * 100:.1f}% ／ "
                    f"完了 {item.completed_trials:,} ／ 残り {item.remaining_trials:,}"
                )
                parameter_status.json(item.current_parameters.as_dict())

            result = run_model_optimization(
                dataset,
                configuration,
                current_settings=load_active_version7b_settings(),
                progress_callback=on_progress,
            )
            save_errors = []
            try:
                save_optimization_history(result)
            except ModelOptimizationError as error:
                save_errors.append(str(error))
            try:
                save_model_ranking(result)
            except ModelOptimizationError as error:
                save_errors.append(str(error))
            bootstrap_results = bootstrap_top_models(
                result.ranking,
                bootstrap_count,
                random_seed=random_seed,
                limit=10,
            )
            st.session_state["version7b_optimization_result"] = result
            st.session_state["version7b_bootstrap_results"] = bootstrap_results
            progress.progress(1.0)
            status.success(
                f"{len(result.all_trials)}モデル完了。Best Score "
                f"{result.best_score:.4f}／最終Validation "
                f"{result.best_validation_score:.4f}"
            )
            if save_errors:
                st.warning(
                    "結果は表示できますが、専用履歴CSVを保存できません: "
                    + " ／ ".join(save_errors)
                )
        except KeyboardInterrupt:
            status.warning("探索を停止しました。完了済みTrialは専用CSVへ保存済みです。")
        except (ModelOptimizationError, OptunaUnavailableError, ValueError) as error:
            status.error(str(error))
        except Exception as error:
            status.error(f"Version7-B最適化を完了できませんでした: {error}")

    result = st.session_state.get("version7b_optimization_result")
    bootstrap_results = st.session_state.get("version7b_bootstrap_results", {})
    if isinstance(result, OptimizationResult):
        _render_result(st, result, bootstrap_results)

    st.subheader("Version7-B最適化履歴")
    try:
        history_rows = load_optimization_history()
        if history_rows:
            st.dataframe(pd.DataFrame(history_rows), width="stretch", hide_index=True)
        else:
            st.info("保存済みのVersion7-B最適化履歴はありません。")
    except ModelOptimizationError as error:
        st.warning(f"最適化履歴を読み込めません: {error}")
