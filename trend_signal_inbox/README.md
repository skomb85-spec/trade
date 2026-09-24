# trend_signal_inbox — TREND_SIGNAL 用の追記経路

Grok → `trend_signal_inbox/*.csv` を `main` へpush → GitHub Actions → スプレッドシート「TREND_SIGNAL」タブの末尾に追記。
`x_inbox` と同じコードを `--config trend_signal_inbox/config.json` で使う。認証は Secret `GOOGLE_SERVICE_ACCOUNT_JSON`（Grokには渡さない）。

- 重複キー: `元ネタ` + `派生ワード`（全角/半角・大文字小文字・空白の違いを無視）。`signal_id` や `発見日時` が違っても再登録しない。
- 停止条件（何も書かず、CSVも移動しない）: シートの列見出しが `config.json` の `columns` と違う / CSVにシートにない列がある / 重複キー列がCSVにない / タブが無い。
- 他のタブ（README / SNS_HANDOFF / PPC_HANDOFF）には書かない。

## Grokに渡す仕様

`trend_signal_inbox/` 直下に `.csv`（UTF-8、1行目ヘッダー）。列名はこのまま（24列）:

```
signal_id,発見日時,source,代表URL,元ネタ,元ワード,派生ワード,ずらし構造,伸びている理由,実際の使用文脈,24-72h伸び,複数媒体,ジャンル,賞味期限,SNS適性,PPC適性,商業意図,SNS転用案,PPC転用案,事実/推測,status,使用日時,成果,メモ
```
