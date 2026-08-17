"""Version8-A実戦履歴のStreamlit表示と明示的な保存操作。"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Mapping, Optional

import pandas as pd
import streamlit as st

from bet_export import bet_plan_display_frame, purchase_entry_text
from bet_optimizer import BetPlan, plan_fingerprint, target_label
from history_manager import JAPAN_TIMEZONE, TotoHistoryManager, TotoRound
from live_history import (
    LiveHistoryError,
    LiveHistoryManager,
    generate_prediction_run_id,
    restore_recommended_bet_plan,
)


_TARGET_LABELS = {
    "toto": "toto",
    "mini_a": "mini toto A",
    "mini_b": "mini toto B",
}


def render_live_history_tab(
    *,
    live_history_manager: LiveHistoryManager,
    history_manager: TotoHistoryManager,
) -> None:
    """現在の実戦予測保存と保存済みrunの閲覧・結果更新を表示する。"""

    st.subheader("Version8-A 実戦履歴")
    st.caption(
        "予測した瞬間の確率・Version・設定と買い目をrun単位で保存します。"
        "過去確率を現在モデルで再計算せず、公式結果だけを後から追記します。"
    )
    st.warning(
        "買い目の保存や購入記録は外部サービスへの購入ではありません。"
        "自動購入、設定の自動変更、自動再最適化は行いません。"
    )

    _render_save_current_prediction(live_history_manager)
    rounds = live_history_manager.load_rounds()
    matches = live_history_manager.load_matches()
    bets = live_history_manager.load_bets()
    for warning in dict.fromkeys(live_history_manager.warnings):
        st.warning(warning)
    live_history_manager.warnings.clear()

    st.divider()
    st.subheader("保存済み実戦履歴")
    if rounds.empty:
        st.info("実戦履歴はまだありません。")
        _render_downloads(live_history_manager)
        return

    summary = build_live_summary(rounds, bets)
    st.dataframe(summary, width="stretch", hide_index=True)
    _render_downloads(live_history_manager)

    run_ids = rounds["prediction_run_id"].astype(str).tolist()
    round_labels = {
        str(row["prediction_run_id"]): (
            f"第{row['round_id']}回 / {row['predicted_at']} / "
            f"{row['prediction_version']} / {str(row['prediction_run_id'])[-8:]}"
        )
        for _, row in rounds.iterrows()
    }
    if (
        "version8a_selected_run" in st.session_state
        and st.session_state["version8a_selected_run"] not in run_ids
    ):
        st.session_state["version8a_selected_run"] = run_ids[0]
    selected_run = st.selectbox(
        "開催回・予測run",
        options=run_ids,
        format_func=lambda value: round_labels.get(value, value),
        key="version8a_selected_run",
    )
    _render_run_detail(
        selected_run,
        rounds,
        matches,
        bets,
        live_history_manager=live_history_manager,
        history_manager=history_manager,
    )


def build_live_summary(rounds: pd.DataFrame, bets: pd.DataFrame) -> pd.DataFrame:
    """開催回runごとの画面用サマリーを、未評価を0へ変換せず作る。"""

    columns = (
        "開催回",
        "prediction_run_id",
        "予測日時",
        "Version",
        "対象商品",
        "購入有無",
        "実購入金額",
        "結果状態",
        "本命的中数",
        "買い目的中",
        "実払戻",
        "実収支",
        "実ROI",
    )
    if not isinstance(rounds, pd.DataFrame) or rounds.empty:
        return pd.DataFrame(columns=columns)
    bet_frame = bets if isinstance(bets, pd.DataFrame) else pd.DataFrame()
    rows = []
    for _, round_row in rounds.iterrows():
        run_id = str(round_row.get("prediction_run_id", ""))
        run_bets = (
            bet_frame.loc[bet_frame["prediction_run_id"].astype(str) == run_id]
            if not bet_frame.empty and "prediction_run_id" in bet_frame.columns
            else pd.DataFrame()
        )
        purchased = (
            run_bets.loc[run_bets["record_type"].astype(str) == "purchased"]
            if not run_bets.empty
            else pd.DataFrame()
        )
        targets = sorted(
            {
                _TARGET_LABELS.get(str(value), str(value))
                for value in run_bets.get("target", pd.Series(dtype=str))
                if str(value)
            }
        )
        amount_values = _numeric_values(purchased, "actual_purchase_amount_yen")
        return_values = _numeric_values(purchased, "actual_return_yen")
        actual_amount = sum(amount_values) if amount_values else None
        all_returns_known = bool(
            not purchased.empty
            and len(return_values) == len(purchased)
        )
        actual_return = sum(return_values) if all_returns_known else None
        actual_profit = (
            actual_return - actual_amount
            if actual_return is not None and actual_amount is not None
            else None
        )
        actual_roi = (
            actual_return / actual_amount
            if actual_return is not None and actual_amount not in (None, 0)
            else None
        )
        hit_labels = []
        for _, bet in run_bets.iterrows():
            role_label = (
                "購入" if str(bet.get("record_type")) == "purchased" else "推奨"
            )
            if _optional_bool(bet.get("all_matches_covered")) is True:
                hit_labels.append(
                    f"{role_label}{_TARGET_LABELS.get(str(bet.get('target')), str(bet.get('target')))}○"
                )
            elif _optional_bool(bet.get("all_matches_covered")) is False:
                hit_labels.append(
                    f"{role_label}{_TARGET_LABELS.get(str(bet.get('target')), str(bet.get('target')))}×"
                )
        rows.append(
            {
                "開催回": _display_round(round_row.get("round_id")),
                "prediction_run_id": run_id,
                "予測日時": str(round_row.get("predicted_at", "")),
                "Version": str(round_row.get("prediction_version", "")),
                "対象商品": " / ".join(targets) if targets else "未保存",
                "購入有無": "あり" if not purchased.empty else "なし",
                "実購入金額": _money_label(actual_amount),
                "結果状態": str(round_row.get("round_status", "")),
                "本命的中数": _hit_label(round_row.get("favorite_hit_count")),
                "買い目的中": " / ".join(hit_labels) if hit_labels else "未評価",
                "実払戻": _money_label(actual_return),
                "実収支": _money_label(actual_profit, signed=True),
                "実ROI": _ratio_label(actual_roi),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_live_detail(matches: pd.DataFrame, bets: pd.DataFrame) -> pd.DataFrame:
    """試合履歴へAI推奨・実購入の最終選択を結合する。"""

    columns = (
        "試合番号",
        "対戦カード",
        "リーグ",
        "P(1)",
        "P(0)",
        "P(2)",
        "本命",
        "引分候補",
        "実結果",
        "本命的中",
        "AI推奨買い目",
        "実購入買い目",
        "実結果カバー",
    )
    if not isinstance(matches, pd.DataFrame) or matches.empty:
        return pd.DataFrame(columns=columns)
    recommended, purchased = _selections_by_match(bets)
    rows = []
    ordered = matches.assign(
        _number=pd.to_numeric(matches["toto_match_number"], errors="coerce")
    ).sort_values("_number")
    for _, row in ordered.iterrows():
        number = int(float(row["toto_match_number"]))
        actual = _optional_outcome(row.get("actual_result"))
        bought = purchased.get(number, [])
        purchased_outcomes = {
            outcome
            for _, outcomes in bought
            for outcome in outcomes
        }
        covered = (
            "○" if actual and actual in purchased_outcomes else "×" if actual and bought else "未評価"
        )
        rows.append(
            {
                "試合番号": number,
                "対戦カード": f"{row.get('home_team', '')} vs {row.get('away_team', '')}",
                "リーグ": str(row.get("league", "")) or "不明",
                "P(1)": _percent_label(row.get("probability_1")),
                "P(0)": _percent_label(row.get("probability_0")),
                "P(2)": _percent_label(row.get("probability_2")),
                "本命": str(row.get("predicted_result", "")),
                "引分候補": "候補" if _optional_bool(row.get("draw_candidate")) else "—",
                "実結果": actual or "未確定",
                "本命的中": (
                    "○"
                    if actual and str(row.get("predicted_result", "")) == actual
                    else "×" if actual else "未評価"
                ),
                "AI推奨買い目": _selection_label(recommended.get(number, [])),
                "実購入買い目": _selection_label(bought),
                "実結果カバー": covered,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def prediction_session_fingerprint(
    result_df: pd.DataFrame,
    prediction_time: datetime,
) -> str:
    """同じ画面予測のbutton再実行だけを同一runへ束ねる。"""

    required = [
        "toto_round",
        "toto_match_number",
        "prediction_version",
        "1",
        "0",
        "2",
        "本命",
    ]
    payload = {
        "predicted_at": _jst_iso(prediction_time),
        "rows": [
            {column: _json_scalar(row.get(column)) for column in required}
            for _, row in result_df.sort_values("toto_match_number").iterrows()
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _render_save_current_prediction(manager: LiveHistoryManager) -> None:
    st.subheader("現在の予測を保存")
    result_df = st.session_state.get("latest_prediction_results")
    toto_round = st.session_state.get("latest_prediction_toto_round")
    settings = st.session_state.get("latest_prediction_settings_snapshot")
    predicted_at = st.session_state.get("latest_prediction_generated_at")
    prediction_run_id = str(
        st.session_state.get("latest_prediction_run_id", "") or ""
    )
    source_name = str(st.session_state.get("latest_prediction_source_name", ""))
    if (
        not isinstance(result_df, pd.DataFrame)
        or result_df.empty
        or not isinstance(toto_round, TotoRound)
        or not isinstance(settings, Mapping)
        or not isinstance(predicted_at, datetime)
    ):
        st.info(
            "先に「予想」タブで公式開催回の13試合を予想してください。"
            "手入力だけで開催回IDを確認できない予測は実戦履歴へ保存しません。"
        )
        return

    # デプロイ前から残るSession Stateだけを後方互換で一度補完する。新規予測は
    # app.pyで予測成立時に生成され、以後のrerunでは変更しない。
    if not prediction_run_id:
        prediction_run_id = generate_prediction_run_id(predicted_at)
        st.session_state["latest_prediction_run_id"] = prediction_run_id

    stored_ai_plan = st.session_state.get("version7c_ai_plan")
    plan_source_run_id = str(
        st.session_state.get("version7c_source_prediction_run_id", "") or ""
    )
    ai_plan = (
        stored_ai_plan
        if isinstance(stored_ai_plan, BetPlan)
        and plan_source_run_id == prediction_run_id
        and stored_ai_plan.source_prediction_run_id == prediction_run_id
        and _plan_matches_prediction_frame(stored_ai_plan, result_df)
        else None
    )
    st.caption(
        f"第{toto_round.round_id}回 / {_jst_iso(predicted_at)} / "
        f"{result_df.iloc[0].get('prediction_version', '')}"
    )
    if st.button(
        "実戦予測として保存",
        type="primary",
        width="stretch",
        key="version8a_save_prediction",
    ):
        try:
            fingerprint = prediction_session_fingerprint(result_df, predicted_at)
            if st.session_state.get("version8a_saved_prediction_fingerprint") == fingerprint:
                run_id = st.session_state.get("version8a_saved_prediction_run_id")
            else:
                run_id = prediction_run_id
            outcome = manager.save_prediction(
                result_df,
                toto_round,
                settings_snapshot=dict(settings),
                prediction_time=predicted_at,
                source_name=source_name,
                prediction_run_id=run_id,
            )
            st.session_state["version8a_saved_prediction_fingerprint"] = fingerprint
            st.session_state["version8a_saved_prediction_run_id"] = outcome.prediction_run_id
            st.session_state["version8a_active_run_id"] = outcome.prediction_run_id
            st.session_state.pop("version8a_recommendation_id", None)
            st.session_state.pop("version8a_purchase_plan_fingerprint", None)
            if isinstance(ai_plan, BetPlan):
                manual_plan = st.session_state.get("version7c_manual_plan")
                final_plan = (
                    manual_plan
                    if (
                        isinstance(manual_plan, BetPlan)
                        and manual_plan.source_prediction_run_id
                        == outcome.prediction_run_id
                        and st.session_state.get(
                            "version7c_manual_plan_source_prediction_run_id"
                        )
                        == outcome.prediction_run_id
                        and _plan_matches_prediction_frame(manual_plan, result_df)
                    )
                    else ai_plan
                )
                generated_at = st.session_state.get("version7c_plan_generated_at")
                recommendation_id = manager.save_recommended_bet(
                    outcome.prediction_run_id,
                    final_plan,
                    generated_at=(
                        generated_at if isinstance(generated_at, datetime) else predicted_at
                    ),
                )
                st.session_state["version8a_recommendation_id"] = recommendation_id
                st.session_state["version8a_purchase_plan_fingerprint"] = (
                    plan_fingerprint(final_plan)
                )
            if outcome.created:
                st.success(
                    f"実戦予測を保存しました。run ID: {outcome.prediction_run_id}"
                )
            else:
                st.info("同じ画面予測は保存済みです。重複行は追加していません。")
        except LiveHistoryError as error:
            st.error(str(error))


def _render_run_detail(
    run_id: str,
    rounds: pd.DataFrame,
    matches: pd.DataFrame,
    bets: pd.DataFrame,
    *,
    live_history_manager: LiveHistoryManager,
    history_manager: TotoHistoryManager,
) -> None:
    round_rows = rounds.loc[rounds["prediction_run_id"].astype(str) == run_id]
    if len(round_rows) != 1:
        st.error("選択した実戦予測を確認できません。")
        return
    round_row = round_rows.iloc[0]
    run_matches = matches.loc[matches["prediction_run_id"].astype(str) == run_id]
    run_bets = bets.loc[bets["prediction_run_id"].astype(str) == run_id]
    st.subheader("開催回詳細")
    detail = build_live_detail(run_matches, run_bets)
    st.dataframe(detail, width="stretch", hide_index=True)

    settings_json = str(round_row.get("settings_snapshot_json", ""))
    with st.expander("予測時点の設定スナップショット"):
        try:
            st.json(json.loads(settings_json))
        except (json.JSONDecodeError, TypeError, ValueError):
            st.error("設定スナップショットを読み込めません。")

    _render_purchase(run_id, live_history_manager)
    action_columns = st.columns(2)
    with action_columns[0]:
        if st.button(
            "公式結果を更新",
            key=f"version8a_update_results_{run_id}",
            width="stretch",
        ):
            try:
                round_id = int(float(round_row["round_id"]))
                load_result = history_manager.load_round(round_id)
                if not load_result.is_loaded or load_result.toto_round is None:
                    st.warning(load_result.message)
                else:
                    outcome = live_history_manager.update_actual_results(
                        run_id,
                        load_result.toto_round,
                        source_name=load_result.source_name,
                    )
                    st.success(
                        f"公式結果を{outcome.actual_result_count}/13試合まで確認しました。"
                        f"状態: {outcome.round_status}"
                    )
            except (LiveHistoryError, TypeError, ValueError) as error:
                st.error(str(error))
    with action_columns[1]:
        confirmed = str(round_row.get("round_status", "")) in (
            "result_confirmed",
            "evaluated",
        )
        if st.button(
            "確定結果を評価",
            key=f"version8a_evaluate_{run_id}",
            width="stretch",
            disabled=not confirmed,
        ):
            try:
                result = live_history_manager.evaluate_run(run_id)
                st.success(
                    f"本命は13試合中{result['favorite_hit_count']}試合的中しました。"
                )
            except LiveHistoryError as error:
                st.error(str(error))


def _render_purchase(
    run_id: str,
    manager: LiveHistoryManager,
) -> None:
    st.subheader("実購入の記録")
    run_bets = manager.load_bets(run_id)
    run_matches = manager.load_matches(run_id)
    recommended_rows = run_bets.loc[
        run_bets["record_type"].astype(str) == "recommended"
    ]
    restored_candidates: list[tuple[dict[str, Any], BetPlan]] = []
    for _, row in recommended_rows.iloc[::-1].iterrows():
        try:
            restored = restore_recommended_bet_plan(
                run_id,
                row.to_dict(),
                run_matches,
            )
            restored_candidates.append((row.to_dict(), restored))
        except LiveHistoryError as error:
            st.warning(f"保存済みAI推奨買い目を購入候補にできません: {error}")

    active_run_id = st.session_state.get("version8a_active_run_id")
    session_plan = st.session_state.get("version7c_manual_plan")
    source_run_id = st.session_state.get(
        "version7c_manual_plan_source_prediction_run_id"
    )
    expected_plan_fingerprint = st.session_state.get(
        "version8a_purchase_plan_fingerprint"
    )
    session_candidate: Optional[tuple[dict[str, Any], BetPlan]] = None
    if (
        active_run_id == run_id
        and isinstance(session_plan, BetPlan)
        and session_plan.source_prediction_run_id == run_id
        and source_run_id == run_id
        and expected_plan_fingerprint == plan_fingerprint(session_plan)
    ):
        session_candidate = next(
            (
                candidate
                for candidate in restored_candidates
                if plan_fingerprint(candidate[1]) == plan_fingerprint(session_plan)
            ),
            None,
        )

    candidate = session_candidate
    recovered_from_history = False
    if candidate is None and len(restored_candidates) == 1:
        candidate = restored_candidates[0]
        recovered_from_history = True
    elif candidate is None and len(restored_candidates) > 1:
        candidate_by_id = {
            str(row["bet_record_id"]): (row, plan)
            for row, plan in restored_candidates
        }
        recommendation_id = st.selectbox(
            "購入候補（同じprediction_run_idのAI推奨）",
            options=list(candidate_by_id),
            format_func=lambda value: _recommendation_option_label(
                candidate_by_id[value][0]
            ),
            key=f"version8a_purchase_recommendation_{run_id}",
        )
        candidate = candidate_by_id[recommendation_id]
        recovered_from_history = True

    if candidate is None:
        st.info(
            "同じprediction_run_id・開催回で保存されたAI推奨買い目がないため、"
            "このrunの実購入は登録できません。履歴から自動購入することはありません。"
        )
        return
    recommendation_row, final_plan = candidate
    recommendation_id = str(recommendation_row["bet_record_id"])
    if recovered_from_history:
        st.info(
            "Session Stateの最終買い目に依存せず、同じprediction_run_idで保存した"
            "AI推奨買い目を購入候補として復元しました。"
        )

    summary_columns = st.columns(4)
    summary_columns[0].metric("商品種別", target_label(final_plan.target))
    summary_columns[1].metric("購入口数", f"{final_plan.ticket_count:,}口")
    summary_columns[2].metric(
        "購入予定金額", f"{final_plan.purchase_amount_yen:,}円"
    )
    summary_columns[3].metric(
        "Coverage", f"{final_plan.estimated_full_coverage * 100.0:.2f}%"
    )
    st.caption(
        "Coverageは保存時点のモデル確率から計算した参考指標であり、"
        "実際の当選確率ではありません。"
    )
    st.dataframe(
        bet_plan_display_frame(final_plan),
        width="stretch",
        hide_index=True,
    )
    with st.expander("買い目内容（転記用）"):
        st.code(purchase_entry_text(final_plan), language=None)

    purchased_rows = run_bets.loc[
        (run_bets["record_type"].astype(str) == "purchased")
        & (run_bets["target"].astype(str) == final_plan.target)
    ]
    plan_signature = _plan_selection_signature(final_plan)
    already_purchased = next(
        (
            row
            for _, row in purchased_rows.iterrows()
            if (
                str(row.get("source_recommendation_id", ""))
                == recommendation_id
                or _history_selection_signature(row) == plan_signature
            )
        ),
        None,
    )
    if already_purchased is not None:
        st.success(
            "実購入登録済みです。買い目ID: "
            f"{already_purchased.get('bet_record_id', '')}"
        )
        return

    amount_key = f"version8a_actual_amount_{run_id}_{plan_fingerprint(final_plan)}"
    if amount_key in st.session_state:
        value = _finite_nonnegative_int(
            st.session_state[amount_key], final_plan.purchase_amount_yen
        )
        st.session_state[amount_key] = value
    amount_options: dict[str, Any] = {
        "label": "実購入金額（円）",
        "min_value": 0,
        "step": 100,
        "key": amount_key,
    }
    if amount_key not in st.session_state:
        amount_options["value"] = final_plan.purchase_amount_yen
    amount = int(st.number_input(**amount_options))
    st.caption(
        "AI推奨ではなく、手動変更を反映した「最終買い目」を保存します。"
    )
    if st.button(
        "この買い目を実際に購入したとして記録",
        key=f"version8a_purchase_{run_id}",
        width="stretch",
    ):
        try:
            purchase_key = hashlib.sha256(
                f"{run_id}|{plan_fingerprint(final_plan)}|{amount}".encode("utf-8")
            ).hexdigest()
            time_key = f"version8a_purchase_time_{purchase_key}"
            purchased_at = st.session_state.get(time_key)
            if not isinstance(purchased_at, datetime):
                purchased_at = datetime.now(JAPAN_TIMEZONE)
                st.session_state[time_key] = purchased_at
            bet_id = manager.record_purchase(
                run_id,
                final_plan,
                actual_purchase_amount_yen=amount,
                purchased_at=purchased_at,
                source_recommendation_id=recommendation_id,
            )
            st.success(f"実購入記録を保存しました。買い目ID: {bet_id}")
        except LiveHistoryError as error:
            st.error(str(error))


def _recommendation_option_label(row: Mapping[str, Any]) -> str:
    tickets = _finite_nonnegative_int(row.get("ticket_count"), 0)
    amount = _finite_nonnegative_int(row.get("planned_purchase_amount_yen"), 0)
    return (
        f"{_TARGET_LABELS.get(str(row.get('target')), str(row.get('target')))} / "
        f"{tickets:,}口 / {amount:,}円 / "
        f"{row.get('generated_at', '')}"
    )


def _plan_selection_signature(
    plan: BetPlan,
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    return tuple(
        sorted(
            (
                recommendation.analysis.prediction.source_match_number,
                tuple(recommendation.outcomes),
            )
            for recommendation in plan.recommendations
        )
    )


def _history_selection_signature(
    row: Mapping[str, Any],
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    try:
        selections = json.loads(str(row.get("selections_json", "")))
    except (json.JSONDecodeError, TypeError, ValueError):
        return ()
    if not isinstance(selections, list):
        return ()
    normalized = []
    for selection in selections:
        if not isinstance(selection, Mapping):
            return ()
        try:
            number = int(selection.get("source_match_number"))
        except (TypeError, ValueError):
            return ()
        raw_outcomes = selection.get("outcomes", ())
        if isinstance(raw_outcomes, str) or not isinstance(raw_outcomes, list):
            return ()
        outcomes = tuple(
            outcome
            for outcome in ("1", "0", "2")
            if outcome in {str(value) for value in raw_outcomes}
        )
        if not outcomes or len(outcomes) != len(raw_outcomes):
            return ()
        normalized.append((number, outcomes))
    return tuple(sorted(normalized))


def _render_downloads(manager: LiveHistoryManager) -> None:
    columns = st.columns(3)
    columns[0].download_button(
        "開催回サマリーCSV",
        data=manager.export_rounds_csv(),
        file_name="version8a_live_round_history.csv",
        key="version8a_download_rounds",
    )
    columns[1].download_button(
        "試合単位履歴CSV",
        data=manager.export_matches_csv(),
        file_name="version8a_live_match_history.csv",
        key="version8a_download_matches",
    )
    columns[2].download_button(
        "買い目履歴CSV",
        data=manager.export_bets_csv(),
        file_name="version8a_live_bet_history.csv",
        key="version8a_download_bets",
    )


def _selections_by_match(
    bets: pd.DataFrame,
) -> tuple[dict[int, list[tuple[str, tuple[str, ...]]]], dict[int, list[tuple[str, tuple[str, ...]]]]]:
    recommended: dict[int, list[tuple[str, tuple[str, ...]]]] = {}
    purchased: dict[int, list[tuple[str, tuple[str, ...]]]] = {}
    if not isinstance(bets, pd.DataFrame) or bets.empty:
        return recommended, purchased
    ordered = bets.sort_values("generated_at")
    # 同じ商品を再保存した場合は最新の1件だけを詳細表示に使う。
    latest_by_role_target = {
        (str(row.get("record_type")), str(row.get("target"))): row
        for _, row in ordered.iterrows()
    }
    for (role, target), row in latest_by_role_target.items():
        try:
            selections = json.loads(str(row.get("selections_json", "")))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        destination = recommended if role == "recommended" else purchased
        for selection in selections if isinstance(selections, list) else ():
            if not isinstance(selection, Mapping):
                continue
            try:
                number = int(selection.get("source_match_number"))
            except (TypeError, ValueError):
                continue
            outcomes = tuple(
                str(value)
                for value in selection.get("outcomes", ())
                if str(value) in ("1", "0", "2")
            )
            destination.setdefault(number, []).append((target, outcomes))
    return recommended, purchased


def _plan_matches_prediction_frame(plan: BetPlan, frame: pd.DataFrame) -> bool:
    """古いSession StateのPlanを別予測runへ誤保存しない。"""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return False
    number_column = (
        "toto_match_number" if "toto_match_number" in frame.columns else "試合"
    )
    try:
        rows = {
            int(float(row.get(number_column))): row
            for _, row in frame.iterrows()
        }
        for item in plan.recommendations:
            prediction = item.analysis.prediction
            row = rows[prediction.source_match_number]
            values = []
            for outcome in ("1", "0", "2"):
                number = float(row.get(outcome))
                values.append(number / 100.0 if number > 1.0 else number)
            if any(
                not math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)
                for left, right in zip(
                    values,
                    (
                        prediction.probability_1,
                        prediction.probability_0,
                        prediction.probability_2,
                    ),
                )
            ):
                return False
            card = str(row.get("対戦カード", ""))
            home, separator, away = card.partition(" vs ")
            if separator and (
                home != prediction.home_team or away != prediction.away_team
            ):
                return False
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return True


def _selection_label(items: list[tuple[str, tuple[str, ...]]]) -> str:
    if not items:
        return "未保存"
    return " / ".join(
        f"{_TARGET_LABELS.get(target, target)}:{'・'.join(outcomes)}"
        for target, outcomes in items
    )


def _numeric_values(frame: pd.DataFrame, column: str) -> list[float]:
    if frame.empty or column not in frame.columns:
        return []
    numeric = pd.to_numeric(frame[column], errors="coerce")
    return [float(value) for value in numeric if pd.notna(value) and math.isfinite(value)]


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


def _optional_outcome(value: Any) -> str:
    text = str(value or "").strip()
    if text in ("1", "0", "2"):
        return text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return str(int(number)) if number.is_integer() and str(int(number)) in ("1", "0", "2") else ""


def _display_round(value: Any) -> str:
    try:
        return f"第{int(float(value))}回"
    except (TypeError, ValueError):
        return "不明"


def _money_label(value: Optional[float], *, signed: bool = False) -> str:
    if value is None or not math.isfinite(float(value)):
        return "N/A"
    prefix = "+" if signed and float(value) > 0 else ""
    return f"{prefix}{int(value):,}円"


def _ratio_label(value: Optional[float]) -> str:
    if value is None or not math.isfinite(float(value)):
        return "N/A"
    return f"{float(value):.2%}"


def _hit_label(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "未評価"
    if not math.isfinite(number):
        return "未評価"
    return f"{int(number)}/13"


def _percent_label(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(number):
        return "N/A"
    return f"{number:.1%}"


def _finite_nonnegative_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return int(default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return int(default)
    if not math.isfinite(number) or number < 0:
        return int(default)
    return int(number)


def _jst_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=JAPAN_TIMEZONE)
    return value.astimezone(JAPAN_TIMEZONE).isoformat()


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        return str(value)
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return str(value)


__all__ = (
    "build_live_detail",
    "build_live_summary",
    "prediction_session_fingerprint",
    "render_live_history_tab",
)
