"""Backtest with walk-forward validation.

    python -m screener.backtest --symbols TSLA,MSTR --strategy all

Reads the minute bars written by ``screener.minutes``. Every headline
number printed is out-of-sample; the in-sample figures are shown beside
them only so the gap between the two is visible.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from .. import fx
from . import data as bt_data
from .costs import CostModel
from .engine import Sizer, trades_to_frame
from .stats import format_summary
from .strategies import REGISTRY, grid_size
from .walkforward import OBJECTIVES, walk_forward

log = logging.getLogger("backtest")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="screener.backtest",
        description="Backtest intraday strategies with walk-forward validation.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--symbols", help="comma-separated tickers, e.g. TSLA,MSTR")
    src.add_argument("--from-passed", help="path to the screen's passed.csv")

    p.add_argument("--strategy", default="all",
                   help=f"one of {', '.join(REGISTRY)} or 'all' (default: all)")
    p.add_argument("--data-dir", default="data/minute", help="minute-bar directory")
    p.add_argument("--bar-minutes", type=int, default=5,
                   help="aggregate 1m bars to this size before testing (default: 5)")

    g = p.add_argument_group("account and risk")
    g.add_argument("--capital-usd", type=float, default=4000.0)
    g.add_argument("--risk-pct", type=float, default=1.0,
                   help="percent of the account risked per trade (default: 1.0)")
    g.add_argument("--fractional", action="store_true", help="allow fractional shares")
    g.add_argument("--target-jpy", type=float, default=5000.0,
                   help="daily profit target used for 'days at target' (default: 5000)")
    g.add_argument("--fx-rate", type=float, default=None, help="fix USD/JPY")

    g = p.add_argument_group("costs")
    g.add_argument("--slippage-bps", type=float, default=5.0,
                   help="one-way slippage in basis points (default: 5)")
    g.add_argument("--commission-per-share", type=float, default=0.0)

    g = p.add_argument_group("walk-forward")
    g.add_argument("--train-sessions", type=int, default=250,
                   help="trading days per training window (default: 250, about a year)")
    g.add_argument("--test-sessions", type=int, default=60,
                   help="trading days per out-of-sample window (default: 60)")
    g.add_argument("--objective", choices=OBJECTIVES, default="expectancy",
                   help="what the training window maximises (default: expectancy)")
    g.add_argument("--min-train-trades", type=int, default=20,
                   help="ignore parameter sets with fewer training trades (default: 20)")
    g.add_argument("--no-baseline", action="store_true",
                   help="skip the all-combinations out-of-sample baseline (faster)")

    p.add_argument("--out-dir", default="data/backtest")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def verdict(result, target_jpy: float) -> list[str]:
    """Plain-language reading of the out-of-sample numbers."""
    oos, ins = result.oos_summary, result.is_summary
    lines: list[str] = []

    if oos.n_trades == 0:
        return ["  VERDICT: no out-of-sample trades. Nothing to conclude."]

    if oos.expectancy_jpy <= 0:
        lines.append(
            f"  VERDICT: does NOT work. Out-of-sample expectancy is "
            f"{oos.expectancy_jpy:,.0f} JPY per trade."
        )
    elif oos.expectancy_jpy <= result.baseline_expectancy:
        lines.append(
            f"  VERDICT: the optimisation added nothing. Out-of-sample "
            f"{oos.expectancy_jpy:,.0f} JPY/trade is no better than the "
            f"{result.baseline_expectancy:,.0f} JPY the average parameter set earned."
        )
    else:
        lines.append(
            f"  VERDICT: positive out of sample -- {oos.expectancy_jpy:,.0f} JPY/trade "
            f"vs a {result.baseline_expectancy:,.0f} JPY all-parameter baseline."
        )

    deg = result.degradation
    if deg is not None and ins.expectancy_jpy > 0:
        note = "" if deg >= 0.5 else "  <- most of the in-sample edge did not survive"
        lines.append(f"  in-sample -> out-of-sample expectancy retained: {deg:.0%}{note}")

    lines.append(
        f"  days clearing {target_jpy:,.0f} JPY out of sample: "
        f"{oos.pct_days_at_target:.1f}% of {oos.n_days} days"
    )

    unstable = [k for k, v in result.param_stability().items() if v > max(2, len(result.windows) // 2)]
    if unstable:
        lines.append(
            f"  unstable parameters across windows: {', '.join(unstable)} "
            f"-- a different answer nearly every window means noise, not an edge"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        path = Path(args.from_passed)
        if not path.exists():
            log.error("%s not found -- run the screen first", path)
            return 1
        df = pd.read_csv(path)
        symbols = [str(t).upper() for t in df.get("ticker", [])]
    if not symbols:
        log.error("no symbols to test")
        return 1

    if args.strategy == "all":
        strategies = list(REGISTRY.values())
    elif args.strategy in REGISTRY:
        strategies = [REGISTRY[args.strategy]]
    else:
        log.error("unknown strategy %r (have: %s)", args.strategy, ", ".join(REGISTRY))
        return 2

    usdjpy, fx_source = fx.get_usdjpy(args.fx_rate)
    sizer = Sizer(capital_usd=args.capital_usd, risk_pct=args.risk_pct,
                  fractional=args.fractional)
    costs = CostModel(slippage_bps=args.slippage_bps,
                      commission_per_share=args.commission_per_share)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nUSD/JPY {usdjpy:,.2f} ({fx_source})  account ${args.capital_usd:,.0f}  "
          f"risk {args.risk_pct}%/trade  slippage {args.slippage_bps} bps one way")
    print(f"walk-forward: train {args.train_sessions} sessions, test {args.test_sessions}, "
          f"objective {args.objective}, {args.bar_minutes}m bars\n")

    rows: list[dict] = []
    failures = 0
    for symbol in symbols:
        try:
            raw = bt_data.load_symbol(Path(args.data_dir), symbol)
        except (FileNotFoundError, ValueError) as exc:
            log.warning("%s: %s", symbol, exc)
            failures += 1
            continue
        bars = bt_data.resample(bt_data.regular_hours(raw), args.bar_minutes)
        sessions = bt_data.sessions(bars)
        log.info("%s: %d sessions of %dm bars (%s to %s)", symbol, len(sessions),
                 args.bar_minutes,
                 sessions[0][0] if sessions else "-", sessions[-1][0] if sessions else "-")

        for cls in strategies:
            try:
                result = walk_forward(
                    symbol, sessions, cls, sizer, costs, usdjpy,
                    target_jpy=args.target_jpy,
                    train_sessions=args.train_sessions,
                    test_sessions=args.test_sessions,
                    objective=args.objective,
                    min_train_trades=args.min_train_trades,
                    baseline=not args.no_baseline,
                )
            except ValueError as exc:
                log.warning("%s/%s: %s", symbol, cls.name, exc)
                failures += 1
                continue

            print("=" * 78)
            print(f"{symbol}  strategy={cls.name}  "
                  f"{grid_size(cls)} parameter combos x {len(result.windows)} windows")
            print("-" * 78)
            print(format_summary("  IN-SAMPLE (chosen params, training data)", result.is_summary))
            print(format_summary("  OUT-OF-SAMPLE (what you would have earned)", result.oos_summary))
            print()
            for line in verdict(result, args.target_jpy):
                print(line)
            print()

            if result.oos_trades:
                frame = trades_to_frame(result.oos_trades)
                frame.to_csv(out_dir / f"{symbol}-{cls.name}-oos-trades.csv", index=False)
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
                } for w in result.windows]).to_csv(
                    out_dir / f"{symbol}-{cls.name}-windows.csv", index=False)

            row = {"symbol": symbol, "strategy": cls.name, "windows": len(result.windows)}
            row.update({f"is_{k}": v for k, v in result.is_summary.as_dict().items()})
            row.update({f"oos_{k}": v for k, v in result.oos_summary.as_dict().items()})
            row["baseline_expectancy_jpy"] = result.baseline_expectancy
            row["degradation"] = result.degradation
            rows.append(row)

    if rows:
        summary = pd.DataFrame(rows).sort_values("oos_expectancy_jpy", ascending=False)
        summary.to_csv(out_dir / "summary.csv", index=False)
        print("=" * 78)
        print("RANKED BY OUT-OF-SAMPLE EXPECTANCY")
        cols = ["symbol", "strategy", "oos_n_trades", "oos_expectancy_jpy",
                "oos_total_pnl_jpy", "oos_pct_days_at_target", "baseline_expectancy_jpy"]
        print(summary[cols].to_string(index=False))
        print(f"\nwrote {out_dir}/summary.csv and per-run trade and window files")
        if (summary["oos_expectancy_jpy"] <= 0).all():
            print("\nNothing was profitable out of sample. That is a real result, "
                  "not a bug: these are the classic patterns, and they are the "
                  "first place everyone looks.")
        return 0

    log.error("no results produced (%d failure(s))", failures)
    return 1


if __name__ == "__main__":
    sys.exit(main())
