"""Customer dashboard polish: charts, radar cash sizing, and analysis history.

Three gaps this pins shut, all found by reading the PRODUCERS rather than the
rendered page:

1. **No charts on any customer pane.** Chart.js was loaded and used seven times by
   the owner console and zero times by the customer's own panes. The Model Risk
   pane now draws `monte_carlo.buckets` (run_monte_carlo's own 10-bucket outcome
   histogram) and Book Health draws `states[].equity_history` (the per-user series
   `customer_equity_history.compute_book_ecg` returns). Both go through the
   Chart.js CANVAS API, never innerHTML — a canvas dataset cannot carry markup,
   which is why `tests/test_dashboard_xss_wiring.py` has nothing to say about it.

2. **`POST /api/mybook/radar-cash` had no UI at all.** The endpoint existed, was
   tested, and was unreachable from the product.

3. **No way to see a past run.** `GET /api/mybook/history` replays a stored
   analysis for zero LLM spend; without a control, re-running (and re-paying) was
   the only way to look at last week's verdict.

Plus one REGRESSION PIN. The Intelligence pane read `f.summary` on a SEC row.
Neither producer behind `sec_agent.get_filing_history` emits that key at the top
level — `_get_recent_from_db` json-loads the stored analysis into `analysis`, and
`discover_filing_cards` nests `{"analysis": {"summary": …}}`. The field was
undefined on every row, so the panel rendered an empty div per filing: right
shape, no content. That is the same failure mode as the invented-fixture panel
this pane was already fixed for once, so it gets a test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"
CUSTOMER = "cust@x.com"
OWNER = "georgy75@gmail.com"


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in (CUSTOMER, OWNER):
        _au.create_user(email, "test-password")
    return _au


@pytest.fixture
def client(au, monkeypatch):
    import server

    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _customer_html(server, monkeypatch, c) -> str:
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: CUSTOMER)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, k: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.get("/")
    assert r.status_code == 200
    return r.text


def _js() -> str:
    return "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", TEMPLATE.read_text(), re.S))


# ─── 1. charts ───────────────────────────────────────────────────────────────


def test_risk_pane_draws_the_monte_carlo_distribution():
    js = _js()
    assert "function drawBookMcChart(" in js
    # Reads run_monte_carlo's OWN keys: buckets[{center,pct}] + current_equity.
    assert "drawBookMcChart(mc.buckets, mc.current_equity)" in js
    assert "book-mc-canvas" in js
    for key in ("b.center", "b.pct"):
        assert key in js, f"MC chart stopped reading the producer key {key}"


def test_book_health_draws_the_stored_equity_series():
    js = _js()
    assert "function drawBookEquityChart(" in js
    assert "drawBookEquityChart(states)" in js
    assert "book-equity-canvas" in js
    # states[].equity_history is the ONLY series source — never db.get_equity_history,
    # which is symbol-keyed owner data with no user column.
    assert "s.equity_history" in js
    assert "get_equity_history" not in js


def test_charts_never_reach_innerhtml():
    """Chart data must travel through the canvas API. If a future edit interpolates
    a series into markup, the XSS guard would flag it — this states the intent."""
    js = _js()
    for fn in ("drawBookMcChart", "drawBookEquityChart"):
        body = js.split("function " + fn, 1)[1].split("\nfunction ", 1)[0]
        assert "innerHTML" not in body, f"{fn} writes markup instead of using the canvas"


def test_customer_document_can_reach_both_canvases(client, monkeypatch):
    server, c = client
    html = _customer_html(server, monkeypatch, c)
    assert 'id="book-risk-body"' in html and 'id="book-ecg-body"' in html
    assert "drawBookMcChart" in html and "drawBookEquityChart" in html


# ─── 2. radar cash ───────────────────────────────────────────────────────────


def test_radar_cash_has_a_control_and_calls_the_real_endpoint(client, monkeypatch):
    server, c = client
    html = _customer_html(server, monkeypatch, c)
    assert 'id="radar-cash-amount"' in html
    assert 'id="radar-cash-results"' in html
    assert "radarCash()" in html
    assert "'/api/mybook/radar-cash'" in html


def test_radar_cash_card_reads_only_producer_keys():
    """Keys are server._radar_allocation's + the envelope the endpoint returns."""
    js = _js()
    body = js.split("function buildRadarCashCard", 1)[1].split("\n// Diversification Radar", 1)[0]
    for key in (
        "a.symbol",
        "a.sector",
        "a.price",
        "a.dollars",
        "a.shares",
        "a.max_corr_to_holdings",
        "a.rationale",
        "d.stale",
        "d.mode",
        "d.cash",
        "d.deployable_cash",
        "d.new_names_to_add",
        "d.leftover",
    ):
        assert key in body, f"radar cash card stopped reading {key}"
    # An unsized SWAP_ONLY preview arrives with dollars/shares null; it must read as
    # "—", never as $0, and the mode must be stated.
    assert "SWAP_ONLY" in body
    assert "a.dollars!=null" in body and "a.shares!=null" in body


# ─── 3. analysis history ─────────────────────────────────────────────────────


