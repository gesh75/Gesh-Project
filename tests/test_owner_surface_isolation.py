"""Owner/operator surface isolation — a customer session must not RECEIVE the
operator console, only be prevented from seeing it.

Audit findings A-01 / A-02 / F-03 (2026-07-22, round 2):

  * The customer dashboard shipped the full owner operations console — Open
    Positions, Buying Power, Equity-Curve Gate, Scanner, Paper Trade Log,
    Risk-Gate dry-run, Bull/Bear debate, Daily Analysis, and header links to
    /watch, /invest, /usage ("Per-customer LLM spend"). It was hidden with
    `body.customer .owner-only{display:none}` — a CSS rule, i.e. the markup was
    delivered and one devtools toggle away. Several of its buttons then called
    endpoints that 404/403 for the caller, which is the tell that the widget
    never belonged in that document.
  * GET /api/config returned the owner's trading-engine configuration
    (allocation %, per-trade caps, max open positions, cash buffer, scanner
    params, auto_trade, paper_mode) to any customer. Writes were correctly
    owner-gated; the read was not gated at all.

The fix is server-side on both counts: owner-only markup is stripped from the
document before it is sent to a customer, and the config READ uses the same
owner dependency as the config WRITE.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

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
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 2.0, raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, c, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, ck: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")


# ─── A-02: owner engine configuration ─────────────────────────────────────────

_OWNER_CONFIG_KEYS = (
    "max_stock_per_trade", "max_options_per_trade", "max_open_positions",
    "min_cash_buffer", "auto_trade", "paper_mode", "scanner_timeframe",
)


def test_config_read_is_owner_only(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, c, CUSTOMER)
    r = c.get("/api/config")
    assert r.status_code == 403, f"customer read owner config: {r.status_code} {r.text[:200]}"
    for key in _OWNER_CONFIG_KEYS:
        assert key not in r.text


def test_config_read_still_works_for_owner(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, c, OWNER)
    r = c.get("/api/config")
    assert r.status_code == 200, r.text
    assert "max_open_positions" in r.json()["data"]


def test_config_write_stays_owner_only(client, monkeypatch):
    """The existing write protection must not regress while fixing the read."""
    server, c = client
    _as(server, monkeypatch, c, CUSTOMER)
    assert c.post("/api/config", json={"max_open_positions": 99}).status_code == 403


_OWNER_LEDGER_ROUTES = (
    "/api/trades",
    "/api/trades/stats",
    "/api/paper/summary",
)


@pytest.mark.parametrize("route", _OWNER_LEDGER_ROUTES)
def test_trade_ledger_reads_are_owner_only(client, monkeypatch, route):
    server, c = client
    _as(server, monkeypatch, c, CUSTOMER)

    response = c.get(route)

    assert response.status_code == 403
    assert "PRIVATE_LEDGER_SENTINEL" not in response.text


@pytest.mark.parametrize("route", _OWNER_LEDGER_ROUTES)
def test_trade_ledger_reads_still_work_for_owner(client, monkeypatch, route):
    server, c = client
    _as(server, monkeypatch, c, OWNER)
    monkeypatch.setattr(
        server,
        "get_trade_history",
        lambda **_kwargs: [{"symbol": "PRIVATE_LEDGER_SENTINEL"}],
    )
    monkeypatch.setattr(
        server,
        "trade_summary_for_report",
        lambda **_kwargs: {"marker": "PRIVATE_LEDGER_SENTINEL"},
    )
    monkeypatch.setattr(
        server,
        "get_paper_summary",
        lambda **_kwargs: {"marker": "PRIVATE_LEDGER_SENTINEL"},
    )

    response = c.get(route)

    assert response.status_code == 200, response.text
    assert "PRIVATE_LEDGER_SENTINEL" in response.text


# ─── A-01 / F-03: the operator console must not be delivered ─────────────────

# Every operator widget the audit named, by the artefact that identifies it:
# element id, endpoint reference, or the on-screen title an operator recognises.
_OWNER_MARKUP_TELLS = (
    # header links to owner-only routes
    'href="/watch"',
    'href="/invest"',
    'href="/usage"',            # "Per-customer usage counters (LLM spend)"
    # Open Positions / account KPI strip
    'id="s-equity"',            # "Portfolio Value"
    'id="s-bp"',                # "Buying Power"
    'id="kpi-day"',
    'id="tab-overview"',        # the Open Positions pane itself
    # operator panes
    'id="tab-trading"',         # Paper Trade Log
    'id="tab-signals"',         # Execution / Signals
    'id="tab-scanner"',         # Scanner configuration
    'id="tab-daily"',           # Daily Analysis
    'id="tab-radar"',
    'id="tab-intelligence"',
    'id="tab-risk"',            # Equity-Curve Gate lives here
    'id="tab-analysis"',
)

# On-screen titles and handler/endpoint references an operator would recognise.
# Titles are checked in the DELIVERED DOM; endpoints in the whole document.
_OWNER_WIDGET_TITLES = (
    "Paper Trade Log", "Risk-Gate", "Bull", "Scanner Config",
    "Portfolio Value", "Buying Power", "Equity Curve",
)
# Endpoints that are operator-only. NOTE `/api/consensus/{sym}`, `/api/news`,
# `/api/regime` etc. are deliberately NOT here — they are the customer
# "analyse any share" surface, and banning them outright would be wrong.
_OWNER_ENDPOINT_REFS = (
    "/api/trading/dry-run", "/api/paper/run", "/api/trades/stats",
    "/api/agents/fundamentals", "/api/screener", "/api/config",
)

# Operator actions that must have no reachable control in a customer document.
_OWNER_ACTIONS = (
    "runDryRun", "runScreener", "runAllAgents", "loadScannerConfig",
    "saveScannerTf", "loadTradeStats", "runConsensus", "runKelly",
    "runMonteCarlo", "daRunAnalysis", "daLoadHistory", "loadCachedScreener",
)


def _customer_html(server, monkeypatch, c) -> str:
    _as(server, monkeypatch, c, CUSTOMER)
    r = c.get("/")
    assert r.status_code == 200
    return r.text


def test_customer_document_has_no_owner_only_markup(client, monkeypatch):
    server, c = client
    html = _customer_html(server, monkeypatch, c)
    assert 'data-initial-role="customer"' in html
    # No element may still carry the owner-only marker in the delivered body.
    body = html.split("</head>", 1)[-1]
    # The class may still be *defined* in CSS; it must not be *applied* to markup
    # outside <script> (script strings are template source, not delivered DOM).
    stripped = re.sub(r"<script\b.*?</script>", "", body, flags=re.S | re.I)
    assert 'class="owner-only' not in stripped
    assert "owner-only\"" not in re.sub(r"<style\b.*?</style>", "", stripped, flags=re.S | re.I)


def _delivered_dom(html: str) -> str:
    """The document minus <script>/<style> bodies — i.e. what actually becomes DOM.

    Script SOURCE may legitimately still mention an owner path (e.g. a defensive
    `querySelector('a[href="/invest"]')` that no-ops when the element is absent);
    that is dead code, not a delivered widget. The authorization question is only
    ever about the markup."""
    out = re.sub(r"<script\b.*?</script>", "", html, flags=re.S | re.I)
    return re.sub(r"<style\b.*?</style>", "", out, flags=re.S | re.I)


@pytest.mark.parametrize("tell", _OWNER_MARKUP_TELLS)
def test_customer_document_omits_operator_widgets(client, monkeypatch, tell):
    server, c = client
    dom = _delivered_dom(_customer_html(server, monkeypatch, c))
    assert tell not in dom, f"operator markup delivered to a customer: {tell}"


def test_owner_document_keeps_the_operator_console(client, monkeypatch):
    """Stripping must be scoped to customers — the owner console stays intact."""
    server, c = client
    _as(server, monkeypatch, c, OWNER)
    html = c.get("/").text
    assert 'data-initial-role="owner"' in html
    for tell in _OWNER_MARKUP_TELLS:
        assert tell in html, f"owner lost their own console: {tell}"


def test_customer_document_keeps_the_customer_surface(client, monkeypatch):
    """The strip must not take the customer's own product with it."""
    server, c = client
    html = _customer_html(server, monkeypatch, c)
    for keep in ('id="tab-home"', 'id="tab-analyze"', 'id="mybook-list"',
                 "Research", "Proof"):
        assert keep in html, f"customer surface lost: {keep}"


