# CLAUDE.md

Guidance for Claude Code working in this repository.

**Reply to the user in Japanese.** They are a Japanese retail trader, not a
professional developer, so explain terminal errors and next steps concretely
rather than assuming they can debug a stack trace.

## What this is

A screening and backtesting toolchain for US equities, aimed at one goal the
user stated: **hold a swing position for 2-7 days and make 10,000 JPY per
trade, on an account of about 4,000 USD.** It screens tickers, downloads
chart data, and backtests strategies. It does not place orders and must not
be extended to place orders unless the user explicitly asks.

## Setup (the user is on macOS)

macOS has no `python`, only `python3`. Every command calls the venv
interpreter by path so `activate` is never needed and the `python`/`python3`
distinction never bites. Run everything from the repo root; `PYTHONPATH=src`
is relative to it.

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
PYTHONPATH=src ./.venv/bin/python -m pytest tests/ -q     # 107 tests, offline
```

## Entry points

```bash
# Daily screen -> data/out/passed.csv, metrics_all.csv, charts/, cache in data/cache
PYTHONPATH=src ./.venv/bin/python -m screener --universe sp500 --history-days 7300

# Swing backtest with walk-forward (the default mode)
PYTHONPATH=src ./.venv/bin/python -m screener.backtest \
  --from-passed data/out/passed.csv --risk-pct 1.7 --fractional

# Minute bars from Alpaca, only needed for --mode intraday
export APCA_API_KEY_ID=... APCA_API_SECRET_KEY=...
PYTHONPATH=src ./.venv/bin/python -m screener.minutes --from-passed data/out/passed.csv --years 5
```

`src/x_inbox/` is a separate tool that has nothing to do with trading: it
appends CSVs dropped into `x_inbox/` to an existing Google Sheet, driven by
`.github/workflows/x-inbox-append.yml`. It needs a Google service account
(secret `GOOGLE_SERVICE_ACCOUNT_JSON`) and no X/Twitter credentials.
`--dry-run` and `--check` never write. Setup lives in `x_inbox/README.md`.

```bash
PYTHONPATH=src ./.venv/bin/python -m x_inbox --dry-run
```

## Invariants — do not break these

These exist because breaking them makes a losing strategy backtest green.

- **No lookahead.** A signal on bar `i` fills at bar `i+1`'s open, never at
  bar `i`'s close or at the trigger price. Indicators are causal
  (`rolling(...).shift(1)` where the current bar would otherwise leak in).
- **A bar spanning both stop and target resolves against the position.**
  Bars do not record which came first; guessing favourably is how a losing
  strategy shows a profit.
- **Walk-forward is the product, not a check.** Never present an in-sample
  number as the result. `screener/backtest/walkforward.py` fits on a train
  window and scores only on the test window that follows.
- **`|t| < 2` is not an edge.** A positive out-of-sample expectancy over a
  few dozen trades with a spread of thousands of yen is noise. The verdict
  logic rejects it before any baseline comparison; keep that gate.
- **The all-parameter baseline stays.** If the chosen parameters do not beat
  the mean OOS result across every combination, the optimisation added
  nothing and the report must say so.

If a change makes results look dramatically better, suspect the change
before believing the result.

## Gotchas already hit

- `--universe sp500 --history-days 7300` fetches 500 tickers over 20 years:
  tens of minutes, and Yahoo rate-limits. Suggest `--limit 30` first.
  `no data for N ticker(s)` warnings are normal and not fatal.
- `macross` fires rarely. On 500 training sessions no parameter set reaches
  `--min-train-trades 20`, so it reports zero windows. Use
  `--train-sessions 750` or `--min-train-trades 10`.
- On a 4,000 USD account, whole-share flooring destroys position sizes on
  high-priced stocks. `--fractional` matters; without it the target is
  unreachable regardless of strategy.
- A 2R winner only clears 10,000 JPY at about 0.83% risk per trade. The CLI
  prints this at startup — read it before blaming a strategy.
- The daily cache records its fetched range in `data/cache/_coverage.json`.
  A cache fetched for 730 days is a miss for a run asking for 20 years.
- Sandboxed environments often block `query1.finance.yahoo.com`,
  `stooq.com` and `data.alpaca.markets`. Then no data arrives and the run
  exits; that is the network, not a bug.

## State

Done: screening, daily and minute data collection, swing and intraday
backtesting, walk-forward with significance testing, and the `x_inbox`
CSV-to-Google-Sheet appender. 107 offline tests pass.

Not done: **no run against live market data has ever happened.** Everything
so far was verified on synthetic data. The immediate next step is the real
S&P 500 run above.

Expect the textbook strategies (donchian, rsi, macross) to come back "does
NOT work" or "not distinguishable from zero" on real data. That is the
honest outcome for patterns everyone tries first, not a defect to tune away.
Report it plainly rather than searching parameters until something passes.
