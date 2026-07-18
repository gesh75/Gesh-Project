# GESH Full Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate every security, tenant-isolation, financial-correctness, provenance, concurrency, runtime, UI, and verification gap from the 2026-07-11 GESH audit while preserving the user's original dirty checkouts and leaving live services untouched.

**Architecture:** Work from clean product and trading worktrees. Establish immutable tenant identity first, expose the trading autonomy gate through a versioned DTO, propagate typed provenance through the daily chain, make the paper gate causal, bound MyBook work with durable quota reservations, correct Monte Carlo semantics, and finish with readiness/backup/runtime/UI/CI hardening. Each independently rejectable change receives a failing regression test before implementation and a focused commit.

**Tech Stack:** Python 3.14, FastAPI, SQLite, pytest, Ruff, Bash orchestration, vanilla JavaScript/HTML, Git worktrees.

## Global Constraints

- Product worktree: `/Users/georgigaydarov/.codex/worktrees/gesh/product-full-remediation`.
- Trading worktree: `/Users/georgigaydarov/.codex/worktrees/gesh/trading-full-remediation`.
- Original checkouts under `/Users/georgigaydarov/Projects/` are read-only inputs during implementation.
- Use type hints on every new Python function signature.
- Prefer async/await for request-path I/O and `asyncio.to_thread` for PBKDF2/SQLite work that can block.
- Use immutable result records and compare-and-set publication; do not mutate caller-owned payloads.
- Never fabricate financial data or silently hide degraded calibration.
- `PAPER_MODE=true` remains the default; do not implement or enable live execution.
- Do not restart LaunchAgents, ports 9000/9003, or mutate live databases.
- Every behavior change must show RED, then GREEN, then focused regression/full-suite verification.

---

## File Responsibility Map

### Trading repository

- `trading/tracker.py` — authoritative versioned autonomy-gate DTO.
- `tests/test_gate_status_dto.py` — DTO shape, cohort, version, and fail-closed contract.

### Product repository

- `auth_users.py` — schema migration, immutable user IDs, customer ownership, quota/job persistence.
- `billing.py` — profile-only email changes after immutable-ID migration.
- `server.py` — authentication throttling, safe request bodies/errors, MyBook jobs, readiness.
- `proof_page.py` — authoritative gate DTO consumer and source-separated public metrics.
- `long_term_macro.py` — FINRA parsing, units, cache quality, no synthetic fallback.
- `monte_carlo.py` — explicit 21-day log-return simulation and calibration quality.
- `run_manifest.py` — immutable daily-run provenance contract.
- `run_engines2.py`, `run_ai2.py`, `run_paper_gate2.py`, `run_daily_analysis.sh`, `gen_report2.py` — manifest propagation, causal gate, degraded behavior, explicit imports.
- `ops_backup.py` — online SQLite backup, integrity verification, retention, restore check.
- `templates/dashboard.html` — honest macro/Monte Carlo labels and functional customer navigation.
- `.github/workflows/tests.yml`, `pyproject.toml`, `requirements.txt`, `requirements.lock` — reproducible runtime and aligned gates.
- `tests/` — regression, contract, security, concurrency, financial-math, UI-wiring, and operations coverage.

---

### Task 1: Publish the authoritative trading-gate DTO

**Files:**

- Modify: `/Users/georgigaydarov/.codex/worktrees/gesh/trading-full-remediation/trading/tracker.py`
- Create: `/Users/georgigaydarov/.codex/worktrees/gesh/trading-full-remediation/tests/test_gate_status_dto.py`

**Interfaces:**

- Consumes: `check_autonomy_gate()`, `ENGINE_SOURCES`, `MIN_ENGINE_CLOSED`.
- Produces: `GATE_DTO_VERSION: int` and `gate_status_dto(db_path: str | Path | None = None) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing DTO contract tests**

```python
def test_dto_counts_engine_cohort_only(sandbox_db):
    seed_manual_wins(9)
    seed_engine_losses(8)
    dto = tracker.gate_status_dto()
    assert dto["version"] == 2
    assert dto["cohort"] == "engine"
    assert dto["closes"] == 8
    assert dto["required"] == tracker.MIN_ENGINE_CLOSED
    assert dto["pass"] is False


