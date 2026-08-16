# Version7.5 コード・バックテスト監査

## 方針

監査基準はGitHub `main`の`a5c5897ba970bcc5b6a4207e3536f8203e7e9fe8`。
全面書換えをせず、予測・最適化・Coverageの数式は変更しない。修正前のVersion7-C
買い目出力を`tests/fixtures/version75_regression_baseline.json`へ固定し、絶対誤差
`1e-12`で比較する。

## 重複と判断

| 対象 | 監査結果 | 判断 |
|---|---|---|
| 実結果`1/0/2`変換 | `prediction_history`、`draw_evaluation`、`bet_evaluation`に同義処理 | `metrics.normalize_toto_outcome`へ共通化 |
| 確率正規化 | 指標、引分モデル、買い目入力に3実装 | 不正値を均等確率にする指標、3クラス残差を厳密化する引分、入力を拒否する買い目で契約が異なるため維持 |
| 開催回・実結果 | 公式取得、履歴照合、時点バックテストに存在 | 取得元とcutoff責務が異なり、統合すると未来データ混入リスクが上がるため維持 |
| Coverage | `bet_optimizer`を唯一の計算元としてUI・CSVが参照 | 重複なし、式を変更しない |
| 買い目生成・CSV | optimizerとexportに分離 | 計算と表現の責務が分離済みのため維持 |
| 払戻形式 | `normalize_toto_payouts`へ集約済み | 追加変更なし |
| 評価指標 | `metrics`と引分二値指標に分離 | 多クラスと引分専用で定義が異なるため維持 |

巨大関数は行数だけで分割していない。`app.py`の予測実行は多数のStreamlit widgetと
同一rerun stateに結び付くため、Version7.5での機械分割は挙動差リスクが高い。
予測計算自体は`model_pipeline`、引分は`draw_predictor`、買い目は`bet_optimizer`、
評価は`metrics`、履歴は`prediction_history`へ既に分離されている。

## import監査

AST import graphに循環はない。通常のPython importで主要moduleを読み込める。
`version7b_runtime.py`と`version7b_config.py`だけは、Streamlit hot rerunで更新前moduleが
残った既知問題を検査し、signature・module identity・dataclass fieldsが一致しない場合
だけreloadする。これは無条件reloadではなく、過去の部分初期化・signature不一致対策の
ためVersion7.5では維持する。UI moduleからモデルmoduleへの逆importは追加していない。

## pandas・Session State監査

先頭位置を取るSeries／DataFrame処理は`.iloc`／`.iat`を使用し、`series[0]`や
`frame[column][0]`へ依存しない。`.loc`／`.at`は開催回maskや列label、`.iloc`／`.iat`は
位置という用途で維持した。機械的な全置換は行っていない。

買い目画面では古いtarget、区分数、予算、引分閾値、手動区分・結果の型をwidget生成前に
検証する。不正な`None`、Series、NaN、Infinity、範囲外値を安全な既定値へ戻し、
Fingerprintの異なる古い手動keyを削除する。通常値のkey・操作・表示は変更しない。

## バックテスト監査

- cutoffは開催回初日`00:00 JST`
- `completed_before(cutoff)`の試合だけで直近、会場別、順位相当、勝点、得失点差、Eloを再構成
- 現在の所属カテゴリーや最終順位でなく、対象時点の履歴からカテゴリーを決定
- Version7-Aオンデマンド生成は各入力source timeがcutoff未満であることを検査
- Version7-B最適化のTraining／Validation／Walk Forwardも同じ時点入力を使用
- 13試合の公式実結果がすべて確定した開催回だけ評価
- 未確定回は的中率・払戻・利益・ROIを0にせず評価対象外
- Version7-B過去履歴は保存済み確率だけを評価し、現在係数で再生成しない
- Version7.5以後のライブ履歴は保存時刻と開催初日cutoffを記録し、cutoff以後の行を除外

Version7.5以後の現在予測Versionには、確率・Versionに加えて予測時設定Snapshotと
引分候補情報を保存する。これにより将来のVersion7-B戦略評価は確率を再計算せず、
当時の保存値を監査できる。全Version行には新規ライブ予想のcutoff適格性を保存する。
過去の欠損設定・適格性は捏造しない。

## 性能比較

開始Commitと修正後へ同一の合成入力を与え、warm-up後5回の中央値を比較した。

| 経路 | 修正前 | 修正後 |
|---|---:|---:|
| 通常`predict_match` 1試合 | 0.0614 ms | 0.0613 ms |
| toto買い目生成 | 0.278 ms | 0.277 ms |
| 10開催回の戦略比較 | 51.4 ms | 50.1 ms |

測定揺らぎの範囲で同等であり、著しい速度低下はない。高速化を目的とした数式変更や
cache追加は行っていない。

## 変更しなかった数値ロジック

Poisson、期待得点、Elo、ホーム／アウェイ、直近、順位・勝点・得失点差、Version7-A
引分補正、Version7-B探索・Best Trial、P(1)・P(0)・P(2)、本命、Uncertainty、Double、
Triple、Draw Inclusion、Coverage、口数、購入額、Brier、Log Loss、Calibration、ROIの
式は変更していない。

## 既知の制限

- 旧Version7-B履歴の設定Snapshotと旧引分候補は遡及復元できない
- mini totoの公式払戻Schemaがないためmini ROIは未評価
- 公式データ・Streamlit Cloudの可用性は外部環境に依存
- hot rerun互換層は残るが、通常import経路とは分離している
