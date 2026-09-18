"""Decide which tickers can plausibly produce the daily profit target.

The original screen asked "does one share move 5,000 JPY a day?". That is
the wrong question for a profit target: ten shares moving 500 JPY each does
the same job. What actually gates the target is

    capital x range_pct x capture_rate >= target

so the binding property is the daily range *as a fraction of price*, not
its size in dollars. A 5,200 USD share moving 1.8% is a worse vehicle for a
small account than a 25 USD share moving 7%, despite a range ninety times
larger in dollars.

``capture_rate`` is the share of the day's high-low range a strategy
actually banks. Nobody catches the whole range; 0.3 is already an
optimistic standing assumption and is the knob to turn down first when the
output looks too good.
"""

from __future__ import annotations

import math

import pandas as pd


def required_range_pct(target_jpy: float, capital_usd: float, capture_rate: float,
                       usdjpy: float) -> float:
    """Daily range, as a percent of price, needed to hit the target."""
    target_usd = target_jpy / usdjpy
    return target_usd / (capital_usd * capture_rate) * 100


def apply(
    table: pd.DataFrame,
    target_jpy: float,
    capital_usd: float,
    usdjpy: float,
    capture_rate: float = 0.30,
    fractional_shares: bool = False,
    min_bars: int = 30,
    min_dollar_volume: float = 0.0,
    max_position_pct_of_volume: float = 1.0,
    min_range_jpy: float = 0.0,
    always_include: list[str] | None = None,
) -> pd.DataFrame:
    """Add sizing, expected-yield and pass/fail columns. Never drops a row.

    Keeping every row means a ticker stays visible with its measured numbers
    even when it misses, so the assumptions can be retuned without
    re-fetching. ``always_include`` marks tickers to collect regardless.
    """
    if table.empty:
        return table

    out = table.copy()
    keep = {t.strip().upper() for t in (always_include or []) if t.strip()}
    need_pct = required_range_pct(target_jpy, capital_usd, capture_rate, usdjpy)

    shares_col: list[float] = []
    expected_col: list[float] = []
    req_capital_col: list[float] = []
    passes: list[bool] = []
    reasons: list[str] = []

    for row in out.itertuples(index=False):
        why: list[str] = []
        price = float(row.last_close_usd or 0.0)
        range_usd = float(row.avg_range_usd or 0.0)

        # How many shares the account can hold, and what the day's range is
        # worth across that position once the capture rate is applied.
        if price <= 0:
            shares = 0.0
        elif fractional_shares:
            shares = capital_usd / price
        else:
            shares = float(math.floor(capital_usd / price))
        expected_jpy = shares * range_usd * capture_rate * usdjpy

        # Capital that WOULD be needed to reach the target at this range.
        if range_usd > 0 and capture_rate > 0:
            req_capital = (target_jpy / usdjpy) / (range_usd * capture_rate) * price
        else:
            req_capital = float("nan")

        if row.bars < min_bars:
            why.append(f"only {row.bars} bars (< {min_bars})")
        if shares < 1 and not fractional_shares:
            why.append(f"one share costs ${price:,.0f}, above the ${capital_usd:,.0f} account")
        elif expected_jpy < target_jpy:
            why.append(
                f"expected {expected_jpy:,.0f} JPY/day at {capture_rate:.0%} capture "
                f"< target {target_jpy:,.0f}"
            )
        if min_range_jpy > 0 and row.avg_range_jpy < min_range_jpy:
            why.append(f"per-share range {row.avg_range_jpy:,.0f} JPY < {min_range_jpy:,.0f}")

        adv = getattr(row, "avg_dollar_volume_usd", None)
        has_adv = adv is not None and not pd.isna(adv)
        if min_dollar_volume > 0 and (not has_adv or adv < min_dollar_volume):
            why.append(f"avg dollar volume below {min_dollar_volume:,.0f} USD")
        # A position that is a meaningful slice of the day's turnover cannot
        # be entered or exited at the prices a backtest assumes.
        if has_adv and adv > 0 and max_position_pct_of_volume > 0:
            position_usd = shares * price
            if position_usd / adv * 100 > max_position_pct_of_volume:
                why.append(
                    f"position is {position_usd / adv * 100:.2f}% of daily turnover "
                    f"(> {max_position_pct_of_volume}%), too illiquid to fill"
                )

        shares_col.append(round(shares, 4))
        expected_col.append(round(expected_jpy, 0))
        req_capital_col.append(round(req_capital, 0) if req_capital == req_capital else None)
        passes.append(not why)
        reasons.append("; ".join(why))

    out["shares_affordable"] = shares_col
    out["position_usd"] = [round(s * p, 2) for s, p in zip(shares_col, out["last_close_usd"])]
    out["expected_daily_jpy"] = expected_col
    out["required_capital_usd"] = req_capital_col
    out["required_range_pct"] = round(need_pct, 3)
    out["passes"] = passes
    out["reason"] = reasons
    out["always_include"] = out["ticker"].str.upper().isin(keep)
    out["collect"] = out["passes"] | out["always_include"]
    return out.sort_values(
        ["passes", "expected_daily_jpy"], ascending=[False, False]
    ).reset_index(drop=True)
