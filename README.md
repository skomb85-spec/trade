# trade

米国上場銘柄を対象に、「この資金額で、1日あたりの目標利益に届く銘柄はどれか」を判定するスクリーニングツールです。

## 概要

このツールが答える問いは「1株あたりの値幅が大きい銘柄はどれか」ではありません。それは間違った問いです。答えるべきなのは次の問いです。

**この資金額で、1日あたりの目標利益に届く銘柄はどれか。**

判定の核になる式は次の通りです。

```
capital_usd × range_pct × capture_rate ≥ target_jpy / usdjpy
```

- `capital_usd` — 口座の資金額（USD）
- `range_pct` — 1日の値幅（High - Low）を終値に対する割合で表したもの、つまりボラティリティ
- `capture_rate` — その日の値幅のうちどれだけ実際に利益として回収できるか（デフォルト 0.30。ど
  の戦略も値幅の全部は取れないので、これは既に楽観的な想定であり、結果が良すぎると感じたら最初
  に疑うべきパラメータです）
- `target_jpy` / `usdjpy` — 円建ての目標利益をドルに換算したもの

この式が言っているのは、**効いてくるのはドル建ての値幅の大きさではなく、価格に対する割合として
の値幅（ボラティリティ）だ**ということです。株価 5,200 ドルの銘柄が 1.8% しか動かないなら、株価
25 ドルの銘柄が 7% 動く方が、資金の少ない口座にとってははるかに優れた乗り物です。ドル建ての値幅
で見れば後者の 90 倍だとしても、です。しかも資金 4,000 ドルでは、株価 7,500 ドルの銘柄は 1 株す
ら買えません。買えない銘柄は、どれだけ値幅が大きくても口座にとっては存在しないのと同じです。

このツールは、この式を実際の株価データに当てはめて銘柄をふるいにかけ、条件を満たした銘柄につい
て日次 OHLCV チャートデータを保存します。

## インストール

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` には日足スクリーニング本体に必要な `pandas` / `yfinance` / `requests` /
`lxml` に加えて、分足データ取得（Alpaca）に使う `alpaca-py` と、分足データを parquet 形式で保存す
るための `pyarrow` も含まれています。`pyarrow` が無くてもツールは動きますが、その場合は分足デー
タが gzip 圧縮 CSV で保存されます。

## 使い方（日足スクリーニング）

基本のコマンドは次の形です（`src` レイアウトのため `PYTHONPATH=src` が必要です）。

```bash
PYTHONPATH=src python -m screener
```

実行例:

```bash
# デフォルト（目標 5,000 JPY/日、資金 $4,000、capture 30%）
PYTHONPATH=src python -m screener

# 複数の資金額でどれだけ通過銘柄数が変わるかを比較する
PYTHONPATH=src python -m screener --capital-sweep 4000,10000,20000,25000

# S&P 500 構成銘柄をスクリーニング
PYTHONPATH=src python -m screener --universe sp500

# 保守的に、capture_rate を下げて見直す
PYTHONPATH=src python -m screener --capture-rate 0.15
```

## CLIオプション

`src/screener/cli.py` の `build_parser()` で定義されているフラグです。

### target and account（目標と資金）

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--target-jpy` | `5000` | 1日あたりの目標利益（円） |
| `--capital-usd` | `4000` | 口座の資金額（USD） |
| `--capture-rate` | `0.3` | 1日の値幅のうち実際に回収できると想定する割合 |
| `--fractional` | `False`（フラグ） | 整数株ではなく端株（fractional shares）を許可する |
| `--capital-sweep` | `""` | 比較したい資金額をカンマ区切りで指定する（例: `4000,10000,20000`） |

### universe（銘柄ユニバース）

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--universe` | `seed` | スクリーニング対象（`seed` / `sp500` / `all` から選択。`seed` はオフラインで使えるバンドル済みリスト） |
| `--universe-file` | `None` | ユニバースの代わりに使う `.txt`（1行1ティッカー）または `.csv` へのパス |
| `--always-include` | `""` | スクリーニング結果に関係なく必ず収集するティッカーをカンマ区切りで指定（例: `TSLA,NVDA`） |
| `--limit` | `None` | 先頭 N 銘柄のみをスクリーニングする（スモークテスト用） |

### filters（フィルタ）

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--min-bars` | `30` | lookback 期間内のバー数がこれ未満の銘柄を除外する |
| `--min-dollar-volume` | `20000000` | 平均日次売買代金（USD）の下限 |
| `--max-position-pct-of-volume` | `1.0` | ポジションが日次売買代金に対してこの割合（%）を超える場合は除外する |
| `--min-range-jpy` | `0.0` | 追加フィルタ: 1株あたりの平均日次値幅（円）の下限（デフォルトは無効） |

