"""USD/JPY rate used to express dollar ranges in yen."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def from_yfinance(symbol: str = "JPY=X") -> float | None:
    try:
        import yfinance as yf

        hist = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
        if hist is not None and not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception as exc:
        log.debug("yfinance FX lookup failed: %s", exc)
    return None


def from_stooq() -> float | None:
    try:
        import pandas as pd

        df = pd.read_csv("https://stooq.com/q/d/l/?s=usdjpy&i=d", index_col=0, parse_dates=True)
        if not df.empty:
            return float(df["Close"].dropna().iloc[-1])
    except Exception as exc:
        log.debug("stooq FX lookup failed: %s", exc)
    return None


def get_usdjpy(override: float | None = None, default: float = 150.0) -> tuple[float, str]:
    """Return ``(rate, source)``.

    An explicit ``override`` wins, which keeps runs reproducible and lets the
    screen work offline. Otherwise try Yahoo, then Stooq, then fall back to a
    fixed rate so a screen never dies on an FX lookup alone.
    """
    if override is not None:
        return float(override), "override"
    rate = from_yfinance()
    if rate:
        return rate, "yfinance"
    rate = from_stooq()
    if rate:
        return rate, "stooq"
    log.warning("could not fetch USD/JPY; falling back to %.2f", default)
    return float(default), "default"
