"""Version6の開催回分析、Version比較、表・グラフを生成する。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

import pandas as pd

from backtest import (
    BacktestError,
    BacktestResult,
    fetch_historical_matches,
    run_backtest,
)
from draw_optimizer import load_active_draw_settings, prepare_draw_round
from draw_predictor import DrawSettings, predict_draw_aware
from history_manager import TotoHistoryManager, TotoPayouts, TotoRoundSummary
from metrics import TOTO_OUTCOMES, aggregate_roi, evaluate_model
from model_config import VERSION7A_MODEL_VERSION
from prediction_history import (
    HISTORY_COLUMNS,
    PredictionHistoryRecord,
    PredictionHistoryManager,
    history_csv_bytes,
)


@dataclass(frozen=True)
class AnalysisTables:
    """分析画面の表とグラフが共有する集計結果。"""

    round_summary: pd.DataFrame
    version_summary: pd.DataFrame
    cumulative_trend: pd.DataFrame
    class_accuracy_trend: pd.DataFrame
    prediction_share_trend: pd.DataFrame
    calibration: pd.DataFrame


def version7a_history_records(
    toto_round,
    historical_matches: Sequence,
    *,
    settings: DrawSettings,
    generated_at: datetime,
) -> list[PredictionHistoryRecord]:
    """過去開催日時点のVersion7-Aを履歴CSV用13行へ変換する。"""

    prepared = prepare_draw_round(toto_round, historical_matches)
    matches_by_number = {
        match.match_number: match for match in prepared.toto_round.matches
    }
    generated_text = generated_at.isoformat()
    records = []
    for row in prepared.rows:
        prediction = predict_draw_aware(
            row.base_probabilities,
            row.home_expected_goals,
            row.away_expected_goals,
            row.home_input,
            row.away_input,
            context=row.context,
            settings=settings,
        )
        toto_match = matches_by_number[row.match_number]
        records.append(
            PredictionHistoryRecord(
                toto_round=row.round_id,
                toto_match_number=row.match_number,
                prediction_version=VERSION7A_MODEL_VERSION,
                prediction_date=generated_text,
                home_team=toto_match.home_team,
                away_team=toto_match.away_team,
                prediction=prediction.prediction,
                probability_1=float(prediction.probabilities["1"]),
                probability_0=float(prediction.probabilities["0"]),
                probability_2=float(prediction.probabilities["2"]),
                home_expected_goals=float(row.home_expected_goals),
                away_expected_goals=float(row.away_expected_goals),
                actual_result=row.actual_result,
                hit=prediction.prediction == row.actual_result,
            )
        )
    return records


def _probability_rows(group: pd.DataFrame) -> list[dict[str, float]]:
    rows = []
    for _, row in group.iterrows():
        probabilities = {}
        for outcome in TOTO_OUTCOMES:
            value = pd.to_numeric(
                row.get(f"probability_{outcome}"),
                errors="coerce",
            )
            probabilities[outcome] = (
                float(value) if not pd.isna(value) else 1.0 / 3.0
            )
        rows.append(probabilities)
    return rows


def _round_metric_row(
    round_id: int,
    version: str,
    group: pd.DataFrame,
) -> Optional[dict]:
    ordered = group.sort_values("toto_match_number")
    actuals = [str(value) for value in ordered["actual_result"].fillna("")]
    predictions = [str(value) for value in ordered["prediction"]]
    if len(ordered) != 13 or not all(
        actual in TOTO_OUTCOMES for actual in actuals
    ):
        return None
    stake_value = pd.to_numeric(ordered.iloc[0].get("stake_yen"), errors="coerce")
    payout_value = pd.to_numeric(ordered.iloc[0].get("payout_yen"), errors="coerce")
    metrics = evaluate_model(
        predictions,
        _probability_rows(ordered),
        actuals,
        stake_yen=(int(stake_value) if not pd.isna(stake_value) else 100),
        payout_yen=(int(payout_value) if not pd.isna(payout_value) else 0),
    )
    return {
        "開催回": int(round_id),
        "Version": version,
        "13試合的中数": metrics.hit_count,
        "全体的中率": metrics.accuracy,
        "1正答率": metrics.class_accuracy["1"],
        "0正答率": metrics.class_accuracy["0"],
        "2正答率": metrics.class_accuracy["2"],
        "1予測割合": metrics.prediction_share["1"],
        "0予測割合": metrics.prediction_share["0"],
        "2予測割合": metrics.prediction_share["2"],
        "ホーム実結果率": metrics.actual_share["1"],
        "引分実結果率": metrics.actual_share["0"],
        "アウェイ実結果率": metrics.actual_share["2"],
        "Brier Score": metrics.brier_score,
        "Log Loss": metrics.log_loss,
        "Calibration": metrics.calibration_error,
        "的中期待値": metrics.expected_hits,
        "購入額": metrics.stake_yen,
        "払戻額": metrics.payout_yen,
        "ROI": metrics.roi,
    }


def build_analysis_tables(history: pd.DataFrame) -> AnalysisTables:
    """履歴CSVから開催回・累積・Version・Calibration表を作る。"""

    empty = pd.DataFrame()
    if not isinstance(history, pd.DataFrame) or history.empty:
        return AnalysisTables(empty, empty, empty, empty, empty, empty)
    required = {
        "toto_round",
        "toto_match_number",
        "prediction_version",
        "prediction",
        "actual_result",
    }
    if not required.issubset(history.columns):
        return AnalysisTables(empty, empty, empty, empty, empty, empty)

    rows = []
    for (round_value, version), group in history.groupby(
        ["toto_round", "prediction_version"],
        dropna=False,
    ):
        try:
            round_id = int(round_value)
        except (TypeError, ValueError):
            continue
        row = _round_metric_row(round_id, str(version), group)
        if row is not None:
            rows.append(row)

    round_summary = pd.DataFrame(rows)
    if round_summary.empty:
        return AnalysisTables(empty, empty, empty, empty, empty, empty)
    round_summary = round_summary.sort_values(
        ["開催回", "Version"]
    ).reset_index(drop=True)

    cumulative_rows = []
    for version, version_rows in round_summary.groupby("Version"):
        version_rows = version_rows.sort_values("開催回")
        cumulative_hits = 0
        cumulative_matches = 0
        cumulative_payout = 0
        cumulative_stake = 0
        for count, (_, row) in enumerate(version_rows.iterrows(), start=1):
            cumulative_hits += int(row["13試合的中数"])
            cumulative_matches += 13
            cumulative_payout += int(row["払戻額"])
            cumulative_stake += int(row["購入額"])
            cumulative_rows.append(
                {
                    "開催回": int(row["開催回"]),
                    "Version": version,
                    "累積開催数": count,
                    "累積的中率": cumulative_hits / cumulative_matches,
                    "累積ROI": (
                        cumulative_payout / cumulative_stake * 100
                        if cumulative_stake > 0
                        else None
                    ),
                }
            )
    cumulative_trend = pd.DataFrame(cumulative_rows)

    version_rows = []
    calibration_rows = []
    for version, version_history in history.groupby("prediction_version"):
        valid_history = version_history.loc[
            version_history["actual_result"].astype(str).isin(TOTO_OUTCOMES)
        ].sort_values(["toto_round", "toto_match_number"])
        if valid_history.empty:
            continue
        predictions = [str(value) for value in valid_history["prediction"]]
        actuals = [str(value) for value in valid_history["actual_result"]]
        round_metrics = round_summary.loc[
            round_summary["Version"] == version
        ]
        total_stake = int(round_metrics["購入額"].sum())
        total_payout = int(round_metrics["払戻額"].sum())
        metrics = evaluate_model(
            predictions,
            _probability_rows(valid_history),
            actuals,
            stake_yen=total_stake,
            payout_yen=total_payout,
        )
        version_rows.append(
            {
                "Version": version,
                "累積開催数": int(round_metrics["開催回"].nunique()),
                "累積試合数": metrics.match_count,
                "累積的中数": metrics.hit_count,
                "累積的中率": metrics.accuracy,
                "1正答率": metrics.class_accuracy["1"],
                "0正答率": metrics.class_accuracy["0"],
                "2正答率": metrics.class_accuracy["2"],
                "ホーム予測率": metrics.prediction_share["1"],
                "引分予測率": metrics.prediction_share["0"],
                "アウェイ予測率": metrics.prediction_share["2"],
                "ホーム実結果率": metrics.actual_share["1"],
                "引分実結果率": metrics.actual_share["0"],
                "アウェイ実結果率": metrics.actual_share["2"],
                "Brier Score": metrics.brier_score,
                "Log Loss": metrics.log_loss,
                "Calibration": metrics.calibration_error,
                "的中期待値": metrics.expected_hits,
                "ROI": aggregate_roi(
                    round_metrics["払戻額"],
                    round_metrics["購入額"],
                ),
            }
        )
        for calibration_bin in metrics.calibration_bins:
            calibration_rows.append(
                {
                    "Version": version,
                    "確率帯": (
                        f"{calibration_bin.lower:.0%}～"
                        f"{calibration_bin.upper:.0%}"
                    ),
                    "試合数": calibration_bin.count,
                    "平均予測確率": calibration_bin.mean_confidence,
                    "実際の的中率": calibration_bin.actual_accuracy,
                    "差": calibration_bin.gap,
                }
            )

    version_summary = pd.DataFrame(version_rows).sort_values("Version")
    calibration = pd.DataFrame(calibration_rows)
    class_accuracy_trend = round_summary[
        ["開催回", "Version", "1正答率", "0正答率", "2正答率"]
    ].copy()
    prediction_share_trend = round_summary[
        ["開催回", "Version", "1予測割合", "0予測割合", "2予測割合"]
    ].copy()

    return AnalysisTables(
        round_summary=round_summary,
        version_summary=version_summary,
        cumulative_trend=cumulative_trend,
        class_accuracy_trend=class_accuracy_trend,
        prediction_share_trend=prediction_share_trend,
        calibration=calibration,
    )


def backtest_comparison_frame(result: BacktestResult) -> pd.DataFrame:
    """Version4～6の本命・勝率・期待得点・変更有無を13行で返す。"""

    rows = []
    for match_result in result.matches:
        version4 = match_result.versions["Version4"]
        version5 = match_result.versions["Version5"]
        version6 = match_result.versions["Version6"]
        rows.append(
            {
                "試合番号": match_result.toto_match.match_number,
                "対戦カード": (
                    f"{match_result.toto_match.home_team} vs "
                    f"{match_result.toto_match.away_team}"
                ),
                "実結果": match_result.actual_result,
                "Version4本命": version4.prediction,
                "Version4勝率": version4.top_probability,
                "Version4期待得点": (
                    f"{version4.home_expected_goals:.2f}-"
                    f"{version4.away_expected_goals:.2f}"
                ),
                "Version5本命": version5.prediction,
                "Version5勝率": version5.top_probability,
                "Version5期待得点": (
                    f"{version5.home_expected_goals:.2f}-"
                    f"{version5.away_expected_goals:.2f}"
                ),
                "Version6本命": version6.prediction,
                "Version6勝率": version6.top_probability,
                "Version6期待得点": (
                    f"{version6.home_expected_goals:.2f}-"
                    f"{version6.away_expected_goals:.2f}"
                ),
                "V4→V5変更": version4.prediction != version5.prediction,
                "V5→V6変更": version5.prediction != version6.prediction,
                "一致／不一致": (
                    "一致"
                    if version6.prediction == match_result.actual_result
                    else "不一致"
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("試合番号").reset_index(drop=True)


def backtest_metrics_frame(result: BacktestResult) -> pd.DataFrame:
    rows = []
    for version in ("Version4", "Version5", "Version6"):
        metrics = result.metrics_by_version[version]
        rows.append(
            {
                "Version": version,
                "的中数": metrics.hit_count,
                "的中率": metrics.accuracy,
                "1正答率": metrics.class_accuracy["1"],
                "0正答率": metrics.class_accuracy["0"],
                "2正答率": metrics.class_accuracy["2"],
                "Brier Score": metrics.brier_score,
                "Log Loss": metrics.log_loss,
                "Calibration": metrics.calibration_error,
                "的中期待値": metrics.expected_hits,
                "ROI": metrics.roi,
            }
        )
    return pd.DataFrame(rows)


def _format_catalog(summary: TotoRoundSummary) -> str:
    return f"{summary.label}｜{summary.fiscal_year}年"


def render_backtest_result(result: BacktestResult) -> None:
    """Streamlitへ選択開催回の比較・指標を表示する。"""

    import streamlit as st

    st.subheader(f"第{result.toto_round.round_id}回 バックテスト結果")
    st.caption(
        "データ基準："
        f"{result.cutoff_at.isoformat()}より前のみ ／ "
        f"使用した完了試合 {result.historical_match_count}件"
    )
    st.dataframe(
        backtest_comparison_frame(result),
        width="stretch",
        hide_index=True,
        column_config={
            "Version4勝率": st.column_config.NumberColumn(format="percent"),
            "Version5勝率": st.column_config.NumberColumn(format="percent"),
            "Version6勝率": st.column_config.NumberColumn(format="percent"),
        },
    )
    st.dataframe(
        backtest_metrics_frame(result),
        width="stretch",
        hide_index=True,
        column_config={
            "的中率": st.column_config.NumberColumn(format="percent"),
            "1正答率": st.column_config.NumberColumn(format="percent"),
            "0正答率": st.column_config.NumberColumn(format="percent"),
            "2正答率": st.column_config.NumberColumn(format="percent"),
            "ROI": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )


def render_analysis_dashboard(history: pd.DataFrame) -> None:
    """履歴から分析表と指定グラフをStreamlitへ表示する。"""

    import streamlit as st

    tables = build_analysis_tables(history)
    if tables.round_summary.empty:
        st.info(
            "実結果付きの予想履歴がありません。"
            "過去開催回をバックテストすると分析が表示されます。"
        )
        return

    version6_summary = tables.version_summary.loc[
        tables.version_summary["Version"] == "Version6"
    ]
    if not version6_summary.empty:
        row = version6_summary.iloc[0]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("累積開催数", int(row["累積開催数"]))
        col2.metric("累積的中率", f'{row["累積的中率"]:.1%}')
        col3.metric("Brier Score", f'{row["Brier Score"]:.4f}')
        col4.metric("ROI", f'{row["ROI"]:.1f}%')

    st.subheader("開催回一覧")
    st.dataframe(
        tables.round_summary.sort_values(
            ["開催回", "Version"], ascending=[False, True]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Version比較")
    st.dataframe(
        tables.version_summary,
        width="stretch",
        hide_index=True,
    )

    st.subheader("開催回別的中数")
    st.bar_chart(
        tables.round_summary.pivot(
            index="開催回",
            columns="Version",
            values="13試合的中数",
        )
    )

    st.subheader("累積的中率")
    st.line_chart(
        tables.cumulative_trend.pivot(
            index="開催回",
            columns="Version",
            values="累積的中率",
        )
    )

    st.subheader("Version比較（Brier Score）")
    st.bar_chart(
        tables.version_summary.set_index("Version")[["Brier Score"]]
    )

    st.subheader("1・0・2別正答率の推移")
    version6_class = tables.class_accuracy_trend.loc[
        tables.class_accuracy_trend["Version"] == "Version6"
    ].set_index("開催回")
    st.line_chart(version6_class[["1正答率", "0正答率", "2正答率"]])

    st.subheader("ホーム・引分・アウェイ予測割合")
    version6_share = tables.prediction_share_trend.loc[
        tables.prediction_share_trend["Version"] == "Version6"
    ].set_index("開催回")
    st.area_chart(version6_share[["1予測割合", "0予測割合", "2予測割合"]])

    st.subheader("Calibration")
    st.caption(
        "本命確率帯ごとの平均予測確率と実際の的中率です。"
        "Calibration（ECE）は小さいほど信頼性が高い指標です。"
    )
    st.dataframe(
        tables.calibration,
        width="stretch",
        hide_index=True,
    )


def render_analysis_tab(
    *,
    history_manager: TotoHistoryManager,
    prediction_history_manager: PredictionHistoryManager,
    fallback_matches: Sequence = (),
) -> None:
    """開催回選択・バックテスト・履歴分析を1タブへ描画する。"""

    import streamlit as st

    st.header("分析")
    st.caption(
        "toto公式開催回を指定し、開催初日0:00より前のデータだけで"
        "Version4～Version7-Aを比較します。"
    )

    if "toto_round_catalog" not in st.session_state:
        st.session_state["toto_round_catalog"] = ()

    if st.button("直近1年以上の開催回一覧を取得", key="load_toto_catalog"):
        catalog = history_manager.load_catalog()
        st.session_state["toto_round_catalog"] = catalog
        if catalog:
            st.success(f"{len(catalog)}開催回を取得しました。")
        else:
            st.warning("開催回一覧を取得できませんでした。")

    catalog = tuple(st.session_state.get("toto_round_catalog", ()))
    history = prediction_history_manager.load()
    history_rounds = sorted(
        {
            int(value)
            for value in pd.to_numeric(
                history.get("toto_round", pd.Series(dtype=float)),
                errors="coerce",
            )
            if not pd.isna(value) and int(value) > 0
        },
        reverse=True,
    )

    if catalog:
        selected_summary = st.selectbox(
            "バックテストする開催回",
            options=catalog,
            format_func=_format_catalog,
            key="backtest_round_summary",
        )
        selected_round_id = selected_summary.round_id
        st.dataframe(
            pd.DataFrame(
                {
                    "開催回": [summary.round_id for summary in catalog],
                    "年度": [summary.fiscal_year for summary in catalog],
                    "結果発表日": [summary.label for summary in catalog],
                }
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        selected_round_id = int(
            st.number_input(
                "バックテストする開催回",
                min_value=1,
                value=(history_rounds[0] if history_rounds else 1548),
                step=1,
                key="backtest_round_number",
            )
        )

    if st.button(
        "指定開催回をバックテスト",
        type="primary",
        key="run_backtest",
    ):
        st.session_state.pop("latest_backtest_result", None)
        round_result = history_manager.load_round(selected_round_id)
        if not round_result.is_loaded or round_result.toto_round is None:
            st.error(round_result.message)
        elif not round_result.toto_round.is_complete:
            st.warning("この開催回は13試合の実結果が未確定です。")
        elif not round_result.toto_round.is_jleague_round:
            st.warning("Jリーグ以外を含む開催回はバックテスト対象外です。")
        else:
            try:
                official_history = fetch_historical_matches(
                    round_result.toto_round,
                    fallback_matches=fallback_matches,
                )
                backtest_result = run_backtest(
                    round_result.toto_round,
                    official_history,
                )
                history_records = [
                    *backtest_result.history_records(),
                    *version7a_history_records(
                        round_result.toto_round,
                        official_history,
                        settings=load_active_draw_settings(),
                        generated_at=backtest_result.generated_at,
                    ),
                ]
                history_saved = prediction_history_manager.save_records(
                    history_records,
                    payouts_by_round={
                        round_result.toto_round.round_id:
                            round_result.toto_round.payouts
                    },
                )
                if history_saved:
                    # 同じ開催回を通常予想で保存済みなら、Version7-Bを含む
                    # 全Versionへ公式実結果を付与する。Version7-Cは予測Versionを
                    # 増やさず、ここで保存した確率を買い目評価へ利用する。
                    prediction_history_manager.reconcile_actual_results(
                        round_result.toto_round
                    )
                st.session_state["latest_backtest_result"] = backtest_result
                if history_saved:
                    st.success(
                        f"第{selected_round_id}回のバックテストを保存しました。"
                    )
                else:
                    st.error(
                        "バックテストは完了しましたが、"
                        "予想履歴CSVを保存できませんでした。"
                    )
            except BacktestError as error:
                st.error(str(error))
            except Exception:
                st.error(
                    "バックテストを完了できませんでした。"
                    "保存CSVまたは現在データを確認してください。"
                )

    latest_result: Optional[BacktestResult] = st.session_state.get(
        "latest_backtest_result"
    )
    if latest_result is not None:
        render_backtest_result(latest_result)

    history = prediction_history_manager.load()
    render_analysis_dashboard(history)

    if not history.empty:
        st.download_button(
            "予想履歴CSVを保存",
            data=history_csv_bytes(history),
            file_name="prediction_history.csv",
            mime="text/csv",
            key="download_prediction_history",
            width="stretch",
        )
