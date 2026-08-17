# 自分専用 Jリーグ toto予想AI

## アプリ概要

J1・J2・J3の試合を対象に、直近成績、会場別成績、順位表、Elo、
ポアソン分布からtotoの「1・0・2」を予想する個人利用専用のStreamlitアプリです。

ログイン、会員管理、決済、マルチユーザー、サブスク販売の機能は持たず、
予想精度、自動データ取得、分析機能、保守性を優先します。

## 現在の機能

- J1～J3、全60クラブのカテゴリー付きチーム選択
- toto公式の開催回と第1～13試合順による13試合の入力・予想
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
- 開催回をキーにしたVersion4～Version7-Bの予想・実結果履歴CSV
- 開催日時点より前のデータだけを使うバックテスト
- 的中率、1・0・2別正答率、Brier Score、Log Loss、Calibration、ROI
- 開催回別・累積・Version別の表とグラフを備えた分析タブ
- CSV読込と予想結果・予想履歴CSV保存
- Version4～Version7-Aの13試合比較表示
- Version7-Aの引分候補、引分専用評価、確率帯別Calibration
- 時系列Training／Validation分離とOptuna小規模探索
- Version6とVersion7-Aの同一Validation比較、明示的な設定採用・復元
- Version7-Bの全体パラメータ最適化（Optuna／Random／Grid／2段階探索）
- 固定分割、シーズン／開催回ウォークフォワード、上位20モデルランキング
- 引分性能保護、過学習判定、モデル安定性、ブートストラップ95%信頼区間
- Version7-Cのtoto／mini toto A・B買い目最適化
- ダブル数・トリプル数指定、口数・購入金額・Coverageの即時計算
- AI提案後の手動調整、試合別／全組み合わせCSV、買い目戦略比較
- Version8-Aの実戦予測run、設定Snapshot、AI推奨／実購入、公式結果・払戻の分離保存
- 実戦履歴サマリー、13試合詳細、購入記録、公式結果更新、3階層CSV export
- Version8-Bの実戦履歴によるモデル診断、1/0/2・引分・リーグ・Version別比較
- 直近5/10開催のRolling診断、劣化・偏り・確率合計・データ品質の異常検知
- AI推奨／実購入を分けた買い目・Coverage・ROI診断と診断履歴CSV
- 公式データ → CSV → 手入力の自動切替
- 補正別の欠損フォールバックとVersion4予測への最終フォールバック

## Version8全体構想とVersion8-A

Version8は、実戦時に保存した正確な履歴を土台に、次の3段階で開発します。

1. **Version8-A：実戦データ蓄積基盤**
2. **Version8-B：モデル診断・異常検知**
3. **Version8-C：AIによる改善提案**

今回のVersion8-Aは保存基盤だけを追加するReleaseです。モデル診断、異常検知、
AI改善提案、自動再最適化、設定の自動採用は実装しません。通常予想、Version7-A/B、
Version7-C買い目最適化の計算式と結果は変更しません。

### 実戦履歴とprediction_run_id

「実戦履歴」タブの「実戦予測として保存」を押した時点で、公式開催回の13試合、
予測日時、使用Version、フル精度P(1)・P(0)・P(2)、本命、予想スコア、期待得点、Elo、
引分候補、引分候補閾値、設定Snapshotを保存します。後から現在モデルで過去確率を
再計算・上書きしません。

1回の明示的保存には次の一意IDを付けます。同じ開催回で再予測した場合も別runとして
保存でき、同じ画面予測に対するStreamlit再実行やボタン連打は同じrunへ束ねます。

```text
run_YYYYMMDDTHHMMSSffffff_<UUID4の32桁hex>
```

既存`prediction_history.csv`はVersion別バックテスト履歴としてそのまま維持し、
Version8-Aは役割別の次の実行時CSVを使います。

- `data/history/live_round_history.csv`：開催回run、設定Snapshot、状態、評価、公式払戻
- `data/history/live_match_history.csv`：runごとの13試合予測と、後日追記する公式結果
- `data/history/live_bet_history.csv`：AI推奨／実購入の買い目、Coverage、金額、ROI

3ファイルはGit管理対象外です。保存は一時ファイルへ正常に書いてから原子的に置換し、
空・欠損・破損・列不足の既存ファイルを空データで上書きしません。既存履歴形式を変更・
移行しないため、Version8-A導入時に旧データを削除しません。

### 予測時点の設定Snapshot

設定Snapshotは安定順序JSONで、使用Version、採用有無・日時、Version7-Bモデル係数、
Version7-A/B引分係数、引分候補閾値、Elo・会場別・直近・順位補正の画面スイッチ、予測日時を
保存します。買い目設定は予測後に変わり得るため予測Snapshotへ後付けせず、買い目履歴側に
対象商品、ダブル/トリプル数、口数、予定購入額、Coverage、Draw閾値、選択を固定します。

採用中のVersion7-B係数と同じSession内の最適化結果が完全一致すると確認できた場合だけ、
`optimization_run_id`、Best Trial、Best Scoreを関連付けます。確認できない最適化情報は
推測で付けません。

### AI推奨買い目と実購入買い目

Version7-Cで生成したAI案は`recommended=True, purchased=False`として保存します。
「この買い目を実際に購入したとして記録」を押した場合だけ、購入日時、実購入金額、
手動変更後の最終買い目を別の`purchased=True`行へ保存します。AI推奨を実購入や実利益として
扱いません。外部totoサービスへの接続、決済、自動購入はありません。

買い目履歴は`toto`、`mini toto A`、`mini toto B`に対応し、試合ごとの最終選択、
single/double/triple、口数、予定/実購入額、Coverage、Draw Inclusion Score・判定・
Coverage loss、0を含む試合数、0を含む口数を保持します。

### 公式結果、払戻、ROI

結果更新は既存の`toto公式 → 公式由来の保存CSV`経路だけを利用し、更新できる列を
`actual_result`、実スコア、確認日時、公式払戻など結果確定後の項目へ限定します。
予測本命や確率からactualを作らず、既存確定結果と異なる値は競合エラーとして上書きしません。

開催回状態は`predicted`、`purchased`、`pending_result`、`result_confirmed`、`evaluated`を
区別します。一部結果や未確定結果を外れ・払戻0・ROI -100%として扱いません。13試合すべての
公式`1/0/2`を確認してから、本命の総的中数と1/0/2別的中数、各買い目の試合別Coverage、
toto全13試合、mini A/B各5試合の的中を評価します。

公式toto 1～3等金をすべて確認できる場合、13/12/11的中券数を全口展開せず集計します。
実購入とシミュレーションは次の別列です。

```text
actual_roi     = actual_return_yen / actual_purchase_amount_yen
simulation_roi = simulation_return_yen / planned_purchase_amount_yen
```

未購入、結果未確定、払戻不明、分母0はN/Aです。現行の公式取得経路はmini toto払戻を
保存しないため、mini A/Bの的中は判定しても払戻・収支・ROIを推測しません。

### 実戦履歴画面とCSV export

実戦履歴一覧では開催回、予測日時、Version、商品、購入有無・金額、結果状態、本命的中数、
買い目的中、実払戻、実収支、実ROIを確認できます。runを選ぶと、13試合の対戦カード、
P(1)/P(0)/P(2)、本命、引分候補、実結果、本命的中、AI推奨、実購入、実結果Coverageを
表示します。開催回サマリー、試合単位、買い目単位をそれぞれCSVでdownloadできます。

### Version8-B/Cへのデータ引継ぎ

P(1)/P(0)/P(2)、本命、実結果、リーグ、season、開催日時、P(0)、引分候補、候補閾値、
0を買い目へ含めたか、0を実際にカバーしたかを保存するため、Version8-Bは将来Brier、
Log Loss、Calibration、1/0/2別、引分Precision/Recall/F1、リーグ別、直近N開催を
当時の予測から計算できます。Version8-Cも同じ不変履歴を参照できますが、Version8-Aでは
診断・劣化判定・改善提案・モデル変更を実行しません。

詳細な必須/任意列、不変hash、結果更新、重複制御、ROI契約は
[`docs/version8a_live_history.md`](docs/version8a_live_history.md)を参照してください。

### Version8-Aの既知の制限

- Version7-A/Bは独立した「引分信頼度」を出力していないため、その列は推測せず空欄
- mini toto A/Bの公式払戻を既存経路から取得できず、金額・収支・ROIはN/A
- 公式サイト障害時の結果更新は、公式由来の保存CSVにある範囲まで
- 個人用の単一Streamlit processを前提とし、複数processをまたぐ排他lockは持たない
- Version8-A以前の予測を現在モデルで「当時の実戦予測」として遡及生成しない
- Version7-B旧履歴は従来の安全仕様を維持し、当時保存された履歴がある場合だけ評価

## Version8-B：モデル診断・異常検知

