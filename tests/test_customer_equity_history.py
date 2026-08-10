"""Per-user equity history — the missing input for a CUSTOMER Equity Curve Gate.

The owner console has an ECG panel driven by `equity_snapshots`, a table with no
user column: `get_equity_history(symbol)` returns the OWNER's curve for any
symbol anybody asks about. `tests/test_customer_owner_isolation.py` pins that as
the reason customers cannot have an ECG today. This file builds the other half —
a per-user store fed exclusively from `customer_positions` valued at public
closes — and the ECG computed against it.

Three properties are asserted structurally rather than by example, because each
one is a bug this repo has actually shipped:

  * INSUFFICIENT HISTORY IS A STATE, NOT A MESSAGE. `gesh_core.ecg.compute_ecg`
    returns state "HEALTHY" with an "Insufficient history" *message* below
    MIN_SNAPSHOTS. A customer panel renders the state, so two days of data would
    paint a confident green pill. The customer verdict must say so in the state.
  * `_loss_streak` TAKES PERCENT CHANGES, NEVER EQUITY LEVELS. Levels are always
    positive, so a level-fed streak pins to 0 and the streak circuit breaker
    silently never fires (fixed twice: consensus.node_ecg, 2026-07-17).
  * HISTORY IS ORDERED OLDEST -> NEWEST BEFORE THE RUNNING-PEAK MATH. Fed
    newest-first, a rising position reads as a crash.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

OWNER_EMAIL = "georgy75@gmail.com"
CUSTOMER = "cust@x.com"
OTHER = "other@x.com"

# Deliberately disjoint from the owner's real book (AMD BAC BKNG BLK COF DELL
# HOOD JD JMKE MPC MU NVDA PANW REGN RTX SCHW) so any owner ticker showing up in
# a customer response is unambiguous contamination.
SYM = "ZZZA"
SYM2 = "ZZZB"

OWNER_SYMBOLS = {
    "AMD", "BAC", "BKNG", "BLK", "COF", "DELL", "HOOD", "JD",
    "JMKE", "MPC", "MU", "NVDA", "PANW", "REGN", "RTX", "SCHW",
}


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in (CUSTOMER, OTHER, OWNER_EMAIL):
        _au.create_user(email, "test-password")
    monkeypatch.setattr(_au, "is_owner", lambda e: (e or "").lower() == OWNER_EMAIL,
                        raising=False)
    return _au


@pytest.fixture
def ceh(au):
    import customer_equity_history as _ceh

    return _ceh


def _seed(au, email: str, symbol: str, equities: list[float], *, price: float = 10.0) -> None:
    """Write one snapshot per equity value, one day apart, oldest first."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, eq in enumerate(equities):
        au.record_equity_snapshots(
            email,
            [{"symbol": symbol, "equity": eq, "current_price": price}],
            at=(base + timedelta(days=i)).isoformat(),
        )


# ─── storage layer: per-user, and only ever per-user ─────────────────────────

def test_record_and_read_roundtrip(au):
    _seed(au, CUSTOMER, SYM, [100.0, 110.0, 120.0])
    hist = au.get_user_equity_history(CUSTOMER, SYM, days=30)

    assert len(hist) == 3
    # newest-first, matching db.get_equity_history's contract, because
    # ecg.compute_ecg reverses what it is handed.
    assert [round(r["equity"], 2) for r in hist] == [120.0, 110.0, 100.0]
    assert all(r["current_price"] == 10.0 for r in hist)
    assert all(r["snapshot_at"] for r in hist)


def test_history_is_per_user_isolated(au):
    _seed(au, CUSTOMER, SYM, [100.0, 50.0])
    _seed(au, OTHER, SYM, [900.0])

    assert [r["equity"] for r in au.get_user_equity_history(CUSTOMER, SYM)] == [50.0, 100.0]
    assert [r["equity"] for r in au.get_user_equity_history(OTHER, SYM)] == [900.0]


def test_history_of_an_unknown_user_is_empty_not_an_error(au):
    assert au.get_user_equity_history("ghost@x.com", SYM) == []


