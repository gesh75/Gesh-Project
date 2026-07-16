# Customer Dashboard Simplification (REMOVE/DE-EMPHASIZE 1–5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape the customer-facing surface around one hero answer (book value + what changed + one next action), push engine mechanics behind progressive disclosure, keep AI output structured, attach news as sentiment (no feed), and make the book table earn its place with signal columns.

**Architecture:** All changes concentrate on the two customer surfaces: the Analyze tab in `templates/dashboard.html` (role-gated via `body.customer`) and the server-rendered `/mybook/report`. One new lightweight endpoint (`GET /api/mybook/summary`) powers the hero; the book table is enriched server-side inside the existing `/api/mybook` payload (no extra round-trips). The owner's dense 8-tab view is untouched — density stays an owner feature.

**Tech Stack:** FastAPI (server.py), vanilla JS + `build*` renderers in dashboard.html, `signal_history.py` (from the Retention Features plan, Task 1 — see Dependencies).

## Global Constraints

- Owner view is UNCHANGED — every removal/de-emphasis applies to `body.customer` surfaces and `/mybook/report` only.
- Customer JS HTML builders: `build*` naming + escape every `${…}` (`tests/test_dashboard_xss_wiring.py` guard).
- `/mybook/report` server-rendered strings all `html.escape()`d.
- No new LLM spend anywhere in this plan; every data element is a cached/DB read.
- Dashboard endpoints keep the `{status, data}` envelope; frontend readers unwrap.
- Fail-soft: a missing `signal_history` table or absent analysis renders an empty state, never a 500.
- Never remove or weaken `/track-record` links or signal explainability.
- Tests first (RED→GREEN); `./venv/bin/python3 -m pytest tests/ -q` + ruff clean on changed files before each commit. Conventional commits, no attribution trailer.

## Dependencies

