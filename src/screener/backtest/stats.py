"""Performance summary for a set of trades.

The headline is ``pct_days_at_target``: the share of trading days that
actually cleared the daily goal. A strategy can show a healthy total
profit and still miss the goal almost every day, because one enormous
winner carried the whole sample -- which is a different business from
earning the target daily, and not the one being asked for here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import pandas as pd

from .engine import Trade


@dataclass
class Summary:
    n_trades: int = 0
    n_days: int = 0
    win_rate: float = 0.0
    total_pnl_jpy: float = 0.0
    expectancy_jpy: float = 0.0
    avg_win_jpy: float = 0.0
    avg_loss_jpy: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_jpy: float = 0.0
    median_daily_pnl_jpy: float = 0.0
    pct_days_at_target: float = 0.0
    sharpe: float = 0.0
    stopped_out_pct: float = 0.0
    hit_target_pct: float = 0.0
    timed_out_pct: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def summarise(trades: list[Trade], target_jpy: float = 5000.0) -> Summary:
    if not trades:
        return Summary()

    pnl = pd.Series([t.pnl_jpy for t in trades], dtype=float)
    dates = pd.Series([t.date for t in trades])
    daily = pnl.groupby(dates).sum()

    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())

    equity = pnl.cumsum()
    drawdown = equity - equity.cummax()

    reasons = pd.Series([t.exit_reason for t in trades])
    n = len(trades)

    sharpe = 0.0
    if len(daily) > 1 and daily.std(ddof=1) > 0:
        # Daily Sharpe annualised over 252 sessions, risk-free taken as 0.
        sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252))

    return Summary(
        n_trades=n,
        n_days=int(daily.size),
        win_rate=round(float((pnl > 0).mean()) * 100, 2),
        total_pnl_jpy=round(float(pnl.sum()), 0),
        expectancy_jpy=round(float(pnl.mean()), 1),
        avg_win_jpy=round(float(wins.mean()), 1) if not wins.empty else 0.0,
        avg_loss_jpy=round(float(losses.mean()), 1) if not losses.empty else 0.0,
        profit_factor=round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf"),
        max_drawdown_jpy=round(float(drawdown.min()), 0),
        median_daily_pnl_jpy=round(float(daily.median()), 1),
        pct_days_at_target=round(float((daily >= target_jpy).mean()) * 100, 2),
        sharpe=round(sharpe, 3),
        stopped_out_pct=round(float((reasons == "stop").mean()) * 100, 1),
        hit_target_pct=round(float((reasons == "target").mean()) * 100, 1),
        timed_out_pct=round(float((reasons == "close").mean()) * 100, 1),
    )


def format_summary(label: str, s: Summary) -> str:
    if s.n_trades == 0:
        return f"{label}: no trades"
    pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
    return (
        f"{label}\n"
        f"  trades {s.n_trades:>5}   days {s.n_days:>5}   win rate {s.win_rate:>5.1f}%\n"
        f"  total {s.total_pnl_jpy:>12,.0f} JPY   expectancy {s.expectancy_jpy:>9,.0f} JPY/trade\n"
        f"  avg win {s.avg_win_jpy:>9,.0f}   avg loss {s.avg_loss_jpy:>9,.0f}   profit factor {pf}\n"
        f"  median day {s.median_daily_pnl_jpy:>9,.0f} JPY   max drawdown {s.max_drawdown_jpy:>10,.0f} JPY\n"
        f"  days at target {s.pct_days_at_target:>5.1f}%   annualised Sharpe {s.sharpe:.2f}\n"
        f"  exits: stop {s.stopped_out_pct:.0f}% / target {s.hit_target_pct:.0f}% / close {s.timed_out_pct:.0f}%"
    )