Version8-Bは、Version8-Aが予測時点に固定した`prediction_run_id`、P(1)・P(0)・P(2)、
本命、Version、設定Snapshot、引分候補、買い目と、後日追記された公式実結果を読み取り、
現在のモデルの悪化・偏り・データ異常を数値で検出します。現在モデルで過去予測を
再生成せず、Version8-Aの3履歴CSVを書き換えません。

「モデル診断」タブで次を選び、「モデル診断を実行」を押した場合だけ診断します。

- 対象期間：全実戦履歴、直近5/10/20開催、今シーズン、任意期間
- 対象リーグ：全リーグ、J1、J2、J3
- 対象Version：全Version、保存済みの各Version

直近N開催は重複しない`round_id`の新しいN開催を選び、同じ開催回で明示的に保存した
複数runはそれぞれ実際の予測として評価します。J1/J2/J3は保存済み`league`だけで分類し、
空欄や不明リーグを推測補完しません。結果を必要とする指標は13試合すべての公式結果が
確定したrunだけ、actual ROIは実購入かつ払戻確認済みの買い目だけで計算します。

### 診断指標

全体では試合数、開催回数、的中率、多クラスBrier Score、Log Loss、本命確率の
Calibration Error、平均本命割当確率、平均最大確率を表示します。1・0・2はそれぞれ
予測数、実発生数、的中数、Precision、Recall、F1、one-vs-rest Brier、Calibration、
平均予測確率、実発生率を分離します。

確率帯別Calibrationは各結果について`0–20%`、`20–30%`、`30–40%`、`40–50%`、
`50–60%`、`60%以上`を使用し、試合数、平均予測確率、実発生率、絶対差を表示します。
全体Calibrationは従来`metrics.py`の本命確率ECE、1/0/2別Calibrationは同じmoduleの
共通確率帯関数を使います。Brier、Log Loss、引分Precision/Recall/F1は既存評価関数を
再利用します。

引分診断は実引分率、本命0率、引分候補率、平均P(0)、引分Precision/Recall/F1、
引分Brier、引分Calibration、AI推奨／実購入別の0採用率と実引分カバー数、保存済みの
Draw Inclusion Score平均を表示します。平均P(0)と実引分率が近くても本命0率が低い
状態を独立検知しますが、引分補正の変更方法は提案しません。

開催回ごとの的中率、Brier、Log Loss、Calibration、引分F1、本命0率、実引分率と、
直近5/10開催のRolling値を表示します。Rollingは全期間値と比較します。結果確定データが
ない開催回をグラフへ捏造せず、必要開催数に満たない窓は「データ不足」です。
Rollingは選択リーグ・Version内の全保存履歴を基準とし、上部の期間指定は全体指標、
時系列、リーグ・Version・設定group、買い目集計へ適用します。

### 異常判定と初期閾値

閾値は`diagnostic_config.py`の`DiagnosticThresholds`へ一元化し、モデル設定や
`config.py`とは分離しています。Version8-Bが閾値を自動最適化・変更することはありません。

| 判定 | 注意 | 警告 |
|---|---:|---:|
| 直近的中率の全期間比低下 | 5pt | 10pt |
| Brier Score増加 | 0.05 | 0.10 |
| Log Loss増加 | 0.08 | 0.15 |
| Calibration Error増加 | 4pt | 8pt |
| 引分F1低下 | 8pt | 15pt |
| 実引分率－本命0率 | 15pt | 25pt |
| リーグ的中率の全体差 | 10pt | 15pt |
| リーグBrierの全体差 | 0.08 | 0.15 |
| リーグLog Lossの全体差 | 0.12 | 0.20 |
| リーグCalibrationの全体差 | 5pt | 10pt |
| リーグ引分F1の全体差 | 12pt | 20pt |
| 高確率予測の平均確率－的中率 | 15pt | 25pt |

高確率は最大確率60%以上、最低10試合で判定します。1/0/2別Recallは実発生5件以上で、
20%以下を注意、10%以下を警告とします。確率合計は絶対誤差`1e-9`を超えた場合に
データ品質警告です。

少数データから悪化を断定しないため、総合診断は最低26試合かつ2開催を必要とします。
この初期値はtoto 13試合×2開催に相当します。直近5/10/20開催は、指定数の開催が実際に
存在することも必要です。不足時も確認できる指標は参考値として表示しますが、総合状態は
「データ不足」です。

総合状態は固定ルールです。警告が1件以上なら「警告」、警告がなくサンプル不足なら
「データ不足」、サンプル十分で注意が1件以上なら「注意」、それ以外は「正常」です。
各異常は異常名、現在値、基準値、差、判定、テンプレート型診断コメントを表示します。
LLMによる自由文章判定は使用しません。

### データ品質・買い目・診断履歴

データ品質診断はP(1)/P(0)/P(2)欠損、NaN/Infinity/範囲外、確率合計異常、
`actual_result`不正、重複`prediction_run_id`、同一run内の試合番号重複、試合番号不足、
13試合未満、Version欠損、設定Snapshot欠損、Version8-A不変hash不一致を検出します。
不正行・不正runは集計から除外して件数と理由を表示しますが、元CSVを削除・修正しません。
データ品質は破損行を期間へ誤分類しないため元CSV全体を検査し、対象絞り込み後のモデル性能と
別区分で表示します。

買い目診断はAI推奨`recommended/simulation`と実購入`purchased/actual`を分離し、買い目数、
run数、平均口数、平均金額、平均Coverage、全結果カバー率、toto完全カバー、mini A/B的中、
購入額、払戻、収支、ROI、最高払戻、最大損失を表示します。購入なしを0円・ROI 0%や
-100%とせず「実購入データなし」と表示します。Coverage帯は0–1%、1–5%、5–10%、
10%以上で、Coverageは実際の当選確率ではない参考指標であることを画面へ明記します。
各Coverage帯は評価済み買い目5件未満を「データ不足」と表示します。
リーグ絞り込み時の買い目・金額・払戻は、そのリーグを含むrun全体の保存値です。混在runを
リーグ別に推測按分しません。

診断実行結果は次の専用実行時CSVへ原子的に保存します。Version8-A履歴とは別ファイルです。

```text
data/history/model_diagnostic_history.csv
```

診断日時、期間、リーグ、Version、run/開催/試合数、主要指標、異常、データ品質、総合状態、
使用閾値を保持し、画面からCSVを取得できます。詳細契約は
[`docs/version8b_model_diagnostics.md`](docs/version8b_model_diagnostics.md)を参照してください。

### Version8-Cとの境界と既知の制限

Version8-Bは診断、数値比較、警告までです。係数の変更値、設定変更方法、再最適化回数、
config.py変更、モデル自動採用、自動購入、AIによる改善提案は実装しません。これらは
Version8-Cの範囲です。

- 実戦履歴が26試合・2開催未満なら総合性能はデータ不足
- Version/リーグ/設定groupは期間が異なる場合があり、因果関係を示さない
- 不明リーグは全リーグ集計には含めるがJ1/J2/J3へ推測分類しない
- mini toto A/Bの払戻が保存されない場合、金額・ROIはN/A
- 初期閾値は説明可能な固定値で、統計的最適値として自動学習した値ではない
- 実行時履歴はGit管理外であり、別環境へ自動同期しない
- 個人用の単一Streamlit processを前提とし、複数process間lockは持たない

## Version7.5

Version7.5は新しい予測モデルや評価指標を追加するReleaseではありません。
Version7-CまでのP(1)・P(0)・P(2)、本命、期待得点、引分判定、買い目、Coverageを
変えずに、Version8以降を安全に開発するための総点検・安定化Releaseです。表示用の
アプリVersionと予測履歴のモデルVersionを分離しているため、過去の`Version6`、
`Version7-A`、`Version7-B`行は書き換えません。

実施内容は次のとおりです。

- Version7-C開始時点の確率・本命・買い目・口数・購入金額・Coverage・
  Draw Inclusionを回帰Fixtureへ固定
- 実結果の`1`／`0`／`2`変換を共通化し、数値化されたCSVの`0.0`も同じ定義で処理
- 予想履歴、開催回、買い目、バックテスト、払戻、Session Stateのデータ契約を文書化
- pandasの先頭位置取得を`.iloc`／`.iat`で監査し、label `0`へ依存しないことを回帰確認
- 古いSession Stateの不正な型、`None`、NaN、Infinity、範囲外値をwidget生成前に正規化
- Version7-Bの今後の通常予想に、当時の確率・Version・モデル係数・引分係数・
  画面スイッチ・引分候補情報・戦略バックテストcutoff適格性を一緒に保存
- CSV欠損・破損、履歴なし、払戻なし、未確定結果、公式取得失敗の既存fallbackを回帰確認
- 新規Python processのclean import、Streamlit実起動、AppTestによるtoto／mini A／mini B、
  手動変更、CSV、Version6／7-A／7-B戦略経路を確認

