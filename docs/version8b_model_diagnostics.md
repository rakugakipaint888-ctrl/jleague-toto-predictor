# Version8-B モデル診断データ契約

## 読み取り境界

Version8-BはVersion8-Aの`live_round_history.csv`、`live_match_history.csv`、
`live_bet_history.csv`を読み取る。現在モデルで過去予測を再生成せず、3ファイルを更新しない。
診断結果だけを`model_diagnostic_history.csv`へ別保存する。

結果を必要とする指標は、開催回状態が`result_confirmed`または`evaluated`で、同じ
`prediction_run_id`に試合番号1～13と有効な公式`actual_result`が揃うrunだけを対象とする。
リーグ絞り込み後もrunの結果確定判定は全13試合で行い、指標計算だけを保存済みリーグへ
限定する。実購入ROIは`record_type=purchased`、実購入額、実払戻を確認できる買い目だけで
計算する。

## 指標定義

- 全体的中率：保存本命と公式実結果が一致した試合数÷対象試合数
- 多クラスBrier：1/0/2の3クラス二乗誤差和の試合平均。0が最良、最大2
- Log Loss：公式実結果へ保存時に割り当てた確率の平均負対数
- 全体Calibration：保存本命の確率を10帯へ分けたExpected Calibration Error
- 1/0/2別Brier：各結果対その他の二値Brier
- 1/0/2別Calibration：0–20、20–30、30–40、40–50、50–60、60%以上の
  予測確率帯で算出する加重絶対差
- Precision/Recall/F1：各結果を陽性としたone-vs-rest分類指標
- ROI：払戻÷購入額。actualとsimulationは別集計

ゼロ除算は0.0、算出根拠そのものがない値は空欄/N/Aとする。未購入、未確定、払戻不明を
損失0円、ROI 0%、ROI -100%へ変換しない。

## 期間、リーグ、Version、設定group

直近N開催は`round_start_at`、取得できない場合は`predicted_at`で並べた一意の`round_id`を
使用する。同じ開催回に複数の明示的実戦runがあれば全runを評価する。今シーズンはJSTの
現在年と保存`season`が一致するrun、任意期間は保存開催日を両端含みで選ぶ。

リーグは保存値が正確にJ1/J2/J3の場合だけ個別集計する。Versionは保存
`prediction_version`を使う。設定groupは`settings_snapshot_json`を安定順序JSONへ戻して
SHA-256を計算し、先頭12桁を`setting_<12桁>`として表示する。group比較は相関・期間差を
含み、設定変更の因果効果を推定しない。

Rolling 5/10は選択リーグ・Version内の全保存履歴を基準にする。上部の期間指定は全体指標、
時系列、group、買い目集計へ適用する。リーグ絞り込み時の買い目指標は対象リーグを含むrun
全体の保存値であり、金額・払戻・Coverageをリーグ別に按分しない。

## 異常と総合状態

固定閾値は`diagnostic_config.py`の`DiagnosticThresholds`だけに置く。直近5/10開催を
全期間と比較し、的中率、Brier、Log Loss、Calibration、引分F1の悪化量を判定する。
引分率と本命0率の差、1/0/2別Recall、高確率予測の平均確率と的中率の差、リーグ別性能差、
確率合計異常も個別ルールで判定する。

リーグ別性能差は的中率、Brier、Log Loss、Calibration、引分F1をそれぞれ全リーグ加重平均と
比較し、異常を起こした指標自身の現在値・基準値・差を表示する。Coverage帯は評価済み買い目
5件未満をデータ不足とする。

総合状態の優先順位は次の固定ルールとする。

1. 警告が1件以上：警告
2. 警告なしで最低26試合・2開催未満、または指定直近N開催不足：データ不足
3. サンプル十分で注意が1件以上：注意
4. 上記以外：正常

各異常はcode、カテゴリ、名称、レベル、指標、現在値、基準値、差、単位、判定、定型コメントを
保持する。改善値や修正方法は生成しない。

## データ品質と除外

raw CSVを読み取り専用で検査した後、Version8-Aの不変hashを通過した行を集計する。
確率欠損・非有限・範囲外・合計異常、本命不正、実結果不正、試合番号不正、重複run、
重複試合、13試合不足、Version欠損、設定Snapshot欠損、結果確定状態と13結果の不一致を
検出する。異常runは診断計算から除外するが、元CSVの削除・修正・上書きはしない。
データ品質検査は破損行を期間やリーグへ推測分類せず元CSV全体に対して行い、絞り込み後の
モデル性能とは別区分で表示する。

## 診断履歴Schema Version 1

1行は1回の手動診断で、主キーは次の形式の`diagnostic_id`である。

```text
diag_YYYYMMDDTHHMMSSffffff_<UUID4の32桁hex>
```

対象期間・開始終了日・リーグ・Version、run/開催/試合数、総合状態と理由、全体指標、
引分指標、異常・品質問題JSON、除外数、閾値JSON、不変hashを保存する。同じID・同じhashは
冪等、同じID・異なる内容は競合エラーとする。一時ファイルへ全体を書き、`fsync`後に
`os.replace`する。破損・列不足・hash不一致の既存診断履歴を空データで上書きしない。

## Version8-Cとの境界

Version8-Bが出力するのは事実、数値、固定ルール判定、テンプレート型診断コメントまでである。
係数変更、改善方法、再最適化回数、config.py変更、モデル採用、自動購入、LLM自由判断は
Version8-Cの範囲として実装しない。