def test_dto_wraps_the_authoritative_gate(sandbox_db):
    gate = tracker.check_autonomy_gate()
    dto = tracker.gate_status_dto()
    assert dto["criteria"] == gate["gates"]
    assert dto["pass"] == gate["ready"]
```

- [ ] **Step 2: Verify RED**

Run: `/Users/georgigaydarov/Projects/gesh-project-trading/venv/bin/python -m pytest tests/test_gate_status_dto.py -q`

Expected: failure with `AttributeError: module 'trading.tracker' has no attribute 'gate_status_dto'`.

- [ ] **Step 3: Implement the versioned wrapper with a lock around DB override**

```python
GATE_DTO_VERSION: int = 2
_GATE_DTO_LOCK = threading.RLock()


def gate_status_dto(db_path: str | Path | None = None) -> dict[str, Any]:
    global DB_PATH
    with _GATE_DTO_LOCK:
        previous = DB_PATH
        if db_path is not None:
            DB_PATH = Path(db_path)
        try:
            gate = check_autonomy_gate()
        finally:
            DB_PATH = previous
    engine = gate["gates"]["engine_closed_trades"]
    return {
        "version": GATE_DTO_VERSION,
        "cohort": "engine",
        "cohort_sources": list(ENGINE_SOURCES),
        "closes": int(engine["value"]),
        "required": int(engine["required"]),
        "criteria": gate["gates"],
        "pass": bool(gate["ready"]),
        "summary": gate["summary"],
        "eta_note": gate["eta_note"],
        "window": gate["window"],
        "as_of": gate["as_of"],
    }
```

- [ ] **Step 4: Verify GREEN and full trading regression**

Run: `/Users/georgigaydarov/Projects/gesh-project-trading/venv/bin/python -m pytest tests/test_gate_status_dto.py -q`

Run: `/Users/georgigaydarov/Projects/gesh-project-trading/venv/bin/python -m pytest tests/ -q -p no:cacheprovider`

Expected: DTO tests pass; baseline remains at least `515 passed, 2 skipped`.

- [ ] **Step 5: Commit**

```bash
git add trading/tracker.py tests/test_gate_status_dto.py
git commit -m "feat(tracker): publish versioned autonomy gate DTO"
```

---

### Task 2: Migrate customer ownership to immutable user IDs

**Files:**

- Modify: `auth_users.py`
- Modify: `billing.py`
- Create: `tests/test_tenant_identity_migration.py`
- Create: `tests/test_tenant_rename_migration.py`

**Interfaces:**

- Produces: `resolve_user_id(email: str) -> str | None`, `get_user_by_id(user_id: str) -> dict[str, Any] | None`, and schema version 2.
- Preserves: public email-accepting CRUD functions while changing their storage scope to `user_id`.

- [ ] **Step 1: Write failing migration and old-email-reuse tests**

```python
def test_old_email_reuse_cannot_inherit_customer_rows(auth_db):
    auth_users.create_user("old@example.com", "pw")
    original_id = auth_users.resolve_user_id("old@example.com")
    auth_users.add_watch("old@example.com", "NVDA")
    auth_users.set_position("old@example.com", "AAPL", 3)
    assert auth_users.rename_user_email("old@example.com", "new@example.com")
    auth_users.create_user("old@example.com", "other")
    assert auth_users.resolve_user_id("new@example.com") == original_id
    assert auth_users.get_watch("new@example.com") == ["NVDA"]
    assert auth_users.get_watch("old@example.com") == []
    assert auth_users.get_positions("old@example.com") == []


def test_child_tables_reference_user_id_with_foreign_keys(auth_db):
    with auth_users._conn() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(watchlist)")}
        foreign = con.execute("PRAGMA foreign_key_list(watchlist)").fetchall()
    assert "user_id" in columns
    assert "email" not in columns
    assert any(row[2] == "users" and row[3] == "user_id" for row in foreign)
```

- [ ] **Step 2: Verify RED**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_tenant_identity_migration.py tests/test_tenant_rename_migration.py -q`

Expected: missing `resolve_user_id` and email-keyed schema assertions fail.

- [ ] **Step 3: Add idempotent schema migration**

Implement `_migrate_to_user_ids(con: sqlite3.Connection) -> None` using:

```python
def _new_user_id() -> str:
    return str(uuid.uuid4())


def resolve_user_id(email: str) -> str | None:
    with _conn() as con:
        row = con.execute(
            "SELECT user_id FROM users WHERE email=? AND is_active=1", (_norm(email),)
        ).fetchone()
    return str(row["user_id"]) if row else None
```

