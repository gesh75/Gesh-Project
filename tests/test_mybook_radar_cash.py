"""Cash sizing for the customer's Diversification Radar.

The radar itself is computed CASH-FREE inside the analysis (the analysis is cached
per (user, book fingerprint), so folding a cash figure into it would invalidate that
fingerprint the moment the customer changed the number). `POST /api/mybook/radar-cash`
is the other half: pure arithmetic over the SHORTLIST captured at scan time
(`resize_state`), sized for whatever cash they type.

The invariants pinned here:

  * No LLM, no quota — exactly like /api/mybook/deploy-cash. This endpoint must
    stay free to call, or the slider becomes a metered feature by accident.
  * `resize_state` never reaches the browser. It carries the book total and the
    per-sector dollar map; only sized public candidates may cross the wire.
  * Session-scoped and per-user — one customer's persisted radar can never size
    another customer's cash.
  * `/mybook/report` renders the radar without spending a call, with every
    dynamic string escaped (it is server-rendered HTML outside the dashboard's
    audited JS builders).

Same offline rules as the rest of the suite: prices and engines are stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in ("cust@x.com", "other@x.com", "georgy75@gmail.com"):
        _au.create_user(email, "test-password")
    return _au


@pytest.fixture
def client(au, monkeypatch):
    import server

    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    prices = {"NVDA": 100.0, "AAPL": 200.0}
    monkeypatch.setattr(server, "_latest_close", lambda sym: prices.get(sym), raising=True)
    monkeypatch.setattr(
        server,
        "_latest_close_meta",
        lambda sym: (prices.get(sym), {"source": "yahoo", "substituted": False}),
        raising=True,
    )
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(
        server.auth_users, "is_owner", lambda e: e == "georgy75@gmail.com", raising=False
    )


def _resize_state() -> dict:
    """A scan-time shortlist shaped exactly like invest_radar.scan_candidates emits.
    Survivors are in DIFFERENT sectors so the radar's diversification pass keeps
    both, and priced low enough that a few thousand dollars buys whole shares."""
    return {
        "survivors": [
            {
                "symbol": "KO",
                "company_name": "Coca-Cola",
                "sector": "Consumer Defensive",
                "price": 60.0,
                "vol": 0.02,
                "composite_z": 0.6,
                "max_corr_to_holdings": 0.11,
                "correlation_ok": True,
                "analyst_upside": 12.0,
                "num_analysts": 20,
                "ret_1w": 1.2,
                "ret_1m": 3.4,
            },
            {
                "symbol": "XOM",
                "company_name": "Exxon Mobil",
                "sector": "Energy",
                "price": 110.0,
                "vol": 0.025,
                "composite_z": 0.4,
                "max_corr_to_holdings": 0.09,
                "correlation_ok": True,
                "analyst_upside": 8.0,
                "num_analysts": 18,
                "ret_1w": 0.5,
                "ret_1m": 2.1,
            },
        ],
        "sector_value": {"Technology": 8000.0},
        "total": 10000.0,
        "open_slots": 4,
        "max_stock_per_trade": 5000.0,
    }


def _radar(**over) -> dict:
    radar = {
        "as_of": "2026-08-09T12:00:00+00:00",
        "mode": "SWAP_ONLY",
        "deployable_cash": 0.0,
        "open_slots": 4,
        "new_names_to_add": 4,
        "excluded_holdings": ["NVDA"],
        "candidates": [
            {"symbol": "KO", "sector": "Consumer Defensive", "composite_score": 60,
             "analyst_upside": 12.0, "max_corr_to_holdings": 0.11,
             "rationale": "Consumer Defensive diversifier"},
        ],
        "swap_suggestions": [
            {"trim_symbol": "NVDA", "fund_new_symbol": "KO",
             "rationale": "Technology is the most concentrated sector (80% of book)."},
        ],
        "existing_sector_weights": {"Technology": 0.8, "Energy": 0.2},
        "resize_state": _resize_state(),
    }
    radar.update(over)
    return radar


def _save(server, au, email: str, *, radar: dict | None = None, fp: str | None = None) -> None:
    au.set_position(email, "NVDA", 10)
    saved = {
        "_fp": fp if fp is not None else server._book_fingerprint(au.get_positions(email)),
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": []},
        "kelly": {"holdings": []},
        "monte_carlo": {},
        "book": {"positions": [], "unpriced": []},
    }
    if radar is not None:
        saved["radar"] = radar
    au.save_book_analysis(email, saved)


def _signed_in(client, monkeypatch, email="cust@x.com"):
    server, c = client
    _as(server, monkeypatch, email)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return server, c


# ─── POST /api/mybook/radar-cash ─────────────────────────────────────────────

def test_radar_cash_sizes_the_persisted_shortlist(client, monkeypatch, au):
    """The whole point: turn the cash-free radar into share counts, using the
    survivors captured at scan time — no new screen, no network."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    r = c.post("/api/mybook/radar-cash", json={"cash": 6000})

    assert r.status_code == 200
    d = r.json()["data"]
    assert d["cash"] == 6000.0
    assert d["stale"] is False
    assert d["mode"] == "BUY"
    assert d["allocations"], "sized picks expected for $6,000 of cash"
    for a in d["allocations"]:
        assert a["shares"] >= 1
        assert a["dollars"] > 0
        assert a["actionable"] is True
    assert sum(a["dollars"] for a in d["allocations"]) <= 6000.0 + 1e-6
    assert d["leftover"] >= 0


