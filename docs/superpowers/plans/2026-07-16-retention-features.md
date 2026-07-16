# Retention Features (ADD 1–6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the six EXA-research-backed retention features: signal-change alerts, AI-card provenance strip, score-stability smoothing + sparkline, bear-case section, "where does new cash go?" card, and a weekly email digest.

**Architecture:** One new foundation module (`signal_history.py` — a per-symbol daily consensus snapshot table in `gesh_financial.db`) feeds features 1, 3, and 6. Alerts and the digest run as launchd jobs after the 10:30 chain, composing from cached/DB data and sending through the existing `email_sender.py` (Resend). Card features (2, 4, 5) extend `/api/analyze-ticker` output + the audited `build*` renderers in `templates/dashboard.html`. **Zero new LLM spend** except the bear-case fields, which ride the existing single ticker-card call.

**Tech Stack:** Python 3.14 / FastAPI / SQLite (stdlib sqlite3), consensus.py (existing 11-node engine), email_sender.py (Resend HTTP), launchd plists, vanilla JS in dashboard.html.

## Global Constraints

- Never paywall or degrade `/track-record` or signal explainability.
- Cached data is free; nothing here may consume customer LLM quota (`_check_customer_quota`) except the existing ticker-card call.
- All customer-visible HTML built in JS must use `build*`-named functions and escape every `${…}` (the XSS regression guard in `tests/test_dashboard_xss_wiring.py` enforces this).
- All new server-rendered HTML (`email bodies`, report additions) must `html.escape()` every dynamic string.
- New DB tables go through the same VirtioFS-safe convention as `db.py` (cwd-relative path, `GESH_DB_PATH` override honored).
- Emails: fail-soft — a send failure logs and returns False, never raises into a request path (match `email_sender.py` conventions).
- Tokens: any new link-token type must be a NEW `typ=` bound into the HMAC payload (`auth_users.issue_token(email, ttl, typ=...)`) — never reuse `'session'`/`'magic'`.
- Tests first (RED→GREEN), `./venv/bin/python3 -m pytest tests/ -q` green + `ruff check` clean on changed files before every commit. Conventional commits. No attribution trailer.
- Env-var config only, no hardcoded secrets. launchd plists keep absolute paths (machine templates); scripts derive roots from `$(dirname $0)` / `GESH_TRADING_HOME`.

---

### Task 1: `signal_history` foundation — daily per-symbol consensus snapshots

The shared substrate for alerts (T2), sparkline/smoothing (T4), and the digest (T7).

**Files:**
- Create: `signal_history.py`
- Create: `tests/test_signal_history.py`

**Interfaces:**
- Consumes: `db.DB_PATH` (env-overridable, monkeypatchable), `consensus.run_consensus(symbol)` shape `{symbol, verdict, score, confidence, ...}` (as in `engine_results.json['consensus'][SYM]`).
- Produces (used by T2/T4/T7):
  - `record_snapshots(rows: list[dict], date: str | None = None) -> int` — upsert `{symbol, verdict, score, confidence}` rows for a date (default: today UTC `YYYY-MM-DD`). Returns rows written.
  - `get_history(symbol: str, days: int = 30) -> list[dict]` — oldest→newest `[{date, verdict, score, confidence}]`.
  - `detect_confirmed_flips(symbols: list[str], persistence_days: int = 2) -> list[dict]` — `[{symbol, prior_verdict, new_verdict, score, confirmed_since}]`. A flip is CONFIRMED only when the new verdict has held for `persistence_days` consecutive snapshots AND differs from the verdict immediately before that streak (the anti-Danelfin-flapping rule).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_signal_history.py
"""Daily per-symbol consensus snapshots — substrate for alerts/sparkline/digest.

The flip detector implements the anti-flapping rule from the 2026-07 product
research (Danelfin's #1 Trustpilot complaint is day-to-day rating whiplash):
a verdict change only counts once it has persisted N consecutive snapshots.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import signal_history as sh


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")


def _row(sym, verdict, score=0.3, confidence=80.0):
    return {"symbol": sym, "verdict": verdict, "score": score, "confidence": confidence}


def test_record_and_history_roundtrip():
    assert sh.record_snapshots([_row("NVDA", "BUY")], date="2026-07-14") == 1
    sh.record_snapshots([_row("NVDA", "HOLD", score=0.1)], date="2026-07-15")
    hist = sh.get_history("NVDA", days=30)
    assert [h["date"] for h in hist] == ["2026-07-14", "2026-07-15"]  # oldest→newest
    assert hist[-1]["verdict"] == "HOLD" and hist[-1]["score"] == 0.1


def test_record_same_day_upserts_not_duplicates():
    sh.record_snapshots([_row("NVDA", "BUY")], date="2026-07-15")
    sh.record_snapshots([_row("NVDA", "SELL")], date="2026-07-15")
    hist = sh.get_history("NVDA")
    assert len(hist) == 1 and hist[0]["verdict"] == "SELL"


def test_flip_needs_persistence():
    # BUY BUY HOLD — HOLD has held only 1 day → NOT confirmed yet
    sh.record_snapshots([_row("NVDA", "BUY")], date="2026-07-13")
    sh.record_snapshots([_row("NVDA", "BUY")], date="2026-07-14")
    sh.record_snapshots([_row("NVDA", "HOLD")], date="2026-07-15")
    assert sh.detect_confirmed_flips(["NVDA"], persistence_days=2) == []
    # one more HOLD day → confirmed
    sh.record_snapshots([_row("NVDA", "HOLD")], date="2026-07-16")
    flips = sh.detect_confirmed_flips(["NVDA"], persistence_days=2)
    assert len(flips) == 1
    f = flips[0]
    assert f["symbol"] == "NVDA" and f["prior_verdict"] == "BUY" and f["new_verdict"] == "HOLD"
    assert f["confirmed_since"] == "2026-07-15"


def test_stable_verdict_is_not_a_flip():
    for d in ("2026-07-13", "2026-07-14", "2026-07-15"):
        sh.record_snapshots([_row("NVDA", "BUY")], date=d)
    assert sh.detect_confirmed_flips(["NVDA"]) == []


def test_flip_detector_ignores_symbols_with_short_history():
    sh.record_snapshots([_row("NEWSYM", "BUY")], date="2026-07-15")
    assert sh.detect_confirmed_flips(["NEWSYM"]) == []


def test_bad_rows_skipped_not_fatal():
    n = sh.record_snapshots([_row("NVDA", "BUY"), {"symbol": "", "verdict": "X"},
                             {"no": "symbol"}], date="2026-07-15")
    assert n == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python3 -m pytest tests/test_signal_history.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'signal_history'`

- [ ] **Step 3: Implement `signal_history.py`**

```python
"""Daily per-symbol consensus snapshots (signal_history table).

