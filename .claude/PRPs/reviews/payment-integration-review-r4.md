# Code Review (R4): Payment Integration — EXA gap analysis + hardening

**Reviewed**: 2026-06-26
**Scope**: `cb474c9` → `927e790` (billing.py, auth_users.py, server.py, email_sender.py, tests)
**Method**: full-file review + EXA research on Lemon Squeezy / Stripe / SaaS webhook best practices (2025–2026)
**Decision**: APPROVE WITH COMMENTS — no CRITICAL/HIGH. Prior Codex rounds (H1–H5, M1–M3) all verified resolved in code.
**Validation**: `pytest tests/test_billing.py` → 35/35 pass (28 prior + 7 added this round).

## Summary
The Lemon Squeezy merchant-of-record integration is materially more robust than a typical
SaaS webhook handler — fail-closed HMAC verification, an outbox-style idempotency state
machine (owner-fenced reclaim), past-due grace (revoke only on `subscription_expired`),
sub-id-authoritative account resolution, and email-change reconciliation. EXA confirms each
of these against current Lemon Squeezy + Stripe guidance. Remaining items are reliability
best-practice, not security, and are safe to defer at ~10-user alpha volume — two were
implemented this round.

## EXA best-practice scorecard

| Best practice | Status |
|---|---|
| HMAC-SHA256 over RAW body, constant-time, fail closed | ✅ `verify_signature` |
| Idempotency on retried deliveries | ✅ outbox state machine (exceeds typical) |
| Webhooks = source of truth, not checkout success URL | ✅ `/billing/success` is informational only |
| Don't revoke on first failed charge; keep access through dunning | ✅ `payment_failed`→`past_due` mark-only |
| Handle `subscription_updated` status transitions | ✅ |
| Strict variant allowlist (no name escalation) | ✅ `_tier_for` |
| Sub-id (not mutable email) as authoritative key | ✅ `_resolve_account` + email-change move |
| Return 2xx fast, defer work (async) | ⚠️ was synchronous incl. SMTP → **M1 safety net added** |
| Dead-letter / alert on delivery failure | ⚠️ silent → **M2 alert added** |
| Replay window | ⚠️ N/A (LS sends no signed timestamp); dedup is the mitigation — see L1 |
| Source-IP allowlist | ➖ LS publishes none; not actionable |

## Findings & actions

### MEDIUM — fixed this round
- **M1 — synchronous email coupled webhook latency to SMTP.** Added `billing.resend_owed_onboarding()`
  — an idempotent async safety net that re-sends to any ACTIVE subscriber with
  `welcome_emailed_at IS NULL` and stamps on success. Backed by `auth_users.list_owed_onboarding()`.
  Run on a schedule: `python3 billing.py sweep-onboarding`. The webhook-inline send is unchanged
  (still forces LS retry on failure); the sweep removes the dependence on LS's 4-attempt budget.
- **M2 — no dead-letter alert.** Added `billing._alert_dead_letter()` (best-effort, never raises),
  wired into the failed-onboarding path and the sweep. Reuses the project's existing Telegram
  channel (`trading.alerts._send`); logs at ERROR when the channel is unconfigured.

### LOW — fixed this round
- **L2 — signature hex case.** `verify_signature` now case-folds the incoming header
  (`signature.strip().lower()`) — removes a latent silent-outage risk if LS ever sends uppercase hex.

### LOW — accepted / documented (not changed)
- **L1 — no time-based replay window.** LS provides no signed timestamp, so a Stripe-style 5-min
  window isn't possible. Mitigations in place: body-hash dedup + `event_at` monotonic ordering.
  Residual edge (a stale captured payload for a brand-new email, replayed >30d after the dedup
  prune) is low-likelihood; revisit if volume grows.
- **L3 — confirm `attributes.user_email` on `subscription_payment_*` invoice payloads.** Handler
  400s when absent; harmless for renewals (idempotent) but verify against a live LS test webhook.

## Files changed (this round)
- `billing.py` — L2 case-fold; `_alert_dead_letter`; `resend_owed_onboarding`; `sweep-onboarding` CLI
- `auth_users.py` — `list_owed_onboarding()`
- `tests/test_billing.py` — +7 tests (signature case, dead-letter fires + never-raises, sweep recover/no-op/filter)

## Validation Results
| Check | Result |
|---|---|
| Tests (`pytest tests/test_billing.py`) | Pass — 35/35 |
| Module import smoke test | Pass |
| Lint/typecheck | Skipped (no configured runner in repo) |

## Files Reviewed
`billing.py`, `auth_users.py`, `server.py` (webhook route + middleware), `email_sender.py`, `tests/test_billing.py`.
