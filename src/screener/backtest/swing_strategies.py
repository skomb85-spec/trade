"""Swing strategies on daily bars, held two to seven sessions.

Daily bars remove the constraint that shaped the intraday work: history
runs to decades rather than the 60 days Yahoo serves for 5-minute bars, so
a parameter search has thousands of observations to answer to instead of a
few hundred. Frictions also stop dominating -- one round trip per five
days against a target of some 1.7% leaves slippage a rounding error, where
the same cost against a 0.3% intraday move eats a third of it.

All three are long only. Shorting a small cash account brings borrow
availability and buy-in risk that none of this models, and pretending
otherwise would flatter every result here.

Indicators are causal: each is computed from bars at or before the one
being decided on, and an entry still fills at the *next* bar's open.
"""

from __future__ import annotations

import itertools
from typing import Iterable

import pandas as pd


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    # An unbroken run of up days leaves loss at zero, which is exactly the
    # case a short RSI period hits often. Divide only where loss is real,
    # then pin those bars at 100; a flat series stays undefined.
    rs = gain / loss.where(loss > 0)
    out = 100 - 100 / (1 + rs)
    return out.mask((loss <= 0) & (gain > 0), 100.0).astype(float)


class SwingStrategy:
    name = "base"
    max_hold = 5

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        raise NotImplementedError

    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def entry(self, row) -> tuple[int, float, float] | None:
        """``(direction, stop, target)`` or ``None``, from this bar alone."""
        raise NotImplementedError

    def params(self) -> dict:
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v}" for k, v in sorted(self.params().items()))
        return f"{self.name}({inner})"


class DonchianBreakout(SwingStrategy):
    """Buy a close above the highest high of the last N sessions."""

    name = "donchian"

    def __init__(self, lookback: int = 20, atr_mult: float = 2.0,
                 target_r: float = 2.0, max_hold: int = 5):
        self.lookback = lookback
        self.atr_mult = atr_mult
        self.target_r = target_r
        self.max_hold = max_hold

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {"lookback": [10, 20, 40, 60], "atr_mult": [1.5, 2.0, 3.0],
                "target_r": [1.5, 2.0, 3.0], "max_hold": [3, 5, 7]}

    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        out = bars.copy()
        # shift(1): the breakout level must exclude today's own high.
        out["_hh"] = bars["high"].rolling(self.lookback).max().shift(1)
        out["_atr"] = atr(bars)
        return out

    def entry(self, row):
        hh, a, close = row["_hh"], row["_atr"], row["close"]
        if pd.isna(hh) or pd.isna(a) or a <= 0 or close <= hh:
            return None
        stop = close - self.atr_mult * a
        if stop >= close:
            return None
        return 1, stop, close + self.target_r * (close - stop)


class RSIPullback(SwingStrategy):
    """Buy a short-term washout inside an uptrend, a la Connors' RSI(2)."""

    name = "rsi"

    def __init__(self, rsi_period: int = 2, entry_level: float = 10.0,
                 trend_ma: int = 200, target_r: float = 2.0, max_hold: int = 5):
        self.rsi_period = rsi_period
        self.entry_level = entry_level
        self.trend_ma = trend_ma      # 0 disables the trend filter
        self.target_r = target_r
        self.max_hold = max_hold

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {"rsi_period": [2, 3], "entry_level": [5.0, 10.0, 15.0],
                "trend_ma": [0, 200], "target_r": [1.5, 2.0, 3.0],
                "max_hold": [3, 5, 7]}

    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        out = bars.copy()
        out["_rsi"] = rsi(bars["close"], self.rsi_period)
        out["_atr"] = atr(bars)
        out["_ma"] = (bars["close"].rolling(self.trend_ma).mean()
                      if self.trend_ma else 0.0)
        return out

    def entry(self, row):
        r, a, close = row["_rsi"], row["_atr"], row["close"]
        if pd.isna(r) or pd.isna(a) or a <= 0 or r >= self.entry_level:
            return None
        if self.trend_ma:
            ma = row["_ma"]
            if pd.isna(ma) or close <= ma:   # only buy dips in an uptrend
                return None
        stop = close - 1.5 * a
        if stop >= close:
            return None
        return 1, stop, close + self.target_r * (close - stop)


class MACross(SwingStrategy):
    """Buy the session a fast moving average crosses above a slow one."""

    name = "macross"

    def __init__(self, fast: int = 10, slow: int = 50, atr_mult: float = 2.0,
                 target_r: float = 2.0, max_hold: int = 5):
        self.fast = fast
        self.slow = slow
        self.atr_mult = atr_mult
        self.target_r = target_r
        self.max_hold = max_hold

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {"fast": [5, 10], "slow": [20, 50, 100], "atr_mult": [2.0, 3.0],
                "target_r": [1.5, 2.0, 3.0], "max_hold": [3, 5, 7]}

    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        out = bars.copy()
        f = bars["close"].rolling(self.fast).mean()
        s = bars["close"].rolling(self.slow).mean()
        out["_cross"] = (f > s) & (f.shift(1) <= s.shift(1))
        out["_atr"] = atr(bars)
        return out

    def entry(self, row):
        if not bool(row["_cross"]):
            return None
        a, close = row["_atr"], row["close"]
        if pd.isna(a) or a <= 0:
            return None
        stop = close - self.atr_mult * a
        if stop >= close:
            return None
        return 1, stop, close + self.target_r * (close - stop)


REGISTRY: dict[str, type[SwingStrategy]] = {
    DonchianBreakout.name: DonchianBreakout,
    RSIPullback.name: RSIPullback,
    MACross.name: MACross,
}


def grid_size(cls: type[SwingStrategy]) -> int:
    total = 1
    for values in cls.param_grid().values():
        total *= len(values)
    return total


def iter_params(cls: type[SwingStrategy]) -> Iterable[dict]:
    grid = cls.param_grid()
    keys = list(grid)
    for combo in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, combo))