Substrate for customer signal-change alerts, the card sparkline, verdict-flip
smoothing, and the weekly digest. Deliberately tiny and stdlib-only.

Anti-flapping rule (product research 2026-07): a verdict change is only
CONFIRMED once the new verdict has persisted ``persistence_days`` consecutive
snapshots — day-to-day whiplash is the #1 credibility complaint against
score-based competitors, so raw one-day flips are never surfaced.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from db import DB_PATH  # same cwd-relative / GESH_DB_PATH convention

logger = logging.getLogger("signal_history")

_DDL = """
CREATE TABLE IF NOT EXISTS signal_history (
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,          -- YYYY-MM-DD (UTC)
    verdict    TEXT NOT NULL,          -- STRONG BUY|BUY|HOLD|SELL|STRONG SELL
    score      REAL,
    confidence REAL,
    PRIMARY KEY (symbol, date)
);
"""


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(Path(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute(_DDL)
    return con


def record_snapshots(rows: list[dict], date: str | None = None) -> int:
    """Upsert one day's consensus rows. Bad rows are skipped, never fatal."""
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = 0
    with _conn() as con:
        for r in rows:
            sym = str(r.get("symbol") or "").upper().strip()
            verdict = str(r.get("verdict") or "").strip()
            if not sym or not verdict:
                continue
            con.execute(
                "INSERT INTO signal_history (symbol, date, verdict, score, confidence) "
                "VALUES (?,?,?,?,?) ON CONFLICT(symbol, date) DO UPDATE SET "
                "verdict=excluded.verdict, score=excluded.score, "
                "confidence=excluded.confidence",
                (sym, day,
                 verdict,
                 float(r["score"]) if r.get("score") is not None else None,
                 float(r["confidence"]) if r.get("confidence") is not None else None))
            written += 1
    return written


def get_history(symbol: str, days: int = 30) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT date, verdict, score, confidence FROM signal_history "
            "WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (symbol.upper().strip(), max(1, days))).fetchall()
    return [dict(r) for r in reversed(rows)]


def detect_confirmed_flips(symbols: list[str], persistence_days: int = 2) -> list[dict]:
    """Confirmed verdict flips: new verdict held `persistence_days` consecutive
    snapshots AND differs from the verdict just before the streak."""
    flips: list[dict] = []
    for sym in symbols:
        hist = get_history(sym, days=persistence_days + 30)
        if len(hist) < persistence_days + 1:
            continue
        streak = hist[-persistence_days:]
        new_verdict = streak[0]["verdict"]
        if any(h["verdict"] != new_verdict for h in streak):
            continue                      # streak not uniform → not confirmed
        prior = hist[-(persistence_days + 1)]
        if prior["verdict"] == new_verdict:
            continue                      # no change at the streak boundary
        flips.append({"symbol": sym.upper().strip(),
                      "prior_verdict": prior["verdict"],
                      "new_verdict": new_verdict,
                      "score": streak[-1]["score"],
                      "confirmed_since": streak[0]["date"]})
    return flips
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python3 -m pytest tests/test_signal_history.py -q`
Expected: 6 passed

- [ ] **Step 5: Ruff + commit**

```bash
./venv/bin/python3 -m ruff check signal_history.py tests/test_signal_history.py
git add signal_history.py tests/test_signal_history.py
git commit -m "feat(signals): signal_history daily snapshots + persistence-gated flip detector"
```

---

### Task 2: Signal snapshot job + alert dispatch (feature 1: score-change alerts)

**Files:**
- Create: `run_signal_alerts.py` (job: snapshot + dispatch)
- Create: `run_signal_alerts.sh` (launchd wrapper, `$(dirname $0)` convention)
- Create: `~/Library/LaunchAgents/com.gesh.signal-alerts.plist` (11:05 ET Mon–Fri — after the 10:30 chain finishes)
- Modify: `auth_users.py` (add `alerts_opt_out` migration column + `all_customer_symbols()` + `customers_watching(symbol)`)
- Modify: `server.py` (add `GET /alerts/unsubscribe` public route)
- Test: `tests/test_signal_alerts.py`

**Interfaces:**
- Consumes: `signal_history.record_snapshots/detect_confirmed_flips` (T1), `consensus.run_consensus(symbol) -> dict` (existing), `email_sender.send_email(to, subject, html) -> bool`, `auth_users.issue_token(email, ttl, typ="alerts_unsub")` / `verify_token(token, expected_typ="alerts_unsub")`.
- Produces: `run_signal_alerts.main(dry_run: bool = False) -> dict` summary `{symbols_snapshotted, flips, emails_sent}`; `auth_users.set_alerts_opt_out(email, flag)` / `get_alerts_opt_out(email) -> bool`; `auth_users.all_customer_symbols() -> set[str]` (union of every active user's watchlist + book symbols); `auth_users.customers_watching(symbol) -> list[str]` (active, not opted out).

- [ ] **Step 1: Write failing tests** — `tests/test_signal_alerts.py`

```python
"""Signal-change alert job: snapshot the customer symbol universe, then email
each affected customer ONE digest of their confirmed flips. LLM-free."""
from __future__ import annotations

from pathlib import Path

import pytest

import signal_history as sh


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    return _au


@pytest.fixture(autouse=True)
def _sig_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")


def test_all_customer_symbols_unions_watchlist_and_book(au):
    au.add_watch("a@x.com", "NVDA")
    au.set_position("a@x.com", "TSLA", 1)
    au.add_watch("b@x.com", "AMD")
    assert au.all_customer_symbols() == {"NVDA", "TSLA", "AMD"}


def test_customers_watching_respects_opt_out(au):
    au.add_watch("a@x.com", "NVDA")
    au.add_watch("b@x.com", "NVDA")
    au.set_alerts_opt_out("b@x.com", True)
    assert au.customers_watching("NVDA") == ["a@x.com"]


def test_job_snapshots_and_emails_one_digest_per_customer(au, monkeypatch):
    import run_signal_alerts as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    au.add_watch("a@x.com", "NVDA")
    au.add_watch("a@x.com", "AMD")
    # history: NVDA and AMD both confirm a flip today
    for d in ("2026-07-13", "2026-07-14"):
        sh.record_snapshots([{"symbol": s, "verdict": "BUY", "score": 0.4,
                              "confidence": 80} for s in ("NVDA", "AMD")], date=d)
    for d in ("2026-07-15", "2026-07-16"):
        sh.record_snapshots([{"symbol": s, "verdict": "HOLD", "score": 0.1,
                              "confidence": 70} for s in ("NVDA", "AMD")], date=d)
    monkeypatch.setattr(job, "_consensus_for", lambda syms: [], raising=True)  # snapshot already seeded
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append((to, subject, html)) or True)
    summary = job.main()
    assert summary["emails_sent"] == 1                    # ONE digest, not one per symbol
    to, subject, html = sent[0]
    assert to == "a@x.com" and "NVDA" in html and "AMD" in html
    assert "BUY" in html and "HOLD" in html               # prior → new verdict shown
    assert "/alerts/unsubscribe" in html                  # opt-out link present


def test_job_is_idempotent_per_day(au, monkeypatch):
    """Second run the same day must not re-email the same flip."""
    import run_signal_alerts as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    au.add_watch("a@x.com", "NVDA")
    for d in ("2026-07-13", "2026-07-14"):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY"}], date=d)
    for d in ("2026-07-15", "2026-07-16"):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "HOLD"}], date=d)
    monkeypatch.setattr(job, "_consensus_for", lambda syms: [], raising=True)
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append(to) or True)
    job.main()
    job.main()
    assert len(sent) == 1


