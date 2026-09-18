"""Backtest with walk-forward validation.

    python -m screener.backtest --symbols TSLA,MSTR                 # swing, the default
    python -m screener.backtest --symbols TSLA --mode intraday

Two modes share one walk-forward split:

``swing``     daily bars, positions held 2-7 sessions, from the cache the
              screen already fills. Decades of history, and frictions too
              small to decide the outcome.
``intraday``  minute bars from ``screener.minutes``, flat every close.
              Limited to what Alpaca serves, and far more cost-sensitive.

Every headline number printed is out-of-sample. The in-sample figures sit
beside them only so the gap between the two is visible.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from .. import fx
from . import data as bt_data
from . import strategies as intraday_strategies
from . import swing as swing_engine
from . import swing_strategies
from .costs import CostModel
from .engine import Sizer, run, trades_to_frame
from .stats import format_summary
from .walkforward import OBJECTIVES, walk_forward

log = logging.getLogger("backtest")

# Swing holds run in days, so a window covers far more calendar time and
# the target is per trade rather than per day.
MODE_DEFAULTS = {
    "swing":    {"data_dir": "data/cache",  "train": 500, "test": 125,
                 "target_jpy": 10_000.0, "bar_minutes": 0},
    "intraday": {"data_dir": "data/minute", "train": 250, "test": 60,
                 "target_jpy": 5_000.0, "bar_minutes": 5},
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="screener.backtest",
        description="Backtest swing or intraday strategies with walk-forward validation.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--symbols", help="comma-separated tickers, e.g. TSLA,MSTR")
    src.add_argument("--from-passed", help="path to the screen's passed.csv")

    p.add_argument("--mode", choices=("swing", "intraday"), default="swing",
                   help="swing = daily bars held 2-7 sessions (default); intraday = minute bars")
    p.add_argument("--strategy", default="all",
                   help="a strategy name for the chosen mode, or 'all' (default: all)")
    p.add_argument("--data-dir", default=None,
                   help="bar directory (default: data/cache for swing, data/minute for intraday)")
    p.add_argument("--bar-minutes", type=int, default=None,
                   help="intraday only: aggregate 1m bars to this size (default: 5)")

    g = p.add_argument_group("account and risk")
    g.add_argument("--capital-usd", type=float, default=4000.0)
    g.add_argument("--risk-pct", type=float, default=1.0,
                   help="percent of the account risked per trade (default: 1.0)")
    g.add_argument("--fractional", action="store_true", help="allow fractional shares")
    g.add_argument("--target-jpy", type=float, default=None,
                   help="profit target: per trade in swing mode (default 10,000), "
                        "per day in intraday mode (default 5,000)")
    g.add_argument("--fx-rate", type=float, default=None, help="fix USD/JPY")

    g = p.add_argument_group("costs")
    g.add_argument("--slippage-bps", type=float, default=5.0,
                   help="one-way slippage in basis points (default: 5)")
    g.add_argument("--commission-per-share", type=float, default=0.0)

    g = p.add_argument_group("walk-forward")
    g.add_argument("--train-sessions", type=int, default=None,
                   help="sessions per training window (default: 500 swing, 250 intraday)")
    g.add_argument("--test-sessions", type=int, default=None,
                   help="sessions per out-of-sample window (default: 125 swing, 60 intraday)")
    g.add_argument("--objective", choices=OBJECTIVES, default="expectancy",
                   help="what the training window maximises (default: expectancy)")
    g.add_argument("--min-train-trades", type=int, default=20,
                   help="ignore parameter sets with fewer training trades (default: 20)")
    g.add_argument("--no-baseline", action="store_true",
                   help="skip the all-combinations out-of-sample baseline (faster)")

    p.add_argument("--out-dir", default="data/backtest")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def verdict(result, target_jpy: float, mode: str) -> list[str]:
    """Plain-language reading of the out-of-sample numbers."""
    oos, ins = result.oos_summary, result.is_summary
    if oos.n_trades == 0:
        return ["  VERDICT: no out-of-sample trades. Nothing to conclude."]

    lines: list[str] = []
    # A positive mean over a few dozen trades with a spread of thousands is
    # not a result. Check that before comparing it to anything.
    if 0 < oos.expectancy_jpy and abs(oos.t_stat) < 2:
        return [
            f"  VERDICT: not distinguishable from zero. Out-of-sample expectancy is "
            f"{oos.expectancy_jpy:,.0f} JPY/trade over {oos.n_trades} trades, "
            f"t = {oos.t_stat:+.2f}. Below |t| = 2 this is noise, not an edge.",
            f"  trades clearing {target_jpy:,.0f} JPY out of sample: "
            f"{oos.pct_trades_at_target:.1f}%",
        ]
    if oos.expectancy_jpy <= 0:
        lines.append(f"  VERDICT: does NOT work. Out-of-sample expectancy is "
                     f"{oos.expectancy_jpy:,.0f} JPY per trade.")
    elif oos.expectancy_jpy <= result.baseline_expectancy:
        lines.append(f"  VERDICT: the optimisation added nothing. Out-of-sample "
                     f"{oos.expectancy_jpy:,.0f} JPY/trade is no better than the "
                     f"{result.baseline_expectancy:,.0f} JPY the average parameter set earned.")
    else:
        lines.append(f"  VERDICT: positive out of sample -- {oos.expectancy_jpy:,.0f} JPY/trade "
                     f"(t = {oos.t_stat:+.2f}) vs a {result.baseline_expectancy:,.0f} JPY "
                     f"all-parameter baseline.")

    deg = result.degradation
    if deg is not None and ins.expectancy_jpy > 0:
        note = "" if deg >= 0.5 else "  <- most of the in-sample edge did not survive"
        lines.append(f"  in-sample -> out-of-sample expectancy retained: {deg:.0%}{note}")

    if mode == "swing":
        lines.append(f"  trades clearing {target_jpy:,.0f} JPY out of sample: "
                     f"{oos.pct_trades_at_target:.1f}% of {oos.n_trades} trades "
                     f"(average hold {oos.avg_hold_days:.1f} days)")
        if oos.pct_trades_at_target == 0 and oos.expectancy_jpy > 0:
            lines.append("  no trade reached the target: the position size, not the "
                         "strategy, is the binding constraint -- raise --risk-pct, "
                         "or use --fractional so a high-priced share is not floored away")
    else:
        lines.append(f"  days clearing {target_jpy:,.0f} JPY out of sample: "
                     f"{oos.pct_days_at_target:.1f}% of {oos.n_days} days")

    unstable = [k for k, v in result.param_stability().items()
                if v > max(2, len(result.windows) // 2)]
    if unstable:
        lines.append(f"  unstable parameters across windows: {', '.join(unstable)} "
                     f"-- a different answer nearly every window means noise, not an edge")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    d = MODE_DEFAULTS[args.mode]
    data_dir = Path(args.data_dir or d["data_dir"])
    train_sessions = args.train_sessions or d["train"]
    test_sessions = args.test_sessions or d["test"]
    target_jpy = args.target_jpy if args.target_jpy is not None else d["target_jpy"]
    bar_minutes = args.bar_minutes if args.bar_minutes is not None else d["bar_minutes"]

    if args.mode == "swing":
        registry, runner = swing_strategies.REGISTRY, swing_engine.run_swing
        grid_size = swing_strategies.grid_size
    else:
        registry, runner = intraday_strategies.REGISTRY, run
        grid_size = intraday_strategies.grid_size

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        path = Path(args.from_passed)
        if not path.exists():
            log.error("%s not found -- run the screen first", path)
            return 1
        symbols = [str(t).upper() for t in pd.read_csv(path).get("ticker", [])]
    if not symbols:
        log.error("no symbols to test")
        return 1

    if args.strategy == "all":
        chosen = list(registry.values())
    elif args.strategy in registry:
        chosen = [registry[args.strategy]]
    else:
        log.error("unknown %s strategy %r (have: %s)",
                  args.mode, args.strategy, ", ".join(registry))
        return 2

    usdjpy, fx_source = fx.get_usdjpy(args.fx_rate)
    sizer = Sizer(capital_usd=args.capital_usd, risk_pct=args.risk_pct,
                  fractional=args.fractional)
    costs = CostModel(slippage_bps=args.slippage_bps,
                      commission_per_share=args.commission_per_share)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    unit = "per trade" if args.mode == "swing" else "per day"
    print(f"\nmode {args.mode}  USD/JPY {usdjpy:,.2f} ({fx_source})  "
          f"account ${args.capital_usd:,.0f}  risk {args.risk_pct}%/trade  "
          f"slippage {args.slippage_bps} bps one way")
    print(f"target {target_jpy:,.0f} JPY {unit}   walk-forward: train {train_sessions} "
          f"sessions, test {test_sessions}, objective {args.objective}")
    if args.mode == "swing":
        # A 2R winner returns twice what was risked, so the target implies a
        # floor on risk per trade that no strategy choice can get around.
        need = target_jpy / usdjpy / 2 / args.capital_usd * 100
        print(f"=> a 2R winner clears {target_jpy:,.0f} JPY only at "
              f"{need:.2f}% risk per trade (currently {args.risk_pct:.2f}%)"
              f"{'' if args.risk_pct >= need else '  <- too low to reach the target'}")
    print()

    rows: list[dict] = []
    failures = 0
    for symbol in symbols:
        try:
            if args.mode == "swing":
                bars = swing_engine.load_daily(data_dir, symbol)
                units = swing_engine.to_units(bars)
            else:
                raw = bt_data.load_symbol(data_dir, symbol)
                bars = bt_data.resample(bt_data.regular_hours(raw), bar_minutes)
                units = bt_data.sessions(bars)
        except (FileNotFoundError, ValueError) as exc:
            log.warning("%s: %s", symbol, exc)
            failures += 1
            continue

        if not units:
            log.warning("%s: no usable sessions", symbol)
            failures += 1
            continue
        log.info("%s: %d session(s) (%s to %s)", symbol, len(units),
                 units[0][0], units[-1][0])

        for cls in chosen:
            try:
                result = walk_forward(
                    symbol, units, cls, sizer, costs, usdjpy,
                    target_jpy=target_jpy,
                    train_sessions=train_sessions, test_sessions=test_sessions,
                    objective=args.objective, min_train_trades=args.min_train_trades,
                    baseline=not args.no_baseline, runner=runner,
                )
            except ValueError as exc:
                log.warning("%s/%s: %s", symbol, cls.name, exc)
                failures += 1
                continue

            print("=" * 78)
            print(f"{symbol}  {args.mode}/{cls.name}  "
                  f"{grid_size(cls)} parameter combos x {len(result.windows)} windows")
            print("-" * 78)
            print(format_summary("  IN-SAMPLE (chosen params, training data)", result.is_summary))
            print(format_summary("  OUT-OF-SAMPLE (what you would have earned)", result.oos_summary))
            print()
            for line in verdict(result, target_jpy, args.mode):
                print(line)
            print()

            tag = f"{symbol}-{args.mode}-{cls.name}"
            if result.oos_trades:
                trades_to_frame(result.oos_trades).to_csv(
                    out_dir / f"{tag}-oos-trades.csv", index=False)
            if result.windows:
                pd.DataFrame([{
                    "window": w.index,
                    "train_start": w.train_start, "train_end": w.train_end,
                    "test_start": w.test_start, "test_end": w.test_end,
                    "best_params": repr(w.best_params),
                    "train_expectancy_jpy": w.train.expectancy_jpy,
                    "test_expectancy_jpy": w.test.expectancy_jpy,
                    "test_trades": w.test.n_trades,
                    "test_total_jpy": w.test.total_pnl_jpy,
                    "baseline_expectancy_jpy": w.baseline_expectancy,
                } for w in result.windows]).to_csv(out_dir / f"{tag}-windows.csv", index=False)

            row = {"symbol": symbol, "mode": args.mode, "strategy": cls.name,
                   "windows": len(result.windows)}
            row.update({f"is_{k}": v for k, v in result.is_summary.as_dict().items()})
            row.update({f"oos_{k}": v for k, v in result.oos_summary.as_dict().items()})
            row["baseline_expectancy_jpy"] = result.baseline_expectancy
            row["degradation"] = result.degradation
            rows.append(row)

    if rows:
        summary = pd.DataFrame(rows).sort_values("oos_expectancy_jpy", ascending=False)
        summary.to_csv(out_dir / "summary.csv", index=False)
        hit = "oos_pct_trades_at_target" if args.mode == "swing" else "oos_pct_days_at_target"
        print("=" * 78)
        print("RANKED BY OUT-OF-SAMPLE EXPECTANCY")
        print(summary[["symbol", "strategy", "oos_n_trades", "oos_expectancy_jpy",
                       "oos_total_pnl_jpy", hit, "oos_avg_hold_days",
                       "baseline_expectancy_jpy"]].to_string(index=False))
        print(f"\nwrote {out_dir}/summary.csv and per-run trade and window files")
        if (summary["oos_expectancy_jpy"] <= 0).all():
            print("\nNothing was profitable out of sample. That is a real result, "
                  "not a bug: these are the textbook patterns, and they are the "
                  "first place everyone looks.")
        return 0

    log.error("no results produced (%d failure(s))", failures)
    return 1


if __name__ == "__main__":
    sys.exit(main())