def test_radar_cash_requires_an_analysis_and_a_sane_amount(client, monkeypatch, au):
    """Mirrors /api/mybook/deploy-cash exactly: 404 before any analysis exists,
    400 for anything that is not a positive, in-range number."""
    server, c = _signed_in(client, monkeypatch)

    assert c.post("/api/mybook/radar-cash", json={"cash": 1000}).status_code == 404

    _save(server, au, "cust@x.com", radar=_radar())
    assert c.post("/api/mybook/radar-cash", json={"cash": -5}).status_code == 400
    assert c.post("/api/mybook/radar-cash", json={"cash": 0}).status_code == 400
    assert c.post("/api/mybook/radar-cash", json={"cash": 10_000_001}).status_code == 400
    assert c.post("/api/mybook/radar-cash", json={"cash": "lots"}).status_code == 400
    assert c.post("/api/mybook/radar-cash", json={}).status_code == 400


def test_radar_cash_requires_a_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    assert c.post("/api/mybook/radar-cash", json={"cash": 1000}).status_code == 401


def test_radar_cash_is_isolated_per_user(client, monkeypatch, au):
    """One customer's persisted radar must never size another customer's cash."""
    server, c = _signed_in(client, monkeypatch, "cust@x.com")
    _save(server, au, "cust@x.com", radar=_radar())

    _as(server, monkeypatch, "other@x.com")
    r = c.post("/api/mybook/radar-cash", json={"cash": 6000})

    assert r.status_code == 404
    assert "KO" not in r.text and "XOM" not in r.text


def test_radar_cash_flags_a_stale_analysis(client, monkeypatch, au):
    """Same fingerprint contract as deploy-cash and the report: sizing computed
    against a book the customer has since edited is labeled, never silently served."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar(), fp="some-other-book")

    d = c.post("/api/mybook/radar-cash", json={"cash": 6000}).json()["data"]

    assert d["stale"] is True


def test_radar_cash_never_echoes_the_private_resize_state(client, monkeypatch, au):
    """`resize_state` carries the book total and the per-sector dollar map. Only
    sized PUBLIC candidates may cross the wire — the state stays server-side."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    blob = c.post("/api/mybook/radar-cash", json={"cash": 6000}).text

    for leak in ("resize_state", "sector_value", "survivors", "_corr_among",
                 "sector_weight_after"):
        assert leak not in blob