def test_the_store_never_reaches_the_owners_equity_snapshots_table(ceh):
    """Structural: the customer path must not import or call the owner history.

    Checked against the parsed AST, not the text — the module docstring names
    `get_equity_history` and `equity_snapshots` precisely to explain why it does
    NOT use them, and a guard that a reword can satisfy guards nothing."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ceh))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert "db" not in imported, "customer ECG imported the owner-portfolio DB module"

    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "get_equity_history" not in called, (
        "customer ECG calls the symbol-keyed OWNER history function — it takes no "
        "user argument, so its result is the owner's curve regardless of who asks"
    )

    # No SQL string in this module may name the bare owner table. Its own table,
    # customer_equity_snapshots, legitimately contains that substring. Docstrings
    # are excluded by identity so prose explaining the owner table is allowed.
    docstrings = {
        id(ast.get_docstring(n, clean=False))
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    sql = re.compile(r"\b(SELECT|INSERT\s+INTO|DELETE\s+FROM|UPDATE)\b", re.I)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node.value) in docstrings or not sql.search(node.value):
            continue
        assert re.search(r"(?<!customer_)\bequity_snapshots\b", node.value) is None, (
            f"customer ECG queries the OWNER table: {node.value!r}"
        )


def test_deleting_an_account_purges_its_equity_history(au):
    """A table not listed in _USER_DATA_TABLES silently survives a 'deletion'."""
    _seed(au, CUSTOMER, SYM, [100.0, 90.0])
    assert au.get_user_equity_history(CUSTOMER, SYM)

    report = au.delete_user_data(CUSTOMER)

    assert "customer_equity_snapshots" in au._USER_DATA_TABLES
    assert report["counts"]["customer_equity_snapshots"] == 2
    assert au.get_user_equity_history(CUSTOMER, SYM) == []


def test_an_existing_auth_db_picks_the_table_up_without_losing_data(au):
    """CREATE TABLE IF NOT EXISTS never alters an existing DB. Every live
    gesh_auth.db predates this table, so the idempotent-migration path — not the
    fresh-install path — is the one that actually runs in production."""
    au.set_position(CUSTOMER, SYM, 7)
    au.add_watch(CUSTOMER, SYM2)

    # Simulate the pre-existing database: same schema version, table absent.
    import sqlite3

    con = sqlite3.connect(au.AUTH_DB_PATH)
    con.execute("DROP TABLE customer_equity_snapshots")
    con.commit()
    con.close()

    au.init_auth_db()  # reconnect -> _migrate_to_user_ids re-creates it

    assert au.record_equity_snapshots(
        CUSTOMER, [{"symbol": SYM, "equity": 70.0, "current_price": 10.0}]
    ) == 1
    assert au.get_user_equity_history(CUSTOMER, SYM)[0]["equity"] == 70.0
    # the pre-existing tenant data survived the migration
    assert [p["symbol"] for p in au.get_positions(CUSTOMER)] == [SYM]
    assert au.get_watch(CUSTOMER) == [SYM2]


def test_history_is_bounded_per_symbol(au):
    """Unbounded per-user append is a storage-growth bug waiting to happen."""
    cap = au._MAX_EQUITY_SNAPSHOTS_PER_SYMBOL
    _seed(au, CUSTOMER, SYM, [float(i) + 1.0 for i in range(cap + 25)])

    hist = au.get_user_equity_history(CUSTOMER, SYM, days=cap + 100)
    assert len(hist) == cap
    # the OLDEST rows are the ones dropped
    assert hist[0]["equity"] == float(cap + 25)


# ─── the recorder (what a scheduler calls) ───────────────────────────────────

def test_recorder_values_the_customers_own_book_at_public_closes(ceh, au):
    au.set_position(CUSTOMER, SYM, 4)
    au.set_position(CUSTOMER, SYM2, 2)

    out = ceh.record_snapshot(
        CUSTOMER, price_fn={SYM: 25.0, SYM2: 100.0}.get, min_interval_seconds=0
    )

    assert out["recorded"] == 2
    assert au.get_user_equity_history(CUSTOMER, SYM)[0]["equity"] == 100.0     # 4 x 25
    assert au.get_user_equity_history(CUSTOMER, SYM2)[0]["equity"] == 200.0    # 2 x 100


def test_recorder_skips_a_second_call_inside_the_interval(ceh, au):
    """A 30-minute scheduler tick must be a cheap no-op, not a duplicate row —
    ECG counts snapshots as sessions, so over-sampling corrupts the streak."""
    au.set_position(CUSTOMER, SYM, 1)
    assert ceh.record_snapshot(CUSTOMER, price_fn=lambda s: 10.0,
                               min_interval_seconds=0)["recorded"] == 1

    again = ceh.record_snapshot(CUSTOMER, price_fn=lambda s: 10.0,
                                min_interval_seconds=6 * 3600)

    assert again["recorded"] == 0 and again["skipped"] is True
    assert len(au.get_user_equity_history(CUSTOMER, SYM)) == 1


def test_recorder_refuses_non_observations(ceh, au):
    """price<=0 / non-finite is MISSING data, never a loss. Persisting sentinels
    is exactly what poisoned the owner curve into a fake -96% crash (audit D-07)."""
    au.set_position(CUSTOMER, SYM, 1)
    au.set_position(CUSTOMER, SYM2, 1)

    out = ceh.record_snapshot(
        CUSTOMER, price_fn={SYM: 0.0, SYM2: 12.0}.get, min_interval_seconds=0
    )

    assert out["recorded"] == 1
    assert out["unpriced"] == [SYM]
    assert au.get_user_equity_history(CUSTOMER, SYM) == []


def test_recorder_never_writes_owner_rows(ceh, au):
    """The owner's curve already lives in the owner-only table. A second copy
    inside the customer database is the leak this whole store exists to avoid."""
    au.set_position(OWNER_EMAIL, "NVDA", 10)

    out = ceh.record_snapshot(OWNER_EMAIL, price_fn=lambda s: 100.0, min_interval_seconds=0)

    assert out["recorded"] == 0
    assert out["reason"] == "owner_excluded"
    assert au.get_user_equity_history(OWNER_EMAIL, "NVDA") == []


def test_scheduler_entrypoint_walks_every_customer_with_a_book(ceh, au):
    au.set_position(CUSTOMER, SYM, 1)
    au.set_position(OTHER, SYM2, 3)
    au.set_position(OWNER_EMAIL, "NVDA", 1)

    report = ceh.record_snapshots_for_all_customers(
        price_fn=lambda s: 10.0, min_interval_seconds=0
    )

    assert report["users"] == 2 and report["recorded"] == 2
    assert au.get_user_equity_history(CUSTOMER, SYM)[0]["equity"] == 10.0
    assert au.get_user_equity_history(OTHER, SYM2)[0]["equity"] == 30.0
    assert au.get_user_equity_history(OWNER_EMAIL, "NVDA") == []


def test_scheduler_entrypoint_survives_one_bad_user(ceh, au, monkeypatch):
    """One user's failure must not abort the sweep for everybody else."""
    au.set_position(CUSTOMER, SYM, 1)
    au.set_position(OTHER, SYM2, 1)

    def _boom(sym):
        if sym == SYM:
            raise RuntimeError("upstream down")
        return 10.0

    report = ceh.record_snapshots_for_all_customers(price_fn=_boom, min_interval_seconds=0)

    assert report["failed"] == 1
    assert au.get_user_equity_history(OTHER, SYM2)[0]["equity"] == 10.0