@pytest.mark.parametrize("title", _OWNER_WIDGET_TITLES)
def test_customer_dom_shows_no_operator_widget_titles(client, monkeypatch, title):
    server, c = client
    dom = _delivered_dom(_customer_html(server, monkeypatch, c))
    assert title not in dom, f"operator widget title delivered to a customer: {title!r}"


@pytest.mark.parametrize("endpoint", _OWNER_ENDPOINT_REFS)
def test_customer_dom_references_no_owner_endpoints(client, monkeypatch, endpoint):
    """Widgets whose buttons called endpoints that 403/404 for the caller were
    the tell that they never belonged in the document (audit F-03)."""
    server, c = client
    dom = _delivered_dom(_customer_html(server, monkeypatch, c))
    assert endpoint not in dom, f"owner endpoint in a customer's DOM: {endpoint}"


@pytest.mark.parametrize("action", _OWNER_ACTIONS)
def test_no_reachable_operator_control_in_a_customer_document(client, monkeypatch, action):
    """The load-bearing assertion: an operator ACTION must have no invocable
    control. Function bodies may still be present in the shared script bundle
    (see the residue test below); a control that fires one must not be."""
    server, c = client
    html = _customer_html(server, monkeypatch, c)
    handlers = re.findall(rf'on\w+\s*=\s*"[^"]*\b{re.escape(action)}\s*\(', html)
    assert not handlers, f"customer document can invoke operator action {action}()"


