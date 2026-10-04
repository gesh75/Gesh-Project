"""SEO step 2: logged-out `/` is a public marketing landing; logged-in `/` is unchanged."""

from __future__ import annotations

import html as htmllib
import re
from pathlib import Path

import pytest
from seo_helpers import (  # noqa: F401  (fixtures + helpers)
    BASE,
    _h1s,
    _jsonld,
    _meta,
    _sitemap,
    assert_seo_head,
)
from starlette.testclient import TestClient

import auth_users
import server

TITLE = "GESH Financial | Portfolio Tracking, AI Scores & Risk Analytics"
H1 = "See what's in your book."


def _text(html: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", html))


def test_logged_out_root_is_the_landing_not_login_or_dashboard(seo_anon: TestClient) -> None:
    r = seo_anon.get("/", follow_redirects=False)
    assert r.status_code == 200
    html = r.text
    assert_seo_head(html, title=TITLE, path="/", h1=H1)
    assert "name=email" not in html and "data-initial-role" not in html
    assert "x-robots-tag" not in r.headers
    assert 'name="robots"' not in html


def test_landing_meta_description_is_short_and_exact(seo_anon: TestClient) -> None:
    desc = _meta(seo_anon.get("/").text, "name", "description")
    assert desc is not None and len(desc) <= 155
    assert desc.startswith("Track positions, AI scores, Monte Carlo risk and Kelly sizing")
    assert "not advice" in desc


def test_landing_hero_sections_links_and_disclaimer(seo_anon: TestClient) -> None:
    html = seo_anon.get("/").text
    text = _text(html)
    assert (
        "GESH Financial is portfolio observability: positions, risk, and sizing measured every day"
        in text
    )
    assert "educational model output, not advice" in text
    for needle in (
        "How it works",
        "What the report measures",
        "Positions table",
        "Sector mix",
        "Diversification radar",
        "Funding swaps",
        "Monte Carlo",
        "Kelly",
        "Illustrative",
        "Frequently asked questions",
    ):
        assert needle in text, needle
    assert 'href="/track-record"' in html and 'href="/pricing"' in html and 'href="/login"' in html
    assert "hypothetical" in text.lower() and "not investment advice" in text.lower()
    assert "Is this financial advice?" in text


def test_landing_jsonld_software_application_and_faq(seo_anon: TestClient) -> None:
    html = seo_anon.get("/").text
    docs = _jsonld(html)
    app = next(d for d in docs if d["@type"] == "SoftwareApplication")
    assert app["name"] == "GESH Financial"
    assert app["applicationCategory"] == "BusinessApplication"
    assert app["operatingSystem"] == "Web"
    assert app["offers"] == [
        {
            "@type": "Offer",
            "price": "0",
            "priceCurrency": "USD",
            "description": "Free tools; paid plans for the full dashboard",
        }
    ]
    # featureList must be visible on the page (hero copy), word for word
    assert app["featureList"] == (
        "Daily portfolio report, AI scores, Monte Carlo risk simulation, Kelly position sizing"
    )
    assert app["featureList"] in _text(html)
    faq = next(d for d in docs if d["@type"] == "FAQPage")
    questions = [q["name"] for q in faq["mainEntity"]]
    assert 4 <= len(questions) <= 6 and "Is this financial advice?" in questions
    visible = _text(html)
    assert all(q in visible for q in questions)
    blob = str(docs).lower()
    assert "aggregaterating" not in blob and "'review'" not in blob


def test_landing_never_touches_owner_broker_llm_or_billing(
    seo_anon: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a, **k):
        raise AssertionError("public landing touched private state")

    for name in (
        "_render_dashboard",
        "build_data_payload",
        "_resolved_payload",
        "load_snapshot_payload",
        "_context_payload",
    ):
        monkeypatch.setattr(server, name, boom, raising=False)
    monkeypatch.setattr(server.billing, "provision_user", boom, raising=False)
    assert seo_anon.get("/").status_code == 200


def test_landing_has_no_dollar_balances_or_account_identifiers(seo_anon: TestClient) -> None:
    text = _text(seo_anon.get("/").text)
    assert "account number" not in text.lower()
    # every $-figure on the landing sits inside an explicitly illustrative card
    assert text.count("Illustrative") >= 3


def test_invalid_or_expired_cookie_gets_landing_not_dashboard(seo_anon: TestClient) -> None:
    r = seo_anon.get("/", cookies={auth_users.COOKIE_NAME: "forged.token.value"})
    assert r.status_code == 200 and "data-initial-role" not in r.text
    assert "See what" in r.text


def _legacy_dashboard(role: str) -> str:
    html = server._render_dashboard(role)
    html = html.replace("<body", f'<body data-initial-role="{role}"', 1)
    return html.replace("{{ model }}", server.MODEL)


def test_logged_in_customer_root_is_byte_identical_dashboard(seo_walled: None) -> None:
    auth_users.create_user("cust@example.com", "pw")
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(auth_users.COOKIE_NAME, auth_users.issue_token("cust@example.com"))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert r.text == _legacy_dashboard("customer")
    assert 'data-initial-role="customer"' in r.text
    assert "noindex" in r.headers["x-robots-tag"]


def test_logged_in_owner_root_is_owner_dashboard(
    seo_walled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_users, "is_owner", lambda email: True)
    auth_users.create_user("owner@example.com", "pw")
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(auth_users.COOKIE_NAME, auth_users.issue_token("owner@example.com"))
    r = c.get("/")
    assert r.text == _legacy_dashboard("owner")
    assert 'data-initial-role="owner"' in r.text


def test_auth_disabled_root_is_unchanged_owner_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    r = TestClient(server.app, raise_server_exceptions=False).get("/")
    assert r.text == _legacy_dashboard("owner")


def test_root_is_exact_public_path_only() -> None:
    assert auth_users.is_public_path("/") is True
    for p in ("//", "/x", "/?x", "/index.html", "/app", "/report"):
        assert auth_users.is_public_path(p) is False, p


def test_sitemap_now_lists_the_homepage_first(seo_anon: TestClient) -> None:
    locs = [e[0] for e in _sitemap(seo_anon)]
    assert locs[0] == BASE + "/"


def test_seo_modules_have_no_private_imports() -> None:
    root = Path(server.__file__).parent
    for name in (
        "seo_pages.py",
        "seo_layout.py",
        "seo_landing.py",
        "seo_tools.py",
        "seo_tools_js.py",
        "seo_tools_data.py",
    ):
        src = (root / name).read_text(encoding="utf-8")
        for banned in (
            "data_fetcher",
            "rh_mcp_data",
            "robin_stocks",
            "ai_analyzer",
            "llm_gateway",
            "billing",
            "model_portfolio",
            "server",
            "auth_users",
            "db",
        ):
            assert not re.search(rf"^\s*(from|import)\s+{banned}\b", src, re.M), (name, banned)