- **Task 1 and Task 5 consume `signal_history.get_history`** from `docs/superpowers/plans/2026-07-16-retention-features.md` Task 1. If that plan hasn't shipped yet, implement its Task 1 first (it is self-contained) — or ship these tasks with the `signals` blocks empty-defaulted (both are written fail-soft, so order is a preference, not a blocker).
- Staleness fingerprint `_fp` (PR #72) is already on main.

---

### Task 1: Customer "Home" hero — one answer above the fold

Replaces the 8-tab landing for customers with: book value • what changed on your names • last verdict • one next action. (Research: "users disengage when the default view doesn't answer their most important question fast enough"; Koyfin is ranked last on exactly this.)

**Files:**
- Modify: `server.py` (add `GET /api/mybook/summary`)
- Modify: `templates/dashboard.html` (new `tab-home` pane + `buildHomeHero(d)`; customer default tab becomes `home`)
- Test: `tests/test_mybook.py` (endpoint), `tests/test_dashboard_customer_nav.py` (default-tab wiring)

**Interfaces:**
- Consumes: `auth_users.get_positions/get_watch/get_latest_book_analysis`, `_cached_close`, `_book_fingerprint` (existing), `signal_history.get_history` (fail-soft import).
- Produces: `GET /api/mybook/summary` → `{status:"ok", data:{total_equity, positions_count, watch_count, last_verdict: str|null, analysis_stale: bool, analyzed_at: str|null, top_changes:[{symbol, prior_verdict, new_verdict, date}] (≤3), next_action: {label, target}}}`.
  - `next_action` decision ladder: no positions → `{"label":"Add your holdings","target":"book"}`; no analysis → `{"label":"Analyze my portfolio","target":"analyze"}`; `analysis_stale` → `{"label":"Re-run analysis — your book changed","target":"analyze"}`; else → `{"label":"View your report","target":"report"}`.

- [ ] **Step 1: Failing endpoint tests** (append to `tests/test_mybook.py`):

```python
def test_mybook_summary_shapes_hero_payload(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)          # NVDA=100 in this fixture
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {"portfolio_verdict": "HOLD"}, "kelly": {}, "monte_carlo": {},
        "book": {"positions": [], "unpriced": []}})
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["total_equity"] == 1000.0 and d["positions_count"] == 1
    assert d["last_verdict"] == "HOLD" and d["analysis_stale"] is False
    assert d["next_action"]["target"] == "report"
    assert isinstance(d["top_changes"], list) and len(d["top_changes"]) <= 3


def test_mybook_summary_next_action_ladder(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    # empty book → add holdings
    assert c.get("/api/mybook/summary").json()["data"]["next_action"]["target"] == "book"
    # book, no analysis → analyze
    au.set_position("cust@x.com", "NVDA", 10)
    assert c.get("/api/mybook/summary").json()["data"]["next_action"]["target"] == "analyze"
    # stale analysis → re-run
    au.save_book_analysis("cust@x.com", {"_fp": "other-book", "analysis": {},
                                         "kelly": {}, "monte_carlo": {},
                                         "book": {"positions": [], "unpriced": []}})
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["analysis_stale"] is True and d["next_action"]["target"] == "analyze"


def test_mybook_summary_survives_missing_signal_history(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    import builtins
    real_import = builtins.__import__
    def _no_sighist(name, *a, **k):
        if name == "signal_history":
            raise ImportError("simulated absence")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _no_sighist)
    r = c.get("/api/mybook/summary")
    assert r.status_code == 200 and r.json()["data"]["top_changes"] == []
```

- [ ] **Step 2: RED** — `./venv/bin/python3 -m pytest tests/test_mybook.py -k summary -q` → 404s

- [ ] **Step 3: Implement the endpoint** (server.py, with the other mybook routes):

```python
@app.get("/api/mybook/summary")
def mybook_summary(request: Request):
    """Hero payload for the customer Home pane: value, what changed, one next
    action. Cached/DB reads only — this endpoint must stay instant and free."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    positions = auth_users.get_positions(email)
    watch = auth_users.get_watch(email)
    ew = auth_users.get_equal_weight(email)
    total = 0.0 if ew else sum(
        p["shares"] * (_cached_close(p["symbol"]) or 0) for p in positions)
    saved = auth_users.get_latest_book_analysis(email)
    stale = bool(saved and saved.get("_fp")
                 and saved["_fp"] != _book_fingerprint(positions) + ("|ew" if ew else ""))
    top_changes: list[dict] = []
    try:
        import signal_history
        syms = sorted({p["symbol"] for p in positions} | set(watch))
        flips = signal_history.detect_confirmed_flips(syms, persistence_days=2)
        top_changes = [{"symbol": f["symbol"], "prior_verdict": f["prior_verdict"],
                        "new_verdict": f["new_verdict"], "date": f["confirmed_since"]}
                       for f in flips[:3]]
    except Exception:  # noqa: BLE001 — hero must render without signal history
        pass
    if not positions:
        action = {"label": "Add your holdings", "target": "book"}
    elif not saved:
        action = {"label": "Analyze my portfolio", "target": "analyze"}
    elif stale:
        action = {"label": "Re-run analysis — your book changed", "target": "analyze"}
    else:
        action = {"label": "View your report", "target": "report"}
    return {"status": "ok", "data": {
        "total_equity": round(total, 2), "positions_count": len(positions),
        "watch_count": len(watch),
        "last_verdict": ((saved or {}).get("analysis") or {}).get("portfolio_verdict"),
        "analysis_stale": stale,
        "analyzed_at": (saved or {}).get("_saved_at"),
        "top_changes": top_changes, "next_action": action}}
```

- [ ] **Step 4: Frontend.** In `dashboard.html`:
  1. Add a pane `<div class="tab-pane" id="tab-home">…</div>` containing an empty `<div id="home-hero"></div>`.
  2. Add nav item `<button class="nav-item customer-only" data-tab="home" …>Home</button>` (add a `.customer-only` CSS rule mirroring how `.owner-only` hides: `body:not(.customer) .customer-only{display:none}`).
  3. In the initial-role JS (the block around line 503 that currently fronts the analyze tab for customers), front `home` instead and call `loadHomeHero()`.
  4. Renderer:

```javascript
async function loadHomeHero(){
  const el=document.getElementById('home-hero'); if(!el) return;
  try{
    const body=await (await fetch('/api/mybook/summary')).json();
    el.innerHTML=buildHomeHero(body.data||{});
  }catch(e){ el.innerHTML='<div class="deep-placeholder"><div class="deep-ph-text">Could not load your summary — retry shortly.</div></div>'; }
}
function buildHomeHero(d){
  const changes=(d.top_changes||[]).map(t=>
    `<li><b>${daEsc(t.symbol)}</b> ${daEsc(t.prior_verdict)} &rarr; <b>${daEsc(t.new_verdict)}</b> <span style="color:var(--muted)">(${daEsc(t.date)})</span></li>`).join('')
    ||'<li style="color:var(--muted)">No confirmed signal changes on your names this week.</li>';
  const stale=d.analysis_stale?`<div style="color:var(--yellow);font-size:12px">&#9888; Your last analysis predates your current book.</div>`:'';
  const act=d.next_action||{};
  return `<div class="scard" style="padding:18px">
    <div class="slabel">YOUR PORTFOLIO</div>
    <div class="sval" style="font-size:28px">$${Number(d.total_equity||0).toLocaleString()}</div>
    <div class="ssub">${Number(d.positions_count||0)} positions &middot; ${Number(d.watch_count||0)} watched &middot; last verdict: <b>${daEsc(String(d.last_verdict||'—'))}</b></div>
    ${stale}
    <div style="margin-top:12px;font-weight:600;font-size:13px">WHAT CHANGED</div>
    <ul style="margin:6px 0 12px 16px;font-size:13px">${changes}</ul>
    <button class="btn" onclick="homeAction('${daEsc(String(act.target||'analyze'))}')">${daEsc(String(act.label||'Analyze my portfolio'))}</button>
  </div>`;
}
function homeAction(target){
  if(target==='report'){ window.location='/mybook/report'; return; }
  switchTab('analyze');
  if(target==='book'&&typeof focusMyBookSection==='function') focusMyBookSection('overview');
  if(target==='analyze'&&typeof mybookAnalyze==='function') mybookAnalyze();
}
```

- [ ] **Step 5: Template wiring test** (append to `tests/test_dashboard_customer_nav.py`, following its existing string-assertion style):

```python
def test_customer_home_pane_and_default_exist():
    html = (Path(__file__).parent.parent / "templates" / "dashboard.html").read_text()
    assert 'id="tab-home"' in html
    assert 'data-tab="home"' in html
    assert "customer-only" in html            # role-gated nav class exists
    assert "buildHomeHero" in html            # audited builder name
