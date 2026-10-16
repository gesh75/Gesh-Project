"""Offline tests for congress_agent — the Congress-Watch research feature.

Network-free: the public-dataset fetch is stubbed. Covers normalization, buy-only
leaderboard counting, per-symbol filtering, offline/degraded empty states,
malformed-row tolerance, dedup, owner attribution, stale-if-error caching,
single-flight/backoff, and XSS escaping on the public page (it renders untrusted
third-party disclosure strings).
"""
import threading
import time

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
    ca._STATE.update(last_attempt=0.0, degraded=False, last_error=None)
    yield
    ca._CACHE.clear()
    ca._STATE.update(last_attempt=0.0, degraded=False, last_error=None)


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
    # A source IS configured here, so requests.get is genuinely exercised — the
    # test proves the exception path degrades to empty instead of passing
    # trivially on the unconfigured state (the old false positive).
    monkeypatch.setattr(ca, "_FMP_KEY", "")
    monkeypatch.setattr(ca, "_SOURCES", ("https://invalid.example/feed",))
    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise requests.exceptions.ConnectionError("no net")

    monkeypatch.setattr(ca.requests, "get", boom)
    assert ca._fetch_raw() == []                       # degrades to empty, no exception
    assert calls, "patched requests.get was never called — test would be a false positive"
    st = ca.source_status()
    assert st["configured"] is True
    assert st["degraded"] is True                      # outage is flagged, not silent


def test_normalizes_fmp_camelcase_shape(monkeypatch):
    # FMP feed uses camelCase + firstName/lastName — _norm must map it.
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
    assert t["owner"] == ""                            # absent owner → empty, not missing key


def test_fmp_endpoints_cover_senate_and_house():
    # Senate and House are SEPARATE FMP feeds — configuring only Senate omits
    # every House trade while the product says "Congress".
    urls = " ".join(ca._FMP_ENDPOINTS)
    assert "senate" in urls
    assert "house" in urls


def test_malformed_row_never_crashes(monkeypatch):
    rows = [
        {"symbol": "X", "type": "Purchase", "firstName": 7, "lastName": "Doe"},
        {"symbol": "MSFT", "type": "Purchase", "firstName": "Jane", "lastName": "Doe",
         "transactionDate": "2026-06-20", "disclosureDate": "2026-07-14"},
        "not-a-dict",
        {"symbol": "BADCOUNT", "type": {"weird": 1}},
    ]
    monkeypatch.setattr(ca, "_fetch_raw", lambda: rows)
    out = ca.recent_activity()                         # must not raise
    syms = [t["symbol"] for t in out["trades"]]
    assert "MSFT" in syms and "X" in syms              # coerced rows survive
    assert "not-a-dict" not in syms                    # non-dict junk dropped
    assert all(isinstance(t["politician"], str) for t in out["trades"])
    assert ca.leaderboard()                            # no raise
    assert "Congress Trade Watch" in ca.render_watch_html(ca.load_watch_data())


def test_owner_preserved_and_shown_on_page(monkeypatch):
    # A spouse's trade is reported on the member's filing — never implied to be
    # the member's own trade.
    rows = [{"symbol": "MSFT", "firstName": "Jane", "lastName": "Doe", "owner": "Spouse",
             "type": "Purchase", "transactionDate": "2026-06-20", "disclosureDate": "2026-07-14"}]
    monkeypatch.setattr(ca, "_fetch_raw", lambda: rows)
    t = ca.recent_activity()["trades"][0]
    assert t["owner"] == "Spouse"
    assert ca._filer_label(t) == "Jane Doe (spouse)"
    html = ca.render_watch_html(ca.load_watch_data())
    assert "Jane Doe (spouse)" in html
    assert "reported on the named member" in html      # attribution note


def test_duplicate_rows_deduped(monkeypatch):
    row = {"symbol": "NVDA", "firstName": "A", "lastName": "B", "type": "Purchase",
           "amount": "$1,001 - $15,000", "transactionDate": "2026-06-01",
           "disclosureDate": "2026-07-10"}
    monkeypatch.setattr(ca, "_fetch_raw", lambda: [row, dict(row), dict(row)])
    assert ca.recent_activity()["count"] == 1
    top = {r["symbol"]: r["buy_disclosures"] for r in ca.leaderboard()["top_bought"]}
    assert top["NVDA"] == 1                            # not 3


def test_stale_if_error_keeps_last_good_data(monkeypatch):
    good = [{"symbol": "MSFT", "type": "Purchase", "firstName": "J", "lastName": "D",
             "transactionDate": "2026-06-20", "disclosureDate": "2026-07-14"}]
    ca._CACHE["_raw"] = (time.monotonic() - 7200, time.time() - 7200, good)  # expired
    monkeypatch.setattr(ca, "_FMP_KEY", "k")
    monkeypatch.setattr(ca, "_fetch_from_sources", lambda: [])                # outage
    assert ca._fetch_raw() == good                                            # stale served
    st = ca.source_status()
    assert st["degraded"] is True
    assert st["fetched_at"] is not None


