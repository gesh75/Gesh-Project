"""First-execution tests for news_agent RSS parsers.

Yahoo / Seeking Alpha / MarketWatch feeds are fail-soft: a 4xx, a body
without <item>, an empty title, or a thrown request must return [] — never
invent a headline. MarketWatch is a shared feed and must not attribute an
unrelated ticker's story to the requested symbol.

Offline: requests.get is stubbed. No gesh-core. Safe under --noconftest.
"""

from __future__ import annotations

import news_agent as na


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _rss(*items: str) -> str:
    return '<?xml version="1.0"?><rss><channel>' + "".join(items) + "</channel></rss>"


def _item(title: str, *, desc: str = "body", link: str = "https://ex/a") -> str:
    return f"<item><title>{title}</title><description>{desc}</description><link>{link}</link><pubDate>Tue, 01 Sep 2026 12:00:00 GMT</pubDate></item>"


def test_yahoo_rss_parses_titled_items(monkeypatch):
    xml = _rss(_item("NVDA beats"), _item("NVDA guide"))
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    out = na._fetch_yahoo_news("NVDA", max_items=8)
    assert [i["title"] for i in out] == ["NVDA beats", "NVDA guide"]
    assert all(i["source"] == "Yahoo Finance" for i in out)
    assert all(i["url"] == "https://ex/a" for i in out)


def test_yahoo_rss_skips_blank_title(monkeypatch):
    xml = _rss(_item(""), _item("kept"))
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    out = na._fetch_yahoo_news("NVDA")
    assert [i["title"] for i in out] == ["kept"]


def test_yahoo_rss_non_200_or_no_item_tag_is_empty(monkeypatch):
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Resp(429, "rate"), raising=True)
    assert na._fetch_yahoo_news("NVDA") == []
    monkeypatch.setattr(
        na.requests, "get", lambda *a, **k: _Resp(200, "<html>no rss</html>"), raising=True
    )
    assert na._fetch_yahoo_news("NVDA") == []


def test_yahoo_rss_exception_is_empty(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("timeout")

    monkeypatch.setattr(na.requests, "get", boom, raising=True)
    assert na._fetch_yahoo_news("NVDA") == []


def test_yahoo_rss_respects_max_items(monkeypatch):
    xml = _rss(*[_item(f"h{i}") for i in range(6)])
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    assert len(na._fetch_yahoo_news("NVDA", max_items=2)) == 2


def test_seeking_alpha_strips_html_from_description(monkeypatch):
    xml = _rss(_item("HOOD files", desc="&lt;p&gt;cash &lt;b&gt;burn&lt;/b&gt;&lt;/p&gt;"))
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    out = na._fetch_seeking_alpha_rss("hood")
    assert len(out) == 1
    assert out[0]["source"] == "Seeking Alpha"
    assert "<" not in out[0]["description"]
    assert "cash" in out[0]["description"] and "burn" in out[0]["description"]


def test_marketwatch_keeps_only_symbol_relevant_headlines(monkeypatch):
    xml = _rss(
        _item("Ford cuts outlook", desc="F guidance"),
        _item("Oil jumps on OPEC", desc="crude"),
        _item("Ford EV delay", desc="unrelated oil"),
    )
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    out = na._fetch_marketwatch_rss("F", max_items=5)
    assert [i["title"] for i in out] == ["Ford cuts outlook", "Ford EV delay"]
    assert all(i["source"] == "MarketWatch" for i in out)


def test_marketwatch_unrelated_feed_is_empty_not_misattributed(monkeypatch):
    xml = _rss(_item("Oil jumps on OPEC", desc="crude"))
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Resp(200, xml), raising=True)
    assert na._fetch_marketwatch_rss("NVDA") == []


def test_public_articles_drops_non_public_keys():
    raw = [
        {
            "title": "x",
            "url": "https://ex",
            "source": "Yahoo Finance",
            "published": "2026-09-01",
            "sentiment_score": 88,
            "owner_equity": 12345.0,
            "description": "internal",
        }
    ]
    out = na._public_articles(raw)
    assert out == [
        {"title": "x", "url": "https://ex", "source": "Yahoo Finance", "published": "2026-09-01"}
    ]
