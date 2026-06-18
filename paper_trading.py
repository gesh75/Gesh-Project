"""
paper_trading.py — Paper Trading Simulation Engine

Mirrors the full GESH action plan in simulation mode:
  - Takes the same BUY/TRIM/SELL actions the daily analysis recommends
  - Executes against paper_trades table (no real money moves)
  - Tracks simulated P&L vs real portfolio performance
  - Auto-closes positions when price hits stop or target

Designed to:
  1. Test new signal configs risk-free before going live
  2. Validate scanner modes (1HR, 5MIN, premarket) before auto_trade=True
  3. Compare paper win rate vs real win rate in weekly review

Usage:
    from paper_trading import run_paper_session, get_paper_summary, close_paper_position

    # Feed action plan from daily analysis:
    run_paper_session(action_plan, holdings, total_portfolio)
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)

from trade_log import log_trade, close_trade, get_trade_history, get_win_rate
from allocation_config import load_config, validate_action


# ─── Risk defaults for paper trades ──────────────────────────────────────────

DEFAULT_STOP_PCT   = 0.05   # 5% stop loss below entry
DEFAULT_TARGET_PCT = 0.12   # 12% take-profit above entry


# ─── Core: execute paper action plan ─────────────────────────────────────────

def run_paper_session(
    action_plan: list[dict],
    holdings: list[dict],
    total_portfolio: float,
    scanner_source: str = "daily-analysis",
) -> list[dict]:
    """
    Convert an action plan into paper trade entries.

    Args:
        action_plan: List of action dicts from daily analysis.
                     Each must have: symbol, action, shares, current_price,
                     signal_reason (optional)
        holdings:    Current holdings list (for open position count)
        total_portfolio: Total portfolio value (for context logging)
        scanner_source:  Label for this session's signal source

    Returns:
        List of created paper trade records (with ids)
    """
    cfg = load_config()
    open_positions = len([h for h in holdings if float(h.get("equity", 0)) > 0])
    created = []

    for item in action_plan:
        symbol = item.get("symbol", "").upper()
        action = item.get("action", "").upper()
        shares = float(item.get("shares", 0))
        price  = float(item.get("current_price", 0))

        # Only paper-trade executable actions
        if action not in ("BUY", "ADD", "TRIM", "SELL"):
            continue
        if shares <= 0 or price <= 0:
            continue

        trade_usd = shares * price

        # Apply allocation caps
        ok, reason = validate_action(
            action, symbol, trade_usd,
            open_positions=open_positions,
            is_options=False,
        )

        # In paper mode we skip the auto_trade gate (paper always runs)
        if not ok and "Auto-trade" not in reason:
            created.append({
                "symbol": symbol, "action": action,
                "blocked": True, "reason": reason
            })
            continue

        # Calculate stop/target from config or defaults
        if action in ("BUY", "ADD"):
            stop   = round(price * (1 - DEFAULT_STOP_PCT),   2)
            target = round(price * (1 + DEFAULT_TARGET_PCT), 2)
        else:  # TRIM/SELL
            stop   = round(price * (1 + DEFAULT_STOP_PCT),   2)
            target = round(price * (1 - DEFAULT_TARGET_PCT), 2)

        signal_reason = item.get("signal_reason") or item.get("reason", "")

        trade_id = log_trade(
            symbol=symbol,
            action=action,
            shares=shares,
            entry_price=price,
            stop_loss=stop,
            target_price=target,
            scanner_source=scanner_source,
            signal_reason=signal_reason,
            notes=f"Paper trade | Portfolio: ${total_portfolio:,.0f}",
            is_paper=True,
        )

        if action in ("BUY", "ADD"):
            open_positions += 1

        created.append({
            "trade_id":     trade_id,
            "symbol":       symbol,
            "action":       action,
            "shares":       shares,
            "entry_price":  price,
            "stop_loss":    stop,
            "target_price": target,
            "trade_usd":    round(trade_usd, 2),
            "signal_reason": signal_reason,
            "blocked":      False,
        })

    return created


# ─── Auto-close: check stops and targets ─────────────────────────────────────

def check_and_close_paper_positions() -> list[dict]:
    """
    Fetch current prices for all open paper trades.
    Auto-close positions that have hit stop loss or target price.

    Returns list of closed trade records.
    """
    open_trades = get_trade_history(open_only=True, is_paper=True)
    if not open_trades:
        return []

    symbols = list({t["symbol"] for t in open_trades})
    prices  = _fetch_current_prices(symbols)
    closed  = []

    for trade in open_trades:
        sym   = trade["symbol"]
        price = prices.get(sym)
        if price is None:
            continue

        action     = trade["action"]
        stop       = trade.get("stop_loss")
        target     = trade.get("target_price")
        trade_id   = trade["id"]

        hit_stop   = False
        hit_target = False

        if action in ("BUY", "ADD"):
            if stop   and price <= stop:   hit_stop   = True
            if target and price >= target: hit_target = True
        else:  # TRIM/SELL
            if stop   and price >= stop:   hit_stop   = True
            if target and price <= target: hit_target = True

        if hit_stop or hit_target:
            note = "Target hit" if hit_target else "Stop hit"
            result = close_trade(trade_id, exit_price=price, notes=note, is_paper=True)
            closed.append(result)

    return closed


def _fetch_current_prices(symbols: list[str]) -> dict[str, float]:
    """Batch fetch latest prices for a list of symbols."""
    prices = {}
    try:
        if not symbols:
            return prices
        tickers = yf.download(
            symbols, period="1d", interval="1m",
            auto_adjust=True, progress=False, group_by="ticker"
        )
        for sym in symbols:
            try:
                if len(symbols) == 1:
                    close = tickers["Close"].dropna()
                else:
                    close = tickers[sym]["Close"].dropna()
                if not close.empty:
                    prices[sym] = float(close.iloc[-1])
            except Exception as e:
                # Visibility: a symbol whose price we couldn't fetch has its
                # stop/target check silently skipped this cycle — log it.
                logger.warning("paper_trading: price fetch failed for %s: %s", sym, e)
    except Exception as e:
        logger.warning("paper_trading: batch price fetch failed for %s: %s", symbols, e)
    missing = [s for s in symbols if s not in prices]
    if missing:
        logger.warning("paper_trading: no price (stop/target NOT checked) for: %s", missing)
    return prices


# ─── Manual close ─────────────────────────────────────────────────────────────

def close_paper_position(trade_id: int, exit_price: float, notes: str = "") -> dict:
    """Manually close a specific paper trade."""
    return close_trade(trade_id, exit_price=exit_price, notes=notes, is_paper=True)


# ─── Summary for weekly review ────────────────────────────────────────────────

def get_paper_summary(days: int = 7) -> dict:
    """
    Full paper trading performance summary for the last N days.
    Suitable for injection into weekly review report.
    """
    stats       = get_win_rate(days=days, is_paper=True)
    open_trades = get_trade_history(open_only=True, is_paper=True, limit=100)
    real_stats  = get_win_rate(days=days, is_paper=False)

    # Compare paper vs real
    paper_win = stats["win_rate_pct"]
    real_win  = real_stats["win_rate_pct"]
    edge      = round(paper_win - real_win, 1)

    return {
        "paper_stats":   stats,
        "real_stats":    real_stats,
        "paper_edge":    edge,          # positive = paper doing better than live
        "open_trades":   open_trades,
        "open_count":    len(open_trades),
        "period_days":   days,
        "verdict": (
            "Paper outperforming live — consider scaling live allocation"
            if edge > 5 else
            "Paper underperforming live — do not increase auto-trade"
            if edge < -5 else
            "Paper and live performing similarly"
        ),
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Paper Trading Engine — Status Check")
    print("=" * 40)

    # Auto-close any hits
    closed = check_and_close_paper_positions()
    if closed:
        print(f"Auto-closed {len(closed)} positions:")
        for t in closed:
            print(f"  {t['symbol']} → {t['outcome']} | P&L: ${t['realized_pnl']:.2f} ({t['pnl_pct']:.1f}%)")
    else:
        print("No positions hit stop/target.")

    summary = get_paper_summary(days=30)
    ps = summary["paper_stats"]
    print(f"\nPaper (30d): {ps['win_rate_pct']}% win | {ps['total_trades']} trades | P&L: ${ps['total_realized_pnl']:.2f}")
    print(f"Edge vs live: {summary['paper_edge']:+.1f}%")
    print(f"Verdict: {summary['verdict']}")
