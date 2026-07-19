# [CLAUDE-AUDIT] Adversarial Verdict — Codex Tasks 3 / 5 / 6 (PR #98)
**Reviewer:** Claude (Cowork, opus-4-8) · **Date:** 2026-07-19 · **Head:** codex/full-remediation-product → merged to main
**Reproduction:** full suite rerun on the Mac venv → **1,342 passed / 0 failed** (Codex reported 1,281; delta = Claude's own added tests). Targeted provenance/quota/auth suites all green within that run. Findings verified against source at file:line.

## Per-task verdicts

| Task | Verdict | Basis |
|---|---|---|
| **Task 3 — auth & public-error hardening** | **PARTIAL** | 4 of 5 claims fully verified with tests. One stated claim — "login request bodies are capped" — is **not implemented**: the 256 KiB cap guards `/api/billing/webhook` only. |
| **Task 5 — provenance + causal paper gate** | **CLOSED** (2 follow-ups) | All 6 claims verified; every safety invariant fails closed. Residuals are availability/continuity edges, never a mis-trade. |
| **Task 6 — durable bounded MyBook jobs + atomic quotas** | **CLOSED** | All 8 claims verified. Guards are genuinely DB-backed (BEGIN IMMEDIATE + partial UNIQUE index + in-txn publish), not in-process locks. No P0/P1. |

## Ranked findings

| ID | Sev | Task | File:line | Defect | Impact | Fix |
|---|---|---|---|---|---|---|
| V1 | **P2** | 3 | server.py:843 (`/auth/login`), :929 (`/auth/request-link`), :1133/:1140 (`/alerts/unsubscribe`, `/digest/unsubscribe`) | These public (unauthenticated, in PUBLIC_PREFIXES) POST routes read `await request.json()`/`.body()` with **no byte cap**; `_read_limited_body` is wired to the webhook only. Contradicts the "login bodies capped" claim. | Unauthenticated memory-exhaustion DoS on the login/recovery/unsub surface. Fails toward crash, not data loss. | Route every public POST body through `_read_limited_body(request, 64_KiB)` then `json.loads`. |
| V2 | **P2** | 5 | run_manifest.py:41 (`__post_init__`) via manifest_from_snapshot:125-127; unguarded at run_engines2.py:80 | A **non-empty but unparseable** provider `quote_as_of` (e.g. `"N/A"`) → `datetime.fromisoformat` `ValueError` → engines stage FATAL → whole daily run aborts. (A *missing* timestamp is handled defensively; a *corrupt* one is not.) | Availability DoS on the daily chain from one malformed provider field. Fails toward "no run", never a bad trade. | In `manifest_from_snapshot`, parse-and-skip bad values (treat unparseable as missing → downstream already fails closed). |
| V3 | **P2** | 6 | server.py:2966; auth_users.py:1889-1899 | Crash-orphaned MyBook job stays `running` (held by the partial unique index) until `GESH_MYBOOK_STALE_SECONDS` (default **7200s ≈ 2h**) expiry; no heartbeat. | A single user is soft-locked out of new book analysis for up to ~2h after a worker crash. Bounded, not permanent; no quota leak. | Lower default to ~900s (runs are minutes) and/or heartbeat `updated_at` during long runs. |
| V4 | P3 | 3 | server.py:782-807 | On an HTTPS deploy with `GESH_COOKIE_SECURE` unset, a request without `X-Forwarded-Proto` over `http` scheme can mint a non-Secure session cookie; readiness only trips on the *explicit* insecure override. Pre-existing, not a Task-3 regression. | Cleartext cookie under a misconfigured/bypassed proxy. | Force `secure=True` when `APP_BASE_URL` is https, independent of the per-request header. |
| V5 | P3 | 6 | auth_users.py:1827-1854 | Reservation state transitions keyed by `job_id` alone, not scoped by `user_id`. Not exploitable today (`job_id` is a server `uuid4`, never client-supplied). | Latent cross-tenant risk if any future endpoint accepts a client job_id. | Add `AND user_id=?` to reservation UPDATE/SELECTs (defense-in-depth). |
| V6 | P3 | 5 | run_engines2.py:82 vs :397 | `run_manifest.json` (written early) and the manifest embedded in `engine_results.json` (written ~minutes later) can transiently diverge / disagree on a crash mid-engines. Chain aborts, so no bad trade. | Dashboard can read mismatched run_ids during the engine window. | Publish both from the final embed together. |
| V7 | P3 | 6 | server.py:3092-3097; ai_analyzer.py:413-430 | Full-book portfolio reasoning for the >8-holding tail relies on a **soft** prompt instruction to use `all_holdings_summary`; data is structurally present but model use is non-deterministic. | Portfolio-level quality for un-detailed names is soft, not a correctness defect. | Reinforce in DEEP_SYSTEM_PROMPT; add a test asserting summary influence. |
| V8 | P3 | 6 | auth_users.py:965 via :1126 | `PRAGMA foreign_key_check` (full child-table scan) runs on **every** `_conn()`. Pre-existing, tangential to Task 6. | Per-request cost grows with usage tables. | Gate behind the user_version fast path. |
| V9 | P3 | 3 | server.py:4229 etc. | A few `str(exc)` sites remain, but all behind `require_api_key` (owner-only), not customer/public-reachable. | Owner-only exception text. | Replace with `_server_error` for consistency. |

No P0/P1 defects in any task. Constant-time PBKDF2, magic-link 409-no-consume, fail-closed data-freshness gate, strictly causal paper recording, degraded-run non-publishing, DB-backed quota atomicity, and the immutable-ID migration all verify with matching failure-path tests.

## Migration / deployment verdict
**Safe.** The gesh_auth.db v2 migration runs under `BEGIN IMMEDIATE` with a verified backup, creates new tables only when absent, uses `IF NOT EXISTS` indexes, FKs to `users(user_id)`, and ends with `PRAGMA foreign_key_check`. Idempotent/re-entrant across processes. Crypto book already restated ($9,702.13, 22 POL rows quarantined). Backups run nightly (com.gesh.backup).

## Merge recommendation
**APPROVE WITH FOLLOW-UPS.** PR #98 is already merged and the suite is green with no safety-critical defect. The three P2s (V1 unauth body DoS, V2 malformed-timestamp chain crash, V3 crash-lockout window) are availability/hardening, not correctness or data-safety — none warrant a revert. Land them as fast-follow fixes. The paid-expansion blocker remains legal/privacy/data-licensing, unchanged by this review.
