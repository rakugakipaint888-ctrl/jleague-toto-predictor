"""Version8-Aの実戦履歴について不変性・結果更新・ROIを確認する。"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from bet_optimizer import (
    apply_manual_selections,
    build_match_predictions,
    optimize_bet_plan,
    plan_fingerprint,
)
from draw_predictor import probability_percentages
from history_manager import JAPAN_TIMEZONE, TotoMatch, TotoPayouts, TotoRound
from live_history import (
    BET_COLUMNS,
    MATCH_COLUMNS,
    ROUND_COLUMNS,
    LiveHistoryConflictError,
    LiveHistoryManager,
    LiveHistoryStorageError,
    LiveHistoryValidationError,
    generate_prediction_run_id,
    restore_recommended_bet_plan,
)
from live_history_ui import build_live_detail, build_live_summary


PREDICTION_TIME = datetime(2026, 8, 16, 10, 30, tzinfo=JAPAN_TIMEZONE)


def prediction_frame() -> pd.DataFrame:
    patterns = (
        (70.0, 18.0, 12.0),
        (38.0, 35.0, 27.0),
        (35.0, 33.0, 32.0),
        (50.0, 10.0, 40.0),
        (45.0, 35.0, 20.0),
    )
    rows = []
    order = ("1", "0", "2")
    for number in range(1, 14):
        p1, p0, p2 = patterns[(number - 1) % len(patterns)]
        probabilities = {"1": p1, "0": p0, "2": p2}
        predicted = max(order, key=lambda outcome: probabilities[outcome])
        rows.append(
            {
                "toto_round": 2001,
                "toto_match_number": number,
                "prediction_version": "Version7-B",
                "league": ("J1", "J2", "J3")[(number - 1) % 3],
                "1": p1,
                "0": p0,
                "2": p2,
                "本命": predicted,
                "予想スコア": "2−1",
                "対戦カード": f"ホーム{number} vs アウェイ{number}",
                "draw_candidate": p0 >= 30.0,
                "draw_candidate_threshold": 0.25,
                "draw_candidate_reasons": (
                    "引分確率が設定閾値以上" if p0 >= 30.0 else ""
                ),
                "version7b_home_elo": 1520.0 + number,
                "version7b_away_elo": 1490.0 + number,
                "version7b_elo_difference": 30.0,
                "home_expected_after_version7b": 1.6,
                "away_expected_after_version7b": 1.1,
            }
        )
    return pd.DataFrame(rows)


def toto_round(
    actuals: dict[int, str] | None = None,
    *,
    payouts: TotoPayouts | None = None,
) -> TotoRound:
    actuals = actuals or {}
    base = datetime(2026, 8, 22, 14, 0, tzinfo=JAPAN_TIMEZONE)
    matches = []
    for number in range(1, 14):
        actual = actuals.get(number)
        score = {
            "1": (2, 1),
            "0": (1, 1),
            "2": (0, 1),
        }.get(actual, (None, None))
        matches.append(
            TotoMatch(
                round_id=2001,
                match_number=number,
                home_team=f"ホーム{number}",
                away_team=f"アウェイ{number}",
                match_time=base + timedelta(hours=number),
                actual_result=actual,
                home_goals=score[0],
                away_goals=score[1],
            )
        )
    return TotoRound(
        round_id=2001,
        matches=tuple(matches),
        payouts=payouts or TotoPayouts(),
        source_url="https://example.invalid/official",
    )


class LiveHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.manager = LiveHistoryManager(
            round_path=root / "live_round_history.csv",
            match_path=root / "live_match_history.csv",
            bet_path=root / "live_bet_history.csv",
        )
        self.frame = prediction_frame()
        self.round = toto_round()
        self.settings = {
            "schema_version": 1,
            "prediction_version": "Version7-B",
            "model_parameters": {"home_correction": 1.08},
            "draw_parameters": {"candidate_threshold": 0.25},
            "model_options": {"use_elo": True},
            "optimization_reference": {
                "run_id": "model-run-1",
                "best_trial": 4,
                "best_score": 0.8123,
            },
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def save(self, run_id: str | None = None):
        return self.manager.save_prediction(
            self.frame,
            self.round,
            settings_snapshot=self.settings,
            prediction_time=PREDICTION_TIME,
            source_name="toto公式",
            prediction_run_id=run_id,
        )

    def plan(self, target: str = "toto", doubles: int = 3, triples: int = 0):
        return optimize_bet_plan(
            build_match_predictions(self.frame, target),
            target=target,
            double_count=doubles,
            triple_count=triples,
        )

    def test_prediction_run_saves_exactly_thirteen_immutable_matches(self) -> None:
        outcome = self.save()
        self.assertTrue(outcome.created)
        self.assertTrue(outcome.prediction_run_id.startswith("run_20260816T103000"))
        rounds = self.manager.load_rounds()
        matches = self.manager.load_matches(outcome.prediction_run_id)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(len(matches), 13)
        self.assertEqual(set(int(value) for value in matches["toto_match_number"]), set(range(1, 14)))
        self.assertEqual(set(matches["prediction_version"]), {"Version7-B"})
        self.assertEqual(set(matches["league"]), {"J1", "J2", "J3"})
        self.assertEqual(rounds.iloc[0]["round_status"], "predicted")
        self.assertEqual(rounds.iloc[0]["optimization_run_id"], "model-run-1")
        snapshot = json.loads(rounds.iloc[0]["settings_snapshot_json"])
        self.assertEqual(snapshot, self.settings)
        for _, row in matches.iterrows():
            self.assertAlmostEqual(
                sum(float(row[column]) for column in ("probability_1", "probability_0", "probability_2")),
                1.0,
                delta=1e-12,
            )
            self.assertEqual(row["actual_result"], "")
            self.assertEqual(row["draw_probability"], row["probability_0"])
            self.assertEqual(row["draw_confidence"], "")

    def test_duplicate_button_save_is_idempotent_but_explicit_reprediction_is_new(self) -> None:
        run_id = generate_prediction_run_id(PREDICTION_TIME)
        first = self.save(run_id)
        second = self.save(run_id)
        third = self.save()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertTrue(third.created)
        self.assertNotEqual(first.prediction_run_id, third.prediction_run_id)
        self.assertEqual(len(self.manager.load_rounds()), 2)
        self.assertEqual(len(self.manager.load_matches()), 26)

    def test_full_precision_history_accepts_its_display_plan_and_rejects_another_run(self) -> None:
        """最大剰余丸め差は許容し、同一開催回の別runはIDで拒否する。"""

        frame = self.frame.copy()
        for index, row in frame.iterrows():
            full = (
                {"1": 0.33334, "0": 0.33333, "2": 0.33333}
                if index == 0
                else {
                    outcome: float(row[outcome]) / 100.0
                    for outcome in ("1", "0", "2")
                }
            )
            displayed = probability_percentages(full)
            for outcome in ("1", "0", "2"):
                frame.at[index, outcome] = displayed[outcome]
                frame.at[index, f"live_probability_{outcome}"] = full[outcome]

        # 33.334%は最大剰余法で33.4%となり、旧0.05pt許容を超えていた。
        self.assertGreater(
            abs(
                float(frame.at[0, "1"]) / 100.0
                - float(frame.at[0, "live_probability_1"])
            ),
            0.0005,
        )
        first_run_id = generate_prediction_run_id(PREDICTION_TIME)
        second_run_id = generate_prediction_run_id(PREDICTION_TIME)
        self.manager.save_prediction(
            frame,
            self.round,
            settings_snapshot=self.settings,
            prediction_time=PREDICTION_TIME,
            source_name="toto公式",
            prediction_run_id=first_run_id,
        )
        self.manager.save_prediction(
            frame,
            self.round,
            settings_snapshot=self.settings,
            prediction_time=PREDICTION_TIME,
            source_name="toto公式",
            prediction_run_id=second_run_id,
        )
        plan = optimize_bet_plan(
            build_match_predictions(frame, "toto"),
            target="toto",
            double_count=5,
            triple_count=0,
            source_prediction_run_id=first_run_id,
        )

        recommendation_id = self.manager.save_recommended_bet(
            first_run_id,
            plan,
            generated_at=PREDICTION_TIME,
        )
        self.assertTrue(recommendation_id.startswith("bet_recommended_"))
        with self.assertRaisesRegex(
            LiveHistoryValidationError,
            "買い目は別の予測runから生成されています",
        ):
            self.manager.save_recommended_bet(
                second_run_id,
                plan,
                generated_at=PREDICTION_TIME,
            )
        self.assertEqual(len(self.manager.load_bets(first_run_id)), 1)
        self.assertTrue(self.manager.load_bets(second_run_id).empty)

    def test_recommended_plan_restores_without_session_state_and_keeps_run_boundary(self) -> None:
        run_id = self.save().prediction_run_id
        plan = optimize_bet_plan(
            build_match_predictions(self.frame, "toto"),
            target="toto",
            double_count=5,
            triple_count=0,
            source_prediction_run_id=run_id,
        )
        recommendation_id = self.manager.save_recommended_bet(
            run_id,
            plan,
            generated_at=PREDICTION_TIME,
        )
        recommended = self.manager.load_bets(run_id).iloc[0].to_dict()

        restored = restore_recommended_bet_plan(
            run_id,
            recommended,
            self.manager.load_matches(run_id),
        )

        self.assertEqual(recommended["bet_record_id"], recommendation_id)
        self.assertEqual(restored.source_prediction_run_id, run_id)
        self.assertEqual(plan_fingerprint(restored), plan_fingerprint(plan))
        self.assertEqual(restored.ticket_count, 32)
        self.assertEqual(restored.purchase_amount_yen, 3_200)

        another_run_id = self.save().prediction_run_id
        with self.assertRaisesRegex(
            LiveHistoryValidationError,
            "prediction_run_id",
        ):
            restore_recommended_bet_plan(
                another_run_id,
                recommended,
                self.manager.load_matches(another_run_id),
            )

    def test_same_run_same_purchase_is_idempotent_across_restart_time(self) -> None:
        run_id = self.save().prediction_run_id
        plan = optimize_bet_plan(
            build_match_predictions(self.frame, "toto"),
            target="toto",
            double_count=5,
            triple_count=0,
            source_prediction_run_id=run_id,
        )
        recommendation_id = self.manager.save_recommended_bet(
            run_id,
            plan,
            generated_at=PREDICTION_TIME,
        )
        first = self.manager.record_purchase(
            run_id,
            plan,
            actual_purchase_amount_yen=3_200,
            purchased_at=PREDICTION_TIME + timedelta(minutes=1),
            source_recommendation_id=recommendation_id,
        )
        second = self.manager.record_purchase(
            run_id,
            plan,
            actual_purchase_amount_yen=3_200,
            purchased_at=PREDICTION_TIME + timedelta(hours=1),
            source_recommendation_id=recommendation_id,
        )

        self.assertEqual(second, first)
        bets = self.manager.load_bets(run_id)
        self.assertEqual(
            int((bets["record_type"].astype(str) == "purchased").sum()),
            1,
        )

    def test_same_run_with_changed_probability_is_rejected(self) -> None:
        run_id = self.save().prediction_run_id
        changed = self.frame.copy()
        changed.at[0, "1"] = 69.0
        changed.at[0, "2"] = 13.0
        with self.assertRaises(LiveHistoryConflictError):
            self.manager.save_prediction(
                changed,
                self.round,
                settings_snapshot=self.settings,
                prediction_time=PREDICTION_TIME,
                source_name="toto公式",
                prediction_run_id=run_id,
            )

    def test_tampered_probability_hash_blocks_result_update(self) -> None:
        run_id = self.save().prediction_run_id
        frame = pd.read_csv(
            self.manager.match_path,
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
        )
        frame.at[0, "probability_1"] = "0.99"
        frame.to_csv(
            self.manager.match_path,
            index=False,
            encoding="utf-8-sig",
        )
        self.assertEqual(len(self.manager.load_matches(run_id)), 12)
        with self.assertRaises(LiveHistoryConflictError):
            self.manager.update_actual_results(
                run_id,
                toto_round({1: "1"}),
                source_name="toto公式",
            )

    def test_partial_then_complete_result_update_never_changes_probabilities(self) -> None:
        run_id = self.save().prediction_run_id
        before = self.manager.load_matches(run_id)[
            ["probability_1", "probability_0", "probability_2", "immutable_hash"]
        ].copy()
        partial_actuals = {number: ("1", "0", "2")[(number - 1) % 3] for number in range(1, 6)}
        partial = self.manager.update_actual_results(
            run_id,
            toto_round(partial_actuals),
            source_name="保存CSV",
        )
        self.assertEqual(partial.actual_result_count, 5)
        self.assertEqual(partial.round_status, "pending_result")
        complete_actuals = {number: ("1", "0", "2")[(number - 1) % 3] for number in range(1, 14)}
        complete = self.manager.update_actual_results(
            run_id,
            toto_round(complete_actuals),
            source_name="toto公式",
        )
        self.assertEqual(complete.actual_result_count, 13)
        self.assertEqual(complete.round_status, "result_confirmed")
        after = self.manager.load_matches(run_id)
        pd.testing.assert_frame_equal(
            before.reset_index(drop=True),
            after[["probability_1", "probability_0", "probability_2", "immutable_hash"]].reset_index(drop=True),
        )
        self.assertEqual(set(after["actual_result"]), {"1", "0", "2"})

    def test_untrusted_or_conflicting_actual_result_is_rejected(self) -> None:
        run_id = self.save().prediction_run_id
        with self.assertRaises(LiveHistoryValidationError):
            self.manager.update_actual_results(
                run_id,
                toto_round({1: "1"}),
                source_name="現在データ",
            )
        self.manager.update_actual_results(
            run_id,
            toto_round({1: "1"}),
            source_name="toto公式",
        )
        with self.assertRaises(LiveHistoryConflictError):
            self.manager.update_actual_results(
                run_id,
                toto_round({1: "2"}),
                source_name="toto公式",
            )

    def test_recommended_and_purchased_are_separate_and_manual_plan_is_kept(self) -> None:
        run_id = self.save().prediction_run_id
        ai_plan = self.plan()
        recommendation_id = self.manager.save_recommended_bet(
            run_id, ai_plan, generated_at=PREDICTION_TIME
        )
        manual = apply_manual_selections(
            ai_plan,
            {
                item.analysis.prediction.match_number: (
                    ("1", "0", "2")
                    if item.analysis.prediction.match_number == 1
                    else item.outcomes
                )
                for item in ai_plan.recommendations
            },
        )
        purchase_id = self.manager.record_purchase(
            run_id,
            manual,
            actual_purchase_amount_yen=2500,
            purchased_at=PREDICTION_TIME + timedelta(minutes=10),
            source_recommendation_id=recommendation_id,
        )
        bets = self.manager.load_bets(run_id)
        self.assertEqual(len(bets), 2)
        recommended = bets.loc[bets["bet_record_id"] == recommendation_id].iloc[0]
        purchased = bets.loc[bets["bet_record_id"] == purchase_id].iloc[0]
        self.assertEqual((recommended["recommended"], recommended["purchased"]), ("True", "False"))
        self.assertEqual((purchased["recommended"], purchased["purchased"]), ("False", "True"))
        self.assertEqual(purchased["actual_purchase_amount_yen"], "2500")
        self.assertEqual(purchased["source_recommendation_id"], recommendation_id)
        self.assertGreaterEqual(int(recommended["draw_included_match_count"]), 0)
        self.assertGreaterEqual(int(recommended["draw_included_ticket_count"]), 0)
        self.assertIsInstance(json.loads(recommended["draw_inclusion_json"]), list)
        selections = json.loads(purchased["selections_json"])
        self.assertEqual(selections[0]["outcomes"], ["1", "0", "2"])
        rounds = self.manager.load_rounds()
        self.assertEqual(rounds.iloc[0]["purchased"], "True")
        self.assertEqual(rounds.iloc[0]["round_status"], "purchased")

    def test_evaluation_separates_simulation_and_actual_roi(self) -> None:
        run_id = self.save().prediction_run_id
        plan = self.plan(target="toto", doubles=2, triples=1)
        self.manager.save_recommended_bet(run_id, plan, generated_at=PREDICTION_TIME)
        self.manager.record_purchase(
            run_id,
            plan,
            actual_purchase_amount_yen=1500,
            purchased_at=PREDICTION_TIME + timedelta(minutes=5),
        )
        actuals = {
            item.analysis.prediction.source_match_number: item.outcomes[0]
            for item in plan.recommendations
        }
        completed_round = toto_round(
            actuals,
            payouts=TotoPayouts(
                first_prize_yen=10_000_000,
                second_prize_yen=100_000,
                third_prize_yen=10_000,
            ),
        )
        self.manager.update_actual_results(
            run_id, completed_round, source_name="toto公式"
        )
        evaluation = self.manager.evaluate_run(run_id)
        self.assertIn(evaluation["favorite_hit_count"], range(14))
        bets = self.manager.load_bets(run_id)
        recommended = bets.loc[bets["record_type"] == "recommended"].iloc[0]
        purchased = bets.loc[bets["record_type"] == "purchased"].iloc[0]
        self.assertNotEqual(recommended["simulation_return_yen"], "")
        self.assertEqual(recommended["actual_return_yen"], "")
        self.assertEqual(purchased["simulation_return_yen"], "")
        self.assertNotEqual(purchased["actual_return_yen"], "")
        self.assertAlmostEqual(
            float(purchased["actual_roi"]),
            float(purchased["actual_return_yen"]) / 1500,
            delta=1e-12,
        )
        self.assertEqual(self.manager.load_rounds().iloc[0]["round_status"], "evaluated")

    def test_unpurchased_or_pending_run_has_no_actual_roi(self) -> None:
        run_id = self.save().prediction_run_id
        self.manager.save_recommended_bet(run_id, self.plan(), generated_at=PREDICTION_TIME)
        bets = self.manager.load_bets(run_id)
        self.assertEqual(bets.iloc[0]["actual_roi"], "")
        with self.assertRaises(LiveHistoryValidationError):
            self.manager.evaluate_run(run_id)
        self.assertEqual(self.manager.load_bets(run_id).iloc[0]["actual_roi"], "")

    def test_mini_a_and_b_hit_evaluation_does_not_invent_payout(self) -> None:
        run_id = self.save().prediction_run_id
        plans = {
            target: self.plan(target=target, doubles=1, triples=0)
            for target in ("mini_a", "mini_b")
        }
        actuals = {number: "1" for number in range(1, 14)}
        for plan in plans.values():
            for item in plan.recommendations:
                actuals[item.analysis.prediction.source_match_number] = item.outcomes[0]
            self.manager.save_recommended_bet(
                run_id, plan, generated_at=PREDICTION_TIME
            )
        self.manager.update_actual_results(
            run_id, toto_round(actuals), source_name="toto公式"
        )
        self.manager.evaluate_run(run_id)
        bets = self.manager.load_bets(run_id)
        self.assertEqual(set(bets["target"]), {"mini_a", "mini_b"})
        self.assertEqual(set(bets["all_matches_covered"]), {"True"})
        self.assertEqual(set(bets["simulation_return_yen"]), {""})
        self.assertEqual(set(bets["simulation_roi"]), {""})

    def test_corrupt_csv_is_read_safely_and_never_overwritten_on_save(self) -> None:
        self.manager.round_path.parent.mkdir(parents=True, exist_ok=True)
        self.manager.round_path.write_text('"unterminated', encoding="utf-8")
        self.assertTrue(self.manager.load_rounds().empty)
        self.assertTrue(self.manager.warnings)
        original = self.manager.round_path.read_text(encoding="utf-8")
        with self.assertRaises(LiveHistoryStorageError):
            self.save()
        self.assertEqual(self.manager.round_path.read_text(encoding="utf-8"), original)

    def test_invalid_nan_infinity_run_id_and_probability_are_rejected(self) -> None:
        for bad in (None, "bad", "run_1"):
            with self.subTest(run_id=bad):
                if bad is None:
                    continue
                with self.assertRaises(LiveHistoryValidationError):
                    self.save(str(bad))
        for value in (None, float("nan"), float("inf")):
            with self.subTest(value=value):
                frame = self.frame.copy()
                frame.at[0, "1"] = value
                with self.assertRaises(LiveHistoryValidationError):
                    self.manager.save_prediction(
                        frame,
                        self.round,
                        settings_snapshot=self.settings,
                        prediction_time=PREDICTION_TIME,
                        source_name="toto公式",
                    )
        with self.assertRaises(LiveHistoryValidationError):
            self.manager.save_prediction(
                self.frame,
                self.round,
                settings_snapshot={"invalid": float("nan")},
                prediction_time=PREDICTION_TIME,
                source_name="toto公式",
            )

    def test_missing_and_empty_csv_load_without_crashing(self) -> None:
        self.assertTrue(self.manager.load_rounds().empty)
        self.manager.round_path.parent.mkdir(parents=True, exist_ok=True)
        self.manager.round_path.write_text("", encoding="utf-8")
        self.assertTrue(self.manager.load_rounds().empty)
        self.assertTrue(self.manager.warnings)

    def test_csv_exports_and_ui_summary_detail_keep_na(self) -> None:
        run_id = self.save().prediction_run_id
        plan = self.plan()
        self.manager.save_recommended_bet(run_id, plan, generated_at=PREDICTION_TIME)
        for payload, columns in (
            (self.manager.export_rounds_csv(), ROUND_COLUMNS),
            (self.manager.export_matches_csv(), MATCH_COLUMNS),
            (self.manager.export_bets_csv(), BET_COLUMNS),
        ):
            self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
            decoded = payload.decode("utf-8-sig")
            self.assertEqual(decoded.splitlines()[0].split(","), list(columns))
        rounds = self.manager.load_rounds()
        matches = self.manager.load_matches(run_id)
        bets = self.manager.load_bets(run_id)
        summary = build_live_summary(rounds, bets)
        detail = build_live_detail(matches, bets)
        self.assertEqual(summary.iloc[0]["実ROI"], "N/A")
        self.assertEqual(summary.iloc[0]["本命的中数"], "未評価")
        self.assertEqual(len(detail), 13)
        self.assertEqual(set(detail["実結果"]), {"未確定"})
        self.assertTrue(any(value != "未保存" for value in detail["AI推奨買い目"]))


if __name__ == "__main__":
    unittest.main()