The migration adds/backfills `users.user_id`, creates a unique index, rebuilds all five child tables with `user_id` foreign keys and their existing uniqueness/index rules, copies only rows that join to a real user, swaps tables in one `BEGIN IMMEDIATE`, sets `PRAGMA user_version=2`, and runs `foreign_key_check` before commit.

- [ ] **Step 4: Convert child CRUD to immutable ownership**

Every customer CRUD function resolves the email once and uses `WHERE user_id=?`. `rename_user_email()` updates only `users.email`; billing continues locating the account by subscription ID.

- [ ] **Step 5: Verify GREEN, migration idempotency, and billing regression**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_tenant_identity_migration.py tests/test_tenant_rename_migration.py tests/test_billing.py tests/test_auth_users.py -q`

Expected: all pass, including a second initialization of the migrated database.

- [ ] **Step 6: Commit**

```bash
git add auth_users.py billing.py tests/test_tenant_identity_migration.py tests/test_tenant_rename_migration.py
git commit -m "fix(auth): key customer data by immutable user ID"
```

---

### Task 3: Harden login, magic links, webhook bodies, cookies, and public errors

**Files:**

- Modify: `auth_users.py`
- Modify: `server.py`
- Create: `tests/test_auth_hardening.py`
- Modify: `tests/test_billing_webhook.py`
- Create: `tests/test_public_error_sanitization.py`

**Interfaces:**

- Produces: `verify_credentials_constant_time(email: str, password: str) -> bool`, `_read_limited_body(request, limit)`, and auth bucket helpers.

- [ ] **Step 1: Write failing endpoint security tests**

```python
def test_login_throttles_by_ip_and_normalized_email(client):
    for _ in range(server.AUTH_LOGIN_ATTEMPTS):
        client.post("/auth/login", json={"email": "A@EXAMPLE.COM", "password": "bad"})
    response = client.post("/auth/login", json={"email": "a@example.com", "password": "bad"})
    assert response.status_code == 429
    assert response.headers["Retry-After"]


def test_magic_link_does_not_replace_another_active_session(alice_client, bob_magic):
    before = alice_client.cookies.get(auth_users.COOKIE_NAME)
    response = alice_client.get(f"/auth/magic?token={bob_magic}", follow_redirects=False)
    assert response.status_code == 409
    assert alice_client.cookies.get(auth_users.COOKIE_NAME) == before


def test_oversized_webhook_is_rejected_before_handler(client, monkeypatch):
    handler = Mock()
    monkeypatch.setattr(billing, "handle_webhook", handler)
    response = client.post("/api/billing/webhook", content=b"x" * (server.WEBHOOK_MAX_BYTES + 1))
    assert response.status_code == 413
    handler.assert_not_called()
```

- [ ] **Step 2: Verify RED**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_auth_hardening.py tests/test_billing_webhook.py tests/test_public_error_sanitization.py -q`

Expected: throttling, session-conflict, body-cap, off-loop hash, or sanitization assertions fail.

- [ ] **Step 3: Implement constant-work credential verification and off-loop login**

Use one process-random dummy stored hash for unknown/inactive accounts. In the endpoint:

```python
verified = await asyncio.to_thread(
    auth_users.verify_credentials_constant_time, email, password
)
```

Apply separate IP and normalized-email buckets under a lock and return `Retry-After`.

- [ ] **Step 4: Implement magic-session conflict and limited body reader**

```python
async def _read_limited_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and int(declared) > limit:
        raise HTTPException(status_code=413, detail="Request too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="Request too large")
    return bytes(body)
```

Parse webhook JSON from the capped bytes. Magic login compares the current session user ID to the token subject before consuming/replacing anything.

- [ ] **Step 5: Sanitize expected offline errors and secure-cookie readiness**

Return fixed `503` messages and log detailed exception text. Add a readiness failure when `APP_BASE_URL` is HTTPS and `GESH_COOKIE_SECURE=0`.

