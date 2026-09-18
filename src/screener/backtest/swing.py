"""Multi-day simulation for the swing strategies.

Same three honesty rules as the intraday engine -- enter at the *next*
bar's open, resolve a bar spanning both stop and target against the
position, and never overlap two positions -- with the flat-at-close rule
replaced by a hard ``max_hold`` in sessions. Overnight gaps are therefore
real here: a gap straight through the stop exits at the stop price, which
is optimistic, and the note below says so rather than hiding it.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from .costs import CostModel
from .engine import Sizer, Trade
from .swing_strategies import SwingStrategy

log = logging.getLogger(__name__)

Units = list[tuple[dt.date, pd.DataFrame]]


def load_daily(data_dir: Path, symbol: str) -> pd.DataFrame:
    """Read a daily OHLCV CSV written by the screener's cache or chart output."""
    for name in (f"{symbol}.csv", f"{symbol.replace('/', '_')}.csv"):
        path = Path(data_dir) / name
        if path.exists():
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            df.columns = [str(c).lower() for c in df.columns]
            missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
            if missing:
                raise ValueError(f"{symbol}: daily data is missing {missing}")
            index = pd.to_datetime(df.index)
            if getattr(index, "tz", None) is not None:
                index = index.tz_convert(None)
            df.index = index
            df.index.name = "date"
            return df.sort_index()
    raise FileNotFoundError(
        f"no daily CSV for {symbol} under {data_dir} -- run the screen first"
    )


def to_units(bars: pd.DataFrame) -> Units:
    """One unit per session, so walk_forward's split logic applies unchanged."""
    return [(ts.date(), bars.iloc[[i]]) for i, ts in enumerate(bars.index)]


# walk_forward hands the same window list to every parameter combination,
# so rebuilding the frame per combo is pure waste. Keyed by identity, with
# the list held so the id stays valid.
_FRAME_CACHE: dict[int, tuple[Units, pd.DataFrame]] = {}


def _frame(units: Units) -> pd.DataFrame:
    hit = _FRAME_CACHE.get(id(units))
    if hit is not None and hit[0] is units:
        return hit[1]
    frame = pd.concat([f for _, f in units])
    if len(_FRAME_CACHE) > 8:
        _FRAME_CACHE.clear()
    _FRAME_CACHE[id(units)] = (units, frame)
    return frame


def run_swing(
    symbol: str,
    units: Units,
    strategy: SwingStrategy,
    sizer: Sizer,
    costs: CostModel,
    usdjpy: float,
) -> list[Trade]:
    if len(units) < 3:
        return []
    bars = _frame(units)
    prepared = strategy.prepare(bars)

    opens = bars["open"].to_numpy()
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    closes = bars["close"].to_numpy()
    n = len(bars)

    trades: list[Trade] = []
    i = 0
    while i < n - 1:
        signal = strategy.entry(prepared.iloc[i])
        if signal is None:
            i += 1
            continue
        direction, stop, target = signal

        entry_bar = i + 1
        side = "buy" if direction > 0 else "sell"
        entry = costs.fill_price(float(opens[entry_bar]), side)
        shares = sizer.shares(entry, stop)
        if shares <= 0:
            i += 1
            continue

        last = min(entry_bar + strategy.max_hold - 1, n - 1)
        exit_idx, raw_exit, reason = last, float(closes[last]), "max_hold"
        for j in range(entry_bar, last + 1):
            if direction > 0:
                # Stop before target: a bar covering both goes against us.
                if lows[j] <= stop:
                    exit_idx, raw_exit, reason = j, stop, "stop"
                    break
                if highs[j] >= target:
                    exit_idx, raw_exit, reason = j, target, "target"
                    break
            else:
                if highs[j] >= stop:
                    exit_idx, raw_exit, reason = j, stop, "stop"
                    break
                if lows[j] <= target:
                    exit_idx, raw_exit, reason = j, target, "target"
                    break

        exit_price = costs.fill_price(raw_exit, "sell" if direction > 0 else "buy")
        pnl_usd = (exit_price - entry) * direction * shares - costs.commission(shares) * 2

        trades.append(Trade(
            symbol=symbol,
            date=bars.index[entry_bar].date(),
            direction=direction,
            entry_time=bars.index[entry_bar],
            entry_price=round(entry, 4),
            exit_time=bars.index[exit_idx],
            exit_price=round(exit_price, 4),
            shares=shares,
            pnl_usd=round(pnl_usd, 4),
            pnl_jpy=round(pnl_usd * usdjpy, 1),
            exit_reason=reason,
            note=f"hold {exit_idx - entry_bar + 1}d",
            params=strategy.params(),
        ))
        # One position at a time: resume after the exit, never overlapping.
        i = exit_idx + 1

    return trades
