"""Daily signal snapshot + customer flip alerts (launchd: com.gesh.signal-alerts).

Runs after the 10:30 chain. LLM-free: consensus is mechanical (yfinance/RH bars).
Universe = union of all customers' watchlist+book symbols. One email per customer
per day covering ALL their confirmed flips; idempotent via alert_log."""
from __future__ import annotations

import html as _html
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import auth_users
import email_sender
import job_health
import signal_history

logger = logging.getLogger("signal_alerts")
JOB_NAME = "com.gesh.signal-alerts"   # launchd label — the /usage health key
PERSISTENCE_DAYS = 2
UNSUB_TTL = 60 * 60 * 24 * 30   # 30-day unsubscribe links


def _base_url() -> str:
    return os.getenv("APP_BASE_URL", "https://pay.geshfinance.com").rstrip("/")


_ALERT_DDL = """
CREATE TABLE IF NOT EXISTS alert_log (
    email  TEXT NOT NULL,
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    PRIMARY KEY (email, symbol, date)
);
"""


def _alert_con() -> sqlite3.Connection:
    con = sqlite3.connect(Path(signal_history.DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute(_ALERT_DDL)
    return con


def _consensus_for(symbols: list[str]) -> list[dict]:
    """Best-effort consensus per symbol (serialized upstream by the yf lock).

    MUST pass allow_ecg=False. This job snapshots the union of every customer's
    watchlist+book into the shared `signal_history` table, which then feeds
    GET /api/signals/history/{symbol}, flip emails, and the weekly digest.
    node_ecg reads `equity_snapshots` (symbol-keyed, no user column) — the
    OWNER's curve. With the default True, a customer holding a name the owner
    also holds (NVDA, HOOD, …) received a verdict that voted the owner's
    drawdown. Same leak class as GET /api/consensus/{symbol} (1ad42d7).
    """
    import consensus as consensus_mod
    out = []
    for sym in symbols:
        try:
            r = consensus_mod.run_consensus_holding(
                symbol=sym, equity=0, total_portfolio=1, avg_cost=0, current_price=0,
                allow_ecg=False,
            )
            if r and r.get("verdict"):
                out.append({"symbol": sym, "verdict": r["verdict"],
                            "score": r.get("raw_score"), "confidence": r.get("confidence")})
        except Exception as e:  # noqa: BLE001 — one symbol must not sink the run
            logger.warning("consensus failed for %s: %s", sym, e)
    return out


def _unsub_headers(email: str) -> dict:
    """RFC 8058 one-click unsubscribe headers. Mail providers POST to the URL
    directly (Gmail/Yahoo list-unsubscribe buttons), which pairs with the
    scanner-safe contract: GET renders a confirmation page, only POST mutates."""
    tok = auth_users.issue_token(email, ttl=UNSUB_TTL, typ="alerts_unsub")
    url = f"{_base_url()}/alerts/unsubscribe?token={tok}"
    return {"List-Unsubscribe": f"<{url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def _build_email(email: str, flips: list[dict]) -> str:
    e = _html.escape
    rows = "".join(
        f"<tr><td style='padding:6px 12px;font-weight:600'>{e(f['symbol'])}</td>"
        f"<td style='padding:6px 12px'>{e(f['prior_verdict'])} &rarr; "
        f"<b>{e(f['new_verdict'])}</b></td>"
        f"<td style='padding:6px 12px;color:#666'>held {PERSISTENCE_DAYS}+ days "
        f"(since {e(f['confirmed_since'])})</td></tr>" for f in flips)
    tok = auth_users.issue_token(email, ttl=UNSUB_TTL, typ="alerts_unsub")
    base = _base_url()
    return (f"<h2>GESH signal change{'s' if len(flips) > 1 else ''} on your holdings</h2>"
            f"<table>{rows}</table>"
            f"<p><a href='{base}/'>Open your dashboard</a> &middot; "
            f"<a href='{base}/track-record'>our simulated paper track record</a></p>"
            f"<p style='color:#888;font-size:12px'>Informational only — not financial advice. "
            f"<a href='{base}/alerts/unsubscribe?token={tok}'>Unsubscribe from alerts</a></p>")


RETRY_WINDOW_DAYS = 5   # deliver any still-unalerted flip confirmed in the last N days


def main(dry_run: bool = False) -> dict:
    symbols = sorted(auth_users.all_customer_symbols())
    snapshots = _consensus_for(symbols)
    signal_history.record_snapshots(snapshots)
    # Durable events (Codex P1): flips are PERSISTED at detection time, and delivery
    # reads the event table. alert_log keys on the flip's confirmed_date (not "today"),
    # and a row is only written after a SUCCESSFUL send — so a failed send simply
    # leaves the event undelivered and the next run retries it, instead of the flip
    # scrolling past the streak boundary and being lost forever.
    signal_history.record_confirmed_flips(symbols, persistence_days=PERSISTENCE_DAYS)
    since = (datetime.now(timezone.utc) - timedelta(days=RETRY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    flips = signal_history.flips_since(symbols, since)

    per_customer: dict[str, list[dict]] = {}
    for f in flips:
        for email in auth_users.customers_watching(f["symbol"]):
            per_customer.setdefault(email, []).append(f)

    sent = 0
    attempted = 0
    with _alert_con() as con:
        for email, their_flips in sorted(per_customer.items()):
            fresh = [f for f in their_flips if not con.execute(
                "SELECT 1 FROM alert_log WHERE email=? AND symbol=? AND date=?",
                (email, f["symbol"], f["confirmed_since"])).fetchone()]
            if not fresh or dry_run:
                continue
            attempted += 1
            subject = "GESH: signal change on " + ", ".join(f["symbol"] for f in fresh)
            if email_sender.send_email(email, subject, _build_email(email, fresh),
                                       headers=_unsub_headers(email)):
                sent += 1
                for f in fresh:
                    con.execute("INSERT OR IGNORE INTO alert_log (email, symbol, date) "
                                "VALUES (?,?,?)", (email, f["symbol"], f["confirmed_since"]))
    result = {"symbols_snapshotted": len(snapshots), "flips": len(flips), "emails_sent": sent}
    # Visibility guard (rule: silent no-op != quiet day) — under launchd, RESEND_API_KEY
    # can be unset even though there were emails to send; make that distinguishable in
    # the JSON summary instead of looking identical to "no flips today".
    if attempted and not email_sender.is_configured():
        result["email_delivery"] = "DISABLED — RESEND_API_KEY unset"
    return result


def _cli(argv: list[str]) -> None:
    import json

    dry_run = "--dry-run" in argv
    # ET schedule gate (Codex P1): launchd now just ticks this script every 30 min;
    # the run/skip decision is made HERE, explicitly in America/New_York, with
    # catch-up (a post-wake tick still runs if today's window is open and unrun).
    # --force bypasses for manual/diagnostic runs; --dry-run never consumes the day.
    if "--force" not in argv and not dry_run:
        import schedule_gate
        ok, reason = schedule_gate.should_run("alerts")
        if not ok:
            print(json.dumps({"skipped": reason}))
            return
    result = main(dry_run=dry_run)
    if not dry_run:  # health ledger for /usage — record_job_run is fail-soft
        job_health.record_job_run(JOB_NAME, result)
        if "--force" not in argv:
            import schedule_gate
            schedule_gate.mark_ran("alerts")   # only after a non-raising run
    print(json.dumps(result))


if __name__ == "__main__":
    import sys

    # launchd does not inherit the shell env, so RESEND_API_KEY/EMAIL_FROM/APP_BASE_URL
    # are unset unless loaded here. Same guarded convention as run_engines2.py/run_ai2.py
    # — deliberately NOT at module level, so importing this module in tests never leaks
    # a real .env (see tests/test_watch_endpoint.py for the class of bug that guards).
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / '.env')
    except Exception:
        pass

    _cli(sys.argv[1:])
