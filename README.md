# trade

米国上場銘柄を対象に、日々の値幅（high - low）をスクリーニングするツールです。

## 概要

このツールは、米国に上場している銘柄の中から、**1日あたりの平均高安値幅（average daily high-low range）を日本円に換算した値**が一定の閾値（デフォルト 5,000 JPY、150 JPY/USD 換算でおよそ 33 USD）を超える銘柄を抽出し、条件を満たした銘柄について日次の OHLCV チャートデータを保存します。閾値をドルではなく円で指定しているのは、このツール自体が日本の利用者向けに「1日にどれだけ円換算で動いたか」という感覚で銘柄を絞り込むために作られているためです。

## インストール

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 使い方

基本のコマンドは次の形です（`src` レイアウトのため `PYTHONPATH=src` が必要です）。

```bash
PYTHONPATH=src python -m screener
```

実行例:

```bash
# S&P 500 構成銘柄をスクリーニング
PYTHONPATH=src python -m screener --universe sp500

# 米国上場の全銘柄（Nasdaq Trader の一覧、約11,000銘柄）をスクリーニング
PYTHONPATH=src python -m screener --universe all

# USD/JPY を固定して再現性のある実行にする
PYTHONPATH=src python -m screener --fx-rate 150.0

# 動作確認用に先頭 N 銘柄だけを素早く流す（スモークテスト）
PYTHONPATH=src python -m screener --limit 20
```

## CLIオプション

`src/screener/cli.py` の `build_parser()` で定義されているフラグは以下の通りです。

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--threshold-jpy` | `5000.0` | minimum average daily high-low range, in yen（円建ての平均日次値幅の下限） |
| `--universe` | `seed` | which tickers to screen（`seed` / `sp500` / `all` から選択。`seed` はオフラインで使えるバンドル済みリスト） |
| `--universe-file` | `None` | path to a .txt (one ticker per line) or .csv to use instead（ユニバースの代わりに使う `.txt`（1行1ティッカー）または `.csv` へのパス） |
| `--always-include` | `""` | comma-separated tickers to collect regardless of the threshold, e.g. TSLA,NVDA（閾値に関係なく必ず収集するティッカーをカンマ区切りで指定） |
| `--lookback-days` | `180` | calendar days of history used to measure the range（値幅を計測するための遡及日数、暦日） |
| `--history-days` | `730` | calendar days of chart data saved for collected tickers（収集した銘柄について保存するチャートデータの日数、暦日） |
| `--min-bars` | `30` | reject tickers with fewer bars in the lookback window（lookback 期間内のバー数がこれ未満の銘柄を除外） |
| `--min-dollar-volume` | `0.0` | minimum average daily dollar volume, in USD（default: no filter）（USD建ての平均日次売買代金の下限。デフォルトはフィルタなし） |
| `--require-median` | `False`（フラグ） | also require the median range to clear the threshold, not just the mean（平均だけでなく中央値も閾値を超えることを要求する） |
| `--fx-rate` | `None` | fix USD/JPY instead of looking it up, for reproducible runs（USD/JPY を都度取得せず固定し、実行を再現可能にする） |
| `--out-dir` | `data/out` | output directory（出力ディレクトリ） |
| `--cache-dir` | `data/cache` | OHLCV cache directory（OHLCVキャッシュのディレクトリ） |
| `--cache-max-age-hours` | `12.0` | （キャッシュの有効期限。この時間を超えたキャッシュは使われず再取得される） |
| `--no-stooq` | `False`（フラグ） | disable the Stooq fallback（Stooq へのフォールバックを無効化する） |
| `--no-charts` | `False`（フラグ） | screen only; skip writing per-ticker CSVs（スクリーニングのみ行い、銘柄ごとのCSV出力をスキップする） |
| `--limit` | `None` | screen only the first N tickers (for a smoke test)（先頭N銘柄のみをスクリーニングする。スモークテスト用） |
| `-v`, `--verbose` | `False`（フラグ） | （詳細ログ（DEBUGレベル）を出力する） |

## 出力ファイル

実行すると `--out-dir`（デフォルト `data/out`）配下に以下が生成されます。

- **`data/out/metrics_all.csv`** — スクリーニング対象になった全銘柄（合格・不合格を問わず）の計測結果。不合格の場合は `reason` 列に理由が入ります。主な列は次の通りです（`metrics.py` / `screen.py` 参照）。
  - `ticker`, `usdjpy`, `bars`, `first_date`, `last_date`
  - `last_close_usd`
  - `avg_range_usd` / `avg_range_jpy` — 平均日次値幅（ドル / 円）。スクリーニングの本体
  - `median_range_usd` / `median_range_jpy` — 値幅の中央値
  - `avg_range_pct` — 終値に対する平均値幅の割合
  - `atr_usd` / `atr_jpy` — Wilder's true range の移動平均（ATR）
  - `avg_abs_close_change_usd` / `avg_abs_close_change_jpy` — 終値の日次変化幅の絶対値平均
  - `avg_dollar_volume_usd` — 平均日次売買代金（USD）
  - `source` — データ取得元（`cache` / `yfinance` / `stooq`）
  - `passes` — 閾値を満たしたかどうか
  - `reason` — 不合格の理由（複数条件がある場合は `; ` 区切り）
  - `always_include` — `--always-include` で指定された銘柄かどうか
  - `collect` — チャートを収集する対象かどうか（`passes` または `always_include`）
- **`data/out/passed.csv`** — `metrics_all.csv` のうち `passes` が真の行だけを抜き出したもの。
- **`data/out/charts/<TICKER>.csv`** — `collect` が真になった銘柄ごとの日次 OHLCV データ（`--history-days` 分、`--no-charts` 指定時は出力されません）。

## データソース

株価データの取得は `yfinance` を第一候補とし、失敗した銘柄は Stooq（`https://stooq.com/q/d/l/?s=<symbol>.us&i=d`）にフォールバックします。取得結果は `data/cache`（`--cache-dir` で変更可）にティッカーごとのCSVとしてキャッシュされ、`--cache-max-age-hours`（デフォルト12時間）以内のキャッシュがあればネットワークアクセスをスキップします。