回帰Fixtureは`tests/fixtures/version75_regression_baseline.json`です。浮動小数は
絶対誤差`1e-12`、本命・区分・買い目は完全一致で比較します。予測式、Version7-Aの
引分式、Version7-Bの最適化式、Version7-Cの各Score・Coverage式は変更していません。
データ契約と監査判断は[`docs/version75_data_contracts.md`](docs/version75_data_contracts.md)と
[`docs/version75_audit.md`](docs/version75_audit.md)に記録しています。

### バックテストの安全条件

開催回の最初の試合日`00:00 JST`をcutoffとし、それより前に完了した試合だけから
順位相当値、勝点、得失点差、Elo、直近成績、会場別成績を再構成します。後日の順位、
シーズン終了順位、対象試合結果、将来の直近成績は入力しません。13試合すべての
公式`actual_result`が`1`・`0`・`2`で確認できた開催回だけを評価し、未確定回は
的中率・払戻・収支・ROIを0として扱わず「未評価」にします。

Version7-Aはこの時点再構成を使って確定回をオンデマンド生成できます。Version7-Bは
過去時点の採用係数を正確に復元できないため、当時保存された履歴がある場合だけ評価し、
現在設定による過去予想の捏造は行いません。Version7.5以後に保存するVersion7-B履歴は
設定Snapshotを持ち、cutoff以後に保存した新規予想は戦略バックテストから除外します。
Version7.5より前の欠損履歴を遡って補完はしません。

### Version7.5の既知の制限

- 公式サイト障害時の実データ取得可否は外部サービスに依存する
- Version7.5より前のVersion7-B履歴に当時の設定がなければ再現できない
- 過去履歴に引分候補情報がなければ、その情報だけは遡及復元しない
- mini toto払戻形式は保存していないため、mini A／Bの収支・ROIは推測しない
- Streamlit hot rerunで旧moduleが残る既知事象に対し、Version7-B入口だけは検証付き
  reloadを維持する。通常のclean importには循環依存がない

## Version7-C

Version7-Cの目的は、Version7-Aまたは採用済みVersion7-Bが通常予想で算出した
P(1)・P(0)・P(2)を、実際に入力しやすいtoto買い目へ変換することです。予測モデル、
期待得点、Elo、引分補正、Version7-BのBest Trial選定は変更せず、正規化済み確率を
再利用します。「予想」タブで13試合を予想した後、「買い目最適化」タブで実行します。

対応する対象は次の3種類です。

- `toto`：公式第1～13試合
- `mini toto A組`：toto公式第1～5試合
- `mini toto B組`：toto公式第6～10試合をmini toto側の第1～5試合として表示

各対象は独立したSession Stateを使うため、ダブル数、トリプル数、予算、引分候補
閾値をA組・B組それぞれに設定できます。画面設定は採用済みモデル設定やconfigへ
永続化しません。

### シングル、ダブル、トリプルと口数

各試合は、確率1位をシングル、確率上位2結果をダブル、`1・0・2`すべてを
トリプルの基本買い目とします。引分候補閾値は0を強制採用する閾値ではなく、
0が確率3位の場合に通常2位との入替価値を詳しく評価し始める閾値です。
P(0)が上位2結果に入る場合は従来どおりダブルに0を含めます。3位の場合は
P(0)、確率差、既存引分根拠、不確実性、Coverage低下を比較し、十分な根拠がある
場合だけ通常2位を0へ入れ替えます。シングルとトリプルの結果選択は変更しません。

ダブル数は0～5または任意値、トリプル数は0～3または任意値を指定できます。
任意値も対象試合数までで、ダブル数とトリプル数の合計が試合数を超える設定は
実行できません。同一試合をダブルとトリプルへ重複配置しません。

```text
総口数 = 2 ^ ダブル試合数 × 3 ^ トリプル試合数
購入金額 = 総口数 × 100円
```

| ダブル | トリプル | 口数 | 購入金額 |
|---:|---:|---:|---:|
| 0 | 0 | 1口 | 100円 |
| 1 | 0 | 2口 | 200円 |
| 3 | 0 | 8口 | 800円 |
| 2 | 1 | 12口 | 1,200円 |
| 3 | 1 | 24口 | 2,400円 |
| 0 | 1 | 3口 | 300円 |

設定変更時に口数と購入金額を即時表示します。予算は上限なし、500円、1,000円、
2,000円、3,000円、5,000円、10,000円または任意値です。予算超過時は警告しますが、
予算からダブル数・トリプル数を自動決定しません。

### Uncertainty Scoreと買い目配置

確率を大きい順に`q1 >= q2 >= q3`、正規化Entropyを`H`、P(0)を`p0`、
引分候補閾値を`θ`、`θe=max(θ, 10^-6)`とします。`clamp(x)`は0～1への制限です。

```text
H    = -Σ pi ln(pi) / ln(3)
C12  = 1 - clamp((q1 - q2) / 0.50)
C13  = 1 - clamp((q1 - q3) / (2/3))
Umax = clamp((1 - q1) / (2/3))
D    = 0.60 × clamp(p0 / θe)
     + 0.40 × (1 - clamp(|q1 - p0| / 0.25))

Uncertainty Score
 = 100 × (0.35H + 0.25C12 + 0.20Umax + 0.10D + 0.10C13)

Double Candidate Score
 = 100 × (0.40C12 + 0.25H + 0.15Umax + 0.10D
          + 0.10 × clamp(q2 / 0.50))

Triple Candidate Score
 = 100 × (0.30H + 0.20C13 + 0.20Umax + 0.15C12 + 0.10D
          + 0.05 × clamp(q3 / (1/3)))
```

Version7-A／7-Bで既に引分候補になり、かつP(0)が`min(20%, θ)`以上なら、
引分Signal `D`の下限を0.75とします。これはダブルに0を強制する処理ではなく、
保険を置く価値とシングル信頼度の補助信号です。初期閾値は現在のVersion7-A／7-B
採用設定の`candidate_threshold`を使用し、画面だけで変更できます。

指定されたダブル数・トリプル数は、単純な個別順位の切取りではなく、
`Double Candidate Score`と`Triple Candidate Score`の合計が最大になる重複なし配置を
動的計画法で同時に求めます。計算量は試合数×ダブル数×トリプル数に比例するため、
全買い目組み合わせを総当たりせず13試合でも安定して配置できます。

### ダブルの2結果選択とDraw Inclusion Score

Version7-Cでは、次の2段階を分離します。

1. `Double Candidate Score`で、指定数のうち「どの試合をダブルにするか」を決める
2. ダブルになった試合で、「どの2結果を買うか」を決める

今回のDraw Inclusion判定は2だけに使用し、1の動的計画法、ダブル数、トリプル数、
シングル、トリプルを変更しません。基本は確率上位2結果です。0が確率3位かつ
`P(0) >= 引分候補閾値`のときだけ、通常2位と0を次のScoreで比較します。

確率1位を`q1`、0以外の通常2位確率を`q2`、入替時のCoverage低下を
`L = q2 - p0`、既存の正規化Entropyを`H`、既存引分Signalを`D`とします。
Version7-A／7-Bの`draw_candidate`が通常予想から渡された場合を`M7=1`、
それ以外を`M7=0`とします。

```text
P = clamp(p0 / (1/3))
T = clamp((p0 - θ) / 0.10)
R = 1 - clamp(L / 0.10)
C = 1 - clamp((q1 - p0) / 0.25)
M = max(D, M7)

Draw Inclusion Score
 = 100 × (0.20P + 0.15T + 0.25R
          + 0.15C + 0.15H + 0.10M)
```

Scoreが60点以上、かつCoverage低下`L`が10ポイント未満の場合だけ0へ入れ替えます。
10ポイント以上失う場合は、ほかの根拠が強くても上位2結果を維持します。閾値未満、
Score不足、Coverage低下上限のいずれかなら通常上位2を維持します。これは選択用の
比較Scoreであり、Version7-A／7-BのP(1)・P(0)・P(2)を書き換える補正ではありません。

Elo差、期待得点差、チーム・直近引分率、ロースコア傾向などは、Version7-A／7-Bが
最終P(0)を算出する時点ですでに反映されています。Version7-Cで同じ特徴量を重ねて
確率を二重補正せず、最終P(0)、既存引分Signal、既存候補フラグを再利用します。
結果表と試合別CSVにはDraw Inclusion Score、判定、0入替時のCoverage低下、
0を採用した理由または上位2を維持した理由を表示します。

### シングル信頼度と引分候補

シングル信頼度は高・中・低の3段階です。最大確率だけの固定閾値にはせず、最大確率、
1位と2位の差、分布の集中度、引分リスクを合成します。

