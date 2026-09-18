"""Minute bars from Alpaca, for intraday pattern work.

Yahoo caps intraday history hard -- 1m for 7 days, 5m for 60, 1h for 730 --
which is far too short a sample to tell a real intraday edge from noise.
Alpaca's free tier serves 1-minute bars back to 2016 on the IEX feed, which
is the cheapest way to get a decade of minute data.

The IEX caveat matters and is not cosmetic: IEX is roughly 2.5% of US
equity volume, so these bars are a *sample* of the tape. Volume is
understated, and a thin symbol can show gaps where consolidated data would
show trades. Liquid large caps track the consolidated tape closely; small
caps do not. Anything sized off these volumes should be re-checked against
a consolidated feed before it sees real money.

Credentials come from the environment:

    export APCA_API_KEY_ID=...
    export APCA_API_SECRET_KEY=...
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Alpaca has no equity data before this date.
EARLIEST = dt.date(2016, 1, 1)


class MissingCredentials(RuntimeError):
    pass


def _client():
    try:
        from alpaca.data.historical import StockHistoricalDataClient
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise RuntimeError("alpaca-py is not installed: pip install alpaca-py") from exc

    key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise MissingCredentials(
            "set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
            "(free keys at https://app.alpaca.markets/signup)"
        )
    return StockHistoricalDataClient(key, secret)


def _request(symbols: list[str], start: dt.date, end: dt.date, minutes: int):
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    return StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame(minutes, TimeFrameUnit.Minute),
        start=dt.datetime.combine(start, dt.time.min),
        end=dt.datetime.combine(end, dt.time.min),
        # RAW keeps the prices actually traded, matching the daily screen.
        adjustment=Adjustment.RAW,
        feed=DataFeed.IEX,
    )


def fetch_minute_bars(
    symbols: list[str], start: dt.date, end: dt.date, minutes: int = 1
) -> dict[str, pd.DataFrame]:
    """Minute bars per symbol over ``[start, end)``. The SDK handles paging."""
    client = _client()
    response = client.get_stock_bars(_request(symbols, start, end, minutes))
    df = response.df
    if df is None or df.empty:
        return {}

    out: dict[str, pd.DataFrame] = {}
    # The SDK returns a (symbol, timestamp) MultiIndex.
    for symbol in df.index.get_level_values(0).unique():
        frame = df.xs(symbol, level=0).copy()
        frame.index = pd.to_datetime(frame.index)
        if getattr(frame.index, "tz", None) is not None:
            # Bars are UTC; New York local time is what an intraday pattern
            # is actually indexed on (the open is 09:30 there, always).
            frame.index = frame.index.tz_convert("America/New_York")
        frame.index.name = "timestamp"
        out[str(symbol)] = frame.sort_index()
    return out


def _write(frame: pd.DataFrame, path_no_ext: Path) -> Path:
    """Parquet when pyarrow is around, gzipped CSV otherwise."""
    path_no_ext.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow  # noqa: F401

        path = path_no_ext.with_suffix(".parquet")
        frame.to_parquet(path)
    except ImportError:
        path = path_no_ext.with_suffix(".csv.gz")
        frame.to_csv(path, compression="gzip")
    return path


def download(
    symbols: list[str],
    out_dir: Path,
    start: dt.date | None = None,
    end: dt.date | None = None,
    minutes: int = 1,
) -> dict[str, list[Path]]:
    """Download minute bars a year at a time and write them per symbol-year.

    A decade of 1-minute bars is roughly a million rows per symbol, so the
    year-at-a-time split keeps memory flat and makes a resumed run cheap:
    a year already on disk is skipped.
    """
    start = start or EARLIEST
    end = end or dt.date.today()
    if start < EARLIEST:
        log.warning("Alpaca has no data before %s; starting there", EARLIEST)
        start = EARLIEST

    written: dict[str, list[Path]] = {s: [] for s in symbols}
    for year in range(start.year, end.year + 1):
        y_start = max(start, dt.date(year, 1, 1))
        y_end = min(end, dt.date(year + 1, 1, 1))
        if y_start >= y_end:
            continue

        todo = [s for s in symbols if not _year_done(out_dir, s, year, written)]
        if not todo:
            continue
        log.info("alpaca %dm bars %s: %d symbol(s)", minutes, year, len(todo))

        frames = fetch_minute_bars(todo, y_start, y_end, minutes=minutes)
        for symbol, frame in frames.items():
            if frame.empty:
                continue
            path = _write(frame, out_dir / symbol / f"{symbol}-{year}")
            written[symbol].append(path)
            log.info("  %s %d: %d bars -> %s", symbol, year, len(frame), path)
    return written


def _year_done(out_dir: Path, symbol: str, year: int, written: dict[str, list[Path]]) -> bool:
    base = out_dir / symbol / f"{symbol}-{year}"
    for suffix in (".parquet", ".csv.gz"):
        path = base.with_suffix(suffix)
        if path.exists() and path.stat().st_size > 0:
            written.setdefault(symbol, []).append(path)
            return True
    return False