def test_radar_cash_quotes_no_config_knobs(client, monkeypatch, au):
    """`sizing_binding_constraint` names internal config variables verbatim
    ("max_stock_per_trade", "7pct_cap", "budget"). The radar's swap rationale
    already had a config-variable name removed for exactly this reason — a
    customer cannot see or change these settings, so naming them is jargon at
    best and advice about a phantom control at worst."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    d = c.post("/api/mybook/radar-cash", json={"cash": 6000}).json()["data"]

    assert d["allocations"]
    for a in d["allocations"]:
        assert "sizing_binding_constraint" not in a
    assert "max_stock_per_trade" not in json.dumps(d)


def test_radar_cash_clamps_to_the_what_if_ceiling(client, monkeypatch, au):
    """invest_radar hard-caps the what-if slider at WHAT_IF_MAX. A request above
    it is answered with the CLAMPED figure — the echoed `cash` stays what they
    asked for so the difference is visible, never silently swallowed."""
    import invest_radar

    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    d = c.post("/api/mybook/radar-cash", json={"cash": 9_000_000}).json()["data"]

    assert d["cash"] == 9_000_000.0
    assert d["deployable_cash"] == invest_radar.WHAT_IF_MAX


def test_radar_cash_spends_no_llm_call_and_no_quota(client, monkeypatch, au):
    """Pure arithmetic on cached data. A quota check here would meter a free
    feature; an LLM call here would bill one."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    def _boom(*a, **k):
        raise AssertionError("radar-cash must never reach the LLM or the quota gate")

    monkeypatch.setattr(server, "deep_analyze_portfolio", _boom, raising=True)
    monkeypatch.setattr(server, "_check_customer_quota", _boom, raising=True)

    assert c.post("/api/mybook/radar-cash", json={"cash": 6000}).status_code == 200


def test_radar_cash_degrades_when_the_radar_engine_failed(client, monkeypatch, au):
    """The radar is best-effort inside the analysis, so a persisted row can carry
    `{"error": ...}` instead of a shortlist. That must read as 'nothing to size',
    not as a 500."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar={"error": "radar unavailable: RuntimeError"})

    r = c.post("/api/mybook/radar-cash", json={"cash": 6000})

    assert r.status_code == 200
    d = r.json()["data"]
    assert d["mode"] == "NO_ACTION"
    assert d["allocations"] == []
    assert d["leftover"] == 6000.0


def test_radar_cash_degrades_when_the_analysis_predates_the_radar(client, monkeypatch, au):
    """Analyses persisted before the radar existed carry no `radar` key at all."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=None)

    r = c.post("/api/mybook/radar-cash", json={"cash": 6000})

    assert r.status_code == 200
    assert r.json()["data"]["allocations"] == []


