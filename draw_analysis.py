"""Version7-Aの引分分析・小規模最適化UI。"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

import pandas as pd

from backtest import BacktestDataLeakError
from data_loader import JAPAN_TIMEZONE, OfficialMatch
from draw_evaluation import DrawEvaluation
from draw_optimizer import (
    DrawOptimizationError,
    DrawOptimizationResult,
    OptunaUnavailableError,
    TrialProgress,
    adopt_draw_settings,
    collect_completed_rounds,
    collect_historical_matches,
    prepare_draw_dataset,
    restore_latest_draw_settings,
    run_draw_optimization,
    save_optimization_result,
    version6_comparison,
)
from history_manager import TotoHistoryManager
from model_config import VERSION7A_TRIAL_COUNT_CHOICES, VERSION7A_TRIAL_COUNT_DEFAULT


def _available_years() -> tuple[int, ...]:
    current_year = datetime.now(JAPAN_TIMEZONE).year
    return tuple(range(2024, current_year + 1))


def _metric_value(value, digits: int = 4):
    return None if value is None else round(float(value), digits)


def evaluation_frame(
    version6: DrawEvaluation,
    version7a: DrawEvaluation,
    *,
    period: str,
) -> pd.DataFrame:
    """同一データに対するVersion6/7-Aの指標を縦持ちで返す。"""

    rows = []
    for version, evaluation in (
        ("Version6", version6),
        ("Version7-A", version7a),
    ):
        rows.append(
            {
                "期間": period,
                "Version": version,
                "試合数": evaluation.overall.match_count,
                "全体的中率": evaluation.overall.accuracy,
                "Brier Score": evaluation.overall.brier_score,
                "Log Loss": evaluation.overall.log_loss,
                "Calibration": evaluation.overall.calibration_error,
                "実際の引分数": evaluation.draw.actual_draw_count,
                "引分予測数": evaluation.draw.predicted_draw_count,
                "引分的中数": evaluation.draw.draw_hit_count,
                "引分Precision": evaluation.draw.precision,
                "引分Recall": evaluation.draw.recall,
                "引分F1": evaluation.draw.f1_score,
                "引分Brier": evaluation.draw.brier_score,
                "引分Calibration": evaluation.draw.calibration_error,
                "引分予測時の平均確率": (
                    evaluation.draw.mean_probability_when_predicted
                ),
                "実際の引分率": evaluation.draw.actual_draw_rate,
                "引分予測率": evaluation.draw.predicted_draw_rate,
            }
        )
    return pd.DataFrame(rows)


def validation_comparison_frame(result: DrawOptimizationResult) -> pd.DataFrame:
    labels = {
        "accuracy": "全体的中率",
        "brier_score": "Brier Score",
        "log_loss": "Log Loss",
        "calibration": "Calibration",
        "draw_precision": "引分Precision",
        "draw_recall": "引分Recall",
        "draw_f1": "引分F1",
        "draw_brier": "引分Brier",
        "draw_calibration": "引分Calibration",
        "draw_mean_probability_when_predicted": "引分予測時の平均確率",
        "actual_draw_rate": "実際の引分率",
        "predicted_draw_rate": "引分予測率",
        "class_accuracy_1": "1の成績",
        "class_accuracy_0": "0の成績",
        "class_accuracy_2": "2の成績",
        "actual_draw_count": "実際の引分数",
        "predicted_draw_count": "引分予測数",
        "draw_hit_count": "引分的中数",
    }
    comparison = version6_comparison(result)
    lower_is_better = {
        "brier_score",
        "log_loss",
        "calibration",
        "draw_brier",
        "draw_calibration",
    }
    higher_is_better = {
        "accuracy",
        "draw_precision",
        "draw_recall",
        "draw_f1",
        "class_accuracy_1",
        "class_accuracy_0",
        "class_accuracy_2",
    }

    def judgment(key: str, difference) -> str:
        if difference is None or key not in lower_is_better | higher_is_better:
            return "参考"
        if abs(float(difference)) < 1e-12:
            return "同等"
        improved = (
            difference < 0 if key in lower_is_better else difference > 0
        )
        return "改善" if improved else "悪化"

    return pd.DataFrame(
        [
            {
                "項目": labels[key],
                "Version6": values["version6"],
                "Version7-A": values["version7a"],
                "差": values["difference"],
                "評価": judgment(key, values["difference"]),
            }
            for key, values in comparison.items()
        ]
    )


def draw_bins_frame(evaluation: DrawEvaluation) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "引分確率帯": item.label,
                "試合数": item.count,
                "平均引分予測確率": item.mean_probability,
                "実際の引分数": item.actual_draw_count,
                "実際の引分率": item.actual_draw_rate,
                "Calibration差": item.calibration_gap,
            }
            for item in evaluation.draw.calibration_bins
        ]
    )


def class_performance_frame(result: DrawOptimizationResult) -> pd.DataFrame:
    rows = []
    for version, evaluation in (
        ("Version6", result.validation_version6),
        ("Version7-A", result.validation_best),
    ):
        for outcome in ("1", "0", "2"):
            rows.append(
                {
                    "Version": version,
                    "結果": outcome,
                    "正答率": evaluation.overall.class_accuracy[outcome],
                    "実結果数": evaluation.overall.class_support[outcome],
                }
            )
    return pd.DataFrame(rows)


def trial_score_frame(result: DrawOptimizationResult) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Trial": [item.trial_number for item in result.trials],
            "Score": [item.score for item in result.trials],
        }
    ).set_index("Trial")


def parameters_frame(result: DrawOptimizationResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"パラメータ": key, "最適値": value}
            for key, value in result.best_settings.as_dict().items()
        ]
    )


def _result_csv_bytes(result: DrawOptimizationResult) -> bytes:
    frame = validation_comparison_frame(result)
    return frame.to_csv(index=False).encode("utf-8-sig")


def _render_result(st, result: DrawOptimizationResult) -> None:
    st.subheader("Training / Validation")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "区分": "Training",
                    "期間": result.dataset.training_period,
                    "開催回数": len(result.dataset.training_rounds),
                    "試合数": len(result.dataset.training_rows),
                    "Best Score": result.training_score.score,
                },
                {
                    "区分": "Validation",
                    "期間": result.dataset.validation_period,
                    "開催回数": len(result.dataset.validation_rounds),
                    "試合数": len(result.dataset.validation_rows),
                    "Best Score": result.validation_score.score,
                },
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    training_frame = evaluation_frame(
        result.training_version6,
        result.training_best,
        period="Training",
    )
    validation_frame = evaluation_frame(
        result.validation_version6,
        result.validation_best,
        period="Validation",
    )
    st.dataframe(
        pd.concat([training_frame, validation_frame], ignore_index=True),
        width="stretch",
        hide_index=True,
    )
    if result.overfitting.is_overfitting:
        st.warning("過学習の可能性：" + " ".join(result.overfitting.reasons))
    else:
        st.success("過学習の兆候なし")

    st.subheader("Version6との比較（同一Validation）")
    st.dataframe(
        validation_comparison_frame(result),
        width="stretch",
        hide_index=True,
    )

    draw = result.validation_best.draw
    metric_columns = st.columns(7)
    metric_columns[0].metric("実際の引分率", f"{draw.actual_draw_rate:.1%}")
    metric_columns[1].metric("引分予測率", f"{draw.predicted_draw_rate:.1%}")
    metric_columns[2].metric("Precision", f"{draw.precision:.3f}")
    metric_columns[3].metric("Recall", f"{draw.recall:.3f}")
    metric_columns[4].metric("F1", f"{draw.f1_score:.3f}")
    metric_columns[5].metric(
        "引分Brier",
        "-" if draw.brier_score is None else f"{draw.brier_score:.4f}",
    )
    metric_columns[6].metric(
        "引分Calibration",
        "-" if draw.calibration_error is None else f"{draw.calibration_error:.4f}",
    )

    st.subheader("引分確率帯別評価")
    bins = draw_bins_frame(result.validation_best)
    st.dataframe(bins, width="stretch", hide_index=True)

    st.subheader("1／0／2別の予測成績")
    class_frame = class_performance_frame(result)
    st.bar_chart(
        class_frame.pivot(index="結果", columns="Version", values="正答率")
    )

    st.subheader("引分Calibration")
    calibration_graph = bins.set_index("引分確率帯")[[
        "平均引分予測確率",
        "実際の引分率",
    ]]
    st.line_chart(calibration_graph)

    st.subheader("引分確率帯別の実際の引分率")
    st.bar_chart(bins.set_index("引分確率帯")[["実際の引分率"]])

    st.subheader("TrialごとのScore推移")
    st.line_chart(trial_score_frame(result))

    st.subheader("最適引分パラメータ")
    st.dataframe(parameters_frame(result), width="stretch", hide_index=True)
    st.download_button(
        "Version6比較をCSV保存",
        data=_result_csv_bytes(result),
        file_name="version7a_validation_comparison.csv",
        mime="text/csv",
        width="stretch",
    )

    st.subheader("Version7-A最適設定の採用")
    st.write("Version7-A最適設定を採用しますか？")
    yes_column, no_column, restore_column = st.columns(3)
    with yes_column:
        if st.button("YES", type="primary", key="version7a_adopt_yes"):
            adoption = adopt_draw_settings(result.best_settings, confirmed=True)
            if adoption.adopted:
                st.success(adoption.message)
            else:
                st.error(adoption.message)
    with no_column:
        if st.button("NO", key="version7a_adopt_no"):
            adoption = adopt_draw_settings(result.best_settings, confirmed=False)
            st.info(adoption.message)
    with restore_column:
        if st.button("直前設定へ戻す", key="version7a_restore"):
            restoration = restore_latest_draw_settings()
            if restoration.adopted:
                st.success(restoration.message)
            else:
                st.info(restoration.message)


def render_draw_analysis_tab(
    *,
    history_manager: TotoHistoryManager,
    fallback_matches: Sequence[OfficialMatch] = (),
) -> None:
    """引分分析、時系列分離、小規模Optuna、採用確認を描画する。"""

    import streamlit as st

    st.header("引分分析・Version7-A")
    st.caption(
        "Poisson引分確率を基準に、期待得点差、Elo差、両チームの得点・失点平均、"
        "チーム・直近引分率、0-0・1-1・ロースコア傾向を連続量で補正します。"
        "Validationは最良Trial確定後だけ評価します。"
    )
    controls = st.columns(2)
    with controls[0]:
        trial_choice = st.selectbox(
            "Trial数",
            options=(*VERSION7A_TRIAL_COUNT_CHOICES, "任意指定"),
            index=VERSION7A_TRIAL_COUNT_CHOICES.index(VERSION7A_TRIAL_COUNT_DEFAULT),
            key="version7a_trial_choice",
        )
        if trial_choice == "任意指定":
            trial_count = st.number_input(
                "任意Trial数",
                min_value=1,
                max_value=10000,
                value=VERSION7A_TRIAL_COUNT_DEFAULT,
                step=1,
                key="version7a_custom_trial_count",
            )
        else:
            trial_count = int(trial_choice)
    with controls[1]:
        rounds_per_year = st.selectbox(
            "各年の使用開催回数",
            options=(1, 3, 5, 10),
            index=2,
            key="version7a_rounds_per_year",
        )
    if int(trial_count) > 100:
        st.warning(
            "100 Trial超も実行できますが、Version7-Aの基本運用は最大100 Trialです。"
            "本格探索はVersion7-Bで行います。"
        )

    available_years = _available_years()
    period_columns = st.columns(2)
    with period_columns[0]:
        training_years = st.multiselect(
            "Training年",
            options=available_years,
            default=[year for year in (2024, 2025) if year in available_years],
            key="version7a_training_years",
        )
    with period_columns[1]:
        default_validation = [available_years[-1]] if available_years else []
        validation_years = st.multiselect(
            "Validation年",
            options=available_years,
            default=default_validation,
            key="version7a_validation_years",
        )

    overlap = set(training_years) & set(validation_years)
    periods_valid = bool(training_years and validation_years and not overlap)
    if overlap:
        st.error("TrainingとValidationに同じ年は指定できません。")
    elif not training_years or not validation_years:
        st.info("Training年とValidation年をそれぞれ指定してください。")

    progress = st.progress(0.0)
    progress_columns = st.columns(4)
    progress_slots = [column.empty() for column in progress_columns]
    progress_slots[0].metric("Trial数", int(trial_count))
    progress_slots[1].metric("現在Trial", 0)
    progress_slots[2].metric("Best Trial", "-")
    progress_slots[3].metric("Best Score", "-")
    status = st.empty()

    if st.button(
        "Version7-A最適化を開始",
        type="primary",
        width="stretch",
        disabled=not periods_valid,
        key="version7a_start_optimization",
    ):
        try:
            requested_years = sorted(set(training_years) | set(validation_years))

            def on_round_progress(current: int, total: int, message: str) -> None:
                status.info(message)
                progress.progress(min(0.20, 0.20 * current / max(1, total)))

            rounds = collect_completed_rounds(
                history_manager,
                requested_years,
                rounds_per_year=int(rounds_per_year),
                progress_callback=on_round_progress,
            )
            history = collect_historical_matches(rounds, fallback_matches)
            dataset = prepare_draw_dataset(
                rounds,
                history,
                training_years=training_years,
                validation_years=validation_years,
            )

            def on_trial_progress(item: TrialProgress) -> None:
                fraction = item.current_trial / max(1, item.trial_count)
                progress.progress(min(1.0, 0.20 + 0.80 * fraction))
                progress_slots[1].metric("現在Trial", item.current_trial)
                progress_slots[2].metric("Best Trial", item.best_trial)
                progress_slots[3].metric("Best Score", f"{item.best_score:.4f}")
                status.info(
                    f"残り{item.remaining_trials} Trial／"
                    f"経過{item.elapsed_seconds:.1f}秒"
                )

            result = run_draw_optimization(
                dataset,
                int(trial_count),
                progress_callback=on_trial_progress,
            )
            saved = save_optimization_result(result)
            st.session_state["version7a_optimization_result"] = result
            progress.progress(1.0)
            progress_slots[1].metric("現在Trial", result.trial_count)
            progress_slots[2].metric("Best Trial", result.best_trial)
            progress_slots[3].metric("Best Score", f"{result.best_score:.4f}")
            status.success(
                f"{result.trial_count} Trial完了。Training "
                f"{len(result.dataset.training_rows)}試合／Validation "
                f"{len(result.dataset.validation_rows)}試合"
            )
            if not saved:
                st.warning(
                    "結果は画面で確認できますが、専用履歴CSVを保存できませんでした。"
                )
        except OptunaUnavailableError as error:
            status.error(str(error))
        except (DrawOptimizationError, BacktestDataLeakError, ValueError) as error:
            status.error(str(error))
        except Exception:
            status.error(
                "Version7-A最適化を完了できませんでした。保存データと通信状態を確認してください。"
            )

    result = st.session_state.get("version7a_optimization_result")
    if isinstance(result, DrawOptimizationResult):
        _render_result(st, result)
