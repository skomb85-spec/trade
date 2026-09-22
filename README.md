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
git clone https://github.com/skomb85-spec/trade.git
cd trade
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

macOS には `python` コマンドがなく `python3` だけが入っています。以降のコマンドは仮想環境の
インタプリタを `./.venv/bin/python` とパスで直接指定するため、`source .venv/bin/activate` は
不要で、`python` / `python3` の違いも踏みません。コマンドはリポジトリのルート（`trade/`）で
実行してください。`PYTHONPATH=src` はそこからの相対パスです。

`requirements.txt` には日足スクリーニング本体に必要な `pandas` / `yfinance` / `requests` /
`lxml` に加えて、分足データ取得（Alpaca）に使う `alpaca-py` と、分足データを parquet 形式で保存す
るための `pyarrow` も含まれています。`pyarrow` が無くてもツールは動きますが、その場合は分足デー
タが gzip 圧縮 CSV で保存されます。

## 使い方（日足スクリーニング）

基本のコマンドは次の形です（`src` レイアウトのため `PYTHONPATH=src` が必要です）。

```bash
PYTHONPATH=src ./.venv/bin/python -m screener
```

初回は小さく試してください。S&P500 の20年分は500銘柄分の取得になり、数十分かかるうえ
Yahoo のレート制限に当たることがあります。

```bash
PYTHONPATH=src ./.venv/bin/python -m screener --limit 30 --history-days 7300
```

実行例:

```bash
# デフォルト（目標 5,000 JPY/日、資金 $4,000、capture 30%）
PYTHONPATH=src ./.venv/bin/python -m screener

# 複数の資金額でどれだけ通過銘柄数が変わるかを比較する
PYTHONPATH=src ./.venv/bin/python -m screener --capital-sweep 4000,10000,20000,25000

# S&P 500 構成銘柄をスクリーニング
PYTHONPATH=src ./.venv/bin/python -m screener --universe sp500

# 保守的に、capture_rate を下げて見直す
PYTHONPATH=src ./.venv/bin/python -m screener --capture-rate 0.15
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
PYTHONPATH=src ./.venv/bin/python -m screener.minutes --from-passed data/out/passed.csv --years 5
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

## バックテストとwalk-forward検証

固定した過去データでパラメータ探索をすれば、必ず何かが見つかります。48通り試せば、コイン投げ戦
略でも「最良の1つ」は好成績に見えます。これは算術であって証拠ではありません。だからこのツールで
は walk-forward が後付けの検証ではなく、設計の中心に置かれています。

### 仕組み

train 期間でパラメータを選び、その直後の test 期間だけで評価し、window を test の長さだけ前進さ
せます。全 test 期間をつなぐと、どの取引も自分の結果を知らずに選ばれた out-of-sample の成績にな
ります。

これだけでは単一の OOS 数値をまだ誤読しかねないため、2つの診断が付いています（`walkforward.py`）。

- `baseline_expectancy` — その window の**全**パラメータ組み合わせの OOS 平均。選んだパラメータ
  がこれを上回らなければ、最適化は何も生んでいません。
- パラメータ安定性 — window ごとに選ばれる値が毎回変わるなら、集計 OOS が黒字でもノイズを拾って
  いるだけです。

### シミュレーションの3つの誠実さのルール（`engine.py` / `swing.py`）

- バー i のシグナルは バー i+1 の始値で約定する（バー i の終値や、トリガー価格ちょうどでは約定
  させない）
- 1本のバーが stop と target の両方を含む場合、stop が先に当たったものとして扱う（分足も日足も
  どちらが先かは記録していないため、自分に有利に解釈しない）
- ポジションは重複させない。1本の手仕舞い後にだけ次のシグナルを探す。`intraday` は全ポジション
  を引けでフラットにし（オーバーナイトは持たない）、`swing` はこれを `max_hold` セッションでの
  強制手仕舞いに置き換える。swing ではオーバーナイトギャップがそのまま反映されるため、ストップ
  を飛び越えるギャップもストップ価格で約定したことにしており、これは楽観的な想定です

### swing と intraday — 2つのモードが1つのwalk-forward基盤を共有する

| モード | バー | 保有 | データ源 | 履歴 |
|---|---|---|---|---|
| `swing`（デフォルト） | 日足 | 2〜7セッション | `data/cache`（スクリーナーが取得済み） | 数十年 |
| `intraday` | 分足 | 引けでフラット | `data/minute`（Alpaca） | 2016年〜 |

swing がデフォルトになったのは、日足は履歴が桁違いに長くサンプル数が多いので過学習しにくく、5
日に1往復ならスリッページは誤差に収まるからです。デイトレでは0.3%の値幅に対し往復コストが利益
の3分の1を食いますが、スイングではそこまで効きません。

### 使い方

```bash
PYTHONPATH=src ./.venv/bin/python -m screener.backtest --symbols TSLA,MSTR
PYTHONPATH=src ./.venv/bin/python -m screener.backtest --from-passed data/out/passed.csv --strategy donchian
PYTHONPATH=src ./.venv/bin/python -m screener.backtest --mode intraday --symbols TSLA
```

### 戦略（`swing_strategies.py`、`--mode swing`）

いずれも108通りの組み合わせがあり、**すべてロングオンリー**です（空売りは借株や買い戻しリスク
をモデル化していないため対象外）。

- `donchian`（DonchianBreakout） — 直近N日の高値終値ブレイクを買う。params: lookback, atr_mult,
  target_r, max_hold
- `rsi`（RSIPullback） — 上昇トレンド内の短期売られ過ぎを買う（Connors の RSI(2) 系）。params:
  rsi_period, entry_level, trend_ma, target_r, max_hold
- `macross`（MACross） — 短期移動平均が長期を上抜けた日に買う。params: fast, slow, atr_mult,
  target_r, max_hold

インジケータは因果的（causal）です。shift(1) 等で当日の値を混入させず、約定は必ず翌バーの始値
です。

### 戦略（`strategies.py`、`--mode intraday`）

- `orb`（OpeningRangeBreakout） — 寄り付きN分のレンジのブレイクに乗る。params: or_minutes,
  target_r, direction。48通り
- `vwap`（VWAPReversion） — VWAPからの乖離を逆張りし、VWAPへの回帰を狙う。params: threshold_pct,
  stop_pct, start_minute。60通り
- `gap`（GapFill） — 寄り付きのギャップを前日終値方向に狙う。params: min_gap_pct, max_gap_pct,
  stop_pct, entry_minute。81通り

### 有意性判定（t統計量）

OOS expectancy が正でも、取引数が少なく1取引ごとのばらつきが大きければそれはノイズです。
`stats.py` は `t_stat`（平均を標準誤差で割った値）を計算しており、`|t| < 2` の場合は "not
distinguishable from zero" として棄却します。baseline比較（全パラメータの平均を上回るか）だけ
では足りません。baseline を上回っていても t が小さければ、それは依然として偶然の範囲内です。

### CLIオプション（`src/screener/backtest/cli.py`）

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--symbols` | （`--from-passed` と排他・必須） | カンマ区切りのティッカー、例: `TSLA,MSTR` |
| `--from-passed` | （`--symbols` と排他・必須） | screen の `passed.csv` へのパス |
| `--mode` | `swing` | `swing`（日足、2〜7セッション保有、デフォルト）または `intraday`（分足、引けでフラット） |
| `--strategy` | `all` | 選んだモードの戦略名、または `all` |
| `--data-dir` | `None`（モード依存） | バーのディレクトリ（デフォルト: swing は `data/cache`、intraday は `data/minute`） |
| `--bar-minutes` | `None`（モード依存） | intraday専用: 1分足をこのバー幅に集約する（デフォルト5） |

