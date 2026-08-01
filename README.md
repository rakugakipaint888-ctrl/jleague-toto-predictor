# 自分専用 Jリーグ toto予想AI

## アプリ概要

J1・J2・J3の試合を対象に、直近成績、ポアソン分布、Eloレーティングから
totoの「1・0・2」を予想する個人利用専用のStreamlitアプリです。

ログイン、会員管理、決済、マルチユーザー、サブスク販売の機能は持たず、
予想精度、自動データ取得、分析機能、保守性を優先します。

## 現在の機能

- J1～J3、全60クラブのカテゴリー付きチーム選択
- 13試合の入力・予想
- Jリーグ公式データの自動取得
- 直近5試合、平均得点、平均失点の自動計算・自動入力
- 順位、ホーム成績、アウェイ成績の自動入力・手修正
- ポアソン分布によるホーム勝ち・引き分け・アウェイ勝ちの確率計算
- J1・J2・J3全クラブのEloレーティング計算
- Elo差による期待得点補正とON/OFF切替
- カテゴリー内順位付きElo一覧とElo順の並べ替え
- 本命、信頼度、予想スコア、予想理由の表示
- CSV読込と予想結果CSV保存
- 公式データ → CSV → 手入力の自動切替
- Elo取得失敗時のVersion3予測への自動フォールバック

## Version4

Version4では、Version3のポアソンモデルを置き換えず、公式試合結果から計算した
Eloを補助情報として期待得点へ反映します。

- Eloレーティング導入
- Elo更新時のホームアドバンテージ
- K係数
- 得失点差補正と設定によるON/OFF
- Elo差による期待得点補正
- 画面の「Elo補正を使用する」スイッチ（初期値ON）
- J1・J2・J3全60クラブのElo一覧
- 公式試合履歴と計算済みEloのキャッシュ
- Eloを利用できない場合のVersion3フォールバック

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
Eloは現行シーズンと取得可能な前シーズン相当の完了試合を使用します。
2026/27シーズンでは、直前大会に当たる2026年上半期の百年構想リーグを
履歴として使用します。

2026年8月1日（JST）の実接続確認では、2026年2月6日～6月7日の
完了試合600件（全60クラブ各20試合）をEloへ反映しました。未来の未開催試合、
得点が未確定の試合、基準時刻より後の試合はElo更新に使用しません。

