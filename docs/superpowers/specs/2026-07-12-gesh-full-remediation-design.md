# GESH Full Remediation Design

**Status:** Approved for implementation by the user's explicit directive to implement all audited gaps.

**Repositories:**

- `/Users/georgigaydarov/Projects/gesh-project` — customer product and daily chain
- `/Users/georgigaydarov/Projects/gesh-project-trading` — authoritative paper-trading tracker and autonomy gate
- `/Users/georgigaydarov/Projects/gesh-core` — shared package, changed only if contract consolidation requires it

**Deployment boundary:** Code, tests, documentation, and commits are in scope. Restarting or deploying ports 9000/9003 and mutating live databases are out of scope until a separate approval after verification.

## Objective

Remove every correctness, tenant-isolation, security, financial-labeling, concurrency, provenance, runtime-drift, UI, and quality-gate gap found in the 2026-07-11 audit without overwriting the user's existing dirty work or weakening the paper-only safety model.

## Approaches Considered

### 1. Patch the dirty live checkout in place

This is fastest initially but unsafe. The product branch is behind its remote, the same files contain user-owned edits, and the product/trading repositories must change together. A merge conflict or failed migration could contaminate the live checkout.

### 2. Clean worktrees, selective integration, phased gates — selected

Create clean worktrees from the latest remote heads. Reproduce or selectively port each valid dirty fix behind a failing regression test, then implement remaining gaps in independently reviewable phases. Keep the original checkouts and live services untouched.

This costs more setup time but provides clean baselines, explicit rollback boundaries, and trustworthy test evidence.

### 3. Minimal stop-ship patch only

Fix tenant email migration, the proof gate, and FINRA units, then defer concurrency, authentication, Monte Carlo, runtime, and operations. This lowers immediate risk but does not satisfy the request to implement all gaps and would leave exploitable and misleading behavior.

## Global Design Principles

1. Immutable IDs, not email addresses, define customer ownership.
2. Financial data is never fabricated. Every derived result exposes source, observation time, coverage, and quality.
3. A risk gate runs before a paper record or order side effect.
4. Degraded inputs produce research-only artifacts and never action-like output.
5. One authoritative autonomy-gate implementation serves every consumer through a versioned contract.
6. Expensive customer work is reserved atomically, bounded globally, single-flight per customer, and published with compare-and-set semantics.
7. Product and trading imports use explicit package contracts, never `sys.path` shadowing.
8. Every behavior change follows red-green-refactor TDD. Existing dirty code is treated as an implementation candidate, not as proof.
9. Paper mode remains the default. This project does not enable live trading.

## Phase 0 — Isolated Baselines and Dirty-Work Preservation

The clean product worktree starts from `origin/fix/mybook-analysis-clean`; the clean trading worktree starts from `origin/main`. The original checkouts remain unchanged. Existing dirty changes are reviewed file by file and re-applied only when their regression tests demonstrate the audited behavior.

Baseline gates:

- Product: full offline suite passes from the clean remote head.
- Trading: full offline suite passes from the clean remote head.
- No live database, report, service, LaunchAgent, or environment file is changed.

## Phase 1 — Immutable Tenant Identity and Authentication Security

### Stable customer identity

Add a UUID `user_id` to `users`, backfill it transactionally, and enforce a unique index. Rebuild the five customer child tables so their ownership key is `user_id` with foreign keys to `users(user_id)` and `ON DELETE CASCADE`:

- `watchlist`
- `customer_positions`
- `customer_analyses`
- `usage_events`
- `customer_book_prefs`

Public Python APIs may continue accepting an email during the compatibility transition, but they must resolve it once to `user_id`; all child reads and writes use only the immutable ID. Billing email changes then update only the user profile. The old email can be reused without inheriting any rows.

Migration requirements:

- `BEGIN IMMEDIATE` transaction.
- One pre-migration SQLite backup for non-test databases.
- `PRAGMA foreign_keys=ON`, `busy_timeout`, schema version, and post-migration `foreign_key_check`.
- Fail closed on orphaned rows; do not attach them to a different account.
- Idempotent re-entry after interruption.