# ─── ECG over the per-user history ───────────────────────────────────────────

def test_insufficient_history_is_a_state_not_a_confident_healthy(ceh, au):
    """Two days of data must be REPORTED as two days of data."""
    au.set_position(CUSTOMER, SYM, 1)
    _seed(au, CUSTOMER, SYM, [100.0, 101.0])

    out = ceh.compute_book_ecg(CUSTOMER)
    state = out["states"][0]

    assert state["state"] == "INSUFFICIENT_HISTORY"
    assert state["snapshots_available"] == 2
    assert state["snapshots_required"] == ceh.MIN_SNAPSHOTS_FOR_VERDICT
    assert state["snapshots_needed"] == ceh.MIN_SNAPSHOTS_FOR_VERDICT - 2
    # None, not 0.0 — a zero reads as a real measurement of "no drawdown".
    assert state["drawdown_pct"] is None and state["loss_streak"] is None
    assert "2" in state["message"] and str(ceh.MIN_SNAPSHOTS_FOR_VERDICT) in state["message"]
    assert out["portfolio_gate"] == "INSUFFICIENT_HISTORY"


def test_a_book_with_no_history_at_all_says_so(ceh, au):
    au.set_position(CUSTOMER, SYM, 1)

    out = ceh.compute_book_ecg(CUSTOMER)

    assert out["states"][0]["snapshots_available"] == 0
    assert out["states"][0]["state"] == "INSUFFICIENT_HISTORY"
    assert out["portfolio_gate"] == "INSUFFICIENT_HISTORY"