- [ ] **Step 6: Verify GREEN and customer isolation**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_auth_hardening.py tests/test_billing_webhook.py tests/test_public_error_sanitization.py tests/test_customer_isolation.py tests/test_customer_leak_sweep.py -q`

- [ ] **Step 7: Commit**

```bash
git add auth_users.py server.py tests/test_auth_hardening.py tests/test_billing_webhook.py tests/test_public_error_sanitization.py
git commit -m "fix(security): harden public authentication and webhook paths"
```

---

### Task 4: Integrate the proof DTO and repair FINRA macro provenance

**Files:**

- Modify: `proof_page.py`
- Modify: `long_term_macro.py`
- Modify: `templates/dashboard.html`
- Modify: `tests/test_proof_page.py`
- Create: `tests/test_proof_page_gate.py`
- Create: `tests/test_long_term_macro.py`
- Create: `tests/fixtures/finra_margin_statistics.html`

**Interfaces:**

- Consumes: trading `gate_status_dto()` version 2.
- Produces: product proof data with `authoritative_gate`; macro data with `unit`, `source`, `as_of`, and `quality`.

- [ ] **Step 1: Write failing DTO consumer and FINRA fixture tests**

```python
def test_public_readiness_uses_engine_dto_not_mixed_rows(monkeypatch, tracker_db):
    monkeypatch.setattr(proof_page, "_load_gate_dto", lambda _: {
        "version": 2, "cohort": "engine", "closes": 8, "required": 100,
        "criteria": {}, "pass": False, "summary": "insufficient", "as_of": "now",
    })
    data = proof_page.load_track_record(str(tracker_db))
    assert data["gate"]["ready"] is False
    assert data["gate"]["closed_trades"]["value"] == 8


def test_finra_values_remain_in_official_millions(finra_fixture, monkeypatch):
    monkeypatch.setattr(pd, "read_html", lambda *_a, **_k: finra_fixture)
    result = long_term_macro.fetch_margin_debt(force_refresh=True)
    assert result["unit"] == "USD_millions"
    assert result["series"].iloc[-1] == 1_415_557
    assert result["quality"] == "good"
```

- [ ] **Step 2: Verify RED**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_proof_page_gate.py tests/test_long_term_macro.py -q`

- [ ] **Step 3: Implement fail-closed DTO consumption**

Load the trading module through an explicit configured path/package import. Accept only DTO version 2. Keep the all-source ledger descriptive, but render readiness exclusively from the DTO. An import/version/read failure returns a typed unavailable gate.

- [ ] **Step 4: Implement typed FINRA fetch/cache behavior**

Parse `%b-%y`, keep the first debit column in millions, validate the series, use UTC-aware timestamps, and return unavailable or stale-cache quality. Delete the synthetic fallback. Store a versioned cache outside the repository or at the configured runtime path.

- [ ] **Step 5: Fix dashboard units and claims**

Render trillions as `margin_debt_millions / 1_000_000`, display source/as-of/quality, and replace “Feeds LT_Macro consensus node” with “Research overlay — not a consensus vote.”

- [ ] **Step 6: Verify GREEN**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_proof_page.py tests/test_proof_page_gate.py tests/test_long_term_macro.py -q`

- [ ] **Step 7: Commit**

```bash
git add proof_page.py long_term_macro.py templates/dashboard.html tests/test_proof_page.py tests/test_proof_page_gate.py tests/test_long_term_macro.py tests/fixtures/finra_margin_statistics.html
git commit -m "fix(data): use authoritative proof gate and sourced FINRA units"
```

---

### Task 5: Propagate daily-run provenance and make the paper gate causal

**Files:**

- Create: `run_manifest.py`
- Modify: `build_snapshot_from_mcp.py`
- Modify: `fetch_live_snapshot.py`
- Modify: `run_engines2.py`
- Modify: `run_ai2.py`
- Modify: `run_paper_gate2.py`
- Modify: `run_daily_analysis.sh`
- Modify: `gen_report2.py`
- Create: `tests/test_run_manifest.py`
- Create: `tests/test_quote_freshness.py`
- Create: `tests/test_degraded_provenance.py`
- Create: `tests/test_paper_gate_causality.py`

**Interfaces:**

- Produces: frozen `RunManifest` and `PaperGateOutcome` records.
- Preserves: separate product paper book and authoritative trading tracker.

- [ ] **Step 1: Write failing degraded-side-effect and rejected-intent tests**

```python
def test_degraded_run_skips_paper_and_current_report(monkeypatch, tmp_path):
    manifest = RunManifest.create(source="last_known", snapshot_as_of=old_time, degraded=True)
    outcome = run_paper_gate2.run(manifest=manifest, engine_results={})
    assert outcome.paper_session == []
    assert outcome.actionable is False


