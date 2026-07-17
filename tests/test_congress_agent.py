"""Offline tests for congress_agent — the Congress-Watch research feature.

Network-free: the public-dataset fetch is stubbed. Covers normalization, buy-only
leaderboard counting, per-symbol filtering, offline/degraded empty states, and XSS
escaping on the public page (it renders untrusted third-party disclosure strings).
"""
import pytest
import requests

import congress_agent as ca

SAMPLE = [
    {"ticker": "NVDA", "representative": "Rep A", "type": "purchase",
     "amount": "$1,001 - $15,000", "transaction_date": "2026-06-01", "disclosure_date": "2026-07-10"},
    {"ticker": "NVDA", "senator": "Sen B", "type": "purchase",
     "amount": "$15,001 - $50,000", "transaction_date": "2026-06-05", "disclosure_date": "2026-07-12"},
    {"ticker": "TSLA", "name": "Rep C", "type": "sale",
     "amount": "$1,001 - $15,000", "transaction_date": "2026-05-20", "disclosure_date": "2026-07-01"},
    {"ticker": "--", "type": "purchase"},                       # junk → dropped
    {"symbol": "AAPL", "transaction": "buy", "range": "$50k+",
     "date": "2026-06-10", "disclosure_date": "2026-07-15"},    # alt field names
]


@pytest.fixture(autouse=True)
def _clear_cache():
    ca._CACHE.clear()
    yield
    ca._CACHE.clear()


@pytest.fixture
def sample(monkeypatch):
    monkeypatch.setattr(ca, "_fetch_raw", lambda: list(SAMPLE))


def test_recent_sorted_newest_first_and_drops_junk(sample):
    out = ca.recent_activity()
    assert out["count"] == 4                          # the "--" junk row is dropped
    assert out["trades"][0]["symbol"] == "AAPL"       # disclosed 2026-07-15 = newest
    assert all(t["symbol"] not in ("--", "") for t in out["trades"])
    assert "45 days" in out["disclaimer"]             # lag note always present


def test_leaderboard_counts_buys_only(sample):
    top = {r["symbol"]: r["buy_disclosures"] for r in ca.leaderboard()["top_bought"]}
    assert top.get("NVDA") == 2                        # two purchases
    assert top.get("AAPL") == 1                        # 'buy' alias counts
    assert "TSLA" not in top                           # a sale never counts as a buy


def test_for_symbol_filters_and_is_case_insensitive(sample):
    out = ca.for_symbol("nvda")
    assert out["symbol"] == "NVDA"
    assert out["count"] == 2
    assert all(t["symbol"] == "NVDA" for t in out["trades"])


def test_offline_safe_empty(monkeypatch):
    monkeypatch.setattr(ca, "_fetch_raw", lambda: [])
    assert ca.recent_activity()["trades"] == []
    assert ca.leaderboard()["top_bought"] == []
    assert ca.for_symbol("NVDA")["trades"] == []
    # empty state still renders a valid page, never raises
    assert "No recent disclosures" in ca.render_watch_html(ca.load_watch_data())


def test_fetch_raw_degrades_on_network_error(monkeypatch):
    def boom(*_a, **_k):
        raise requests.exceptions.ConnectionError("no net")
    monkeypatch.setattr(ca.requests, "get", boom)
    assert ca._fetch_raw() == []                       # degrades to empty, no exception


def test_normalizes_fmp_camelcase_shape(monkeypatch):
    # FMP free-tier feed uses camelCase + firstName/lastName — _norm must map it.
    fmp = [{"symbol": "MSFT", "firstName": "Jane", "lastName": "Doe", "office": "Senate",
            "type": "Purchase", "amount": "$1,001 - $15,000",
            "transactionDate": "2026-06-20", "disclosureDate": "2026-07-14"}]
    monkeypatch.setattr(ca, "_fetch_raw", lambda: fmp)
    out = ca.for_symbol("MSFT")
    assert out["count"] == 1
    t = out["trades"][0]
    assert t["politician"] == "Jane Doe"
    assert t["is_buy"] is True
    assert t["tx_date"] == "2026-06-20"
    assert t["disclosed"] == "2026-07-14"


def test_public_page_escapes_untrusted_strings():
    # Disclosure data is third-party; a malicious member/name must not inject script.
    html = ca.render_watch_html({"top_bought": [], "recent": [
        {"symbol": "X", "politician": "<script>alert(1)</script>", "action": "purchase",
         "is_buy": True, "amount": "$1", "disclosed": "2026-07-10"}]})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "Congress Trade Watch" in html
