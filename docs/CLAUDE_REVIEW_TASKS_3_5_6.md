# Claude adversarial review packet — Codex Tasks 3, 5, and 6

**Prepared:** 2026-07-19  
**Repository:** `gesh75/Gesh-Project`  
**Head branch:** `codex/full-remediation-product`  
**Base:** current `main` through `e73102e`  
**Review commits:** `496cb89`, `4ed7109`, `658905c`

This is a merge-blocking review request, not a summary approval. Reproduce the
security, provenance, concurrency, and migration claims below. Rank findings as
P0–P3, include file/line evidence, and return a per-task verdict of **CLOSED**,
**PARTIAL**, or **NOT CLOSED**.

## Scope

### Task 3 — authentication and public-error hardening

Files: `auth_users.py`, `server.py`, `tests/test_auth_hardening.py`,
`tests/test_public_error_sanitization.py`.

Implemented claims:

- Password verification performs one PBKDF2 operation for known, unknown, and
  inactive accounts and runs off the event loop.
- Login request bodies are capped; throttled login responses include
  `Retry-After`.
- A valid session cannot be silently replaced by another user's magic link.
- HTTPS `APP_BASE_URL` plus an explicitly insecure cookie override fails closed.
- Broker, Anthropic, billing webhook, and unexpected public failures do not
  return raw exception text, paths, customer addresses, or upstream payloads.

Adversarial checks:

1. Can user enumeration still be inferred through database lookup or PBKDF2
   branch differences?
2. Can a magic token be consumed before the session-conflict decision?
3. Do malformed/chunked bodies bypass the configured cap?
4. Is any public 4xx/5xx path still returning `str(exc)` or nested exception
   content?
5. Does cookie readiness reject only the unsafe production combination without
   locking out HTTP development?

### Task 5 — immutable provenance and causal paper gate

Files: `run_manifest.py`, `build_snapshot_from_mcp.py`,
`fetch_live_snapshot.py`, `run_engines2.py`, `run_ai2.py`,
`run_paper_gate2.py`, `run_daily_analysis.sh`, `gen_report2.py`, and the
corresponding provenance/gate tests.

Implemented claims:

- One frozen `RunManifest` is atomically persisted to `run_manifest.json` and
  embedded in `engine_results.json` with a stable run ID.
- Both snapshot builders preserve provider/session metadata and the provider's
  own quote timestamp. Missing provider timestamps remain explicitly missing;
  transport time is never disguised as quote freshness.
- Each risk decision receives that symbol's quote timestamp.
- The paper chain remains intent → gate decision → record, and rejected intents
  never create paper records.
- Degraded runs return an immutable audit-only `PaperGateOutcome`, skip paper
  side effects, write a run-ID research artifact, and cannot replace either the
  current local report or the trading-repository report.
- Product modules no longer prepend the sibling trading repository to
  `sys.path`.

Adversarial checks:

1. Corrupt or missing quote timestamps: confirm the data-freshness gate fails
   closed rather than using snapshot transport time.
2. Manifest mismatch: can `run_manifest.json` and embedded provenance diverge
   after the AI stage or a partial write?
3. Degraded publishing: force every failure point and confirm
   `gesh_report.html` and the trading copy remain byte-identical.
4. Causality: inject recorder/gate failures and verify no rejected or undecided
   intent reaches the recorder and the UUID remains unchanged end to end.
5. Path/import isolation: confirm the product pipeline cannot accidentally load
   a same-named sibling module.

### Task 6 — durable bounded MyBook jobs and atomic quotas

Files: `auth_users.py`, `server.py`, `tests/test_mybook_job_control.py`,
`tests/test_mybook.py`, `tests/test_spend_gate_hardening.py`.

Implemented claims:

- SQLite now owns durable `mybook_jobs` and `usage_reservations` state.
- `BEGIN IMMEDIATE` serializes count-and-reserve; reserved plus completed events
  count in the rolling window and released reservations do not.
- A partial unique index permits only one queued/running generation per user,
  including across processes.
- One module-level `ThreadPoolExecutor` plus a bounded semaphore replaces
  unbounded daemon-thread creation.
- Workers carry immutable `user_id`, `job_id`, and fingerprint values. A stale
  generation cannot publish over a newer job or a changed book.
- Job results and analysis history publish in the same transaction before the
  active-job slot is released.
- Crash-orphaned jobs expire and release reservations; durable-state failures
  deny new spend with a sanitized 503.
- Detailed cards stay capped, but `all_holdings_summary` and
  `detailed_coverage` make the full book available for portfolio-level judgment.

Adversarial checks:

1. Run the 30-request job-start race and 30-thread quota race repeatedly under
   WAL/lock contention. Prove one active generation and no over-limit accepts.
2. Kill the process in queued, running, publication, and delivery phases. Verify
   retry behavior, reservation state, and no permanent lockout.
3. Finish an old worker after a newer generation. Confirm neither cache nor
   `customer_analyses` can regress.
4. Exhaust worker and queue capacity. Confirm the rejected submission releases
   quota and exposes no exception details.
5. Attempt cross-tenant job IDs and fingerprints directly against storage APIs.
   Verify every read/write remains scoped to immutable `user_id`.
6. Upgrade a realistic pre-change `gesh_auth.db`; confirm tables, foreign keys,
   partial indexes, permissions, and existing tenant data survive.
7. Inspect the actual AI prompt path and verify portfolio-level sections consume
   `all_holdings_summary`, not merely the eight detailed names.

## Required reproductions

```bash
git fetch origin
git checkout origin/codex/full-remediation-product
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest tests/ -q -p no:cacheprovider

PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest \
  tests/test_auth_hardening.py \
  tests/test_public_error_sanitization.py \
  tests/test_run_manifest.py \
  tests/test_quote_freshness.py \
  tests/test_degraded_provenance.py \
  tests/test_paper_gate_causality.py \
  tests/test_mybook_job_control.py \
  tests/test_mybook.py \
  tests/test_spend_gate_hardening.py \
  tests/test_tenant_identity_migration.py \
  tests/test_customer_leak_sweep.py -q -p no:cacheprovider

venv/bin/python -m py_compile \
  auth_users.py server.py run_manifest.py build_snapshot_from_mcp.py \
  fetch_live_snapshot.py run_engines2.py run_ai2.py run_paper_gate2.py \
  gen_report2.py
bash -n run_daily_analysis.sh
```

Codex's final local evidence before this packet was added: **1,281 passed, one
expected skip, zero failures**. Do not accept that report without rerunning it.

## Required output

1. Ranked findings table: severity, file/line, reproduction, impact, fix.
2. Task 3 verdict: CLOSED / PARTIAL / NOT CLOSED.
3. Task 5 verdict: CLOSED / PARTIAL / NOT CLOSED.
4. Task 6 verdict: CLOSED / PARTIAL / NOT CLOSED.
5. Explicit migration/deployment verdict.
6. Explicit merge recommendation: **APPROVE**, **APPROVE WITH FOLLOW-UPS**, or
   **BLOCK**.

Do not modify production data, restart services, or place broker orders during
review. Use temporary databases and offline mocks only.