### data（データ取得）

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--lookback-days` | `180` | 値幅を計測するための遡及日数（暦日） |
| `--history-days` | `730` | 収集した銘柄について保存するチャートデータの日数（暦日） |
| `--fx-rate` | `None` | USD/JPY を都度取得せず固定し、実行を再現可能にする |
| `--out-dir` | `data/out` | 出力ディレクトリ |
| `--cache-dir` | `data/cache` | OHLCV キャッシュのディレクトリ |
| `--cache-max-age-hours` | `12.0` | この時間より新しいキャッシュを再利用する |
| `--no-stooq` | `False`（フラグ） | Stooq へのフォールバックを無効化する |
| `--no-charts` | `False`（フラグ） | スクリーニングのみ行い、銘柄ごとの CSV 出力をスキップする |
| `-v`, `--verbose` | `False`（フラグ） | デバッグログを出力する |

## 出力ファイル

実行すると `--out-dir`（デフォルト `data/out`）配下に以下が生成されます。

- **`data/out/metrics_all.csv`** — スクリーニング対象になった全銘柄（合格・不合格を問わず）の計
  測結果と判定結果。`metrics.py` / `screen.py` を参照。主な列は次の通りです。
  - `ticker`, `usdjpy`, `bars`, `first_date`, `last_date`, `source`（`cache` / `yfinance` /
    `stooq`）
  - `last_close_usd` — 直近終値
  - `avg_range_usd` / `median_range_usd` — 平均・中央値の日次値幅（High - Low、ドル）
  - `avg_range_jpy` / `median_range_jpy` — 同上の円換算
  - `avg_range_pct` — 終値に対する平均値幅の割合。判定式の `range_pct` に対応する本体の数値
  - `atr_usd` / `atr_jpy` — Wilder's true range の移動平均（ATR）
  - `avg_abs_close_change_usd` / `avg_abs_close_change_jpy` — 終値の日次変化幅の絶対値平均
  - `avg_dollar_volume_usd` — 平均日次売買代金（USD）
  - `shares_affordable` — この資金で保有できる株数（`--fractional` 指定時は端株を含む）
  - `position_usd` — その株数を保有した場合のポジション金額（USD）
  - `expected_daily_jpy` — 上記ポジションと `capture_rate` から見込まれる1日あたりの期待利益（円）
  - `required_capital_usd` — この銘柄の値幅で目標利益に届くために必要な資金額（USD）
  - `required_range_pct` — この資金・目標・capture_rate で必要になる値幅率（%）。全行共通の値
  - `passes` — 目標を満たしたかどうか
  - `reason` — 不合格の理由（複数条件がある場合は `; ` 区切り）
  - `always_include` — `--always-include` で指定された銘柄かどうか
  - `collect` — チャートを収集する対象かどうか（`passes` または `always_include`）
- **`data/out/passed.csv`** — `metrics_all.csv` のうち `passes` が真の行だけを抜き出したもの。
- **`data/out/charts/<TICKER>.csv`** — `collect` が真になった銘柄ごとの日次 OHLCV データ
  （`--history-days` 分、`--no-charts` 指定時は出力されません）。

## 実行例の出力

以下は合成データによるスモーク実行の結果であり実データではありません。実際の数値は必ず自分で実
行して確認してください。

```
target 5,000 JPY/day  account $4,000  capture 30%  USD/JPY 150.00
=> needs an average daily range of 2.78% of price

ticker  last_close_usd  avg_range_pct  shares_affordable  position_usd  expected_daily_jpy  passes
  SOXL         22.2830          7.644              179.0       3988.66             13721.0    True
  MSTR        303.9995          6.609               13.0       3951.99             11753.0    True
  TSLA        457.1982          3.311                8.0       3657.59              5450.0    True
  NVDA        159.7249          2.538               25.0       3993.12              4561.0   False
  META        783.0145          2.316                5.0       3915.07              4079.0   False
   NVR       7600.5940          2.250                0.0          0.00                 0.0   False
  BKNG       7207.8807          1.813                0.0          0.00                 0.0   False

--- how many tickers clear the target at each account size ---
  capital$    %/day  need range%  passing   best ticker
     4,000    0.833         2.78        3   SOXL
    10,000    0.333         1.11        7   SOXL
    20,000    0.167         0.56        7   SOXL
    25,000    0.133         0.44        7   SOXL
