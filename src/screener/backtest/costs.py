"""Trading frictions.

Most US brokers charge no equity commission now, so the cost that decides
whether an intraday strategy survives is slippage: the spread you cross
plus the price moving while the order fills. A backtest that fills at the
printed price will show an edge that does not exist, and the edge it
invents is largest exactly where trade counts are highest.

The defaults here are deliberately not generous.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    # One-way slippage in basis points of price. 5 bps on a liquid large cap
    # is about a cent on a 20 USD share -- roughly half a typical spread.
    slippage_bps: float = 5.0
    commission_per_share: float = 0.0
    commission_min: float = 0.0
    # Alpaca and most retail brokers charge no per-trade commission on
    # equities; a per-share model is here for brokers that do.

    def fill_price(self, price: float, side: str) -> float:
        """Price actually paid or received, after crossing the spread."""
        adj = price * self.slippage_bps / 10_000
        return price + adj if side == "buy" else price - adj

    def commission(self, shares: float) -> float:
        if self.commission_per_share <= 0:
            return 0.0
        return max(shares * self.commission_per_share, self.commission_min)

    def round_trip_cost(self, price: float, shares: float) -> float:
        """Total friction in USD for entering and exiting one position."""
        slip = price * self.slippage_bps / 10_000 * shares * 2
        return slip + self.commission(shares) * 2