def test_retry_backoff_limits_upstream_attempts(monkeypatch):
    good = [{"symbol": "MSFT", "type": "Purchase"}]
    ca._CACHE["_raw"] = (time.monotonic() - 7200, time.time() - 7200, good)
    calls = []

    def failing():
        calls.append(1)
        return []

    monkeypatch.setattr(ca, "_fetch_from_sources", failing)
    assert ca._fetch_raw() == good
    assert ca._fetch_raw() == good
    assert len(calls) == 1                   # second call is inside the backoff window


def test_concurrent_cold_requests_collapse_to_one_fetch(monkeypatch):
    calls = []
    barrier = threading.Barrier(6)

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return [{"symbol": "MSFT", "type": "Purchase"}]

    monkeypatch.setattr(ca, "_fetch_from_sources", slow)

    def worker():
        barrier.wait()
        assert ca._fetch_raw()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    barrier.wait()
    for t in threads:
        t.join()
    assert len(calls) == 1                   # single-flight, not N upstream calls


def test_source_status_unconfigured(monkeypatch):
    monkeypatch.setattr(ca, "_FMP_KEY", "")
    monkeypatch.setattr(ca, "_SOURCES", ())
    st = ca.source_status()
    assert st["configured"] is False
    assert st["degraded"] is False
    assert st["chambers"] == []
    html = ca.render_watch_html({"top_bought": [], "recent": [], "status": st})
    assert "No disclosure source configured" in html


def test_page_shows_degraded_state_not_fake_empty():
    st = {"configured": True, "source": "fmp", "chambers": ["senate", "house"],
          "fetched_at": "2026-07-17 10:00 UTC", "degraded": True, "note": "outage"}
    html = ca.render_watch_html({"top_bought": [], "recent": [], "status": st})
    assert "degraded" in html.lower()
    assert "2026-07-17 10:00 UTC" in html


def test_filing_link_rendered_only_when_http(monkeypatch):
    rows = [{"symbol": "MSFT", "type": "Purchase", "firstName": "J", "lastName": "D",
             "link": "https://efd.senate.gov/filing/123", "disclosureDate": "2026-07-14"}]
    monkeypatch.setattr(ca, "_fetch_raw", lambda: rows)
    assert "https://efd.senate.gov/filing/123" in ca.render_watch_html(ca.load_watch_data())
    # a javascript: URL must never become an anchor
    bad = [dict(rows[0], link="javascript:alert(1)")]
    monkeypatch.setattr(ca, "_fetch_raw", lambda: bad)
    assert "javascript:alert(1)" not in ca.render_watch_html(ca.load_watch_data())


def test_public_page_escapes_untrusted_strings():
    # Disclosure data is third-party; a malicious member/name must not inject script.
    html = ca.render_watch_html({"top_bought": [], "recent": [
        {"symbol": "X", "politician": "<script>alert(1)</script>", "action": "purchase",
         "is_buy": True, "amount": "$1", "disclosed": "2026-07-10"}]})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "Congress Trade Watch" in html


# ─── env resolution is dynamic (Phase-3 hardening) ───────────────────────────────

def test_env_key_is_read_at_call_time_without_reload(monkeypatch):
    """Rotating FMP_API_KEY / CONGRESS_DATA_URL in the environment must take effect
    on the next call — no module reload, no restart."""
    monkeypatch.setattr(ca, "_FMP_KEY", None)        # no module-level override
    monkeypatch.setattr(ca, "_SOURCES", None)
    monkeypatch.setenv("FMP_API_KEY", "  rotated-key  ")
    monkeypatch.setenv("CONGRESS_DATA_URL", "https://mirror.example/feed.json")
    assert ca._fmp_key() == "rotated-key"
    assert ca._sources() == ("https://mirror.example/feed.json",)
    st = ca.source_status()
    assert st["configured"] is True and st["source"] == "fmp"
    monkeypatch.delenv("FMP_API_KEY")
    monkeypatch.delenv("CONGRESS_DATA_URL")
    assert ca._fmp_key() == "" and ca._sources() == ()
    assert ca.source_status()["configured"] is False


def test_fetch_uses_the_current_env_key(monkeypatch):
    monkeypatch.setattr(ca, "_FMP_KEY", None)
    monkeypatch.setattr(ca, "_SOURCES", None)
    monkeypatch.setenv("FMP_API_KEY", "live-k2")
    urls = []

    class _R:
        ok = True
        def json(self): return []

    monkeypatch.setattr(ca.requests, "get", lambda url, **k: urls.append(url) or _R())
    ca._fetch_from_sources()
    assert urls and all(u.endswith("apikey=live-k2") for u in urls)


def test_module_attribute_override_still_wins(monkeypatch):
    """Existing tests pin ca._FMP_KEY / ca._SOURCES directly; a monkeypatched module
    attribute must beat whatever the ambient environment (or a loaded .env) says."""
    monkeypatch.setenv("FMP_API_KEY", "ambient")
    monkeypatch.setenv("CONGRESS_DATA_URL", "https://ambient.example/x")
    monkeypatch.setattr(ca, "_FMP_KEY", "")
    monkeypatch.setattr(ca, "_SOURCES", ())
    assert ca._fmp_key() == "" and ca._sources() == ()
    assert ca.source_status()["configured"] is False