Jリーグ公式サイトの[著作権について](https://www.jleague.jp/general/copyright/)
では、掲載コンテンツの無断複製を制限しています。本アプリは個人利用に限定し、
取得データや公式ページの内容を再配布・販売しません。サイトの仕様・方針が
変わった場合は取得を停止し、CSV・手入力へ切り替えてください。

## Elo計算仕様

設定は[`config.py`](config.py)の`EloSettings`へ集約しています。

### 初期値

カテゴリー差を反映する方式を採用しています。

- J1：1500
- J2：1450
- J3：1400
- カテゴリー不明：1500
- `use_category_initial_ratings=False`にすると全クラブ1500へ変更可能

初期値は計算対象期間の開始時に1回だけ与えます。シーズンやカテゴリーが変わっても
Eloはリセットせず、そのまま引き継ぎます。過去データのない新規参入クラブだけ、
現在の所属カテゴリーの初期値から開始します。

### 期待スコアと更新式

標準的なElo式を使用します。

```text
E_home = 1 / (1 + 10 ^ ((R_away - (R_home + 65)) / 400))
R_home_new = R_home + K × goal_multiplier × (S_home - E_home)
R_away_new = R_away - K × goal_multiplier × (S_home - E_home)
```

- K係数：20
- ホームアドバンテージ：ホーム側へ65ポイント
- 勝利：`S=1.0`
- 引き分け：`S=0.5`
- 敗戦：`S=0.0`

ホームアドバンテージを除いた同格チーム同士の期待スコアは50%です。65ポイントを
加えたElo更新上のホーム期待スコアは約59.2%になります。1試合の増減はホームと
アウェイで同量・逆符号のため、対象クラブ間のElo総量を保ちます。

### 得失点差補正

`goal_difference_adjustment_enabled=True`が初期値です。倍率をK係数へ掛けます。

- 1点差：1.00
- 2点差：1.25
- 3点差：1.50
- 4点差以上：1.75

`False`にすると全試合1.00になります。倍率は`goal_difference_multipliers`で
変更できます。

### Eloによる期待得点補正

Version3で計算した補正前期待得点を必ず保持し、ホームEloからアウェイEloを
引いた差を次の式で緩やかに反映します。

```text
adjustment = clamp((home_elo - away_elo) / 100 × 0.05 × strength, -0.15, 0.15)
home_expected_after = home_expected_before × (1 + adjustment)
away_expected_after = away_expected_before × (1 - adjustment)
```

- Elo差100ポイントごと：強い側+5%、弱い側-5%
- 最大補正：±15%
- 補正強度`strength`：1.0

既存ポアソン式にはVersion1からのホーム8%補正があるため、期待得点補正では
65ポイントを二重加算せず、生のElo差を使用します。画面のスイッチをOFFにすると
補正後期待得点は補正前と同じになり、Version3と同じ勝敗確率を返します。

## キャッシュと更新条件

### 公式試合結果

- Streamlitメモリキャッシュ：6時間
- 永続キャッシュ：`data/cache/official_match_results.json`
- 6時間以内：公式サイトへ再接続せずキャッシュを使用
- 6時間経過後：現行シーズンと前シーズン相当を再取得
- 新しい試合結果の追加：永続キャッシュを置き換え、Elo更新対象へ追加
- 取得失敗時：最終正常キャッシュを最大7日間だけ使用
- 7日を超えて取得できない場合：CSV、手入力へフォールバック

### 計算済みElo

- 保存先：`data/cache/elo_ratings.json`
- 時計による固定TTLは設けず、試合ID、得点、設定、クラブ構成の一致で有効性を判定
- 同一履歴・同一設定：計算済みEloをそのまま使用し、全試合を再計算しない
- 新しい試合が時系列末尾へ追加：追加試合だけを増分更新
- 過去結果の訂正、設定変更、クラブ構成変更：全履歴から自動再計算

両キャッシュは実行時に自動生成され、Git管理しません。強制更新する場合は
Streamlitキャッシュと`data/cache/`内のJSONを削除してアプリを再起動します。

## 取得失敗時

通常データは次の順で自動切替します。

1. `JLeagueOfficialDataSource`（Jリーグ公式公開ページ／有効な最終正常キャッシュ）
2. `CsvMatchDataSource`（`data/matches.csv`）
3. 手入力

技術的な例外やHTML解析エラーは画面へ表示しません。公式データとCSVの両方を
利用できない場合は、`手入力モードで起動しました。`と表示します。

Eloに必要な完了試合履歴を利用できない場合は、アプリを停止せずVersion3の
ポアソン計算へ戻し、次の文言を表示します。

```text
Eloデータを取得できないため、Elo補正なしで計算しました。
```

## CSV

### 読込

`data/matches.csv`は任意です。古い8列形式のCSVも引き続き読み込めます。
順位・会場別成績の列は省略可能です。詳しい仕様は
[`data/README.md`](data/README.md)を参照してください。

### 予想結果保存

Version3までの列を削除せず、次の8列を追加しています。

- `home_elo`
- `away_elo`
- `elo_difference`
- `home_expected_before_elo`
- `away_expected_before_elo`
- `home_expected_after_elo`
- `away_expected_after_elo`
- `elo_adjustment_enabled`

## フォルダ構成

```text
.
├── app.py                    # Streamlit画面・入力・結果表示
├── teams.py                  # クラブ一覧・カテゴリー・チーム名正規化
├── prediction.py             # Version3までのポアソン予測ロジック
├── elo_rating.py             # Elo計算・取得・期待得点補正・Eloキャッシュ
├── config.py                 # Elo・キャッシュ設定
├── data_loader.py            # 公式データ・CSV取得・試合結果キャッシュ
├── data/
│   ├── README.md             # matches.csvと実行時キャッシュの仕様
│   └── cache/                # 実行時に自動生成（Git管理外）
├── tests/
│   ├── test_app.py           # 13試合・画面・Elo・CSV統合テスト
│   ├── test_data_loader.py   # 公式取得・履歴キャッシュ・フォールバック
│   ├── test_elo_rating.py    # Elo式・補正・増分キャッシュ
│   └── test_prediction.py    # Version3ポアソン計算の回帰テスト
├── requirements.txt
└── README.md
```

データ取得元を変更するときは、`MatchDataSource`の`name`と`load()`を実装する
クラスを`data_loader.py`へ追加します。Elo用履歴も渡す取得元は、互換性を保った
まま追加メソッド`load_bundle()`で`OfficialDataBundle`を返します。

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

- Version1：J1・J2・J3チーム選択
- Version2：CSV・データ取得基盤
- Version3：Jリーグ公式試合データ自動取得
- Version4：Eloレーティング導入
- Version5：順位・ホーム／アウェイ成績補正
- Version6：おすすめ度・波乱度・引き分け指数
- Version7：予想履歴・実結果保存・的中率分析
- Version8：バックテストによる重み最適化

予想は統計モデルによる推定であり、的中や利益を保証しません。
