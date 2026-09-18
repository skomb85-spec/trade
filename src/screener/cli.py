"""Command-line entry point.

    python -m screener --capital-usd 4000 --target-jpy 5000

Screens US listings for ones where the account can plausibly extract the
daily profit target, and saves chart data for those. Writes under
``--out-dir``:

  metrics_all.csv   every ticker screened, with its measured numbers, the
                    position the account could take, the expected daily
                    yield, and -- on a miss -- why it missed
  passed.csv        the tickers that cleared the target
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

DEFAULT_TARGET_JPY = 5000.0
DEFAULT_CAPITAL_USD = 4000.0
# Share of the day's high-low range a strategy actually banks. 0.30 is
# already generous; turn it down before trusting an encouraging result.
DEFAULT_CAPTURE_RATE = 0.30


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="screener",
        description="Find US stocks where a given account size can plausibly earn a daily JPY target.",
    )
    g = p.add_argument_group("target and account")
    g.add_argument("--target-jpy", type=float, default=DEFAULT_TARGET_JPY,
                   help=f"daily profit target in yen (default: {DEFAULT_TARGET_JPY:.0f})")
    g.add_argument("--capital-usd", type=float, default=DEFAULT_CAPITAL_USD,
                   help=f"account size in USD (default: {DEFAULT_CAPITAL_USD:.0f})")
    g.add_argument("--capture-rate", type=float, default=DEFAULT_CAPTURE_RATE,
                   help=f"fraction of the daily range assumed captured (default: {DEFAULT_CAPTURE_RATE})")
    g.add_argument("--fractional", action="store_true",
                   help="allow fractional shares instead of whole shares only")
    g.add_argument("--capital-sweep", default="",
                   help="comma-separated account sizes in USD to compare, e.g. 4000,10000,20000")

    g = p.add_argument_group("universe")
    g.add_argument("--universe", choices=("seed", "sp500", "all"), default="seed",
                   help="which tickers to screen (default: seed, the bundled offline list)")
    g.add_argument("--universe-file", default=None,
                   help="path to a .txt (one ticker per line) or .csv to use instead")
    g.add_argument("--always-include", default="",
                   help="comma-separated tickers to collect regardless of the screen, e.g. TSLA,NVDA")
    g.add_argument("--limit", type=int, default=None,
                   help="screen only the first N tickers (for a smoke test)")

    g = p.add_argument_group("filters")
    g.add_argument("--min-bars", type=int, default=30,
                   help="reject tickers with fewer bars in the lookback window (default: 30)")
    g.add_argument("--min-dollar-volume", type=float, default=20_000_000.0,
                   help="minimum average daily dollar volume in USD (default: 20,000,000)")
    g.add_argument("--max-position-pct-of-volume", type=float, default=1.0,
                   help="reject if the position exceeds this %% of daily turnover (default: 1.0)")
    g.add_argument("--min-range-jpy", type=float, default=0.0,
                   help="optional extra filter: minimum per-share daily range in yen (default: off)")

    g = p.add_argument_group("data")
    g.add_argument("--lookback-days", type=int, default=180,
                   help="calendar days of history used to measure the range (default: 180)")
    g.add_argument("--history-days", type=int, default=730,
                   help="calendar days of chart data saved for collected tickers (default: 730)")
    g.add_argument("--fx-rate", type=float, default=None,
                   help="fix USD/JPY instead of looking it up, for reproducible runs")
    g.add_argument("--out-dir", default="data/out", help="output directory (default: data/out)")
    g.add_argument("--cache-dir", default="data/cache", help="OHLCV cache directory")
    g.add_argument("--cache-max-age-hours", type=float, default=12.0,
                   help="reuse cached bars younger than this (default: 12)")
    g.add_argument("--no-stooq", action="store_true", help="disable the Stooq fallback")
    g.add_argument("--no-charts", action="store_true", help="screen only; skip writing per-ticker CSVs")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not 0 < args.capture_rate <= 1:
        log.error("--capture-rate must be in (0, 1]")
        return 2

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
    need_pct = screen.required_range_pct(
        args.target_jpy, args.capital_usd, args.capture_rate, usdjpy
    )
    log.info("USD/JPY = %.4f (%s)", usdjpy, fx_source)
    log.info(
        "target %.0f JPY/day on $%.0f at %.0f%% capture -> need an average daily range of %.2f%% of price",
        args.target_jpy, args.capital_usd, args.capture_rate * 100, need_pct,
    )

    result = fetch.fetch_ohlcv(
        tickers, start=history_start, end=end, cache_dir=cache_dir,
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
        target_jpy=args.target_jpy,
        capital_usd=args.capital_usd,
        usdjpy=usdjpy,
        capture_rate=args.capture_rate,
        fractional_shares=args.fractional,
        min_bars=args.min_bars,
        min_dollar_volume=args.min_dollar_volume,
        max_position_pct_of_volume=args.max_position_pct_of_volume,
        min_range_jpy=args.min_range_jpy,
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
    print(f"\ntarget {args.target_jpy:,.0f} JPY/day  account ${args.capital_usd:,.0f}  "
          f"capture {args.capture_rate:.0%}  USD/JPY {usdjpy:,.2f} ({fx_source})")
    print(f"=> needs an average daily range of {need_pct:.2f}% of price")
    print(f"screened {len(table)} ticker(s), {n_pass} passed -> {passed_path}")
    print(f"full table (all tickers, pass or fail) -> {metrics_path}\n")

    cols = ["ticker", "last_close_usd", "avg_range_pct", "shares_affordable",
            "position_usd", "expected_daily_jpy", "passes"]
    print(table.head(25)[cols].to_string(index=False))

    if n_pass == 0:
        best = table.iloc[0]
        print(f"\nNothing cleared the target. The closest was {best['ticker']} at "
              f"{best['expected_daily_jpy']:,.0f} JPY/day. To reach "
              f"{args.target_jpy:,.0f} JPY you would need about "
              f"${best['required_capital_usd']:,.0f} of capital in it, or a wider universe "
              f"(--universe sp500 / all), or a lower --target-jpy.")

    sweep = [float(x) for x in args.capital_sweep.split(",") if x.strip()]
    if sweep:
        print("\n--- how many tickers clear the target at each account size ---")
        print(f"{'capital$':>10}{'%/day':>9}{'need range%':>13}{'passing':>9}   best ticker")
        for capital in sweep:
            swept = screen.apply(
                table.drop(columns=[c for c in ("passes", "reason", "collect",
                                                "always_include", "shares_affordable",
                                                "position_usd", "expected_daily_jpy",
                                                "required_capital_usd", "required_range_pct")
                                    if c in table.columns]),
                target_jpy=args.target_jpy, capital_usd=capital, usdjpy=usdjpy,
                capture_rate=args.capture_rate, fractional_shares=args.fractional,
                min_bars=args.min_bars, min_dollar_volume=args.min_dollar_volume,
                max_position_pct_of_volume=args.max_position_pct_of_volume,
                min_range_jpy=args.min_range_jpy,
            )
            hits = swept[swept["passes"]]
            best = hits.iloc[0]["ticker"] if not hits.empty else "-"
            print(f"{capital:>10,.0f}"
                  f"{args.target_jpy / usdjpy / capital * 100:>9.3f}"
                  f"{screen.required_range_pct(args.target_jpy, capital, args.capture_rate, usdjpy):>13.2f}"
                  f"{len(hits):>9}   {best}")

    for t in always:
        row = table[table["ticker"].str.upper() == t]
        if not row.empty and not bool(row.iloc[0]["passes"]):
            r = row.iloc[0]
            print(f"\n[always-include] {t}: expected {r['expected_daily_jpy']:,.0f} JPY/day "
                  f"-- below target, collected anyway. {r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
