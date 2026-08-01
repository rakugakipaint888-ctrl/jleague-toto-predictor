# 自分専用 Jリーグ toto予想AI

## アプリ概要

J1・J2・J3の試合を対象に、直近成績とポアソン分布からtotoの「1・0・2」を
予想する個人利用専用のStreamlitアプリです。

個人利用に必要な予測・データ取得・分析機能だけに絞り、予想精度と保守性を
優先して開発します。

## 現在の機能

- J1～J3、全60クラブのカテゴリー付きチーム選択
- 13試合の入力・予想
- ポアソン分布によるホーム勝ち・引き分け・アウェイ勝ちの確率計算
- 本命、信頼度、予想スコア、予想理由の表示
- CSV読込
- Jリーグ公式データの自動取得
- 直近5試合、平均得点、平均失点の自動計算・自動入力
- 順位、ホーム成績、アウェイ成績の自動入力・手修正
- 予想結果のCSV保存
- 公式データ → CSV → 手入力の自動切替

## データ取得元

2026年8月1日現在、次のJリーグ公式公開ページを使用しています。

### 日程・試合結果

- [J. League Data Site 日程・結果](https://data.j-league.or.jp/SFMS01/)
- 2026/27 J1：`competition_years=2026&competition_frame_ids=1`
- 2026/27 J2：`competition_years=2026&competition_frame_ids=2`
- 2026/27 J3：`competition_years=2026&competition_frame_ids=3`
- 2026特別 J1百年構想リーグ：`competition_years=20261&competition_frame_ids=35`
- 2026特別 J2・J3百年構想リーグ：
  `competition_years=20261&competition_frame_ids=36`

### 順位

- [J1公式順位表](https://www.jleague.jp/j1/standings/)
- [J2公式順位表](https://www.jleague.jp/j2/standings/)
- [J3公式順位表](https://www.jleague.jp/j3/standings/)

公式APIやAPIキーは使用せず、公開HTML内の表を低頻度で読み取ります。
2026/27シーズンだけで直近5試合がそろわない期間は、2026年上半期の
百年構想リーグを新しい順に補完します。

Jリーグ公式サイトの[著作権について](https://www.jleague.jp/general/copyright/)
では、掲載コンテンツの無断複製を制限しています。本アプリは個人利用に限定し、
取得したデータや公式ページの内容を再配布・販売しません。サイトの仕様・方針が
変わった場合は取得を停止し、CSVへ切り替えてください。

## 自動取得するデータ

- 試合日
- ホームチーム
- アウェイチーム
- 各クラブの直近5試合
- 直近5試合の平均得点
- 直近5試合の平均失点
- 現行シーズンの順位
- 取得対象期間のホーム成績（試合数・勝分敗・得失点）
- 取得対象期間のアウェイ成績（試合数・勝分敗・得失点）

2026年8月1日時点では2026/27シーズン開幕前のため、公式順位表は未確定です。
開幕後、公式順位表に数値が掲載されると自動入力されます。

## データ更新方法

Streamlitのキャッシュは6時間です。

- 通常：6時間ごとに自動更新
- すぐ更新：Streamlitのキャッシュを削除してアプリを再起動
- シーズン更新：`data_loader.py`の大会年・大会区分を確認
- 昇降格・クラブ名更新：`teams.py`だけを修正

2026/27シーズン序盤は1回の更新で最大5ページを取得します。現行シーズンだけで
全60クラブの直近5試合がそろうと、過去大会1ページの取得を自動で省略し、
4ページになります。

## 取得失敗時

次の順で自動切替します。

1. `JLeagueOfficialDataSource`（Jリーグ公式公開ページ）
2. `CsvMatchDataSource`（`data/matches.csv`）
3. 手入力

技術的な例外やHTML解析エラーは画面へ表示しません。公式データとCSVの両方を
利用できない場合は、`手入力モードで起動しました。`だけを表示します。

## CSV読込

`data/matches.csv`は任意です。古い8列形式のCSVも引き続き読み込めます。
新しい順位・会場別成績の列は省略可能です。詳しい仕様は
[`data/README.md`](data/README.md)を参照してください。

## フォルダ構成

```text
.
├── app.py                   # Streamlit画面・入力・結果表示
├── teams.py                 # J1～J3クラブ一覧
├── prediction.py            # ポアソン分布と予測ロジック
├── data_loader.py           # 公式データ・CSV取得と共通形式への変換
├── data/
│   └── README.md            # matches.csvの仕様
├── tests/
│   ├── test_app.py          # Streamlit画面テスト
│   ├── test_data_loader.py  # 公式取得・フォールバックテスト
│   └── test_prediction.py   # 予測計算の回帰テスト
├── requirements.txt
└── README.md
```

データ取得元を変更するときは、`MatchDataSource`の`name`と`load()`を実装する
新しいクラスを`data_loader.py`へ追加します。`MATCH_COLUMNS`と同じDataFrameを
返せば、`app.py`と`prediction.py`は変更不要です。

## 実行方法

```bash
pip install -r requirements.txt
streamlit run app.py
```

テストは次のコマンドで実行します。

```bash
python -m unittest discover -s tests -v
```

## 開発ロードマップ

- Version 1：チームプルダウン
- Version 2：CSV・データ取得基盤
- Version 3：Jリーグ公式データ自動取得
- Version 4：おすすめ度表示
- Version 5：ELOレーティング導入
- Version 6：AI分析
- Version 7：過去予想保存・的中率分析

## 今後追加予定

- ホーム補正
- 引分補正
- ホーム・アウェイ成績の予測利用
- 過去対戦成績
- AIコメント

予想は統計モデルによる推定であり、的中や利益を保証しません。