```

- [ ] **Step 6: GREEN + guards** — `./venv/bin/python3 -m pytest tests/test_mybook.py tests/test_dashboard_customer_nav.py tests/test_dashboard_xss_wiring.py -q`
- [ ] **Step 7: Commit** — `git commit -m "feat(dashboard): customer Home hero — value, confirmed changes, one next action"`

---

### Task 2: Progressive disclosure of engine mechanics (customer views + report)

Conclusions first; mechanics behind `<details>`. Applies to the customer book-analysis render and `/mybook/report`. Owner tabs untouched.

**Files:**
- Modify: `server.py` (`_render_book_report`: wrap Monte Carlo + Kelly tables in `<details>`, preceded by one conclusion line each)
- Modify: `templates/dashboard.html` (customer book-analysis renderer: same pattern)
- Test: `tests/test_mybook.py` (report assertions)

**Interfaces:**
- Produces (report HTML contract, asserted by tests): a plain-language conclusion line per engine —
  - Monte Carlo: `Worst plausible month (95% confidence): −$X on your $Y book.`
  - Kelly: `N sizing adjustments suggested — largest: SYMBOL ACTION.`
  followed by `<details><summary>How this was computed</summary>…existing tables…</details>`.

- [ ] **Step 1: Failing tests** (append to `tests/test_mybook.py`):

```python
def test_report_leads_with_conclusions_and_folds_mechanics(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {"median": 2100.0, "p5": 1500.0, "p95": 2600.0,
                        "var_95": 300.0, "horizon_days": 21},
        "kelly": {"holdings": [{"symbol": "NVDA", "action": "TRIM",
                                "current_pct": 50.0, "optimal_pct": 40.0}]},
        "book": {"positions": [{"symbol": "NVDA"}], "unpriced": []}})
    text = c.get("/mybook/report").text
    assert "Worst plausible" in text and "$300" in text      # conclusion line
    assert "<details>" in text and "How this was computed" in text
    assert "1 sizing adjustment" in text and "NVDA TRIM" in text
    # mechanics still present, just folded
    assert "Median outcome" in text
