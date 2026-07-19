"""L1 + L5 — publish the Privacy Policy, reach the policies from the product, and
frame the product as beta/educational while the ToS is still a DRAFT.

Verified gaps this closes (2026-07-19, checked against the code, not assumed):
  * no `/privacy` route existed anywhere in server.py / legal_pages.py, while the
    app collects emails, customer-entered positions, and usage analytics;
  * dashboard.html contained ZERO links to /terms or /disclaimer — the policies
    existed but were unreachable from the product;
  * TERMS_OF_SERVICE.md:3 self-labels "DRAFT — not yet legally reviewed" and is
    served live at /terms while the product charges $49/mo.

The privacy policy ships behind a visible DRAFT banner on purpose: publishing an
honest draft beats publishing nothing, but it must not read as counsel-approved.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    return _au


@pytest.fixture
def client(au):
    import server

    return TestClient(server.app)


def _dashboard() -> str:
    return (REPO / "templates" / "dashboard.html").read_text()


# ─── L1: the /privacy route ───────────────────────────────────────────────────

def test_privacy_route_serves_the_policy(client):
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy Policy" in r.text


def test_privacy_is_public_no_login_required():
    """A privacy policy behind a login wall is not a published policy."""
    import auth_users

    assert auth_users.is_public_path("/privacy") is True


def test_privacy_discloses_third_party_llm_processing(client):
    """The GESH-specific disclosure: customer holdings leave for Anthropic/Google.
    This is the single most important line in the document."""
    body = client.get("/privacy").text

    assert "Anthropic" in body
    assert "Google" in body or "Gemini" in body


def test_privacy_covers_the_required_subject_areas(client):
    """Skeleton completeness — collection, processors, retention, rights."""
    body = client.get("/privacy").text.lower()

    for topic in ("information we collect", "lemon squeezy", "retention", "rights"):
        assert topic in body, f"privacy policy is missing: {topic}"


def test_privacy_is_labeled_draft_until_counsel_signs_off(client):
    """Honesty gate: it must not read as attorney-reviewed when it isn't."""
    assert "DRAFT" in client.get("/privacy").text


def test_privacy_page_escapes_markup_it_renders():
    """Rendered through the same minimal markdown path as /terms — a stray angle
    bracket in the source must not become live markup."""
    import legal_pages

    html = legal_pages._md_to_html("Contact <script>alert(1)</script> us")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_privacy_never_leaks_an_internal_compliance_section():
    """Same rule /terms already enforces: internal legal strategy is never served."""
    import legal_pages

    md = "# Privacy\nPublic text.\n\n### Compliance notes (internal)\nSecret strategy.\n"
    body = legal_pages._md_to_html(legal_pages._strip_internal(md))

    assert "Public text." in body
    assert "Secret strategy" not in body


# ─── L1: unfilled-placeholder detection ───────────────────────────────────────

def test_placeholder_detector_finds_unfilled_brackets():
    """The draft ships with [BRACKET] placeholders only the owner can fill.
    Detect them so the page can flag itself instead of publishing '[DOMAIN]'."""
    import legal_pages

    found = legal_pages.unfilled_placeholders("Contact [PRIVACY@YOURDOMAIN] at [DOMAIN].")

    assert "[PRIVACY@YOURDOMAIN]" in found
    assert "[DOMAIN]" in found


def test_placeholder_detector_ignores_ordinary_brackets():
    """Markdown links and citations use brackets legitimately — no false alarms."""
    import legal_pages

    assert legal_pages.unfilled_placeholders("See [the terms](/terms) and [1].") == []


def test_privacy_page_flags_remaining_placeholders_visibly(client):
    """If any placeholder survives, the page must SAY SO rather than render a
    policy that silently names nobody."""
    import legal_pages

    body = client.get("/privacy").text
    remaining = legal_pages.unfilled_placeholders(legal_pages.PRIVACY_PATH.read_text())

    if remaining:
        assert "pending" in body.lower() or "placeholder" in body.lower()


# ─── L1: footer links reachable from the product ──────────────────────────────

def test_dashboard_links_to_all_three_policies():
    """The verified gap: dashboard.html had zero policy links."""
    html = _dashboard()

    for route in ("/privacy", "/terms", "/disclaimer"):
        assert f'href="{route}"' in html, f"dashboard has no link to {route}"


def test_book_report_links_to_the_policies(client, au, monkeypatch):
    """The print/PDF surface a customer keeps — it must carry the policy links."""
    import server

    au.create_user("cust@x.com", "pw")
    monkeypatch.setattr(server, "_customer_book_payload", lambda *_a, **_k: {
        "holdings": [], "equity_summary": {"portfolio_equity": 0.0}, "unpriced": [],
    })
    monkeypatch.setattr(au, "get_latest_book_analysis", lambda *_a, **_k: None)

    html = server._render_book_report("cust@x.com")

    assert "/privacy" in html
    assert "/terms" in html


def test_legal_pages_carry_cross_links(client):
    """From any policy page you can reach the others."""
    body = client.get("/terms").text

    assert "/privacy" in body


# ─── L5: beta / educational framing ───────────────────────────────────────────

def test_dashboard_shows_the_beta_educational_banner():
    """While the ToS is a DRAFT and no counsel has classified 'My Book', the
    product must present itself as beta/educational, not as advice."""
    html = _dashboard()

    assert "gesh-beta-banner" in html, "no beta banner element on the dashboard"
    lowered = html.lower()
    assert "beta" in lowered and "educational" in lowered


def test_beta_banner_says_not_investment_advice():
    html = _dashboard().lower()
    idx = html.index("gesh-beta-banner")
    near = html[idx:idx + 900]

    assert "not investment advice" in near or "not financial advice" in near


def test_pricing_page_carries_the_beta_framing(client):
    """The page where money changes hands is where the framing matters most."""
    body = client.get("/pricing").text.lower()

    assert "beta" in body
    assert "educational" in body


def test_pricing_page_links_privacy(client):
    """Auto-renewal/negative-option exposure: the policies must be one click from
    the checkout, not buried."""
    assert "/privacy" in client.get("/pricing").text


# ─── L1: AI-disclosure at the point of use ────────────────────────────────────

def test_analyze_button_carries_an_ai_data_flow_disclosure():
    """The hub's key GESH-specific disclosure, shown where the customer acts —
    a policy page alone does not put it in front of them at the moment of use."""
    html = _dashboard()

    assert "gesh-ai-disclosure" in html, "no AI-disclosure element near analyze"
    idx = html.index("gesh-ai-disclosure")
    near = html[idx:idx + 700]
    assert "Anthropic" in near or "Claude" in near


def test_author_instruction_note_is_never_served():
    """Line 2 of the draft is written TO the author ("Placeholders in [BRACKETS]
    need your real values"), not to the reader. Serving it would leak internal
    drafting instructions onto a public policy page."""
    import legal_pages

    md = ("# Privacy\n"
          "> **DRAFT — NOT YET LEGALLY REVIEWED.** Placeholders in **[BRACKETS]** need values.\n\n"
          "Real policy text.\n")
    out = legal_pages._strip_author_note(md)

    assert "[BRACKETS]" not in out
    assert "Real policy text." in out


def test_served_privacy_page_has_no_author_instructions(client):
    body = client.get("/privacy").text

    assert "[BRACKETS]" not in body
    assert "need your real values" not in body