```text
最大確信度 = clamp((q1 - 1/3) / (2/3))
差の確信度 = clamp((q1 - q2) / 0.35)
分布確信度 = 1 - H
引分安全度 = 1 - D  （本命が0の場合は1）

Single Confidence Score
 = 100 × (0.40×最大確信度 + 0.30×差の確信度
          + 0.20×分布確信度 + 0.10×引分安全度)
```

60点以上を高、35点以上60点未満を中、35点未満を低とします。引分候補は、P(0)が
閾値以上、P(0)と1位の差が既存`candidate_margin`以内、または既存モデル候補信号を
満たす場合に表示します。0の購入を義務化する判定ではありません。特にダブルでは、
引分候補表示と0採用を分け、0が3位なら上記Draw Inclusion Scoreを通過した場合だけ
通常2位と入れ替えます。

### Coverage、手動調整、CSV

試合別Coverageは選択した結果のモデル確率合計です。例えばP(1)=45%、P(0)=35%、
P(2)=20%なら、シングル1は45%、ダブル1・0は80%、トリプルは100%です。

```text
試合別Coverage = Σ 選択した結果のモデル確率
開催回推定Coverage = Π 各試合のCoverage
```

開催回推定Coverageは「各試合を独立と仮定した参考値」として表示し、絶対的な
実当選確率とは表示しません。

AI提案後は試合ごとに区分と1・0・2を手動変更できます。変更後の区分、口数、
購入金額、試合別Coverage、開催回推定Coverageを再計算し、AI案を強制しません。
試合別CSVは口数にかかわらず保存できます。全組み合わせは200口まで画面表示、
100,000口までCSV保存し、それを超える場合は組み合わせ爆発を避けて警告します。

### 買い目戦略バックテスト

保存済み予想履歴に実結果が揃う開催回では、同じ過去確率を使って次を比較できます。

- A：全試合シングル
- B：指定数ダブル
- C：指定数ダブル＋トリプル

各戦略について、ダブル数、トリプル数、1回・合計口数、購入金額、全試合的中回数、
全試合的中率を表示します。totoの全評価回について保存済み公式1～3等金を取得できた
場合だけ、各買い目に含まれる13・12・11的中券数へ対応する公式金額を掛け、払戻金、
収支、ROIを算出します。的中券数は全組み合わせを展開せず、試合ごとに集計します。
現行保存形式にmini toto A／Bの1等金はないため、mini totoの払戻金・収支・ROIは
「払戻データなし」とし、推測しません。

Version7-Aを選んだ場合は、事前に分析タブで履歴を作る必要はありません。Version7-C
画面の「買い目戦略をバックテスト」1回で、次を順に実行します。

1. 保存済みVersion6の確定回を優先して、Version7-Aが不足する開催回を抽出する
2. 履歴CSV自体が空なら、公式一覧から直近の確定済みJリーグtotoを最大3開催回取得する
3. 各開催回の開催初日`00:00 JST`より前の完了試合だけでVersion7-Aを13試合再計算する
4. `toto_round`、試合番号1～13、Version、1・0・2確率、公式実結果を履歴へ保存する
5. 保存直後の履歴で戦略A／B／Cを比較する

生成中は画面へ進捗を表示し、完了後は対象開催回数、生成開催回数、生成試合数、
`actual_result`紐付け件数を表示します。同じ開催回・Version・試合番号が既に13件
揃っている場合は再生成しません。保存処理も同じ3項目をキーに置換するため、再実行で
重複行を増やしません。Streamlitの一時ファイルが消失しても、同じ操作で公式データと
同梱履歴から再生成できます。

過去時点の計算は既存のVersion7-A経路へ集約しています。後日の試合結果、順位、勝点、
シーズン集計、Elo、直近成績は、開催初日`00:00 JST`で切り捨ててから入力を再構成します。
Version7-C内に予測式を複製せず、P(1)・P(0)・P(2)も買い目評価側では変更しません。

Version7-Bは当時採用されていた最適パラメータを現在から正確に復元できないため、
オンデマンド再生成しません。当時保存された13試合のVersion7-B予測がある開催回だけ、
公式実結果をreconcileして評価します。保存履歴がない場合は画面に
「Version7-Bは当時保存された予測履歴が必要です」と表示します。Version7-Cは引き続き
買い目戦略レイヤーであり、`prediction_version = Version7-C`の履歴行は生成しません。

### 既知の制限と今後の境界

- モデル確率間の試合独立を仮定したCoverageであり、同節の相関は表現しない
- 予算超過警告は行うが、予算から区分数を自動決定しない
- toto投票率、期待値、配当妙味、投票率との乖離は最適化に使用しない
- Draw Inclusion Scoreの重み・60点基準・Coverage低下上限は固定値であり、
  十分な確定結果標本による実績校正は未完了
- Version7.5より前の過去予想履歴は当時の`draw_candidate`と候補理由を保存して
  いないため、欠損行では最終確率・現在指定した閾値・連続引分Signalを使い、
  当時の候補フラグ加点は遡及復元しない。Version7.5以後の新規履歴は保存する
- mini totoの対象は現行toto公式順のA＝1～5、B＝6～10を使用する
- 取消・中止・くじ不成立時の購入判断や返還処理は扱わない
- 自動購入、外部サイトログイン、決済、購入ボタン操作は実装しない

投票率を使う期待値最適化、予算からの区分数自動探索、試合間相関、より高度な
戦略安定性評価は今後の拡張対象です。

### Version8への引継ぎ

Version7.5で固定した回帰Fixtureとデータ契約を、Version8の変更判定に使用します。
Version8で係数・特徴量・予測結果を変える場合は、安定化変更とモデル変更を同じCommitへ
混在させず、変更理由とValidation差を明示します。mini toto払戻形式、試合間相関、
投票率を使う期待値最適化は引き続き未実装です。

## Version7-B

Version7-Bの目的は、Version7-Aで追加した引分予測を維持しながら、既存の
統計モデル全体が未知データで安定する係数を探すことです。バックテスト的中率だけを
最大化するのではなく、1・0・2の確率品質、引分性能、Training／Validation差、
シーズン・リーグ間のばらつきをまとめて評価します。機械学習モデル、ニューラル
ネットワーク、LLM予測、販売、ログイン、決済、マルチユーザー機能は追加しません。

Version7-Aが引分関連10係数だけを小規模探索するのに対し、Version7-Bはホーム補正、
Elo補正率・ホームアドバンテージ・K係数、直近5試合重み、直近／シーズン平均の
混合率、ホーム／アウェイ成績混合率、順位・勝点・得失点差補正率、期待得点の
上下限を対象にします。これらは画面用の見せかけの値ではなく、Elo再計算、
期待得点、各補正処理へTrialごとに実際に渡されます。初期値はVersion7-Aまでの
予測を再現します。

「引分パラメータを探索対象に含める」をOFFにすると、Version7-Aの現在採用設定を
固定したまま全体係数だけを探索します。ONにすると`base_draw_logit_bias`、
`poisson_draw_weight`、近さ・引分率・ロースコア等の重み、`candidate_threshold`、
`candidate_margin`も同じ候補へ含めます。OFFの候補を採用してもVersion7-Aの
引分設定ファイルは上書きしません。

### 探索方式、Trial数、モデル上限

「モデル最適化」タブで次の4方式を選択できます。初期値はOptuna、Trial数は100、
乱数seedは`20260808`です。

- **Optuna（ベイズ最適化）**：TPE samplerで有望な範囲を順次探索
- **ランダムサーチ**：同一seedなら同一候補列を可能な限り再現
- **グリッドサーチ**：実行前に全組み合わせを数え、上限超過時は実行を拒否。
  明示的に制限実行を選んだ場合だけ、Trial数とモデル上限内へ切り詰める
- **2段階探索**：前半約70%をOptunaで広く探索し、上位3候補のホーム補正、
  Elo補正率、会場別混合率の周辺を狭いGridで再探索

Trial数は10、30、50、100、300、500、1,000、3,000、5,000、10,000または
任意値を選べます。モデル上限は簡易1,000、標準10,000、詳細50,000または
任意値です。標準Gridは4,096組、引分係数を含むGridは4,194,304組になるため、
無制限実行しません。画面には予定組み合わせ数、設定上限、拒否または制限理由を
表示します。

Trial数を増やしても性能向上は保証されません。候補数、開催回数、ブートストラップ
回数にほぼ比例して処理時間が増え、1,000 Trial以上や10,000回Bootstrapは
長時間になる可能性があります。まず10～100 Trialでデータ分離と評価結果を確認し、
必要な場合だけ増やしてください。

### Training／Validation、ウォークフォワード、未来データ禁止

初期検証方式は「シーズン単位ウォークフォワード」です。固定Training／Validation
分割、シーズン単位ウォークフォワード、開催回単位ウォークフォワードから選べます。
探索順位はTraining内の時系列FoldのValidationで決めます。各Foldを同じ重みで平均し、
Fold Scoreの標準偏差とMean−Worst差を安定性へ反映します。シーズン方式で利用可能な
シーズンがN件なら、最新1シーズンをFinal Validation、最初の1シーズンを初期学習期間
とし、Training内部Fold数はN−2件です。5シーズンなら3 Foldです。
最終Validationは順位とBest Trialの確定後に、Version7-AとBest候補だけへ使用します。
同じ試合をパラメータ選択と最終評価の両方には使いません。

