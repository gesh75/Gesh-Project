"""End-to-end subscriber lifecycle — the founder-invitation flow, exercised across
the real webhook → provision → magic-link → session → masked-dashboard → cancel →
lockout chain (Mission C, 8th-pass). No network: email + data seams are mocked, the
auth/billing DBs are a temp SQLite, but the auth wall, typed tokens, single-use magic
tokens, model-book masking, and revoke re-check are the REAL code paths.

This is the integration proof behind the GO/NO-GO checklist: it verifies a paying
customer can sign in and sees ONLY the masked $100K model book — never the owner's
real equity — and that cancelling immediately locks the session out.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Generator

import pytest
from starlette.testclient import TestClient

import model_portfolio

# owner sentinels that must NEVER reach a customer body. This E2E asserts the
# LIFECYCLE + masking integration on keys main already masks (portfolio_equity,
# cash); exhaustive omitted-key coverage (equity_change/extended_hours_equity/…)
# lives in tests/test_customer_leak_sweep.py.
_OWNER_TOTAL = 37522.83
_OWNER_CASH = 509.70


def _sign(raw: bytes, secret: str = "whsec_test") -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _payload(event: str, email: str, *, status: str = "active",
             variant_name: str = "Trader plan", sub_id: str = "sub_e2e",
             variant_id: str = "vid_trader",
             updated_at: str = "2026-07-05T00:00:00.000000Z") -> dict:
    attrs = {"user_email": email, "status": status, "customer_id": 4242,
             "variant_name": variant_name, "product_name": "GESH", "updated_at": updated_at,
             "variant_id": variant_id}
    return {"meta": {"event_name": event},
            "data": {"id": sub_id, "type": "subscriptions", "attributes": attrs}}


@pytest.fixture
def flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator:
    db = tmp_path / "auth.db"
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setenv("GESH_AUTH_DB", str(db))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("APP_BASE_URL", "https://gesh.test")
    monkeypatch.setenv("GESH_OWNER_EMAIL", "owner@gesh.test")   # subscriber is NOT this
    # deterministic tier mapping: strict allowlist so an ambient .env LS_VARIANT_*
    # can't flip name-based bootstrap on/off under us. The payload carries this id.
    monkeypatch.setenv("LS_VARIANT_TRADER", "vid_trader")
    for _v in ("LS_VARIANT_OBSERVER", "LS_VARIANT_DESK"):
        monkeypatch.delenv(_v, raising=False)

    import auth_users as au
    monkeypatch.setattr(au, "AUTH_DB_PATH", db)
    au.init_auth_db()

    # capture the magic link the webhook emails (no network)
    sent: list[str] = []
    import email_sender
    monkeypatch.setattr(email_sender, "send_magic_link",
                        lambda to, link, tier=None: (sent.append(link) or True))
    monkeypatch.setattr("billing.email_sender.send_magic_link", email_sender.send_magic_link)

    import server
    # deterministic model/real factor + a payload carrying owner sentinels the mask must scale
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 2.5)
    monkeypatch.setattr(server, "build_data_payload", lambda *a, **k: {
        "as_of": "2026-07-05T13:00:00+00:00", "offline": True, "source": "test",
        "equity_summary": {"portfolio_equity": _OWNER_TOTAL, "cash": _OWNER_CASH,
                           "buying_power": _OWNER_CASH},
        "holdings": [{"symbol": "NVDA", "quantity": 6.078929, "price": 194.51,
                      "equity": 101.988616, "average_buy_price": 180.0,
                      "percent_change": -3.5}],
        "watchlist": [],
    })
    monkeypatch.setattr("fetch_live_data.fetch_and_cache", lambda *a, **k: {}, raising=False)

    # https base URL so the production-realistic Secure session cookie is resent
    client = TestClient(server.app, base_url="https://testserver", raise_server_exceptions=False)
    yield {"client": client, "sent": sent, "au": au}


def _post_webhook(client, event: str, email: str, **kw) -> None:
    import billing
    payload = _payload(event, email, **kw)
    raw = json.dumps(payload).encode()
    billing.handle_webhook(raw, _sign(raw), payload)


def test_full_subscriber_lifecycle(flow):
    client, sent, au = flow["client"], flow["sent"], flow["au"]
    email = "founder@example.com"

    # 0) BEFORE sign-in: the dashboard API is walled off
    assert client.get("/api/portfolio").status_code == 401

    # 1) PAY → webhook provisions an active subscriber + emails a magic link
    _post_webhook(client, "subscription_created", email, variant_name="Trader plan")
    u = au.get_user(email)
    assert u and u["is_active"] == 1 and u["tier"] == "trader"
    assert sent, "provision did not emit a magic link"
    token = re.search(r"token=([^&\s]+)", sent[-1]).group(1)

    # 2) MAGIC LINK → single-use, mints a SESSION cookie (separate from the URL token)
    r = client.get(f"/auth/magic?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert au.COOKIE_NAME in r.cookies  # session cookie set on the client jar

    # 3) DASHBOARD → the customer sees ONLY the PUBLISHED model portfolio, never
    #    the owner brokerage payload. Since 2026-07-23 this is a distinct shape,
    #    not the owner payload with rescaled numbers (audit A-01): no
    #    `equity_summary`, no brokerage/session metadata, and a headline that is
    #    the calculated total rather than the broker's reported one.
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body.get("view") == "model_book"
    blob = json.dumps(body)
    for sentinel in (f"{_OWNER_TOTAL}", f"{_OWNER_CASH}", "37,522.83", "509.70"):
        assert sentinel not in blob, f"owner value {sentinel} leaked to a customer"
    data = body["data"]
    assert "equity_summary" not in data, "owner payload shape reached a customer"
    assert data["source_label"] == model_portfolio.SOURCE_LABEL
    assert data["model_portfolio_id"] == model_portfolio.MODEL_PORTFOLIO_ID
    # The headline is CALCULATED from the published components, never the
    # broker's reported total — assert the identity rather than a literal, so
    # this cannot pass by coincidence on a different fixture.
    assert data["positions_market_value"] + data["cash"] == pytest.approx(
        data["calculated_total_equity"], abs=0.05)
    assert sum(h["equity"] for h in data["holdings"]) == pytest.approx(
        data["positions_market_value"], abs=0.05)
    assert data["cash"] == pytest.approx(_OWNER_CASH * 2.5)   # scaled exactly once

    # 4) REPLAY the magic token → single-use, rejected
    r2 = client.get(f"/auth/magic?token={token}", follow_redirects=False)
    assert r2.status_code == 302 and "/login" in r2.headers.get("location", "")

    # 5) CANCEL → webhook revokes; the SAME session is locked out on the next request
    _post_webhook(client, "subscription_expired", email, status="expired")
    assert au.get_user(email)["is_active"] == 0
    assert client.get("/api/portfolio").status_code == 401
