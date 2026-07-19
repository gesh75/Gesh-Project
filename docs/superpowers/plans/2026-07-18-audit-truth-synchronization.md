# Audit Truth Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the three founder cockpit routes, three static reports and existing Notion tracker agree with the verified 2026-07-18 audit without broad refactoring.

**Architecture:** Preserve each self-contained renderer and static report. Add copy/data-quality contracts through focused tests, then make minimal rendering and documentation changes. Use the existing Notion tracker and databases as the external progress source of truth.

**Tech Stack:** Python 3.14, FastAPI, pytest, stdlib HTML/SQLite tooling, self-contained HTML/CSS/JavaScript, Notion MCP.

## Global Constraints

- Open pull requests are unshipped.
- The owner pages remain authenticated, read-only and paper/research-only.
- Existing navy/cyan styling remains; status semantics are Verified, Degraded and Blocked.
- Missing/stale/provider-failed data never becomes a positive or actionable state.
- No broad module extraction, public-route change or live-trading capability.
- Existing unrelated dirty-worktree files are preserved.

---

### Task 1: Replace the retired `/progress` launch countdown

**Files:**
- Modify: `tests/test_progress_view.py`
- Modify: `progress_view.py`

**Interfaces:**
- Consumes: `proof_page._load_gate_dto() -> dict[str, object]`
- Produces: `load_progress(..., gate_loader: Callable | None = None) -> dict[str, Any]` with `signal_gate` and `readiness`; `render_progress_html(data) -> str`

- [ ] **Step 1: Write failing progress copy/contract tests**

Add assertions that rendered HTML contains `Paid-beta readiness`, `Signal evidence`, `Operations`, `100` and `Legal + privacy review`, and excludes `DAYS TO SEP-1 ALPHA`, `30 gate` and `6/30`.

- [ ] **Step 2: Verify the tests fail for the retired UI**

Run: `venv/bin/python -m pytest -q tests/test_progress_view.py`  
Expected: failures on the new readiness copy and prohibited legacy strings.

- [ ] **Step 3: Implement the minimal readiness renderer**

Inject/load the gate DTO, retain weekly snapshot history as legacy context, render the three readiness lanes and use the authoritative required close count. Remove deadline and 30-gate readiness logic.

- [ ] **Step 4: Verify progress tests pass**

Run: `venv/bin/python -m pytest -q tests/test_progress_view.py tests/test_watch_endpoint.py`  
Expected: all pass.

### Task 2: Reframe `/watch` as paper evidence

**Files:**
- Modify: `tests/test_watch_view.py`
- Modify: `tests/test_watch_endpoint.py`
- Modify: `watch_view.py`

**Interfaces:**
- Consumes: existing `load_watch_data` contract
- Produces: existing HTML route with truthful labels; no JSON schema change

- [ ] **Step 1: Write failing copy/data-quality tests**

Require `Paper Evidence Watch`, `PAPER ONLY`, `Data quality`, `SIMULATED`, and `updated`; prohibit `Live Trading Watch` and `what the engine is doing right now`.

- [ ] **Step 2: Verify tests fail**

Run: `venv/bin/python -m pytest -q tests/test_watch_view.py tests/test_watch_endpoint.py`  
Expected: copy-contract failures only.

- [ ] **Step 3: Update title, hero, status and quality rail**

Keep current polling and escape functions. Derive displayed quality from existing `price_source`, `live_prices_available` and `sample_provisional`; use `updated` rather than `live` for poll status.

- [ ] **Step 4: Verify watch tests pass**

Run the command from Step 2.  
Expected: all pass.

### Task 3: Reframe `/invest` as hypothetical scenarios

**Files:**
- Modify: `tests/test_invest_view.py`
- Modify: `invest_view.py`

**Interfaces:**
- Consumes: existing `load_invest_data` JSON contract
- Produces: existing scenario UI and polling; no sizing-algorithm change

- [ ] **Step 1: Write failing compliance-copy tests**

Require `Diversification Scenario Lab`, `HYPOTHETICAL`, `not suitability-checked`, `Model allocation`, and `Scenario alternatives`; prohibit `where should NEW cash go?`, `Suggest` and `Swap ideas`.

