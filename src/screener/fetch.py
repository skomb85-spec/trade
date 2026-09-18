"""OHLCV retrieval: yfinance first, Stooq as the fallback.

yfinance reads an undocumented Yahoo endpoint, so it breaks whenever Yahoo
reshapes its site -- it went down for everyone during the February 2025
redesign. Stooq serves plain CSV over a stable URL with no API key, which
makes it a good second leg even though its coverage is thinner.

Prices are deliberately left *unadjusted* (``auto_adjust=False``). The
screen measures how many dollars a share actually travelled on the day, so
split- and dividend-adjusted history would understate older bars.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
STOOQ_CSV_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
COVERAGE_FILE = "_coverage.json"


@dataclass
class FetchResult:
    frames: dict[str, pd.DataFrame]
    sources: dict[str, str]
    failed: dict[str, str]


def _normalise(df: pd.DataFrame) -> pd.DataFrame | None:
    """Coerce a provider frame to a clean DatetimeIndex + OHLCV columns."""
    if df is None or df.empty:
        return None
    df = df.copy()
    df.columns = [str(c).strip().title() for c in df.columns]
    if not all(c in df.columns for c in OHLCV_COLUMNS):
        return None
    df = df[OHLCV_COLUMNS].apply(pd.to_numeric, errors="coerce")
    index = pd.to_datetime(df.index)
    # Yahoo returns tz-aware timestamps for intraday-capable symbols and naive
    # ones elsewhere; normalise to naive so frames from both providers align.
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert(None)
    df.index = index
    df.index.name = "Date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # A bar with no high/low is unusable for a range screen.
    df = df.dropna(subset=["High", "Low", "Close"])
    return df if not df.empty else None


def _cache_path(cache_dir: Path, ticker: str) -> Path:
    # Slashes and colons appear in FX and class-share symbols.
    safe = ticker.replace("/", "_").replace(":", "_").replace("=", "_")
    return cache_dir / f"{safe}.csv"


def _read_coverage(cache_dir: Path | None) -> dict[str, str]:
    """Earliest date each cached ticker was actually *asked* for.

    Without this a cache filled by a 730-day run is served to a later run
    asking for twenty years: the file is fresh and parses fine, it just
    silently holds a fraction of the history. The requested start is the
    only way to tell that apart from a ticker that simply has no older
    data, which is a legitimate short cache.
    """
    if cache_dir is None:
        return {}
    path = cache_dir / COVERAGE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_coverage(cache_dir: Path | None, ticker: str, start: str) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    coverage = _read_coverage(cache_dir)
    previous = coverage.get(ticker)
    # Keep the earliest start ever fetched; a later short run must not
    # shrink what the cache claims to cover.
    if previous is None or str(start) < previous:
        coverage[ticker] = str(start)
    try:
        (cache_dir / COVERAGE_FILE).write_text(json.dumps(coverage, indent=0, sort_keys=True))
    except OSError as exc:
        log.debug("could not update cache coverage: %s", exc)


def _read_cache(cache_dir: Path | None, ticker: str, max_age_hours: float,
                start: str | None = None, coverage: dict[str, str] | None = None
                ) -> pd.DataFrame | None:
    if cache_dir is None:
        return None
    path = _cache_path(cache_dir, ticker)
    if not path.exists():
        return None
    if start is not None:
        covered = (coverage if coverage is not None else _read_coverage(cache_dir)).get(ticker)
        if covered is None or str(start) < covered:
            return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > max_age_hours:
        return None
    try:
        return _normalise(pd.read_csv(path, index_col=0, parse_dates=True))
    except Exception as exc:  # a truncated cache file should never be fatal
        log.debug("ignoring unreadable cache for %s: %s", ticker, exc)
        return None


def _write_cache(cache_dir: Path | None, ticker: str, df: pd.DataFrame) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(_cache_path(cache_dir, ticker))


def fetch_yfinance(
    tickers: list[str], start: str, end: str, chunk_size: int = 50, pause: float = 0.6
) -> dict[str, pd.DataFrame]:
    """Batch-download daily bars. Returns only the tickers that came back."""
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        log.info("yfinance: %d-%d of %d", i + 1, i + len(chunk), len(tickers))
        try:
            raw = yf.download(
                chunk,
                start=start,
                end=end,
                interval="1d",
                auto_adjust=False,
                actions=False,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as exc:
            log.warning("yfinance chunk failed (%s); falling through", exc)
            continue
        if raw is None or raw.empty:
            continue
        for ticker in chunk:
            try:
                # A single-ticker download comes back without the outer level.
                sub = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            frame = _normalise(sub)
            if frame is not None:
                out[ticker] = frame
        if i + chunk_size < len(tickers):
            time.sleep(pause)
    return out


def fetch_stooq(ticker: str, start: str, end: str, timeout: int = 30) -> pd.DataFrame | None:
    """Daily bars from Stooq. US listings carry a ``.us`` suffix there."""
    symbol = ticker.lower().replace("-", "-")
    if not symbol.endswith(".us") and "=" not in symbol:
        symbol = f"{symbol}.us"
    try:
        df = pd.read_csv(STOOQ_CSV_URL.format(symbol=symbol), index_col=0, parse_dates=True)
    except Exception as exc:
        log.debug("stooq failed for %s: %s", ticker, exc)
        return None
    frame = _normalise(df)
    if frame is None:
        return None
    return frame.loc[str(start) : str(end)]


def fetch_ohlcv(
    tickers: list[str],
    start: str,
    end: str,
    cache_dir: Path | None = None,
    cache_max_age_hours: float = 12.0,
    use_stooq_fallback: bool = True,
) -> FetchResult:
    """Fetch daily bars for every ticker, preferring cache, then Yahoo, then Stooq."""
    frames: dict[str, pd.DataFrame] = {}
    sources: dict[str, str] = {}
    failed: dict[str, str] = {}

    coverage = _read_coverage(cache_dir)
    pending: list[str] = []
    for ticker in tickers:
        cached = _read_cache(cache_dir, ticker, cache_max_age_hours, start, coverage)
        if cached is not None:
            frames[ticker] = cached.loc[str(start) : str(end)]
            sources[ticker] = "cache"
        else:
            pending.append(ticker)

    if pending:
        for ticker, frame in fetch_yfinance(pending, start, end).items():
            frames[ticker] = frame
            sources[ticker] = "yfinance"
            _write_cache(cache_dir, ticker, frame)
            _write_coverage(cache_dir, ticker, start)

    missing = [t for t in tickers if t not in frames or frames[t].empty]
    if missing and use_stooq_fallback:
        log.info("stooq fallback for %d ticker(s)", len(missing))
        for ticker in missing:
            frame = fetch_stooq(ticker, start, end)
            if frame is not None and not frame.empty:
                frames[ticker] = frame
                sources[ticker] = "stooq"
                _write_cache(cache_dir, ticker, frame)
                _write_coverage(cache_dir, ticker, start)

    for ticker in tickers:
        if ticker not in frames or frames[ticker].empty:
            frames.pop(ticker, None)
            sources.pop(ticker, None)
            failed[ticker] = "no data from yfinance or stooq"

    return FetchResult(frames=frames, sources=sources, failed=failed)
