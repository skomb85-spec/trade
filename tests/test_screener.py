"""Offline tests: synthetic frames, no network."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screener import fetch, metrics, screen, universe  # noqa: E402

USDJPY = 150.0
TARGET = 5000.0


def make_frame(n: int = 60, close: float = 1000.0, day_range: float = 40.0,
               volume: int = 1_000_000) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(
        {
            "Open": [close] * n,
            "High": [close + day_range / 2] * n,
            "Low": [close - day_range / 2] * n,
            "Close": [close] * n,
            "Volume": [volume] * n,
        },
        index=idx,
    )


def screened(frames: dict[str, pd.DataFrame], **kwargs) -> pd.DataFrame:
    opts = {"target_jpy": TARGET, "capital_usd": 4000.0, "usdjpy": USDJPY,
            "min_dollar_volume": 0.0}
    opts.update(kwargs)
    return screen.apply(metrics.build_table(frames, USDJPY), **opts)


# --- metrics ---------------------------------------------------------------

def test_avg_range_converts_to_jpy():
    row = metrics.compute("WIDE", make_frame(day_range=40.0), USDJPY)
    assert row["avg_range_usd"] == pytest.approx(40.0)
    assert row["avg_range_jpy"] == pytest.approx(6000.0)


def test_avg_range_pct_is_range_over_price():
    row = metrics.compute("V", make_frame(close=400.0, day_range=14.0), USDJPY)
    assert row["avg_range_pct"] == pytest.approx(3.5, abs=0.01)


def test_true_range_counts_overnight_gap():
    df = make_frame(n=3, close=100.0, day_range=2.0)
    # Gap the last bar up 10 dollars; its day range is still only 2.
    df.iloc[2, df.columns.get_loc("High")] = 111.0
    df.iloc[2, df.columns.get_loc("Low")] = 109.0
    df.iloc[2, df.columns.get_loc("Close")] = 110.0
    assert metrics.true_range(df).iloc[2] == pytest.approx(11.0)  # vs 100 prev close, not 2


# --- the capital arithmetic ------------------------------------------------

def test_required_range_pct():
    # $33.33/day out of $4,000 at 30% capture needs a 2.78% daily range.
    assert screen.required_range_pct(TARGET, 4000.0, 0.30, USDJPY) == pytest.approx(2.78, abs=0.01)
    # Five times the capital needs a fifth of the volatility.
    assert screen.required_range_pct(TARGET, 20000.0, 0.30, USDJPY) == pytest.approx(0.556, abs=0.01)


def test_volatility_beats_share_price_for_a_small_account():
    """A $25 share moving 7% beats a $5,200 share moving 1.8% on $4,000."""
    table = screened({
        "CHEAP_VOLATILE": make_frame(close=25.0, day_range=1.75),
        "PRICEY_CALM": make_frame(close=5200.0, day_range=93.6, volume=50_000),
    }).set_index("ticker")
    assert bool(table.loc["CHEAP_VOLATILE", "passes"])
    assert not bool(table.loc["PRICEY_CALM", "passes"])
    # ...even though the expensive one has a 53x larger range in dollars.
    assert table.loc["PRICEY_CALM", "avg_range_usd"] > table.loc["CHEAP_VOLATILE", "avg_range_usd"]


def test_share_costing_more_than_the_account_is_rejected():
    row = screened({"NVR": make_frame(close=7500.0, day_range=172.0)}).iloc[0]
    assert not bool(row["passes"])
    assert row["shares_affordable"] == 0
    assert "one share costs" in row["reason"]


def test_fractional_shares_let_a_small_account_hold_an_expensive_stock():
    frames = {"NVR": make_frame(close=7500.0, day_range=172.0)}
    row = screened(frames, fractional_shares=True).iloc[0]
    assert row["shares_affordable"] == pytest.approx(4000 / 7500, abs=1e-4)
    # 0.533 shares x $172 x 30% x 150 = ~4,128 JPY: closer, but still short.
    assert row["expected_daily_jpy"] == pytest.approx(4128, abs=50)
    assert not bool(row["passes"])


def test_more_capital_passes_what_a_small_account_missed():
    frames = {"META": make_frame(close=700.0, day_range=15.4)}
    assert not bool(screened(frames, capital_usd=4000.0).iloc[0]["passes"])
    assert bool(screened(frames, capital_usd=20000.0).iloc[0]["passes"])


def test_required_capital_is_reported_for_a_miss():
    row = screened({"META": make_frame(close=700.0, day_range=15.4)}).iloc[0]
    # $33.33 / ($15.4 x 0.3) = 7.2 shares x $700 = ~$5,051.
    assert row["required_capital_usd"] == pytest.approx(5051, abs=20)


def test_lower_capture_rate_makes_the_screen_stricter():
    frames = {"T": make_frame(close=400.0, day_range=14.0)}
    assert bool(screened(frames, capture_rate=0.30).iloc[0]["passes"])
    assert not bool(screened(frames, capture_rate=0.10).iloc[0]["passes"])


# --- filters ---------------------------------------------------------------

def test_position_too_large_versus_turnover_is_rejected():
    # 20,000 USD of daily turnover cannot absorb a 4,000 USD position.
    row = screened({"THIN": make_frame(close=25.0, day_range=1.75, volume=800)}).iloc[0]
    assert not bool(row["passes"])
    assert "turnover" in row["reason"]


def test_min_dollar_volume_rejects_an_illiquid_name():
    frames = {"THIN": make_frame(close=25.0, day_range=1.75, volume=1_000)}
    row = screened(frames, min_dollar_volume=20_000_000.0,
                   max_position_pct_of_volume=0.0).iloc[0]
    assert not bool(row["passes"])
    assert "dollar volume" in row["reason"]


def test_min_bars_rejects_short_history():
    row = screened({"SHORT": make_frame(n=5, close=25.0, day_range=1.75)}).iloc[0]
    assert not bool(row["passes"])
    assert "bars" in row["reason"]


def test_failing_tickers_stay_in_the_table():
    table = screened({"NVR": make_frame(close=7500.0, day_range=172.0)})
    assert len(table) == 1  # never silently dropped


def test_always_include_is_collected_despite_failing():
    row = screened({"TSLA": make_frame(close=400.0, day_range=4.0)},
                   always_include=["tsla"]).iloc[0]
    assert not bool(row["passes"])
    assert bool(row["collect"])


# --- fetch and universe ----------------------------------------------------

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


# --- cli defaults ----------------------------------------------------------

def test_cli_defaults_match_the_account_being_screened():
    from screener.cli import build_parser

    args = build_parser().parse_args([])
    assert args.target_jpy == pytest.approx(5000.0)
    assert args.capital_usd == pytest.approx(4000.0)
    assert args.capture_rate == pytest.approx(0.30)


# --- alpaca minute bars (offline) ------------------------------------------

def test_alpaca_requires_credentials(monkeypatch):
    from screener import alpaca

    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(alpaca.MissingCredentials):
        alpaca._client()


def test_alpaca_skips_years_already_on_disk(tmp_path, monkeypatch):
    from screener import alpaca

    (tmp_path / "TSLA").mkdir()
    (tmp_path / "TSLA" / "TSLA-2024.parquet").write_bytes(b"x")

    def explode(*a, **k):  # a cached year must not hit the network
        raise AssertionError("fetch_minute_bars should not be called")

    monkeypatch.setattr(alpaca, "fetch_minute_bars", explode)
    written = alpaca.download(
        ["TSLA"], tmp_path, start=dt.date(2024, 1, 1), end=dt.date(2024, 12, 31)
    )
    assert written["TSLA"] == [tmp_path / "TSLA" / "TSLA-2024.parquet"]


def test_alpaca_start_is_clamped_to_2016(tmp_path, monkeypatch):
    from screener import alpaca

    seen: list[dt.date] = []

    def record(symbols, start, end, minutes=1):
        seen.append(start)
        return {}

    monkeypatch.setattr(alpaca, "fetch_minute_bars", record)
    alpaca.download(["X"], tmp_path, start=dt.date(2010, 1, 1), end=dt.date(2016, 6, 1))
    assert seen and min(seen) >= alpaca.EARLIEST


def test_minutes_cli_accepts_either_source_but_not_neither():
    from screener.minutes import build_parser

    assert build_parser().parse_args(["--tickers", "TSLA,MSTR"]).tickers == "TSLA,MSTR"
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