def test_unsubscribe_route_flips_flag(au, monkeypatch):
    import server
    from starlette.testclient import TestClient
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    c = TestClient(server.app, raise_server_exceptions=False)
    tok = au.issue_token("a@x.com", ttl=86400, typ="alerts_unsub")
    r = c.get(f"/alerts/unsubscribe?token={tok}")
    assert r.status_code == 200 and "unsubscribed" in r.text.lower()
    assert au.get_alerts_opt_out("a@x.com") is True
    # a session token must NOT work here (typed-token discipline)
    sess = au.issue_token("b@x.com", ttl=86400, typ="session")
    assert c.get(f"/alerts/unsubscribe?token={sess}").status_code == 400
```

- [ ] **Step 2: Run to verify failure** — `./venv/bin/python3 -m pytest tests/test_signal_alerts.py -q` → FAIL (missing module/attrs)

- [ ] **Step 3: Implement**

`auth_users.py` — extend `_BILLING_COLUMNS`-style migration (add to the existing `_migrate`):

```python
# in the module constants, next to _BILLING_COLUMNS:
_PREF_COLUMNS = {
    "alerts_opt_out": "INTEGER NOT NULL DEFAULT 0",   # signal-change alert emails
    "digest_opt_out": "INTEGER NOT NULL DEFAULT 0",   # weekly digest emails (Task 7)
}

# inside _migrate(con), after the billing loop:
    for col, decl in _PREF_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")

# new public helpers (bottom of the user-store section):
def set_alerts_opt_out(email: str, flag: bool) -> None:
    em = _norm(email)
    if not em:
        return
    with _conn() as con:
        # users row may not exist for pre-provision alpha emails — upsert-lite
        con.execute("UPDATE users SET alerts_opt_out=? WHERE email=?", (int(flag), em))


def get_alerts_opt_out(email: str) -> bool:
    em = _norm(email)
    with _conn() as con:
        row = con.execute("SELECT alerts_opt_out FROM users WHERE email=?", (em,)).fetchone()
    return bool(row and row["alerts_opt_out"])


