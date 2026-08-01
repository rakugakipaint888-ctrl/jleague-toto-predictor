# バックテスト用Jリーグ公式結果

`jleague_history_2024_2025.csv`は、Streamlit CloudからJリーグ公式の過去ページへ
一時的に接続できない場合に使用する読み取り専用フォールバックです。

- 取得日：2026年8月1日（JST）
- 取得元：J. League Data Site「日程・結果」
- 対象：2024年・2025年、J1・J2・J3
- 件数：2024年1,134試合、2025年1,140試合、合計2,274試合
- 項目：試合日時、ホーム、アウェイ、得点、失点、カテゴリー

取得URL：

- `https://data.j-league.or.jp/SFMS01/search?competition_years=2024&competition_frame_ids=1&competition_frame_ids=2&competition_frame_ids=3`
- `https://data.j-league.or.jp/SFMS01/search?competition_years=2025&competition_frame_ids=1&competition_frame_ids=2&competition_frame_ids=3`

バックテストでは対象開催初日0:00（JST）以後の行を必ず除外します。このCSVに
対象回より後の結果が含まれていても、順位・直近成績・会場別成績・Eloには
使用されません。