各開催回のcutoffは最初の試合日の`00:00 JST`です。直近成績、会場別成績、
シーズン集計、順位相当値、勝点、得失点差、Elo、引分特徴量は、必ずcutoffより前に
完了した試合から再構成します。対象試合結果、後日の試合、現在順位、最終順位・
最終勝点などのシーズン終了値は過去予測へ持ち込みません。

バックテスト期間は直近3／5／10シーズンまたは任意期間、対象リーグはJ1／J2／J3／
全リーグです。初期値は直近5シーズン・全リーグです。保存・取得できたデータだけを
使い、存在しない年は生成しません。画面には要求期間とは別に実際の使用期間、
Training期間、Validation期間、試合数、実際のFold数、データ不足リーグを表示します。3シーズン未満
ではシーズンウォークフォワードへ勝手に代替せず、成立条件と利用可能シーズンを
エラー表示します。全リーグ選択時もJ1・J2・J3別のScoreを安定性表で確認できます。

### 総合Scoreと評価指標

初期重みはBrier Score 30%、Log Loss 20%、Calibration 15%、全体的中率15%、
引分性能10%、Walk Forward安定性10%です。画面で変更でき、合計100%でない場合は
警告して実行を停止します。内部では各指標を0～1の品質へ正規化してから0～100の
総合Scoreへ変換します。小さいほど良いBrier、Log Loss、Calibrationと、大きいほど
良い的中率・引分性能を同じ向きへ揃えるため、異なる単位の生値は加算しません。

引分性能はVersion7-Aの引分Precision、Recall、F1、Brier、Calibrationを全て使用
します。各モデルについて、総合Score、Brier、Log Loss、Calibration、全体的中率、
試合数、1／0／2の予測数・予測率・クラス別的中率・平均予測確率・実発生率を表示
します。ROIは13試合の公式順・実結果・必要な公式配当が揃う場合だけ計算し、不足時は
推測せず「算出不可」とします。

### 引分性能保護、過学習、安定性

候補は同じValidation上のVersion7-Aと比較します。引分F1、Recallの低下、引分Brier、
Calibrationの増加が許容幅を超えると「引分性能悪化」とし、探索Scoreへ段階的な
ペナルティを付けます。許容幅は`model_config.py`の
`VERSION7B_DRAW_DEGRADATION_TOLERANCES`で変更できます。固定値だけで候補を隠さず、
悪化理由と実数値をランキング・比較表へ残します。

TrainingとValidationは、総合Score、Brier、Log Loss、Calibration、全体的中率、
引分F1、引分Brier、引分Calibrationを別表示します。設定閾値を超える差は
「過学習の可能性」と表示します。閾値は`VERSION7B_OVERFIT_THRESHOLDS`で管理します。
最終候補ではTraining Mean Score、Robust Training Score、Fold標準偏差、Worst Fold、
Mean−Worst差、安定性ペナルティ、Training内引分悪化ペナルティ、Optuna objectiveに加え、
Best決定後のシーズン別Score、リーグ別Score、Training／Validation差、Bootstrap
信頼区間、Trial間のパラメータ重要度を確認できます。個別指標のFold分散は総合Scoreと
二重評価になるため重ねず、Brier・Log Loss・的中率・引分性能を含むFold Scoreで
安定性を評価します。

各FoldのBrier・Log Loss・Calibration・全体的中率・引分性能を0～100へ正規化した
性能Scoreを算出し、試合数に関係なくFoldを等重みで平均した値を`Training Mean Score`
とします。安定性品質Q、Robust Training Score R、Optuna objective Oは次です。

```text
Q = clamp(1 − Fold標準偏差 / 20 − (Training Mean − Worst Fold) / 40, 0, 1)
R = (1 − 安定性重み) × Training Mean + 100 × 安定性重み × Q
安定性Penalty = 100 × 安定性重み × (1 − Q)
O = max(0, R − Training内引分悪化Penalty)
```

既定の安定性重み10%では、標準偏差とMean−Worst差の実質係数は0.5、0.25です。
これは今回のFinal Validation結果から逆算した値ではなく、既存の安定性重み10%と
Score尺度20点／40点から導かれます。尺度・警告閾値・安定性判定に必要な最小Fold数は
`model_config.py`の`VERSION7B_ROBUST_SELECTION_SETTINGS`で管理します。

1 Foldでは期間間の標準偏差を評価できないため安定性Penaltyは0点とし、画面には
「安定性判定不可（1 Fold）」と表示します。評価試合0件のFoldは黙って除外せず、
対象リーグまたは期間を確認するエラーで停止します。

画面の`Training全体Score`はTraining全行を連結した参考値で、Optuna objectiveとは
異なります。Best TrialはOの降順で決め、完全同点時だけWorst Foldの高い順、
Fold標準偏差の小さい順、Brierの小さい順、Trial番号の小さい順を使います。
Final ValidationのScore、過学習判定、Version7-A比較はこの順位条件に含めません。

### ランキング、Bootstrap、比較グラフ

探索後はTraining内Walk Forward順の上位20モデルを表示・専用CSVへ保存します。
各行にTraining Mean、Robust Training、標準偏差、Worst Fold、Mean−Worst、
安定性／引分ペナルティ、Optuna objective、Training全体Score、全体指標、
引分5指標、1／0／2的中率・予測率、全パラメータを残します。最終ValidationはBest行だけに
表示し、その値で探索順位を後から並べ替えません。

全Trialの各Foldについて、対象期間、開催回数、試合数、総合Score、Brier、Log Loss、
Calibration、全体的中率、1／0／2的中率、引分Precision／Recall／F1を画面からCSVで
保存できます。これにより、Bestになった理由をFinal Validationを参照せず再計算できます。

再評価はなし、1,000回、10,000回または任意回数を選べ、Training内で選定済みの
Best候補だけを最終Validationの試合単位Bootstrapで復元抽出します。Brier、Log Loss、Calibration、全体的中率、引分F1の
平均、中央値、標準偏差、95%信頼区間を算出します。同じデータ・設定・seedなら
可能な限り再現します。決定論的な同じバックテストを単純に何千回も繰り返す処理
ではありません。

グラフはTrialごとのOptuna objective、Robust Training、Training Mean、
Fold標準偏差・Worst Fold・各ペナルティ、
Brier、Log Loss、Calibration、全体的中率、引分F1、Training全体との比較、
パラメータ重要度を表示します。加えて
シーズン別・リーグ別ScoreとBootstrap表を表示します。現在のVersion7-Aと候補は
同じ最終Validationで13指標を「Version7-A／Version7-B候補／差／改善・悪化」として
比較し、Training→ValidationのScore低下量も表示します。総合Score、Brier、
Log Loss、Calibration、的中率をすべて上回り、過学習・引分悪化がない場合だけ
「Version7-B採用候補」、それ以外は「Version7-A継続推奨」と表示します。

### 保存、採用、バックアップ

Trialは完了直後に`data/history/version7b_partial_trials.csv`へ逐次保存します。
全Trial・全Fold明細は`version7b_fold_metrics.csv`へ逐次保存します。
上位20モデルは`version7b_model_ranking.csv`、実行単位の設定・期間・seed・指標・
最良係数・判定・採用有無は`version7b_optimization_history.csv`へ保存し、既存の
予想履歴やVersion7-A履歴とは混在させません。破損した列構成を検出した場合は
黙って上書きせず、画面へ理由を表示します。過去の最適化履歴は同じタブで確認できます。
同じデータ・設定・seedを再実行してもRun IDは実行ごとに一意、設定Fingerprintは同一に
なります。認識済みの旧Version7-B列は空の新列を補って移行し、旧100 Trialを削除・置換
しません。同一Run IDを再保存した場合だけ重複防止のため同じ実行行を置換します。

最適化完了だけでは本番設定を変更しません。「Version7-B最適設定を採用しますか？」
で`YES`を押した場合だけ`data/config/version7b_model_settings.json`を原子的に更新し、
その前に`data/config/version7b_backups/`へ直前設定を保存します。`NO`は無変更です。
採用前に現在／候補設定、変更箇所、Training／Validation、Version7-Aとの差、
過学習・引分悪化を確認でき、「直前設定へ戻す」で検証済みバックアップを復元します。
採用済みVersion7-BのP(1)・P(0)・P(2)は既存の正規化済み予測結果として公開され、
将来のVersion7-Cから安全に再利用できます。

### 既知の制限とVersion7-Cとの境界

