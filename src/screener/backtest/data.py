"""Load the minute bars written by ``screener.minutes``."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

MARKET_OPEN = dt.time(9, 30)
MARKET_CLOSE = dt.time(16, 0)
COLUMNS = ["open", "high", "low", "close", "volume"]


def load_symbol(data_dir: Path, symbol: str) -> pd.DataFrame:
    """Concatenate every per-year file for one symbol, oldest first."""
    folder = Path(data_dir) / symbol
    if not folder.is_dir():
        raise FileNotFoundError(
            f"no minute data for {symbol} under {data_dir} -- run screener.minutes first"
        )

    frames: list[pd.DataFrame] = []
    for path in sorted(folder.iterdir()):
        if path.suffix == ".parquet":
            frames.append(pd.read_parquet(path))
        elif path.name.endswith(".csv.gz"):
            frames.append(pd.read_csv(path, index_col=0, parse_dates=True))
    if not frames:
        raise FileNotFoundError(f"{folder} holds no .parquet or .csv.gz files")

    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df.columns = [str(c).lower() for c in df.columns]
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{symbol}: minute data is missing columns {missing}")

    index = pd.to_datetime(df.index)
    if getattr(index, "tz", None) is None:
        index = index.tz_localize("America/New_York")
    else:
        index = index.tz_convert("America/New_York")
    df.index = index
    df.index.name = "timestamp"
    return df


def regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Keep 09:30-16:00 New York only.

    Pre- and post-market bars on the IEX feed are thin enough that a fill
    there is largely fictional, and including them would flatter any
    strategy that trades the open.
    """
    t = df.index.time
    return df[(t >= MARKET_OPEN) & (t < MARKET_CLOSE)]


def sessions(df: pd.DataFrame) -> list[tuple[dt.date, pd.DataFrame]]:
    """Split into trading days, dropping any stub session."""
    out: list[tuple[dt.date, pd.DataFrame]] = []
    for day, bars in df.groupby(df.index.date, sort=True):
        # A day with a handful of bars is a holiday, an outage, or a symbol
        # that barely traded on IEX; none of them backtest meaningfully.
        if len(bars) >= 30:
            out.append((day, bars))
    return out


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1-minute bars up to a coarser interval."""
    if minutes <= 1:
        return df
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    cols = {k: v for k, v in agg.items() if k in df.columns}
    return df.resample(f"{minutes}min").agg(cols).dropna(subset=["open", "high", "low", "close"])
