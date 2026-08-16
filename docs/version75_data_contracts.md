# Version7.5 データ契約

この文書はVersion7.5時点でコードが実際に読み書きする項目だけを記載する。
実行時CSVは`.gitignore`対象のままとし、Schema定義とテストだけをGit管理する。

## 予想履歴

保存先は`data/history/prediction_history.csv`。1行は1開催回・1試合・1予測Versionで、
主キーは`toto_round`、`toto_match_number`、`prediction_version`。同じ主キーの再保存は
最新行へ置換する。

必須列は次のとおり。

| 区分 | 列 |
|---|---|
| 開催回・Version | `toto_round`, `toto_match_number`, `prediction_version`, `prediction_date` |
| 対戦 | `home_team`, `away_team` |
| 予測 | `prediction`, `probability_1`, `probability_0`, `probability_2` |
| 期待得点 | `home_expected_goals`, `away_expected_goals` |

任意値の列もCSV Schema上は常に存在する。未確定・未取得は空欄であり、0とは扱わない。

| 区分 | 列 |
|---|---|
| 実結果・評価 | `actual_result`, `hit`, `total_hits`, `accuracy` |
| 確率評価 | `brier_score`, `log_loss`, `calibration`, `expected_hits` |
| 金額 | `stake_yen`, `payout_yen`, `roi` |
| Version7.5メタデータ | `draw_candidate`, `draw_candidate_reasons`, `prediction_settings_json`, `strategy_backtest_eligible`, `strategy_backtest_cutoff_at` |

`probability_1/0/2`は0～1で保存する。`actual_result`は`1`、`0`、`2`または空欄。
`prediction_settings_json`はVersion7.5以後の現在予測Versionにだけ保存し、過去行を
現在設定で埋めない。Version7-B SnapshotはSchema Version、予測Version、採用状態・
採用日時、モデル係数、引分係数、Elo・会場・直近・順位補正スイッチを含む。
ライブ予想は開催初日`00:00 JST`より前に保存された場合だけ
`strategy_backtest_eligible=True`。cutoff以後の保存行は履歴には残すが、戦略評価から
除外する。旧行の空欄は現在値から推測しない。

## toto開催回

`TotoRound`の必須構造は`round_id`と`matches`。各`TotoMatch`は`round_id`、
`match_number`、`home_team`、`away_team`、`match_time`を必須とする。`stadium`、
`actual_result`、`home_goals`、`away_goals`は任意。開催回CSVの必須列も同じ5項目で、
任意列は次のとおり。

`stadium`, `actual_result`, `home_goals`, `away_goals`, `sale_start`, `sale_end`,
`result_date`, `first_prize_yen`, `second_prize_yen`, `third_prize_yen`, `source_url`,
`fetched_at`

評価可能な開催回は、公式順1～13が揃い、13件すべての`actual_result`が
`1`・`0`・`2`である場合だけ。未確定回は未評価とし、0的中・0円払戻へ変換しない。

## 払戻

払戻の正規形は`first_prize_yen`、`second_prize_yen`、`third_prize_yen`。
mapping、Series、tuple/list、属性objectという既存入力差を`normalize_toto_payouts`で
吸収する。3値が揃い1等が正の値の場合だけtoto ROIへ使用する。欠損時の払戻・利益・
ROIは`None`で、0円と区別する。mini totoの払戻SchemaはVersion7.5では未実装。

## 買い目

入力`MatchPrediction`は対象内試合番号、元toto試合番号、ホーム、アウェイ、
`probability_1/0/2`、任意のモデル引分候補・理由を持つ。各確率は有限・非負で合計1。
出力`BetRecommendation`は分析値、`bet_type`、`outcomes`、理由を持つ。

| `bet_type` | `outcomes`件数 |
|---|---:|
| `single` | 1 |
| `double` | 2 |
| `triple` | 3 (`1`, `0`, `2`) |

`BetPlan`は`target`、全試合の推薦、引分閾値・marginを持つ。口数は
`2^double × 3^triple`、購入額は口数×100円。試合別Coverageは選択結果の確率和、
全体Coverageは試合別Coverageの積。

表示・試合別CSVの正式列順は`bet_export.BET_PLAN_FRAME_COLUMNS`、画面Schemaは
`BET_PLAN_DISPLAY_COLUMNS`（Schema Version 1）で管理する。組み合わせCSVは
`口番号`と対象試合列からなり、mini Bも元toto第6～10試合との対応を保持する。

## 買い目バックテスト

必須列は`toto_round`、`toto_match_number`、`prediction_version`、
`probability_1/0/2`、`actual_result`。任意列は`prediction_date`、チーム名、予測、
購入額、払戻、ROI、引分候補・理由、設定Snapshot、cutoff適格性。Version7-Bは
保存済み履歴だけを使い、現在設定で再生成しない。

## Streamlit Session State

主要な型契約は次のとおり。対象別widget keyは`toto`、`mini_a`、`mini_b`で分離する。

| key | 型・意味 |
|---|---|
| `latest_prediction_results` | 13行の`DataFrame` |
| `latest_prediction_draw_threshold` | 0～1の有限`float` |
| `version7c_ai_plan`, `version7c_manual_plan` | `BetPlan` |
| `version7c_plan_request` | 入力・設定Fingerprint文字列 |
| `version7c_type_<fingerprint>_<match>` | `single` / `double` / `triple` |
| `version7c_outcomes_<fingerprint>_<match>` | 重複のない`1` / `0` / `2`のlist |
| `version7c_backtest_results` | `BetStrategyBacktest`のtuple |
| `version7c_backtest_round_ids` | 公式確認済み開催回IDのtuple |

古いFingerprintの手動keyは新しい計画生成時に削除する。widgetの古い型、`None`、NaN、
Infinity、範囲外値は生成前に既定値または許容範囲へ正規化する。正しい既存値とUI操作は
変更しない。