account and risk（資金とリスク）:

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--capital-usd` | `4000.0` | 口座の資金額（USD） |
| `--risk-pct` | `1.0` | 1トレードあたりリスクにさらす資金の割合（%） |
| `--fractional` | `False`（フラグ） | 端株（fractional shares）を許可する |
| `--target-jpy` | `None`（モード依存） | 利益目標（円）。swing は1取引あたり（デフォルト10,000）、intraday は1日あたり（デフォルト5,000） |
| `--fx-rate` | `None` | USD/JPY を固定する |

costs（コスト）:

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--slippage-bps` | `5.0` | 片道スリッページ（ベーシスポイント） |
| `--commission-per-share` | `0.0` | 1株あたりの手数料 |

walk-forward:

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--train-sessions` | `None`（モード依存） | 訓練 window の取引日数（デフォルト: swing 500、intraday 250） |
| `--test-sessions` | `None`（モード依存） | out-of-sample window の取引日数（デフォルト: swing 125、intraday 60） |
| `--objective` | `expectancy` | 訓練 window が最大化する対象（`expectancy` / `total` /
  `sharpe` / `profit_factor` から選択） |
| `--min-train-trades` | `20` | 訓練取引数がこれ未満のパラメータ組み合わせは無視する |
| `--no-baseline` | `False`（フラグ） | 全組み合わせの out-of-sample baseline 計算を省略する（高速化） |

| フラグ | デフォルト | 説明 |
| --- | --- | --- |
| `--out-dir` | `data/backtest` | 出力ディレクトリ |
| `-v`, `--verbose` | `False`（フラグ） | デバッグログを出力する |

`--data-dir` / `--train-sessions` / `--test-sessions` / `--target-jpy` / `--bar-minutes` はいず
れもモード依存のデフォルトを持ちます（`cli.py` の `MODE_DEFAULTS`）。値を明示すれば、モードに
関係なくその値が使われます。

### 起動時の表示

実行のたびに `main()` は次の2つを表示します。

- 「2R の勝ちトレードで目標に届くのに必要なリスク率」（`target_jpy / usdjpy / 2 / capital *
  100`）。資金 $4,000 で目標 10,000 JPY なら **0.83%/取引** です。`--risk-pct` がこれを下回っ
  ていれば、2Rの勝ちを積み重ねても目標には届きません。
- swing モードで目標到達が 0% かつ expectancy が正のとき、「ポジションサイズが制約であって戦略
  ではない。`--risk-pct` を上げるか `--fractional` を使え」という趣旨の一文を表示します。

### コストモデル（`costs.py`）

米国株は手数料無料のブローカーが多いため、成否を決めるのは slippage です。デフォルトは片道 5
bps で、意図的に甘くしていません。`--slippage-bps` で変更可能です。

### ポジションサイジング（`engine.py` の `Sizer`）

リスクベース（`--risk-pct`、デフォルト1%、ストップまでの距離で株数を決める）と、資金による上限
の小さい方を採用します。

## フレームワークの検証結果

これは合成データによる**フレームワーク自体の検証**であり、実市場の成績ではありません。

walk-forward の枠組みが正しく機能するかを、答えが構成上わかっている合成データを使って**両方向
で**検証しました。片方はエッジを持たない純粋なランダムウォーク、もう片方は人工的に本物のORBエッ
ジを埋め込んだ系列です。

| 銘柄 | 構造 | In-sample | Out-of-sample | 判定 |
|---|---|---|---|---|
| RANDOM | エッジ無し（ランダムウォーク） | 目標達成日 29.2%、Sharpe 0.54 | -118 JPY/取引、合計 -36,242 JPY | does NOT work |
| TRENDY | エッジ有り（ORBドリフトを埋込） | +6,687 JPY/取引 | +6,964 JPY/取引、目標達成日 63.0% | positive |

**RANDOM の行が重要**です。エッジが存在しないデータでも、最適化は in-sample で Sharpe 0.54・目
標達成日29.2% という「それらしい」結果を見つけてしまいました。walk-forward がそれを out-of-
sample で -118 JPY/取引として正しく棄却しています。この検証がなければ「勝ちパターンを発見した」
と誤認するところでした。

同じ検証を `swing` モードでも行いました。こちらも合成日足データでエッジの有無を作り分けていま
す。

| 銘柄 | 構造 | Out-of-sample | t統計量 | 判定 |
|---|---|---|---|---|
| DRANDOM | エッジ無し（ランダムウォーク） | +165 JPY/取引（122取引） | +0.21 | not distinguishable from zero |
| DTREND | エッジ有り（20日高値ブレイク後に5日ドリフト） | +6,969 JPY/取引（99取引）、目標達成 43.4%、平均保有 5.7日 | +5.95 | positive |

ここでも同じ落とし穴が確認できます。DRANDOM は expectancy が正（+165円）で、all-parameter
baseline（-691円）も上回っていました。baseline比較だけを見れば「positive」と誤判定するところ
でしたが、t = +0.21 は `|t| = 2` を大きく下回っており、実際には棄却されます。t統計量による判定
が無ければ、ここでもノイズを「勝ちパターン」と誤認していました。

もう一つの発見です。最初の実行では DRANDOM・DTREND とも目標到達 0.0% でした。1%リスク（$40）
では2Rでも$80のはずが、整数株への切り捨てで実際の平均利益が$30程度まで潰れていたためです。
`--risk-pct 1.7 --fractional` に変えたところ 43.4% になりました。**$4,000 の口座では端株対応の
有無が結果を左右します。**

## 出力ファイル（バックテスト）

`--out-dir`（デフォルト `data/backtest`）配下に以下が生成されます。ファイル名にはモード
（`swing` / `intraday`）が含まれます。

- **`data/backtest/summary.csv`** — 銘柄×戦略×モードごとの IS/OOS 全指標、
  `baseline_expectancy_jpy`、`degradation` をまとめたもの。OOS expectancy の降順でソートされま
  す。
- **`data/backtest/<SYMBOL>-<mode>-<strategy>-oos-trades.csv`** — out-of-sample の全取引
  （entry/exit の時刻・価格、株数、`pnl_usd`、`pnl_jpy`、`exit_reason`）。
- **`data/backtest/<SYMBOL>-<mode>-<strategy>-windows.csv`** — window ごとの採用パラメータ、
  train/test の expectancy、baseline。

主な指標（`stats.py` の `Summary`）: `n_trades`, `n_days`, `win_rate`, `total_pnl_jpy`,
`expectancy_jpy`, `avg_win_jpy`, `avg_loss_jpy`, `profit_factor`, `max_drawdown_jpy`,
`median_daily_pnl_jpy`, `pct_days_at_target`, `pct_trades_at_target`, `avg_hold_days`, `sharpe`,
`t_stat`, `stopped_out_pct`, `hit_target_pct`, `timed_out_pct`。

`pct_trades_at_target` は swing モードの主要指標です。「1取引+10,000円に届いた取引の割合」とい
う問いに直接答える数値だからです。`pct_days_at_target` は intraday 用で、「1日+5,000円に届いた
日は何%か」に答えます。`stats.py` はこう注意しています。合計利益が大きくても、1回の大勝ちが全
体を担いでいれば「毎回・毎日目標を稼ぐ」こととは別の話です。

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

いまの目標は「2〜7日のスイングで1取引あたり +10,000円」です。資金 $4,000、USD/JPY 150円換算
では次のようになります。

- 10,000 JPY = $66.67 = 口座の **1.67%/取引**
- 2R の勝ちトレードで届かせるには **0.83%/取引** のリスクが必要（1R = $33.33）
- 平均5日保有で市場に居続けると、年間およそ **50取引**（250営業日 ÷ 5日）

同じ「1取引 +10,000円」という目標でも、資金額によって難易度は変わります。

| 資金 | 1取引の目標(%) | 2Rで必要なリスク率/取引 | 年間取引数(目安) | 年率換算(目安) |
| --- | --- | --- | --- | --- |
| $4,000 (60万円) | 1.67% | 0.83% | 50 | 83% |
| $10,000 (150万円) | 0.67% | 0.33% | 50 | 33% |
| $20,000 (300万円) | 0.33% | 0.17% | 50 | 17% |
| $25,000 (375万円) | 0.27% | 0.13% | 50 | 13% |

デイトレで1日+5,000円（年率208%）に比べ、スイングで1取引+10,000円は現実的な水準に下りていま
す。ただし勝率と期待値次第であり、目標額そのものが達成を保証するわけではありません。年間取引
数（50取引）は平均保有5日を前提にした目安であり、実際の頻度は戦略のシグナル発生率に依存します。

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

## Xの抽出データをスプレッドシートに追記する（x_inbox）

スクリーニングとは独立した補助ツールです。`x_inbox/` に置かれたCSVを、GitHub Actions が既存の
Googleスプレッドシートの末尾に追記します。Grokなどの外部ツールがCSVをpushするだけで、シートが
更新される、という使い方を想定しています。

- **Xのトークンは不要**です。必要なのはGoogleのサービスアカウント1つだけです。
- 同じ行を2回pushしても二重には入りません（`id` 列、無ければ行の内容のハッシュで判定）。
- 列の順番はシートの1行目に合わせます。CSVの列順は問いません。
- 追記済みのCSVは `x_inbox/archive/<日付>/` に移動され、コミットとして残ります。

```bash
# 何が追記されるかだけ見る（Googleには接続しない）
PYTHONPATH=src ./.venv/bin/python -m x_inbox --dry-run

