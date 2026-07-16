"""Daily signal snapshot + customer flip alerts (launchd: com.gesh.signal-alerts).

Runs after the 10:30 chain. LLM-free: consensus is mechanical (yfinance/RH bars).
Universe = union of all customers' watchlist+book symbols. One email per customer
per day covering ALL their confirmed flips; idempotent via alert_log."""
from __future__ import annotations

import html as _html
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import auth_users
import email_sender
import signal_history

logger = logging.getLogger("signal_alerts")
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
    """Best-effort consensus per symbol (serialized upstream by the yf lock)."""
    import consensus as consensus_mod
    out = []
    for sym in symbols:
        try:
            r = consensus_mod.run_consensus_holding(
                symbol=sym, equity=0, total_portfolio=1, avg_cost=0, current_price=0)
            if r and r.get("verdict"):
                out.append({"symbol": sym, "verdict": r["verdict"],
                            "score": r.get("raw_score"), "confidence": r.get("confidence")})
        except Exception as e:  # noqa: BLE001 — one symbol must not sink the run
            logger.warning("consensus failed for %s: %s", sym, e)
    return out


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
            f"<a href='{base}/track-record'>our verified track record</a></p>"
            f"<p style='color:#888;font-size:12px'>Informational only — not financial advice. "
            f"<a href='{base}/alerts/unsubscribe?token={tok}'>Unsubscribe from alerts</a></p>")


def main(dry_run: bool = False) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbols = sorted(auth_users.all_customer_symbols())
    snapshots = _consensus_for(symbols)
    signal_history.record_snapshots(snapshots)
    flips = signal_history.detect_confirmed_flips(symbols, persistence_days=PERSISTENCE_DAYS)

    per_customer: dict[str, list[dict]] = {}
    for f in flips:
        for email in auth_users.customers_watching(f["symbol"]):
            per_customer.setdefault(email, []).append(f)

    sent = 0
    with _alert_con() as con:
        for email, their_flips in sorted(per_customer.items()):
            fresh = [f for f in their_flips if not con.execute(
                "SELECT 1 FROM alert_log WHERE email=? AND symbol=? AND date=?",
                (email, f["symbol"], today)).fetchone()]
            if not fresh or dry_run:
                continue
            subject = "GESH: signal change on " + ", ".join(f["symbol"] for f in fresh)
            if email_sender.send_email(email, subject, _build_email(email, fresh)):
                sent += 1
                for f in fresh:
                    con.execute("INSERT OR IGNORE INTO alert_log (email, symbol, date) "
                                "VALUES (?,?,?)", (email, f["symbol"], today))
    return {"symbols_snapshotted": len(snapshots), "flips": len(flips), "emails_sent": sent}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(main(dry_run="--dry-run" in sys.argv)))