def all_customer_symbols() -> set[str]:
    """Union of every user's watchlist + book symbols — the alert/snapshot universe."""
    with _conn() as con:
        w = con.execute("SELECT DISTINCT symbol FROM watchlist").fetchall()
        p = con.execute("SELECT DISTINCT symbol FROM customer_positions").fetchall()
    return {r["symbol"] for r in w} | {r["symbol"] for r in p}


def customers_watching(symbol: str) -> list[str]:
    """Active, non-opted-out emails with `symbol` on watchlist OR book."""
    sym = str(symbol).upper().strip()
    with _conn() as con:
        rows = con.execute(
            "SELECT DISTINCT s.email FROM ("
            "  SELECT email, symbol FROM watchlist"
            "  UNION SELECT email, symbol FROM customer_positions) s "
            "LEFT JOIN users u ON u.email = s.email "
            "WHERE s.symbol=? AND COALESCE(u.alerts_opt_out, 0)=0 "
            "  AND COALESCE(u.is_active, 1)=1 ORDER BY s.email", (sym,)).fetchall()
    return [r["email"] for r in rows]
```

`run_signal_alerts.py`:

```python
"""Daily signal snapshot + customer flip alerts (launchd: com.gesh.signal-alerts).

Runs after the 10:30 chain. LLM-free: consensus is mechanical (yfinance/RH bars).
Universe = union of all customers' watchlist+book symbols. One email per customer
per day covering ALL their confirmed flips; idempotent via alert_log."""
from __future__ import annotations

import html as _html
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import auth_users
import email_sender
import signal_history

logger = logging.getLogger("signal_alerts")
PERSISTENCE_DAYS = 2
UNSUB_TTL = 60 * 60 * 24 * 30   # 30-day unsubscribe links
BASE_URL = "https://pay.geshfinance.com"

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
            r = consensus_mod.run_consensus(sym)
            if r and r.get("verdict"):
                out.append({"symbol": sym, "verdict": r["verdict"],
                            "score": r.get("score"), "confidence": r.get("confidence")})
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
    return (f"<h2>GESH signal change{'s' if len(flips) > 1 else ''} on your holdings</h2>"
            f"<table>{rows}</table>"
            f"<p><a href='{BASE_URL}/'>Open your dashboard</a> &middot; "
            f"<a href='{BASE_URL}/track-record'>our verified track record</a></p>"
            f"<p style='color:#888;font-size:12px'>Informational only — not financial advice. "
            f"<a href='{BASE_URL}/alerts/unsubscribe?token={tok}'>Unsubscribe from alerts</a></p>")


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
```

`server.py` — public unsubscribe route (register the path in `auth_users.PUBLIC_PREFIXES` so the login wall exempts it):

```python
@app.get("/alerts/unsubscribe", response_class=HTMLResponse)
def alerts_unsubscribe(token: str = ""):
    """One-click opt-out from signal-change alert emails. Public (link-in-email),
    gated by a typed 30-day HMAC token — a session/magic token is rejected."""
    email = auth_users.verify_token(token, expected_typ="alerts_unsub")
    if not email:
        raise HTTPException(status_code=400, detail="Invalid or expired unsubscribe link")
    auth_users.set_alerts_opt_out(email, True)
    return HTMLResponse("<h2>Unsubscribed</h2><p>You will no longer receive GESH "
                        "signal-change alerts. You can re-enable them from the dashboard.</p>")
```

`run_signal_alerts.sh` + plist follow the exact pattern of `sweep_onboarding.sh` / `com.gesh.onboarding-sweep.plist` (copy those, change label to `com.gesh.signal-alerts`, program to `run_signal_alerts.py`, `StartCalendarInterval` 11:05 ET Mon–Fri, log `/tmp/gesh_signal_alerts.log`).

- [ ] **Step 4: Run tests** — `./venv/bin/python3 -m pytest tests/test_signal_alerts.py tests/test_auth_users.py -q` → all pass
- [ ] **Step 5: Full-suite + ruff + commit**

```bash
./venv/bin/python3 -m pytest tests/ -q && ./venv/bin/python3 -m ruff check run_signal_alerts.py auth_users.py server.py
git add run_signal_alerts.py run_signal_alerts.sh auth_users.py server.py tests/test_signal_alerts.py
git commit -m "feat(alerts): daily signal snapshot job + persistence-gated customer flip alerts"
```

- [ ] **Step 6: Load the LaunchAgent (deploy step, after merge)**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gesh.signal-alerts.plist
GESH_DRY=1 ./venv/bin/python3 run_signal_alerts.py --dry-run   # smoke: prints JSON summary
```

---

### Task 3: Provenance strip on every AI research card (feature 2)

