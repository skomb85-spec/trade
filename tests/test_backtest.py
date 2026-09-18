"""Offline tests for the backtest engine and walk-forward split."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screener.backtest import data as bt_data  # noqa: E402
from screener.backtest import stats, strategies, walkforward  # noqa: E402
from screener.backtest.costs import CostModel  # noqa: E402
from screener.backtest.engine import Sizer, Trade, run, run_session  # noqa: E402
from screener.backtest.strategies import DayContext, Signal, Strategy  # noqa: E402

FREE = CostModel(slippage_bps=0.0)
USDJPY = 150.0


def session_bars(n: int = 40, start: str = "2025-01-02 09:30") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="1min", tz="America/New_York")
    return pd.DataFrame(
        {"open": [100.0] * n, "high": [100.5] * n, "low": [99.5] * n,
         "close": [100.0] * n, "volume": [10_000] * n},
        index=idx,
    )


class FixedSignal(Strategy):
    """A stub that always fires on a chosen bar, for engine-level tests."""

    name = "fixed"

    def __init__(self, bar=5, direction=1, stop=98.0, target=102.0):
        self.bar, self.direction, self.stop, self.target = bar, direction, stop, target

    @classmethod
    def param_grid(cls):
        return {"bar": [5], "direction": [1], "stop": [98.0], "target": [102.0]}

    def signals(self, bars, ctx):
        return [Signal(self.bar, self.direction, self.stop, self.target)]


def simulate(bars, strategy, sizer=None, costs=FREE) -> Trade | None:
    return run_session("T", dt.date(2025, 1, 2), bars, strategy,
                       DayContext(prev_close=100.0),
                       sizer or Sizer(capital_usd=4000.0, risk_pct=1.0),
                       costs, USDJPY)


# --- the engine must not see the future ------------------------------------

def test_entry_fills_at_the_next_bar_open_not_the_signal_bar():
    bars = session_bars()
    bars.iloc[6, bars.columns.get_loc("open")] = 105.0  # gap after the signal
    trade = simulate(bars, FixedSignal(bar=5))
    assert trade is not None
    assert trade.entry_price == pytest.approx(105.0)   # not 100.0
    assert trade.entry_time == bars.index[6]


def test_a_bar_spanning_stop_and_target_is_resolved_against_us():
    bars = session_bars()
    # Bar 7 reaches both the 102 target and the 98 stop.
    bars.iloc[7, bars.columns.get_loc("high")] = 103.0
    bars.iloc[7, bars.columns.get_loc("low")] = 97.0
    trade = simulate(bars, FixedSignal(bar=5, stop=98.0, target=102.0))
    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(98.0)
    assert trade.pnl_usd < 0


def test_an_unresolved_position_is_flat_at_the_close():
    trade = simulate(session_bars(), FixedSignal(bar=5, stop=90.0, target=110.0))
    assert trade.exit_reason == "close"
    assert trade.exit_time == session_bars().index[-1]


def test_short_trades_are_signed_correctly():
    bars = session_bars()
    bars.iloc[10:, bars.columns.get_loc("low")] = 95.0  # price falls to the target
    trade = simulate(bars, FixedSignal(bar=5, direction=-1, stop=102.0, target=96.0))
    assert trade.direction == -1
    assert trade.exit_reason == "target"
    assert trade.pnl_usd > 0


def test_no_signal_means_no_trade():
    class Silent(FixedSignal):
        def signals(self, bars, ctx):
            return []

    assert simulate(session_bars(), Silent()) is None


def test_a_signal_on_the_last_bar_cannot_be_entered():
    bars = session_bars(n=20)
    assert simulate(bars, FixedSignal(bar=19)) is None


# --- costs and sizing ------------------------------------------------------

def test_slippage_is_paid_in_the_wrong_direction_both_ways():
    costs = CostModel(slippage_bps=10.0)
    assert costs.fill_price(100.0, "buy") == pytest.approx(100.10)
    assert costs.fill_price(100.0, "sell") == pytest.approx(99.90)


def test_slippage_turns_a_break_even_trade_into_a_loss():
    bars = session_bars()
    flat = FixedSignal(bar=5, stop=90.0, target=110.0)  # exits at the close, same price
    assert simulate(bars, flat, costs=FREE).pnl_usd == pytest.approx(0.0)
    assert simulate(bars, flat, costs=CostModel(slippage_bps=10.0)).pnl_usd < 0


def test_size_is_capped_by_risk_then_by_capital():
    sizer = Sizer(capital_usd=4000.0, risk_pct=1.0)
    # $40 of risk over a $4 stop distance is 10 shares.
    assert sizer.shares(400.0, 396.0) == 10
    # A 1-cent stop would allow 4,000 shares by risk; capital allows 160.
    assert sizer.shares(25.0, 24.99) == 160


def test_fractional_sizing_does_not_floor():
    assert Sizer(capital_usd=4000.0, risk_pct=1.0, fractional=True).shares(
        400.0, 395.0) == pytest.approx(8.0)


# --- statistics ------------------------------------------------------------

def make_trades(pnls: list[float]) -> list[Trade]:
    out = []
    for i, pnl in enumerate(pnls):
        ts = pd.Timestamp("2025-01-02 10:00", tz="America/New_York")
        out.append(Trade("T", dt.date(2025, 1, 2) + dt.timedelta(days=i), 1, ts, 100.0,
                         ts, 101.0, 10, pnl / USDJPY, pnl, "target"))
    return out


def test_days_at_target_counts_days_not_trades():
    s = stats.summarise(make_trades([6000, 1000, 7000, -2000]), target_jpy=5000)
    assert s.n_days == 4
    assert s.pct_days_at_target == pytest.approx(50.0)  # 2 of 4


def test_profit_factor_and_drawdown():
    s = stats.summarise(make_trades([100, -50, 100, -50]), target_jpy=5000)
    assert s.profit_factor == pytest.approx(2.0)
    assert s.max_drawdown_jpy == pytest.approx(-50)


def test_empty_summary_is_safe():
    assert stats.summarise([]).n_trades == 0


# --- the walk-forward split ------------------------------------------------

def test_test_windows_never_overlap_their_training_data():
    for a, b, c, d in walkforward.make_windows(1000, train=250, test=60):
        assert b == c            # test starts exactly where train ends
        assert a < b < d         # and runs strictly after it


def test_windows_roll_forward_without_reusing_test_data():
    spans = walkforward.make_windows(1000, train=250, test=60)
    test_spans = [(c, d) for _, _, c, d in spans]
    for (_, prev_end), (next_start, _) in zip(test_spans, test_spans[1:]):
        assert next_start == prev_end   # contiguous, never overlapping


def test_too_little_history_yields_no_windows():
    assert walkforward.make_windows(100, train=250, test=60) == []


def test_walk_forward_refuses_a_history_it_cannot_split():
    sessions = [(dt.date(2025, 1, 2), session_bars())] * 10
    with pytest.raises(ValueError, match="too few"):
        walkforward.walk_forward(
            "T", sessions, strategies.OpeningRangeBreakout,
            Sizer(), FREE, USDJPY, train_sessions=250, test_sessions=60,
        )


def test_objective_ranks_an_empty_result_last():
    assert walkforward.objective_value(stats.Summary(), "expectancy") == float("-inf")


# --- strategies ------------------------------------------------------------

def test_orb_goes_long_on_a_break_above_the_opening_range():
    bars = session_bars()
    bars.iloc[10:, bars.columns.get_loc("high")] = 110.0
    sig = strategies.OpeningRangeBreakout(or_minutes=5).signals(bars, DayContext(100.0))
    assert sig and sig[0].direction == +1 and sig[0].bar == 10


def test_orb_stays_out_when_the_range_never_breaks():
    assert strategies.OpeningRangeBreakout(or_minutes=5).signals(
        session_bars(), DayContext(100.0)) == []


def test_gap_fill_trades_back_towards_the_previous_close():
    bars = session_bars()
    bars.iloc[:, bars.columns.get_loc("open")] = 105.0
    sig = strategies.GapFill(min_gap_pct=1.0).signals(bars, DayContext(prev_close=100.0))
    assert sig and sig[0].direction == -1   # gapped up, so sell towards 100
    assert sig[0].target == pytest.approx(100.0)


def test_gap_fill_needs_a_previous_close():
    assert strategies.GapFill().signals(session_bars(), DayContext(prev_close=None)) == []


def test_every_registered_strategy_exposes_a_finite_grid():
    for name, cls in strategies.REGISTRY.items():
        assert strategies.grid_size(cls) > 0
        assert len(list(strategies.iter_params(cls))) == strategies.grid_size(cls)


# --- session handling ------------------------------------------------------

def test_regular_hours_drops_pre_and_post_market():
    idx = pd.date_range("2025-01-02 04:00", periods=24 * 60, freq="1min",
                        tz="America/New_York")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
                      index=idx)
    kept = bt_data.regular_hours(df)
    assert kept.index.min().time() == dt.time(9, 30)
    assert kept.index.max().time() == dt.time(15, 59)


def test_stub_sessions_are_dropped():
    full = session_bars(n=40, start="2025-01-02 09:30")
    stub = session_bars(n=5, start="2025-01-03 09:30")
    days = bt_data.sessions(pd.concat([full, stub]))
    assert [d for d, _ in days] == [dt.date(2025, 1, 2)]


def test_resample_aggregates_ohlcv_correctly():
    bars = session_bars(n=10)
    bars.iloc[3, bars.columns.get_loc("high")] = 120.0
    out = bt_data.resample(bars, 5)
    assert len(out) == 2
    assert out["high"].iloc[0] == pytest.approx(120.0)
    assert out["volume"].iloc[0] == pytest.approx(50_000)


# --- end to end ------------------------------------------------------------

def test_run_produces_one_trade_per_session_at_most():
    sessions = [(dt.date(2025, 1, 2) + dt.timedelta(days=i), session_bars())
                for i in range(5)]
    trades = run("T", sessions, FixedSignal(bar=5, stop=90.0, target=110.0),
                 Sizer(), FREE, USDJPY)
    assert len(trades) == 5
    assert len({t.date for t in trades}) == 5
