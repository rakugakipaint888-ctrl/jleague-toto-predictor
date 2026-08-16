# CSV試合データ

Jリーグ公式データを取得できない場合、このフォルダの`matches.csv`を自動で
読み込みます。ファイルがなくても、手入力モードで正常に起動します。

## 最小構成

ホーム・アウェイのクラブ名だけでも読み込めます。

```csv
home_team,away_team
鹿島アントラーズ,浦和レッズ
柏レイソル,ＦＣ東京
```

## 全項目

```csv
match_number,match_date,home_team,away_team,home_scored,home_conceded,away_scored,away_conceded,home_recent_matches,away_recent_matches,home_rank,away_rank,home_points,away_points,home_goal_difference,away_goal_difference,home_season_played,home_season_wins,home_season_draws,home_season_losses,home_season_goals_for,home_season_goals_against,away_season_played,away_season_wins,away_season_draws,away_season_losses,away_season_goals_for,away_season_goals_against,home_played,home_wins,home_draws,home_losses,home_goals_for,home_goals_against,away_played,away_wins,away_draws,away_losses,away_goals_for,away_goals_against
1,2026-08-07,鹿島アントラーズ,浦和レッズ,1.8,0.8,1.4,1.0,,,2,6,28,12,14,-5,12,9,1,2,25,11,12,3,3,6,12,17,6,5,1,0,15,4,6,1,2,3,5,11
```

## 列の意味

- 必須：`home_team`、`away_team`
- `match_number`：1～13。省略時は上から順に自動採番
- `match_date`：推奨形式は`YYYY-MM-DD`
- `home_scored`、`home_conceded`：ホーム側クラブの平均得点・平均失点
- `away_scored`、`away_conceded`：アウェイ側クラブの平均得点・平均失点
- `home_recent_matches`、`away_recent_matches`：複数試合を` / `で区切る。
  `YYYY-MM-DD H/A vs 対戦相手 得点-失点`形式なら時系列重み付けにも使用
- `home_rank`、`away_rank`：順位。空欄なら未確定
- `home_points`、`away_points`：現在勝点
- `home_goal_difference`、`away_goal_difference`：現在得失点差
- `home_season_*`、`away_season_*`：シーズン全体の試合数、勝分敗、得失点
- `home_*`の成績列：ホーム側クラブのホーム成績
- `away_*`の成績列：アウェイ側クラブのアウェイ成績
- `played`：試合数
- `wins`、`draws`、`losses`：勝・分・敗
- `goals_for`、`goals_against`：得点・失点

平均値を省略・空欄・範囲外にした場合はVersion 1の初期値を使います。
順位表・会場別成績の列はすべて省略可能で、従来形式との互換性があります。
シーズン試合数、勝点、得失点差のいずれかがない場合、順位等補正は適用しません。

## 実行時キャッシュと履歴

Version6～Version7-Bは`data/cache/`、`data/history/`、`data/config/`を
自動作成し、次を保存します。

- `official_match_results.json`：現行・前シーズン相当の試合結果、順位表、
  直近5試合、シーズン・会場別クラブ成績
- `elo_ratings.json`：処理済み試合ID、全クラブElo、試合数、最終更新日
- `toto_rounds.csv`：開催回、第1～13試合、試合日時、実結果、公式配当
- `backtest_match_history.csv`：バックテスト用Jリーグ履歴の動的保存CSV
- `../history/prediction_history.csv`：開催回・試合番号・Version別の予想履歴と
  的中率、Brier Score、Log Loss、Calibration、的中期待値、ROI。Version7.5以後の
  新規履歴は引分候補、候補理由、予測時設定Snapshot、戦略バックテストcutoffも保存
- `../history/version7a_optimization_history.csv`：実行日時、Trial数、時系列の
  Training／Validation期間・試合数、Best Score・係数、全体・引分指標、
  Version6比較、乱数seed
- `../config/version7a_draw_settings.json`：画面でYESを選んだ場合だけ更新する
  Version7-A採用済み引分設定
- `../config/version7a_backups/`：採用直前の設定を復元するJSONバックアップ
- `../history/version7b_partial_trials.csv`：Version7-BのTrial完了ごとのobjective、
  Training Mean、Robust Training、安定性・引分ペナルティ
- `../history/version7b_fold_metrics.csv`：全Trial・全Training内部Foldの期間、
  試合数、Score、Brier、Log Loss、Calibration、的中率、1／0／2、引分指標
- `../history/version7b_model_ranking.csv`：Training内Optuna objective順の上位20モデル
- `../history/version7b_optimization_history.csv`：探索方式、期間、seed、重み、
  Training／Validation指標、最適係数、判定、採用有無
- `../config/version7b_model_settings.json`：画面でYESを選んだ場合だけ更新する
  Version7-B採用済み全体モデル設定
- `../config/version7b_backups/`：Version7-B採用直前の設定バックアップ

