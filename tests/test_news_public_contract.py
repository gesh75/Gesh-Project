"""GESH-7 — /api/news is mask-EXEMPT, so it needs its own no-owner-data guard.

The money mask redacts by SEMANTIC PATH (the dict key), not string shape. News headline
`title` was never enrolled as a KEEP-class key, so public analyst price targets
("Morgan Stanley raises PT to $290") were redacted to "$•••" — protecting nothing, since
the customer reads the same target on Yahoo, while making the product look broken.

Exempting the route removes it from tests/test_customer_leak_sweep.py's masking sweep, so
the protection is replaced here with something STRICTER for this shape: the payload keys
are ALLOW-LISTED. A future change that starts returning a sentiment score, an owner
position, or any other field fails this test rather than silently shipping.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import server

# Exactly the fields news_symbol() trims to — nothing else may appear.
_ALLOWED_KEYS = {"title", "url", "source", "published"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False, raising=False)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


def _stub_news(monkeypatch, items):
    import news_agent
    monkeypatch.setattr(news_agent, "_fetch_alpaca_news", lambda s: items, raising=False)
    monkeypatch.setattr(news_agent, "_fetch_all_news", lambda s: [], raising=False)


def test_public_price_targets_survive_unmasked(client, monkeypatch):
    """The whole point of the exemption: a PUBLIC analyst PT must reach the customer
    as a real number, not '$•••'."""
    _stub_news(monkeypatch, [{"title": "Morgan Stanley raises TSLA price target to $290",
                              "url": "https://example.com/a", "source": "benzinga",
                              "published": "2026-07-15"}])
    r = client.get("/api/news/TSLA")
    assert r.status_code == 200
    body = r.json()
    assert "•••" not in r.text                       # not redacted
    assert "$290" in body["data"][0]["title"]        # the real public number survives


def test_payload_keys_are_allow_listed(client, monkeypatch):
    """Shape guard replacing the masking sweep: only the four public headline fields
    may ship. Anything else (a sentiment score, an owner field) fails here."""
    _stub_news(monkeypatch, [{"title": "T", "url": "u", "source": "s", "published": "p",
                              # fields the upstream feed may carry that must NOT ship:
                              "sentiment_score": -42, "owner_position_usd": 12345}])
    r = client.get("/api/news/TSLA")
    assert r.status_code == 200
    for item in r.json()["data"]:
        extra = set(item) - _ALLOWED_KEYS
        assert not extra, f"/api/news leaked non-public field(s): {sorted(extra)}"
    assert "sentiment_score" not in r.text and "12345" not in r.text


def test_exemption_prefix_does_not_leak_to_owner_news_routes():
    """`/api/news` must not accidentally exempt the OWNER agent route
    `/api/agents/news/history` (prefix matching is `path == p or startswith(p + '/')`)."""
    assert "/api/news" in server._MASK_EXEMPT_PREFIXES
    for owner_path in ("/api/agents/news/history", "/api/agents/news"):
        assert not any(owner_path == p or owner_path.startswith(p + "/")
                       for p in server._MASK_EXEMPT_PREFIXES), owner_path


def test_empty_news_is_clean_empty_list(client, monkeypatch):
    _stub_news(monkeypatch, [])
    r = client.get("/api/news/TSLA")
    assert r.status_code == 200 and r.json()["data"] == []