Streamlitの同期実行では、ページ再読込をまたぐ確実な停止・完全自動再開を保証できません。
不安定なバックグラウンド処理は追加せず、各Trialの逐次保存と過去履歴確認を優先します。
途中CSVから残りTrialだけを自動継続する機能は未実装です。Bootstrap信頼区間と過去
Validation改善は将来成績を保証しません。パラメータ重要度は探索Trialとの単変量相関に
基づく診断値で、因果効果ではありません。利用可能な確定済み開催回が少ないリーグ・
シーズンでは警告を確認してください。Training内部Foldが安定していても、制度変更、
カテゴリ構成、対戦分布などFinal Holdout側の分布変化までは保証できません。

Version7-Cのtoto買い目最適化、シングル／ダブル／トリプル配置、予算警告、
CoverageはVersion7-Bから分離しています。Version7-Bは予測モデルそのものの
係数最適化までを責務とし、Version7-Cはその確率出力を読み取るだけです。

## Version7-A

Version7-Aは、Version6で本命「0」がほとんど選ばれなかった状態を分析し、
引分を独立したクラスとして評価・補正する小規模な改善段階です。Version1～6の
期待得点、Elo、会場別、順位等、バックテスト、履歴、分析、確率指標、ROIは残し、
その上へ引分専用モデルを重ねています。

採用設定がない初期状態は、全係数0・Poisson重み1.0です。この状態では
Version7-Aの1・0・2確率と本命はVersion6を再現します。最適化結果は自動採用せず、
画面で`YES`を押した場合だけ通常予想へ反映します。

### 現状調査と修正

- Version6の引分確率は残差ではなく、Poissonで計算した`0-0`～`6-6`の対角確率を
  直接合計し、1・0・2を正規化していた
- Version6の本命判定は`1`、`0`、`2`の3候補に対する最大確率で、`0`は
  `argmax`から除外されていなかった
- 予想スコアは最頻の単一スコア、本命は全スコアを集約した3結果確率の最大値で
  あるため、`1-1`でも本命が`1`または`2`になること自体は不整合ではない
- 保存開催回CSVで未確定の空欄と実結果が混在すると、pandasが`0`を`0.0`として
  読み、文字列比較で結果が無効化される不具合を修正した。`1.0`、`0.0`、`2.0`も
  必ず正規ラベルへ戻し、数値0をFalseや欠損として扱わない
- 過去バックテストの順位カテゴリーで、当時の公式履歴より現在所属カテゴリーを
  優先していた箇所を修正した。昇降格後のカテゴリーを過去時点へ持ち込まない

調査上、Version6で引分本命が少ない主因は、Poissonの引分確率が有効な候補では
あってもホーム勝ち・アウェイ勝ちの集約確率を上回りにくいモデル特性でした。
加えて、上記CSV読込不具合はバックテスト結果の保存・再読込を妨げていました。

### 3クラス確率と引分補正

最終確率は`P(1)`、`P(0)`、`P(2)`の3クラスとして保持します。入力に欠損、NaN、
Infinity、負数があっても有限・非負へ補正し、内部は合計1.0、画面の小数1桁表示も
最大剰余法で合計100.0%にします。

引分の基準確率はVersion6のPoisson対角和です。引分確率を
`1 - P(1) - P(2)`だけで作ることはありません。補正後の引分確率を決めた後、
残り確率をVersion6のホーム勝ち／アウェイ勝ちの相対比に従って配分するため、
既存の勝敗方向を維持します。本命は補正・正規化後の3確率の最大値です。

補正には、対象試合開始前に既存データから確認できる次の要素を使います。

- Poisson引分確率、期待得点差、Elo差
- 両チームの直近・シーズン平均得点と平均失点
- チーム別、ホーム／アウェイ別、直近5試合の引分率
- リーグ別・シーズン別引分率
- Poissonと過去実績の0-0、1-1、ロースコア傾向
- 順位差、1試合平均勝点差、1試合平均得失点差

差が小さいほど引分を上げ、差が大きいほど下げられる連続量をlog-odds上で
組み合わせます。一律`+10%`の固定判定ではなく、各係数を小規模探索対象にします。
リーグ別標本が30試合未満の場合はJ1～J3全体へ安全にフォールバックし、存在しない
外部情報を推測で補いません。

### 引分候補

本命とは別に、次のいずれかで「引分候補」を表示します。

- 引分確率が3結果中最大
- 引分確率が設定閾値以上
- 1位確率と引分確率との差が設定値以内

通常予想画面の閾値は20%、25%、30%、35%、40%または任意値から選択できます。
候補数そのものは最適化Scoreに加点せず、候補PrecisionとRecallのF1だけを小さく
評価します。

### 引分専用評価とグラフ

「引分分析」タブは、TrainingとValidationの両方についてVersion6とVersion7-Aを
同じ試合で比較します。

- 実際の引分試合数、引分予測試合数、引分的中数
- 引分Precision：引分本命のうち実際に引分だった割合
- 引分Recall：実際の引分のうち引分本命で拾えた割合
- 引分F1：PrecisionとRecallの調和平均
- 引分Brier：引分か否かの二値Brier Score
- 引分Calibration：引分確率と実際の引分率の加重絶対差
- 引分予測時の平均確率、全試合の実際の引分率、引分予測率
- 1・0・2別正答率、全体的中率、全体Brier、Log Loss、Calibration

Precision、Recall、F1は分母0件の場合に0.0を返します。引分確率帯は
`0–20%`、`20–25%`、`25–30%`、`30–35%`、`35–40%`、`40%以上`の6区分で、
試合数、平均予測確率、実引分数、実引分率、Calibration差を表示します。
グラフは1／0／2別成績、引分Calibration、確率帯別実引分率、Trial Score推移の
4種類だけです。

### Training／Validationと未来データ禁止

開催回はランダムシャッフルせず、シーズン単位の分離を優先します。画面の既定は
2024～2025年をTraining、最新年をValidationとし、シーズンが1つしかない場合だけ
時系列の過去80%／直近20%へ分割します。Training最終日時がValidation開始日時
以後になる指定は拒否します。

各開催回では最初の試合日の`00:00 JST`をcutoffとし、それより前に確定した
試合だけから、直近5試合、シーズン・会場別成績、順位相当値、Elo、引分率、
0-0・1-1・ロースコア率を再構成します。現在順位、シーズン終了時の最終勝点、
未来の試合結果・Elo・直近成績は使用しません。OptunaはTrainingだけでTrialを
探索し、最良設定を確定した後にValidationを評価します。

### Optuna小規模探索とScore

Trial数は10、30、50、100または任意値を選べ、初期値は30です。100超も入力
できますが、Version7-Aの基本運用は最大100 Trialと表示します。探索対象は次の
引分関連10項目だけです。

- 基本引分logit補正、Poisson引分確率の重み
- Elo差、期待得点差、チーム引分率、直近引分率の重み
- ロースコア、順位・勝点・得失点差の近さの重み
- 引分候補閾値、1位確率との差の閾値

Scoreは、全体Brier、Log Loss、Calibration、的中率、引分F1、引分Brier、
引分Calibration、引分候補F1を0～100へまとめます。Version6比で全体確率品質や
的中率が許容幅を超えて悪化したTrialへペナルティを付け、引分F1だけを上げる
設定を優先しません。乱数seedは固定・保存します。Optunaが未インストールの場合は
`requirements.txt`からの導入案内を表示し、通常予想は停止しません。

### 保存、採用、復元

最適化実行結果は既存CSVと分離して
`data/history/version7a_optimization_history.csv`へ追記します。実行日時、Version、
Trial数、Training／Validation期間・試合数、Best Trial、Best Score、最適係数、
全体・引分指標、Version6比較、乱数seedを保存します。列が壊れた既存CSVは
上書きせず、画面に保存失敗を表示します。

「Version7-A最適設定を採用しますか？」で`YES`を押した場合だけ
`data/config/version7a_draw_settings.json`を更新します。`NO`はファイルを変更しません。
採用前設定は`data/config/version7a_backups/`へ保存し、「直前設定へ戻す」で復元
できます。最適化完了、CSV保存、画面表示だけでは通常予想の設定は変わりません。

### 既知の制限とVersion7-Bへの引継ぎ

Version7-Aは少数開催回・最大100 Trial程度を前提とするため、Validationの標本差や
過学習の影響を受けます。Validationの改善は将来試合での改善を保証しません。
負傷者、出場停止、予想先発など既存データにない情報は使いません。

Version7-Bでは、Version7-Aの引分設定を固定または探索対象として選び、既存モデル
全体のRandom／Grid／2段階探索、最大50,000モデルの上限管理、ウォークフォワード、
Bootstrap、95%信頼区間、安定性分析、上位20ランキングを追加しました。
買い目最適化はVersion7-Cの範囲です。AI自動改善提案、負傷者・出場停止、note連携も
引き続き対象外です。

## Version6

