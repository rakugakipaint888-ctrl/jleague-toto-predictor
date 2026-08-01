# 試合データ

このフォルダに `matches.csv` を置くと、アプリ起動時に対戦カードと平均値を読み込みます。
`matches.csv` がなくても、従来どおり手入力で利用できます。

## CSVの列

```csv
match_number,home_team,away_team,home_scored,home_conceded,away_scored,away_conceded
1,鹿島アントラーズ,浦和レッズ,1.8,0.8,1.4,1.0
2,柏レイソル,ＦＣ東京,1.6,1.0,1.2,1.4
```

- `home_team` と `away_team` は必須です。
- `match_number` を省略した場合は、CSVの上から第1試合、第2試合として扱います。
- 4つの平均値を省略した場合は、Version 1と同じ初期値を使います。
- 平均値は `0.0` から `5.0` の範囲で入力します。
- totoに合わせて、第1試合から第13試合までを読み込みます。

将来Jリーグ公式データや無料APIへ移行する場合は、`data_loader.py` に新しいデータ取得クラスを追加します。`app.py` の計算処理は変更せずに差し替えられます。