### Login hardening

`/auth/login` will:

- apply a strict bucket keyed by trusted client IP and normalized email;
- return `429` with `Retry-After` when exhausted;
- execute PBKDF2 through `asyncio.to_thread` so the FastAPI event loop remains responsive;
- perform one dummy PBKDF2 for unknown or inactive accounts to reduce account-enumeration timing differences;
- cap JSON request size before parsing.

### Magic-link session integrity

An active session must never be silently replaced by a magic token for a different user. The route preserves the current cookie and returns an explicit conflict/interstitial response. Same-user recovery remains single-use.

### Public input and error hardening

- Billing webhook bodies are stream-read with a hard byte limit and reject oversized requests with `413` before HMAC processing.
- Robinhood/offline and other expected customer errors return fixed public messages; detailed paths and exception strings are logged only server-side.
- Production configuration fails readiness when an HTTPS customer deployment disables secure cookies.

## Phase 2 — Authoritative Proof and Financial Provenance

### Versioned autonomy-gate DTO

The trading repository exports `gate_status_dto()` from the existing engine-only `check_autonomy_gate()` implementation. It includes a schema version, engine cohort sources, engine close count and requirement, complete criteria, readiness, summary, window, and `as_of`.

The product proof page consumes this DTO and never re-derives gate thresholds. Manual and engine trade cohorts remain separately visible, but only the engine cohort controls readiness. Unknown DTO versions fail closed and display “gate unavailable,” never “ready.”

### FINRA margin data

The macro module parses FINRA month labels with `%b-%y`, preserves official `$ millions` units, validates monotonic dates and plausible numeric ranges, and attaches `source`, `as_of`, `quality`, and cache metadata. Retrieval failure returns unavailable or explicitly stale cached data. It never invents a synthetic market series.

The dashboard renders values from their declared unit and removes the false claim that an `LT_Macro` consensus node exists. The long-term macro panel remains a display-only research overlay until walk-forward validation supports a voting role.

### Daily-run provenance

Create one run manifest containing run ID, input source, snapshot time, symbol-level quote times where available, age, degraded flag, and artifact timestamps. Every daily-chain step reads or propagates the manifest.

A degraded run:

- may produce a separately labeled research artifact;
- does not create paper entries, action alerts, fresh-looking engine timestamps, or current customer report replacements;
- does not publish over the last clean report.

## Phase 3 — Causal Paper Gate

Build the real `TradeIntent` from the action plan using actual confidence, notional, target percentage, and quote timestamp. Evaluate it before any paper-trade side effect. Only an approved intent may be recorded. One immutable intent ID links intent, gate decision, and paper record.

Rejected, stale, cash-blocked, or kill-switched intents remain visible in the audit result but produce zero paper positions. The separate product paper book and authoritative trading tracker remain intentionally separate.

## Phase 4 — MyBook Concurrency, Quotas, and Scope

### Job control

Replace unbounded per-request daemon threads with a bounded executor and durable job records keyed by `user_id` and `job_id`.

- At most one running analysis exists per user, regardless of fingerprint.
- Quota is reserved atomically before submission and finalized or released after completion.
- A global worker and queue cap protects CPU and LLM spend.
- Results publish only when the current job ID and book fingerprint still match.
- A stale worker cannot overwrite a newer result or write to an old email identity.
- Restart recovery marks abandoned running jobs failed and allows a safe retry.

### Full-book truthfulness

Detailed narrative cards may remain capped, but the model receives a lightweight summary for every holding: symbol, weight, value or equal-weight marker, sector, price availability, and relevant aggregate risk fields. Portfolio verdict, sector mix, and diversification use this complete summary. The response explicitly states detailed-card coverage.

## Phase 5 — Monte Carlo Correctness and Labels

The simulation contract explicitly declares `horizon_days=21`. Every UI and report label reads the returned horizon rather than hard-coding “1yr.”

