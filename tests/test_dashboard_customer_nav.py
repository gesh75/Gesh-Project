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

# Split the markup on each sidebar GROUP container `<div class="sb-group"…>` /
# `<div class="sb-group owner-only">` — but NOT on `<div class="sb-group-title">`
# (the `-` after `sb-group` excludes it), so each chunk holds one whole group.
_GROUP_SPLIT = re.compile(r'(?=<div class="sb-group(?:"| ))')
_SCANNER_BTN = re.compile(r'<button[^>]*data-tab="scanner"[^>]*>')


def _scanner_groups(html: str) -> list[str]:
    return [g for g in _GROUP_SPLIT.split(html) if 'data-tab="scanner"' in g]


def test_scanner_nav_is_owner_only_for_customers():
    html = TEMPLATE.read_text()
    groups = _scanner_groups(html)
    assert groups, "expected at least one Scanner sidebar nav item to check"
    for group in groups:
        group_open = group[: group.find(">") + 1]        # the <div class="sb-group…"> tag
        group_owner_only = "owner-only" in group_open
        for btn in _SCANNER_BTN.findall(group):
            assert group_owner_only or "owner-only" in btn, (
                "Scanner sidebar nav must be owner-only (deny-by-default) so it is "
                "hidden from customers — the button or its sb-group needs the "
                f"owner-only class:\n  {btn}"
            )


def test_at_least_one_scanner_nav_is_explicitly_owner_only():
    """Teeth: prove the customer-facing Scanner button actually carries the class
    (guards against the whole nav block being deleted, which would vacuously pass
    the group-level check above)."""
    html = TEMPLATE.read_text()
    btns = _SCANNER_BTN.findall(html)
    assert btns, "expected Scanner nav button(s) in the sidebar"
    assert any("owner-only" in b for b in btns), (
        "no Scanner nav button carries owner-only — the customer-facing Scanner "
        "item must be marked owner-only"
    )


def test_margin_panel_makes_no_lt_macro_node_claim():
    html = TEMPLATE.read_text()
    assert "LT_Macro" not in html, (
        "The long-term-macro / margin-debt panel must not reference an 'LT_Macro' "
        "consensus node — it is an advisory long-term macro overlay, not a node."
    )
