"""SEO step 1: robots.txt, sitemap.xml, exact public paths, noindex, page head tags.

Public SEO routes must be reachable logged-out WITHOUT widening the auth wall:
exact-path matching only, private routes stay gated, private/login pages carry
noindex, and every indexable page has a distinct title, canonical, description,
Open Graph/Twitter tags and exactly one H1.
"""

from __future__ import annotations

import json
import re

import pytest
from seo_helpers import (
    BASE,
    _jsonld,
    _meta,
    _sitemap,
    assert_seo_head,
)
from starlette.testclient import TestClient

import auth_users
import seo_pages
import server

# ── base URL ────────────────────────────────────────────────────────────────────


def test_site_base_url_defaults_to_production_origin() -> None:
    assert seo_pages.site_base_url() == BASE


def test_site_base_url_honours_https_app_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com/")
    assert seo_pages.site_base_url() == "https://app.example.com"


@pytest.mark.parametrize(
    "bad",
    [
        "http://pay.geshfinance.com",
        "https://abc-def.trycloudflare.com",
        "http://localhost:9000",
        "https://127.0.0.1:9000",
        "not a url",
        "",
    ],
)
def test_site_base_url_ignores_ephemeral_or_insecure_values(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("APP_BASE_URL", bad)
    assert seo_pages.site_base_url() == BASE


# ── robots.txt ──────────────────────────────────────────────────────────────────


def test_robots_txt_is_plain_text_not_the_login_page(seo_anon: TestClient) -> None:
    r = seo_anon.get("/robots.txt", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert "<html" not in body.lower()
    lines = [ln.strip() for ln in body.splitlines()]
    assert "User-agent: *" in lines
    for d in ("/app", "/admin", "/onboarding", "/api"):
        assert f"Disallow: {d}" in lines
    assert f"Sitemap: {BASE}/sitemap.xml" in lines
    # login must stay crawlable (noindex works only if the crawler can fetch the page)
    assert "Disallow: /login" not in lines
    assert not any(ln.startswith("Disallow: /static") for ln in lines)
    # documented AI-crawler decision with a marked place to opt out
    assert any(ln.startswith("#") and "AI crawler" in ln for ln in lines)
    assert "GPTBot" in body and "ClaudeBot" in body and "PerplexityBot" in body
    assert "Google-Extended" in body
    assert not any(re.match(r"^Disallow:\s*/\s*$", ln) for ln in lines)


def test_search_console_verification_file_is_public_exact_and_plain(seo_anon: TestClient) -> None:
    name = seo_pages.GSC_VERIFICATION_NAME
    r = seo_anon.get(f"/{name}", follow_redirects=False)
    assert r.status_code == 200
    assert r.text == f"google-site-verification: {name}"
    # exact match only: neighbours stay behind the login wall
    assert seo_anon.get(f"/{name}.bak", follow_redirects=False).status_code != 200


def test_robots_txt_allows_head(seo_anon: TestClient) -> None:
    assert seo_anon.head("/robots.txt", follow_redirects=False).status_code == 200


# ── sitemap.xml ─────────────────────────────────────────────────────────────────


def test_sitemap_lists_only_public_marketing_urls_with_real_lastmod(seo_anon: TestClient) -> None:
    entries = _sitemap(seo_anon)
    locs = [e[0] for e in entries]
    assert BASE + "/pricing" in locs
    assert BASE + "/track-record" in locs
    assert len(locs) == len(set(locs))
    for loc, lastmod, others in entries:
        assert loc.startswith(BASE + "/") or loc == BASE
        assert lastmod and re.fullmatch(r"\d{4}-\d{2}-\d{2}", lastmod)
        assert others == [], "priority/changefreq must be omitted"
    for private in ("/login", "/app", "/admin", "/api", "/onboarding", "/report"):
        assert not any(loc.startswith(BASE + private) for loc in locs)


def test_every_sitemap_url_is_public_and_served_to_logged_out_visitors(
    seo_anon: TestClient, seo_empty_track_record: None
) -> None:
    for loc, _, _ in _sitemap(seo_anon):
        path = loc[len(BASE) :] or "/"
        assert auth_users.is_public_path(path), path
        r = seo_anon.get(path, follow_redirects=False)
        assert r.status_code == 200, (path, r.status_code)
        assert "noindex" not in r.headers.get("x-robots-tag", ""), path


def test_sitemap_uses_canonical_base_url_from_env(
    seo_anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    assert all(e[0].startswith("https://app.example.com") for e in _sitemap(seo_anon))


# ── exact-path public allowlist ─────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml"])
def test_seo_files_are_public_by_exact_match_only(path: str) -> None:
    assert auth_users.is_public_path(path) is True
    for sibling in (path + "/x", path + "x", path + ".bak", path.upper(), "/x" + path):
        assert auth_users.is_public_path(sibling) is False, sibling


PRIVATE_API = [
    "/api/portfolio",
    "/api/watchlist",
    "/api/mybook",
    "/api/me",
    "/api/ecg",
    "/api/regime",
    "/api/deep-analysis",
    "/api/og/portfolio-xray",
]
PRIVATE_HTML = [
    "/report",
    "/invest",
    "/progress",
    "/watch",
    "/usage",
    "/app",
    "/admin",
    "/onboarding",
    "/robots.txt.bak",
    "/sitemap.xml/x",
]


@pytest.mark.parametrize("path", PRIVATE_API)
def test_private_api_routes_stay_gated(seo_anon: TestClient, path: str) -> None:
    assert seo_anon.get(path, follow_redirects=False).status_code == 401


@pytest.mark.parametrize("path", PRIVATE_HTML)
def test_private_html_routes_stay_gated(seo_anon: TestClient, path: str) -> None:
    r = seo_anon.get(path, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"


# ── noindex on private/login pages ──────────────────────────────────────────────


def test_login_page_is_noindex_with_full_head(seo_anon: TestClient) -> None:
    r = seo_anon.get("/login")
    assert r.status_code == 200
    assert_seo_head(
        r.text,
        title="Sign in | GESH Financial",
        path="/login",
        h1="Sign in to GESH Financial",
        description="Sign in to your GESH Financial dashboard.",
    )
    assert _meta(r.text, "name", "robots") == "noindex"
    assert "noindex" in r.headers["x-robots-tag"]
    # sign-in behaviour untouched
    assert "name=email" in r.text and "/auth/login" in r.text and "/auth/request-link" in r.text


def test_api_and_gated_responses_carry_x_robots_noindex(seo_anon: TestClient) -> None:
    assert "noindex" in seo_anon.get("/api/portfolio").headers["x-robots-tag"]
    assert "noindex" in seo_anon.get("/report", follow_redirects=False).headers["x-robots-tag"]


def test_logged_in_dashboard_is_noindex(seo_walled: None) -> None:
    auth_users.create_user("a@b.com", "pw")
    c = TestClient(server.app, raise_server_exceptions=False)
    r = c.get("/", cookies={auth_users.COOKIE_NAME: auth_users.issue_token("a@b.com")})
    assert r.status_code == 200
    assert "noindex" in r.headers["x-robots-tag"]


def test_public_marketing_pages_are_not_noindexed(
    seo_anon: TestClient, seo_empty_track_record: None
) -> None:
    for path in ("/pricing", "/track-record", "/robots.txt", "/sitemap.xml", "/terms"):
        r = seo_anon.get(path, follow_redirects=False)
        assert r.status_code == 200, path
        assert "x-robots-tag" not in r.headers, path
        assert 'name="robots"' not in r.text, path


# ── /pricing ────────────────────────────────────────────────────────────────────


def test_pricing_head_and_single_h1(seo_anon: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)
    r = seo_anon.get("/pricing")
    assert r.status_code == 200
    assert_seo_head(
        r.text,
        title="Pricing | GESH Financial – Plans for Portfolio Analytics",
        path="/pricing",
        h1="GESH Financial — Access",
        description=(
            "Simple plans for daily portfolio observability. "
            "Compare features and start measuring your book today."
        ),
    )


def test_pricing_keeps_every_existing_behaviour(
    seo_anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)
    monkeypatch.setenv("LS_PLAN_NAME", "Plan X")
    monkeypatch.setenv("LS_PLAN_PRICE", "59")
    monkeypatch.setenv("LS_CHECKOUT_PRIMARY", "https://checkout.example/buy?a=1&b=2")
    html = seo_anon.get("/pricing").text
    assert "Plan X" in html and "<strong>$59</strong>" in html
    assert 'href="https://checkout.example/buy?a=1&amp;b=2"' in html
    assert ">Subscribe</a>" in html
    for frag in (
        "Signal-accuracy proof (live track record)",
        "Daily AI portfolio report",
        "Watch dashboard",
        "Diversification radar",
        "BETA · educational use.",
        'href="/terms"',
        'href="/privacy"',
        'href="/disclaimer"',
    ):
        assert frag in html


def test_pricing_jsonld_offer_matches_visible_price(
    seo_anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)
    monkeypatch.setenv("LS_PLAN_NAME", "Plan X")
    monkeypatch.setenv("LS_PLAN_PRICE", "59")
    docs = _jsonld(seo_anon.get("/pricing").text)
    offers = [o for d in docs for o in (d.get("offers") or [])]
    assert len(offers) == 1
    assert offers[0]["@type"] == "Offer"
    assert offers[0]["price"] == "59" and offers[0]["priceCurrency"] == "USD"
    assert offers[0]["name"] == "Plan X"
    blob = json.dumps(docs)
    assert "aggregateRating" not in blob and "review" not in blob.lower()


def test_pricing_jsonld_has_no_offer_when_signups_paused_or_price_not_numeric(
    seo_anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LS_PLAN_PRICE", "free-ish")
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)
    assert all("offers" not in d for d in _jsonld(seo_anon.get("/pricing").text))
    monkeypatch.setenv("LS_PLAN_PRICE", "49")
    monkeypatch.setattr(server, "_signups_paused", lambda: True)
    html = seo_anon.get("/pricing").text
    assert "Not accepting new subscribers" in html
    assert all("offers" not in d for d in _jsonld(html))


# ── /track-record ───────────────────────────────────────────────────────────────


def test_track_record_head_keeps_existing_h1(
    seo_anon: TestClient, seo_empty_track_record: None
) -> None:
    r = seo_anon.get("/track-record")
    assert r.status_code == 200
    assert_seo_head(
        r.text,
        title="Signal Track Record (Hypothetical) | GESH Financial",
        path="/track-record",
        h1="GESH Financial — Signal Accuracy Track Record",
        description=(
            "The public GESH signal track record: logged closes, expectancy, profit factor. "
            "Hypothetical/simulated results — methodology and limitations disclosed."
        ),
    )


# ── head helper ─────────────────────────────────────────────────────────────────


def test_head_tags_escape_values_and_jsonld_cannot_break_out() -> None:
    html = seo_pages.head_tags(
        title='A "quoted" <b>title</b>',
        description='desc with "quotes" & <tags>',
        path="/x",
        jsonld=[{"@type": "Thing", "name": "</script><script>alert(1)</script>"}],
    )
    assert "<b>title</b>" not in html
    assert "</script><script>" not in html
    assert "&quot;quoted&quot;" in html
    assert _jsonld(html)[0]["name"] == "</script><script>alert(1)</script>"


def test_head_tags_trim_og_description_to_200_chars() -> None:
    html = seo_pages.head_tags(title="T", description="x" * 400, path="/x")
    assert len(_meta(html, "property", "og:description") or "") <= 200