Version6はVersion5の予測式を変更せず、将来のモデル最適化と重み自動調整に
必要な検証・分析基盤を追加します。そのため同じ入力に対するVersion5と
Version6の本命、確率、期待得点は同値です。Version7以降でモデルを変更した際に、
保存済み履歴と同じ条件で差を比較できます。

### toto公式試合順と開催回

- スポーツくじ公式の「第1試合」～「第13試合」を唯一の表示順に使用
- 開催回、試合番号、ホーム、アウェイ、試合日時、実結果、公式配当を保持
- 公式の略称は`teams.py`の`normalize_team_name()`で既存クラブ名へ正規化
- 入力、本命、予想一覧、試合詳細、Version比較、CSV、履歴を同じ順に統一
- 将来のnote出力も`TotoRound.matches`の公式試合番号順を再利用可能
- 開催回を履歴、バックテスト、分析のキーとして使用

### 過去開催回とバックテスト

分析タブの「直近1年以上の開催回一覧を取得」で、既定では現在年と前年の
toto結果一覧を取得します。選択した開催回について、対象13試合、実結果、
1～3等の公式当せん金を取得します。2026年8月1日の実接続確認では、
2025年と2026年の計121開催回を一覧化できました。詳細データは選択時に取得し、
公式コンテンツをリポジトリへ同梱・再配布しません。

バックテストの基準時刻は、対象開催回の最初の試合日の`00:00 JST`です。
次のデータを基準時刻より前の完了試合だけから再構成します。

- 直近5試合と時系列加重平均
- シーズン成績、順位、勝点、得失点差
- ホーム／アウェイ別成績
- Eloレーティング

開催当日を含む基準時刻以後の試合結果、現在の順位表、現在のクラブ成績、
現在のEloは入力しません。Jリーグ公式の日程・結果を過去年度単位で取得し、
日時で切り捨てた後にVersion5パイプラインを実行します。

### 予想履歴と分析

`data/history/prediction_history.csv`へ、1試合・1Versionを1行として保存します。
同じ開催回・試合番号・Versionを再実行した場合は最新行へ置き換え、分析での
二重計上を防ぎます。確定済み開催回のバックテストでは、Version4～Version6に
加えて、同じ開催初日`00:00 JST`以前のデータと現在採用中の引分設定で
Version7-Aも13行保存します。同じ開催回に通常予想で保存済みのVersion7-Bがあれば、
公式実結果を全13行へ照合します。Version7-Cは予測確率を作るVersionではないため、
専用履歴行を増やさず、保存されたVersion7-AまたはVersion7-Bの確率を買い目戦略
バックテストへ使用します。Version7-C画面からVersion7-Aを選んだ場合も、不足する
確定回を同じ過去時点経路でオンデマンド生成してから戦略比較します。分析タブでは次を
表示します。

- 開催回一覧、13試合的中数、全体的中率、累積開催数、累積的中率
- Version4・Version5・Version6・Version7-Aの本命、勝率、期待得点
- 1・0・2別正答率と推移
- ホーム・引分・アウェイの予測割合と実結果割合
- 開催回別的中数、累積的中率、Version比較のグラフ
- Brier Score、Log Loss、Calibration、的中期待値、ROI

### 評価指標の定義

- **的中率**：本命が実結果と一致した試合数 ÷ 実結果がある試合数
- **1・0・2別正答率**：実結果が各ラベルだった試合のうち、本命も同じだった割合
- **Brier Score**：1・0・2の多クラス確率について、正解one-hotとの差の二乗和を
  試合平均した値。範囲は0～2で、小さいほど良い
- **Log Loss**：実結果へ割り当てた確率の負対数平均。小さいほど良い
- **Calibration**：本命確率を10区間に分け、平均予測確率と実際の的中率の差を
  試合数で加重したECE。小さいほど確率の信頼性が高い
- **ROI**：各Versionの本命13個をtotoシングル1口100円で毎開催購入した想定の
  `公式払戻額 ÷ 購入額 × 100`。13・12・11的中時だけ各開催回の公式1～3等
  当せん金を使い、それ未満は払戻0円として累積する
- **的中期待値**：13試合それぞれの本命確率の合計。Version5との比較に使用

ROIは過去データの機械的な検証値であり、購入や利益を推奨・保証するものでは
ありません。実結果が未確定の開催回は確率指標とROIの集計対象外です。

過去Jリーグ履歴は、公式ページ、実行時保存CSV、同梱した2024～2025年の
公式結果CSV、現在取得済みデータの順で読み込みます。公式サイトへの接続が
一時的に失敗してもバックテストを継続でき、基準日前の完了試合が0件の場合は
初期値で評価を作らず、分析タブ内にエラーを表示します。

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

2026年8月1日現在、次のスポーツくじ公式・Jリーグ公式公開ページを使用しています。

### toto開催回・公式試合順・実結果