The API already carries `generated_at`, `data_sources`/`decision_quality` (Codex #6), `price_suspect`. This task renders them as a visible strip and guarantees the fields are always present.

**Files:**
- Modify: `server.py` (analyze-ticker handler: always attach `provenance` block)
- Modify: `templates/dashboard.html` (new `buildProvenanceStrip(card)` renderer, called from the existing card builder)
- Test: `tests/test_analyze_ticker.py` (extend), `tests/test_dashboard_xss_wiring.py` (auto-covers the new builder by its guard)

**Interfaces:**
- Produces: card JSON gains `provenance: {generated_at: str, price_source: str|null, price_as_of: str|null, model: str, suspect: bool, suspect_reason: str|null}`. Frontend `buildProvenanceStrip(card) -> html string` (escapes internally, `build*` naming for the XSS audit).

- [ ] **Step 1: Failing test** (append to `tests/test_analyze_ticker.py`, reusing its existing client/stub fixtures)

```python
def test_card_always_carries_provenance_block(client_with_stubbed_llm):
    server, c = client_with_stubbed_llm          # match this file's existing fixture name
    r = c.post("/api/analyze-ticker", json={"symbol": "NVDA"})
    card = r.json()["data"]
    p = card["provenance"]
    assert p["generated_at"] and p["model"]
    assert p["suspect"] in (True, False)
    assert "price_source" in p and "price_as_of" in p     # keys present even when None
```

- [ ] **Step 2: Verify RED** — `./venv/bin/python3 -m pytest tests/test_analyze_ticker.py -q` → new test FAILS (KeyError `provenance`)

- [ ] **Step 3: Implement.** In the `/api/analyze-ticker` handler (server.py, right after `decision_quality.attach(...)`):

```python
            card["provenance"] = {
                "generated_at": card.get("generated_at"),
                "price_source": pmeta.get("source"),
                "price_as_of": card.get("generated_at"),
                "model": MODEL,
                "suspect": bool(suspect),
                "suspect_reason": reason if suspect else None,
            }
```

In `dashboard.html`, next to the existing card builders:

```javascript
function buildProvenanceStrip(card){
  const p=card.provenance||{};
  const src=p.price_source?daEsc(p.price_source):'unattributed';
  const when=p.generated_at?new Date(p.generated_at).toLocaleString():'—';
  const warn=p.suspect?`<span style="color:var(--red)">&#9888; ${daEsc(p.suspect_reason||'price cross-source suspect')}</span>`:'';
  return `<div class="prov-strip" style="font-size:11px;color:var(--muted);border-top:1px solid var(--border);margin-top:10px;padding-top:6px">
    AI-generated ${daEsc(when)} &middot; model ${daEsc(p.model||'—')} &middot; price feed: ${src} ${warn}
  </div>`;
}
```

Call it at the end of the existing ticker-card builder (`... + buildProvenanceStrip(card)`).

- [ ] **Step 4: GREEN + XSS guard** — `./venv/bin/python3 -m pytest tests/test_analyze_ticker.py tests/test_dashboard_xss_wiring.py -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(analyze): provenance strip on AI research cards (timestamp, model, price feed, suspect flag)"`

---

### Task 4: 30-day consensus sparkline endpoint + card rendering (feature 3)

Flip smoothing itself shipped in T1/T2 (`detect_confirmed_flips`); this task exposes the history and draws it.

**Files:**
- Modify: `server.py` (add `GET /api/signals/history/{symbol}`)
- Modify: `templates/dashboard.html` (`buildSignalSparkline(hist)`; call from ticker card)
- Test: `tests/test_signal_history_endpoint.py`

**Interfaces:**
- Consumes: `signal_history.get_history(symbol, days=30)` (T1).
- Produces: `GET /api/signals/history/{symbol}` → `{status:"ok", data:{symbol, history:[{date, verdict, score, confidence}]}}`; session-gated (`require_customer_or_owner` dependency, same as the ticker card); 5-min `_ticker_get_cached("sighist", sym)` cache; never 500s on empty history (`history: []`).

- [ ] **Step 1: Failing tests**

```python
# tests/test_signal_history_endpoint.py
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import signal_history as sh


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as au
    import server
    monkeypatch.setattr(au, "AUTH_DB_PATH", tmp_path / "auth.db")
    au.init_auth_db()
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return server, c


def test_history_endpoint_returns_series(client):
    server, c = client
    sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY", "score": 0.4,
                          "confidence": 80}], date="2026-07-15")
    r = c.get("/api/signals/history/NVDA")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["symbol"] == "NVDA" and d["history"][0]["verdict"] == "BUY"


def test_history_endpoint_empty_is_ok_not_500(client):
    server, c = client
    r = c.get("/api/signals/history/ZZZZ9")
    assert r.status_code == 200 and r.json()["data"]["history"] == []


def test_history_endpoint_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    assert c.get("/api/signals/history/NVDA").status_code in (401, 403)
```

- [ ] **Step 2: RED** — `./venv/bin/python3 -m pytest tests/test_signal_history_endpoint.py -q` → 404s/failures
- [ ] **Step 3: Implement** (server.py, near the other per-ticker GETs):

```python
@app.get("/api/signals/history/{symbol}", dependencies=[Depends(require_customer_or_owner)])
def signals_history(symbol: str):
    """30-day consensus verdict/score series for the card sparkline. Mechanical
    read of signal_history — no LLM, no quota. Empty history is a normal answer."""
    sym = symbol.upper().strip()
    if not sym.isalnum() or len(sym) > 10:
        raise HTTPException(status_code=400, detail="Bad symbol")
    hit = _ticker_get_cached("sighist", sym)
    if hit:
        return {"status": "ok", "data": hit}
    import signal_history
    data = {"symbol": sym, "history": signal_history.get_history(sym, days=30)}
    _ticker_get_store("sighist", sym, data)
    return {"status": "ok", "data": data}
```

Frontend (`dashboard.html`) — numeric-only inline SVG (no user strings ⇒ XSS-safe by construction, but keep `build*` naming + escape verdict labels):

```javascript
function buildSignalSparkline(d){
  const h=(d&&d.history)||[];
  if(h.length<2) return '';
  const scores=h.map(p=>Number(p.score)||0);
  const min=Math.min(...scores), max=Math.max(...scores), span=(max-min)||1;
  const pts=scores.map((s,i)=>`${(i/(scores.length-1)*120).toFixed(1)},${(28-((s-min)/span)*24).toFixed(1)}`).join(' ');
  const last=h[h.length-1];
  return `<div style="display:flex;align-items:center;gap:8px;margin-top:6px">
    <svg width="120" height="30" viewBox="0 0 120 30"><polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>
    <span style="font-size:11px;color:var(--muted)">30d consensus &middot; now <b>${daEsc(String(last.verdict||''))}</b></span>
  </div>`;
}
```