def test_rejected_intent_is_never_recorded(monkeypatch, action):
    monkeypatch.setattr(run_paper_gate2, "evaluate", lambda *_: rejected_verdict())
    recorder = monkeypatch.spy(run_paper_gate2, "record_approved_paper_trade")
    result = run_paper_gate2.evaluate_action(action, fresh_manifest())
    assert result.approved is False
    recorder.assert_not_called()
```

- [ ] **Step 2: Verify RED**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_run_manifest.py tests/test_degraded_provenance.py tests/test_paper_gate_causality.py -q`

- [ ] **Step 3: Implement immutable run manifest**

```python
@dataclass(frozen=True, slots=True)
class RunManifest:
    run_id: str
    input_source: str
    snapshot_as_of: datetime
    started_at: datetime
    degraded: bool
    quote_as_of: Mapping[str, datetime]
```

Serialize it to `run_manifest.json` atomically and embed the same object in `engine_results.json`.

Preserve each provider quote's own `quote_as_of` and provider/session metadata in both snapshot builders. The risk decision for a symbol uses that symbol's quote timestamp; the snapshot transport time is not a substitute.

- [ ] **Step 4: Reorder the paper pipeline**

Build actual intents, persist/evaluate decisions, and only then call the paper recorder for approved intents. Use the same `intent_id` in all three records. Degraded manifests return an audit-only result before intent execution.

- [ ] **Step 5: Make degraded report output separate and non-publishing**

`run_daily_analysis.sh` passes `GESH_RUN_DEGRADED=1`; report generation writes a clearly named research artifact and never replaces the current local or trading-repo report.

- [ ] **Step 6: Remove sibling path precedence**

Use explicit `importlib.util.spec_from_file_location` only for narrowly named contract modules or install/import package names. Do not prepend the sibling repository to `sys.path`.

- [ ] **Step 7: Verify GREEN and orchestrator regressions**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_run_manifest.py tests/test_quote_freshness.py tests/test_degraded_provenance.py tests/test_paper_gate_causality.py tests/test_snapshot_state.py -q`

- [ ] **Step 8: Commit**

```bash
git add run_manifest.py build_snapshot_from_mcp.py fetch_live_snapshot.py run_engines2.py run_ai2.py run_paper_gate2.py run_daily_analysis.sh gen_report2.py tests/test_run_manifest.py tests/test_quote_freshness.py tests/test_degraded_provenance.py tests/test_paper_gate_causality.py tests/test_snapshot_state.py
git commit -m "fix(pipeline): propagate provenance and gate before paper records"
```

---

### Task 6: Bound MyBook work and make quota reservation atomic

**Files:**

- Modify: `auth_users.py`
- Modify: `server.py`
- Create: `tests/test_mybook_job_control.py`
- Modify: `tests/test_customer_book_analysis.py`

**Interfaces:**

- Produces: `reserve_usage(user_id, event, job_id, limit)`, `complete_usage(job_id)`, `release_usage(job_id)`, and durable job state.

- [ ] **Step 1: Write failing concurrency, quota, and stale-publication tests**

```python
def test_book_change_cannot_start_second_worker(client, blocked_worker):
    assert client.post("/api/mybook/analyze").status_code == 202
    change_one_position(client)
    second = client.post("/api/mybook/analyze")
    assert second.status_code == 202
    assert submitted_worker_count() == 1


def test_stale_worker_cannot_overwrite_new_fingerprint(job_harness):
    old, new = job_harness.start_two_generations()
    job_harness.finish(new, result={"generation": "new"})
    job_harness.finish(old, result={"generation": "old"})
    assert job_harness.cached_result()["generation"] == "new"
```

- [ ] **Step 2: Verify RED**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_mybook_job_control.py tests/test_customer_book_analysis.py -q`

- [ ] **Step 3: Add durable reservation/job tables and atomic APIs**

Use `BEGIN IMMEDIATE`, a unique running job per `user_id`, explicit states `queued/running/done/failed`, and reservation states `reserved/completed/released`. Count reserved plus completed events within the rolling window.

- [ ] **Step 4: Replace daemon threads with a bounded executor**

