"""SEO step 1: robots.txt, sitemap.xml, exact public paths, noindex, page head tags.

Public SEO routes must be reachable logged-out WITHOUT widening the auth wall:
exact-path matching only, private routes stay gated, private/login pages carry
noindex, and every indexable page has a distinct title, canonical, description,
Open Graph/Twitter tags and exactly one H1.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import auth_users
import proof_page
import seo_pages
import server

BASE = "https://pay.geshfinance.com"


@pytest.fixture
def walled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth wall ON with an isolated auth DB (anonymous client = logged-out visitor)."""
    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()


@pytest.fixture
def anon(walled: None) -> TestClient:
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.fixture
def empty_track_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real = proof_page.load_track_record
    monkeypatch.setattr(
        proof_page,
        "load_track_record",
        lambda: real(str(tmp_path / "absent.db"), daily_analysis_db_path=None),
    )


def _meta(html: str, attr: str, name: str) -> str | None:
    m = re.search(rf'<meta\s+{attr}="{re.escape(name)}"\s+content="([^"]*)"', html)
    return m.group(1) if m else None


def _title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    assert m, "missing <title>"
    return m.group(1)


def _canonical(html: str) -> str | None:
    m = re.search(r'<link\s+rel="canonical"\s+href="([^"]*)"', html)
    return m.group(1) if m else None


def _h1s(html: str) -> list[str]:
    return [
        re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
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


def test_robots_txt_is_plain_text_not_the_login_page(anon: TestClient) -> None:
    r = anon.get("/robots.txt", follow_redirects=False)
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


def test_robots_txt_allows_head(anon: TestClient) -> None:
    assert anon.head("/robots.txt", follow_redirects=False).status_code == 200


# ── sitemap.xml ─────────────────────────────────────────────────────────────────


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


def test_sitemap_lists_only_public_marketing_urls_with_real_lastmod(anon: TestClient) -> None:
    entries = _sitemap(anon)
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
    anon: TestClient, empty_track_record: None
) -> None:
    for loc, _, _ in _sitemap(anon):
        path = loc[len(BASE) :] or "/"
        assert auth_users.is_public_path(path), path
        r = anon.get(path, follow_redirects=False)
        assert r.status_code == 200, (path, r.status_code)
        assert "noindex" not in r.headers.get("x-robots-tag", ""), path


def test_sitemap_uses_canonical_base_url_from_env(
    anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    assert all(e[0].startswith("https://app.example.com") for e in _sitemap(anon))


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
def test_private_api_routes_stay_gated(anon: TestClient, path: str) -> None:
    assert anon.get(path, follow_redirects=False).status_code == 401


@pytest.mark.parametrize("path", PRIVATE_HTML)
def test_private_html_routes_stay_gated(anon: TestClient, path: str) -> None:
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"


# ── noindex on private/login pages ──────────────────────────────────────────────


def test_login_page_is_noindex_with_full_head(anon: TestClient) -> None:
    r = anon.get("/login")
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


def test_api_and_gated_responses_carry_x_robots_noindex(anon: TestClient) -> None:
    assert "noindex" in anon.get("/api/portfolio").headers["x-robots-tag"]
    assert "noindex" in anon.get("/report", follow_redirects=False).headers["x-robots-tag"]


def test_logged_in_dashboard_is_noindex(walled: None) -> None:
    auth_users.create_user("a@b.com", "pw")
    c = TestClient(server.app, raise_server_exceptions=False)
    r = c.get("/", cookies={auth_users.COOKIE_NAME: auth_users.issue_token("a@b.com")})
    assert r.status_code == 200
    assert "noindex" in r.headers["x-robots-tag"]


def test_public_marketing_pages_are_not_noindexed(
    anon: TestClient, empty_track_record: None
) -> None:
    for path in ("/pricing", "/track-record", "/robots.txt", "/sitemap.xml", "/terms"):
        r = anon.get(path, follow_redirects=False)
        assert r.status_code == 200, path
        assert "x-robots-tag" not in r.headers, path
        assert 'name="robots"' not in r.text, path


# ── /pricing ────────────────────────────────────────────────────────────────────


def test_pricing_head_and_single_h1(anon: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)
    r = anon.get("/pricing")
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
    anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)
    monkeypatch.setenv("LS_PLAN_NAME", "Plan X")
    monkeypatch.setenv("LS_PLAN_PRICE", "59")
    monkeypatch.setenv("LS_CHECKOUT_PRIMARY", "https://checkout.example/buy?a=1&b=2")
    html = anon.get("/pricing").text
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
    anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)
    monkeypatch.setenv("LS_PLAN_NAME", "Plan X")
    monkeypatch.setenv("LS_PLAN_PRICE", "59")
    docs = _jsonld(anon.get("/pricing").text)
    offers = [o for d in docs for o in (d.get("offers") or [])]
    assert len(offers) == 1
    assert offers[0]["@type"] == "Offer"
    assert offers[0]["price"] == "59" and offers[0]["priceCurrency"] == "USD"
    assert offers[0]["name"] == "Plan X"
    blob = json.dumps(docs)
    assert "aggregateRating" not in blob and "review" not in blob.lower()


def test_pricing_jsonld_has_no_offer_when_signups_paused_or_price_not_numeric(
    anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LS_PLAN_PRICE", "free-ish")
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)
    assert all("offers" not in d for d in _jsonld(anon.get("/pricing").text))
    monkeypatch.setenv("LS_PLAN_PRICE", "49")
    monkeypatch.setattr(server, "_signups_paused", lambda: True)
    html = anon.get("/pricing").text
    assert "Not accepting new subscribers" in html
    assert all("offers" not in d for d in _jsonld(html))


# ── /track-record ───────────────────────────────────────────────────────────────


def test_track_record_head_keeps_existing_h1(anon: TestClient, empty_track_record: None) -> None:
    r = anon.get("/track-record")
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


def test_unauthenticated_paths_do_not_touch_owner_modules() -> None:
    """The SEO module must stay free of owner-data / broker / LLM / billing imports."""
    src = (Path(seo_pages.__file__)).read_text(encoding="utf-8")
    for banned in (
        "data_fetcher",
        "rh_mcp_data",
        "robin_stocks",
        "ai_analyzer",
        "llm_gateway",
        "billing",
        "model_portfolio",
        "import server",
    ):
        assert not re.search(rf"^\s*(from|import)\s+{banned}\b", src, re.M), banned
