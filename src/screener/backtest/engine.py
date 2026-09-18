"""Bar-by-bar simulation of one strategy over one symbol's sessions.

Three rules keep the result honest:

* a signal on bar *i* fills at the open of bar *i+1*, never at bar *i*;
* when a bar's range covers both the stop and the target, the stop is
  assumed to have come first -- minute bars do not record the order, and
  guessing in your own favour is how a losing strategy backtests green;
* every position is flat by the close. Nothing is carried overnight, so
  no result here depends on a gap the strategy never had to sit through.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import asdict, dataclass, field

import pandas as pd

from .costs import CostModel
from .strategies import DayContext, Strategy

log = logging.getLogger(__name__)


@dataclass
class Sizer:
    capital_usd: float = 4000.0
    # Fraction of the account put at risk per trade, i.e. what is lost if
    # the stop fills. 1% is a common ceiling; it is the knob that decides
    # whether a losing streak is survivable.
    risk_pct: float = 1.0
    fractional: bool = False
    max_leverage: float = 1.0

    def shares(self, entry: float, stop: float) -> float:
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0 or entry <= 0:
            return 0.0
        by_risk = self.capital_usd * self.risk_pct / 100 / risk_per_share
        by_capital = self.capital_usd * self.max_leverage / entry
        n = min(by_risk, by_capital)
        return n if self.fractional else float(math.floor(n))


@dataclass
class Trade:
    symbol: str
    date: dt.date
    direction: int
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    shares: float
    pnl_usd: float
    pnl_jpy: float
    exit_reason: str
    note: str = ""
    params: dict = field(default_factory=dict)

    def as_row(self) -> dict:
        row = asdict(self)
        row["params"] = repr(self.params)
        return row


def run_session(
    symbol: str,
    day: dt.date,
    bars: pd.DataFrame,
    strategy: Strategy,
    ctx: DayContext,
    sizer: Sizer,
    costs: CostModel,
    usdjpy: float,
) -> Trade | None:
    signals = strategy.signals(bars, ctx)
    if not signals:
        return None
    sig = signals[0]

    entry_bar = sig.bar + 1
    if entry_bar >= len(bars):
        return None

    side = "buy" if sig.direction > 0 else "sell"
    entry = costs.fill_price(float(bars["open"].iloc[entry_bar]), side)
    shares = sizer.shares(entry, sig.stop)
    if shares <= 0:
        return None

    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    exit_idx = len(bars) - 1
    raw_exit = float(bars["close"].iloc[-1])
    reason = "close"

    for j in range(entry_bar, len(bars)):
        if sig.direction > 0:
            # Stop first: a bar that spans both is resolved against us.
            if lows[j] <= sig.stop:
                exit_idx, raw_exit, reason = j, sig.stop, "stop"
                break
            if highs[j] >= sig.target:
                exit_idx, raw_exit, reason = j, sig.target, "target"
                break
        else:
            if highs[j] >= sig.stop:
                exit_idx, raw_exit, reason = j, sig.stop, "stop"
                break
            if lows[j] <= sig.target:
                exit_idx, raw_exit, reason = j, sig.target, "target"
                break

    exit_price = costs.fill_price(raw_exit, "sell" if sig.direction > 0 else "buy")
    gross = (exit_price - entry) * sig.direction * shares
    pnl_usd = gross - costs.commission(shares) * 2

    return Trade(
        symbol=symbol,
        date=day,
        direction=sig.direction,
        entry_time=bars.index[entry_bar],
        entry_price=round(entry, 4),
        exit_time=bars.index[exit_idx],
        exit_price=round(exit_price, 4),
        shares=shares,
        pnl_usd=round(pnl_usd, 4),
        pnl_jpy=round(pnl_usd * usdjpy, 1),
        exit_reason=reason,
        note=sig.note,
        params=strategy.params(),
    )


def run(
    symbol: str,
    sessions: list[tuple[dt.date, pd.DataFrame]],
    strategy: Strategy,
    sizer: Sizer,
    costs: CostModel,
    usdjpy: float,
) -> list[Trade]:
    """Simulate every session in order. ``sessions`` must be chronological."""
    trades: list[Trade] = []
    prev_close: float | None = None
    for day, bars in sessions:
        ctx = DayContext(prev_close=prev_close)
        trade = run_session(symbol, day, bars, strategy, ctx, sizer, costs, usdjpy)
        if trade is not None:
            trades.append(trade)
        prev_close = float(bars["close"].iloc[-1])
    return trades


def trades_to_frame(trades: list[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame([t.as_row() for t in trades])
