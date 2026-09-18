"""Apply the yen threshold and liquidity filters to a metrics table."""

from __future__ import annotations

import pandas as pd


def apply(
    table: pd.DataFrame,
    threshold_jpy: float,
    min_bars: int = 30,
    min_dollar_volume: float = 0.0,
    require_median: bool = False,
    always_include: list[str] | None = None,
) -> pd.DataFrame:
    """Add ``passes`` and ``reason`` columns; never drop rows.

    Keeping every row means a ticker you care about stays visible with its
    measured numbers even when it misses the bar, so the threshold can be
    retuned without re-fetching. ``always_include`` marks tickers to keep
    downstream (chart download) regardless of the screen.
    """
    if table.empty:
        return table

    out = table.copy()
    keep = {t.strip().upper() for t in (always_include or []) if t.strip()}

    reasons: list[str] = []
    passes: list[bool] = []
    for row in out.itertuples(index=False):
        why: list[str] = []
        if row.bars < min_bars:
            why.append(f"only {row.bars} bars (< {min_bars})")
        if row.avg_range_jpy < threshold_jpy:
            why.append(f"avg range {row.avg_range_jpy:,.0f} JPY < {threshold_jpy:,.0f}")
        if require_median and row.median_range_jpy < threshold_jpy:
            why.append(f"median range {row.median_range_jpy:,.0f} JPY < {threshold_jpy:,.0f}")
        adv = getattr(row, "avg_dollar_volume_usd", None)
        if min_dollar_volume > 0 and (adv is None or pd.isna(adv) or adv < min_dollar_volume):
            why.append(f"avg dollar volume below {min_dollar_volume:,.0f} USD")
        passes.append(not why)
        reasons.append("; ".join(why))

    out["passes"] = passes
    out["reason"] = reasons
    out["always_include"] = out["ticker"].str.upper().isin(keep)
    out["collect"] = out["passes"] | out["always_include"]
    return out.sort_values(["passes", "avg_range_jpy"], ascending=[False, False]).reset_index(drop=True)
