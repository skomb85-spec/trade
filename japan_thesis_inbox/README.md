# japan_thesis_inbox — Japan Thesis Radar 用の追記経路

Grok → `japan_thesis_inbox/*.csv` を `main` へpush → GitHub Actions → Googleスプレッドシート「投稿データ」タブの末尾に追記。
`x_inbox` と同じ仕組み（`python -m x_inbox --config japan_thesis_inbox/config.json`）を、設定とworkflowだけ分けて使う。`x_inbox` 側には触れない。

- 保存先: `config.json` の `spreadsheet_id` / `worksheet`
- 重複キー: `source_url` + `ticker` の組（同じURLでも別tickerなら別行）
- 処理済みCSV: `japan_thesis_inbox/archive/<日付>/` へ移動
- 認証: `x_inbox` と同じ Secret `GOOGLE_SERVICE_ACCOUNT_JSON`。シートを、そのサービスアカウントに「編集者」で共有しておく

## 行チェック

共通の行チェックで壊れたCSV（列数違い・断片行・`SEE_ARTIFACT_FILE`・必須列が空・引用符なしの改行）は丸ごと拒否されます。詳細は [x_inbox/README.md](../x_inbox/README.md) の「CSVの行チェック」。ticker は空欄でも通ります（必須は `collected_at, handle, display_name, post_created_at, post_text, source_url, topic, stance, summary`）。

## Grokに渡す仕様

- 置き場所: `japan_thesis_inbox/` 直下に `.csv`（UTF-8、1行目はヘッダー、サブフォルダ不可）
- 列名（このまま）:

```
collected_at,handle,display_name,ticker,company,post_created_at,post_text,source_url,topic,stance,summary
```
