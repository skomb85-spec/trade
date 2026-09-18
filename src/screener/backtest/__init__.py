"""Intraday backtesting with walk-forward validation.

The walk-forward split is the point of this package, not an add-on. Any
parameter search over a fixed history will find a combination that looks
profitable -- that is what searching does. Only performance on data the
search never saw carries information, so the engine here is built to
produce out-of-sample results and to report them next to the in-sample
ones, where the gap between the two is visible.
"""

__all__ = ["costs", "data", "engine", "stats", "strategies", "walkforward"]
