"""Version8-Bモデル診断・異常検知のStreamlit画面。"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Mapping, Optional

import pandas as pd
import streamlit as st

from diagnostic_config import LEAGUE_OPTIONS, PERIOD_OPTIONS
from diagnostic_history import (
    DiagnosticHistoryError,
    DiagnosticHistoryManager,
)
from history_manager import JAPAN_TIMEZONE
from live_history import LiveHistoryManager
from model_diagnostics import (
    ALL_VERSIONS,
    DiagnosticFilter,
    DiagnosticReport,
    available_versions,
    run_model_diagnostics,
)


def render_model_diagnostics_tab(
    *,
    live_history_manager: LiveHistoryManager,
    diagnostic_history_manager: DiagnosticHistoryManager,
) -> None:
    """実戦履歴の選択、手動診断、結果、診断履歴を表示する。"""

    st.subheader("Version8-B モデル診断")
    st.caption(
        "Version8-Aで予測時点に保存した確率・本命・設定・買い目と、"
        "後日確定した実結果だけを使用します。現在モデルで過去予測を再生成しません。"
    )
    st.warning(
        "この画面は問題の発見と数値比較までです。改善方法の提案、config.py変更、"
        "モデル採用、自動再最適化は行いません。"
    )

    rounds = live_history_manager.load_rounds()
    bets = live_history_manager.load_bets()
    for warning in dict.fromkeys(live_history_manager.warnings):
        st.warning(warning)
    live_history_manager.warnings.clear()
    _render_lightweight_counts(rounds, bets)

    version_options = (ALL_VERSIONS, *available_versions(live_history_manager))
    selectors = st.columns(3)
    period = selectors[0].selectbox(
        "診断対象期間",
        options=PERIOD_OPTIONS,
        key="version8b_period",
    )
    league = selectors[1].selectbox(
        "対象リーグ",
        options=LEAGUE_OPTIONS,
        key="version8b_league",
    )
    version = selectors[2].selectbox(
        "対象Version",
        options=version_options,
        key="version8b_version",
    )
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    if period == "任意期間":
        default_start, default_end = _date_bounds(rounds)
        dates = st.columns(2)
        start_date = dates[0].date_input(
            "開始日",
            value=default_start,
            key="version8b_start_date",
        )
        end_date = dates[1].date_input(
            "終了日",
            value=default_end,
            key="version8b_end_date",
        )

    if st.button(
        "モデル診断を実行",
        type="primary",
        key="version8b_run_diagnostics",
    ):
        try:
            selection = DiagnosticFilter(
                period=period,
                league=league,
                version=version,
                start_date=start_date,
                end_date=end_date,
            )
            with st.spinner("保存済み実戦履歴を診断しています..."):
                report = run_model_diagnostics(
                    live_history_manager,
                    selection,
                )
                diagnostic_history_manager.save(report)
            st.session_state["version8b_report"] = report
            st.success("モデル診断を実行し、診断履歴へ保存しました。")
        except (
            ValueError,
            DiagnosticHistoryError,
            OSError,
            KeyError,
            TypeError,
        ) as error:
            st.error(f"モデル診断を完了できません: {error}")

    report = st.session_state.get("version8b_report")
    if not isinstance(report, DiagnosticReport):
        st.info("対象を選び「モデル診断を実行」を押してください。")
        _render_diagnostic_history(diagnostic_history_manager)
        return
    _render_report(report)
    _render_diagnostic_history(diagnostic_history_manager)


def _render_lightweight_counts(rounds: pd.DataFrame, bets: pd.DataFrame) -> None:
    if rounds.empty:
        st.info("Version8-A実戦履歴はまだありません。診断結果はデータ不足になります。")
        return
    purchased_ids = set(
        bets.loc[
            bets["record_type"].astype(str) == "purchased",
            "prediction_run_id",
        ].astype(str)
    ) if not bets.empty else set()
    columns = st.columns(4)
    columns[0].metric("予測済みrun", len(rounds))
    columns[1].metric(
        "結果未確定run",
        int(
            rounds["round_status"].astype(str).isin(
                ("predicted", "purchased", "pending_result")
            ).sum()
        ),
    )
    columns[2].metric(
        "結果確定済みrun",
        int(
            rounds["round_status"].astype(str).isin(
                ("result_confirmed", "evaluated")
            ).sum()
        ),
    )
    columns[3].metric("実購入ありrun", len(purchased_ids))


def _render_report(report: DiagnosticReport) -> None:
    st.divider()
    st.subheader("現在のモデル状態")
    if report.status == "正常":
        st.success(f"モデル状態：正常 — {report.status_reason}")
    elif report.status == "警告":
        st.error(f"モデル状態：警告 — {report.status_reason}")
    elif report.status == "注意":
        st.warning(f"モデル状態：注意 — {report.status_reason}")
    else:
        st.info(f"モデル状態：データ不足 — {report.status_reason}")
    st.caption(
        f"対象：{report.selection.period} / {report.selection.league} / "
        f"{report.selection.version} / 診断日時：{report.diagnosed_at.isoformat()}"
    )

    st.subheader("診断対象データ")
    counts = report.counts
    first = st.columns(4)
    first[0].metric("予測済みrun", counts.predicted_run_count)
    first[1].metric("結果未確定run", counts.pending_run_count)
    first[2].metric("結果確定済みrun", counts.confirmed_run_count)
    first[3].metric("評価済みrun", counts.evaluated_run_count)
    second = st.columns(4)
    second[0].metric("購入ありrun", counts.purchased_run_count)
    second[1].metric("購入なしrun", counts.unpurchased_run_count)
    second[2].metric("診断開催回数", counts.round_count)
    second[3].metric("診断試合数", counts.match_count)

    st.subheader("全体指標")
    overall = report.overall
    metrics = st.columns(4)
    metrics[0].metric(
        "全体的中率",
        _percent(overall.accuracy if overall else None),
    )
    metrics[1].metric(
        "Brier Score",
        _number(overall.brier_score if overall else None),
    )
    metrics[2].metric(
        "Log Loss",
        _number(overall.log_loss if overall else None),
    )
    metrics[3].metric(
        "Calibration Error",
        _number(overall.calibration_error if overall else None),
    )
    probability_metrics = st.columns(3)
    probability_metrics[0].metric(
        "平均本命割当確率",
        _percent(report.average_predicted_probability),
    )
    probability_metrics[1].metric(
        "平均最大確率",
        _percent(report.average_max_probability),
    )
    probability_metrics[2].metric(
        "期待的中数",
        _number(overall.expected_hits if overall else None, digits=2),
    )

    st.subheader("1 / 0 / 2別診断")
    st.dataframe(_class_metrics_frame(report), width="stretch", hide_index=True)
    st.caption("Precision・Recall・F1は各結果を陽性としたone-vs-rest評価です。")

    st.subheader("引分診断")
    draw = report.draw
    draw_primary = st.columns(4)
    draw_primary[0].metric("実引分率", _percent(draw.actual_draw_rate))
    draw_primary[1].metric("本命0率", _percent(draw.favorite_draw_rate))
    draw_primary[2].metric("引分候補率", _percent(draw.candidate_rate))
    draw_primary[3].metric("平均P(0)", _percent(draw.mean_probability_0))
    draw_counts = st.columns(4)
    draw_counts[0].metric("実際の引分数", draw.actual_draw_count)
    draw_counts[1].metric("本命0的中数", draw.favorite_draw_hit_count)
    draw_counts[2].metric("引分候補的中数", draw.candidate_hit_count)
    draw_counts[3].metric(
        "平均P(0)−実引分率",
        _signed_points(draw.probability_actual_gap),
    )
    draw_scores = st.columns(5)
    draw_scores[0].metric("引分Precision", _number(draw.precision))
    draw_scores[1].metric("引分Recall", _number(draw.recall))
    draw_scores[2].metric("引分F1", _number(draw.f1_score))
    draw_scores[3].metric("引分Brier", _number(draw.brier_score))
    draw_scores[4].metric("引分Calibration", _number(draw.calibration_error))
    inclusion = st.columns(4)
    inclusion[0].metric(
        "AI推奨の0採用率",
        _percent(draw.recommended_draw_inclusion_rate),
    )
    inclusion[1].metric(
        "実購入の0採用率",
        _percent(draw.purchased_draw_inclusion_rate),
    )
    inclusion[2].metric(
        "AI推奨で実引分カバー",
        draw.recommended_draw_covered_count,
    )
    inclusion[3].metric(
        "実購入で実引分カバー",
        draw.purchased_draw_covered_count,
    )
    st.caption(
        "Draw Inclusion Score平均："
        f"{_number(draw.draw_inclusion_score_mean)}。元データがない項目はN/Aです。"
    )

    st.subheader("確率帯別Calibration")
    st.dataframe(
        _format_probability_table(report.calibration_table),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Rolling診断・モデル劣化比較")
    st.dataframe(
        _format_metric_table(report.rolling_summary),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Rolling比較は対象リーグ・Version内の全保存履歴を基準に、"
        "直近5/10開催を比較します。画面上部の期間指定は全体指標・"
        "時系列・group集計へ適用します。"
    )

    st.subheader("時系列推移")
    _render_timeline(report.timeline)

    st.subheader("異常一覧")
    if report.anomalies:
        st.dataframe(
            _anomaly_frame(report),
            width="stretch",
            hide_index=True,
        )
    else:
        st.success("検知した異常はありません。")
    st.caption("判定は事実 → 数値 → 閾値比較の固定ルールで行います。")
    with st.expander("異常判定ルールと閾値"):
        st.write(
            "警告が1件以上なら総合状態は警告、警告がなくサンプル不足なら"
            "データ不足、サンプル十分で注意が1件以上なら注意、それ以外は正常です。"
        )
        st.dataframe(
            _threshold_frame(report),
            width="stretch",
            hide_index=True,
        )

    st.subheader("リーグ別診断")
    st.dataframe(
        _format_metric_table(report.league_summary),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Version別診断")
    st.dataframe(
        _format_metric_table(report.version_summary),
        width="stretch",
        hide_index=True,
    )
    st.caption("Versionごとに対象期間が異なる場合があり、単純比較には期間差を含みます。")

    st.subheader("設定スナップショットgroup別診断")
    st.dataframe(
        _format_metric_table(report.settings_summary),
        width="stretch",
        hide_index=True,
    )
    st.caption("同一設定groupの集計であり、設定変更の因果関係は判定しません。")

    st.subheader("買い目診断")
    if report.selection.league != "全リーグ":
        st.caption(
            "買い目・Coverage・ROIは選択リーグを含むrun全体の保存値です。"
            "混在runの金額や払戻をリーグ別に推測按分しません。"
        )
    st.dataframe(
        _format_bet_summary(report.bet_summary),
        width="stretch",
        hide_index=True,
    )
    _render_financial_performance("実購入成績", report.purchase_performance)
    _render_financial_performance(
        "AI推奨買い目のシミュレーション性能",
        report.simulation_performance,
    )

    st.subheader("Coverage診断")
    st.warning(
        "Coverageは保存時のモデル確率質量に基づく参考指標で、実際の当選確率ではありません。"
    )
    st.dataframe(
        _format_probability_table(report.coverage_summary),
        width="stretch",
        hide_index=True,
    )

    st.subheader("データ品質診断")
    st.caption(
        "データ品質は元のVersion8-A実戦履歴CSV全体を検査し、"
        "モデル性能の期間・リーグ・Version絞り込みとは分離して表示します。"
    )
    if report.quality_issues:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "異常": item.name,
                        "レベル": item.level,
                        "件数": item.count,
                        "除外数": item.excluded_count,
                        "内容": item.message,
                    }
                    for item in report.quality_issues
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.success("データ品質異常は検知されませんでした。")
    st.caption(
        f"診断計算から除外した試合行：{report.excluded_match_count}件。"
        "不正データは削除・修正していません。"
    )


def _render_timeline(frame: pd.DataFrame) -> None:
    if (
        frame.empty
        or pd.to_numeric(frame.get("的中率"), errors="coerce").notna().sum() == 0
    ):
        st.info("時系列グラフを表示できる結果確定データがありません。")
        return
    graph = frame.copy()
    graph.index = graph["開催回"].astype(str)
    st.line_chart(
        graph[["的中率", "Brier Score", "Log Loss", "Calibration"]],
        width="stretch",
    )
    st.line_chart(
        graph[["引分F1", "本命0率", "実引分率"]],
        width="stretch",
    )
    st.caption(
        "結果確定データがない開催回は欠損点として保持し、前後の線をつないでいません。"
    )


def _render_financial_performance(
    heading: str,
    performance: Mapping[str, Any],
) -> None:
    st.markdown(f"**{heading}**")
    if not performance.get("has_records"):
        st.info(f"{performance.get('label', heading)}データなし")
        return
    if not performance.get("has_evaluated_records"):
        st.info(
            f"{performance.get('label', heading)}はありますが、"
            "払戻を確認できる評価済みデータがありません。ROIはN/Aです。"
        )
        return
    columns = st.columns(4)
    count_label = (
        "購入回数（評価済みrun）"
        if str(performance.get("label", "")).startswith("実購入")
        else "評価済みrun"
    )
    columns[0].metric(
        count_label,
        performance.get("evaluated_run_count", 0),
    )
    columns[1].metric(
        "総購入金額",
        _yen(performance.get("total_amount_yen")),
    )
    columns[2].metric(
        "総払戻",
        _yen(performance.get("total_return_yen")),
    )
    columns[3].metric("ROI", _percent(performance.get("roi")))
    details = st.columns(3)
    details[0].metric("収支", _yen(performance.get("profit_yen"), signed=True))
    details[1].metric(
        "最高払戻",
        _yen(performance.get("highest_return_yen")),
    )
    details[2].metric(
        "最大損失",
        _yen(performance.get("maximum_loss_yen"), signed=True),
    )
    if performance.get("pending_count", 0):
        st.caption(
            f"全{performance.get('record_count', 0)}記録中、"
            f"{performance.get('pending_count', 0)}件は払戻未確認のため"
            "金額・収支・ROI集計から除外しています。"
        )


def _render_diagnostic_history(manager: DiagnosticHistoryManager) -> None:
    st.divider()
    st.subheader("診断履歴")
    history = manager.load()
    for warning in dict.fromkeys(manager.warnings):
        st.warning(warning)
    manager.warnings.clear()
    if history.empty:
        st.info("保存済み診断履歴はありません。")
        return
    display_columns = [
        "diagnosed_at",
        "period",
        "league",
        "prediction_version",
        "match_count",
        "round_count",
        "accuracy",
        "brier_score",
        "log_loss",
        "calibration_error",
        "draw_f1",
        "model_status",
        "anomaly_count",
    ]
    st.dataframe(history[display_columns], width="stretch", hide_index=True)
    st.download_button(
        "診断履歴CSV",
        data=manager.export_csv(),
        file_name="model_diagnostic_history.csv",
        mime="text/csv",
        key="version8b_download_history",
    )


def _class_metrics_frame(report: DiagnosticReport) -> pd.DataFrame:
    rows = []
    for outcome in ("1", "0", "2"):
        item = report.class_metrics[outcome]
        rows.append(
            {
                "結果": outcome,
                "予測数": item.predicted_count,
                "実発生数": item.actual_count,
                "的中数": item.hit_count,
                "Precision": _number(item.precision),
                "Recall": _number(item.recall),
                "F1 Score": _number(item.f1_score),
                "Brier Score": _number(item.brier_score),
                "Calibration": _number(item.calibration_error),
                "予測時平均確率": _percent(item.mean_probability),
                "実際の発生率": _percent(item.actual_rate),
            }
        )
    return pd.DataFrame(rows)


def _threshold_frame(report: DiagnosticReport) -> pd.DataFrame:
    thresholds = report.thresholds
    definitions = (
        ("最低試合数", "minimum_match_count", "件"),
        ("最低開催回数", "minimum_round_count", "開催"),
        ("結果別最低実発生数", "minimum_class_support", "件"),
        ("高確率予測最低試合数", "minimum_high_probability_count", "件"),
        ("Coverage帯最低評価数", "minimum_coverage_evaluated_count", "件"),
        ("的中率低下・注意", "accuracy_drop_attention", "pt"),
        ("的中率低下・警告", "accuracy_drop_warning", "pt"),
        ("Brier悪化・注意", "brier_increase_attention", ""),
        ("Brier悪化・警告", "brier_increase_warning", ""),
        ("Log Loss悪化・注意", "log_loss_increase_attention", ""),
        ("Log Loss悪化・警告", "log_loss_increase_warning", ""),
        ("Calibration悪化・注意", "calibration_increase_attention", "pt"),
        ("Calibration悪化・警告", "calibration_increase_warning", "pt"),
        ("引分F1低下・注意", "draw_f1_drop_attention", "pt"),
        ("引分F1低下・警告", "draw_f1_drop_warning", "pt"),
        ("実引分率－本命0率・注意", "draw_favorite_gap_attention", "pt"),
        ("実引分率－本命0率・警告", "draw_favorite_gap_warning", "pt"),
        ("結果別Recall・注意以下", "low_recall_attention", "pt"),
        ("結果別Recall・警告以下", "low_recall_warning", "pt"),
        ("高確率予測の下限", "high_probability_threshold", "pt"),
        ("高確率予測乖離・注意", "high_probability_gap_attention", "pt"),
        ("高確率予測乖離・警告", "high_probability_gap_warning", "pt"),
        ("リーグ的中率差・注意", "league_accuracy_gap_attention", "pt"),
        ("リーグ的中率差・警告", "league_accuracy_gap_warning", "pt"),
        ("リーグBrier差・注意", "league_brier_gap_attention", ""),
        ("リーグBrier差・警告", "league_brier_gap_warning", ""),
        ("リーグLog Loss差・注意", "league_log_loss_gap_attention", ""),
        ("リーグLog Loss差・警告", "league_log_loss_gap_warning", ""),
        ("リーグCalibration差・注意", "league_calibration_gap_attention", "pt"),
        ("リーグCalibration差・警告", "league_calibration_gap_warning", "pt"),
        ("リーグ引分F1差・注意", "league_draw_f1_gap_attention", "pt"),
        ("リーグ引分F1差・警告", "league_draw_f1_gap_warning", "pt"),
        ("確率合計許容誤差", "probability_sum_tolerance", ""),
    )
    return pd.DataFrame(
        [
            {
                "判定項目": label,
                "設定名": name,
                "閾値": _diagnostic_value(getattr(thresholds, name), unit),
            }
            for label, name, unit in definitions
        ]
    )


def _anomaly_frame(report: DiagnosticReport) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "区分": item.category,
                "異常名": item.name,
                "指標": item.metric,
                "現在値": _diagnostic_value(item.current_value, item.unit),
                "基準値": _diagnostic_value(item.baseline_value, item.unit),
                "差": _diagnostic_value(item.difference, item.unit, signed=True),
                "判定": item.judgement,
                "レベル": item.level,
                "診断コメント": item.message,
            }
            for item in report.anomalies
        ]
    )


def _format_metric_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    for column in (
        "的中率",
        "Calibration",
        "引分F1",
        "全期間的中率",
        "全期間Calibration",
        "全期間引分F1",
        "的中率差",
        "Calibration差",
        "引分F1差",
    ):
        if column in result.columns:
            result[column] = result[column].map(_percent)
    for column in (
        "Brier Score",
        "Log Loss",
        "全期間Brier",
        "全期間Log Loss",
        "Brier差",
        "Log Loss差",
    ):
        if column in result.columns:
            result[column] = result[column].map(_number)
    return result


def _format_probability_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    for column in (
        "平均予測確率",
        "実発生率",
        "Calibration差",
        "完全カバー率",
    ):
        if column in result.columns:
            result[column] = result[column].map(_percent)
    return result


def _format_bet_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    for column in ("平均Coverage", "全結果カバー率"):
        result[column] = result[column].map(_percent)
    result["平均購入金額"] = result["平均購入金額"].map(_yen)
    result["平均口数"] = result["平均口数"].map(
        lambda value: "N/A" if _finite(value) is None else f"{_finite(value):.1f}"
    )
    return result


def _date_bounds(rounds: pd.DataFrame) -> tuple[date, date]:
    today = datetime.now(JAPAN_TIMEZONE).date()
    if rounds.empty:
        return today, today
    values = pd.to_datetime(rounds["round_start_at"], errors="coerce").dropna()
    if values.empty:
        return today, today
    return values.min().date(), values.max().date()


def _diagnostic_value(
    value: Any,
    unit: str,
    *,
    signed: bool = False,
) -> str:
    number = _finite(value)
    if number is None:
        return "N/A"
    sign = "+" if signed and number > 0 else ""
    if unit == "pt":
        return f"{sign}{number:.1%}"
    if unit == "件":
        return f"{number:.0f}件"
    if unit == "開催":
        return f"{number:.0f}開催"
    return f"{sign}{number:.4f}"


def _percent(value: Any) -> str:
    number = _finite(value)
    return "N/A" if number is None else f"{number:.1%}"


def _signed_points(value: Any) -> str:
    number = _finite(value)
    return "N/A" if number is None else f"{number:+.1%}"


def _number(value: Any, *, digits: int = 4) -> str:
    number = _finite(value)
    return "N/A" if number is None else f"{number:.{digits}f}"


def _yen(value: Any, *, signed: bool = False) -> str:
    number = _finite(value)
    if number is None:
        return "N/A"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:,.0f}円"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


__all__ = ["render_model_diagnostics_tab"]
