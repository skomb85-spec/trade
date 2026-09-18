"""Offline tests: synthetic frames, no network."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screener import fetch, metrics, screen, universe  # noqa: E402

USDJPY = 150.0


def make_frame(n: int = 60, close: float = 1000.0, day_range: float = 40.0) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(
        {
            "Open": [close] * n,
            "High": [close + day_range / 2] * n,
            "Low": [close - day_range / 2] * n,
            "Close": [close] * n,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


def test_avg_range_converts_to_jpy():
    row = metrics.compute("WIDE", make_frame(day_range=40.0), USDJPY)
    assert row["avg_range_usd"] == pytest.approx(40.0)
    assert row["avg_range_jpy"] == pytest.approx(6000.0)


def test_true_range_counts_overnight_gap():
    df = make_frame(n=3, close=100.0, day_range=2.0)
    # Gap the last bar up 10 dollars; its day range is still only 2.
    df.iloc[2, df.columns.get_loc("High")] = 111.0
    df.iloc[2, df.columns.get_loc("Low")] = 109.0
    df.iloc[2, df.columns.get_loc("Close")] = 110.0
    tr = metrics.true_range(df)
    assert tr.iloc[2] == pytest.approx(11.0)  # 111 - 100 prev close, not 2


def test_screen_separates_pass_from_fail():
    frames = {
        "WIDE": make_frame(day_range=40.0),   # 6,000 JPY
        "NARROW": make_frame(day_range=10.0),  # 1,500 JPY
    }
    table = screen.apply(metrics.build_table(frames, USDJPY), threshold_jpy=5000.0)
    by_ticker = table.set_index("ticker")
    assert bool(by_ticker.loc["WIDE", "passes"])
    assert not bool(by_ticker.loc["NARROW", "passes"])
    assert "below" in by_ticker.loc["NARROW", "reason"] or "<" in by_ticker.loc["NARROW", "reason"]


def test_failing_tickers_stay_in_the_table():
    frames = {"NARROW": make_frame(day_range=10.0)}
    table = screen.apply(metrics.build_table(frames, USDJPY), threshold_jpy=5000.0)
    assert len(table) == 1  # never silently dropped


def test_always_include_is_collected_despite_failing():
    frames = {"TSLA": make_frame(close=400.0, day_range=14.0)}  # 2,100 JPY
    table = screen.apply(
        metrics.build_table(frames, USDJPY), threshold_jpy=5000.0, always_include=["tsla"]
    )
    row = table.iloc[0]
    assert not bool(row["passes"])
    assert bool(row["collect"])


def test_min_bars_rejects_short_history():
    frames = {"SHORT": make_frame(n=5, day_range=40.0)}
    table = screen.apply(metrics.build_table(frames, USDJPY), threshold_jpy=5000.0, min_bars=30)
    assert not bool(table.iloc[0]["passes"])
    assert "bars" in table.iloc[0]["reason"]


def test_require_median_rejects_a_single_wide_day():
    df = make_frame(n=60, close=1000.0, day_range=4.0)
    df.iloc[0, df.columns.get_loc("High")] = 3000.0  # one enormous outlier
    table = screen.apply(
        metrics.build_table({"SPIKY": df}, USDJPY), threshold_jpy=5000.0, require_median=True
    )
    row = table.iloc[0]
    assert row["avg_range_jpy"] > 5000.0  # the mean clears the bar
    assert not bool(row["passes"])        # the median does not


def test_normalise_handles_tz_aware_index():
    df = make_frame(n=5)
    df.index = df.index.tz_localize("America/New_York")
    out = fetch._normalise(df)
    assert out is not None and out.index.tz is None


def test_normalise_rejects_a_frame_without_ohlcv():
    assert fetch._normalise(pd.DataFrame({"Foo": [1, 2]})) is None


def test_seed_universe_includes_tsla_and_has_no_duplicates():
    seed = universe.load_seed()
    assert "TSLA" in seed
    assert len(seed) == len(set(seed))


def test_cli_default_threshold_is_2000_jpy():
    from screener.cli import build_parser

    assert build_parser().parse_args([]).threshold_jpy == pytest.approx(2000.0)


def test_tsla_sized_range_clears_the_2000_jpy_default():
    # ~400 USD share, ~14 USD daily range -> ~2,100 JPY: over the new bar,
    # well under the old 5,000 one.
    frames = {"TSLA": make_frame(close=400.0, day_range=14.0)}
    table = metrics.build_table(frames, USDJPY)
    assert bool(screen.apply(table, threshold_jpy=2000.0).iloc[0]["passes"])
    assert not bool(screen.apply(table, threshold_jpy=5000.0).iloc[0]["passes"])