Wire into the ticker-card renderer: after the verdict badge, `fetch('/api/signals/history/'+sym)` then append `buildSignalSparkline`.

- [ ] **Step 4: GREEN** — endpoint tests + `tests/test_dashboard_xss_wiring.py`
- [ ] **Step 5: Commit** — `git commit -m "feat(signals): 30-day consensus history endpoint + card sparkline"`

---

### Task 5: Bear case + verdict invalidators in the ticker card (feature 4)

**Files:**
- Modify: `ai_analyzer.py` (deep_analyze_ticker prompt + schema)
- Modify: `templates/dashboard.html` (`buildBearCase(card)` section)
- Test: `tests/test_analyze_ticker.py` (extend)

**Interfaces:**
- Produces: card JSON gains `bear_case: {thesis: str, invalidators: [str, ...]}` — `thesis` = the strongest argument AGAINST the verdict; `invalidators` = 2–4 concrete, observable conditions that would change the verdict.

- [ ] **Step 1: Failing test** (append to `tests/test_analyze_ticker.py`; the LLM stub in this file must be extended to include the new fields — update the stub payload in the same commit):

```python
def test_card_carries_bear_case_and_invalidators(client_with_stubbed_llm):
    server, c = client_with_stubbed_llm
    r = c.post("/api/analyze-ticker", json={"symbol": "NVDA"})
    card = r.json()["data"]
    assert card["bear_case"]["thesis"]
    assert isinstance(card["bear_case"]["invalidators"], list)
    assert len(card["bear_case"]["invalidators"]) >= 2
```

- [ ] **Step 2: RED**, then **Step 3: Implement.** In `ai_analyzer.py` `deep_analyze_ticker`'s JSON schema/prompt block, add (exact wording matters — it forces observable conditions, not vibes):

```text
"bear_case": {
  "thesis": "<the single strongest argument AGAINST your verdict, 2-3 sentences,
             grounded in the same data you used — steelman it, do not strawman>",
  "invalidators": ["<2-4 concrete OBSERVABLE conditions that would flip this verdict,
                    each tied to a number or event, e.g. 'gross margin prints below
                    70% next quarter' — never vague ('sentiment worsens')>"]
}
```

And in the response-validation path, default it when the model omits it:

```python
    card.setdefault("bear_case", {"thesis": "", "invalidators": []})
```

`dashboard.html`:

```javascript
function buildBearCase(card){
  const b=card.bear_case||{};
  if(!b.thesis) return '';
  const inv=(b.invalidators||[]).map(x=>`<li>${daEsc(String(x))}</li>`).join('');
  return `<div style="margin-top:10px;border-left:3px solid var(--red);padding:8px 12px;background:var(--card2)">
    <div style="font-size:12px;font-weight:600;color:var(--red)">BEAR CASE — what the other side sees</div>
    <p style="font-size:12.5px;margin:6px 0">${daEsc(b.thesis)}</p>
    ${inv?`<div style="font-size:11px;color:var(--muted)">Would change this verdict:</div><ul style="font-size:12px;margin:4px 0 0 16px">${inv}</ul>`:''}
  </div>`;
}
```

- [ ] **Step 4: GREEN + full analyze suite** — `./venv/bin/python3 -m pytest tests/test_analyze_ticker.py tests/test_analyze_any_ticker.py tests/test_dashboard_xss_wiring.py -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(analyze): bear-case thesis + observable verdict invalidators on ticker cards"`

---

### Task 6: "Where does new cash go?" card (feature 5)

LLM-free: ranks the customer's last persisted Kelly ADDs against a cash amount they enter.

**Files:**
- Modify: `server.py` (add `POST /api/mybook/deploy-cash`)
- Modify: `templates/dashboard.html` (cash input + `buildDeployCashCard(data)` in the My Portfolio section)
- Test: `tests/test_mybook.py` (extend)

**Interfaces:**
- Consumes: `auth_users.get_latest_book_analysis(email)` (kelly.holdings rows `{symbol, action, current_pct, optimal_pct, delta_usd}`), `_cached_close(sym)`.
- Produces: `POST /api/mybook/deploy-cash` body `{"cash": 2500}` → `{status:"ok", data:{cash, stale: bool, allocations:[{symbol, weight_pct, dollars, shares, price, rationale}], leftover}}`. `stale` mirrors the report's fingerprint check (T from PR #72: `saved["_fp"]`). Allocation rule: distribute cash across Kelly `ADD` rows proportionally to `optimal_pct - current_pct` (positive gaps only), `shares = floor(dollars/price)`, skip rows with no price; anything unallocatable returns in `leftover`.

- [ ] **Step 1: Failing tests** (append to `tests/test_mybook.py`, reusing `client`, `_as`, `_stub_engines`, `_mybook_analyze`):

