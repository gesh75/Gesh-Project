"""
trading/alerts.py — Telegram alert system for GESH Financial.

Sends structured alerts to @gesh755bot for:
  - Order fills (paper + live)
  - Gate rejections
  - Daily P&L warnings
  - Auth failures
  - Kill switch triggered
  - Autopilot job start/stop
  - Daily EOD summary

Configuration via .env (never commit real values):
  TELEGRAM_BOT_TOKEN=<your-bot-token>   # from @BotFather
  TELEGRAM_CHAT_ID=<your-chat-id>
  TELEGRAM_ALERTS=true   # set false to silence all alerts

Usage:
    from trading.alerts import alert_order_fill, alert_rejection, alert_daily_pnl
"""

from __future__ import annotations

import os
import logging
import requests
from datetime import datetime, timezone
from typing import Any

# Load .env so the token is available when running as a module directly
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("alerts")

# ─── Config ───────────────────────────────────────────────────────────────────

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
ENABLED    = os.getenv("TELEGRAM_ALERTS", "true").strip().lower() in {"1", "true", "yes"}
# Send URL is built inside _send() at call time so the bot token is never stored
# in a module-level constant that could surface in a traceback or log line.
TIMEOUT    = 8   # seconds


# ─── Core send ────────────────────────────────────────────────────────────────

def _send(text: str, silent: bool = False) -> bool:
    """
    Send a message to the GESH Telegram chat.
    Returns True on success, False on any failure (never raises).
    """
    if not ENABLED:
        return True
    if not BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — alert suppressed")
        return False
    if not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set — alert suppressed")
        return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id":                  CHAT_ID,
                "text":                     text,
                "parse_mode":               "HTML",
                "disable_notification":     silent,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
        if not resp.ok:
            logger.warning("Telegram API error %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception:
        logger.warning("Telegram send failed (network/timeout) — token redacted")
        return False


def _now_et() -> str:
    """Current time as a readable ET string."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
        return now.strftime("%H:%M:%S ET")
    except Exception:
        return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


# ─── Alert types ──────────────────────────────────────────────────────────────

def alert_order_fill(
    symbol: str,
    action: str,
    qty: float | None,
    price: float | None,
    mode: str,
    status: str,
    confidence: float,
    rationale: str | None = None,
) -> None:
    """Fired when an order is SIMULATED (paper) or FILLED (live)."""
    mode_tag  = "📄 PAPER" if mode == "paper" else "🔴 LIVE"
    icon      = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"
    qty_str   = f"{qty:.4f}" if qty else "—"
    price_str = f"${price:,.2f}" if price else "—"

    lines = [
        f"{icon} <b>{mode_tag} {action} {symbol}</b>",
        f"Status    : {status}",
        f"Qty       : {qty_str}",
        f"Price     : {price_str}",
        f"Confidence: {confidence:.0%}",
        f"Time      : {_now_et()}",
    ]
    if rationale:
        lines.append(f"Reason    : {rationale[:120]}")

    _send("\n".join(lines))


def alert_rejection(
    symbol: str,
    action: str,
    reason: str,
    gates: dict[str, bool],
    confidence: float,
) -> None:
    """Fired when the risk gate rejects an intent."""
    failed = [g for g, passed in gates.items() if not passed]
    lines = [
        f"🚫 <b>REJECTED {action} {symbol}</b>",
        f"Reason    : {reason}",
        f"Failed    : {', '.join(failed) or 'none'}",
        f"Confidence: {confidence:.0%}",
        f"Time      : {_now_et()}",
    ]
    _send("\n".join(lines), silent=True)   # silent — rejections are low-priority


def alert_daily_pnl(
    equity: float,
    prev_close: float,
    pnl_pct: float,
    max_loss_pct: float,
) -> None:
    """Fired at EOD or when P&L approaches the daily loss cap."""
    if pnl_pct >= 0:
        icon = "📈"
        severity = "normal"
    elif pnl_pct > -max_loss_pct * 0.75:
        icon = "📉"
        severity = "warning"
    else:
        icon = "🚨"
        severity = "critical"

    lines = [
        f"{icon} <b>EOD P&L Report</b>",
        f"Equity    : ${equity:,.2f}",
        f"Prev Close: ${prev_close:,.2f}",
        f"Daily P&L : {pnl_pct:+.2%}",
        f"Loss Cap  : {-max_loss_pct:.2%}",
        f"Status    : {severity.upper()}",
        f"Time      : {_now_et()}",
    ]
    _send("\n".join(lines), silent=(severity == "normal"))


def alert_auth_failure(error: str) -> None:
    """Fired when Robinhood authentication fails."""
    lines = [
        "🔐 <b>AUTH FAILURE</b>",
        f"Error : {error[:200]}",
        f"Time  : {_now_et()}",
        "Action: autopilot paused until re-auth",
    ]
    _send("\n".join(lines))


def alert_kill_switch(path: str) -> None:
    """Fired when the kill switch file is detected."""
    lines = [
        "⛔ <b>KILL SWITCH ACTIVE</b>",
        f"File  : {path}",
        f"Time  : {_now_et()}",
        "All orders blocked until file is removed.",
    ]
    _send("\n".join(lines))


def alert_autopilot_start(paper_mode: bool, jobs: list[str]) -> None:
    """Fired when the autopilot scheduler starts."""
    mode = "PAPER MODE" if paper_mode else "⚠️ LIVE MODE"
    lines = [
        f"🤖 <b>GESH Autopilot Started — {mode}</b>",
        f"Jobs      : {', '.join(jobs)}",
        f"Time      : {_now_et()}",
    ]
    _send("\n".join(lines))


def alert_autopilot_stop(reason: str = "manual") -> None:
    """Fired when the autopilot stops."""
    lines = [
        "🛑 <b>GESH Autopilot Stopped</b>",
        f"Reason: {reason}",
        f"Time  : {_now_et()}",
    ]
    _send("\n".join(lines))


def alert_scan_summary(
    symbols_scanned: int,
    candidates: dict[str, str],   # symbol → action
) -> None:
    """Fired after pre_market_scan with a summary of candidates."""
    if not candidates:
        lines = [
            "🔍 <b>Pre-Market Scan Complete</b>",
            f"Scanned: {symbols_scanned} symbols",
            "Result : no actionable candidates",
            f"Time   : {_now_et()}",
        ]
    else:
        cand_lines = "\n".join(
            f"  {'🟢' if a == 'BUY' else '🔴'} {sym} → {a}"
            for sym, a in candidates.items()
        )
        lines = [
            "🔍 <b>Pre-Market Scan Complete</b>",
            f"Scanned   : {symbols_scanned} symbols",
            f"Candidates: {len(candidates)}",
            cand_lines,
            f"Time      : {_now_et()}",
        ]
    _send("\n".join(lines))


def alert_error(context: str, error: str) -> None:
    """Generic error alert for unexpected exceptions in any job."""
    lines = [
        f"⚠️ <b>GESH Error — {context}</b>",
        f"Error : {str(error)[:300]}",
        f"Time  : {_now_et()}",
    ]
    _send("\n".join(lines))


# ─── Test / verify ────────────────────────────────────────────────────────────

def send_test_message() -> bool:
    """Send a test message to verify the bot is working."""
    return _send(
        "✅ <b>GESH Financial — Bot Connected</b>\n"
        "Telegram alerts are active.\n"
        f"Time: {_now_et()}"
    )


if __name__ == "__main__":
    print("Sending test message to @gesh755bot ...")
    ok = send_test_message()
    print("✅ Sent successfully!" if ok else "❌ Failed — check BOT_TOKEN and CHAT_ID")
