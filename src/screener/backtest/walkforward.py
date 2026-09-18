"""Walk-forward validation.

A parameter search over a fixed history always finds something. With 48
combinations and a coin-flip strategy, the best of them will look good --
that is arithmetic, not evidence. Walk-forward answers the only question
that matters: having chosen parameters on data up to a date, how did those
parameters do *after* it?

Each window fits on ``train`` sessions and is scored on the ``test``
sessions that follow, then the window rolls forward by the test length.
Concatenating every test segment gives one out-of-sample track record over
nearly the whole history, in which no trade was ever chosen with knowledge
of its own outcome.

Two diagnostics come with it, because a single OOS number is still easy to
misread:

``baseline_expectancy``
    the mean out-of-sample result across *every* parameter combination in
    the window. If the selected parameters do no better than this, the
    optimisation contributed nothing and the strategy is what it is.
``parameter stability``
    which parameters each window picked. A set that jumps around every
    window is fitting noise even when the aggregate OOS looks positive.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from .costs import CostModel
from .engine import Sizer, Trade, run
from .stats import Summary, summarise
from .strategies import Strategy, iter_params

log = logging.getLogger(__name__)

Sessions = list[tuple[dt.date, "object"]]

OBJECTIVES = ("expectancy", "total", "sharpe", "profit_factor")


def objective_value(summary: Summary, objective: str) -> float:
    if summary.n_trades == 0:
        return float("-inf")
    if objective == "expectancy":
        return summary.expectancy_jpy
    if objective == "total":
        return summary.total_pnl_jpy
    if objective == "sharpe":
        return summary.sharpe
    if objective == "profit_factor":
        pf = summary.profit_factor
        return 1e9 if pf == float("inf") else pf
    raise ValueError(f"unknown objective {objective!r}")


@dataclass
class WindowResult:
    index: int
    train_start: dt.date
    train_end: dt.date
    test_start: dt.date
    test_end: dt.date
    best_params: dict
    n_combos: int
    train: Summary
    test: Summary
    baseline_expectancy: float
    test_trades: list[Trade] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    symbol: str
    strategy: str
    objective: str
    windows: list[WindowResult]
    oos_trades: list[Trade]
    is_summary: Summary
    oos_summary: Summary
    baseline_expectancy: float

    @property
    def degradation(self) -> float | None:
        """OOS expectancy as a fraction of in-sample. Below ~0.5 is a warning."""
        if self.is_summary.n_trades == 0 or self.is_summary.expectancy_jpy == 0:
            return None
        return self.oos_summary.expectancy_jpy / self.is_summary.expectancy_jpy

    def param_stability(self) -> dict[str, int]:
        """How many distinct values each parameter took across windows."""
        if not self.windows:
            return {}
        keys = self.windows[0].best_params.keys()
        return {k: len({repr(w.best_params.get(k)) for w in self.windows}) for k in keys}


def make_windows(n_sessions: int, train: int, test: int) -> list[tuple[int, int, int, int]]:
    """Rolling (train, test) index spans, stepping forward by the test length."""
    spans: list[tuple[int, int, int, int]] = []
    start = 0
    while start + train + test <= n_sessions:
        spans.append((start, start + train, start + train, start + train + test))
        start += test
    return spans


def walk_forward(
    symbol: str,
    sessions: Sessions,
    strategy_cls: type[Strategy],
    sizer: Sizer,
    costs: CostModel,
    usdjpy: float,
    target_jpy: float = 5000.0,
    train_sessions: int = 250,
    test_sessions: int = 60,
    objective: str = "expectancy",
    min_train_trades: int = 20,
    baseline: bool = True,
    runner=run,
) -> WalkForwardResult:
    combos = list(iter_params(strategy_cls))
    spans = make_windows(len(sessions), train_sessions, test_sessions)
    if not spans:
        raise ValueError(
            f"{symbol}: {len(sessions)} sessions is too few for "
            f"train={train_sessions} + test={test_sessions}"
        )
    log.info("%s/%s: %d window(s), %d parameter combo(s) each",
             symbol, strategy_cls.name, len(spans), len(combos))

    windows: list[WindowResult] = []
    oos_trades: list[Trade] = []
    is_trades: list[Trade] = []
    baselines: list[float] = []

    for i, (a, b, c, d) in enumerate(spans):
        train_set, test_set = sessions[a:b], sessions[c:d]

        best_score, best_params, best_train = float("-inf"), None, Summary()
        for params in combos:
            trades = runner(symbol, train_set, strategy_cls(**params), sizer, costs, usdjpy)
            summary = summarise(trades, target_jpy)
            # Too few trades to distinguish an edge from a lucky handful.
            if summary.n_trades < min_train_trades:
                continue
            score = objective_value(summary, objective)
            if score > best_score:
                best_score, best_params, best_train = score, params, summary

        if best_params is None:
            log.info("  window %d: no combo reached %d trades in training; skipped",
                     i, min_train_trades)
            continue

        test_trades = runner(symbol, test_set, strategy_cls(**best_params), sizer, costs, usdjpy)
        test_summary = summarise(test_trades, target_jpy)

        # What the average parameter set earned out of sample, as the bar
        # the optimisation has to beat to have been worth running.
        window_baseline = 0.0
        if baseline:
            scores = []
            for params in combos:
                t = runner(symbol, test_set, strategy_cls(**params), sizer, costs, usdjpy)
                if t:
                    scores.append(summarise(t, target_jpy).expectancy_jpy)
            window_baseline = sum(scores) / len(scores) if scores else 0.0
            baselines.append(window_baseline)

        windows.append(WindowResult(
            index=i,
            train_start=train_set[0][0], train_end=train_set[-1][0],
            test_start=test_set[0][0], test_end=test_set[-1][0],
            best_params=best_params, n_combos=len(combos),
            train=best_train, test=test_summary,
            baseline_expectancy=round(window_baseline, 1),
            test_trades=test_trades,
        ))
        oos_trades.extend(test_trades)
        is_trades.extend(runner(symbol, train_set, strategy_cls(**best_params),
                             sizer, costs, usdjpy))
        log.info("  window %d %s..%s: chose %s -> OOS %s JPY over %d trades",
                 i, test_set[0][0], test_set[-1][0], best_params,
                 f"{test_summary.total_pnl_jpy:,.0f}", test_summary.n_trades)

    return WalkForwardResult(
        symbol=symbol,
        strategy=strategy_cls.name,
        objective=objective,
        windows=windows,
        oos_trades=oos_trades,
        is_summary=summarise(is_trades, target_jpy),
        oos_summary=summarise(oos_trades, target_jpy),
        baseline_expectancy=round(sum(baselines) / len(baselines), 1) if baselines else 0.0,
    )