```

- [ ] **Step 2: RED**, then **Step 3: Implement** in `_render_book_report` — replace the current Monte Carlo/Kelly block with:

```python
        n_adj = len([k for k in (ky.get("holdings") or []) if k.get("action") in ("ADD", "TRIM")])
        top_adj = next((k for k in (ky.get("holdings") or [])
                        if k.get("action") in ("ADD", "TRIM")), None)
        kelly_conclusion = (
            f"{n_adj} sizing adjustment{'s' if n_adj != 1 else ''} suggested"
            + (f" — largest: {e(top_adj['symbol'])} {e(top_adj['action'])}." if top_adj else "."))
        mc_var = mc.get("var_95", 0) or 0
        mc_conclusion = (f"Worst plausible {e(str(mc.get('horizon_days') or 21))}-day stretch "
                         f"(95% confidence): &minus;${mc_var:,.0f} on your ${total:,.0f} book.")
        body = f"""
  <h2>AI Verdict: {e(str(d.get('portfolio_verdict', '—')))}</h2>
  ...existing verdict/scores/risks/sectors unchanged...
  <h2>Risk check</h2><p>{mc_conclusion}</p>
  <details><summary>How this was computed</summary>
  <table>{row('Median outcome', '$' + format(mc.get('median', 0) or 0, ',.0f'))}
  {row('P5 – P95', '$' + format(mc.get('p5', 0) or 0, ',.0f') + ' – $' + format(mc.get('p95', 0) or 0, ',.0f'))}
  {row('VaR 95%', '$' + format(mc_var, ',.0f'))}</table></details>
  <h2>Position sizing</h2><p>{kelly_conclusion}</p>
  <details><summary>How this was computed</summary>
  <table>{kelly_rows or row('Status', 'unavailable')}</table></details>
  ..."""
```

(Keep every existing table row — nothing is deleted, only folded. `<details>` prints expanded in most browsers' print CSS; add `details{{page-break-inside:avoid}} @media print{{details{{open}}}}` to the report stylesheet.)

Mirror the same conclusion-first pattern in the dashboard's customer book-analysis renderer (`buildBookAnalysis*` functions): conclusion `<p>` + `<details>` around the stat grids.

- [ ] **Step 4: GREEN** — `./venv/bin/python3 -m pytest tests/test_mybook.py tests/test_dashboard_xss_wiring.py -q`
- [ ] **Step 5: Commit** — `git commit -m "refactor(customer): conclusions-first — Monte Carlo/Kelly mechanics behind progressive disclosure"`

---

### Task 3: Structured-only AI output on customer surfaces

Free-form narrative is the most-cited bloat at competitors. Customer surfaces get a one-line takeaway + folded prose; no free-standing commentary blocks.

**Files:**
- Modify: `server.py` (`_render_book_report`: market_context → first-sentence lead + `<details>` for the rest)
- Modify: `templates/dashboard.html` (customer book-analysis + ticker-card narrative fields: same lead+fold pattern; verify no gemini/ollama commentary is reachable from customer nav)
- Test: `tests/test_mybook.py`, `tests/test_dashboard_customer_nav.py`

**Interfaces:**
- Produces: helper `_lead_and_fold(text: str, max_lead: int = 220) -> tuple[str, str]` in server.py — returns `(lead, rest)` split at the first sentence boundary past `max_lead` chars (rest may be empty). Report renders `<p><b>{lead}</b></p>` + `<details><summary>Full context</summary><p>{rest}</p></details>` when rest is non-empty.

- [ ] **Step 1: Failing tests**

```python
def test_lead_and_fold_splits_on_sentence_boundary():
    import server
    text = ("First sentence about the macro backdrop. " * 3
            + "Later detail that belongs in the fold. " * 10)
    lead, rest = server._lead_and_fold(text)
    assert lead.endswith(".") and len(lead) <= 320
    assert rest and rest in text


def test_report_folds_long_market_context(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 1)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD",
                     "market_context": "Lead sentence here. " + ("Filler detail. " * 40),
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {}, "kelly": {}, "book": {"positions": [], "unpriced": []}})
    text = c.get("/mybook/report").text
    assert "Lead sentence here." in text
    assert text.count("Filler detail.") >= 1 and "Full context" in text


def test_customer_nav_has_no_commentary_surfaces():
    html = (Path(__file__).parent.parent / "templates" / "dashboard.html").read_text()
    # every gemini/ollama commentary trigger must be inside an owner-only scope
    for needle in ("gemini/analyze", "gemini/market-commentary"):
        for pos in _find_all(html, needle):
            assert _enclosing_pane_is_owner_only(html, pos), needle