- [ ] **Step 2: Verify tests fail**

Run: `venv/bin/python -m pytest -q tests/test_invest_view.py`  
Expected: copy-contract failures.

- [ ] **Step 3: Apply the minimal copy/status implementation**

Preserve whitelisting, XSS neutralization, server-side what-if resizing and endpoint shape. Add a stale/degraded rail and relabel action-oriented columns and sections.

- [ ] **Step 4: Verify invest tests pass**

Run: `venv/bin/python -m pytest -q tests/test_invest_view.py tests/test_invest_radar.py`  
Expected: all pass.

### Task 4: Synchronize the three static reports

**Files:**
- Modify: `/Users/georgigaydarov/Projects/gesh-project-trading/GESH_PROJECT_DOCS.html`
- Modify: `/Users/georgigaydarov/Projects/gesh-project-trading/GESH_MONETIZATION_REPORT.html`
- Modify: `/Users/georgigaydarov/Projects/gesh-project-trading/Crypto_Architecture_Diagram.html`
- Create: `/Users/georgigaydarov/Projects/gesh-project-trading/tests/test_audit_truth_docs.py`

**Interfaces:**
- Consumes: verified audit facts at `dc969264` and read-only runtime counts
- Produces: self-contained HTML with a 2026-07-18 audit state block

- [ ] **Step 1: Write failing static-document contract tests**

Parse each file and require the audit date, research/paper boundary and appropriate blockers. Assert active copy does not contain the arbitrary `85%` build claim, `No SEC registration required at launch`, `TARGET ≥80% ACCURACY`, or `orders — paper fills (707+ rows)`.

- [ ] **Step 2: Verify tests fail**

Run: `venv/bin/python -m pytest -q tests/test_audit_truth_docs.py` from `gesh-project-trading`.  
Expected: failures identifying every stale claim.

- [ ] **Step 3: Update current-state sections in place**

Add the readiness rail, link audit deliverables, correct service names/counts, label historical plans, and replace legal/accuracy certainty with explicit review/evidence gates.

- [ ] **Step 4: Verify static-document tests pass**

Run the command from Step 2.  
Expected: all pass.

### Task 5: Update the existing Notion tracker

**Files:**
- External: `GESH Financial — Project Tracker`
- External data source: `GESH Build Items`
- External data source: `GESH Milestones`

**Interfaces:**
- Produces: one milestone, eight build items and a dated tracker update linked to local audit/plan paths

- [ ] **Step 1: Create the milestone and build items using fetched schemas**

Use valid phase/status/priority options only. Set the six surface tasks and verification task to `In Progress`; set counsel/privacy closure to `Blocked` because it needs external authority.

- [ ] **Step 2: Append a tracker progress update**

Record audit completion, implementation scope, verified facts, blockers and links without replacing prior history.

- [ ] **Step 3: Fetch created pages and tracker**

Confirm titles, properties, counts and progress note are present.

### Task 6: Integrated verification and deployment check

**Files:**
- Verify only: both repositories and live routes

**Interfaces:**
- Produces: evidence for completion and remaining authenticated-browser limitation

- [ ] **Step 1: Run focused and full regression suites**

Run the route/view suites first, then `venv/bin/python -m pytest -q tests` in `gesh-project`; run crypto/static-doc suites in `gesh-project-trading`.

- [ ] **Step 2: Run structural checks**

Run `git diff --check` in both repos, compile changed Python, parse changed HTML and scan the six surfaces for prohibited claims.

- [ ] **Step 3: Restart only the :9000 service after tests pass**

Use the existing launchd service and verify `/healthz` plus unauthenticated 302 behavior on `/progress`, `/watch` and `/invest`. Do not restart :9003 because static files do not require it.

- [ ] **Step 4: Verify browser state where authorization permits**

Reload the live routes. If the audit browser is unauthenticated, record protected visual QA as not verified rather than bypassing authentication.

- [ ] **Step 5: Close Notion implementation items**

Mark completed surface and verification items `Done`, leave external counsel/privacy work `Blocked`, and append the final test/deployment evidence.

