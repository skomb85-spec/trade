# x_inbox — CSVを既存のGoogleスプレッドシートに自動追記する

Xのトークンは使いません。必要なのは **Googleのサービスアカウント1つだけ** です。

## 流れ

```
  Grok（または誰でも）              GitHub                     Google
  ────────────────              ──────                     ──────
  抽出した行をCSVにして   push→  x_inbox/*.csv
                                     │
                                     │ GitHub Actions が自動で起動
                                     ▼
                                python -m x_inbox  ──追記→  既存シートの末尾
                                     │
                                     ▼
                          x_inbox/archive/2026-09-22/ に移動
```

同じCSVを2回pushしても、**シートに既にある行は追記されません**（後述の「重複しない仕組み」）。

---

## セットアップ（最初の1回だけ、15分ほど）

### 1. Googleサービスアカウントを作る

1. <https://console.cloud.google.com/> を開く（Googleアカウントでログイン）
2. 画面上部のプロジェクト選択 →「新しいプロジェクト」→ 名前は `x-inbox` など何でも可 →「作成」
3. 左メニュー「APIとサービス」→「ライブラリ」→ `Google Sheets API` を検索 →「有効にする」
4. 左メニュー「APIとサービス」→「認証情報」→ 上部「+ 認証情報を作成」→「サービスアカウント」
   - 名前: `sheet-writer` など
   - 「作成して続行」→ ロールは設定不要 →「完了」
5. 作られたサービスアカウントをクリック →「キー」タブ →「鍵を追加」→「新しい鍵を作成」→
   **JSON** を選んで「作成」。JSONファイルがダウンロードされます。

このJSONの中に `"client_email": "sheet-writer@....iam.gserviceaccount.com"` という行があります。
**このメールアドレスを控えてください。** 次のステップで使います。

### 2. 対象のスプレッドシートを、そのメールアドレスに共有する

書き込みたいスプレッドシートを開き、右上「共有」→ 控えたメールアドレスを貼り付け →
権限を **編集者** にして「送信」。

> ここを忘れると、実行時に「403」で止まります。エラーメッセージにも同じ案内が出ます。

### 3. GitHubに登録する

リポジトリの **Settings → Secrets and variables → Actions** を開きます。

| タブ | 名前 | 値 |
|---|---|---|
| Secrets（New repository secret） | `GOOGLE_SERVICE_ACCOUNT_JSON` | ダウンロードしたJSONを**テキストエディタで開き、`{` から `}` まで全部**コピーして貼り付け |
| Variables（New repository variable） | `SPREADSHEET_ID` | シートURLの `/d/` と `/edit` の間の文字列 |

スプレッドシートIDの位置:

```
https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890/edit#gid=0
                                       └────────────── これ ──────────────┘
```

`SPREADSHEET_ID` をVariablesに入れず、`x_inbox/config.json` の `"spreadsheet_id"` に直接書いても
動きます（IDは秘密情報ではありません）。両方ある場合はVariablesが優先されます。

### 4. 書き込み先のシート名を合わせる

`x_inbox/config.json` の `"worksheet"` を、実際のタブ名（画面下のシート名）に変えてください。
デフォルトは `inbox` です。存在しないタブ名を書いた場合は、そのタブが自動で作られます。

---

## 動作確認

`Actions` タブ →「x-inbox to Google Sheet」→ 右の `Run workflow` →
**check** にチェックを入れて実行。これは何も書き込まず、接続だけ試します。

成功すると、ログの最後にこう出ます。

```
connected: 「Xリサーチ」 / 既存データ 128 行
header:    collected_at, posted_at, id, author, text, url, ...
OK: 接続できました。あとは x_inbox/ にCSVを置いてpushするだけです。
```

失敗した場合、ログの `error:` の行に日本語で原因と対処が出ます。

> **注意**: このワークフローは `main` ブランチに入って初めて動きます。まだ作業ブランチにいる
> 場合は、先にmainへマージしてください。

---

## Grok（または他のツール）に渡す仕様

これをそのまま渡せば動きます。

> - リポジトリ: `skomb85-spec/trade`、ブランチ: `main`
> - ファイルの置き場所: `x_inbox/` 直下に `.csv` として置く（ファイル名は自由。例:
>   `x_inbox/2026-09-22-breakouts.csv`）。サブフォルダは読みません。
> - 文字コード: UTF-8、1行目は列名のヘッダー
> - 列名（既存シートの1行目と同じ名前にすること）:
>   `collected_at, posted_at, id, author, text, url, likes, reposts, tags, note`
> - `id` には投稿ID（投稿URL末尾の数字）を入れる。これが重複判定に使われる。
> - 列が足りなくても構わない（足りない列は空欄で埋まる）。シートに無い列名は捨てられる。
> - 既に送った行をもう一度含めても問題ない（シート側で弾かれる）。

サンプル: [`examples/sample.csv`](examples/sample.csv)

---

## 重複しない仕組み

追記の前に、シートの `id` 列を全部読み、**同じ `id` の行は飛ばします**。

`id` が空、あるいはCSVに `id` 列が無い場合は、**行の内容そのもののハッシュ**を鍵にします。つまり
IDが無くても、中身が完全に同じ行は二重に入りません。

列の順番はシートの1行目（既存のヘッダー）に合わせます。CSVの列順は気にしなくて構いません。
シートが完全に空のときだけ、`config.json` の `columns` を1行目に書き込みます。

---

## ローカルで試す（macOS）

Googleに接続せず、何が追記されるかだけ見る:

```bash
cp x_inbox/examples/sample.csv x_inbox/test.csv
PYTHONPATH=src ./.venv/bin/python -m x_inbox --dry-run
rm x_inbox/test.csv
```

実際に書き込むところまでローカルで試す場合は、JSONキーを環境変数に入れてから:

```bash
./.venv/bin/pip install -r x_inbox/requirements.txt
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat ~/Downloads/xxxxx.json)"
export SPREADSHEET_ID=1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890
PYTHONPATH=src ./.venv/bin/python -m x_inbox --check      # 接続テスト
PYTHONPATH=src ./.venv/bin/python -m x_inbox --limit 3    # 3行だけ入れてみる
```

| オプション | 意味 |
|---|---|
| `--dry-run` | Googleに一切接続せず、追記される内容を表示するだけ |
| `--check` | 接続テストのみ。シートの行数とヘッダーを表示 |
| `--limit N` | 今回追記する行数の上限 |
| `--no-archive` | 処理後にCSVを `archive/` へ移動しない |
| `--inbox DIR` | 読み込み先フォルダを変える |

---

## うまくいかないとき

| ログに出るもの | 原因と対処 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON が空です` | Secretsの登録漏れ、または名前の打ち間違い |
| `403` | シートをサービスアカウントのメールに「編集者」で共有していない。または Sheets API が無効 |
| `404` | スプレッドシートIDが違う。URLの `/d/` と `/edit` の間だけを入れる |
| `429` | Google側の回数制限。数分おいて Actions の `Re-run jobs` |
| ワークフローが起動しない | (1) `main` 以外にpushした (2) `x_inbox/` 直下以外に置いた (3) 拡張子が `.csv` でない |
| `シートにない列は捨てられます` | CSVの列名とシート1行目の列名が違う。シート側に同じ名前の列を足すか、CSVの列名を合わせる |

処理済みCSVの中身は `x_inbox/archive/<日付>/` に残っているので、取りこぼしはそこで確認できます。
