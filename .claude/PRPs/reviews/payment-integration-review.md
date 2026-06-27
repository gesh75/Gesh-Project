# Code Review: Payment Integration (Lemon Squeezy) — :9000

**Reviewed**: 2026-06-26
**Scope**: commits `cb474c9` (LS integration) → `f755d81` (magic-link single-use) → `389cdc7` (302→303)
**Branch**: main (already committed)
**Decision**: APPROVE WITH COMMENTS — one MEDIUM/HIGH security gap worth closing before real traffic

## Summary
Clean, well-tested merchant-of-record integration: hosted LS checkout (no card data
touches us) → HMAC-verified, idempotent webhook → provision/revoke on `auth_users`.
Fail-closed signature check, byte-hash dedup, and passwordless magic-link onboarding.
13/13 tests pass. One real weakness: magic-link tokens and session tokens are structurally
interchangeable, which partially defeats the single-use protection added in `f755d81`.

## Findings

### CRITICAL
None.

### HIGH
**H1 — Magic-link token is accepted as a session cookie, bypassing single-use guard**
`auth_users.py:319 authenticate_session` → `verify_token` accepts ANY valid HMAC
`{sub, exp}` token. `issue_token` produces the same token shape for both the 15-min
magic link and the 7-day session — there is no type claim. So an attacker who captures
the magic URL token (browser history, Referer, proxy log) before the user clicks can set
it directly as the `gesh_session` cookie and authenticate for its TTL **without ever
hitting `/auth/magic`**, never tripping the `used_magic_tokens` single-use check.
This is the same "leaked link → reusable session" risk the commit aimed to close, left
open on the cookie path.
- Mitigating: 15-min TTL; requires intercepting the URL in that window.
- Fix: add a `"typ": "magic"|"session"` claim in `issue_token`; `consume_magic_token`
  rejects non-magic, `authenticate_session` rejects non-session. Cheap, fully closes it.

### MEDIUM
**M1 — `used_magic_tokens` grows unbounded** (`auth_users.py`). No pruning of spent
tokens. Low volume so not urgent, but add a periodic `DELETE WHERE used_at < now-1d`
(tokens are dead after their 15-min TTL anyway).

**M2 — `/auth/magic` sets `secure=False` on the session cookie** (`server.py:auth_magic`).
Intentional (commented "set True once :9000 is served over TLS") but it means the session
cookie rides plain HTTP today. Gate on an env flag (`GESH_COOKIE_SECURE`) so prod TLS
flips it without a code edit, and so it can't ship to a TLS deploy still False.

**M3 — Webhook trusts `user_email` from payload as the account key.** Correct for LS
(email is provider-verified at checkout), but worth a one-line assertion/normalization
note: provisioning creates an account for whatever email LS sends. Fine given HMAC gate;
flagging for the Codex pass to confirm LS guarantees email verification before webhook.

### LOW
**L1 — Pricing/email HTML is hand-built string concatenation** (`server.py:pricing_page`,
`email_sender.py`). Tier names/prices are static literals (no user input) so no XSS today,
but if any of these ever interpolate user/DB data, escape it. Note for future.

**L2 — `tier` value is rendered into email HTML** (`email_sender.py:send_magic_link`).
`tier` originates from `_tier_for` which returns only `observer|trader|desk|None` — safe.
Keep it that way (don't let raw `variant_name` reach the template).

## Validation Results
| Check | Result |
|---|---|
| Tests (`pytest tests/test_billing.py`) | **Pass** — 13/13 in 0.16s |
| Lint | Skipped (not run) |
| Type check | Skipped |
| Build | N/A (Python) |

## Files Reviewed
- `billing.py` (Added, 220) — webhook orchestrator, sig verify, dedup, tier map, provision/revoke
- `auth_users.py` (Modified, +87) — billing columns migration, magic-link user, single-use consume
- `email_sender.py` (Added, 71) — Resend transactional email, dev no-op
- `server.py` (Modified, +106) — `/auth/magic`, `/pricing`, `/billing/success`, `/api/billing/webhook`
- `tests/test_billing.py` (Added, 161) — sig/dedup/provision/revoke/single-use coverage
- `docs/PAYMENT_INTEGRATION_PLAN.md`, `docs/PAYMENT_PHASE0_CHECKLIST.md` (docs)

## What's good (keep)
- Fail-closed signature verify with `compare_digest`; webhook public *only* because it
  carries its own HMAC auth (correct).
- Idempotency via raw-body SHA-256 — sound given LS retries are byte-identical.
- Revocation takes effect next request (`authenticate_session` re-checks `is_active`).
- Idempotent ALTER migration so existing `gesh_auth.db` picks up billing columns.
- Email failure never 500s the webhook; LS will retry on non-2xx.
