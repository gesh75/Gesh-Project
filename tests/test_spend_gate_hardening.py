"""Spend-gate hardening (from the Grok external-review brief, Q5/Q6):

  #5 — a quota-metering read failure must FAIL CLOSED (deny the paid Claude call),
       never fail open into unbounded spend on a DB hiccup.
  #6 — get_tier() must not hand a tier-scaled quota multiplier to an INACTIVE
       (churned/revoked) account whose stale `tier` still lingers — revoke clears
       is_active, not tier, so the tier lookup itself must gate on is_active.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import auth_users as au
import server


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(au, "AUTH_DB_PATH", tmp_path / "auth.db")
    yield


# ── #6: get_tier active-gating ───────────────────────────────────────────────

def test_get_tier_desk_for_active_user():
    au.create_user("desk@x.com", "pw123456")
    au.set_billing("desk@x.com", tier="desk")
    assert au.get_tier("desk@x.com") == "desk"


def test_get_tier_none_for_inactive_user():
    au.create_user("churn@x.com", "pw123456")
    au.set_billing("churn@x.com", tier="desk")
    au.set_active("churn@x.com", False)                 # churn / revoke
    assert au.get_tier("churn@x.com") is None           # stale tier must not scale quota


# ── #5: quota fail-closed on metering error ──────────────────────────────────

def _customer(monkeypatch):
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False)
    monkeypatch.setenv("QUOTA_DAILY_TICKER_CARD", "25")


def test_quota_fails_closed_on_metering_error(monkeypatch):
    _customer(monkeypatch)
    monkeypatch.setattr(server.auth_users, "get_tier", lambda e: None)
    monkeypatch.setattr(server.auth_users, "usage_count_24h",
                        lambda e, ev: (_ for _ in ()).throw(RuntimeError("gesh_auth.db locked")))
    with pytest.raises(HTTPException) as ei:
        server._check_customer_quota("cust@x.com", "ticker_card")
    assert ei.value.status_code == 503                  # denied, NOT silently allowed


def test_quota_allows_under_limit(monkeypatch):
    _customer(monkeypatch)
    monkeypatch.setattr(server.auth_users, "get_tier", lambda e: "observer")
    monkeypatch.setattr(server.auth_users, "usage_count_24h", lambda e, ev: 3)
    server._check_customer_quota("cust@x.com", "ticker_card")   # under cap → no raise


def test_quota_429_at_tier_scaled_limit(monkeypatch):
    _customer(monkeypatch)
    monkeypatch.setattr(server.auth_users, "get_tier", lambda e: "desk")   # ×5 → 125
    monkeypatch.setattr(server.auth_users, "usage_count_24h", lambda e, ev: 125)
    with pytest.raises(HTTPException) as ei:
        server._check_customer_quota("cust@x.com", "ticker_card")
    assert ei.value.status_code == 429


def test_quota_owner_is_exempt(monkeypatch):
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: True)
    monkeypatch.setattr(server.auth_users, "usage_count_24h",
                        lambda e, ev: (_ for _ in ()).throw(AssertionError("owner must skip metering")))
    server._check_customer_quota("owner@x.com", "ticker_card")   # returns early, no raise