価格は意図的に**未調整（`auto_adjust=False`）**のまま扱われます。このスクリーニングは「その日に株価が実際に何ドル動いたか」を測るものなので、分割・配当調整された価格を使うと過去のバーの値幅が実際より小さく見えてしまうためです。

USD/JPY レートは `--fx-rate` で明示しない限り、yfinance（`JPY=X`）→ Stooq（`usdjpy`）→ 固定値 150.0 の順にフォールバックして取得します（`src/screener/fx.py`）。

## 閾値についての注意

**5,000 JPY/日という閾値はかなり高いハードルです。** これをクリアできるのは、数百ドル台後半〜数千ドル台の高額株に限られます（NVR、BKNG、AZO、NFLX、MELI、ASML など）。

たとえば TSLA は株価がおおよそ 400 USD 前後で、平均日次値幅はおよそ 12〜15 USD（円換算でおよそ 1,800〜2,300 JPY）程度しかなく、**デフォルトの 5,000 JPY フィルタには通過しません。**

このような銘柄を扱う方法は2通りあります。

1. `--always-include TSLA` を指定して、閾値未達でも強制的に収集する。この場合も `metrics_all.csv` には実測値と `reason`（不合格理由）付きでその銘柄の行が残ります。
2. `--threshold-jpy` を下げる。

`metrics_all.csv` にはスクリーニング対象になった全銘柄が常に残るため、一度取得したデータに対して再取得なしで閾値だけを見直す（`passes` を計算し直す）ことも可能です。

## テスト

```bash
./.venv/bin/python -m pytest tests/ -q
```

`tests/test_screener.py` に10個のテストがあり、いずれも合成データのみを使ったオフラインテストです。ネットワークアクセスは不要です。

## 制約

このスクリーニング処理は `query1.finance.yahoo.com`（yfinance 経由）と `stooq.com` への外向きのネットワークアクセスを必要とします。サンドボックス環境やプロキシ制限のある環境ではこれらへの通信がブロックされる場合があり、その場合は実行してもデータが取得できずに終了します。