```

資金 4,000 ドルでは、SOXL・MSTR・TSLA のような高ボラティリティ銘柄しか目標をクリアできません。
NVDA や META は値幅率こそそれなりにありますが、この資金では届きません。そして NVR と BKNG は、値
幅率自体は悪くないにもかかわらず、株価が高すぎて 1 株も買えず（`shares_affordable` が 0）、ポジ
ションが取れないため `expected_daily_jpy` も 0 のままです。

## 分足データの取得（Alpaca）

日足スクリーニングで通過した銘柄について、Alpaca から 1 分足データを取得できます。

```bash
export APCA_API_KEY_ID=...
export APCA_API_SECRET_KEY=...
PYTHONPATH=src python -m screener.minutes --from-passed data/out/passed.csv --years 5
```

Alpaca の無料枠は IEX フィード経由で 2016 年まで（約10年分）の 1 分足データを提供しています。デ
ータはシンボル・年ごとに parquet（`pyarrow` があれば）または gzip 圧縮 CSV（`csv.gz`）として書き
出され、既にディスク上にある年はスキップされるため、途中から再開しても安価に済みます。

**IEX に関する注意点**: Alpaca の無料フィードは米国株全体の出来高のおよそ 2.5% しかカバーしてい
ないサンプルです。流動性の高い大型株では価格は連結テープ（consolidated tape）に近い動きをします
が、出来高は過小に表示されます。出来高に依存する分析は、実際の資金を投じる前に必ず連結フィード
で確認し直してください。

データ量の目安: 1 銘柄・1 取引日あたり約 390 本のバーがあるため、10 年分では 1 銘柄あたり約 100
万行になります。

## 分足データの取得可能期間の比較

| 足 | 取得可能期間 | ソース | APIキー |
| --- | --- | --- | --- |
| 日足 | 数十年（全期間） | yfinance / Stooq | 不要 |
| 1時間足 | 730日（2年） | yfinance | 不要 |
| 5分足 | 60日 | yfinance | 不要 |
| 1分足 | 7日 | yfinance | 不要 |
| 1分足 | 2016年〜（約10年） | Alpaca 無料枠（IEX） | 必要 |
| 1分足 | 全期間・全取引所 | Polygon 等 | 有料 |

60日分の5分足でパターンを探すと過学習しやすいため、Alpacaの10年分を推奨します。

## データソース

株価データの取得は `yfinance` を第一候補とし、失敗した銘柄は Stooq
（`https://stooq.com/q/d/l/?s=<symbol>.us&i=d`）にフォールバックします。取得結果は `data/cache`
（`--cache-dir` で変更可）にティッカーごとの CSV としてキャッシュされ、`--cache-max-age-hours`
（デフォルト12時間）以内のキャッシュがあればネットワークアクセスをスキップします。

価格は意図的に**未調整（`auto_adjust=False`）**のまま扱われます。このスクリーニングは「その日に
株価が実際に何ドル動いたか」を測るものなので、分割・配当調整された価格を使うと過去のバーの値幅
が実際より小さく見えてしまうためです。

USD/JPY レートは `--fx-rate` で明示しない限り、yfinance（`JPY=X`）→ Stooq（`usdjpy`）→ 固定値
150.0 の順にフォールバックして取得します（`src/screener/fx.py`）。

## 資金と目標について

同じ「1日 +5,000円」という目標でも、資金額によって難易度はまったく違います。

| 資金 | 日次リターン | 年率(250日) | 必要な値幅率(30%捕捉) |
| --- | --- | --- | --- |
| $4,000 (60万円) | 0.833% | 208% | 2.78% |
| $10,000 (150万円) | 0.333% | 83% | 1.11% |
| $20,000 (300万円) | 0.167% | 42% | 0.56% |
| $25,000 (375万円) | 0.133% | 33% | 0.44% |

目標額はどの行も同じ「1日 +5,000円」ですが、必要な日次リターンは資金額に応じて約6倍の差があり
ます。年率208%を継続できる個人トレーダーはまず存在しません。一方、年率42%は困難ではあるものの、
実在する水準です。

なお、この表も `metrics_all.csv` の判定も `capture_rate = 0.30`（値幅の30%を実際に回収できる）
という前提に立っています。この前提自体が既に楽観的で、`--capture-rate 0.15` あたりで見直すのが
健全です。

## 規制について（PDT）

参考情報であり法的助言ではありません。実際の取引にあたっては必ず利用しているブローカーに確認し
てください。

PDT（Pattern Day Trader、$25,000最低残高ルール）は**2026年6月4日に撤廃済み**です。SECが2026年4
月14日にFINRA Rule 4210の改正を承認し、"Pattern Day Trader" という区分自体が廃止され、intraday
margin standard に置き換わりました。出典は FINRA Regulatory Notice 26-10
（https://www.finra.org/rules-guidance/notices/26-10）です。

ただし、ブローカー側の実装期限は2027年10月20日までとされているため、証券会社によってはまだ旧ル
ール（$25,000ルール）を運用している場合があります。利用するブローカーに要確認です。

また、現金口座（cash account）はもともと PDT の対象外でしたが、T+1 の受渡し規則は引き続き適用さ
れます。

## テスト

```bash
./.venv/bin/python -m pytest tests/ -q
```

`tests/test_screener.py` に23個のテストがあり、いずれも合成データのみを使ったオフラインテストで
す。ネットワークアクセスも API キーも不要です。

## 制約

- 日足スクリーニングは `query1.finance.yahoo.com`（yfinance 経由）と `stooq.com` への、分足デー
  タ取得は `data.alpaca.markets` への外向きのネットワークアクセスを必要とします。サンドボックス
  環境やプロキシ制限のある環境ではこれらへの通信がブロックされる場合があり、その場合は実行して
  もデータが取得できずに終了します。
- このツールはスクリーニングとデータ収集のみを行い、売買は一切実行しません。バックテストや戦略
  の検証機能もまだ含まれていません。
</content>
</invoke>
