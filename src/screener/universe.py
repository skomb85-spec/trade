"""Ticker universes to screen.

Three sources, in increasing order of coverage and network cost:

``seed``    a bundled, offline-safe list of US names that have historically
            shown a wide absolute daily range, plus the mega-caps and
            high-volatility names people usually want to see regardless.
``sp500``   the current S&P 500 constituents, scraped from Wikipedia.
``all``     every symbol on the Nasdaq Trader consolidated listing file
            (~11k symbols, including NYSE/AMEX). Slow but complete.

Absolute range in dollars -- not percent -- is what matters here, so the
seed list leans towards high-priced stocks. It will go stale as prices
move and companies split; use ``sp500`` or ``all`` for a live universe.
"""

from __future__ import annotations

import io

import pandas as pd
import requests

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Historically wide-range / high-priced US listings, plus the mega-caps and
# high-volatility names that are worth reporting on even when they fall
# short of the threshold.
SEED_TICKERS: tuple[str, ...] = (
    # Four-figure share prices
    "NVR", "BKNG", "SEB", "AZO", "TPL", "FICO", "MELI", "TDG", "MTD",
    "GWW", "NFLX", "BLK", "COST", "NOW", "EQIX", "ORLY", "URI", "ASML",
    # Several-hundred-dollar names with a wide daily range
    "LLY", "REGN", "GS", "UNH", "MCK", "COR", "ELV", "CI", "HUM", "ISRG",
    "SPGI", "MSCI", "ADBE", "INTU", "PH", "ROP", "TDY", "TMO", "WST",
    "IDXX", "VRTX", "KLAC", "SNPS", "CDNS", "MPWR", "LMT", "NOC", "GD",
    "HUBS", "TYL", "ANET", "PANW", "CRWD", "LRCX", "AMT", "SHW", "WSO",
    "BIIB", "ALGN", "DPZ", "CMG", "ULTA", "POOL", "WAT", "RCL", "MAR",
    # Mega-caps and high-volatility names
    "TSLA", "NVDA", "META", "MSFT", "AAPL", "AMZN", "GOOGL", "AVGO",
    "MSTR", "SMCI", "COIN", "PLTR", "AMD", "NFLX", "ARM", "DELL",
    # Index and leveraged ETFs, which often carry the widest dollar ranges
    "SPY", "QQQ", "DIA", "IWM", "TQQQ", "SOXL", "SPXL", "UPRO", "SOXX",
)


def _dedupe(tickers: list[str]) -> list[str]:
    """Upper-case, strip, and drop duplicates while keeping order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in tickers:
        t = str(raw).strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def load_seed() -> list[str]:
    return _dedupe(list(SEED_TICKERS))


def load_sp500(timeout: int = 30) -> list[str]:
    """Current S&P 500 constituents from Wikipedia."""
    resp = requests.get(
        SP500_WIKI_URL, timeout=timeout, headers={"User-Agent": "us-stock-high-range-screener"}
    )
    resp.raise_for_status()
    table = pd.read_html(io.StringIO(resp.text), match="Symbol")[0]
    # Wikipedia writes class shares as BRK.B; Yahoo wants BRK-B.
    symbols = table["Symbol"].astype(str).str.replace(".", "-", regex=False)
    return _dedupe(symbols.tolist())


def load_all_us(timeout: int = 60, include_etfs: bool = True) -> list[str]:
    """Every symbol on the Nasdaq Trader consolidated listing file."""
    resp = requests.get(
        NASDAQ_TRADED_URL, timeout=timeout, headers={"User-Agent": "us-stock-high-range-screener"}
    )
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), sep="|")
    # The final row is a "File Creation Time" footer, not a symbol.
    df = df[df["Symbol"].notna() & (df["Nasdaq Traded"] == "Y")]
    # Test issues are synthetic symbols used for exchange testing.
    df = df[df["Test Issue"] == "N"]
    if not include_etfs:
        df = df[df["ETF"] == "N"]
    symbols = df["Symbol"].astype(str).str.replace(".", "-", regex=False)
    # Skip warrants, units and rights, which have no meaningful chart history.
    symbols = symbols[~symbols.str.contains(r"[\$\^]", regex=True)]
    return _dedupe(symbols.tolist())


def load_file(path: str) -> list[str]:
    """One ticker per line, or a CSV with a ``symbol``/``ticker`` column."""
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
        for col in ("symbol", "ticker", "Symbol", "Ticker"):
            if col in df.columns:
                return _dedupe(df[col].astype(str).tolist())
        return _dedupe(df.iloc[:, 0].astype(str).tolist())
    with open(path, encoding="utf-8") as fh:
        return _dedupe([line for line in fh.read().splitlines() if not line.startswith("#")])


def load_universe(name: str, path: str | None = None) -> list[str]:
    if path:
        return load_file(path)
    if name == "seed":
        return load_seed()
    if name == "sp500":
        return load_sp500()
    if name == "all":
        return load_all_us()
    raise ValueError(f"unknown universe: {name!r} (expected seed, sp500 or all)")