def test_radar_cash_survives_a_corrupt_resize_state(client, monkeypatch, au):
    """Hand-edited or truncated persisted state must degrade, never 500."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar(resize_state={"survivors": "nope"}))

    r = c.post("/api/mybook/radar-cash", json={"cash": 6000})

    assert r.status_code == 200
    assert r.json()["data"]["allocations"] == []


def test_radar_cash_allocations_carry_what_a_buyer_needs(client, monkeypatch, au):
    """An allocation the customer cannot act on is noise: symbol, price, dollars,
    shares and the reason it was picked all have to be there."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    d = c.post("/api/mybook/radar-cash", json={"cash": 6000}).json()["data"]

    a = d["allocations"][0]
    assert set(a) >= {"symbol", "sector", "price", "dollars", "shares", "rationale",
                      "composite_score", "actionable"}
    assert a["symbol"] in ("KO", "XOM")
    assert a["price"] > 0
    assert a["shares"] == int(a["dollars"] // a["price"])


# ─── /mybook/report — the radar block ────────────────────────────────────────

def test_report_renders_the_radar_block(client, monkeypatch, au):
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    r = c.get("/mybook/report")

    assert r.status_code == 200
    assert "Diversification" in r.text
    assert "Technology" in r.text and "80.0%" in r.text     # sector concentration
    assert "KO" in r.text                                   # candidate table
    assert "Consumer Defensive" in r.text
    assert "NVDA &rarr; KO" in r.text or "NVDA → KO" in r.text   # swap suggestion


def test_report_escapes_every_radar_string(client, monkeypatch, au):
    """Server-rendered HTML outside the dashboard's audited JS builders — sector
    keys, symbols and rationales are all attacker-influenceable text."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar(
        existing_sector_weights={"<script>alert('sector')</script>": 0.8},
        candidates=[{"symbol": "<script>alert('sym')</script>",
                     "sector": "<img src=x onerror=alert(1)>",
                     "composite_score": 60, "analyst_upside": 1.0,
                     "max_corr_to_holdings": 0.1,
                     "rationale": "<svg onload=alert(2)>"}],
        swap_suggestions=[{"trim_symbol": "<b>T</b>", "fund_new_symbol": "<b>F</b>",
                           "rationale": "<script>alert('swap')</script>"}],
    ))

    r = c.get("/mybook/report")

    assert r.status_code == 200
    for payload in ("<script>alert('sector')</script>", "<script>alert('sym')</script>",
                    "<img src=x onerror=alert(1)>", "<svg onload=alert(2)>",
                    "<script>alert('swap')</script>", "<b>T</b>"):
        assert payload not in r.text
    assert "&lt;script&gt;" in r.text


def test_report_without_a_radar_omits_the_block(client, monkeypatch, au):
    """Legacy analyses have no radar; the report must not grow an empty section."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=None)

    r = c.get("/mybook/report")

    assert r.status_code == 200
    assert "Diversification Radar" not in r.text


def test_report_radar_block_never_leaks_the_resize_state(client, monkeypatch, au):
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    r = c.get("/mybook/report")

    for leak in ("resize_state", "sector_value", "_corr_among", "max_stock_per_trade"):
        assert leak not in r.text


class _RadarTrap:
    """Any attribute access explodes — the report renders PERSISTED radar data and
    must never re-run the engine (it screens a live ~95-name universe)."""

    def __getattr__(self, name):
        raise AssertionError(f"the report must not touch invest_radar.{name}")


def test_report_radar_block_spends_no_llm_call(client, monkeypatch, au):
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    def _boom(*a, **k):
        raise AssertionError("the report must never spend an LLM call")

    monkeypatch.setattr(server, "deep_analyze_portfolio", _boom, raising=True)
    monkeypatch.setattr(server, "invest_radar", _RadarTrap(), raising=True)

    assert c.get("/mybook/report").status_code == 200


def test_report_radar_survives_a_hostile_shaped_payload(client, monkeypatch, au):
    """A persisted radar with wrong-typed members must not 500 a print page."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar(
        existing_sector_weights={"Technology": None},
        candidates=[{"symbol": "KO"}, "not-a-dict"],
        swap_suggestions=["nope"],
    ))

    assert c.get("/mybook/report").status_code == 200


def test_report_radar_survives_wrong_typed_containers(client, monkeypatch, au):
    """Persisted JSON is only as well-shaped as the run that wrote it: a list
    where a dict belongs (or the reverse) must not take the print page down."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar(
        existing_sector_weights=["Technology", 0.8],
        candidates={"symbol": "KO"},
        swap_suggestions={"trim_symbol": "NVDA"},
        excluded_holdings="NVDA",
    ))

    r = c.get("/mybook/report")

    assert r.status_code == 200
    assert "Sector mix unavailable" in r.text


# ─── owner isolation ─────────────────────────────────────────────────────────

_OWNER_SYMBOLS = ("BKNG", "BLK", "COF", "DELL", "JMKE", "MPC", "PANW", "REGN", "SCHW")


def test_radar_cash_answers_only_from_the_customers_own_persisted_state(
    client, monkeypatch, au
):
    """The response is derived purely from the caller's own persisted radar — no
    owner holding, and no owner equity, can reach it."""
    server, c = _signed_in(client, monkeypatch)
    _save(server, au, "cust@x.com", radar=_radar())

    body = c.post("/api/mybook/radar-cash", json={"cash": 6000}).text
    payload = json.loads(body)

    assert all(sym not in body for sym in _OWNER_SYMBOLS)
    assert "33758" not in body
    assert {a["symbol"] for a in payload["data"]["allocations"]} <= {"KO", "XOM"}
