# Version8-A 実戦履歴データ契約

## 目的と境界

Version8-Aは、通常予想を実行した時点の予測、設定、Version7-C買い目を
`prediction_run_id`で固定し、後日確認できた公式結果だけを追記する保存基盤です。
Version8-Bの診断、Version8-Cの改善提案、自動再最適化、自動設定変更、自動購入は
実装しません。

既存の`data/history/prediction_history.csv`はVersion別バックテスト履歴で、同じ
開催回・試合・Versionを最新行へ置換する契約です。複数回の実戦予測を永久保持する
契約とは異なるため、Version8-Aは既存ファイルを移行・上書きせず、次の3ファイルへ
分離します。すべて実行時データでありGit管理対象外です。

| 階層 | 保存先 | 1行の単位 | 主キー |
|---|---|---|---|
| 開催回run | `data/history/live_round_history.csv` | 1回の明示的予測保存 | `prediction_run_id` |
| 試合 | `data/history/live_match_history.csv` | 1run・1試合 | `prediction_run_id, toto_match_number` |
| 買い目 | `data/history/live_bet_history.csv` | 1run・1商品・1推奨/購入記録 | `bet_record_id` |

各ファイルは`schema_version=1`です。空・欠損・破損・列不足は画面へ理由を表示し、
破損ファイルを空データで上書きしません。保存は同じdirectoryの一時ファイルへ全体を
書き、`fsync`後に`os.replace`します。既存履歴形式を変更しないためVersion8-Aでの
Migrationと事前バックアップは不要です。

## IDと重複制御

`prediction_run_id`は次の形式です。

```text
run_YYYYMMDDTHHMMSSffffff_<UUID4の32桁hex>
```

画面の同じ予測に対する再実行・ボタン連打では、予測日時と13試合の開催回、試合番号、
Version、表示確率、本命から作るfingerprintに対応する同じrun IDを再利用します。同じ
run IDと同じ不変hashならidempotentに成功し、行を追加しません。同じrun IDに異なる
予測が渡された場合は競合エラーです。「13試合を予想する」を明示的に再実行した場合は
新しい予測日時とUUIDを持つ別runになります。

買い目IDはrun、商品、recommended/purchased、最終選択、購入日時、実購入金額から
決定します。同じ画面操作の再実行は同じ購入日時をSession Stateで再利用するため、
同じ買い目を重複保存しません。

## 開催回runスキーマ

| 区分 | 項目 |
|---|---|
| 必須・不変 | `schema_version`, `prediction_run_id`, `round_id`, `prediction_version`, `predicted_at`, `settings_snapshot_json`, `prediction_match_count`, `season`, `round_start_at`, `round_end_at`, `source_name`, `immutable_hash` |
| 任意・不変 | `optimization_run_id`, `best_trial`, `best_score` |
| 状態 | `saved_at`, `round_status`, `purchased`, `purchased_at`, `result_confirmed_at`, `evaluated_at`, `actual_result_count` |
| 評価 | `favorite_hit_count`, `favorite_hit_count_1`, `favorite_hit_count_0`, `favorite_hit_count_2` |
| 買い目集計 | `recommended_bet_count`, `purchased_bet_count` |
| 公式払戻 | `first_prize_yen`, `second_prize_yen`, `third_prize_yen` |

`round_status`は次の状態を区別します。

```text
predicted → purchased → pending_result → result_confirmed → evaluated
```

購入せず結果待ちになる場合、または購入後に一部結果だけ確認できた場合も
`pending_result`です。13試合すべての`actual_result`が`1/0/2`になった場合だけ
`result_confirmed`となり、明示的評価後に`evaluated`となります。

## 試合スキーマ

| 区分 | 項目 |
|---|---|
| 識別・時間 | `prediction_run_id`, `round_id`, `toto_match_number`, `season`, `match_time`, `predicted_at` |
| 対戦 | `league`, `home_team`, `away_team`, `prediction_version` |
| 予測・不変 | `probability_1`, `probability_0`, `probability_2`, `predicted_result`, `predicted_score`, `home_expected_goals`, `away_expected_goals`, `home_elo`, `away_elo`, `elo_difference` |
| 引分・不変 | `draw_candidate`, `draw_probability`, `draw_confidence`, `draw_candidate_threshold`, `draw_candidate_reasons` |
| 結果だけ更新可 | `actual_result`, `actual_home_goals`, `actual_away_goals`, `result_confirmed_at`, `predicted_hit` |
| 改変検知 | `immutable_hash` |

通常予想には表示用の小数1桁%と別に、最終モデルのフル精度3クラス確率を追加し、
実戦履歴はフル精度値を保存します。保存時に有限・0～1・合計1を絶対誤差`1e-9`で
検証し、不正値を正規化して救済しません。Version7-C買い目は従来どおり表示用確率を
入力にするため、run所属確認に限り小数1桁%の丸め半単位`0.0005000001`を許容します。
予測式、表示確率、買い目、Coverageは変更しません。

`league`は通常予想が既存のチームカテゴリーから同一リーグを確認できた場合だけ
`J1/J2/J3`を保存し、確認できない対戦は空欄です。`season`は保存済み公式試合日時の
JST年です。独立した「引分信頼度」はVersion7-A/Bが出力していないため、
`draw_confidence`を推測で作らず空欄にします。P(0)、候補flag、閾値、候補理由は保存します。