def test_operator_javascript_residue_is_zero(client, monkeypatch):
    """Replaces the earlier test that PINNED the residue as a tracked limitation.

    Operator JS now lives in its own script block inside the owner partial
    (templates/owner_console.html) and is spliced only for an owner session, so
    the customer bundle carries no operator function definitions at all. Full
    coverage lives in tests/test_owner_js_extraction.py."""
    server, c = client
    html = _customer_html(server, monkeypatch, c)
    still_defined = [a for a in _OWNER_ACTIONS
                     if re.search(rf"function\s+{re.escape(a)}\b", html)]
    assert not still_defined, f"operator JS still in the customer bundle: {still_defined}"


# ─── composition is the mechanism; stripping is only a net ───────────────────

def test_owner_console_lives_in_its_own_partial(client):
    """The operator console must not sit in the shell waiting to be cut out."""
    server, _ = client
    assert server.OWNER_PARTIAL.exists()
    blocks = server._owner_blocks()
    assert blocks, "owner console partial parsed to nothing"
    shell = server.HTML.read_text()
    for name, body in blocks.items():
        assert body not in shell, f"{name} is still inline in the shell"
        assert f"<!--GESH_SLOT:{name}-->" in shell, f"{name} has no slot in the shell"


def test_customer_render_never_opens_the_partial(client, monkeypatch):
    """Assembly, not removal: the customer document is built without the owner
    console ever being read, let alone concatenated and then cut."""
    server, _ = client
    monkeypatch.setattr(server, "_owner_blocks",
                        lambda: pytest.fail("owner partial read while rendering for a customer"))
    out = server._render_dashboard("customer")
    assert "GESH_SLOT" not in out


def test_every_slot_resolves_for_the_owner(client):
    server, _ = client
    out = server._render_dashboard("owner")
    assert not server._SLOT_RE.findall(out), "unresolved owner slot"
    for body in server._owner_blocks().values():
        assert body in out


def test_stripping_is_now_a_no_op_for_the_customer_render(client):
    """The shell should contain NO owner-only markup, so the secondary net has
    nothing left to do. If this ever fails, someone put operator UI back into
    the shell — fix that, don't rely on the strip."""
    server, _ = client
    composed = server._render_dashboard("customer")
    assert server._strip_owner_only_html(composed) == composed


def test_composed_documents_are_well_formed(client):
    """Both renders must still parse: balanced <script>/<style>, no truncation."""
    server, _ = client
    for role in ("owner", "customer"):
        out = server._render_dashboard(role)
        assert out.lower().count("<script") == out.lower().count("</script>")
        assert out.lower().count("<style") == out.lower().count("</style>")
        assert out.count("</html>") == 1 and out.count("</body>") == 1


def test_stripper_is_script_and_style_safe(client):
    """The secondary net, exercised directly against a document that DOES carry
    owner-only markup — it must never cut into a <script> template literal."""
    server, _ = client
    raw = ('<div class="owner-only"><b>x</b></div>'
           + server.HTML.read_text()
           + '<div class="a owner-only">y</div>')
    out = server._strip_owner_only_html(raw)
    assert out.lower().count("<script") == raw.lower().count("<script")
    assert out.lower().count("</script>") == raw.lower().count("</script>")
    assert out.lower().count("<style") == raw.lower().count("<style")
    assert out.count("</html>") == raw.count("</html>")
    assert len(out) < len(raw)          # something was actually removed


def test_stripper_unit_cases():
    import server
    s = server._strip_owner_only_html
    assert s('<div class="owner-only">x</div><p>keep</p>') == "<p>keep</p>"
    # nested same-tag elements must not close the span early
    assert s('<div class="a owner-only"><div>in</div></div>ok') == "ok"
    # class token must match exactly — "owner-only-ish" is a different class
    assert "keep" in s('<div class="owner-only-ish">keep</div>')
    # script content is untouched
    src = '<script>var h = `<div class="owner-only">t</div>`;</script>'
    assert s(src) == src
    # an unbalanced/unknown shape is left alone rather than corrupting the doc
    assert s('<div class="owner-only">oops') == '<div class="owner-only">oops'
