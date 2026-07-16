#!/usr/bin/env python3
"""Reproducible signal-expectancy diagnostic for the engine (paper) cohort.

Reads the trading repo's tracker DB (`paper_outcomes`) read-only and prints the
breakdown behind docs/SIGNAL_EXPECTANCY_DIAGNOSIS_2026-07-16.md — engine-cohort
expectancy, win/payoff, per-symbol concentration, and the break-even win rate
implied by the observed payoff ratio. No writes, no network.

DB resolution (first that exists):
  1. $GESH_TRACKER_DB
  2. $GESH_TRADING_HOME/gesh_financial.db
  3. ../gesh-project-trading/gesh_financial.db   (sibling-dir convention)

Usage:  python3 scripts/diagnose_signal_expectancy.py
The engine cohort = sources other than 'manual' (override with --sources).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def _resolve_db() -> Path | None:
    env = os.getenv("GESH_TRACKER_DB")
    if env:
        return Path(env).expanduser()
    home = os.getenv("GESH_TRADING_HOME")
    if home:
        p = Path(home).expanduser() / "gesh_financial.db"
        if p.exists():
            return p
    sibling = Path(__file__).resolve().parent.parent.parent / "gesh-project-trading" / "gesh_financial.db"
    return sibling if sibling.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="consensus,market_scan",
                    help="comma list treated as the engine cohort (default: consensus,market_scan)")
    ap.add_argument("--db", default=None, help="explicit tracker DB path (overrides resolution)")
    args = ap.parse_args()

    db = Path(args.db).expanduser() if args.db else _resolve_db()
    if not db or not db.exists():
        print("ERROR: tracker DB not found. Set $GESH_TRACKER_DB or --db.", file=sys.stderr)
        return 2

    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    placeholders = ",".join("?" * len(sources))

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT symbol, source, pnl_usd, pnl_pct, outcome, hold_days "
            f"FROM paper_outcomes WHERE source IN ({placeholders})", sources
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"ERROR reading paper_outcomes: {e}", file=sys.stderr)
        return 2
    finally:
        con.close()

    if not rows:
        print(f"No engine-cohort outcomes for sources={sources} in {db}")
        return 0

    n = len(rows)
    wins = [r for r in rows if r[4] == "WIN"]
    losses = [r for r in rows if r[4] == "LOSS"]
    be = [r for r in rows if r[4] == "BREAKEVEN"]
    tot_usd = sum(r[2] for r in rows)
    expectancy = sum(r[3] for r in rows) / n
    avg_win = (sum(r[3] for r in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(r[3] for r in losses) / len(losses)) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss else float("inf")
    win_rate = len(wins) / n
    # Break-even win rate for the observed payoff: w* = 1 / (1 + payoff).
    breakeven_wr = 1.0 / (1.0 + payoff) if payoff not in (0, float("inf")) else float("nan")

    print(f"DB: {db}")
    print(f"Engine cohort sources: {sources}\n")
    print(f"closed trades      : {n}   (W {len(wins)} / L {len(losses)} / BE {len(be)})")
    print(f"total P&L (USD)    : {tot_usd:+.2f}")
    print(f"expectancy / trade : {expectancy:+.4f}  ({expectancy*100:+.2f}% )")
    print(f"win rate           : {win_rate*100:.1f}%")
    print(f"avg win / avg loss : {avg_win*100:+.2f}% / {avg_loss*100:+.2f}%   payoff {payoff:.2f}:1")
    print(f"break-even win rate: {breakeven_wr*100:.1f}%   "
          f"({'BELOW — losing' if win_rate < breakeven_wr else 'above'} the required threshold)\n")

    # Per-symbol concentration — where the money actually went.
    by_sym: dict[str, list] = {}
    for r in rows:
        by_sym.setdefault(r[0], []).append(r)
    print("per-symbol (sorted by P&L — concentration check):")
    print(f"  {'sym':<6} {'n':>2} {'W':>2} {'L':>2} {'pnl_usd':>10}")
    for sym, rs in sorted(by_sym.items(), key=lambda kv: sum(r[2] for r in kv[1])):
        w = sum(1 for r in rs if r[4] == "WIN")
        loss = sum(1 for r in rs if r[4] == "LOSS")
        print(f"  {sym:<6} {len(rs):>2} {w:>2} {loss:>2} {sum(r[2] for r in rs):>+10.2f}")

    # The single-name-dependence check: cohort P&L minus the best name.
    best_sym = max(by_sym, key=lambda s: sum(r[2] for r in by_sym[s]))
    best_pnl = sum(r[2] for r in by_sym[best_sym])
    print(f"\nsingle-name dependence: total {tot_usd:+.2f} → "
          f"{tot_usd - best_pnl:+.2f} once the best name ({best_sym}, {best_pnl:+.2f}) is removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
