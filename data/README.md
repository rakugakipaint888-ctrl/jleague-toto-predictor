# CSV試合データ

APIを利用できない場合、このフォルダの`matches.csv`を自動で読み込みます。
`matches.csv`がなくても、手入力モードで正常に起動します。

## CSVの列

```csv
match_number,match_date,home_team,away_team,home_scored,home_conceded,away_scored,away_conceded,home_recent_matches,away_recent_matches
1,2026-08-07,鹿島アントラーズ,浦和レッズ,1.8,0.8,1.4,1.0,,
2,2026-08-08,柏レイソル,ＦＣ東京,1.6,1.0,1.2,1.4,,
```

- 必須列：`home_team`、`away_team`
- 任意列：それ以外のすべての列
- `match_number`を省略した場合：上から第1～第13試合として扱う
- 平均値を省略した場合：Version 1と同じ初期値を使用
- 平均値の範囲：`0.0`～`5.0`
- `match_date`の推奨形式：`YYYY-MM-DD`
- `home_recent_matches`と`away_recent_matches`：複数試合を` / `で区切る
- 読み込み上限：第1～第13試合

APIへ戻す場合は`API_FOOTBALL_KEY`を設定してアプリを再起動します。
