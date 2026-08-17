"""Version8-C AI改善提案のStreamlit画面。

Version8-BのDiagnosticReportを入力とし、設定・モデル・買い目を変更しない。
"""

from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd
import streamlit as st

from improvement_history import (
    ImprovementHistoryError,
    ImprovementHistoryManager,
)
from improvement_recommendations import (
    ImprovementRecommendation,
    ImprovementReport,
    generate_improvement_recommendations,
)
from model_diagnostics import DiagnosticReport


def render_improvement_recommendations_tab(
    *,
    improvement_history_manager: ImprovementHistoryManager,
) -> None:
    """最新のVersion8-B診断から手動で改善提案を生成・表示する。"""

    st.subheader("Version8-C AI改善提案")
    st.caption(
        "Version8-Bの数値診断と異常一覧を入力に、原因候補・改善候補・"
        "優先度・信頼度・再最適化の検討度を固定ルールで構造化します。"
    )
    st.warning(
        "提案は検証候補の表示だけです。config.py、設定、閾値、Coverage、"
        "買い目ルール、モデルを変更せず、再最適化も自動実行しません。"
    )

    diagnostic = st.session_state.get("version8b_report")
    if not isinstance(diagnostic, DiagnosticReport):
        st.subheader("1. 現在の状態")
        st.info(
            "データ不足：このセッションでVersion8-B診断が実行されていません。"
            "先に「モデル診断」タブで診断を実行してください。"
        )
        _render_empty_sections()
        _render_history(improvement_history_manager)
        return

    _render_source_summary(diagnostic)
    if st.button(
        "改善提案を生成",
        type="primary",
        key="version8c_generate_recommendations",
    ):
        try:
            with st.spinner("Version8-B診断から改善候補を構造化しています..."):
                report = generate_improvement_recommendations(diagnostic)
                improvement_history_manager.save(report)
            st.session_state["version8c_report"] = report
            st.success("改善提案を生成し、提案履歴へ保存しました。")
        except (
            ValueError,
            ImprovementHistoryError,
            OSError,
            KeyError,
            TypeError,
        ) as error:
            st.error(f"改善提案を生成できません: {error}")

    report = st.session_state.get("version8c_report")
    if not isinstance(report, ImprovementReport):
        st.info("「改善提案を生成」を押してください。")
        _render_history(improvement_history_manager)
        return
    if report.diagnostic_id != diagnostic.diagnostic_id:
        st.info(
            "表示中のVersion8-B診断が変わりました。現在の診断に対して"
            "「改善提案を生成」を押してください。"
        )
        _render_history(improvement_history_manager)
        return

    _render_report(report)
    _render_history(improvement_history_manager)


def _render_source_summary(diagnostic: DiagnosticReport) -> None:
    st.markdown("**提案元のVersion8-B診断**")
    columns = st.columns(4)
    columns[0].metric("総合モデル状態", diagnostic.status)
    columns[1].metric("結果確定試合", diagnostic.counts.match_count)
    columns[2].metric("結果確定開催", diagnostic.counts.round_count)
    columns[3].metric("異常", len(diagnostic.anomalies))
    st.caption(
        f"診断ID：{diagnostic.diagnostic_id} / "
        f"対象：{diagnostic.selection.period}・{diagnostic.selection.league}・"
        f"{diagnostic.selection.version}"
    )


