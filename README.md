# Jリーグ toto予想アプリ

J1・J2・J3の試合データと、各クラブの直近5試合から算出した平均得点・
平均失点を使い、ポアソン分布でtotoの勝敗確率を計算するStreamlitアプリです。

Version 3では、APIからの自動取得と、API → CSV → 手入力の自動切替に
対応しました。APIやCSVがなくても、従来どおり手入力で予想できます。

## データ取得元

取得元は [API-Football](https://www.api-football.com/) です。

- API URL：`https://v3.football.api-sports.io`
- [公式ドキュメント](https://www.api-football.com/documentation-v3)
- [公式カバレッジ](https://www.api-football.com/coverage)
- [公式料金表](https://www.api-football.com/pricing)
- [公式利用規約](https://www.api-football.com/terms)

2026年8月1日時点で、公式カバレッジにJ1 League・J2 League・J3 Leagueが
掲載されています。無料プランは全大会・全エンドポイントを利用でき、上限は
1日100リクエストです。ただし無料プランで利用できる過去シーズンには制限が
あります。

API-Footballの規約では、取得データそのものの再販売は禁止されています。
また、各大会データを公開・商用利用するために必要な権利確認は利用者の責任と
されています。このアプリを有料提供する前に、API-FootballとJリーグへ
利用形態を提示し、必要な許諾を確認してください。

## APIキーの設定

1. [API-Footballのダッシュボード](https://dashboard.api-football.com/)で
   無料アカウントを作り、APIキーを取得します。
2. ローカルまたはCodespacesでは、`.streamlit/secrets.toml.example`を
   `.streamlit/secrets.toml`へコピーし、値を自分のAPIキーへ変更します。

```toml
API_FOOTBALL_KEY = "ここにAPIキーを入力"
```

3. Streamlit Community Cloudでは、アプリの設定画面にあるSecretsへ同じ内容を
   登録して再起動します。

`secrets.toml`はGit管理対象外です。APIキーをコードやGitHubへ直接書かないで
ください。

## 自動取得する内容

起動時に次の処理を行います。

1. 日本の現在開催中のJ1・J2・J3を取得
2. 各カテゴリーの当季と前季の試合を取得
3. 未開催試合を試合日順に並べ、先頭13試合を入力欄へ設定
4. 各クラブの完了済み試合を新しい順に5試合取得
5. 直近5試合の得点・失点から平均得点・平均失点を算出

取得項目は、試合日、ホームチーム、アウェイチーム、ホーム／アウェイ各クラブの
直近5試合、平均得点、平均失点です。チームを変更すると該当クラブの平均値を
自動入力し、その後はユーザーが自由に修正できます。

## 更新方法

APIの使用量を抑えるため、Streamlitの取得結果を6時間キャッシュします。
J1・J2・J3をすべて取得した場合、1回の更新は最大7リクエスト、通常は最大
28リクエスト／日です。

- 通常：6時間ごとに自動更新
- すぐ更新：Streamlitのキャッシュを削除してアプリを再起動
- APIキー変更後：アプリを再起動

## API失敗時の動作

取得順は次のとおりです。

1. `ApiDataSource`（API-Football）
2. `CsvMatchDataSource`（`data/matches.csv`）
3. 手入力

APIキー未設定、通信失敗、取得上限到達、応答形式変更のいずれでも、技術的な
エラー画面は表示せずCSVへ切り替えます。CSVも利用できない場合は、Version 1と
同じ初期値で手入力画面を表示します。

## CSVの利用

CSVの仕様と例は [`data/README.md`](data/README.md) に記載しています。
`data/matches.csv`は必須ではありません。

## APIを変更する方法

`data_loader.py`の`MatchDataSource`は、次の2点だけを要求します。

```python
class MatchDataSource(Protocol):
    @property
    def name(self) -> str: ...

    def load(self) -> pd.DataFrame: ...
```

将来APIを変更するときは、`ApiDataSource`の認証・URL・JSON変換を新しいAPIに
合わせて差し替えます。返すDataFrameを`MATCH_COLUMNS`の形式にそろえれば、
`app.py`、ポアソン計算、CSV保存、`teams.py`は変更不要です。取得元の優先順位を
変える場合だけ、`get_default_data_sources()`の並びを変更します。

## 実行とテスト

```bash
pip install -r requirements.txt
streamlit run app.py
python -m unittest discover -s tests -v
```
