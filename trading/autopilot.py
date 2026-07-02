"""
trading/autopilot.py — scheduled job runner for the GESH trading system.

Four scheduled jobs (ET timezone):
  08:30  pre_market_scan    — run consensus over watchlist, store top candidates
  09:35  open_window        — materialize intents, push through risk gate, execute survivors
  12:30  midday_rebalance   — re-run consensus on held positions, check for >5% drift
  15:55  close_risk_check   — flatten stop-loss breaches, log EOD snapshot

START IN PAPER MODE ONLY. The PAPER_MODE env var defaults to True everywhere.
Run for 10+ clean trading days before evaluating live.

Usage:
    cd gesh-project-trading
    source venv/bin/activate
    python -m trading.autopilot            # blocking, runs the scheduler

    # Or to test one job immediately:
    python -m trading.autopilot --job pre_market_scan
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# APScheduler is the only new dependency — add to requirements if not present.
# pip install apscheduler
try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    _HAS_APSCHEDULER = True
except ImportError:
    _HAS_APSCHEDULER = False

from .config import load_risk_config
from .contract import from_consensus, TradeIntent
from .risk_gate import evaluate
from .execution import place
from .state import (
    active_stops,
    get_marker,
    init_trading_tables,
    paper_open_positions,
    recent_intents,
    set_marker,
)
from .alerts import (
    alert_autopilot_start, alert_autopilot_stop,
    alert_scan_summary, alert_error, alert_daily_pnl,
    alert_auth_failure,
)


# ─── Robinhood session management ─────────────────────────────────────────────

def _ensure_logged_in() -> bool:
    """
    Ensure a valid Robinhood session exists before any data fetch.
    Uses stored pickle session first (no MFA needed if session is fresh).
    Returns True if logged in, False if auth failed.
    """
    try:
        from auth import login  # noqa: PLC0415
        login()
        logger.info("Robinhood session active")
        return True
    except Exception as e:
        msg = str(e)
        logger.error("Robinhood auth failed: %s", msg)
        alert_auth_failure(msg)
        return False

logger = logging.getLogger("autopilot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] autopilot — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# ─── Lazy imports (avoid hard-dep at module level) ────────────────────────────

def _load_consensus():
    """Import consensus lazily so autopilot can be imported without yfinance."""
    from consensus import run_consensus_holding  # noqa: PLC0415
    return run_consensus_holding


def _load_data_fetcher():
    """Import data fetcher lazily."""
    from data_fetcher import build_data_payload  # noqa: PLC0415
    return build_data_payload


def _consensus_args(sym: str, snapshot: dict[str, Any]) -> dict[str, float]:
    """
    Derive run_consensus_holding() arguments for one symbol from the live snapshot.

    run_consensus_holding(symbol, equity, total_portfolio, avg_cost, current_price):
      - equity / avg_cost come from the held position if `sym` is currently held,
        otherwise default to 0 (watchlist candidate not yet held).
      - total_portfolio is the whole-account equity.
      - current_price falls back to the watchlist quote when the symbol is unheld.
    """
    total_portfolio = float(
        snapshot.get("equity_summary", {}).get("portfolio_equity", 0) or 0
    ) or 1.0

    holding = next(
        (h for h in snapshot.get("holdings", []) if h.get("symbol") == sym),
        None,
    )
    if holding is not None:
        return {
            "equity": float(holding.get("equity", 0) or 0),
            "total_portfolio": total_portfolio,
            "avg_cost": float(holding.get("average_buy_price", 0) or 0),
            "current_price": float(holding.get("current_price", 0) or 0),
        }

    quote = next(
        (w for w in snapshot.get("watchlist", []) if w.get("symbol") == sym),
        None,
    )
    current_price = float((quote or {}).get("last_trade_price", 0) or 0)
    return {
        "equity": 0.0,
        "total_portfolio": total_portfolio,
        "avg_cost": 0.0,
        "current_price": current_price,
    }


# ─── Candidate storage (in-memory between pre-market and open-window) ─────────
# Keyed by symbol → (TradeIntent, consensus_row). Persisted to a job marker so a
# process restart between 08:30 and 09:35 no longer silently trades nothing.
_CANDIDATES: dict[str, tuple[TradeIntent | None, dict]] = {}

_CANDIDATE_MARKER = "premarket_candidates"


def _et_today() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _persist_candidates() -> None:
    """Write today's candidate symbols to the DB (restart-safe handoff)."""
    try:
        set_marker(_CANDIDATE_MARKER, json.dumps({
            "date": _et_today(),
            "symbols": sorted(_CANDIDATES),
        }))
    except Exception as e:  # noqa: BLE001 — persistence is best-effort
        logger.warning("pre_market_scan: could not persist candidates: %s", e)


