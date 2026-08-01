# 自分専用 Jリーグ toto予想AI

## アプリ概要

J1・J2・J3の試合を対象に、直近成績、会場別成績、順位表、Elo、
ポアソン分布からtotoの「1・0・2」を予想する個人利用専用のStreamlitアプリです。

ログイン、会員管理、決済、マルチユーザー、サブスク販売の機能は持たず、
予想精度、自動データ取得、分析機能、保守性を優先します。

## 現在の機能

- J1～J3、全60クラブのカテゴリー付きチーム選択
- 13試合の入力・予想
- Jリーグ公式データの自動取得
- 直近5試合、平均得点、平均失点の自動計算・自動入力
- 直近5試合の時系列重み付け（最新順に5・4・3・2・1）
- 順位、勝点、試合数、勝分敗、得点、失点、得失点差の自動取得
- ホーム成績、アウェイ成績の自動入力・手修正
- 直近、会場別、順位表、Eloの各補正ON/OFF
- ポアソン分布によるホーム勝ち・引き分け・アウェイ勝ちの確率計算
- J1・J2・J3全クラブのEloレーティング計算
- Elo差による期待得点補正とON/OFF切替
- カテゴリー内順位付きElo一覧とElo順の並べ替え
- 本命、信頼度、予想スコア、予想理由の表示
- CSV読込と予想結果CSV保存
- Version4相当とVersion5の13試合比較表示
- 公式データ → CSV → 手入力の自動切替
- 補正別の欠損フォールバックとVersion4予測への最終フォールバック

## Version5

Version5はVersion4のポアソン＋Eloを土台に、次の3要素を追加します。

- 直近5試合を最新順に`5, 4, 3, 2, 1`で時系列重み付け
- 加重直近平均60%とシーズン平均40%の混合
- ホームチームのホーム成績、アウェイチームのアウェイ成績を試合数別に混合
- 1試合平均勝点と1試合平均得失点差による最大±8%の補正
- Elo、会場別、直近重み、順位等の4スイッチ（すべて初期値ON）
- Version4相当とVersion5の本命・1/0/2確率・最大確率の比較
- 欠損項目だけを無効化し、取得済みデータで予測を継続
- NaN、無限大、負数が発生した場合のVersion4フォールバック

順位は画面表示と比較用の補助指標です。順位だけで期待得点を直接大きく変更せず、
補正計算には1試合平均勝点と1試合平均得失点差を使用します。

## Version5の計算順序

`model_pipeline.py`で次の順序を固定しています。

1. シーズン全体の平均得点・平均失点
2. 直近5試合の加重平均との混合
3. ホーム／アウェイ別成績との混合
4. 基本期待得点の算出（Version1からのホーム8%を維持）
5. Elo補正
6. 勝点・得失点差補正
7. 最終期待得点を0.15～4.00へ制御
8. ポアソン分布による1・0・2確率計算

Version4相当値も同時に計算・保持します。会場別、直近重み、順位等をOFFにし、
EloだけをONにすると、通常範囲ではVersion4と同じ期待得点・確率・本命を返します。

## Version5設定値

設定はすべて[`model_config.py`](model_config.py)へ集約しています。

- 直近試合の重み：`(5, 4, 3, 2, 1)`
- 加重直近平均：60%
- シーズン平均：40%
- 会場別5試合以上：会場別70%／全体30%
- 会場別4試合：会場別60%／全体40%
- 会場別1～3試合：会場別40%／全体60%
- 会場別0試合または欠損：全体100%
- 1試合平均勝点差1.0：強い側+5%／弱い側-5%（最大±5%）
- 1試合平均得失点差1.0：強い側+3%／弱い側-3%（最大±3%）
- 順位等の合計補正：最大±8%
- 最終期待得点：最小0.15／最大4.00

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

順位表から取得する項目は、順位、勝点、試合数、勝数、引分数、敗数、総得点、
総失点、得失点差です。日程・結果から、ホーム／アウェイ別の試合数・勝分敗・
得失点と、直近5試合の日付・対戦相手・得点・失点・結果を計算します。

公式APIやAPIキーは使用せず、公開HTML内の表を低頻度で読み取ります。
Eloは現行シーズンと取得可能な前シーズン相当の完了試合を使用します。
2026/27シーズンでは、直前大会に当たる2026年上半期の百年構想リーグを
履歴として使用します。

2026年8月1日17:15（JST）の実接続確認では、2026年2月6日～6月7日の
完了試合600件（全60クラブ各20試合）をEloへ反映しました。未来の未開催試合、
得点が未確定の試合、基準時刻より後の試合はElo更新に使用しません。

同時点は2026/27シーズン開幕前のため、公式順位表の数値は全クラブ`-`表示です。
この状態では順位・勝点・得失点差補正だけを自動で無効化し、直前大会の直近成績、
ホーム／アウェイ成績、Eloで予測を継続します。開幕後に公式値が掲載されると、
次回キャッシュ更新時から自動で補正対象になります。

