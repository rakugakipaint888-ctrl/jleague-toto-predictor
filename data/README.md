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
match_number,match_date,home_team,away_team,home_scored,home_conceded,away_scored,away_conceded,home_recent_matches,away_recent_matches,home_rank,away_rank,home_played,home_wins,home_draws,home_losses,home_goals_for,home_goals_against,away_played,away_wins,away_draws,away_losses,away_goals_for,away_goals_against
1,2026-08-07,鹿島アントラーズ,浦和レッズ,1.8,0.8,1.4,1.0,,,2,6,10,8,1,1,18,6,10,4,3,3,12,11
```

## 列の意味

- 必須：`home_team`、`away_team`
- `match_number`：1～13。省略時は上から順に自動採番
- `match_date`：推奨形式は`YYYY-MM-DD`
- `home_scored`、`home_conceded`：ホーム側クラブの平均得点・平均失点
- `away_scored`、`away_conceded`：アウェイ側クラブの平均得点・平均失点
- `home_recent_matches`、`away_recent_matches`：複数試合を` / `で区切る
- `home_rank`、`away_rank`：順位。空欄なら未確定
- `home_*`の成績列：ホーム側クラブのホーム成績
- `away_*`の成績列：アウェイ側クラブのアウェイ成績
- `played`：試合数
- `wins`、`draws`、`losses`：勝・分・敗
- `goals_for`、`goals_against`：得点・失点

平均値を省略・空欄・範囲外にした場合はVersion 1の初期値を使います。
順位・会場別成績の列はすべて省略可能で、従来形式との互換性があります。