`data/reference/jleague_history_2024_2025.csv`は、公式取得と動的保存CSVの両方が
利用できない初回起動でも直近1シーズン以上を検証できる読み取り用データです。
実行時に更新されるファイルとは分離してGit管理します。

`data/cache/`、`data/history/`、`data/config/`のファイルは入力CSVではなく、
取得失敗時の継続、検証履歴、採用設定のための実行時ファイルで、Git管理対象外です。
`data/reference/`だけは
初回フォールバック用の読み取り専用データとしてGit管理します。`toto_rounds.csv`は
公式→保存CSV→現在データ→エラー表示のフォールバックで使います。
`prediction_history.csv`を削除すると累積分析履歴も失われるため、必要に応じて
分析タブのダウンロードボタンから保存してください。

## 予想結果CSVのVersion4追加列

画面の「予想結果をCSVで保存」では、既存列に次の8列を追加します。

- `home_elo`
- `away_elo`
- `elo_difference`
- `home_expected_before_elo`
- `away_expected_before_elo`
- `home_expected_after_elo`
- `away_expected_after_elo`
- `elo_adjustment_enabled`

Elo履歴を取得できない場合、Elo値は空欄、補正前後の期待得点は同値、
`elo_adjustment_enabled`は`False`になります。

## 予想結果CSVのVersion5追加列

Version4までの列を残したまま、順位表、通常・加重直近平均、会場別平均、
Version4／Version5期待得点、補正状態、本命差分を追加します。列名の全一覧は
ルートの[`README.md`](../README.md)を参照してください。

## 予想結果CSVのVersion6追加列

Version5までの列を残したまま、次の列を追加します。

- `toto_round`
- `toto_match_number`
- `prediction_version`
- `actual_result`
- `hit`
- `total_hits`
- `accuracy`
- `prediction_date`

入力、予想一覧、Version比較、試合詳細、予想結果CSV、予想履歴CSVはすべて
スポーツくじ公式の第1～13試合順です。公式試合順を取得できない場合だけ、
保存済み開催回CSV、現在のJリーグ試合データの順でフォールバックします。

## 予想結果CSVのVersion7-A追加列

Version6までの列を残し、Version6とVersion7-Aを同じ行で再比較できるように
次の列を追加します。

- `version6_prediction` / `version7a_prediction`
- `version6_home_win` / `version6_draw` / `version6_away_win`
- `version7a_home_win` / `version7a_draw` / `version7a_away_win`
- `version6_top_probability` / `version7a_top_probability`
- `poisson_draw_probability` / `adjusted_draw_probability`
- `draw_candidate` / `draw_candidate_reasons`
- `version7a_prediction_changed`

`1`、`0`、`2`の表示確率は小数1桁でも合計100.0%になるよう丸めます。`0`は
数値0、文字列`"0"`、CSV読込後の`0.0`を同じ引分ラベルとして保存・評価します。
Version7-Aの最適化履歴は既存の予想履歴や開催回CSVへ混在させません。列構成が
不正な最適化履歴CSVは上書きせず、画面で保存失敗を通知します。

## Version7-Bの保存データ

Version7-BのTrial、ランキング、実行履歴も既存CSVとは分離します。Trial設定や
探索結果を`model_config.py`へ書き戻しません。通常予想へ反映されるのは、モデル
最適化画面で比較結果を確認して`YES`を押した候補だけです。採用前JSONは必ず別名で
バックアップし、直前設定へ復元できます。引分係数を探索対象にしなかった採用では、
Version7-Aの引分設定をそのまま参照します。

途中Trialは逐次保存されるためプロセス中断後も確認できますが、保存済みTrialから
残りだけを自動再開する機能はありません。CSV列が破損している場合は上書きせず、
画面へエラーを表示します。ROIは公式13試合・結果・必要な配当を確認できる場合だけ
保存し、不足データから推測しません。

Run IDは実行ごとに一意で、同じ条件の再実行は共通の設定Fingerprintを持ちます。
このため、同じ100 Trialを再実行しても旧実行を削除・置換しません。認識済みの旧列は
既存行を保持したまま新列へ移行します。Final Validation列はTraining内で確定した
Best Trialにだけ保存し、他TrialのFinal Validationは計算・保存しません。

Version7.5以後に通常予想で保存したVersion7-B行は、P(1)・P(0)・P(2)とVersionに加え、
`draw_candidate`、`draw_candidate_reasons`、`prediction_settings_json`、
`strategy_backtest_eligible`、`strategy_backtest_cutoff_at`を保持します。
設定JSONには保存Schema Version、採用Version、モデル係数、引分係数、採用日時、
Elo・会場別・直近・順位補正の画面スイッチを含めます。旧CSVは不足列を空欄で読み込む
後方互換を維持し、当時の設定を現在値で補完しません。
開催初日`00:00 JST`以後に保存した新規ライブ予想は履歴として残しますが、買い目戦略
バックテストでは評価対象外にします。旧履歴の適格性は推測で補完しません。