- [スポーツくじ公式 totoくじ情報](https://store.toto-dream.com/dcs/subos/screen/pi01/spin000/PGSPIN00001DisptotoLotInfo.form)
- [スポーツくじ公式 totoくじ結果一覧](https://store.toto-dream.com/dcs/subos/screen/pi04/spin011/PGSPIN01101InitLotResultLsttoto.form)
- 現在の開催回ページ：開催回、販売期間、結果発表日、第1～13試合、ホーム、
  アウェイ、開催日、開始予定時刻、競技場
- 過去結果ページ：開催回、第1～13試合、実結果、スコア、1～3等当せん金
- 年度別結果一覧：現在年と前年を既定取得範囲とし、複数年を指定可能

HTML解析は`history_manager.py`だけで行い、他のモジュールは開催回と公式試合番号を
持つ共通オブジェクトを使用します。

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

### toto開催回・予想履歴

- Streamlitメモリキャッシュ：6時間
- 開催回保存CSV：`data/cache/toto_rounds.csv`
- 予想履歴CSV：`data/history/prediction_history.csv`
- 開催回CSVは公式取得に成功した13試合を開催回単位で置換保存
- 予想履歴は開催回・公式試合番号・Versionを複合キーとして置換保存
- 過去開催回の実結果を取得した時点で、的中数と全評価指標を再計算

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

toto開催回は次の順でフォールバックし、取得失敗だけでアプリを停止しません。

1. スポーツくじ公式の開催回・13試合
2. `data/cache/toto_rounds.csv`の保存済み開催回
3. 起動時に取得済みの現在のJリーグ試合データ
4. 画面へ取得エラーを表示し、13試合の手入力を継続

過去開催回はスポーツくじ公式の結果ページ、保存済み開催回CSVの順です。
バックテスト用Jリーグ履歴は公式取得済み履歴へフォールバックします。それでも
基準日時点の履歴を用意できない場合は、その開催回だけエラー表示し、予想画面と
保存済み分析は継続します。

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
- `model_config.py`：Elo、Version5、Version7-A／7-B、キャッシュの設定値
- `config.py`：Version4までのimport互換用ブリッジ
- `elo_rating.py`：Elo計算、期待得点補正、Eloキャッシュ
- `form_adjuster.py`：直近5試合の時系列重み付け
- `venue_adjuster.py`：ホーム／アウェイ別成績の混合
- `standings_adjuster.py`：勝点・得失点差補正
- `model_pipeline.py`：Version4比較とVersion5補正順序の管理
- `data_loader.py`：公式試合・順位表・クラブ統計の取得とキャッシュ
- `history_manager.py`：toto公式開催回・第1～13試合・実結果・保存CSV
- `prediction_history.py`：開催回・Version別の予想履歴CSV
- `backtest.py`：開催日時点のデータ再構成とVersion4～Version6再実行
- `metrics.py`：的中率、Brier Score、Log Loss、Calibration、ROI
- `analysis.py`：開催回集計、Version比較、Version7-A履歴の個別／オンデマンド生成、分析タブ
- `draw_predictor.py`：引分特徴量、3クラス正規化、引分補正・候補判定
- `draw_evaluation.py`：引分専用指標、確率帯評価、複合Score
- `draw_optimizer.py`：時系列分割、Optuna小規模探索、保存・採用・復元
- `draw_analysis.py`：Version7-A比較表、4グラフ、最適化UI
- `parameter_manager.py`：Version7-B候補、採用設定、バックアップ・復元
- `walk_forward_validator.py`：固定／シーズン／開催回の時系列分割
- `model_evaluation.py`：正規化Score、引分保護、過学習・安定性判定
- `model_optimizer.py`：4探索方式、時点バックテスト、ランキング・履歴保存
- `bootstrap_evaluation.py`：上位候補のBootstrapと95%信頼区間
- `model_compare.py`：Training／Validation、Version7-A比較、グラフ用表
- `model_optimization_ui.py`：Version7-Bモデル最適化タブ
- `bet_config.py`：Version7-CのScore重み、口数、予算、展開上限
- `bet_optimizer.py`：確率検証、試合別Score、区分配置、Coverage、手動調整
- `bet_export.py`：試合別買い目と全購入組み合わせのCSV
- `bet_evaluation.py`：買い目戦略バックテスト、的中・払戻・ROI判定
- `bet_optimization_ui.py`：Version7-C買い目最適化、履歴準備、戦略比較タブ
- `app_version.py`：予測履歴Versionと分離した表示用アプリVersion
- `app.py`：各モジュールを接続する入力・予想画面

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

Version6ではさらに次の列を予想結果CSVへ追加します。

- `toto_round`
- `toto_match_number`
- `prediction_version`
- `actual_result`
- `hit`
- `total_hits`
- `accuracy`
- `prediction_date`

Version7-Aでは既存列を残したまま、次の列を追加します。

- `version6_prediction` / `version7a_prediction`
- `version6_home_win` / `version6_draw` / `version6_away_win`
- `version7a_home_win` / `version7a_draw` / `version7a_away_win`
- `version6_top_probability` / `version7a_top_probability`
- `poisson_draw_probability` / `adjusted_draw_probability`
- `draw_candidate` / `draw_candidate_reasons`
- `version7a_prediction_changed`

Version7-B設定を採用した通常予想では、Version4～Version7-A列を従来係数で保持し、
候補係数の結果を次の別列へ追加します。

- `version7b_prediction`
- `version7b_home_win` / `version7b_draw` / `version7b_away_win`
- `version7b_top_probability`
- `version7b_home_elo` / `version7b_away_elo` / `version7b_elo_difference`
- `home_expected_after_version7b` / `away_expected_after_version7b`
- `version7b_poisson_draw_probability` / `version7b_adjusted_draw_probability`
- `version7b_prediction_changed`

Version8-Aでは画面用の小数1桁%を変更せず、実戦保存用の次の列を予想結果へ追加します。

- `league`
- `live_probability_1` / `live_probability_0` / `live_probability_2`
- `draw_candidate_threshold`
- `live_home_elo` / `live_away_elo` / `live_elo_difference`
- `live_home_expected_goals` / `live_away_expected_goals`

`live_probability_*`は予測時点の最終3クラス確率をフル精度で保持するための列です。
Version7-Cは従来どおり表示用`1`・`0`・`2`を使うため、買い目とCoverageは変わりません。

`prediction_version`が`Version7-B`の行だけ、予想履歴にもVersion7-Bを5番目の独立
Versionとして保存します。既存のVersion7-A行をVersion7-B値で上書きしません。

予想履歴CSVは上記キーに加え、Versionごとの1・0・2確率、期待得点、
`brier_score`、`log_loss`、`calibration`、`expected_hits`、`stake_yen`、
`payout_yen`、`roi`を保存します。すべてtoto公式試合番号順です。

Version7-Cの試合別買い目CSVは、対象側の試合番号、元のtoto試合番号、ホーム、
アウェイ、P(1)・P(0)・P(2)、1位・2位、確率差、引分候補、Entropy、
Uncertainty／Double／Triple Score、Draw Inclusion Score・判定・Coverage低下、
シングル信頼度、区分、買い目、Coverage、判定理由を保存します。
全組み合わせCSVは1口1行、対象側の試合番号順です。
これらはダウンロード用であり、予想履歴や採用設定を上書きしません。

## フォルダ構成

```text
.
├── app.py                    # Streamlit画面・入力・結果表示
├── teams.py                  # クラブ一覧・カテゴリー・チーム名正規化
├── prediction.py             # Version3までのポアソン予測ロジック
├── elo_rating.py             # Elo計算・取得・期待得点補正・Eloキャッシュ
├── form_adjuster.py           # 直近5試合の時系列重み付け
├── venue_adjuster.py          # ホーム／アウェイ別成績の混合
├── standings_adjuster.py      # 順位・勝点・得失点差補正
├── model_pipeline.py          # Version5の適用順序とVersion4比較
├── model_config.py           # Elo・Version5・キャッシュ設定
├── config.py                 # Version4までのimport互換用ブリッジ
├── data_loader.py            # 公式試合・順位表・クラブ統計キャッシュ
├── history_manager.py        # toto開催回・公式順・結果・CSVフォールバック
├── prediction_history.py     # Version別予想履歴CSV
├── backtest.py               # 時点バックテスト・データリーク防止
├── metrics.py                # 確率評価指標・ROI
├── analysis.py               # 分析集計・表・グラフ
├── draw_predictor.py         # 引分特徴量・確率補正・候補判定
├── draw_evaluation.py        # 引分専用評価・確率帯・Score
├── draw_optimizer.py         # 時系列分割・Optuna・保存・設定採用
├── draw_analysis.py          # 引分分析タブ・比較表・グラフ
├── parameter_manager.py      # Version7-B候補・採用・バックアップ
├── walk_forward_validator.py # 3方式の時系列Training／Validation分割
├── model_evaluation.py       # 総合Score・引分保護・過学習・安定性
├── model_optimizer.py        # Optuna／Random／Grid／2段階探索
├── bootstrap_evaluation.py   # Bootstrap分布・95%信頼区間
├── model_compare.py          # ランキング・比較・グラフ用表
├── model_optimization_ui.py  # Version7-Bモデル最適化タブ
├── bet_config.py             # Version7-C買い目設定・Score重み
├── bet_optimizer.py          # 区分配置・Coverage・手動調整
├── bet_export.py             # 試合別・全組み合わせCSV
├── bet_evaluation.py         # 買い目戦略バックテスト
├── bet_optimization_ui.py    # Version7-C買い目最適化タブ
├── live_history.py           # Version8-A実戦履歴・不変保存・結果更新
├── live_history_ui.py        # 実戦履歴・購入記録・詳細・CSV画面
├── diagnostic_config.py      # Version8-Bのサンプル数・異常判定閾値
├── model_diagnostics.py      # 実戦指標・劣化・偏り・品質診断
├── diagnostic_history.py     # 診断結果専用CSV・原子的保存
├── diagnostic_ui.py          # モデル診断タブ・グラフ・異常一覧
├── data/
│   ├── README.md             # matches.csvと実行時キャッシュの仕様
│   ├── reference/            # 公式取得失敗時の2024～2025年結果CSV
│   ├── cache/                # 実行時に自動生成（Git管理外）
│   ├── history/              # 予想・最適化履歴CSV（Git管理外）
│   └── config/               # 採用済み引分設定・バックアップ（Git管理外）
├── tests/
│   ├── test_app.py           # 13試合・画面・Elo・CSV統合テスト
│   ├── test_history_manager.py # toto公式順・開催回・フォールバック
│   ├── test_prediction_history.py # 履歴保存・実結果照合
│   ├── test_backtest.py      # 時点再計算・未来データ除外
│   ├── test_metrics.py       # Brier・Log Loss・Calibration・ROI
│   ├── test_analysis.py      # 開催回・累積・Version集計
│   ├── test_draw_predictor.py # 3クラス確率・引分特徴・候補
│   ├── test_draw_evaluation.py # Precision・Recall・F1・確率帯
│   ├── test_draw_optimizer.py # 時系列分割・10/30 Trial・採用復元
│   ├── test_draw_analysis.py # 比較表・グラフ入力
│   ├── test_version7b.py     # 4探索・時系列安全性・採用・Bootstrap
│   ├── test_version7c.py     # 口数・配置・Coverage・CSV・戦略比較
│   ├── test_version7c_streamlit.py # toto／mini・手動調整の画面統合
│   ├── test_live_history.py # Version8-A保存・不変性・結果・ROI
│   ├── test_live_history_streamlit.py # 実戦保存～購入～結果待ちE2E
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
python -m pytest -q
```

## 開発ロードマップ

- Version1：J1・J2・J3チーム選択
- Version2：CSV・データ取得基盤
- Version3：Jリーグ公式試合データ自動取得
- Version4：Eloレーティング導入
- Version5：直近成績・ホーム／アウェイ成績・順位等補正
- Version6：toto公式順・開催回・バックテスト・履歴・分析・確率指標・ROI
- Version7-A：引分予想強化、引分専用評価、時系列分割、Optuna小規模探索
- Version7-B：既存モデル全体の4方式探索、Walk Forward、安定性・信頼区間
- Version7-C：確率に基づく区分配置、Coverage、手動調整、CSV、戦略比較
- Version7.5：予測結果を変えない総点検、データ契約、pandas／Session State安全化、
  バックテスト監査、回帰Fixture、clean import、Streamlit E2E
- Version8-A：予測時点の実戦履歴、AI推奨／実購入、公式結果・払戻の不変保存基盤
- Version8-B：保存済み実戦履歴によるモデル診断・異常検知
- Version8-C（予定）：診断結果を根拠にしたAI改善提案
- Version9：独自判断入力・AIコメント生成
- Version10：note記事自動生成

予想は統計モデルによる推定であり、的中や利益を保証しません。