def test_history_has_a_control_and_calls_the_real_endpoints(client, monkeypatch):
    server, c = client
    html = _customer_html(server, monkeypatch, c)
    assert 'id="mybook-history-body"' in html
    assert "mybookHistory(true)" in html
    assert "'/api/mybook/history'" in html
    assert "'/api/mybook/history/'" in html


def test_history_list_reads_only_producer_keys():
    """Keys are server._history_entry's own dict literal."""
    js = _js()
    body = js.split("function buildMybookHistoryList", 1)[1].split("\n// Replay one stored", 1)[0]
    for key in (
        "h.saved_at",
        "h.is_current_book",
        "h.parse_error",
        "h.portfolio_verdict",
        "h.portfolio_score",
        "h.positions_count",
        "h.symbols",
        "h.id",
        "d.max_kept",
    ):
        assert key in body, f"history list stopped reading {key}"


def test_a_corrupt_history_row_is_shown_not_dropped():
    """auth_users.list_book_analyses KEEPS an unreadable row on purpose; hiding it
    in the UI would undo that honesty."""
    js = _js()
    body = js.split("function buildMybookHistoryList", 1)[1].split("\n// Replay one stored", 1)[0]
    assert "h.parse_error" in body
    assert "could not be read back" in body


def test_replay_uses_the_same_renderer_and_spends_nothing():
    js = _js()
    assert "async function mybookRenderResult(" in js
    # both the fresh run and the replay funnel through it
    assert "await mybookRenderResult(d, box, null)" in js
    assert "await mybookRenderResult(saved, box, (j&&j.meta)||null)" in js
    # the replay banner is driven by the server's own staleness verdict
    assert "meta.is_current_book" in js
    assert "no new AI usage" in js


# ─── regression pin: the SEC summary is nested ───────────────────────────────


def test_sec_summary_is_read_from_the_nested_analysis_key():
    js = _js()
    body = js.split("function buildBookIntelligencePane", 1)[1].split("\n// Book-health gate", 1)[0]
    # Asserted as an INVARIANT, not as a spelling. The original pinned the exact
    # string "(f.analysis || {}).summary", which failed against an equivalent
    # `x.analysis && x.analysis.summary` written elsewhere — a test that breaks on
    # a rename while the behaviour is unchanged trains people to edit the test.
    # What must hold: the summary is reached THROUGH .analysis, and is never read
    # off the row top level, where every producer leaves it undefined.
    sec_block = body.split("const newsRows", 1)[0]
    assert re.search(r"\.analysis\s*(?:\|\||&&)[^;]*\.summary", sec_block), (
        "SEC summary must be reached through the nested .analysis object"
    )
    assert not re.search(r"daEsc\(\s*(\w+)\.summary\b", sec_block), (
        "summary was read off the row top level, where every producer leaves it undefined"
    )


def test_intelligence_pane_discloses_its_coverage_gaps():
    """Both legs report their own gaps (`sec.truncated`, `fundamentals.errors`);
    dropping them makes a capped scan look like an empty result."""
    js = _js()
    body = js.split("function buildBookIntelligencePane", 1)[1].split("\n// Book-health gate", 1)[0]
    assert "sec.truncated" in body and "sec.symbol_cap" in body
    assert "fund.errors" in body


def test_intelligence_pane_does_not_treat_parse_error_as_a_signal():
    """Failed LLM parses must not render as PARSE_ERROR · urgency UNKNOWN."""
    js = _js()
    body = js.split("function buildBookIntelligencePane", 1)[1].split("\n// Book-health gate", 1)[0]
    assert "PARSE_ERROR" in body
    assert "sigU === 'PARSE_ERROR'" in body or 'sigU === "PARSE_ERROR"' in body
    assert "typeof news.avg_score === 'number'" in body
    assert "news.headlines" in body
    assert "fundPct" in body


# ─── the new UI must not smuggle in operator vocabulary ──────────────────────


_OWNER_WIDGET_TITLES = (
    "Paper Trade Log",
    "Risk-Gate",
    "Bull",
    "Scanner Config",
    "Portfolio Value",
    "Buying Power",
    "Equity Curve",
)

# The blocks this change added. Scoped to them on purpose: a whole-file assertion
# would trip on pre-existing OWNER code ("Bull/Bear debate"), which
# test_owner_surface_isolation already handles by checking the DELIVERED DOM.
_NEW_BLOCK_MARKERS = (
    ("radar-cash-amount", "radar-cash-results"),
    ("mybook-history-body", "Where Does New Cash Go?"),
    ("function drawBookMcChart", "function buildBookRiskPane"),
    ("function drawBookEquityChart", "function buildBookRiskPane"),
    ("function buildRadarCashCard", "// Diversification Radar"),
    ("function buildMybookHistoryList", "// Replay one stored"),
)


@pytest.mark.parametrize("title", _OWNER_WIDGET_TITLES)
def test_new_customer_copy_uses_no_operator_widget_titles(title):
    """Operator vocabulary must not ride in on the new panes — including via a JS
    comment, which ships inside the delivered document."""
    text = TEMPLATE.read_text()
    for start, end in _NEW_BLOCK_MARKERS:
        assert start in text, f"marker vanished: {start}"
        block = text.split(start, 1)[1].split(end, 1)[0]
        assert title not in block, f"operator title {title!r} appeared near {start}"
