"""Intraday strategies, each reduced to at most one trade per day.

One trade per session matches the stated goal -- a daily profit target,
not a high-frequency book -- and it keeps the number of free parameters
small, which matters more than it looks: every extra parameter widens the
search and makes an accidental in-sample winner likelier.

No strategy may look at a bar it has not lived through. A signal detected
on bar *i* is entered at the open of bar *i+1*, never at bar *i*'s close or
at the exact trigger price, both of which quietly assume a fill nobody
would have got.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class Signal:
    bar: int          # index into the session; entry fills at bar + 1
    direction: int    # +1 long, -1 short
    stop: float
    target: float
    note: str = ""


@dataclass(frozen=True)
class DayContext:
    prev_close: float | None


class Strategy:
    name = "base"

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        raise NotImplementedError

    def signals(self, bars: pd.DataFrame, ctx: DayContext) -> list[Signal]:
        raise NotImplementedError

    def params(self) -> dict:
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v}" for k, v in sorted(self.params().items()))
        return f"{self.name}({inner})"


class OpeningRangeBreakout(Strategy):
    """Fade nothing: take the first break of the first N minutes' range.

    The oldest intraday pattern there is, which is exactly why it is worth
    testing honestly -- if it still worked plainly it would not still be
    the first thing everyone tries.
    """

    name = "orb"

    def __init__(self, or_minutes: int = 15, target_r: float = 2.0,
                 direction: str = "both"):
        self.or_minutes = or_minutes
        self.target_r = target_r
        self.direction = direction

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {
            "or_minutes": [5, 15, 30, 60],
            "target_r": [1.0, 1.5, 2.0, 3.0],
            "direction": ["both", "long", "short"],
        }

    def signals(self, bars: pd.DataFrame, ctx: DayContext) -> list[Signal]:
        n = self.or_minutes
        if len(bars) <= n + 2:
            return []
        opening = bars.iloc[:n]
        hi, lo = float(opening["high"].max()), float(opening["low"].min())
        height = hi - lo
        if height <= 0:
            return []

        highs = bars["high"].to_numpy()
        lows = bars["low"].to_numpy()
        for i in range(n, len(bars) - 1):
            if self.direction in ("both", "long") and highs[i] > hi:
                return [Signal(i, +1, stop=lo, target=hi + height * self.target_r,
                               note=f"break above {hi:.2f}")]
            if self.direction in ("both", "short") and lows[i] < lo:
                return [Signal(i, -1, stop=hi, target=lo - height * self.target_r,
                               note=f"break below {lo:.2f}")]
        return []


class VWAPReversion(Strategy):
    """Fade a stretch away from the session VWAP, targeting a return to it."""

    name = "vwap"

    def __init__(self, threshold_pct: float = 1.0, stop_pct: float = 0.75,
                 start_minute: int = 30):
        self.threshold_pct = threshold_pct
        self.stop_pct = stop_pct
        self.start_minute = start_minute

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {
            "threshold_pct": [0.5, 0.75, 1.0, 1.5, 2.0],
            "stop_pct": [0.5, 0.75, 1.0, 1.5],
            "start_minute": [15, 30, 60],
        }

    def signals(self, bars: pd.DataFrame, ctx: DayContext) -> list[Signal]:
        if len(bars) <= self.start_minute + 2:
            return []
        typical = (bars["high"] + bars["low"] + bars["close"]) / 3
        volume = bars["volume"].replace(0, pd.NA).ffill().fillna(1)
        vwap = (typical * volume).cumsum() / volume.cumsum()

        closes = bars["close"].to_numpy()
        vw = vwap.to_numpy()
        for i in range(self.start_minute, len(bars) - 1):
            if vw[i] <= 0:
                continue
            dev = (closes[i] - vw[i]) / vw[i] * 100
            if dev >= self.threshold_pct:      # stretched above VWAP -> short it
                px = closes[i]
                return [Signal(i, -1, stop=px * (1 + self.stop_pct / 100),
                               target=vw[i], note=f"+{dev:.2f}% over VWAP")]
            if dev <= -self.threshold_pct:     # stretched below VWAP -> buy it
                px = closes[i]
                return [Signal(i, +1, stop=px * (1 - self.stop_pct / 100),
                               target=vw[i], note=f"{dev:.2f}% under VWAP")]
        return []


class GapFill(Strategy):
    """Trade an opening gap back towards the previous close."""

    name = "gap"

    def __init__(self, min_gap_pct: float = 1.0, max_gap_pct: float = 5.0,
                 stop_pct: float = 1.0, entry_minute: int = 5):
        self.min_gap_pct = min_gap_pct
        self.max_gap_pct = max_gap_pct
        self.stop_pct = stop_pct
        self.entry_minute = entry_minute

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {
            "min_gap_pct": [0.5, 1.0, 2.0],
            "max_gap_pct": [3.0, 5.0, 10.0],
            "stop_pct": [0.5, 1.0, 1.5],
            "entry_minute": [1, 5, 15],
        }

    def signals(self, bars: pd.DataFrame, ctx: DayContext) -> list[Signal]:
        if ctx.prev_close is None or ctx.prev_close <= 0:
            return []
        i = self.entry_minute
        if len(bars) <= i + 2:
            return []
        gap_pct = (float(bars["open"].iloc[0]) - ctx.prev_close) / ctx.prev_close * 100
        if not self.min_gap_pct <= abs(gap_pct) <= self.max_gap_pct:
            return []

        px = float(bars["close"].iloc[i])
        direction = -1 if gap_pct > 0 else +1   # trade back towards prev close
        stop = px * (1 - self.stop_pct / 100) if direction > 0 else px * (1 + self.stop_pct / 100)
        return [Signal(i, direction, stop=stop, target=ctx.prev_close,
                       note=f"gap {gap_pct:+.2f}%")]


REGISTRY: dict[str, type[Strategy]] = {
    OpeningRangeBreakout.name: OpeningRangeBreakout,
    VWAPReversion.name: VWAPReversion,
    GapFill.name: GapFill,
}


def grid_size(cls: type[Strategy]) -> int:
    total = 1
    for values in cls.param_grid().values():
        total *= len(values)
    return total


def iter_params(cls: type[Strategy]) -> Iterable[dict]:
    import itertools

    grid = cls.param_grid()
    keys = list(grid)
    for combo in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, combo))