def _render_report(report: ImprovementReport) -> None:
    st.divider()
    st.subheader("1. 現在の状態")
    if not report.data_sufficient:
        st.info(
            f"総合状態：{report.diagnostic_status} / "
            f"再最適化：{report.reoptimization_level}"
        )
    elif report.diagnostic_status == "警告":
        st.error(
            f"総合状態：警告 / 再最適化：{report.reoptimization_level}"
        )
    elif report.diagnostic_status == "注意":
        st.warning(
            f"総合状態：注意 / 再最適化：{report.reoptimization_level}"
        )
    else:
        st.success(
            f"総合状態：{report.diagnostic_status} / "
            f"再最適化：{report.reoptimization_level}"
        )
    st.write(report.reoptimization_reason)
    st.caption(
        f"{report.match_count}試合・{report.round_count}開催 / "
        f"文章モード：{_text_mode_label(report.text_mode)} / "
        f"提案ID：{report.improvement_id}"
    )

    st.subheader("2. 検知された問題")
    if report.detected_problems:
        for problem in report.detected_problems:
            st.markdown(f"- {problem}")
    else:
        st.success("提案対象となる問題は確認されませんでした。")
    evidence = _evidence_frame(report)
    if not evidence.empty:
        st.dataframe(evidence, width="stretch", hide_index=True)

    st.subheader("3. 原因候補")
    if report.recommendations:
        for item in report.recommendations:
            st.markdown(f"**{item.rank}位 {item.title}**")
            st.caption(item.diagnosis)
            for cause in item.possible_causes:
                st.markdown(f"- {cause}")
    else:
        st.info("原因候補を示すだけの十分な診断根拠はありません。")

    st.subheader("4. 改善候補")
    if report.recommendations:
        for item in report.recommendations:
            st.markdown(f"**{item.rank}位 {item.category}**")
            for candidate in item.improvement_candidates:
                st.markdown(f"- {candidate}")
    elif report.data_sufficient:
        st.success("現時点で設定変更を検討する改善候補はありません。監視を継続します。")
    else:
        st.info("データ不足のため、モデルや買い目設定の改善候補を生成しません。")

    st.subheader("5. 推奨アクション")
    st.write(report.recommended_action)
    if report.recommendations:
        st.dataframe(
            _ranking_frame(report),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "優先度・信頼度は異常レベル、影響範囲、サンプル数、複数指標、"
            "直近5/10開催の一致を使う固定ルールです。"
        )

    st.subheader("6. 注意事項")
    if not report.data_sufficient:
        st.warning("データ不足のため設定変更は推奨しません。")
    st.warning(report.notice)
    st.caption(
        "原因は確定事項ではなく可能性です。提案生成はVersion8-A実戦履歴を"
        "再計算せず、保存済みVersion8-B診断の数値・異常だけを主入力にします。"
    )


def _render_empty_sections() -> None:
    for heading, message in (
        ("2. 検知された問題", "診断未実行のため確認できません。"),
        ("3. 原因候補", "診断根拠がないため生成しません。"),
        ("4. 改善候補", "データ不足のため生成しません。"),
        ("5. 推奨アクション", "データ不足のため設定変更は推奨しません。"),
        ("6. 注意事項", "先にVersion8-Bモデル診断を実行してください。"),
    ):
        st.subheader(heading)
        st.info(message)


def _evidence_frame(report: ImprovementReport) -> pd.DataFrame:
    rows = []
    for item in report.recommendations:
        for evidence in item.evidence:
            rows.append(
                {
                    "順位": item.rank,
                    "カテゴリ": item.category,
                    "指標": evidence.metric,
                    "現在値": _diagnostic_value(
                        evidence.current_value,
                        evidence.unit,
                    ),
                    "基準値": _diagnostic_value(
                        evidence.baseline_value,
                        evidence.unit,
                    ),
                    "差": _diagnostic_value(
                        evidence.difference,
                        evidence.unit,
                        signed=True,
                    ),
                    "診断": item.diagnosis,
                    "根拠": evidence.source,
                }
            )
    return pd.DataFrame(rows)


def _ranking_frame(report: ImprovementReport) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "順位": item.rank,
                "カテゴリ": item.category,
                "改善提案": item.title,
                "推奨アクション": item.recommended_action,
                "優先度": item.priority,
                "優先度根拠": item.priority_reason,
                "信頼度": item.confidence,
                "信頼度根拠": item.confidence_reason,
            }
            for item in report.recommendations
        ]
    )


def _render_history(manager: ImprovementHistoryManager) -> None:
    st.divider()
    st.subheader("提案履歴")
    history = manager.load()
    for warning in dict.fromkeys(manager.warnings):
        st.warning(warning)
    manager.warnings.clear()
    if history.empty:
        st.info("保存済み改善提案履歴はありません。")
        return
    columns = [
        "generated_at",
        "diagnostic_id",
        "period",
        "league",
        "prediction_version",
        "match_count",
        "round_count",
        "diagnostic_status",
        "recommendation_count",
        "reoptimization_level",
        "text_mode",
    ]
    st.dataframe(history[columns], width="stretch", hide_index=True)
    st.download_button(
        "提案履歴CSV",
        data=manager.export_csv(),
        file_name="model_improvement_history.csv",
        mime="text/csv",
        key="version8c_download_history",
    )


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


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text_mode_label(mode: str) -> str:
    return {
        "template": "テンプレート",
        "ai": "制約付きAI整文",
        "mixed": "AI整文＋テンプレート",
    }.get(mode, mode)


__all__ = ["render_improvement_recommendations_tab"]
