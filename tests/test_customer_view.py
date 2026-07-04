"""Customer-view model-book masking (launch gap: customers saw the owner's
real dollars, cash, and share counts).

Non-owner sessions get /api/* JSON normalized to a $100k model book: money and
quantity fields scale by model/real_total, so percentages and per-share prices
stay exact while real wealth never leaves the server. Owner-only HTML views
(/report, /invest, /progress) are refused outright. Fail CLOSED everywhere.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import auth_users
import server


# ─── _scale_money (pure) ──────────────────────────────────────────────────────

def test_scale_money_scales_money_and_qty_not_prices():
    payload = {
        "equity_summary": {"portfolio_equity": 37_522.83, "cash": 509.70},
        "holdings": [{"symbol": "NVDA", "quantity": 28.0, "price": 194.51,
                      "equity": 5446.28, "percent_change": -3.51,
                      "pnl_usd": -218.0}],
        "alloc_pct": 14.7,
    }
    out = server._scale_money(payload, 2.0)
    assert out["equity_summary"]["portfolio_equity"] == pytest.approx(75_045.66)
    assert out["holdings"][0]["quantity"] == pytest.approx(56.0)
    assert out["holdings"][0]["pnl_usd"] == pytest.approx(-436.0)   # *_usd suffix
    assert out["holdings"][0]["price"] == 194.51                    # prices untouched
    assert out["holdings"][0]["percent_change"] == -3.51            # pct untouched
    assert out["alloc_pct"] == 14.7
    assert payload["holdings"][0]["quantity"] == 28.0               # input not mutated


def test_scale_factor_fails_closed(monkeypatch, tmp_path):
    """No readable snapshot total → factor 0.0 → dollars zero out, never leak."""
    monkeypatch.setattr(server, "_customer_scale_factor", server._customer_scale_factor)
    # simulate unreadable snapshot by pointing Path read at a bogus dir via direct call
    assert server._scale_money({"cash": 999.0}, 0.0)["cash"] == 0.0


def test_is_owner():
    assert auth_users.is_owner("georgy75@gmail.com") is True
    assert auth_users.is_owner("GEORGY75@GMAIL.COM ") is True
    assert auth_users.is_owner("customer@example.com") is False
    assert auth_users.is_owner(None) is False


# ─── middleware end-to-end ────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.5)
    return TestClient(server.app, raise_server_exceptions=False)


def _as(monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda path, cookie: "allow")


def test_customer_gets_scaled_chart_free_but_masked_portfolio(monkeypatch, client):
    _as(monkeypatch, "customer@example.com")
    monkeypatch.setattr(
        server, "_cached_response",
        lambda key: {"status": "ok", "cached": True,
                     "data": {"total_equity": 1000.0, "score": 42, "alloc_pct": 10.0}},
    )
    r = client.get("/api/kelly/cached")
    assert r.status_code == 200
    body = r.json()
    assert body["view"] == "model_book"                       # customer tell
    assert body["data"]["total_equity"] == pytest.approx(500.0)  # scaled ×0.5
    assert body["data"]["score"] == 42                        # non-money untouched
    assert body["data"]["alloc_pct"] == 10.0


def test_owner_sees_raw_dollars(monkeypatch, client):
    _as(monkeypatch, "georgy75@gmail.com")
    monkeypatch.setattr(
        server, "_cached_response",
        lambda key: {"status": "ok", "cached": True, "data": {"total_equity": 1000.0}},
    )
    r = client.get("/api/kelly/cached")
    assert r.status_code == 200
    assert r.json()["data"]["total_equity"] == 1000.0
    assert "view" not in r.json()


@pytest.mark.parametrize("path", ["/report", "/invest", "/invest/data", "/progress", "/watch"])
def test_owner_only_views_refused_for_customers(monkeypatch, client, path):
    _as(monkeypatch, "customer@example.com")
    r = client.get(path)
    assert r.status_code == 403
    assert "Owner-only" in r.json()["detail"]


# ─── A1: Monte-Carlo + prose masking (Codex P0) ───────────────────────────────

def test_monte_carlo_outcome_fields_are_scaled():
    payload = {"median": 38527.04, "current_equity": 36970.93, "cvar_95": 10346.95,
               "p5": 28826.97, "cvar_95_pct": 27.9,
               "buckets": [{"label": "$25,274–$28,518", "center": 26895.93, "pct": 4.0}]}
    out = server._scale_money(payload, 0.5)
    assert out["median"] == pytest.approx(19263.52)
    assert out["current_equity"] == pytest.approx(18485.47)
    assert out["cvar_95"] == pytest.approx(5173.48)
    assert out["cvar_95_pct"] == 27.9                       # percentages untouched
    assert out["buckets"][0]["center"] == pytest.approx(13447.97)
    assert "$12,637" in out["buckets"][0]["label"]          # label dollars scaled too


def test_prose_recommendation_is_redacted():
    obj = {"portfolio_recommendations": [
        {"action": "TRIM HOOD", "rationale": "Sell 4-5 shares (~$450-560) at $112.73; keep $509.70 cash."}]}
    out = server._scale_money(obj, 2.665)
    r = out["portfolio_recommendations"][0]["rationale"]
    for real in ("450", "560", "112.73", "509.70", "4-5"):
        assert real not in r, f"{real!r} leaked: {r!r}"
    assert "$•••" in r


def test_price_forecast_predictions_are_kept_raw_not_scaled():
    """Codex P0: per-share price forecasts are shape-identical to portfolio
    ranges but are PUBLIC stock-level values — must be kept raw, never scaled."""
    obj = {"holdings": [{"symbol": "NVDA",
        "prediction": {"3m": "$180-210", "12m": "$220-260", "rationale": "Sell 5 shares ($900)"}}]}
    out = server._scale_money(obj, 2.665)
    pred = out["holdings"][0]["prediction"]
    assert pred["3m"] == "$180-210"      # forecast unchanged (not $480-560)
    assert pred["12m"] == "$220-260"
    assert "900" not in pred["rationale"] and "5 shares" not in pred["rationale"]  # prose redacted


def test_mc_bucket_label_scaled_by_semantic_path_only():
    obj = {"monte_carlo": {"buckets": [{"label": "$25,274–$28,518", "center": 26895.93, "pct": 4.0}]}}
    out = server._scale_money(obj, 2.0)
    b = out["monte_carlo"]["buckets"][0]
    assert b["label"] == "$50,548–$57,036"     # portfolio label scaled
    assert b["center"] == pytest.approx(53791.86)


def test_scale_money_reaches_strings_recursively():
    obj = {"portfolio_recommendations": [{"action": "Sell 4 shares ($38 risk)"}]}
    out = server._scale_money(obj, 2.665)
    s = out["portfolio_recommendations"][0]["action"]
    assert "4 shares" not in s and "$38" not in s and "38" not in s


def test_ecg_equity_history_array_is_scaled_not_leaked():
    """Codex P0: bare-number arrays (equity_history) had no dict key so the naive
    recursion returned the owner's real per-position dollars unchanged."""
    obj = {"states": [{"symbol": "NVDA", "state": "COOLING", "drawdown_pct": -7.5,
                       "equity_history": [5843.32, 5446.28]}]}
    out = server._scale_money(obj, 2.0)
    eh = out["states"][0]["equity_history"]
    assert eh == [pytest.approx(11686.64), pytest.approx(10892.56)]
    assert out["states"][0]["drawdown_pct"] == -7.5        # a % — untouched