Historical mean log return is simulated as a log increment without subtracting a second `0.5σ²`. Calibration reports:

- requested and usable observations;
- priced and fallback symbols;
- portfolio coverage percentage;
- data source and `as_of`;
- `quality` of `good`, `degraded`, or `unavailable`.

Fallback volatility may be used only with a visible degraded state. Insufficient portfolio coverage returns unavailable rather than a normal-looking risk result. Pure-function statistical tests cover zero-volatility drift, correlated covariance, deterministic seeds, fallback behavior, and horizon labeling.

## Phase 6 — Runtime, Readiness, Backup, UI, and CI

### Runtime ownership

Remove sibling-repository `sys.path` precedence. Product scripts import product modules explicitly and consume trading behavior through versioned contracts. Align the `gesh-core` immutable commit/version in both repositories and pin `alpaca-py` and other direct dependencies through a reviewed lock/constraints file.

### Readiness and backups

Keep `/healthz` as cheap process liveness. Add `/readyz` for local dependency readiness: auth DB query, foreign-key check status, tracker DB readability, required secrets/config, secure-cookie configuration, and artifact freshness. It performs no external market request.

Add a repository-owned SQLite backup command using the online backup API, integrity verification, timestamped files, retention, and documented restore test. Scheduling templates may be committed but are not loaded during this task.

### Customer UI

Customer navigation must never expose a button whose pane CSS cannot display. Scanner remains usable as public research. Deep Analysis, Signals, and Risk either render distinct anchored sections or use honest labels describing a shared analysis run. Browser-level tests cover role-specific navigation and the dynamic Monte Carlo horizon.

### CI and coverage

- Align CI with the supported Python runtime.
- Use the same coverage floor in CI and `pyproject.toml`.
- Require Ruff, full offline tests, customer isolation, cross-repository DTO contract, and migration/security suites.
- Achieve at least 80% coverage on every new or materially changed module; do not hide orchestrators from the report.
- Remove the wall-clock-dependent snapshot test by injecting or deriving the same clock used to create the fixture.

## Error Handling

Public responses use stable error codes and safe messages. Internal logs retain exception type and correlation ID, never secrets. Financial data failures carry typed quality/provenance instead of disappearing into broad exception handlers. Database migrations and quota reservations are transactional and idempotent.

## Rollback Strategy

- One commit group per phase and repository.
- Database backup and integrity check before schema migration.
- Compatibility lookup by email remains only until all child operations use `user_id`; rollback never reassigns rows by email.
- Product proof consumer fails closed if the trading DTO is absent or incompatible.
- Behavioral changes are locally configurable, but no flag may restore cross-tenant behavior, fabricated data, or gate-after-record ordering.
- Live services are not restarted in this implementation task.

## Verification Matrix

Each phase requires targeted red-green evidence and the full affected repository suite. Final verification includes:

1. Product and trading full offline suites.
2. Ruff on all changed Python and tests.
3. Coverage report and changed-module thresholds.
4. Migration round-trip, old-email reuse, concurrent rename/write, and foreign-key checks.
5. Login throttling, timing-path hash invocation, event-loop responsiveness, magic-session conflict, webhook cap, and error-sanitization tests.
6. Cross-repository gate DTO contract and proof-page fail-closed behavior.
7. FINRA fixture, unit/date/provenance, cache degradation, and no-synthetic-data tests.
8. Paper intent-to-gate-to-record causality and degraded-run no-side-effect tests.
9. MyBook quota concurrency, stale worker, restart recovery, and full-book scope tests.
10. Monte Carlo analytical and UI horizon tests.
11. Readiness, backup/restore, dependency, and customer navigation tests.
12. `git diff --check`, secret scan, and manual diff review.

## Acceptance Criteria

The remediation is complete only when all audited gaps above have corresponding passing regression tests, both repositories pass their complete quality gates, no original dirty-checkout file has been overwritten, and no live service or live database has been changed.