Use one module-level `ThreadPoolExecutor(max_workers=GESH_MYBOOK_WORKERS)` plus a bounded submission semaphore. Capture `user_id`, `job_id`, and fingerprint. Compare the durable current job before cache or analysis publication.

- [ ] **Step 5: Provide full-book lightweight context**

Keep detailed cards capped but include `all_holdings_summary` for every holding and assert that portfolio-level sections are based on it. Return `detailed_coverage` with analyzed and total counts.

- [ ] **Step 6: Verify GREEN with a 30-request concurrency test**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_mybook_job_control.py tests/test_customer_book_analysis.py -q`

- [ ] **Step 7: Commit**

```bash
git add auth_users.py server.py tests/test_mybook_job_control.py tests/test_customer_book_analysis.py
git commit -m "fix(mybook): bound jobs and reserve quota atomically"
```

---

### Task 7: Correct Monte Carlo semantics and expose data quality

**Files:**

- Modify: `monte_carlo.py`
- Modify: `server.py`
- Modify: `templates/dashboard.html`
- Create: `tests/test_monte_carlo_correctness.py`
- Modify: `tests/test_customer_book_analysis.py`

**Interfaces:**

- Produces: a 21-day result with `horizon_days` and `data_quality`.

- [ ] **Step 1: Write failing analytical and label tests**

```python
def test_mean_log_return_is_not_variance_adjusted_twice():
    result = simulate_log_paths(start=100.0, daily_log_mu=0.01, daily_sigma=0.0,
                                horizon_days=2, n_sims=1, seed=42)
    assert result[0] == pytest.approx(100.0 * math.exp(0.02))


def test_result_declares_fallback_symbols_and_degraded_quality(monkeypatch):
    monkeypatch.setattr(monte_carlo, "_fetch_history", partial_history)
    result = monte_carlo.run_monte_carlo(holdings, summary)
    assert result["horizon_days"] == 21
    assert result["data_quality"]["quality"] == "degraded"
    assert result["data_quality"]["fallback_symbols"] == ["MISS"]
```

- [ ] **Step 2: Verify RED**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_monte_carlo_correctness.py -q`

- [ ] **Step 3: Extract pure simulation and calibration records**

Use mean log return directly as the normal location. Preserve correlated covariance. Collect usable observations and coverage; return unavailable below the configured minimum.

- [ ] **Step 4: Make all labels data-driven**

Replace hard-coded “1yr” strings in server reports and MyBook/dashboard HTML with the returned `horizon_days`. Render a degraded warning and sources/fallbacks.

- [ ] **Step 5: Verify GREEN and existing risk tests**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_monte_carlo_correctness.py tests/test_monte_carlo*.py tests/test_customer_book_analysis.py -q`

- [ ] **Step 6: Commit**

```bash
git add monte_carlo.py server.py templates/dashboard.html tests/test_monte_carlo_correctness.py tests/test_customer_book_analysis.py
git commit -m "fix(risk): correct Monte Carlo drift and quality labels"
```

---

### Task 8: Add readiness, verified backups, runtime locking, and functional customer navigation

**Files:**

- Create: `ops_backup.py`
- Modify: `server.py`
- Modify: `templates/dashboard.html`
- Modify: `requirements.txt`
- Create: `requirements.lock`
- Modify: `/Users/georgigaydarov/.codex/worktrees/gesh/trading-full-remediation/requirements.txt`
- Create: `/Users/georgigaydarov/.codex/worktrees/gesh/trading-full-remediation/requirements.lock`
- Modify: `.github/workflows/tests.yml`
- Modify: `pyproject.toml`
- Create: `tests/test_readiness.py`
- Create: `tests/test_ops_backup.py`
- Create: `tests/test_customer_navigation.py`

**Interfaces:**

- Produces: `/readyz`, `backup_database(source, destination)`, `verify_backup(path)`, and reproducible dependency input.

- [ ] **Step 1: Write failing readiness, backup, and navigation tests**

```python
def test_readyz_fails_when_auth_db_is_unreadable(client, monkeypatch):
    monkeypatch.setattr(server, "_auth_db_ready", lambda: (False, "auth_db"))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_online_backup_round_trip(tmp_path, seeded_db):
    backup = ops_backup.backup_database(seeded_db, tmp_path)
    assert ops_backup.verify_backup(backup) == "ok"
    assert read_seed_row(backup) == read_seed_row(seeded_db)


