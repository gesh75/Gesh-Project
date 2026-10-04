"""Shared fixtures/helpers for the SEO test modules (not collected as tests)."""

from __future__ import annotations

import html as htmllib
import json
import re
import xml.etree.ElementTree as ET

from starlette.testclient import TestClient

BASE = "https://pay.geshfinance.com"


def _meta(html: str, attr: str, name: str) -> str | None:
    m = re.search(rf'<meta\s+{attr}="{re.escape(name)}"\s+content="([^"]*)"', html)
    return htmllib.unescape(m.group(1)) if m else None


def _title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    assert m, "missing <title>"
    return htmllib.unescape(m.group(1))


def _canonical(html: str) -> str | None:
    m = re.search(r'<link\s+rel="canonical"\s+href="([^"]*)"', html)
    return m.group(1) if m else None


def _h1s(html: str) -> list[str]:
    return [
        htmllib.unescape(re.sub(r"<[^>]+>", "", h)).strip()
        for h in re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    ]


def _jsonld(html: str) -> list[dict]:
    return [
        json.loads(b)
        for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    ]


def assert_seo_head(html: str, *, title: str, path: str, h1: str, description: str | None = None):
    assert _title(html) == title
    assert _canonical(html) == BASE + path
    desc = _meta(html, "name", "description")
    assert desc and (description is None or desc == description)
    assert _meta(html, "property", "og:title") == title
    og_desc = _meta(html, "property", "og:description")
    assert og_desc and len(og_desc) <= 200
    assert _meta(html, "property", "og:type")
    assert _meta(html, "property", "og:url") == BASE + path
    og_img = _meta(html, "property", "og:image")
    assert og_img and og_img.startswith(BASE + "/og/") and og_img.endswith(".png")
    assert _meta(html, "name", "twitter:card") == "summary_large_image"
    assert _h1s(html) == [h1]


def _sitemap(client: TestClient) -> list[tuple[str, str | None, list[str]]]:
    r = client.get("/sitemap.xml", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(r.content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    assert root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"
    out = []
    for u in root.findall("s:url", ns):
        loc = u.find("s:loc", ns)
        lastmod = u.find("s:lastmod", ns)
        others = [c.tag.split("}")[1] for c in u if c.tag.split("}")[1] not in ("loc", "lastmod")]
        out.append((loc.text, lastmod.text if lastmod is not None else None, others))
    return out