def test_an_empty_book_is_not_a_verdict(ceh, au):
    out = ceh.compute_book_ecg(CUSTOMER)

    assert out["states"] == []
    assert out["portfolio_gate"] == "INSUFFICIENT_HISTORY"


def test_a_real_drawdown_is_classified_once_history_suffices(ceh, au):
    au.set_position(CUSTOMER, SYM, 1)
    _seed(au, CUSTOMER, SYM, [100.0, 105.0, 110.0, 108.0, 95.0, 88.0, 80.0, 70.0])

    state = ceh.compute_book_ecg(CUSTOMER)["states"][0]

    assert state["state"] == "SUSPENDED"
    # peak 110 -> 70 = -36.4%, from the PEAK not the first point
    assert state["drawdown_pct"] == pytest.approx(-36.36, abs=0.05)
    assert state["snapshots_available"] == 8


def test_loss_streak_is_fed_percent_changes_never_equity_levels(ceh, au):
    """A -1%/session slide for 7 sessions is only -6.8% from peak — ABOVE the
    -8% cooling threshold — so the ONLY thing that can flag it is the streak.
    Equity levels are always positive, so a level-fed streak is 0 and this book
    reads HEALTHY. That regression shipped twice; this is its tripwire."""
    au.set_position(CUSTOMER, SYM, 1)
    equities, eq = [], 100.0
    for _ in range(8):
        equities.append(eq)
        eq *= 0.99
    _seed(au, CUSTOMER, SYM, equities)

    state = ceh.compute_book_ecg(CUSTOMER)["states"][0]

    assert state["loss_streak"] == 7, "streak was computed from levels, not changes"
    assert state["drawdown_pct"] > ceh_cooling_threshold(), (
        "test no longer discriminates — drawdown alone now trips the gate"
    )
    assert state["state"] == "SUSPENDED"


def ceh_cooling_threshold() -> float:
    import ecg

    return ecg.COOLING_DRAWDOWN_PCT


def test_history_is_ordered_oldest_to_newest_before_the_peak_math(ceh, au):
    """A monotonically RISING position: correct ordering puts the peak last, so
    drawdown is 0. Fed newest-first the 'latest' equity is the oldest, smallest
    value and the same book reads as a -50% crash."""
    au.set_position(CUSTOMER, SYM, 1)
    _seed(au, CUSTOMER, SYM, [100.0, 115.0, 130.0, 145.0, 160.0, 180.0, 200.0])

    state = ceh.compute_book_ecg(CUSTOMER)["states"][0]

    assert state["drawdown_pct"] == pytest.approx(0.0, abs=0.01)
    assert state["loss_streak"] == 0
    assert state["state"] == "HEALTHY"


def test_one_users_history_never_appears_in_another_users_ecg(ceh, au):
    """THE isolation property. Both users hold the same ticker; only one of them
    has a crash on the books."""
    _seed(au, CUSTOMER, SYM, [100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0])
    au.set_position(CUSTOMER, SYM, 1)
    au.set_position(OTHER, SYM, 1)

    crashed = ceh.compute_book_ecg(CUSTOMER)["states"][0]
    innocent = ceh.compute_book_ecg(OTHER)["states"][0]

    assert crashed["state"] == "SUSPENDED"
    assert innocent["state"] == "INSUFFICIENT_HISTORY"
    assert innocent["snapshots_available"] == 0
    assert innocent["drawdown_pct"] is None


def test_portfolio_gate_takes_the_worst_READY_holding(ceh, au):
    """A name still accumulating must neither mask a suspended one nor be
    counted as a healthy vote."""
    au.set_position(CUSTOMER, SYM, 1)
    au.set_position(CUSTOMER, SYM2, 1)
    _seed(au, CUSTOMER, SYM, [100.0, 95.0, 90.0, 85.0, 80.0, 75.0, 60.0])
    _seed(au, CUSTOMER, SYM2, [100.0, 101.0])

    out = ceh.compute_book_ecg(CUSTOMER)

    assert out["portfolio_gate"] == "SUSPENDED"
    assert out["coverage"] == {"ready": 1, "accumulating": 1, "total": 2}
    assert out["accumulating"] == [SYM2]
    assert str(ceh.MIN_SNAPSHOTS_FOR_VERDICT) in out["portfolio_message"]


