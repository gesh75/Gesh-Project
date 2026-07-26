"""An unavailable scaling basis must not be served as a confirmed $0.00.

THE DEFECT (live in d46487c). `_customer_scale_factor()` documents itself as
failing closed:

    "Fails CLOSED: an undeterminable basis returns 0.0 so dollars zero out."

That is fail-closed against a *leak* but fail-OPEN against a *fabrication*. The
factor is a multiplier, so 0.0 does not withhold the number — it asserts one:

    {"equity": 37522.83, "cash": 509.70}   ->   {"equity": 0.0, "cash": 0.0}

served inside an HTTP 200. A customer cannot distinguish "your model book is
empty" from "we could not work out the basis", and $0.00 is the more alarming
of the two readings. A missing number must be absent, never zero.

THE FIX IS DELIBERATELY NARROW. It does not touch `_scale_money`, the disk
read in `_customer_scale_factor`, the published-portfolio basis, or any route.
It changes exactly one decision: what the customer middleware does when the
factor it obtained cannot honestly scale anything.

    factor usable            -> unchanged behaviour
    factor <= 0, money body  -> typed 503, no numbers at all
    factor <= 0, inert body  -> serve, still fully masked (see below)

WHY "INERT" IS NOT "PASS THROUGH" — the trap this test file guards.
`_scale_money` does TWO independent jobs:

  1. SCALING numbers, which needs a factor;
  2. REDACTING owner sizing prose ("sell 4 shares ~$450"), which does not.

So a naive `if no money keys: return decoded` would hand the customer raw owner
prose the moment the basis went unavailable — turning a correctness bug into a
disclosure bug. Inert bodies are therefore still run through the masker with a
NEUTRAL factor, so every factor-independent protection still applies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import auth_users
import server

OWNER = "georgy75@gmail.com"
CUSTOMER = "cust@example.com"


# ─────────────────────────────────────────────────────────────────────────────
# The predicate: does this body's output depend on the factor?
# ─────────────────────────────────────────────────────────────────────────────

def test_money_body_requires_a_factor():
    assert server._requires_scaling_factor({"cash": 509.70}) is True
    assert server._requires_scaling_factor({"equity": 1.0, "symbol": "NVDA"}) is True


def test_quantity_body_requires_a_factor():
    """Share counts scale too — a book of 28 shares is not a book of 0."""
    assert server._requires_scaling_factor({"quantity": 28.0}) is True


def test_money_list_body_requires_a_factor():
    """The ECG equity_history leak class: bare numbers in a listed array."""
    assert server._requires_scaling_factor({"equity_history": [1.0, 2.0]}) is True


def test_monte_carlo_bucket_label_requires_a_factor():
    """A histogram label is portfolio dollars rendered as a string."""
    assert server._requires_scaling_factor(
        {"buckets": [{"label": "$25,274-$28,518"}]}) is True


def test_per_share_prices_and_percentages_do_not_require_a_factor():
    """These are public, stock-level facts. They never scale, so an unavailable
    basis is no reason to withhold them."""
    body = {"symbol": "NVDA", "price": 194.51, "percent_change": -3.51,
            "recommendation": "Buy", "num_analysts": 41}
    assert server._requires_scaling_factor(body) is False


def test_error_body_does_not_require_a_factor():
    assert server._requires_scaling_factor({"detail": "Not Found"}) is False


def test_an_exact_zero_money_field_does_not_require_a_factor():
    """0 x any factor is 0. Serving it is honest, not a fabrication."""
    assert server._requires_scaling_factor({"cash": 0.0, "equity": 0}) is False


def test_prose_alone_does_not_require_a_factor():
    """Prose is REDACTED, not scaled — the factor is irrelevant to it. This is
    the case that must NOT be answered with a 503, and must NOT be passed
    through raw either (see the middleware test below)."""
    assert server._requires_scaling_factor(
        {"thesis": "trim 4 shares near $450"}) is False


def test_booleans_are_not_mistaken_for_money():
    assert server._requires_scaling_factor({"total": True}) is False


def test_predicate_cannot_drift_from_the_scaler():
    """The predicate is defined by differential evaluation of _scale_money
    itself, so a future money key added to _MONEY_KEYS is covered the day it is
    added, with no second list to maintain."""
    for key in sorted(server._MONEY_KEYS | server._QTY_KEYS):
        assert server._requires_scaling_factor({key: 12.5}) is True, key


# ─────────────────────────────────────────────────────────────────────────────
# The typed unavailable response
# ─────────────────────────────────────────────────────────────────────────────

def test_unavailable_response_shape():
    r = server._model_unavailable_response()
    assert r.status_code == 503
    body = json.loads(bytes(r.body))
    assert body["status"] == "unavailable"
    assert body["reason_code"] == "model_scaling_unavailable"
    assert body["model_scaling_available"] is False
    assert body["data_as_of"] is None
    assert body["retryable"] is True
    assert body["view"] == "model_book"
    assert r.headers["cache-control"] == "no-store"


def test_unavailable_response_carries_no_numbers():
    """The whole point: a refusal must not contain a financial value at all."""
    body = json.loads(bytes(server._model_unavailable_response().body))
    assert server._requires_scaling_factor(body) is False


def test_unavailable_detail_names_no_internals():
    txt = bytes(server._model_unavailable_response().body).decode()
    for leak in ("snapshot", "live_portfolio", ".json", "factor", "basis",
                 "Traceback", "_customer_scale_factor"):
        assert leak not in txt, f"internal detail leaked: {leak}"


# ─────────────────────────────────────────────────────────────────────────────
# End to end through the customer middleware
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def customer(monkeypatch):
    """An authenticated NON-owner session."""
    monkeypatch.setattr(auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth_users, "authenticate_session", lambda tok: CUSTOMER)
    monkeypatch.setattr(auth_users, "is_owner", lambda e: e == OWNER)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(auth_users.COOKIE_NAME, "session-token")
    return c


@pytest.fixture
def mount():
    """Attach throwaway JSON routes so tests target the MIDDLEWARE rather than
    any real handler's own behaviour, and remove them again afterwards so the
    app object is never mutated across tests."""
    from fastapi.responses import JSONResponse as _JR

    added = []

    def _add(path: str, payload, status_code: int = 200):
        async def _h():
            return _JR(status_code=status_code, content=payload)

        server.app.add_api_route(path, _h, methods=["GET"])
        added.append(server.app.router.routes[-1])

    yield _add

    for route in added:
        if route in server.app.router.routes:
            server.app.router.routes.remove(route)


@pytest.fixture
def no_basis(monkeypatch):
    """The production failure mode: the basis cannot be determined."""
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.0)


def test_money_route_with_no_basis_returns_503_not_zeros(customer, mount, no_basis):
    mount("/api/_t_money", {"equity_summary": {"cash": 509.70,
                                               "portfolio_equity": 37_522.83}})
    r = customer.get("/api/_t_money")
    assert r.status_code == 503, f"served {r.status_code}: {r.text[:200]}"
    assert r.json()["reason_code"] == "model_scaling_unavailable"
    assert "509" not in r.text and "37,5" not in r.text and "37522" not in r.text


def test_the_exact_defect_no_zero_dollars_anywhere(customer, mount, no_basis):
    """The regression reproducing the audited behaviour: before the fix this body
    came back 200 with every dollar 0.0."""
    mount("/api/_t_defect", {"data": {"equity": 37_522.83, "cash": 509.70,
                                      "quantity": 28.0}})
    r = customer.get("/api/_t_defect")
    assert r.status_code != 200, "an unavailable basis was served as success"
    assert "0.0" not in r.text, "a fabricated zero was served"


def test_public_price_route_still_served_when_basis_unavailable(customer, mount,
                                                               no_basis):
    """No factor is needed for per-share facts, so withholding them would be an
    over-correction — a self-inflicted outage on data that is always correct."""
    mount("/api/_t_public", {"symbol": "NVDA", "price": 194.51,
                             "percent_change": -3.51})
    r = customer.get("/api/_t_public")
    assert r.status_code == 200, f"over-blocked: {r.text[:200]}"
    assert r.json()["price"] == 194.51
    assert r.json()["percent_change"] == -3.51


def test_error_body_keeps_its_own_status_and_is_not_turned_into_503(customer, mount,
                                                                   no_basis):
    """A 404 must stay a 404. Masking is not an error handler."""
    mount("/api/_t_404", {"detail": "Unknown symbol."}, status_code=404)
    r = customer.get("/api/_t_404")
    assert r.status_code == 404, f"error status rewritten to {r.status_code}"
    assert r.json()["detail"] == "Unknown symbol."


def test_inert_body_is_still_masked_not_passed_through(customer, mount, no_basis):
    """THE TRAP. A body needing no factor still needs prose redaction. If the
    unavailable path short-circuits to the raw body, owner sizing prose leaks."""
    mount("/api/_t_prose", {"thesis": "Trim 4 shares near $450 to rebalance."})
    r = customer.get("/api/_t_prose")
    assert r.status_code == 200
    text = r.json()["thesis"]
    assert "$450" not in text, "owner $ leaked when the basis was unavailable"
    assert "4 shares" not in text, "owner share count leaked"


def test_a_usable_factor_is_completely_unaffected(customer, mount, monkeypatch):
    """The happy path must behave exactly as before the change."""
    mount("/api/_t_ok", {"cash": 100.0, "quantity": 10.0, "price": 194.51})
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 2.0)
    r = customer.get("/api/_t_ok")
    assert r.status_code == 200
    assert r.json()["cash"] == pytest.approx(200.0)
    assert r.json()["quantity"] == pytest.approx(20.0)
    assert r.json()["price"] == 194.51
    assert r.json()["view"] == "model_book"


@pytest.mark.parametrize("bad", [0.0, -1.0, -0.0001, float("nan"), float("inf")])
def test_every_unusable_factor_refuses(customer, mount, monkeypatch, bad):
    """Negative inverts the book; NaN/inf corrupt it. All are unusable."""
    mount("/api/_t_bad", {"cash": 100.0})
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: bad)
    r = customer.get("/api/_t_bad")
    assert r.status_code == 503, f"factor {bad!r} produced {r.status_code}"


def test_owner_is_unaffected_by_the_unavailable_path(mount, monkeypatch):
    """The owner sees real dollars and never enters the masking middleware."""
    monkeypatch.setattr(auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth_users, "authenticate_session", lambda tok: OWNER)
    monkeypatch.setattr(auth_users, "is_owner", lambda e: e == OWNER)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.0)
    mount("/api/_t_owner", {"cash": 509.70})

    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(auth_users.COOKIE_NAME, "session-token")
    r = c.get("/api/_t_owner")
    assert r.status_code == 200
    assert r.json()["cash"] == 509.70, "owner dollars were masked or withheld"


def test_unauthenticated_request_is_unaffected(mount, monkeypatch):
    """With the wall on, an anonymous /api/* caller is refused by the LOGIN wall
    (401) before masking is ever reached — so this fix cannot change what an
    unauthenticated caller sees. Asserted as the concrete preserved contract:
    still 401, and specifically never a scaling 503 (which would misattribute an
    auth refusal to a data problem, and would also leak that a basis exists)."""
    monkeypatch.setattr(auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.0)
    mount("/api/_t_anon", {"cash": 509.70})

    r = TestClient(server.app, raise_server_exceptions=False).get("/api/_t_anon")
    assert r.status_code == 401, f"auth-wall behaviour changed: {r.status_code}"
    assert r.status_code != 503
    assert "509" not in r.text, "owner dollars reached an anonymous caller"


def test_unauthenticated_request_with_the_wall_off_is_unaffected(mount, monkeypatch):
    """Wall OFF is the other half of "unauthenticated behaviour is unchanged":
    the middleware returns at the `auth_enabled()` guard, so no masking, no 503,
    and the route's own body is served exactly as before."""
    monkeypatch.setattr(auth_users, "auth_enabled", lambda: False)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.0)
    mount("/api/_t_anon_off", {"cash": 509.70})

    r = TestClient(server.app, raise_server_exceptions=False).get("/api/_t_anon_off")
    assert r.status_code == 200
    assert r.json()["cash"] == 509.70
    assert "view" not in r.json(), "a body was masked with the wall off"


# ─────────────────────────────────────────────────────────────────────────────
# The refusal must not be a side-effecting operation
# ─────────────────────────────────────────────────────────────────────────────

def _db_fingerprint() -> dict[str, int]:
    """Row counts for every table in the ISOLATED test auth DB (conftest points
    AUTH_DB_PATH at a tmp_path per test)."""
    import sqlite3
    path = auth_users.AUTH_DB_PATH
    if not Path(path).exists():
        return {}
    con = sqlite3.connect(str(path))
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in tables}
    finally:
        con.close()


def test_refused_response_mutates_no_database_rows(customer, mount, no_basis):
    """A refusal is a read that declined to answer, so it must not write.

    SCOPE NOTE, stated rather than implied: the masking middleware runs AFTER the
    handler (`resp = await call_next(request)`), so this fix neither can nor does
    roll back writes a handler already made — that is unchanged from d46487c and
    is not what this test claims. What it proves is that the REFUSAL PATH ITSELF
    introduces no mutation: no usage row, no cached analysis, no job record."""
    auth_users.init_auth_db()
    mount("/api/_t_nodb", {"equity": 37_522.83, "cash": 509.70})

    before = _db_fingerprint()
    r = customer.get("/api/_t_nodb")
    after = _db_fingerprint()

    assert r.status_code == 503
    assert before == after, f"refusal mutated the DB: {before} -> {after}"


def test_repeated_refusals_stay_side_effect_free(customer, mount, no_basis):
    """A retryable response invites retries; they must not accumulate state."""
    auth_users.init_auth_db()
    mount("/api/_t_retry", {"cash": 100.0})
    customer.get("/api/_t_retry")
    before = _db_fingerprint()
    for _ in range(5):
        assert customer.get("/api/_t_retry").status_code == 503
    assert _db_fingerprint() == before, "retries accumulated rows"


# ─────────────────────────────────────────────────────────────────────────────
# Neighbouring hardening must survive: PR #107 and PR #108
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_origin", ["https://x:notaport", "https://x:65536",
                                        "http://u:p@evil.example"])
def test_pr107_malformed_origin_still_a_controlled_refusal(monkeypatch, bad_origin):
    """PR #107 turned a malformed Origin on a cookie-authenticated mutation into
    a controlled 403 instead of a pre-auth 500. The new 503 must not intercept
    that: a rejected request boundary is not a scaling problem."""
    monkeypatch.setattr(auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth_users, "authenticate_session", lambda tok: CUSTOMER)
    monkeypatch.setattr(auth_users, "is_owner", lambda e: e == OWNER)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.0)

    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(auth_users.COOKIE_NAME, "session-token")
    r = c.post("/api/watchlist", json={"symbol": "NVDA"},
               headers={"Origin": bad_origin})

    assert r.status_code == 403, f"{bad_origin} gave {r.status_code}"
    assert r.status_code != 500, "PR #107 regression: uncontrolled failure"
    assert r.json().get("reason_code") != "model_scaling_unavailable", \
        "CSRF refusal was rewritten as a scaling refusal"


def test_pr108_unknown_symbol_stays_404_for_a_customer(customer, monkeypatch):
    """PR #108 made an unknown ticker a 404 rather than a fabricated card.
    `/api/fundamentals` is NOT mask-exempt, so it passes through this middleware
    — with no basis its 404 body must stay a 404, not become a 503."""
    import fundamental_agent
    import market_data
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.0)
    monkeypatch.setattr(market_data, "rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch",
                        lambda syms: [{"symbol": "ZZZQQ", "company_name": "ZZZQQ",
                                       "sector": "", "price": None}], raising=False)
    server._ticker_get_cache.clear()

    r = customer.get("/api/fundamentals/ZZZQQ")
    assert r.status_code == 404, f"unknown-symbol honesty regressed: {r.status_code}"
    assert "hold" not in r.text.lower()


# ─────────────────────────────────────────────────────────────────────────────
# The refusal log must be throttled
#
# An unavailable basis is a STANDING CONDITION, not an event. The dashboard
# re-polls every money route on a timer, so an unthrottled warning emits a line
# per route per poll per open tab for as long as the snapshot is missing — the
# failure mode that previously put ~26,000 duplicate lines into one unrotated
# log. Reusing `_throttled_log_due` keeps the first hit immediate (an operator
# still learns at once) while repeats collapse into a counted line.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_throttle_state():
    """Throttle state is process-GLOBAL; conftest does not clear it. Reset on
    both sides so these tests neither inherit nor leak a throttle window."""
    server._reset_throttled_logs()
    yield
    server._reset_throttled_logs()


def test_first_refusal_logs_immediately(customer, mount, no_basis, caplog):
    """An operator must hear about the very first failure without waiting."""
    mount("/api/_t_log1", {"cash": 100.0})
    with caplog.at_level("WARNING", logger=server.logger.name):
        assert customer.get("/api/_t_log1").status_code == 503
    lines = [r for r in caplog.records if "model scaling unavailable" in r.message]
    assert len(lines) == 1, f"expected exactly one line, got {len(lines)}"
    assert "suppressed" not in lines[0].getMessage(), \
        "the first line must not claim suppressed repeats"


def test_repeats_inside_the_window_do_not_each_log(customer, mount, no_basis, caplog):
    """The regression this correction exists to prevent."""
    mount("/api/_t_log2", {"cash": 100.0})
    with caplog.at_level("WARNING", logger=server.logger.name):
        for _ in range(25):
            assert customer.get("/api/_t_log2").status_code == 503
    lines = [r for r in caplog.records if "model scaling unavailable" in r.message]
    assert len(lines) == 1, f"25 refusals emitted {len(lines)} lines, expected 1"


def test_the_next_emitted_line_reports_the_suppressed_count(customer, mount,
                                                            no_basis, caplog):
    """A quiet log must never imply a quiet system: the count is the whole point
    of throttling rather than dropping."""
    mount("/api/_t_log3", {"cash": 100.0})
    for _ in range(9):                       # 1 logged + 8 suppressed
        assert customer.get("/api/_t_log3").status_code == 503

    # Expire the window without sleeping: rewind this key's last-emission stamp.
    with server._throttled_log_lock:
        last, suppressed = server._throttled_log_last["model_scaling_unavailable"]
        server._throttled_log_last["model_scaling_unavailable"] = (
            last - server._THROTTLED_LOG_INTERVAL_S - 1, suppressed)

    # caplog collects for the WHOLE test, so drop the first emission before
    # asserting on the one this case is actually about.
    caplog.clear()
    with caplog.at_level("WARNING", logger=server.logger.name):
        assert customer.get("/api/_t_log3").status_code == 503
    lines = [r.getMessage() for r in caplog.records
             if "model scaling unavailable" in r.message]
    assert len(lines) == 1
    assert "+8 suppressed" in lines[0], f"count not reported: {lines[0]}"


def test_throttle_key_is_distinct_from_other_throttled_call_sites(customer, mount,
                                                                  no_basis):
    """A shared key would let an unrelated offline-portfolio line swallow the
    first scaling refusal, or vice versa."""
    mount("/api/_t_log4", {"cash": 100.0})
    assert customer.get("/api/_t_log4").status_code == 503
    keys = set(server._throttled_log_last)
    assert "model_scaling_unavailable" in keys
    assert "api_portfolio_offline" not in keys, "collided with another call site"


def test_throttling_never_changes_the_response(customer, mount, no_basis):
    """Requirement 5, asserted directly: every refusal in a throttle window is
    byte-identical, whether or not its occurrence was logged."""
    mount("/api/_t_log5", {"cash": 100.0})
    seen = []
    for _ in range(6):
        r = customer.get("/api/_t_log5")
        seen.append((r.status_code, r.headers.get("cache-control"), r.text))
    assert len({s for s in seen}) == 1, "throttling altered a response"
    assert seen[0][0] == 503
    assert seen[0][1] == "no-store"
    assert json.loads(seen[0][2])["reason_code"] == "model_scaling_unavailable"


def test_a_served_response_logs_nothing(customer, mount, no_basis, caplog):
    """Only refusals are noisy; a factor-independent body must stay silent."""
    mount("/api/_t_log6", {"symbol": "NVDA", "price": 194.51})
    with caplog.at_level("WARNING", logger=server.logger.name):
        assert customer.get("/api/_t_log6").status_code == 200
    assert not [r for r in caplog.records if "model scaling unavailable" in r.message]


def test_pr108_known_symbol_still_served_to_a_customer(customer, monkeypatch):
    """The mirror case: a real symbol's public facts need no scaling factor, so
    an unavailable basis must not withhold them."""
    import fundamental_agent
    import market_data
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.0)
    monkeypatch.setattr(market_data, "rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch",
                        lambda syms: [{"symbol": "AAPL", "company_name": "Apple Inc.",
                                       "sector": "Technology", "price": 213.5,
                                       "trailing_pe": 28.4}], raising=False)
    server._ticker_get_cache.clear()

    r = customer.get("/api/fundamentals/AAPL")
    assert r.status_code == 200, f"over-blocked a public card: {r.text[:200]}"
    assert r.json()["data"]["price"] == 213.5, "a per-share price was altered"