```

(Implement `_find_all`/`_enclosing_pane_is_owner_only` as small helpers in the test module: locate the nearest preceding `class="tab-pane"` id and assert that id's nav button carries `owner-only`.)

- [ ] **Step 2: RED**, then **Step 3: Implement**:

```python
def _lead_and_fold(text: str, max_lead: int = 220) -> tuple[str, str]:
    """First-sentence(s) lead + folded remainder, split on a sentence boundary.
    Customer surfaces show structured leads, not walls of model prose."""
    t = " ".join(str(text or "").split())
    if len(t) <= max_lead:
        return t, ""
    cut = t.find(". ", max_lead)
    if cut == -1:
        return t, ""
    return t[:cut + 1], t[cut + 2:]
```

Report usage (replacing the bare `market_context` paragraph):

```python
  lead, rest = _lead_and_fold(str(d.get('market_context', '')))
  ctx_html = (f"<p style='color:#444;line-height:1.6'><b>{e(lead)}</b></p>"
              + (f"<details><summary>Full context</summary><p style='color:#444'>{e(rest)}</p></details>" if rest else ""))
```

Dashboard: apply the identical lead+fold in the customer book-analysis renderer; audit the ticker card's long prose fields (`narrative`/`market_context`-like) the same way.

- [ ] **Step 4: GREEN**, **Step 5: Commit** — `git commit -m "refactor(customer): structured lead + folded prose for AI narrative on customer surfaces"`

---

### Task 4: News as attached sentiment — no generic feed for customers

**Files:**
- Modify: `templates/dashboard.html` (ticker card: `buildNewsChip(n)` in the card header; ensure the customer Analyze pane renders NO article list; owner SEC/News tab untouched)
- Test: `tests/test_dashboard_customer_nav.py`

**Interfaces:**
- Consumes: existing `GET /api/news/{symbol}` mechanical card (`{score, mood, article_count, as_of}` shape — verify exact field names in the existing widget code and match them).
- Produces: `buildNewsChip(n) -> html` — a single pill `NEWS {mood} · {score} ({article_count} articles)`, color-mapped mood, tooltip with `as_of`. **Rendered only when `article_count > 0`** (the "never fabricate sentiment when article_count=0" project rule).

- [ ] **Step 1: Failing test**

```python
def test_ticker_card_uses_news_chip_not_feed():
    html = (Path(__file__).parent.parent / "templates" / "dashboard.html").read_text()
    assert "buildNewsChip" in html
    # the chip guards the zero-article rule
    assert "article_count" in html
```

- [ ] **Step 2: RED**, then **Step 3: Implement**:

```javascript
function buildNewsChip(n){
  if(!n||!Number(n.article_count)) return '';   // never fabricate sentiment (project rule)
  const s=Number(n.score)||0;
  const color=s>15?'var(--green)':s<-15?'var(--red)':'var(--muted)';
  return `<span class="chip" title="news sentiment as of ${daEsc(String(n.as_of||''))}"
    style="border:1px solid ${color};color:${color};font-size:11px;padding:2px 8px;border-radius:10px">
    NEWS ${daEsc(String(n.mood||'NEUTRAL'))} &middot; ${s>0?'+':''}${s} (${Number(n.article_count)} articles)
  </span>`;
}
```

Wire into the ticker-card header next to the verdict badge (fetch `/api/news/{sym}` alongside the other mechanical cards — the call already exists in the Analyze tab; reuse its response). If the Analyze pane currently renders an article list for customers, replace that block with the chip; keep any article list owner-only.

- [ ] **Step 4: GREEN + XSS guard**, **Step 5: Commit** — `git commit -m "refactor(customer): news as sentiment chip on cards — no generic feed on customer surface"`

---

### Task 5: Book table earns its place — signal columns

The plain holdings table duplicates Robinhood; add the columns only GESH can provide. (Research: brokerages already do tracking better — tables should exist only where they add signal.)

**Files:**
- Modify: `server.py` (`mybook_get`: enrich payload with `signals` + `kelly_deltas` maps)
- Modify: `templates/dashboard.html` (book table renderer: add CONSENSUS / KELLY Δ / RISK columns with empty states)
- Test: `tests/test_mybook.py`

**Interfaces:**
- Produces: `/api/mybook` `data` gains:
  - `signals: {SYM: {verdict: str, date: str}}` — latest `signal_history` row per book symbol (empty dict when none; fail-soft on import/table absence);
  - `kelly_deltas: {SYM: {action: str, delta_pp: float}}` — from the latest persisted analysis (`optimal_pct - current_pct`), `{}` when no analysis;
  - `analysis_stale: bool` — same `_fp` check as the report (columns get a ⚠ header hint when stale).

- [ ] **Step 1: Failing tests** (append to `tests/test_mybook.py`):

```python
def test_mybook_payload_carries_signal_and_kelly_columns(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    import signal_history as sh
    sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY", "score": 0.3,
                          "confidence": 80}], date="2026-07-16")
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {}, "monte_carlo": {},
        "kelly": {"holdings": [{"symbol": "NVDA", "action": "ADD",
                                "current_pct": 2.5, "optimal_pct": 5.4}]},
        "book": {"positions": [], "unpriced": []}})
    d = c.get("/api/mybook").json()["data"]
    assert d["signals"]["NVDA"]["verdict"] == "BUY"
    assert d["kelly_deltas"]["NVDA"] == {"action": "ADD", "delta_pp": 2.9}
    assert d["analysis_stale"] is False


