"""HOTFIX regression guard — the public /privacy page leaked internal author notes.

Verified live on https://pay.geshfinance.com/privacy (2026-07-23) before this fix:

    "[Decide: either "GESH is intended for US users only; do not use if you are in
     the EU/UK," OR add a full GDPR section. Geo-blocking the EU is the simplest
     path for a solo operator unless you want EU customers.]"

That is an author-facing decision note about legal strategy, served verbatim on a
public legal document. Root cause: `_PLACEHOLDER_RE` required SHOUTING CASE
(`\\[[A-Z][A-Z0-9@]...`), so it caught only 3 of the 10 bracket tokens in the draft
and silently rendered the other 7 — including every lower/mixed-case instruction.

The original tests passed because they asserted against synthetic ALL-CAPS strings
that matched the regex by construction, never against the real source document.
This file tests the REAL FILE, which is the only thing that would have caught it.

New contract: the draft is an annotated document; **nothing in square brackets is
ever served** except genuine markdown links. Unresolved brackets are redacted from
the body and counted in the DRAFT banner.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    import legal_pages
    import server

    legal_pages._cache.clear()
    return TestClient(server.app)


# ─── the specific leak ────────────────────────────────────────────────────────

def test_the_geoblocking_decision_note_is_not_served(client):
    """The exact string found live. Named explicitly so it can never come back."""
    body = client.get("/privacy").text

    assert "Geo-blocking" not in body
    assert "simplest path for a solo operator" not in body


def test_no_author_decision_notes_survive(client):
    """Any bracketed instruction addressed to the author, in any casing."""
    body = client.get("/privacy").text.lower()

    for leak in ("[decide:", "if you add analytics", "if required by applicable law",
                 "e.g., 12 months", "strictly-necessary cookies"):
        assert leak not in body, f"author note served publicly: {leak}"


# ─── the real source file, not synthetic strings ──────────────────────────────

def test_every_bracket_token_in_the_real_draft_is_detected():
    """The bug in one line: the ORIGINAL tests only used synthetic ALL-CAPS input,
    so a regex that only matched ALL-CAPS passed while leaking 7 of 10 real tokens.
    Assert against the actual shipped document."""
    import legal_pages

    md = legal_pages.PRIVACY_PATH.read_text()
    detected = set(legal_pages.unfilled_placeholders(md))

    import re
    every_bracket = {
        m for m in re.findall(r"\[[^\]\n]+\]", md)
        if not re.search(re.escape(m) + r"\(", md)      # exclude markdown links
    }
    missed = every_bracket - detected
    assert not missed, f"unfilled placeholders NOT detected (would be served): {sorted(missed)}"


def test_lowercase_and_mixed_case_placeholders_are_detected():
    import legal_pages

    found = legal_pages.unfilled_placeholders(
        "a [Decide: pick one] b [MAILING ADDRESS if required] c [ANALYTICS, IF ANY]"
    )

    assert len(found) == 3


def test_markdown_links_are_still_not_flagged():
    """Must not regress into flagging legitimate prose."""
    import legal_pages

    assert legal_pages.unfilled_placeholders("See [the terms](/terms) and [our policy](/privacy).") == []


def test_rendered_body_contains_no_square_bracket_spans(client):
    """Belt and braces: whatever the detector thinks, nothing bracketed reaches
    the reader — the whole class is redacted, not enumerated."""
    import re

    html = client.get("/privacy").text
    body_only = html[html.index("Privacy Policy"):]
    # the DRAFT banner legitimately lists pending items inside <code>…</code>
    body_only = re.sub(r"<code>.*?</code>", "", body_only, flags=re.S)

    assert not re.search(r"\[[^\]\n]{3,}\]", body_only)


def test_banner_still_discloses_that_items_are_pending(client):
    """Redaction must not become concealment — the reader is still told the
    document is incomplete."""
    body = client.get("/privacy").text

    assert "DRAFT" in body
    assert "pending" in body.lower()


def test_substantive_policy_content_survives_redaction(client):
    """The redaction must not gut the document."""
    body = client.get("/privacy").text

    assert "Anthropic" in body
    assert "Lemon Squeezy" in body
    for heading in ("Information we collect", "Retention", "rights"):
        assert heading.lower() in body.lower()