```python
def test_deploy_cash_allocates_proportionally_to_kelly_gaps(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {"portfolio_verdict": "HOLD", "holdings_analysis": [],
                     "portfolio_risks": []},
        "kelly": {"holdings": [
            {"symbol": "NVDA", "action": "ADD", "current_pct": 2.0, "optimal_pct": 6.0},
            {"symbol": "AAPL", "action": "ADD", "current_pct": 3.0, "optimal_pct": 5.0},
            {"symbol": "MU",   "action": "TRIM", "current_pct": 9.0, "optimal_pct": 4.0}]},
        "monte_carlo": {}, "book": {"positions": [], "unpriced": []}})
    r = c.post("/api/mybook/deploy-cash", json={"cash": 3000})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["stale"] is False
    by = {a["symbol"]: a for a in d["allocations"]}
    assert set(by) == {"NVDA", "AAPL"}                     # TRIM rows never receive cash
    assert by["NVDA"]["dollars"] > by["AAPL"]["dollars"]   # 4pp gap beats 2pp gap
    # client fixture prices NVDA=100, AAPL=200
    assert by["NVDA"]["shares"] == int(by["NVDA"]["dollars"] // 100)


def test_deploy_cash_requires_analysis_and_valid_cash(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.post("/api/mybook/deploy-cash", json={"cash": 1000}).status_code == 404
    assert c.post("/api/mybook/deploy-cash", json={"cash": -5}).status_code == 400
    assert c.post("/api/mybook/deploy-cash", json={}).status_code == 400


def test_deploy_cash_flags_stale_analysis(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "_fp": "some-other-book",
        "analysis": {}, "monte_carlo": {},
        "kelly": {"holdings": [{"symbol": "NVDA", "action": "ADD",
                                "current_pct": 1.0, "optimal_pct": 3.0}]},
        "book": {"positions": [], "unpriced": []}})
    d = c.post("/api/mybook/deploy-cash", json={"cash": 1000}).json()["data"]
    assert d["stale"] is True
```

- [ ] **Step 2: RED**, then **Step 3: Implement** (server.py, after the mybook endpoints):

```python
@app.post("/api/mybook/deploy-cash")
def mybook_deploy_cash(request: Request, body: dict = Body(...)):
    """'Where does new cash go?' — ranks the customer's LAST persisted Kelly ADD
    gaps against a cash amount. Pure arithmetic on cached data: no LLM, no quota.
    Advisory only; mirrors the report's staleness fingerprint so out-of-date
    sizing is labeled, never silently served."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        cash = float(body.get("cash"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="cash must be a number") from None
    if not (0 < cash <= 10_000_000):
        raise HTTPException(status_code=400, detail="cash out of range")
    saved = auth_users.get_latest_book_analysis(email)
    if not saved:
        raise HTTPException(status_code=404,
                            detail="No analysis yet — run Analyze My Portfolio first")
    positions = auth_users.get_positions(email)
    ew = auth_users.get_equal_weight(email)
    fp_now = _book_fingerprint(positions) + ("|ew" if ew else "")
    stale = bool(saved.get("_fp")) and saved["_fp"] != fp_now
    adds = [k for k in (saved.get("kelly") or {}).get("holdings") or []
            if k.get("action") == "ADD"
            and (k.get("optimal_pct") or 0) > (k.get("current_pct") or 0)]
    gaps = {k["symbol"]: k["optimal_pct"] - k["current_pct"] for k in adds}
    total_gap = sum(gaps.values())
    allocations, spent = [], 0.0
    for k in sorted(adds, key=lambda x: gaps[x["symbol"]], reverse=True):
        sym = k["symbol"]
        price = _cached_close(sym)
        if not price or price <= 0 or total_gap <= 0:
            continue
        dollars = round(cash * gaps[sym] / total_gap, 2)
        shares = int(dollars // price)
        if shares < 1:
            continue
        allocations.append({"symbol": sym, "weight_pct": round(gaps[sym] / total_gap * 100, 1),
                            "dollars": dollars, "shares": shares, "price": price,
                            "rationale": f"Kelly gap {k['current_pct']:.1f}% → {k['optimal_pct']:.1f}%"})
        spent += shares * price
    return {"status": "ok", "data": {"cash": cash, "stale": stale,
                                     "allocations": allocations,
                                     "leftover": round(cash - spent, 2)}}
```

Frontend: a numeric input + button in the My Portfolio overview section; `buildDeployCashCard(d)` renders a table (Symbol | Weight | $ | Shares | Why) with a stale warning row when `d.stale`, and the standing "advisory only — not financial advice" footer.

- [ ] **Step 4: GREEN** — `./venv/bin/python3 -m pytest tests/test_mybook.py -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(mybook): 'where does new cash go' Kelly-gap cash deployment card (LLM-free)"`

---

### Task 7: Weekly email digest (feature 6)