def test_mybook_signal_columns_fail_soft(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1})
    d = c.get("/api/mybook").json()["data"]
    assert d["signals"] == {} or "NVDA" in d["signals"]   # empty history is fine
    assert d["kelly_deltas"] == {}                        # no analysis yet
```

(NOTE: the `signal_history` test fixture must point `sh.DB_PATH` at a tmp file — reuse the autouse fixture pattern from `tests/test_signal_history.py`, added at this test file's top if not already present.)

- [ ] **Step 2: RED**, then **Step 3: Implement** inside `mybook_get` (after the payload is built):

```python
        syms = [p["symbol"] for p in payload["holdings"]] + payload["unpriced"]
        signals: dict[str, dict] = {}
        try:
            import signal_history
            for s in syms:
                hist = signal_history.get_history(s, days=1)
                if hist:
                    signals[s] = {"verdict": hist[-1]["verdict"], "date": hist[-1]["date"]}
        except Exception:  # noqa: BLE001 — the table is optional context, never a 500
            pass
        saved = auth_users.get_latest_book_analysis(email)
        kelly_deltas: dict[str, dict] = {}
        stale = False
        if saved:
            stale = bool(saved.get("_fp")) and saved["_fp"] != (
                _book_fingerprint(auth_users.get_positions(email)) + ("|ew" if ew else ""))
            for k in (saved.get("kelly") or {}).get("holdings") or []:
                if k.get("symbol") in syms and k.get("action"):
                    kelly_deltas[k["symbol"]] = {
                        "action": k["action"],
                        "delta_pp": round((k.get("optimal_pct") or 0)
                                          - (k.get("current_pct") or 0), 1)}
        # merge into the existing return dict:
        #   "signals": signals, "kelly_deltas": kelly_deltas, "analysis_stale": stale
```

Frontend: extend the book-table `build*` row renderer with three columns —
`CONSENSUS` (verdict pill from `signals[sym]`, em-dash empty state), `KELLY Δ` (`ADD +2.9pp` green / `TRIM −5.0pp` red, "run analysis" empty state), `RISK` (⚠ when the symbol appears in the last analysis's `portfolio_risks` text, else —). Header gains a small `⚠ analysis out of date` note when `analysis_stale`.

- [ ] **Step 4: GREEN** — `./venv/bin/python3 -m pytest tests/test_mybook.py tests/test_dashboard_xss_wiring.py -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(mybook): consensus/Kelly/risk signal columns on the book table"`

---

## Rollout order & dependencies

```
retention-plan T1 (signal_history) ──► this plan T1 (hero top_changes) & T5 (consensus column)
This plan: T2, T3, T4 independent; T1 before T5 (both touch mybook_get/table — avoid merge conflicts)
```

Ship as 2 PRs: **PR-D** = T1+T5 (hero + table, both touch `/api/mybook`), **PR-E** = T2+T3+T4 (disclosure + structured output + news chip, all render-layer). Each PR: full suite + CI green, merge, restart `com.gesh.main`, then a customer-session browser smoke (login as test customer → Home hero renders, book table shows new columns, report shows folded mechanics).

## Self-review notes

- Every "remove" is implemented as fold/de-emphasize or role-scoping — no owner capability is deleted, and nothing touches `/track-record`.
- Both `signal_history` consumers are fail-soft (`try/except ImportError/Exception` → empty state), so this plan can technically ship before the retention plan, just with empty CHANGED/CONSENSUS states.
- T1 and T5 both modify `mybook_get`/the book table — same PR, sequenced, to avoid the multi-agent branch-collision hazard noted in project memory.
- Report `<details>` needs the print-CSS note in T2 so printed PDFs aren't missing the mechanics.
