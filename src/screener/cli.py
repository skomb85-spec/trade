"""Command-line entry point.

    python -m screener --threshold-jpy 5000 --always-include TSLA

Writes three things under ``--out-dir``:

  metrics_all.csv   every ticker in the universe with its measured numbers,
                    whether it passed or not, and why it failed
  passed.csv        the tickers that cleared the threshold
  charts/<T>.csv    daily OHLCV for each collected ticker
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

from . import fetch, fx, metrics, screen, universe

log = logging.getLogger("screener")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="screener",
        description="Collect chart data for US stocks whose average daily range exceeds a JPY threshold.",
    )
    p.add_argument("--threshold-jpy", type=float, default=5000.0,
                   help="minimum average daily high-low range, in yen (default: 5000)")
    p.add_argument("--universe", choices=("seed", "sp500", "all"), default="seed",
                   help="which tickers to screen (default: seed, the bundled offline list)")
    p.add_argument("--universe-file", default=None,
                   help="path to a .txt (one ticker per line) or .csv to use instead")
    p.add_argument("--always-include", default="",
                   help="comma-separated tickers to collect regardless of the threshold, e.g. TSLA,NVDA")
    p.add_argument("--lookback-days", type=int, default=180,
                   help="calendar days of history used to measure the range (default: 180)")
    p.add_argument("--history-days", type=int, default=730,
                   help="calendar days of chart data saved for collected tickers (default: 730)")
    p.add_argument("--min-bars", type=int, default=30,
                   help="reject tickers with fewer bars in the lookback window (default: 30)")
    p.add_argument("--min-dollar-volume", type=float, default=0.0,
                   help="minimum average daily dollar volume, in USD (default: no filter)")
    p.add_argument("--require-median", action="store_true",
                   help="also require the median range to clear the threshold, not just the mean")
    p.add_argument("--fx-rate", type=float, default=None,
                   help="fix USD/JPY instead of looking it up, for reproducible runs")
    p.add_argument("--out-dir", default="data/out", help="output directory (default: data/out)")
    p.add_argument("--cache-dir", default="data/cache", help="OHLCV cache directory")
    p.add_argument("--cache-max-age-hours", type=float, default=12.0)
    p.add_argument("--no-stooq", action="store_true", help="disable the Stooq fallback")
    p.add_argument("--no-charts", action="store_true", help="screen only; skip writing per-ticker CSVs")
    p.add_argument("--limit", type=int, default=None, help="screen only the first N tickers (for a smoke test)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    out_dir = Path(args.out_dir)
    charts_dir = out_dir / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir) if args.cache_dir else None

    today = dt.date.today()
    screen_start = (today - dt.timedelta(days=args.lookback_days)).isoformat()
    history_start = (today - dt.timedelta(days=args.history_days)).isoformat()
    end = (today + dt.timedelta(days=1)).isoformat()

    tickers = universe.load_universe(args.universe, args.universe_file)
    always = [t.strip().upper() for t in args.always_include.split(",") if t.strip()]
    if args.limit:
        tickers = tickers[: args.limit]
    # A ticker named in --always-include must be screened even if the
    # universe or --limit left it out, otherwise it can never be collected.
    for t in always:
        if t not in tickers:
            tickers.append(t)
    log.info("universe: %d ticker(s)", len(tickers))

    usdjpy, fx_source = fx.get_usdjpy(args.fx_rate)
    log.info("USD/JPY = %.4f (%s); threshold %.0f JPY = %.2f USD",
             usdjpy, fx_source, args.threshold_jpy, args.threshold_jpy / usdjpy)

    result = fetch.fetch_ohlcv(
        tickers,
        start=history_start,
        end=end,
        cache_dir=cache_dir,
        cache_max_age_hours=args.cache_max_age_hours,
        use_stooq_fallback=not args.no_stooq,
    )
    if result.failed:
        log.warning("no data for %d ticker(s): %s",
                    len(result.failed), ", ".join(sorted(result.failed)[:20]))
    if not result.frames:
        log.error("no data fetched for any ticker -- check network access to Yahoo/Stooq")
        return 1

    # Measure over the shorter lookback window, but keep the full history
    # around so the saved charts are longer than the screen itself.
    windowed = {t: df.loc[screen_start:] for t, df in result.frames.items()}
    table = metrics.build_table(windowed, usdjpy, sources=result.sources)
    if table.empty:
        log.error("fetched data but could not compute metrics for any ticker")
        return 1

    table = screen.apply(
        table,
        threshold_jpy=args.threshold_jpy,
        min_bars=args.min_bars,
        min_dollar_volume=args.min_dollar_volume,
        require_median=args.require_median,
        always_include=always,
    )
    table.insert(1, "usdjpy", round(usdjpy, 4))

    metrics_path = out_dir / "metrics_all.csv"
    passed_path = out_dir / "passed.csv"
    table.to_csv(metrics_path, index=False)
    table[table["passes"]].to_csv(passed_path, index=False)

    collect = table[table["collect"]]["ticker"].tolist()
    if not args.no_charts and collect:
        charts_dir.mkdir(parents=True, exist_ok=True)
        for ticker in collect:
            frame = result.frames.get(ticker)
            if frame is not None and not frame.empty:
                frame.to_csv(charts_dir / f"{ticker.replace('/', '_')}.csv")
        log.info("wrote %d chart file(s) to %s", len(collect), charts_dir)

    n_pass = int(table["passes"].sum())
    print(f"\nUSD/JPY {usdjpy:,.2f} ({fx_source})  "
          f"threshold {args.threshold_jpy:,.0f} JPY = {args.threshold_jpy / usdjpy:,.2f} USD")
    print(f"screened {len(table)} ticker(s), {n_pass} passed -> {passed_path}")
    print(f"full table (all tickers, pass or fail) -> {metrics_path}\n")

    cols = ["ticker", "last_close_usd", "avg_range_usd", "avg_range_jpy", "median_range_jpy", "passes"]
    head = table.head(25)[cols].to_string(index=False)
    print(head)

    for t in always:
        row = table[table["ticker"].str.upper() == t]
        if not row.empty and not bool(row.iloc[0]["passes"]):
            r = row.iloc[0]
            print(f"\n[always-include] {t}: avg range {r['avg_range_jpy']:,.0f} JPY "
                  f"(${r['avg_range_usd']:,.2f}) -- below threshold, collected anyway. "
                  f"{r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
