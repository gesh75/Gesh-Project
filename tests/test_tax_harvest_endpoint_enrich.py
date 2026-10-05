"""M-03: /api/tax/harvest must not read 'clear' on purchase-side wash risk it never checked.

The base scan only sees prior harvest SALES; purchases inside the 30-day window need the
lot-level `tax_lots_enrich.enrich`. That loader reads the OWNER's Robinhood lots, so it is
OWNER-ONLY: a customer's context is the published model portfolio, and enriching it from the
owner's lots would leak owner lot dates/cost. Customers instead get an explicit
`purchase_wash_check: "not_checked"` so a missing check cannot read as "no warnings".
"""

from __future__ import annotations

import asyncio
import types

import pytest

import server

BASE = {"eligible": [], "in_wash_window": [], "no_cost_basis": []}
HOLDINGS = [{"symbol": "NVDA", "equity": 900.0, "current_price": 90.0, "average_buy_price": 100.0}]


@pytest.fixture
def harvest(monkeypatch):
    calls = {"enrich": 0}

    monkeypatch.setattr(server, "_context_payload", lambda request: {"holdings": HOLDINGS})
    monkeypatch.setattr("tax_harvester.get_harvest_summary", lambda *a, **k: dict(BASE), raising=False)

    def fake_enrich(report, holdings, **_k):
        calls["enrich"] += 1
        return {**report, "purchase_wash_warnings": [{"ticker": "NVDA"}],
                "lot_coverage": {"checked": 1, "available": 1, "unavailable": 0}}

    monkeypatch.setattr("tax_lots_enrich.enrich", fake_enrich, raising=False)
    return calls


def _call(monkeypatch, *, owner: bool) -> dict:
    monkeypatch.setattr(server, "_is_owner_request", lambda request: owner)
    return asyncio.run(server.api_tax_harvest(types.SimpleNamespace()))


def test_owner_gets_lot_level_purchase_wash_warnings(monkeypatch, harvest):
    out = _call(monkeypatch, owner=True)
    assert harvest["enrich"] == 1
    assert out["purchase_wash_warnings"] == [{"ticker": "NVDA"}]
    assert out["lot_coverage"]["checked"] == 1


def test_customer_is_never_enriched_from_owner_lots(monkeypatch, harvest):
    out = _call(monkeypatch, owner=False)
    assert harvest["enrich"] == 0                      # no owner-lot read for a customer
    assert "purchase_wash_warnings" not in out
    assert out["purchase_wash_check"] == "not_checked"
    assert "purchase" in out["purchase_wash_note"].lower()


def test_owner_enrich_failure_degrades_to_not_checked_not_to_clear(monkeypatch, harvest):
    def boom(*_a, **_k):
        raise RuntimeError("mcp down")

    monkeypatch.setattr("tax_lots_enrich.enrich", boom, raising=False)
    out = _call(monkeypatch, owner=True)
    assert out["status"] == "ok"
    assert out["purchase_wash_check"] == "not_checked"
    assert "purchase_wash_warnings" not in out