def test_customer_scanner_button_targets_visible_pane(template_text):
    assert_customer_nav_targets_visible_panes(template_text)
```

- [ ] **Step 2: Verify RED**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_readiness.py tests/test_ops_backup.py tests/test_customer_navigation.py -q`

- [ ] **Step 3: Implement liveness/readiness split and online backup**

`/healthz` stays constant-time. `/readyz` checks local DB/config/artifact dependencies without external network. `ops_backup.py` uses `sqlite3.Connection.backup`, `PRAGMA integrity_check`, atomic destination rename, and retention by parsed timestamp.

- [ ] **Step 4: Fix customer navigation and honest shared-analysis labels**

Remove the CSS rule that permanently hides scanner or map customer buttons to visible anchored sections. Ensure Deep Analysis, Signals, and Risk identify that one analysis run powers distinct views.

- [ ] **Step 5: Pin dependencies and align CI**

Pin `alpaca-py`, pin `gesh-core` to one immutable commit/version in both repositories, generate/review `requirements.lock`, use Python 3.14 in CI, and set the same coverage floor in CI and `pyproject.toml` after measuring the new suite.

- [ ] **Step 6: Verify GREEN**

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/test_readiness.py tests/test_ops_backup.py tests/test_customer_navigation.py -q`

Run: `/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pip check`

- [ ] **Step 7: Commit**

```bash
git add ops_backup.py server.py templates/dashboard.html requirements.txt requirements.lock .github/workflows/tests.yml pyproject.toml tests/test_readiness.py tests/test_ops_backup.py tests/test_customer_navigation.py
git commit -m "chore(ops): add readiness backups and reproducible gates"

cd /Users/georgigaydarov/.codex/worktrees/gesh/trading-full-remediation
git add requirements.txt requirements.lock
git commit -m "chore(deps): align locked shared runtime versions"
```

---

### Task 9: Integrated verification, documentation, and handoff

**Files:**

- Modify: `CLAUDE.md`
- Modify: `docs/CUSTOMER_SURFACE.md`
- Modify: `docs/AUDIT_GUIDE.md`
- Modify: `GAPS_AND_IMPROVEMENTS_2026-07-09.md`

**Interfaces:**

- Consumes: all previous task outputs.
- Produces: verified remediation report and deployment/rollback instructions.

- [ ] **Step 1: Run product full verification with coverage**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pytest tests/ -q -p no:cacheprovider \
  --cov=. --cov-report=term-missing --cov-report=xml --cov-fail-under=55
/Users/georgigaydarov/Projects/gesh-project/venv/bin/ruff check .
/Users/georgigaydarov/Projects/gesh-project/venv/bin/python -m pip check
```

Expected: zero failures; changed modules at least 80% coverage; overall coverage meets the single aligned floor chosen from measured output.

- [ ] **Step 2: Run trading full verification**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/georgigaydarov/Projects/gesh-project-trading/venv/bin/python -m pytest tests/ -q -p no:cacheprovider
/Users/georgigaydarov/Projects/gesh-project-trading/venv/bin/ruff check .
/Users/georgigaydarov/Projects/gesh-project-trading/venv/bin/python -m pip check
```

Expected: zero failures, with only explicitly documented environment skips.

- [ ] **Step 3: Run cross-repository and security gates**

Run DTO producer/consumer compatibility, customer-isolation, login, magic-link, webhook, migration, degraded-run, causal-gate, MyBook concurrency, Monte Carlo, readiness, backup, and UI-wiring suites explicitly. Run tracked-file secret scanning and `git diff --check` in both worktrees.

- [ ] **Step 4: Review every changed file and document operations**

Update architecture/runbooks with user-ID migration, DTO version, run manifest, quota/job states, Monte Carlo quality, `/readyz`, backup/restore, and the explicit “not deployed” boundary.

- [ ] **Step 5: Commit documentation**

```bash
git add CLAUDE.md docs/ GAPS_AND_IMPROVEMENTS_2026-07-09.md
git commit -m "docs: close GESH remediation audit gaps"
```

- [ ] **Step 6: Final evidence report**

Report exact commits, test totals, coverage, lint/type/security results, remaining warnings, original-checkout status, and the commands required for a separately approved deployment. Do not claim live completion and do not restart services.