Jリーグ公式サイトの[著作権について](https://www.jleague.jp/general/copyright/)
では、掲載コンテンツの無断複製を制限しています。本アプリは個人利用に限定し、
取得データや公式ページの内容を再配布・販売しません。サイトの仕様・方針が
変わった場合は取得を停止し、CSV・手入力へ切り替えてください。

## Elo計算仕様

設定は[`model_config.py`](model_config.py)の`EloSettings`と、`INITIAL_ELO`、
`CATEGORY_BONUS`、`LEAGUE_INITIAL_ELO`、`K_FACTOR`、`HOME_ADVANTAGE`等の
明示定数へ集約しています。`teams.py`にはElo設定を置きません。

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
Elo段階だけを無効にし、直近重み・会場別・順位等の選択状態は維持します。

## キャッシュと更新条件

### 公式試合結果・順位表・クラブ別成績

- Streamlitメモリキャッシュ：6時間
- 永続キャッシュ：`data/cache/official_match_results.json`
- 保存内容：現行・前シーズン相当の試合、順位表全項目、直近5試合、会場別成績
- 6時間以内：公式サイトへ再接続せずキャッシュを使用
- 6時間経過後：現行シーズンと前シーズン相当を再取得
- 新しい試合結果の追加：次回更新時に永続キャッシュとクラブ統計を置き換え、
  Elo更新対象へ追加
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

補正別のフォールバックは次のとおりです。

- ホーム／アウェイ成績なし：通常・シーズン平均を使用
- 順位表なし：順位・勝点・得失点差補正だけを無効化
- 直近試合が5試合未満：取得できた試合だけで加重平均
- 直近試合の詳細なし：通常の直近平均を使用
- Elo履歴なし：Elo補正だけを無効化
- Version5データがすべてない、または異常値：Version4相当の期待得点を使用

Eloに必要な完了試合履歴を利用できない場合は次の文言を表示します。

```text
Eloデータを取得できないため、Elo補正なしで計算しました。
```

## モジュール構成

- `teams.py`：J1・J2・J3のクラブ情報、名称正規化、プルダウン用データ
- `model_config.py`：Elo、Version5、キャッシュの設定値
- `config.py`：Version4までのimport互換用ブリッジ
- `elo_rating.py`：Elo計算、期待得点補正、Eloキャッシュ
- `form_adjuster.py`：直近5試合の時系列重み付け
- `venue_adjuster.py`：ホーム／アウェイ別成績の混合
- `standings_adjuster.py`：勝点・得失点差補正
- `model_pipeline.py`：Version4比較とVersion5補正順序の管理
- `data_loader.py`：公式試合・順位表・クラブ統計の取得とキャッシュ
- `app.py`：各モジュールを接続するStreamlit画面

`elo_rating.py`は`teams.py`をimportしません。クラブ構成と名称正規化関数は
`app.py`から引数で渡すため、Elo計算モジュールを単体でimportでき、循環importも
発生しない構成です。

## CSV

### 読込

`data/matches.csv`は任意です。古い8列形式のCSVも引き続き読み込めます。
順位表・会場別成績の列は省略可能です。詳しい仕様は
[`data/README.md`](data/README.md)を参照してください。

### 予想結果保存

Version4までの列を削除せず、Version5では次の指定列を追加しています。

- `home_elo`
- `away_elo`
- `elo_difference`
- `home_expected_before_elo`
- `away_expected_before_elo`
- `home_expected_after_elo`
- `away_expected_after_elo`
- `elo_adjustment_enabled`
- `home_rank` / `away_rank`
- `home_points` / `away_points`
- `home_goal_difference` / `away_goal_difference`
- `home_points_per_match` / `away_points_per_match`
- `home_recent_scored_average` / `home_recent_conceded_average`
- `away_recent_scored_average` / `away_recent_conceded_average`
- `home_recent_weighted_scored` / `home_recent_weighted_conceded`
- `away_recent_weighted_scored` / `away_recent_weighted_conceded`
- `home_home_scored_average` / `home_home_conceded_average`
- `away_away_scored_average` / `away_away_conceded_average`
- `home_expected_before_version5` / `away_expected_before_version5`
- `home_expected_after_version5` / `away_expected_after_version5`
- `venue_adjustment_enabled`
- `recent_weighting_enabled`
- `standings_adjustment_enabled`
- `version4_prediction`
- `version5_prediction`
- `prediction_changed`

## フォルダ構成

```text
.
├── app.py                    # Streamlit画面・入力・結果表示
├── teams.py                  # クラブ一覧・カテゴリー・チーム名正規化
├── prediction.py             # Version3までのポアソン予測ロジック
├── elo_rating.py             # Elo計算・取得・期待得点補正・Eloキャッシュ
├── form_adjuster.py           # 直近5試合の時系列重み付け
├── venue_adjuster.py          # ホーム／アウェイ別成績の混合
├── standings_adjuster.py      # 勝点・得失点差補正
├── model_pipeline.py          # Version5の適用順序とVersion4比較
├── model_config.py           # Elo・Version5・キャッシュ設定
├── config.py                 # Version4までのimport互換用ブリッジ
├── data_loader.py            # 公式試合・順位表・クラブ統計キャッシュ
├── data/
│   ├── README.md             # matches.csvと実行時キャッシュの仕様
│   └── cache/                # 実行時に自動生成（Git管理外）
├── tests/
│   ├── test_app.py           # 13試合・画面・Elo・CSV統合テスト
│   ├── test_data_loader.py   # 公式取得・履歴キャッシュ・フォールバック
│   ├── test_elo_rating.py    # Elo式・補正・増分キャッシュ
│   ├── test_prediction.py    # Version3ポアソン計算の回帰テスト
│   ├── test_version5_model.py # Version5補正・上下限・ON/OFF
│   └── test_architecture.py  # モジュール責務・循環import
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
- Version5：直近成績・ホーム／アウェイ成績・順位等補正
- Version6：引き分け指数・波乱度・おすすめ度
- Version7：予想履歴・実結果保存・的中率分析
- Version8：バックテストと重み最適化
- Version9：独自判断入力・AIコメント
- Version10：note用原稿出力

予想は統計モデルによる推定であり、的中や利益を保証しません。