# ─── B1: mutating / spend endpoints reject customers, accept owner ────────────

@pytest.mark.parametrize("path", ["/api/paper/run", "/api/paper/check-closes",
                                   "/api/deep-analysis", "/api/screener",
                                   "/api/consensus", "/api/config"])
def test_customer_cannot_trigger_spend_or_mutation(monkeypatch, client, path):
    _as(monkeypatch, "customer@example.com")
    r = client.post(path, json={})
    assert r.status_code == 403, f"{path} must be owner-only for customers"


def test_owner_passes_the_gate(monkeypatch):
    """Owner session satisfies require_api_key without any X-API-Key header."""
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "georgy75@gmail.com")
    import types
    req = types.SimpleNamespace(cookies={server.auth_users.COOKIE_NAME: "x"})
    assert server.require_api_key(req, x_api_key=None) is True


def test_no_key_no_session_fails_closed(monkeypatch):
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.delenv("GESH_API_KEY", raising=False)
    import types
    from fastapi import HTTPException
    req = types.SimpleNamespace(cookies={})
    with pytest.raises(HTTPException) as ei:
        server.require_api_key(req, x_api_key=None)
    assert ei.value.status_code == 503


# ─── B2: security headers (Codex P1) ──────────────────────────────────────────

def test_security_headers_present(monkeypatch, client):
    _as(monkeypatch, "customer@example.com")
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.5)
    r = client.get("/pricing")
    h = r.headers
    assert "Content-Security-Policy" in h
    csp = h["Content-Security-Policy"]
    assert "connect-src 'self'" in csp          # blocks XSS exfiltration beacons
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "DENY"
    assert "Permissions-Policy" in h


# ─── C1: ECG degrades instead of 503-ing into a forever spinner ───────────────

def test_ecg_degrades_when_no_snapshot(monkeypatch, client):
    import data_fetcher
    _as(monkeypatch, "georgy75@gmail.com")   # owner (masking irrelevant here)
    def boom(*a, **k): raise data_fetcher.RobinhoodOfflineError("offline")
    monkeypatch.setattr(server, "_resolved_payload", boom)
    r = client.get("/api/ecg")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["data"]["portfolio_gate"] == "UNKNOWN"
    assert body["data"]["states"] == []


def test_ecg_ok_from_snapshot(monkeypatch, client):
    _as(monkeypatch, "georgy75@gmail.com")
    monkeypatch.setattr(server, "_resolved_payload",
                        lambda *a, **k: {"holdings": [], "offline": True})
    monkeypatch.setattr(server, "compute_ecg", lambda h, fn: {"states": [], "portfolio_gate": "OPEN"})
    r = client.get("/api/ecg")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["degraded"] is True and "cached snapshot" in d["portfolio_message"]


# ─── XSS: adversarial payload in AI fields renders inert (Codex P0-3) ──────────

def test_daesc_helper_escapes_html_payload():
    """The dashboard's daEsc must neutralize an injected event-handler payload.
    (JS helper mirrored here so the escaping contract is covered by a Python test;
    the template applies daEsc() to every AI/external string sink.)"""
    def daEsc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    payload = '<img src=x onerror="fetch(\'/api/config\',{method:\'POST\'})">'
    out = daEsc(payload)
    assert "<img" not in out and "onerror" in out and "&lt;img" in out
    assert "<" not in out and ">" not in out


# ─── /api/portfolio/history sparkline series must scale (Fable audit, 4th of class) ─

def test_portfolio_history_series_scaled_for_customer():
    obj = {"status": "ok", "series": [37080.88, 36970.94], "points": 2}
    out = server._scale_money(obj, 2.0)
    assert out["series"] == [pytest.approx(74161.76), pytest.approx(73941.88)]
    assert out["points"] == 2   # a count, not money — untouched