## 設定スナップショット

`settings_snapshot_json`は`allow_nan=False`の安定順序JSONです。通常予想時点の次の既存値を
保存します。

- `prediction_version`, 採用有無・採用日時・引分override
- Version7-B全モデル係数
- Version7-A/B引分係数と引分候補閾値
- Elo、会場別、直近、順位補正の画面スイッチ
- 予測生成日時と、取得できる場合の戦略バックテストcutoff

買い目は予測後に生成・変更されるため、後から予測Snapshotへ書き足しません。商品、
ダブル/トリプル数、口数、予定額、Coverage、Draw閾値・margin、最終選択は買い目履歴の
不変列へ構造化して保存します。

同じStreamlit session内で、採用中のVersion7-Bパラメータと直前の最適化結果が完全一致
すると確認できた場合だけ、`optimization_run_id`, `best_trial`, `best_score`も関連付けます。
一致を確認できない最適化情報は推測で関連付けません。

## 買い目スキーマ

| 区分 | 項目 |
|---|---|
| 識別 | `bet_record_id`, `prediction_run_id`, `round_id`, `target`, `prediction_version`, `record_type` |
| 推奨/購入 | `recommended`, `purchased`, `source_recommendation_id`, `generated_at`, `purchased_at` |
| 最終選択・不変 | `double_count`, `triple_count`, `selections_json`, `ticket_count`, `planned_purchase_amount_yen`, `actual_purchase_amount_yen`, `coverage` |
| 引分・不変 | `draw_candidate_threshold`, `draw_candidate_margin`, `draw_inclusion_json`, `draw_included_match_count`, `draw_included_ticket_count` |
| 結果評価 | `covered_match_count`, `all_matches_covered`, `winning_rank`, `winning_ticket_count`, `evaluated_at` |
| 金額評価 | `simulation_return_yen`, `actual_return_yen`, `simulation_profit_yen`, `actual_profit_yen`, `simulation_roi`, `actual_roi` |
| 改変検知 | `immutable_hash` |

`target`は`toto`, `mini_a`, `mini_b`だけです。`selections_json`は元toto試合番号、対象側
試合番号、対戦カード、single/double/triple、最終`1/0/2`選択、0の包含、試合別Coverageを
保持します。`draw_inclusion_json`はP(0)、モデル/optimizer引分候補、閾値、Draw Signal、
Draw Inclusionの評価有無・Score・Coverage loss・推奨、最終的な0包含を保持します。

AI案は`record_type=recommended, recommended=True, purchased=False`です。購入記録は手動変更後の
最終Planから別行を作り、`record_type=purchased, recommended=False, purchased=True`とします。
外部購入・決済は行いません。

## 公式結果、的中、払戻、ROI

`update_actual_results()`が更新できるのは試合の結果列と開催回の結果・公式払戻列だけです。
入力元は既存`TotoHistoryManager.load_round()`が返す`toto公式`または公式由来`保存CSV`に
限定します。現在データ、予測本命、確率からactualを作りません。既存の確定結果と異なる
結果・得点が来た場合は上書きせず競合エラーにします。一部結果は保存できますが未評価です。

13結果確定後の`evaluate_run()`は、本命の総的中数と本命が正解した実結果別`1/0/2`件数、
各商品の試合別Coverage、全試合Coverageを計算します。totoは全組合せを展開せず、13/12/11
的中券数を動的集計し、公式1～3等金が3件すべて確認できる場合だけ払戻を計算します。
mini toto A/Bは5試合的中を判定しますが、既存データにmini公式払戻がないため金額とROIを
推測しません。

```text
simulation_roi = simulation_return_yen / planned_purchase_amount_yen
actual_roi     = actual_return_yen / actual_purchase_amount_yen
```

ROIは倍率として保存し、画面は%表示します。未購入、未確定、払戻不明、分母0は空欄/N/Aです。
未購入を実ROI=-100%にせず、recommendedの払戻を実利益へ混ぜません。

## Version8-B/Cへの引継ぎと既知の制限

フル精度P(1)/P(0)/P(2)、本命、実結果、リーグ、開催日時、season、P(0)、引分候補、閾値、
0を買い目へ含めたか、0をカバーしたかを残すため、将来Brier、Log Loss、Calibration、
1/0/2別、引分Precision/Recall/F1、リーグ別、直近N開催を再計算できます。Version8-A自身は
それらの診断や劣化判定を実装しません。

既知の制限は次のとおりです。

- 独立した引分信頼度は現行モデルに存在せず空欄
- mini toto A/Bの公式払戻・収支・ROIは既存取得経路がないためN/A
- toto公式サイト障害時は公式由来の保存CSVまでしか結果を更新できない
- 複数プロセスをまたぐ排他lockは持たず、個人用の単一Streamlit processを前提とする
- Version8-A以前の予測を現在モデルで実戦履歴へ遡及生成しない
- Version7-B旧履歴は従来どおり当時保存された履歴だけをバックテストし、実戦履歴へ移植しない
