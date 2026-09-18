"""Offline tests for the daily-bar swing engine and its strategies."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screener.backtest import stats, swing, swing_strategies as sw  # noqa: E402
from screener.backtest.costs import CostModel  # noqa: E402
from screener.backtest.engine import Sizer, Trade  # noqa: E402

FREE = CostModel(slippage_bps=0.0)
USDJPY = 150.0


def daily(closes: list[float], spread: float = 1.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame(
        {"open": closes, "high": [c + spread for c in closes],
         "low": [c - spread for c in closes], "close": closes,
         "volume": [1_000_000] * len(closes)},
        index=idx,
    )


class AlwaysEnter(sw.SwingStrategy):
    """Stub that fires on the first bar only, for engine-level tests."""

    name = "stub"

    def __init__(self, bar=0, stop=95.0, target=110.0, max_hold=5):
        self.bar, self.stop, self.target, self.max_hold = bar, stop, target, max_hold

    @classmethod
    def param_grid(cls):
        return {"bar": [0]}

    def prepare(self, bars):
        out = bars.copy()
        out["_i"] = range(len(bars))
        return out

    def entry(self, row):
        return (1, self.stop, self.target) if row["_i"] == self.bar else None


def simulate(bars, strategy, sizer=None, costs=FREE) -> list[Trade]:
    return swing.run_swing("T", swing.to_units(bars), strategy,
                           sizer or Sizer(capital_usd=100_000.0, risk_pct=1.0),
                           costs, USDJPY)


# --- the engine must not see the future ------------------------------------

def test_entry_fills_at_the_next_session_open():
    bars = daily([100.0] * 10)
    bars.iloc[1, bars.columns.get_loc("open")] = 104.0   # gap after the signal
    trade = simulate(bars, AlwaysEnter(bar=0))[0]
    assert trade.entry_price == pytest.approx(104.0)     # not 100.0
    assert trade.entry_time == bars.index[1]


def test_max_hold_closes_the_position_even_when_nothing_is_hit():
    bars = daily([100.0] * 20)
    trade = simulate(bars, AlwaysEnter(max_hold=3, stop=1.0, target=1e6))[0]
    assert trade.exit_reason == "max_hold"
    assert (trade.exit_time - trade.entry_time).days <= 5   # 3 sessions, maybe a weekend


def test_a_session_spanning_stop_and_target_is_resolved_against_us():
    bars = daily([100.0] * 10)
    bars.iloc[1, bars.columns.get_loc("high")] = 120.0   # target
    bars.iloc[1, bars.columns.get_loc("low")] = 90.0     # and stop
    trade = simulate(bars, AlwaysEnter(stop=95.0, target=110.0))[0]
    assert trade.exit_reason == "stop"
    assert trade.pnl_usd < 0


def test_target_is_taken_when_only_the_target_is_reached():
    bars = daily([100.0] * 10)
    bars.iloc[2, bars.columns.get_loc("high")] = 115.0
    trade = simulate(bars, AlwaysEnter(stop=95.0, target=110.0))[0]
    assert trade.exit_reason == "target"
    assert trade.pnl_usd > 0


def test_positions_never_overlap():
    class EveryBar(AlwaysEnter):
        def entry(self, row):
            return 1, 95.0, 110.0

    trades = simulate(daily([100.0] * 30), EveryBar(max_hold=5))
    for a, b in zip(trades, trades[1:]):
        assert b.entry_time > a.exit_time


def test_a_signal_on_the_final_bar_cannot_be_entered():
    assert simulate(daily([100.0] * 6), AlwaysEnter(bar=5)) == []


def test_a_zero_width_stop_produces_no_trade():
    assert simulate(daily([100.0] * 10), AlwaysEnter(stop=100.0)) == []


# --- significance ----------------------------------------------------------

def make_trades(pnls: list[float]) -> list[Trade]:
    ts = pd.Timestamp("2025-01-02")
    return [Trade("T", dt.date(2025, 1, 2) + dt.timedelta(days=i), 1, ts, 100.0,
                  ts + pd.Timedelta(days=3), 101.0, 10, p / USDJPY, p, "target")
            for i, p in enumerate(pnls)]


def test_a_tiny_mean_against_a_wide_spread_is_not_significant():
    pnls = [4000.0, -3900.0] * 30 + [100.0]     # mean near zero, spread huge
    assert abs(stats.summarise(make_trades(pnls)).t_stat) < 2


def test_a_consistent_edge_is_significant():
    assert stats.summarise(make_trades([3000.0, 3200.0, 2800.0] * 15)).t_stat > 2


def test_trades_at_target_counts_per_trade_not_per_day():
    s = stats.summarise(make_trades([12000, 3000, 11000, -2000]), target_jpy=10000)
    assert s.pct_trades_at_target == pytest.approx(50.0)


def test_average_hold_is_reported_in_days():
    assert stats.summarise(make_trades([100.0] * 4)).avg_hold_days == pytest.approx(3.0)


# --- indicators and strategies ---------------------------------------------

def test_rsi_saturates_on_a_one_way_series():
    assert sw.rsi(pd.Series(range(1, 40), dtype=float), 2).iloc[-1] > 95
    assert sw.rsi(pd.Series(range(40, 1, -1), dtype=float), 2).iloc[-1] < 5


def test_atr_matches_a_constant_range():
    df = daily([100.0] * 30, spread=2.5)   # high-low is exactly 5.0 every day
    assert sw.atr(df, 14).iloc[-1] == pytest.approx(5.0)


def test_donchian_breakout_level_excludes_todays_own_high():
    bars = daily([100.0] * 25 + [130.0])
    s = sw.DonchianBreakout(lookback=20)
    row = s.prepare(bars).iloc[-1]
    assert row["_hh"] == pytest.approx(101.0)   # yesterday's high, not 131
    assert s.entry(row) is not None


def test_donchian_stays_out_of_a_flat_market():
    s = sw.DonchianBreakout(lookback=20)
    prepared = s.prepare(daily([100.0] * 40))
    assert all(s.entry(prepared.iloc[i]) is None for i in range(25, 40))


def test_rsi_pullback_respects_its_trend_filter():
    falling = daily([200.0 - i for i in range(250)])     # below its own 200-day MA
    s = sw.RSIPullback(rsi_period=2, entry_level=15, trend_ma=200)
    prepared = s.prepare(falling)
    assert all(s.entry(prepared.iloc[i]) is None for i in range(210, 250))


def test_target_is_placed_at_the_requested_r_multiple():
    bars = daily([100.0] * 25 + [130.0])
    s = sw.DonchianBreakout(lookback=20, atr_mult=2.0, target_r=3.0)
    direction, stop, target = s.entry(s.prepare(bars).iloc[-1])
    assert direction == 1
    assert (target - 130.0) == pytest.approx(3.0 * (130.0 - stop))


def test_every_swing_strategy_exposes_a_finite_grid():
    for cls in sw.REGISTRY.values():
        assert len(list(sw.iter_params(cls))) == sw.grid_size(cls)


# --- loading ---------------------------------------------------------------

def test_load_daily_accepts_the_screeners_title_case_csv(tmp_path):
    daily([100.0] * 5).rename(columns=str.title).rename_axis("Date").to_csv(
        tmp_path / "TSLA.csv")
    df = swing.load_daily(tmp_path, "TSLA")
    assert list(df.columns[:4]) == ["open", "high", "low", "close"]


def test_load_daily_reports_a_missing_symbol_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="run the screen first"):
        swing.load_daily(tmp_path, "NOPE")


def test_window_frames_are_cached_per_window():
    units = swing.to_units(daily([100.0] * 10))
    assert swing._frame(units) is swing._frame(units)
