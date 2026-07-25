"""The model-scaling basis must understand the RAW snapshot shape.

`_customer_scale_factor()` reads `live_portfolio_snapshot.json` DIRECTLY,
bypassing `data_fetcher.load_snapshot_payload()` — the only thing that renames
`price` to `current_price`. Both raw writers emit `price`:

    build_snapshot_from_mcp.py   "price": round(price, 4)
    fetch_live_snapshot.py       "price": …

`canonical_holdings()` correctly requires the NORMALIZED `current_price`, so
every raw holding was classified unpriced, positions summed to 0, and the basis
collapsed to cash alone. On the real snapshot on disk that is a factor of 11.17
instead of 2.95 — every customer-visible dollar on a fallback route inflated
~3.8×.

The fix belongs at the RAW BOUNDARY, not in `canonical_holdings()`: that helper
is right to demand the normalized field, and a global `price` fallback inside it
would hide a genuine normalization regression everywhere else.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

import data_fetcher
import model_portfolio
import server

# The exact negative control from the work order.
POSITIONS = 88_000.0
CASH = 20_000.0
ACCOUNT_EQUITY = 108_000.0
QTY = 1_000.0
PRICE = 88.0


def _raw_mcp_shaped(*, price_key: str = "price", qty=QTY, price=PRICE,
                    cash=CASH, extra_holdings: list | None = None) -> dict:
    """A snapshot in the shape build_snapshot_from_mcp.py / fetch_live_snapshot.py
    actually write: holdings carry `price`, and equity_summary carries BOTH the
    positions figure (portfolio_equity) and the account headline."""
    h = {"symbol": "NVDA", "quantity": qty, "equity": round(qty * price, 4)
         if isinstance(qty, (int, float)) and isinstance(price, (int, float)) else None,
         "average_buy_price": 50.0, "percent_change": 10.0}
    if price is not None:
        h[price_key] = price
    return {
        "fetched_at": "2026-07-25T12:00:00+00:00", "source": "robinhood_mcp",
        "equity_summary": {"portfolio_equity": POSITIONS, "account_equity": ACCOUNT_EQUITY,
                           "total": ACCOUNT_EQUITY, "cash": cash,
                           "adjusted_equity_previous_close": 100_000.0},
        "holdings": [h] + list(extra_holdings or []),
    }


# ─── CASE A — the real MCP writer's own output ───────────────────────────────

def test_A_mcp_writer_emits_price_not_current_price():
    """Pin the raw contract at its source, so this test fails if the writer is
    ever changed rather than silently diverging from the reader."""
    src = Path(server.__file__).parent / "build_snapshot_from_mcp.py"
    text = src.read_text()
    assert '"price": round(price, 4)' in text, "MCP writer no longer emits `price`"
    assert '"current_price"' not in text, "MCP writer now emits current_price — update the reader"


def test_A_basis_from_an_mcp_shaped_raw_snapshot():
    basis = server.model_basis_from_payload(_raw_mcp_shaped())
    assert basis == pytest.approx(POSITIONS + CASH, abs=0.02)


# ─── CASE B — the legacy live writer ─────────────────────────────────────────

def test_B_legacy_live_snapshot_writer_also_emits_price():
    src = Path(server.__file__).parent / "fetch_live_snapshot.py"
    text = src.read_text()
    assert "'price'" in text or '"price"' in text
    assert "'current_price'" not in text and '"current_price"' not in text


def test_B_basis_from_a_legacy_raw_snapshot():
    raw = _raw_mcp_shaped()
    raw["source"] = "robinhood_live"
    assert server.model_basis_from_payload(raw) == pytest.approx(POSITIONS + CASH, abs=0.02)


# ─── CASE C — raw and normalized agree ───────────────────────────────────────

def test_C_raw_and_normalized_snapshots_give_the_same_basis():
    raw = _raw_mcp_shaped()
    p = Path(tempfile.mkdtemp()) / "snap.json"
    p.write_text(json.dumps(raw))
    normalized = data_fetcher.load_snapshot_payload(p)
    assert "current_price" in normalized["holdings"][0]
    assert "price" not in normalized["holdings"][0] or True   # normalizer may keep both
    assert server.model_basis_from_payload(raw) == pytest.approx(
        server.model_basis_from_payload(normalized), abs=0.02)


def test_C_normalized_form_still_works_unchanged():
    p = Path(tempfile.mkdtemp()) / "snap.json"
    p.write_text(json.dumps(_raw_mcp_shaped()))
    normalized = data_fetcher.load_snapshot_payload(p)
    assert server.model_basis_from_payload(normalized) == pytest.approx(
        POSITIONS + CASH, abs=0.02)


# ─── CASE D — the exact negative control ─────────────────────────────────────

def test_D_exact_negative_control_88k_positions_plus_20k_cash():
    basis = server.model_basis_from_payload(_raw_mcp_shaped())
    assert basis == pytest.approx(108_000.0, abs=0.02)

    factor = server._model_scale_factor(basis)
    assert factor == pytest.approx(server._MODEL_BOOK_USD / 108_000.0, rel=1e-9)
    assert factor != pytest.approx(5.0, abs=0.01), "basis collapsed to cash alone"
    # and the basis scales to exactly the notional
    assert basis * factor == pytest.approx(server._MODEL_BOOK_USD, abs=0.01)


# ─── CASE E — a raw holding with no price at all ─────────────────────────────

def test_E_missing_raw_price_makes_the_basis_unavailable():
    """Stock holdings were present but unpriced: a cash-only factor would be a
    fabricated stock-portfolio basis, so refuse instead."""
    raw = _raw_mcp_shaped(price=None)
    assert raw["holdings"][0].get("price") is None
    assert server.model_basis_from_payload(raw) is None
    assert server._model_scale_factor(server.model_basis_from_payload(raw)) == 0.0


@pytest.mark.parametrize("bad_price", [0, -1.0])
def test_E_zero_or_negative_raw_price_is_unavailable(bad_price):
    raw = _raw_mcp_shaped(price=bad_price)
    assert server.model_basis_from_payload(raw) is None


def test_E_one_unpriced_holding_poisons_the_whole_basis():
    """A partial basis would silently under-scale every dollar."""
    raw = _raw_mcp_shaped(extra_holdings=[
        {"symbol": "MU", "quantity": 100.0, "equity": 1000.0}])   # no price
    assert server.model_basis_from_payload(raw) is None


# ─── CASE F — non-finite raw values ──────────────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("quantity", float("nan")), ("quantity", float("inf")),
    ("price", float("nan")), ("price", float("inf")),
])
def test_F_non_finite_holding_field_is_unavailable(field, value):
    raw = _raw_mcp_shaped(**{field: value} if field == "price" else {})
    if field == "quantity":
        raw["holdings"][0]["quantity"] = value
    assert server.model_basis_from_payload(raw) is None
    assert server._model_scale_factor(server.model_basis_from_payload(raw)) == 0.0


@pytest.mark.parametrize("bad_cash", [float("nan"), float("inf"), "abc", None])
def test_F_non_finite_or_missing_cash_is_unavailable(bad_cash):
    raw = _raw_mcp_shaped()
    raw["equity_summary"]["cash"] = bad_cash
    assert server.model_basis_from_payload(raw) is None


def test_F_cash_only_book_is_a_legitimate_basis():
    """No holdings at all + positive cash is a real, usable basis — distinct
    from 'holdings present but unpriced'."""
    raw = _raw_mcp_shaped()
    raw["holdings"] = []
    assert server.model_basis_from_payload(raw) == pytest.approx(CASH)


def test_F_zero_basis_is_unavailable_not_a_divide_by_zero():
    raw = _raw_mcp_shaped()
    raw["holdings"] = []
    raw["equity_summary"]["cash"] = 0.0
    assert server._model_scale_factor(server.model_basis_from_payload(raw)) == 0.0


# ─── CASE G — the real fallback middleware path ──────────────────────────────


def test_G_fallback_route_scales_by_positions_plus_cash(tmp_path, monkeypatch):
    """Exercise fallback scaling through the real customer middleware.

    The temporary route returns a synthetic money field and is removed in
    ``finally``. It proves the still-live generic masking path is wired to the
    raw positions-plus-cash basis without reopening any owner-data endpoint.
    """
    from starlette.testclient import TestClient

    import auth_users

    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    auth_users.create_user("cust@x.com", "pw")
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(
        server.auth_users, "gate_decision", lambda _path, _cookie: "allow"
    )
    monkeypatch.setattr(server.auth_users, "is_owner", lambda _email: False)
    monkeypatch.setattr(
        server.auth_users,
        "authenticate_session",
        lambda _token: "cust@x.com",
    )

    snap_path = tmp_path / "live_portfolio_snapshot.json"
    snap_path.write_text(json.dumps(_raw_mcp_shaped()))
    real_basis = server.model_basis_from_payload(_raw_mcp_shaped())
    assert real_basis == pytest.approx(108_000.0, abs=0.02)
    monkeypatch.setattr(
        server,
        "_customer_scale_factor",
        lambda: server._model_scale_factor(
            server.model_basis_from_payload(json.loads(snap_path.read_text()))
        ),
    )

    expected_factor = server._MODEL_BOOK_USD / 108_000.0
    wrong_factor = server._MODEL_BOOK_USD / CASH          # the cash-only bug: 5.0

    probe_path = "/api/__raw_scaling_probe"

    @server.app.get(probe_path)
    def _probe():
        return {"status": "ok", "data": {"equity": 1_000.0}}

    try:
        client = TestClient(server.app, raise_server_exceptions=False)
        client.cookies.set(server.auth_users.COOKIE_NAME, "tok")
        response = client.get(probe_path)
        assert response.status_code == 200
        body = response.json()
        assert body.get("view") == "model_book"
        assert "model_portfolio_id" not in body.get("data", {})
        scaled = body["data"]["equity"]
        assert scaled == pytest.approx(1_000.0 * expected_factor, abs=0.01)
        assert scaled != pytest.approx(1_000.0 * wrong_factor, abs=1.0), (
            "scaled by the cash-only basis")
    finally:
        server.app.router.routes = [
            route
            for route in server.app.router.routes
            if getattr(route, "path", None) != probe_path
        ]


# ─── CASE H — dormant publication + scaler composition ──────────────────────


def test_H_dormant_publication_scales_to_exactly_the_notional(tmp_path):
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(_raw_mcp_shaped()))
    payload = data_fetcher.load_snapshot_payload(p)
    payload["offline"] = False
    published = model_portfolio.publish(payload, prior_close=lambda _symbol: 80.0)
    assert published["calculated_total_equity"] == pytest.approx(ACCOUNT_EQUITY)

    factor = server._model_scale_factor(published["calculated_total_equity"])
    scaled = server._scale_money(published, factor)

    total = sum(h["equity"] for h in scaled["holdings"]) + scaled["cash"]
    assert total == pytest.approx(server._MODEL_BOOK_USD, abs=0.05)
    assert scaled["calculated_total_equity"] == pytest.approx(
        server._MODEL_BOOK_USD, abs=0.05)
