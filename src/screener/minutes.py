"""Download Alpaca minute bars for the tickers the screen collected.

    export APCA_API_KEY_ID=... APCA_API_SECRET_KEY=...
    python -m screener.minutes --from-passed data/out/passed.csv --years 5

Run the daily screen first; this reads its output so the two stay in step.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

import pandas as pd

from . import alpaca

log = logging.getLogger("minutes")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="screener.minutes",
        description="Download Alpaca (IEX) minute bars for a set of tickers.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--tickers", help="comma-separated tickers, e.g. TSLA,MSTR,SOXL")
    src.add_argument("--from-passed", help="path to the screen's passed.csv")
    p.add_argument("--years", type=int, default=5,
                   help="how many years back to fetch (default: 5; Alpaca starts at 2016)")
    p.add_argument("--start", default=None, help="explicit start date YYYY-MM-DD, overrides --years")
    p.add_argument("--end", default=None, help="explicit end date YYYY-MM-DD (default: today)")
    p.add_argument("--minutes", type=int, default=1,
                   help="bar size in minutes (default: 1). 5 cuts the data volume 5x.")
    p.add_argument("--out-dir", default="data/minute", help="output directory (default: data/minute)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.tickers:
        symbols = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        path = Path(args.from_passed)
        if not path.exists():
            log.error("%s not found -- run the daily screen first", path)
            return 1
        df = pd.read_csv(path)
        if df.empty:
            log.error("%s is empty: nothing cleared the screen, so there is nothing to fetch", path)
            return 1
        symbols = [str(t).upper() for t in df["ticker"].tolist()]
    if not symbols:
        log.error("no tickers to fetch")
        return 1

    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    if args.start:
        start = dt.date.fromisoformat(args.start)
    else:
        start = end.replace(year=end.year - args.years)

    days = (end - max(start, alpaca.EARLIEST)).days
    # ~390 minute bars per symbol per trading day, ~252 trading days a year.
    est_rows = len(symbols) * days * (252 / 365) * (390 / args.minutes)
    log.info("%d symbol(s), %s to %s, %dm bars -- roughly %.1fM rows",
             len(symbols), start, end, args.minutes, est_rows / 1e6)

    try:
        written = alpaca.download(symbols, Path(args.out_dir), start=start, end=end,
                                  minutes=args.minutes)
    except alpaca.MissingCredentials as exc:
        log.error("%s", exc)
        return 2

    total = sum(len(paths) for paths in written.values())
    got = [s for s, paths in written.items() if paths]
    missing = [s for s, paths in written.items() if not paths]
    print(f"\nwrote {total} file(s) for {len(got)} symbol(s) under {args.out_dir}")
    if missing:
        print(f"no data returned for: {', '.join(sorted(missing))}")
    print("\nNote: these are IEX bars (~2.5% of US volume). Prices track the "
          "consolidated tape closely for liquid names; volume does not. "
          "Re-check anything volume-sensitive against a consolidated feed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