**Files:**
- Create: `run_weekly_digest.py`
- Create: `run_weekly_digest.sh` + `~/Library/LaunchAgents/com.gesh.weekly-digest.plist` (Fri 17:15 ET)
- Modify: `server.py` (`GET /digest/unsubscribe`, same shape as T2's)
- Test: `tests/test_weekly_digest.py`

**Interfaces:**
- Consumes: `auth_users` (active customers, `digest_opt_out` from T2's migration, positions/watchlist, `get_latest_book_analysis`), `signal_history.get_history` (T1), `_customer_book_payload`-equivalent valuation via `_cached_close`, `email_sender.send_email`, `issue_token(..., typ="digest_unsub")`.
- Produces: `run_weekly_digest.main(dry_run=False) -> dict` `{customers, emails_sent}`. Digest sections per customer: book value now, this week's confirmed verdict changes on their symbols, their last analysis verdict + staleness note, links to `/mybook/report` and `/track-record`, unsubscribe link. **No LLM calls.**

- [ ] **Step 1: Failing tests**

```python
# tests/test_weekly_digest.py
from __future__ import annotations

from pathlib import Path

import pytest

import signal_history as sh


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    return _au


@pytest.fixture(autouse=True)
def _sig_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")


def _seed_flip(sym):
    for d in ("2026-07-13", "2026-07-14"):
        sh.record_snapshots([{"symbol": sym, "verdict": "BUY"}], date=d)
    for d in ("2026-07-15", "2026-07-16"):
        sh.record_snapshots([{"symbol": sym, "verdict": "HOLD"}], date=d)


def test_digest_sent_to_active_customer_with_book(au, monkeypatch):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    _seed_flip("NVDA")
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append((to, html)) or True)
    out = job.main()
    assert out["emails_sent"] == 1
    to, html = sent[0]
    assert to == "a@x.com"
    assert "$1,000" in html                       # book value from stubbed close
    assert "NVDA" in html and "HOLD" in html      # the week's confirmed change
    assert "/digest/unsubscribe" in html
    assert "/track-record" in html


def test_digest_skips_opted_out_and_empty_customers(au, monkeypatch):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    au.set_digest_opt_out("a@x.com", True)
    au.add_watch("empty@x.com", "NVDA")   # watch-only user still gets a digest
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append(to) or True)
    out = job.main()
    assert sent == ["empty@x.com"]


def test_digest_never_calls_llm(au, monkeypatch):
    import run_weekly_digest as job
    assert not hasattr(job, "deep_analyze_portfolio")     # structural: no LLM import
```

- [ ] **Step 2: RED**, then **Step 3: Implement `run_weekly_digest.py`** (structure mirrors `run_signal_alerts.py`; the composer):

```python
"""Weekly customer digest (launchd: com.gesh.weekly-digest, Fri 17:15 ET).

Composes ENTIRELY from cached/DB state — signal_history, the customer's book
valued via the shared close cache, their last persisted analysis. No LLM.
Push cadence is the retention lever: research shows retail research subs decay
in ~5 quarters without one (Review of Accounting Studies 2026)."""
from __future__ import annotations

import html as _html
import logging
from datetime import datetime, timezone

import auth_users
import email_sender
import signal_history

logger = logging.getLogger("weekly_digest")
BASE_URL = "https://pay.geshfinance.com"
UNSUB_TTL = 60 * 60 * 24 * 30


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
    book_line = (f"<p>Your book: <b>${total:,.0f}</b> across {len(positions)} positions.</p>"
                 if positions else "<p>You are watching " + str(len(watch)) + " symbols.</p>")
    return (f"<h2>Your GESH week — {datetime.now(timezone.utc).strftime('%b %d, %Y')}</h2>"
            f"{book_line}"
            f"<h3>Signal changes on your names</h3><table>{flip_rows}</table>"
            f"<p>Last portfolio verdict: <b>{verdict}</b> &middot; "
            f"<a href='{BASE_URL}/mybook/report'>your full report</a> &middot; "
            f"<a href='{BASE_URL}/track-record'>verified track record</a></p>"
            f"<p style='color:#888;font-size:12px'>Informational only — not financial advice. "
            f"<a href='{BASE_URL}/digest/unsubscribe?token={tok}'>Unsubscribe from the digest</a></p>")


def main(dry_run: bool = False) -> dict:
    sent = 0
    customers = _customers()
    for email in customers:
        html = _compose(email)
        if not html or dry_run:
            continue
        if email_sender.send_email(email, "Your GESH weekly digest", html):
            sent += 1
    return {"customers": len(customers), "emails_sent": sent}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(main(dry_run="--dry-run" in sys.argv)))
```

Plus in `auth_users.py`: `set_digest_opt_out` / `get_digest_opt_out` (identical shape to the T2 alert pair; the column migration already shipped in T2). And in `server.py` the `/digest/unsubscribe` route (copy T2's route, `typ="digest_unsub"`, flag `digest_opt_out`).

- [ ] **Step 4: GREEN** — `./venv/bin/python3 -m pytest tests/test_weekly_digest.py tests/test_signal_alerts.py -q`
- [ ] **Step 5: Full suite + commit**

```bash
./venv/bin/python3 -m pytest tests/ -q
git add run_weekly_digest.py run_weekly_digest.sh auth_users.py server.py tests/test_weekly_digest.py
git commit -m "feat(digest): weekly customer email digest — book value, confirmed signal changes, report links"
```

- [ ] **Step 6: Deploy** — bootstrap `com.gesh.weekly-digest.plist`, run `./venv/bin/python3 run_weekly_digest.py --dry-run` once for a JSON smoke.

---

## Rollout order & dependencies

```
T1 signal_history ──► T2 alerts ──► T7 digest
                └────► T4 sparkline
T3 provenance   (independent)
T5 bear case    (independent)
T6 cash card    (independent — only needs PR #72's _fp, already on main)
```

Ship as 3 PRs: **PR-A** = T1+T2+T4 (signals), **PR-B** = T3+T5 (card trust), **PR-C** = T6+T7 (deployment + digest). Each PR: full suite green, CI green, merge, restart `com.gesh.main`, smoke.

## Self-review notes

- Feature 3 (smoothing) is split deliberately: the gate lives in T1's `detect_confirmed_flips` (used by T2 alerts + T7 digest); the visible sparkline is T4.
- `run_signal_alerts` consensus sweep is serialized by PR #72's `_YF_DOWNLOAD_LOCK` — a 60-symbol universe at ~1–2s/symbol ≈ 2 min in a background job: acceptable; do NOT parallelize it.
- Both email jobs are idempotent (alert_log; the digest fires weekly by schedule) and fail-soft per customer.
- Quota untouched everywhere: only T5 rides an existing LLM call; everything else is mechanical.