def _restore_candidates() -> None:
    """Reload today's pre-market candidates after a process restart.

    Only the symbols matter — open_window re-runs consensus fresh anyway, so a
    placeholder (None, {}) tuple is enough. Stale (non-today) markers are ignored.
    """
    try:
        raw = get_marker(_CANDIDATE_MARKER)
        if not raw:
            return
        data = json.loads(raw)
        if data.get("date") != _et_today():
            return
        for sym in data.get("symbols", []):
            _CANDIDATES.setdefault(sym, (None, {}))
        if _CANDIDATES:
            logger.info(
                "open_window: restored %d candidates from marker after restart",
                len(_CANDIDATES),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("open_window: candidate restore failed: %s", e)


# ─── Job implementations ──────────────────────────────────────────────────────

def pre_market_scan() -> None:
    """
    08:30 ET — Run consensus over the watchlist. Store top BUY/SELL candidates
    in memory for the open_window job to execute 65 minutes later.
    """
    logger.info("pre_market_scan: starting")
    cfg = load_risk_config()

    try:
        build_data_payload = _load_data_fetcher()
        run_consensus_holding = _load_consensus()
    except ImportError as e:
        logger.error("pre_market_scan: import failure — %s", e)
        return

    if not _ensure_logged_in():
        return

    try:
        snapshot = build_data_payload([])      # holdings + watchlist
    except Exception as e:
        logger.error("pre_market_scan: could not build data payload — %s", e)
        alert_error("pre_market_scan", str(e))
        return

    watchlist = snapshot.get("watchlist", [])
    symbols = [w["symbol"] for w in watchlist if w.get("symbol")]
    if not symbols:
        logger.warning("pre_market_scan: no watchlist symbols found")
        return

    logger.info("pre_market_scan: scanning %d symbols: %s", len(symbols), symbols)

    _CANDIDATES.clear()
    for sym in symbols:
        try:
            row = run_consensus_holding(sym, **_consensus_args(sym, snapshot))
            intent = from_consensus(row, max_position_pct=cfg.max_position_pct)
            if intent.action in ("BUY", "SELL") and intent.confidence >= cfg.min_confidence:
                # P2 entry-conviction floor (ported from the engine repo): gate NEW
                # long entries on the consensus raw_score — confidence is data
                # coverage (≈ nodes-voted/9), so alone it let a near-coin-flip
                # weighted score through. SELL is a managed exit — never blocked.
                raw_score = float(row.get("raw_score", row.get("score", 0)) or 0)
                if intent.action == "BUY" and raw_score < cfg.min_entry_score:
                    logger.info(
                        "  ⊘ %s → BUY skipped: raw_score=%.2f < min_entry_score=%.2f (low conviction)",
                        sym, raw_score, cfg.min_entry_score,
                    )
                    continue
                _CANDIDATES[sym] = (intent, row)
                logger.info(
                    "  ✓ %s → %s conf=%.2f raw=%.2f verdict=%s",
                    sym, intent.action, intent.confidence, raw_score, row.get("verdict"),
                )
        except Exception as e:
            logger.warning("  ✗ %s failed: %s", sym, e)

    _persist_candidates()
    logger.info(
        "pre_market_scan: complete — %d actionable candidates stored",
        len(_CANDIDATES),
    )
    alert_scan_summary(
        symbols_scanned=len(symbols),
        candidates={sym: intent.action for sym, (intent, _) in _CANDIDATES.items()},
    )


def open_window() -> None:
    """
    09:35 ET — Execute (or paper-simulate) all candidates from pre_market_scan.
    Each intent is pushed through the full risk gate before any order is placed.
    """
    if not _CANDIDATES:
        _restore_candidates()   # in-memory dict is empty after a restart
    logger.info("open_window: starting — %d candidates queued", len(_CANDIDATES))
    if not _CANDIDATES:
        logger.info("open_window: no candidates — skipping")
        return

    cfg = load_risk_config()
    try:
        build_data_payload = _load_data_fetcher()
        run_consensus_holding = _load_consensus()
    except ImportError as e:
        logger.error("open_window: import failure — %s", e)
        return

    if not _ensure_logged_in():
        return

    # Refresh snapshot at open, not pre-market
    try:
        snapshot = build_data_payload([])
    except Exception as e:
        logger.error("open_window: could not build snapshot — %s", e)
        return

    approved = rejected = 0
    for sym, (_stale_intent, _row) in list(_CANDIDATES.items()):
        try:
            # Re-run consensus against the fresh open snapshot — the 08:30
            # confidence may be stale by 09:35. Skip if the signal has weakened.
            try:
                fresh_row = run_consensus_holding(sym, **_consensus_args(sym, snapshot))
                intent = from_consensus(fresh_row, max_position_pct=cfg.max_position_pct)
            except Exception as e:
                logger.warning(
                    "  ✗ %s re-consensus failed (%s: %s) — skipping",
                    sym, type(e).__name__, e,
                )
                rejected += 1
                continue
            if intent.action not in ("BUY", "SELL") or intent.confidence < cfg.min_confidence:
                logger.info("  ✗ %s no longer actionable at open (action=%s conf=%.2f) — skipping",
                            sym, intent.action, intent.confidence)
                rejected += 1
                continue
            # Re-apply the P2 entry-conviction floor against the FRESH open
            # snapshot — the 08:30 raw_score may have weakened by 09:35.
            fresh_raw = float(fresh_row.get("raw_score", fresh_row.get("score", 0)) or 0)
            if intent.action == "BUY" and fresh_raw < cfg.min_entry_score:
                logger.info(
                    "  ⊘ %s BUY skipped at open: raw_score=%.2f < min_entry_score=%.2f",
                    sym, fresh_raw, cfg.min_entry_score,
                )
                rejected += 1
                continue
            verdict = evaluate(intent, snapshot, cfg)
            result = place(intent, verdict, snapshot, cfg)
            if verdict.approved:
                approved += 1
                logger.info(
                    "  ✓ %s %s qty=%.4f price=%.2f [%s]",
                    sym, intent.action,
                    result.qty or 0, result.target_price or 0, result.status,
                )
            else:
                rejected += 1
                logger.info("  ✗ %s REJECTED — %s", sym, verdict.reason)
        except Exception as e:
            logger.error("  ✗ %s execution error: %s", sym, e)

    logger.info(
        "open_window: complete — %d approved, %d rejected",
        approved, rejected,
    )
    _CANDIDATES.clear()


def midday_rebalance() -> None:
    """
    12:30 ET — Re-run consensus on currently held positions.
    If a position has drifted >5% from Kelly-optimal, log a TRIM/ADD intent
    and push through the risk gate (paper mode will just simulate it).
    """
    logger.info("midday_rebalance: starting")
    cfg = load_risk_config()

    try:
        build_data_payload = _load_data_fetcher()
        run_consensus_holding = _load_consensus()
    except ImportError as e:
        logger.error("midday_rebalance: import failure — %s", e)
        return

    if not _ensure_logged_in():
        return

    try:
        snapshot = build_data_payload([])
    except Exception as e:
        logger.error("midday_rebalance: could not build snapshot — %s", e)
        return

    holdings = snapshot.get("holdings", [])
    if not holdings:
        logger.info("midday_rebalance: no holdings — skipping")
        return

    equity = float(snapshot.get("equity_summary", {}).get("portfolio_equity", 0) or 0)

    for h in holdings:
        sym = h.get("symbol")
        if not sym:
            continue
        try:
            current_pct = float(h.get("portfolio_percentage", 0) or 0) / 100.0
            row = run_consensus_holding(sym, **_consensus_args(sym, snapshot))
            intent = from_consensus(row, max_position_pct=cfg.max_position_pct)

            # Drift check: current_pct vs target_pct
            target_pct = intent.target_position_pct or 0.0
            drift = abs(current_pct - target_pct)

            if drift > 0.05:   # >5% absolute drift from Kelly-optimal
                logger.info(
                    "  drift %s: current=%.1f%% target=%.1f%% drift=%.1f%% → %s",
                    sym, current_pct * 100, target_pct * 100, drift * 100, intent.action,
                )
                verdict = evaluate(intent, snapshot, cfg)
                place(intent, verdict, snapshot, cfg)
            else:
                logger.debug("  %s drift=%.1f%% within tolerance", sym, drift * 100)

        except Exception as e:
            logger.warning("  %s midday error: %s", sym, e)

    logger.info("midday_rebalance: complete")


def close_risk_check() -> None:
    """
    15:55 ET — End-of-day risk sweep:
      1. Flag any position where current_price < stop_loss (log SELL intent).
      2. Log an EOD snapshot of portfolio equity to the audit trail.
      3. Clear stale candidates.
    """
    logger.info("close_risk_check: starting")
    cfg = load_risk_config()

    try:
        build_data_payload = _load_data_fetcher()
    except ImportError as e:
        logger.error("close_risk_check: import failure — %s", e)
        return

    if not _ensure_logged_in():
        return

    try:
        snapshot = build_data_payload([])
    except Exception as e:
        logger.error("close_risk_check: could not build snapshot — %s", e)
        return

    holdings = snapshot.get("holdings", [])
    equity_summary = snapshot.get("equity_summary", {})
    equity = float(equity_summary.get("portfolio_equity", 0) or 0)
    prev_close = float(equity_summary.get("adjusted_equity_previous_close", 0) or 0)
    daily_pnl = (equity - prev_close) / prev_close if prev_close > 0 else 0.0

    logger.info(
        "close_risk_check: EOD equity=$%.2f daily_pnl=%+.2f%%",
        equity, daily_pnl * 100,
    )
    from .config import load_risk_config as _cfg
    alert_daily_pnl(equity, prev_close, daily_pnl, _cfg().max_daily_loss_pct)

    # Stop-loss sweep — the PAPER book first (positions reconstructed from
    # SIMULATED fills, stops recorded at fill time by execution._sync_paper_stop).
    # The old holdings-field sweep was doubly dead: data_fetcher holdings never
    # carry a stop_loss field, and the SELL intent it built (no
    # target_position_pct) failed validate() at the gate.
    from .contract import manual as _manual

    def _sweep_sell(sym: str, price: float, stop: float, book: str) -> None:
        logger.warning(
            "  STOP-LOSS BREACH (%s): %s price=%.2f < stop=%.2f — logging SELL intent",
            book, sym, price, stop,
        )
        intent = _manual(
            sym, "SELL",
            confidence=1.0,
            target_position_pct=0.0,   # full close — required for a valid SELL intent
            rationale=f"EOD stop-loss breach ({book}): price={price:.2f} stop={stop:.2f}",
            source="stop_sweep",
        )
        verdict = evaluate(intent, snapshot, cfg)
        place(intent, verdict, snapshot, cfg)

    price_by_symbol: dict[str, float] = {
        h.get("symbol"): float(h.get("current_price") or 0) for h in holdings
    }
    for w in snapshot.get("watchlist", []):
        price_by_symbol.setdefault(w.get("symbol"), float(w.get("last_trade_price") or 0))

    if cfg.paper_mode:
        try:
            paper_positions = paper_open_positions()
            stops = active_stops()
        except Exception as e:  # noqa: BLE001 — sweep must not die on a book read
            paper_positions, stops = {}, {}
            logger.error("close_risk_check: paper-book read failed: %s", e)
        for sym in paper_positions:
            stop = float((stops.get(sym) or {}).get("stop_price") or 0)
            price = float(price_by_symbol.get(sym) or 0)
            if stop and price and price < stop:
                _sweep_sell(sym, price, stop, "paper")

    # Legacy real-holdings sweep — LIVE mode only. In paper mode the paper-book sweep
    # above is authoritative; this loop must never run then, so a broker holding that
    # ever DID carry a stop_loss field can't emit a "live"-book intent from a paper run.
    # (Safe today only because RH holdings lack stop_loss + place() honors PAPER_MODE —
    # this makes the paper/live boundary a code invariant, not an accident.)
    if not cfg.paper_mode:
        for h in holdings:
            sym = h.get("symbol")
            price = float(h.get("current_price") or 0)
            stop = float(h.get("stop_loss") or 0)   # if holdings carry stop_loss field
            if stop and price and price < stop:
                _sweep_sell(sym, price, stop, "live")

    # Warn if daily loss cap was approached
    if daily_pnl < -0.008:
        logger.warning(
            "close_risk_check: daily P&L %+.2f%% approaching max loss cap of %.2f%%",
            daily_pnl * 100, cfg.max_daily_loss_pct * 100,
        )

    _CANDIDATES.clear()
    logger.info("close_risk_check: complete")


# ─── Scheduler setup ─────────────────────────────────────────────────────────

def build_scheduler() -> "BlockingScheduler":
    """
    Wire the four jobs onto their ET cron triggers.
    Only the pre_market_scan job is active by default.
    Uncomment the others after 10 clean days of pre-market scan operation.
    """
    if not _HAS_APSCHEDULER:
        raise ImportError(
            "APScheduler is required: pip install apscheduler"
        )

    tz = "America/New_York"
    sched = BlockingScheduler(timezone=tz)

    # Job 1 — always on
    sched.add_job(
        pre_market_scan,
        CronTrigger(hour=8, minute=30, timezone=tz),
        id="pre_market_scan",
        name="Pre-market consensus scan",
        replace_existing=True,
    )

    # Jobs 2-4 — uncomment after 10 clean pre-market days
    sched.add_job(
        open_window,
        CronTrigger(hour=9, minute=35, timezone=tz),
        id="open_window",
        name="Open-window execution",
        replace_existing=True,
    )

    sched.add_job(
        midday_rebalance,
        CronTrigger(hour=12, minute=30, timezone=tz),
        id="midday_rebalance",
        name="Midday drift rebalance",
        replace_existing=True,
    )

    sched.add_job(
        close_risk_check,
        CronTrigger(hour=15, minute=55, timezone=tz),
        id="close_risk_check",
        name="Close risk sweep + EOD snapshot",
        replace_existing=True,
    )

    return sched


# ─── Entry point ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    """
    Run the scheduler (blocking), or invoke a single job immediately with --job.
    """
    init_trading_tables()   # ensure tables exist before any job runs

    parser = argparse.ArgumentParser(description="GESH Autopilot — scheduled trading runner")
    parser.add_argument(
        "--job",
        choices=["pre_market_scan", "open_window", "midday_rebalance", "close_risk_check"],
        help="Run a single job immediately and exit (for manual testing).",
    )
    args = parser.parse_args(argv)

    cfg = load_risk_config()
    logger.info(
        "Autopilot starting — paper_mode=%s require_approval=%s",
        cfg.paper_mode, cfg.require_approval,
    )
    alert_autopilot_start(cfg.paper_mode, ["pre_market_scan", "open_window", "midday_rebalance", "close_risk_check"])

    if args.job:
        job_fn = {
            "pre_market_scan": pre_market_scan,
            "open_window": open_window,
            "midday_rebalance": midday_rebalance,
            "close_risk_check": close_risk_check,
        }[args.job]
        logger.info("Running single job: %s", args.job)
        job_fn()
        logger.info("Job complete. Exiting.")
        return

    if not _HAS_APSCHEDULER:
        logger.error(
            "APScheduler not installed. Run: pip install apscheduler\n"
            "Or test a single job: python -m trading.autopilot --job pre_market_scan"
        )
        sys.exit(1)

    sched = build_scheduler()
    jobs = sched.get_jobs()
    logger.info("Scheduled %d jobs:", len(jobs))
    for j in jobs:
        logger.info("  %s → next run: %s", j.name, j.next_run_time)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Autopilot stopped.")
        alert_autopilot_stop("manual shutdown")


if __name__ == "__main__":
    main()