def test_a_corrupt_stored_row_never_reads_as_a_verdict(ceh, au, monkeypatch):
    """Non-finite equity must not reach _classify — every NaN comparison is
    false, which would make the gate fail OPEN."""
    au.set_position(CUSTOMER, SYM, 1)
    monkeypatch.setattr(
        au, "get_user_equity_history",
        lambda email, symbol, days=30: [
            {"snapshot_at": "2026-01-0%d" % (8 - i), "equity": float("nan"),
             "current_price": 1.0}
            for i in range(7)
        ],
        raising=True,
    )

    state = ceh.compute_book_ecg(CUSTOMER)["states"][0]

    assert state["state"] == "SUSPENDED"
    assert "invalid" in state["message"].lower()


# ─── the endpoint ────────────────────────────────────────────────────────────

@pytest.fixture
def client(au, monkeypatch):
    import server

    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    prices = {SYM: 10.0, SYM2: 20.0}
    monkeypatch.setattr(server, "_latest_close", lambda sym: prices.get(sym), raising=True)
    monkeypatch.setattr(
        server, "_latest_close_meta",
        lambda sym: (prices.get(sym), {"source": "yahoo", "substituted": False}),
        raising=True,
    )
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER_EMAIL,
                        raising=False)


def test_ecg_endpoint_requires_a_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)

    assert c.get("/api/mybook/ecg").status_code == 401


def test_ecg_endpoint_reports_the_insufficient_state_with_counts(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position(CUSTOMER, SYM, 1)
    _seed(au, CUSTOMER, SYM, [100.0, 99.0])

    r = c.get("/api/mybook/ecg")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["portfolio_gate"] == "INSUFFICIENT_HISTORY"
    assert data["snapshots_required"] >= 2
    assert data["states"][0]["snapshots_available"] == 2
    assert data["states"][0]["state"] == "INSUFFICIENT_HISTORY"


def test_ecg_endpoint_classifies_once_history_suffices(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position(CUSTOMER, SYM, 1)
    _seed(au, CUSTOMER, SYM, [100.0, 95.0, 90.0, 85.0, 78.0, 70.0, 60.0])

    data = c.get("/api/mybook/ecg").json()["data"]

    assert data["states"][0]["state"] == "SUSPENDED"
    assert data["portfolio_gate"] == "SUSPENDED"


def test_ecg_endpoint_never_carries_owner_symbols_or_equity(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position(CUSTOMER, SYM, 1)
    _seed(au, CUSTOMER, SYM, [100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0])

    text = c.get("/api/mybook/ecg").text

    leaked = set(re.findall(r"\b[A-Z]{1,5}\b", text)) & OWNER_SYMBOLS
    assert not leaked, f"owner symbols leaked: {leaked}"
    assert "33758" not in text


def test_ecg_endpoint_shows_only_the_callers_own_history(client, au, monkeypatch):
    server, c = client
    _seed(au, CUSTOMER, SYM, [100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0])
    au.set_position(CUSTOMER, SYM, 1)
    au.set_position(OTHER, SYM, 1)

    _as(server, monkeypatch, CUSTOMER)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.get("/api/mybook/ecg").json()["data"]["states"][0]["state"] == "SUSPENDED"

    _as(server, monkeypatch, OTHER)
    other = c.get("/api/mybook/ecg").json()["data"]
    assert other["states"][0]["state"] == "INSUFFICIENT_HISTORY"
    assert other["states"][0]["snapshots_available"] == 0


def test_ecg_endpoint_lives_under_the_mybook_prefix(client):
    """The prefix is what makes it mask-exempt (the customer's own dollars) and
    rate-limited in the heavy bucket. A sibling path would silently lose both."""
    server, _ = client
    paths = {getattr(r, "path", "") for r in server.app.routes}

    assert "/api/mybook/ecg" in paths
    assert any(p == "/api/mybook" for p in server._MASK_EXEMPT_PREFIXES)
