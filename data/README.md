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

## 実行時キャッシュ

Version5は`data/cache/`を自動作成し、次のJSONを保存します。

- `official_match_results.json`：現行・前シーズン相当の試合結果、順位表、
  直近5試合、シーズン・会場別クラブ成績
- `elo_ratings.json`：処理済み試合ID、全クラブElo、試合数、最終更新日

これらは入力CSVではなく再計算を減らすための内部ファイルです。Git管理対象外で、
削除しても次回の公式取得・Elo計算時に再生成されます。

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
