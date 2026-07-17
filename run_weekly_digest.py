"""Weekly customer digest (launchd: com.gesh.weekly-digest, Fri 17:15 local).

Composes ENTIRELY from cached/DB state — signal_history, the customer's book
valued via the shared close cache, their last persisted analysis. No LLM.
Push cadence is the retention lever: research shows retail research subs decay
in ~5 quarters without one (Review of Accounting Studies 2026)."""
from __future__ import annotations

import html as _html
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import auth_users
import email_sender
import job_health
import signal_history

logger = logging.getLogger("weekly_digest")
JOB_NAME = "com.gesh.weekly-digest"   # launchd label — the /usage health key
UNSUB_TTL = 60 * 60 * 24 * 30

# One digest per customer per ISO week — launchd can double-fire around
# sleep/wake, and a duplicate email erodes the exact trust the digest exists to
# build. Mirrors run_signal_alerts' alert_log (same DB, same lazy-DDL pattern).
_DIGEST_DDL = """
CREATE TABLE IF NOT EXISTS digest_log (
    email    TEXT NOT NULL,
    iso_week TEXT NOT NULL,
    PRIMARY KEY (email, iso_week)
);
"""


def _digest_con() -> sqlite3.Connection:
    con = sqlite3.connect(Path(signal_history.DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute(_DIGEST_DDL)
    return con


def _base_url() -> str:
    return os.getenv("APP_BASE_URL", "https://pay.geshfinance.com").rstrip("/")


def _close_for(sym: str) -> float | None:
    """Seam for tests; production imports the server's shared close cache lazily
    (import inside the function so importing this module never drags in server)."""
    from server import _cached_close
    return _cached_close(sym)


def _customers() -> list[str]:
    with auth_users._conn() as con:  # noqa: SLF001 — same-package convention
        rows = con.execute(
            "SELECT DISTINCT s.email FROM ("
            "  SELECT email FROM watchlist UNION SELECT email FROM customer_positions) s "
            "LEFT JOIN users u ON u.email=s.email "
            "WHERE COALESCE(u.digest_opt_out,0)=0 AND COALESCE(u.is_active,1)=1 "
            "ORDER BY s.email").fetchall()
    return [r["email"] for r in rows]


def _compose(email: str) -> str | None:
    e = _html.escape
    positions = auth_users.get_positions(email)
    watch = auth_users.get_watch(email)
    syms = sorted({p["symbol"] for p in positions} | set(watch))
    if not syms:
        return None
    total = sum((p["shares"] * (_close_for(p["symbol"]) or 0)) for p in positions)
    flips = signal_history.detect_confirmed_flips(syms, persistence_days=2)
    flip_rows = "".join(
        f"<tr><td style='padding:4px 10px;font-weight:600'>{e(f['symbol'])}</td>"
        f"<td style='padding:4px 10px'>{e(f['prior_verdict'])} &rarr; <b>{e(f['new_verdict'])}</b></td></tr>"
        for f in flips) or "<tr><td style='padding:4px 10px;color:#666'>No confirmed changes this week.</td></tr>"
    saved = auth_users.get_latest_book_analysis(email)
    verdict = e(str(((saved or {}).get("analysis") or {}).get("portfolio_verdict", "—")))
    tok = auth_users.issue_token(email, ttl=UNSUB_TTL, typ="digest_unsub")
    base = _base_url()
    book_line = (f"<p>Your book: <b>${total:,.0f}</b> across {len(positions)} positions.</p>"
                 if positions else "<p>You are watching " + str(len(watch)) + " symbols.</p>")
    return (f"<h2>Your GESH week — {datetime.now(timezone.utc).strftime('%b %d, %Y')}</h2>"
            f"{book_line}"
            f"<h3>Signal changes on your names</h3><table>{flip_rows}</table>"
            f"<p>Last portfolio verdict: <b>{verdict}</b> &middot; "
            f"<a href='{base}/mybook/report'>your full report</a> &middot; "
            f"<a href='{base}/track-record'>verified track record</a></p>"
            f"<p style='color:#888;font-size:12px'>Informational only — not financial advice. "
            f"<a href='{base}/digest/unsubscribe?token={tok}'>Unsubscribe from the digest</a></p>")


def main(dry_run: bool = False) -> dict:
    sent = 0
    attempted = 0
    customers = _customers()
    week = datetime.now(timezone.utc).strftime("%G-W%V")
    with _digest_con() as con:
        for email in customers:
            try:  # fail-soft per customer — one bad compose/send must not sink the run
                if con.execute("SELECT 1 FROM digest_log WHERE email=? AND iso_week=?",
                               (email, week)).fetchone():
                    continue                      # double-fired job → already delivered
                html = _compose(email)
                if not html or dry_run:           # dry-run never consumes the week
                    continue
                attempted += 1
                if email_sender.send_email(email, "Your GESH weekly digest", html):
                    sent += 1
                    con.execute("INSERT OR IGNORE INTO digest_log (email, iso_week) "
                                "VALUES (?,?)", (email, week))
            except Exception as e:  # noqa: BLE001 — same isolation pattern as run_signal_alerts
                logger.warning("weekly digest failed for %s: %s", email, e)
    result = {"customers": len(customers), "emails_sent": sent}
    # Visibility guard (rule: silent no-op != quiet week) — under launchd, RESEND_API_KEY
    # can be unset even though there were digests to send; make that distinguishable in
    # the JSON summary instead of looking identical to "no customers this week".
    if attempted and not email_sender.is_configured():
        result["email_delivery"] = "DISABLED — RESEND_API_KEY unset"
    return result


def _cli(argv: list[str]) -> None:
    import json

    dry_run = "--dry-run" in argv
    result = main(dry_run=dry_run)
    if not dry_run:  # health ledger for /usage — record_job_run is fail-soft
        job_health.record_job_run(JOB_NAME, result)
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
