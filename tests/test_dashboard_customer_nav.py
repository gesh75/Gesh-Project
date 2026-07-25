"""Customer-nav + macro-panel regressions (GPT-5.6 audit items 10 & 3).

ITEM 10 — deny-by-default nav. Customers only ever get the Analyze tab: the
customer CSS (`body.customer .tab-pane{display:none!important}` +
`#tab-analyze{display:block!important}`) force-hides every other tab-pane, so the
Scanner tab has NO customer-reachable pane. Therefore every sidebar nav button
that switches to the scanner tab must be hidden for customers — either the button
carries `owner-only`, or its enclosing `sb-group` does (both routes are hidden by
`body.customer .owner-only{display:none!important}`). A bare Scanner nav item in a
customer-visible group is a dead link and fails this test.

ITEM 3 — the long-term-macro / margin-debt panel must not claim the value "feeds"
an `LT_Macro` consensus node. There is no such node on this branch (it is a
separate draft PR); the panel is an advisory long-term macro overlay. The literal
token `LT_Macro` must not appear anywhere in the template.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"

# Since 2026-07-23 the operator console is a SEPARATE partial spliced into named
# slots for an owner session only (server._render_dashboard), so the sidebar nav
# no longer lives in the shell. These checks therefore run against the COMPOSED
# documents — which is the stronger question anyway: not "is the markup marked
# owner-only?" but "does a customer receive it at all?".


def _composed(role: str) -> str:
    import server
    return server._render_dashboard(role)

# Split the markup on each sidebar GROUP container `<div class="sb-group"…>` /
# `<div class="sb-group owner-only">` — but NOT on `<div class="sb-group-title">`
# (the `-` after `sb-group` excludes it), so each chunk holds one whole group.
_GROUP_SPLIT = re.compile(r'(?=<div class="sb-group(?:"| ))')
_SCANNER_BTN = re.compile(r'<button[^>]*data-tab="scanner"[^>]*>')


def _scanner_groups(html: str) -> list[str]:
    return [g for g in _GROUP_SPLIT.split(html) if 'data-tab="scanner"' in g]


def test_scanner_nav_is_not_delivered_to_customers():
    """The Scanner tab has no customer-reachable pane, so a Scanner nav item in a
    customer document is a dead link. It is now absent outright rather than
    present-and-hidden."""
    assert not _SCANNER_BTN.findall(_composed("customer")), (
        "Scanner sidebar nav reached a customer document — it belongs in the "
        "owner console partial (templates/owner_console.html)."
    )


def test_scanner_nav_still_exists_for_the_owner():
    """Teeth: guards against the whole nav block being deleted, which would make
    the customer-side check above pass vacuously."""
    owner = _composed("owner")
    btns = _SCANNER_BTN.findall(owner)
    assert btns, "expected Scanner nav button(s) in the owner sidebar"
    groups = _scanner_groups(owner)
    assert groups, "expected the Scanner nav to sit inside an sb-group"


def test_no_sidebar_nav_group_reaches_a_customer_unmarked():
    """Deny-by-default, restated for composition: any sidebar group a customer
    DOES receive must have a pane they can actually reach."""
    customer = _composed("customer")
    for group in _GROUP_SPLIT.split(customer):
        if not group.startswith('<div class="sb-group'):
            continue
        for tab in re.findall(r'data-tab="(\w+)"', group):
            assert f'id="tab-{tab}"' in customer, (
                f"customer sidebar links to tab '{tab}' with no pane in their document")


def test_margin_panel_makes_no_lt_macro_node_claim():
    html = TEMPLATE.read_text() + _composed("owner")
    assert "LT_Macro" not in html, (
        "The long-term-macro / margin-debt panel must not reference an 'LT_Macro' "
        "consensus node — it is an advisory long-term macro overlay, not a node."
    )


def test_customer_home_pane_and_default_exist():
    html = TEMPLATE.read_text()
    assert 'id="tab-home"' in html
    assert 'data-tab="home"' in html
    assert "customer-only" in html            # role-gated nav class exists
    assert "buildHomeHero" in html            # audited builder name


def test_customer_book_analysis_leads_with_conclusions():
    """The dashboard's customer book-analysis renderer mirrors the report's
    conclusions-first pattern: one plain-language line per engine, mechanics
    behind <details> (plan T2)."""
    html = TEMPLATE.read_text()
    assert "Worst plausible" in html                  # Monte Carlo conclusion line
    assert "sizing adjustment" in html                # Kelly conclusion line
    assert "How this was computed" in html            # fold summary label
    # the "largest" headline pick is by |optimal-current| delta, not list order
    # (the list is sorted by optimal_pct DESC — PR #79 review, finding 1)
    assert "kAdjTop" in html and "Math.abs((k.optimal_pct||0)-(k.current_pct||0))" in html


# ─── structured-only AI output on customer surfaces (plan T3) ─────────────────

_PANE_RE = re.compile(r'<div class="tab-pane[^"]*" id="tab-([\w-]+)"')


def _find_all(html: str, needle: str) -> list[int]:
    return [m.start() for m in re.finditer(re.escape(needle), html)]


def _enclosing_pane_is_owner_only(html: str, pos: int) -> bool:
    """True iff the nearest preceding tab-pane opener's nav button is owner-only.
    Positions before any pane (or in the shared <script> after them, which the
    last pane would misattribute) are NOT provably owner-gated → False."""
    script_start = html.find("<script")
    panes = [(m.start(), m.group(1)) for m in _PANE_RE.finditer(html)]
    enclosing = None
    for start, pane_id in panes:
        if start <= pos:
            enclosing = pane_id
    if enclosing is None or (script_start != -1 and pos >= script_start):
        return False
    btn = re.search(rf'<button[^>]*data-tab="{enclosing}"[^>]*>', html)
    return bool(btn and "owner-only" in btn.group(0))


def test_customer_nav_has_no_commentary_surfaces():
    """Free-form model commentary must never be reachable from a customer surface.
    Every gemini/ollama commentary trigger (currently zero exist) must sit inside
    an owner-only-gated pane; the live enforcement is the server-side 403 guard
    (tests/test_mybook.py::test_gemini_commentary_rejects_customer_sessions)."""
    html = TEMPLATE.read_text()
    for needle in ("gemini/analyze", "gemini/market-commentary", "ollama/analyze"):
        for pos in _find_all(html, needle):
            assert _enclosing_pane_is_owner_only(html, pos), needle


def test_customer_book_analysis_market_context_is_lead_and_fold():
    """The dashboard's customer book-analysis market-context render uses the same
    lead + folded-prose pattern as the report (audited builder + fold label)."""
    html = TEMPLATE.read_text()
    assert "buildLeadFold" in html            # audited build*-named helper
    assert "Full context" in html             # fold summary label


# ─── news as attached sentiment — no generic feed for customers (plan T4) ─────

def test_ticker_card_uses_news_chip_not_feed():
    html = TEMPLATE.read_text()
    assert "buildNewsChip" in html            # audited build*-named chip builder
    assert "article_count" in html            # guards the zero-article rule
    # the raw article-headline card on the shared Analyze pane is owner-only
    idx = html.find("Recent News")
    assert idx != -1, "Recent News card expected in the Analyze pane"
    assert "owner-only" in html[max(0, idx - 300):idx], (
        "the article feed must be scoped owner-only — customers get the chip")
