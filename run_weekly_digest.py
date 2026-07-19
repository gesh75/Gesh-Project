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
from datetime import datetime, timedelta, timezone
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
    """Seam for tests; production prices via the standalone price_cache module —
    NEVER via server, whose import drags the whole FastAPI app + billing +
    trading into this cron process (an unrelated import-time failure there used
    to silently zero customers' book values)."""
    import price_cache
    return price_cache._cached_close(sym)


def _customers() -> list[str]:
    with auth_users._conn() as con:  # noqa: SLF001 — same-package convention
        # [CLAUDE 2026-07-18] immutable-user-ID migration: child tables key on user_id.
        rows = con.execute(
            "SELECT DISTINCT u.email FROM ("
            "  SELECT user_id FROM watchlist UNION SELECT user_id FROM customer_positions) s "
            "JOIN users u ON u.user_id=s.user_id "
            "WHERE COALESCE(u.digest_opt_out,0)=0 AND COALESCE(u.is_active,1)=1 "
            "ORDER BY u.email").fetchall()
    return [r["email"] for r in rows]


def _week_window_start() -> str:
    """Start (YYYY-MM-DD, inclusive) of the digest's flip window: the trailing
    7 days. Covers the market week whether the digest fires Friday evening or
    catches up over the weekend — 'this week' stays honest either way."""
    return (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")


def _valid_close(c) -> bool:
    """A usable price is a REAL, finite, strictly-positive number. `is not None`
    is not enough (Codex re-review): 0.0, NaN, +/-inf, a negative, or a bool all
    pass a None-check and would render a false/garbage book value. Math-free NaN
    test: NaN != NaN."""
    return (isinstance(c, (int, float)) and not isinstance(c, bool)
            and c == c and c not in (float("inf"), float("-inf")) and c > 0)


def _book_line(email: str, positions: list, watch: list) -> str:
    """The dollar line, honesty-guarded (Codex P1): a missing/zero/NaN quote must
    NEVER silently render as $0, and equal-weight (privacy) books have placeholder
    shares whose dollars are not real — say so instead of inventing a number."""
    if not positions:
        return "<p>You are watching " + str(len(watch)) + " symbols.</p>"
    if auth_users.get_equal_weight(email):
        return (f"<p>Your book: {len(positions)} positions "
                f"(private-size mode — dollar totals are not shown).</p>")
    closes = {p["symbol"]: _close_for(p["symbol"]) for p in positions}
    priced = [p for p in positions if _valid_close(closes[p["symbol"]])]
    if len(priced) < len(positions):
        return (f"<p>Your book: {len(positions)} positions. Value unavailable this week "
                f"— we could price only {len(priced)} of {len(positions)} holdings and "
                f"won't show a partial total.</p>")
    total = sum(p["shares"] * closes[p["symbol"]] for p in positions)
    return f"<p>Your book: <b>${total:,.0f}</b> across {len(positions)} positions.</p>"


def _compose(email: str) -> str | None:
    e = _html.escape
    positions = auth_users.get_positions(email)
    watch = auth_users.get_watch(email)
    syms = sorted({p["symbol"] for p in positions} | set(watch))
    if not syms:
        return None
    # One batched warm instead of N cold serial fetches (the batch warmer holds
    # the yf lock once); _close_for stays the per-symbol cached read + test seam.
    try:
        import price_cache
        price_cache.warm_close_cache_batch([p["symbol"] for p in positions])
    except Exception as warm_err:  # noqa: BLE001 — warm is an optimization, never fatal
        logger.warning("batch close warm failed: %s", warm_err)
    book_line = _book_line(email, positions, watch)
    # Durable events for the WHOLE week (Codex P1) — the live detector only sees
    # flips at the latest streak boundary, so Friday's compose used to omit
    # anything confirmed Mon-Wed. Record first (idempotent): if the alerts job
    # was down all week, the digest still captures today's boundary flip.
    signal_history.record_confirmed_flips(syms, persistence_days=2)
    flips = signal_history.flips_since(syms, _week_window_start())
    flip_rows = "".join(
        f"<tr><td style='padding:4px 10px;font-weight:600'>{e(f['symbol'])}</td>"
        f"<td style='padding:4px 10px'>{e(f['prior_verdict'])} &rarr; <b>{e(f['new_verdict'])}</b></td>"
        f"<td style='padding:4px 10px;color:#888'>{e(f['confirmed_since'])}</td></tr>"
        for f in flips) or "<tr><td style='padding:4px 10px;color:#666'>No confirmed changes this week.</td></tr>"
    saved = auth_users.get_latest_book_analysis(email)
    verdict = e(str(((saved or {}).get("analysis") or {}).get("portfolio_verdict", "—")))
    # Staleness label (Codex P1): pairing current positions with a verdict computed
    # for a DIFFERENT book silently misleads — compare the analysis' symbol set.
    # Compare (symbol, shares) — not just the symbol SET — so an economically
    # different book (e.g. NVDA 1 → 100 shares, same tickers) is flagged stale
    # too (Codex re-review). Falls back to symbol presence when a legacy saved
    # row has no share count.
    def _book_shape(rows) -> set:
        out = set()
        for p in rows or []:
            sym = str(p.get("symbol") or "")
            if not sym:
                continue
            sh = p.get("shares", p.get("quantity"))
            out.add((sym, round(float(sh), 4)) if isinstance(sh, (int, float))
                    and not isinstance(sh, bool) else (sym, None))
        return out
    saved_shape = _book_shape(((saved or {}).get("book") or {}).get("positions"))
    cur_shape = _book_shape(positions)
    if saved and saved_shape and cur_shape and saved_shape != cur_shape:
        verdict += " <span style='color:#c90'>(from an earlier version of your book — re-run analysis)</span>"
    tok = auth_users.issue_token(email, ttl=UNSUB_TTL, typ="digest_unsub")
    base = _base_url()
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
    failed = 0
    customers = _customers()
    # Dedup on the SAME ET period key the schedule gate uses — NOT UTC (Codex
    # re-review): a Sunday-catch-up run keyed on UTC could write next week's
    # number, then the real Friday run sees it already present and skips silently.
    import schedule_gate
    week = schedule_gate.period_key("digest")
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
                unsub_tok = auth_users.issue_token(email, ttl=UNSUB_TTL, typ="digest_unsub")
                unsub_headers = {
                    "List-Unsubscribe": f"<{_base_url()}/digest/unsubscribe?token={unsub_tok}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
                if email_sender.send_email(email, "Your GESH weekly digest", html,
                                           headers=unsub_headers):
                    sent += 1
                    con.execute("INSERT OR IGNORE INTO digest_log (email, iso_week) "
                                "VALUES (?,?)", (email, week))
                else:
                    failed += 1                   # eligible delivery that did NOT succeed
            except Exception as e:  # noqa: BLE001 — same isolation pattern as run_signal_alerts
                failed += 1
                logger.warning("weekly digest failed for %s: %s", email, e)
    # `failed` gates period-marking in _cli: a week is "done" ONLY when no eligible
    # delivery failed, so the weekend catch-up actually RETRIES the failures
    # (digest_log dedups anyone already delivered).
    result = {"customers": len(customers), "emails_sent": sent, "failed": failed}
    # Visibility guard (rule: silent no-op != quiet week) — under launchd, RESEND_API_KEY
    # can be unset even though there were digests to send; make that distinguishable in
    # the JSON summary instead of looking identical to "no customers this week".
    if attempted and not email_sender.is_configured():
        result["email_delivery"] = "DISABLED — RESEND_API_KEY unset"
    return result


def _cli(argv: list[str]) -> None:
    import json

    dry_run = "--dry-run" in argv
    # ET schedule gate (Codex P1): launchd ticks every 30 min; the decision — Friday
    # 17:15 ET with the weekend as catch-up, once per ISO week — is made here in
    # America/New_York. The old Friday-17:15 *local* calendar trigger fired at
    # 10:15 ET and vanished entirely if the Mac was asleep (it never ran once).
    if "--force" not in argv and not dry_run:
        import schedule_gate
        ok, reason = schedule_gate.should_run("digest")
        if not ok:
            print(json.dumps({"skipped": reason}))
            return
    result = main(dry_run=dry_run)
    if not dry_run:  # health ledger for /usage — record_job_run is fail-soft
        job_health.record_job_run(JOB_NAME, result)
        # Mark the ET period done ONLY when nothing eligible failed to send (Codex
        # re-review): otherwise a False/errored send would consume the week and the
        # weekend catch-up would never retry. digest_log makes the retry idempotent.
        if "--force" not in argv and result.get("failed", 0) == 0:
            import schedule_gate
            schedule_gate.mark_ran("digest")
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
