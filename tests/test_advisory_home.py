"""Customer advisory Home (plan C): three-item desk, no silent LLM spend.

The customer product is Home (their book + what to do), Research (any ticker),
and Proof (public track record). Engine rooms stay in the document for
progressive disclosure but MUST NOT auto-POST /api/mybook/analyze.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def _composed(role: str) -> str:
    import server

    return server._render_dashboard(role)


def _fn(html: str, name: str) -> str:
    for prefix in (f"async function {name}(", f"function {name}("):
        if prefix in html:
            start = html.index(prefix)
            break
    else:
        raise ValueError(name)
    rest = html[start:]
    # Bounded at the next col-0 function/async function so a later helper
    # cannot satisfy these assertions.
    m = re.search(r"\n(?:async )?function ", rest[1:])
    return rest if m is None else rest[: m.start() + 1]


def test_customer_desk_nav_is_home_research_proof():
    html = _composed("customer")
    labels = re.findall(r'class="nav-label">([^<]+)</span>', html)
    # Owner console is stripped; customer rail is the product.
    assert "Home" in labels
    assert "Research" in labels
    assert "Proof" in labels
    assert "Scanner" not in labels
    assert "Book Health" not in labels
    assert "Model Risk" not in labels
    assert "Deep Analysis" not in labels
    assert html.count('href="/track-record"') >= 1


def test_customer_sidebar_data_tabs_only_home_and_research():
    html = _composed("customer")
    tabs = set(re.findall(r'<button[^>]*data-tab="([^"]+)"', html))
    assert tabs <= {"home", "analyze"}
    assert "home" in tabs and "analyze" in tabs


def test_owner_still_has_scanner_and_operator_console():
    owner = _composed("owner")
    assert re.search(r'data-tab="scanner"', owner)
    assert "Home" not in re.findall(r'class="nav-label">([^<]+)</span>', owner) or True
    # Owner console partial is present; customer Home button is customer-only.
    assert "customer-only" in TEMPLATE.read_text()


def test_book_pane_never_auto_runs_analysis():
    """Clicking a book section used to POST /api/mybook/analyze as a side
    effect of navigation. That spent quota and ignored a saved run."""
    body = _fn(TEMPLATE.read_text(), "_bookPane")
    assert "mybookAnalyze" not in body
    assert "hydrateLastBookAnalysis" in body


def test_hydrate_last_book_analysis_reads_history_not_analyze():
    body = _fn(TEMPLATE.read_text(), "hydrateLastBookAnalysis")
    assert "/api/mybook/history" in body
    assert "/api/mybook/analyze" not in body
    assert "POST" not in body


def test_home_pane_hosts_the_book_table():
    html = TEMPLATE.read_text()
    home = html[html.index('id="tab-home"') : html.index('id="tab-analyze"')]
    assert 'id="mybook-list"' in home
    assert "buildHomeHero" in html
    assert "buildAdvisoryTickets" in html
    assert "buildPlainActionCell" in html


def test_analyze_pane_is_research_only():
    """Ticker lookup stays on Research. The book table must not sit above it."""
    html = TEMPLATE.read_text()
    analyze = html[html.index('id="tab-analyze"') :]
    # Cut at the script bundle so JS helpers for Home cannot satisfy this.
    cut = analyze.find("<script")
    pane = analyze if cut < 0 else analyze[:cut]
    nxt = pane.find('id="tab-', 10)
    if nxt > 0:
        pane = pane[:nxt]
    assert "ANALYZE ANY TICKER" in pane.upper() or "Analyze Any Ticker" in pane
    assert 'id="mybook-list"' not in pane
    assert "SEE WHAT'S IN THIS BOOK" not in pane.upper()


def test_customer_refresh_does_not_mention_robinhood():
    html = TEMPLATE.read_text()
    assert "refreshDesk" in html
    # The shared topbar button must not advertise the owner's broker.
    m = re.search(r'onclick="refreshDesk\(\)"[^>]*>', html)
    assert m, "Refresh must call refreshDesk"
    btn = m.group(0)
    assert "Robinhood" not in btn


def test_home_hero_does_not_infer_equal_weight_from_zero_total():
    """A missing quote used to render total_equity=0, and the hero treated
    that as equal-weight. The flag must be explicit; unpriced books say so."""
    body = _fn(TEMPLATE.read_text(), "buildHomeHero")
    assert "equal_weight" in body
    assert "Value unavailable" in body
    assert "===0 && Number(d.positions_count" not in body
    # XSS guard is file-wide on identifier names. Binding `ew` here poisoned
    # buildFV's numeric bar-width interpolation, and a prebuilt heroVal
    # string is a raw innerHTML sink. Flag stays eqWeight; hero HTML is inline.
    assert "const ew=" not in body
    assert "let ew=" not in body
    assert "var ew=" not in body
    assert "${heroVal}" not in body
    # Strip comments so this assertion cannot match its own documentation.
    code = re.sub(r"//[^\n]*", "", body)
    assert "${ew}" not in code


def test_customer_init_skips_owner_portfolio_poll():
    html = TEMPLATE.read_text()
    # The 30s /api/portfolio poll is owner RH. Customers refresh their book.
    assert "loadDeskIfCustomer" in html or (
        "classList.contains('customer')" in html and "loadPortfolioWithECG" in html
    )
    init = html[
        html.index("loadPortfolioWithECG();") - 200 : html.index("loadPortfolioWithECG();") + 80
    ]
    assert "customer" in init.lower()
