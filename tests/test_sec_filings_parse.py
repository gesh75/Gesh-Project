"""First-execution tests for SEC EDGAR Atom parse + CIK fallback.

_watch_filings is already exercised through cache/cap tests that stub
_fetch_recent_filings. The RSS parser itself was untested: a non-200, a
missing <updated>, or a filing older than the window must return [] / skip
the entry — never invent a filing. CIK resolution must fall through to
company_tickers.json (zero-padded) when the 2025+ atom shape is absent.

Offline: requests.get is stubbed. No gesh-core. Safe under --noconftest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import sec_agent as sa


@pytest.fixture(autouse=True)
def _reset_cik_cache():
    sa._TICKER_CIK_CACHE = None
    yield
    sa._TICKER_CIK_CACHE = None


class _Resp:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _atom_feed(*entries: str) -> str:
    body = "\n".join(entries)
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'


def _entry(
    *, updated: str, title: str = "10-Q - NVIDIA CORP", href: str = "https://sec.gov/f"
) -> str:
    return f'<entry><title>{title}</title><link href="{href}"/><updated>{updated}</updated></entry>'


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT12:00:00+00:00")


# ── _fetch_recent_filings ─────────────────────────────────────────────────────


def test_fetch_recent_filings_non_200_returns_empty(monkeypatch):
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _Resp(503), raising=True)
    assert sa._fetch_recent_filings("0001045810", "10-Q") == []


def test_fetch_recent_filings_exception_returns_empty(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("dns")

    monkeypatch.setattr(sa.requests, "get", boom, raising=True)
    assert sa._fetch_recent_filings("0001045810", "10-K") == []


def test_fetch_recent_filings_keeps_in_window_and_stamps_form(monkeypatch):
    xml = _atom_feed(_entry(updated=_iso_days_ago(2), href="https://sec.gov/recent"))
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    out = sa._fetch_recent_filings("0001045810", "8-K", days_back=7)
    assert len(out) == 1
    assert out[0]["form"] == "8-K"
    assert out[0]["url"] == "https://sec.gov/recent"
    assert out[0]["title"] == "10-Q - NVIDIA CORP"


def test_fetch_recent_filings_drops_older_than_cutoff(monkeypatch):
    xml = _atom_feed(
        _entry(updated=_iso_days_ago(2), href="https://sec.gov/new"),
        _entry(updated=_iso_days_ago(30), href="https://sec.gov/old"),
    )
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    out = sa._fetch_recent_filings("0001045810", "10-Q", days_back=7)
    assert [f["url"] for f in out] == ["https://sec.gov/new"]


def test_fetch_recent_filings_skips_missing_or_unparseable_updated(monkeypatch):
    xml = _atom_feed(
        "<entry><title>no date</title><link href='https://sec.gov/x'/></entry>",
        _entry(updated="not-a-date", href="https://sec.gov/bad"),
        _entry(updated=_iso_days_ago(1), href="https://sec.gov/ok"),
    )
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    out = sa._fetch_recent_filings("0001045810", "10-Q", days_back=7)
    assert [f["url"] for f in out] == ["https://sec.gov/ok"]


def test_fetch_recent_filings_missing_title_and_link_are_empty_strings(monkeypatch):
    xml = (
        '<?xml version="1.0"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<entry><updated>{_iso_days_ago(1)}</updated></entry>"
        f"<entry><title></title><updated>{_iso_days_ago(1)}</updated></entry>"
        "</feed>"
    )
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    out = sa._fetch_recent_filings("0001045810", "10-Q", days_back=7)
    assert len(out) == 2
    assert all(f["title"] == "" for f in out)
    assert all(f["url"] == "" for f in out)
    assert all(isinstance(f["title"], str) for f in out)


def test_parse_edgar_dt_rejects_garbage_and_accepts_zulu():
    assert sa._parse_edgar_dt("not-a-date") is None
    assert sa._parse_edgar_dt("") is None
    dt = sa._parse_edgar_dt("2026-07-02T13:23:16Z")
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 7, 2, 13)


# ── _get_cik fallback ─────────────────────────────────────────────────────────


def test_get_cik_falls_back_to_company_tickers_and_zero_pads(monkeypatch):
    atom = _atom_feed("<title>no cik here</title>")
    tickers = {"0": {"ticker": "NVDA", "cik_str": "1045810"}}

    def fake_get(url, **_k):
        if "company_tickers" in url:
            return _Resp(200, payload=tickers)
        return _Resp(200, atom)

    monkeypatch.setattr(sa.requests, "get", fake_get, raising=True)
    assert sa._get_cik("nvda") == "0001045810"


def test_get_cik_ticker_map_is_cached(monkeypatch):
    atom = _atom_feed("<title>no cik</title>")
    calls: list[str] = []

    def fake_get(url, **_k):
        calls.append(url)
        if "company_tickers" in url:
            return _Resp(200, payload={"0": {"ticker": "HOOD", "cik_str": "1783879"}})
        return _Resp(200, atom)

    monkeypatch.setattr(sa.requests, "get", fake_get, raising=True)
    assert sa._get_cik("HOOD") == "0001783879"
    assert sa._get_cik("HOOD") == "0001783879"
    ticker_hits = [u for u in calls if "company_tickers" in u]
    assert len(ticker_hits) == 1


def test_get_cik_ticker_map_exception_is_miss(monkeypatch):
    def fake_get(url, **_k):
        if "company_tickers" in url:
            raise RuntimeError("sec down")
        return _Resp(200, _atom_feed("<title>no cik</title>"))

    monkeypatch.setattr(sa.requests, "get", fake_get, raising=True)
    assert sa._get_cik("NVDA") is None


def test_get_cik_ticker_map_exception_does_not_poison_later_success(monkeypatch):
    """A failed company_tickers fetch must not cache {} for the process life."""
    atom = _atom_feed("<title>no cik</title>")
    state = {"fail": True}

    def fake_get(url, **_k):
        if "company_tickers" in url:
            if state["fail"]:
                raise RuntimeError("sec down")
            return _Resp(200, payload={"0": {"ticker": "NVDA", "cik_str": "1045810"}})
        return _Resp(200, atom)

    monkeypatch.setattr(sa.requests, "get", fake_get, raising=True)
    assert sa._get_cik("NVDA") is None
    state["fail"] = False
    assert sa._get_cik("NVDA") == "0001045810"


def test_get_cik_parses_cik_from_feed_id(monkeypatch):
    xml = (
        '<?xml version="1.0"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<id>https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000320193</id>"
        "</feed>"
    )
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    assert sa._get_cik("AAPL") == "0000320193"
