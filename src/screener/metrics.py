"""Per-ticker daily-movement statistics.

The headline number is ``avg_range_usd``: the mean of ``High - Low`` over the
lookback window, i.e. how many dollars a share travelled on an average day.
Multiplied by USD/JPY it gives the yen figure the screen filters on.

``median_range_usd`` sits alongside it because a mean is easily dragged up by
one earnings gap; a ticker whose mean clears the bar but whose median does
not was wide on a handful of days, not routinely.
"""

from __future__ import annotations

import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder's true range, which counts overnight gaps the day range misses."""
    prev_close = df["Close"].shift(1)
    return pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def compute(ticker: str, df: pd.DataFrame, usdjpy: float, atr_window: int = 14) -> dict | None:
    """Summarise one ticker's frame. ``None`` if there are too few bars."""
    if df is None or len(df) < 2:
        return None

    day_range = (df["High"] - df["Low"]).dropna()
    close_change = df["Close"].diff().abs().dropna()
    tr = true_range(df).dropna()
    if day_range.empty:
        return None

    avg_range_usd = float(day_range.mean())
    last_close = float(df["Close"].iloc[-1])
    atr = float(tr.rolling(atr_window).mean().iloc[-1]) if len(tr) >= atr_window else float(tr.mean())
    dollar_volume = (df["Close"] * df["Volume"]).dropna()

    return {
        "ticker": ticker,
        "bars": int(len(df)),
        "first_date": df.index[0].date().isoformat(),
        "last_date": df.index[-1].date().isoformat(),
        "last_close_usd": round(last_close, 4),
        "avg_range_usd": round(avg_range_usd, 4),
        "median_range_usd": round(float(day_range.median()), 4),
        "avg_range_jpy": round(avg_range_usd * usdjpy, 1),
        "median_range_jpy": round(float(day_range.median()) * usdjpy, 1),
        "avg_range_pct": round(avg_range_usd / last_close * 100, 3) if last_close else None,
        "atr_usd": round(atr, 4),
        "atr_jpy": round(atr * usdjpy, 1),
        "avg_abs_close_change_usd": round(float(close_change.mean()), 4) if not close_change.empty else None,
        "avg_abs_close_change_jpy": round(float(close_change.mean()) * usdjpy, 1) if not close_change.empty else None,
        "avg_dollar_volume_usd": round(float(dollar_volume.mean()), 0) if not dollar_volume.empty else None,
    }


def build_table(
    frames: dict[str, pd.DataFrame],
    usdjpy: float,
    sources: dict[str, str] | None = None,
    atr_window: int = 14,
) -> pd.DataFrame:
    rows = []
    for ticker, df in frames.items():
        row = compute(ticker, df, usdjpy, atr_window=atr_window)
        if row is None:
            continue
        row["source"] = (sources or {}).get(ticker, "")
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("avg_range_jpy", ascending=False).reset_index(drop=True)
