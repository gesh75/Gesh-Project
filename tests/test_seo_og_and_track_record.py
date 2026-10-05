"""SEO step 4: OG images (public, exact paths, not under /api) and track-record compliance."""

from __future__ import annotations

import re
import struct
from pathlib import Path
from urllib.parse import urlparse

import pytest
from seo_helpers import BASE, _meta, _sitemap

import auth_users
import proof_page
import seo_pages

OG_NAMES = ["gesh-financial", "portfolio-xray", "monte-carlo-simulator", "kelly-calculator"]


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


@pytest.mark.parametrize("name", OG_NAMES)
def test_og_image_is_a_real_1200x630_png_served_logged_out(seo_anon, name) -> None:
    r = seo_anon.get(f"/og/{name}.png", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert _png_size(r.content) == (1200, 630)
    assert "max-age" in r.headers["cache-control"]
    assert "x-robots-tag" not in r.headers
    assert 20_000 < len(r.content) < 600_000
    assert seo_anon.head(f"/og/{name}.png", follow_redirects=False).status_code == 200


@pytest.mark.parametrize("name", OG_NAMES)
def test_og_paths_are_exact_public_and_not_under_api(name) -> None:
    assert auth_users.is_public_path(f"/og/{name}.png") is True
    assert not f"/og/{name}.png".startswith("/api")
    for bad in (f"/og/{name}.png/x", f"/og/{name}.jpg", f"/og/{name}", "/og/", "/og/evil.png"):
        assert auth_users.is_public_path(bad) is False, bad


def test_unknown_or_traversal_og_names_are_gated_or_404(seo_anon) -> None:
    for p in ("/og/evil.png", "/og/../server.py", "/og/%2e%2e/server.py", "/og/..%2fserver.py"):
        r = seo_anon.get(p, follow_redirects=False)
        assert r.status_code in (302, 404), p
        assert b"FastAPI" not in r.content


def test_every_indexable_page_points_at_an_og_image_that_resolves(
    seo_anon, seo_empty_track_record
) -> None:
    pages = ["/login", *(urlparse(e[0]).path or "/" for e in _sitemap(seo_anon))]
    for path in pages:
        img = _meta(seo_anon.get(path).text, "property", "og:image")
        assert img and img.startswith(BASE + "/og/"), path
        r = seo_anon.get(img[len(BASE) :])
        assert r.status_code == 200 and _png_size(r.content) == (1200, 630), (path, img)


def test_tool_pages_use_their_own_og_image(seo_anon) -> None:
    for name in OG_NAMES[1:]:
        assert (
            _meta(seo_anon.get(f"/tools/{name}").text, "property", "og:image")
            == f"{BASE}/og/{name}.png"
        )
    assert _meta(seo_anon.get("/").text, "property", "og:image") == f"{BASE}/og/gesh-financial.png"


def test_og_dir_matches_whitelist_and_nothing_else_is_served() -> None:
    files = {p.stem for p in (Path(seo_pages.__file__).parent / "static" / "og").glob("*.png")}
    assert files == set(OG_NAMES) == set(seo_pages.OG_IMAGES)


# ── /track-record compliance additions (insert-only) ────────────────────────────


@pytest.fixture
def page(seo_empty_track_record, seo_anon) -> str:
    return seo_anon.get("/track-record").text


def test_hypothetical_banner_sits_before_every_figure(page: str) -> None:
    i = page.index('data-k="hypothetical-banner"')
    assert "HYPOTHETICAL / SIMULATED — NOT LIVE TRADING" in page
    assert "Past performance is not indicative of future results" in page
    for later in (
        'data-k="headline-realized-pnl-usd"',
        "Profit factor (gross $)",
        "Complete gate-eligible closed-trade ledger",
    ):
        assert page.index(later) > i, later
    # adjacent to the figures: nothing but the page title, subtitle and the status
    # banner sits between the compliance banner and the headline block
    between = page[i : page.index('data-k="headline-realized-pnl-usd"')]
    assert "<table" not in between and "<svg" not in between


def test_methodology_and_limitations_section_is_explicit_and_honest(page: str) -> None:
    assert re.search(r'<h2 id="methodology"[^>]*>Methodology and limitations</h2>', page)
    block = page[page.index('id="methodology-limits"') :]
    block = block[: block.index("</ul>")]
    for needle in (
        "Universe:",
        "Date range:",
        proof_page.LAUNCH_DATE,
        "Costs and slippage:",
        "10 bp entry slippage",
        "Gross vs. net:",
        "net of a 20 bp",
        "stress scenarios, not measured costs",
        "Not audited:",
    ):
        assert needle in block, needle
    # unknowns are said to be unknown, never invented
    assert "Commissions, fees and taxes are not stated" in block


def test_page_never_claims_legal_review(page: str) -> None:
    assert "PENDING COUNSEL REVIEW" not in page
    low = page.lower()
    assert "reviewed by counsel" not in low and "attorney-approved" not in low


def test_track_record_keeps_a_single_h1(page: str) -> None:
    assert len(re.findall(r"<h1[ >]", page)) == 1