# 接続テストだけ行う
PYTHONPATH=src ./.venv/bin/python -m x_inbox --check
```

セットアップ手順（サービスアカウントの作り方、シートの共有、GitHubへの登録）は
[`x_inbox/README.md`](x_inbox/README.md) にまとめてあります。

## テスト

```bash
./.venv/bin/python -m pytest tests/ -q
```

`tests/test_screener.py`・`tests/test_backtest.py`・`tests/test_swing.py`・
`tests/test_x_inbox.py` に合わせて107個のテストがあり、いずれも合成データのみを使ったオフライ
ンテストです。ネットワークアクセスも API キーも不要です。

テストは実際にバグを検出しています。`rsi()` が上昇継続で損失ゼロになる区間でクラッシュする不具
合をテストが捕捉し修正しました。

日足キャッシュが取得済みの期間を記録していなかった不具合も同様に修正しています。`--history-days 730` で取得した後に `--history-days 7300` を指定しても、12時間以内なら短いキャッシュがそのまま使われていました。現在は `data/cache/_coverage.json` に取得開始日を記録し、要求された範囲を満たさないキャッシュはミス扱いにします。

## 制約

- 日足スクリーニングは `query1.finance.yahoo.com`（yfinance 経由）と `stooq.com` への、分足デー
  タ取得は `data.alpaca.markets` への外向きのネットワークアクセスを必要とします。サンドボックス
  環境やプロキシ制限のある環境ではこれらへの通信がブロックされる場合があり、その場合は実行して
  もデータが取得できずに終了します。
- 売買は一切実行しない（スクリーニング、データ収集、バックテストのみ）。また、バックテストは実
  市場データでは未実行で、検証は合成データによるフレームワークの動作確認にとどまる。
</content>
</invoke>
