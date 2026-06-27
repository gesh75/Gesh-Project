# Codex Review Handoff — Payment Integration (Lemon Squeezy → :9000)

**Author:** Georgi · **Date:** 2026-06-26 · **Repo:** `gesh-project` (main)
**Reviewer role:** Adversarially verify the payment/auth flow. Assume real money + real
accounts. Your job is to find the way in, the double-charge, the access leak, the stuck user.

---

## 1. What this change does (the contract to verify)

Closes the launch `payment` gate. Lemon Squeezy (LS) is the **merchant of record** — it owns
checkout, card data, tax/VAT. We only react to its signed webhooks.

```
/pricing  ──► LS hosted checkout (no card data touches us)
                 │  payment succeeds
                 ▼
LS POSTs signed webhook ──► POST /api/billing/webhook
                 │  verify HMAC → dedup → parse → provision
                 ▼
auth_users: create passwordless user + email magic link
                 │  user clicks link
                 ▼
GET /auth/magic?token=… ──► consume single-use token → set fresh session cookie → /
```

Revoke path: `subscription_expired` (only) → `set_active(False)` → blocked on the user's
very next request. `payment_failed`/`cancelled` keep access (dunning / runs-to-period-end)
and only record `billing_status` — see §6/§7.

### Files in scope
| File | What to scrutinize |
|---|---|
| `billing.py` | Signature verify (fail-closed), dedup idempotency, event→action mapping, provision/revoke |
| `auth_users.py` | `consume_magic_token` (single-use), `create_magic_link_user`, billing-column migration, `authenticate_session` |
| `server.py` | `/api/billing/webhook`, `/auth/magic`, `/pricing`, `/billing/success`, `PUBLIC_PREFIXES` |
| `email_sender.py` | Resend send; never-raise contract |
| `tests/test_billing.py` | Coverage gaps |

---

## 2. Known issue already found (confirm + fix)

**H1 — magic token == session token (no type claim).**
`issue_token` mints the same `{sub, exp}` HMAC shape for both the 15-min magic link and the
7-day session. `authenticate_session` accepts ANY valid token. So a captured magic-URL token
can be replayed directly as the `gesh_session` cookie, bypassing the `used_magic_tokens`
single-use guard (which only triggers on the `/auth/magic` path).

→ **Confirm reproducible**, then verify the proposed fix: add `"typ": "magic"|"session"` to
the token payload; `consume_magic_token` rejects non-magic; `authenticate_session` rejects
non-session. Check no existing 7-day cookies break (back-compat: treat missing `typ` as
`session` during a grace window, or accept reissue-on-next-login).

---

## 3. Adversarial checklist — try to break each

### Signature / webhook auth
- [ ] Empty/missing `BILLING_WEBHOOK_SECRET` → does `verify_signature` fail CLOSED? (it should)
- [ ] Timing: is the compare constant-time? (`hmac.compare_digest` — confirm no early return leaks)
- [ ] Can a body be replayed with a *mutated* field but same signature? (no — sig is over raw body)
- [ ] Signature over `raw` body vs the re-parsed `payload` — confirm we sign-check `raw`, not a
      re-serialized dict (we do; verify the route passes `await request.body()` unchanged).

### Idempotency / double-provision
- [ ] Dedup key = SHA-256 of raw body. Two *distinct* LS events (created, then updated) for the
      same sub have different bodies → both process. Intended? Confirm no double-charge risk
      (we don't charge — LS does — but double-provision should be harmless/idempotent).
- [ ] Race: two identical deliveries arrive concurrently. `INSERT OR IGNORE` + the
      `_already_processed` check are in separate connections — is there a TOCTOU window where
      both pass the check and both provision? (provision is idempotent, but confirm.)
- [ ] `_mark_processed` runs AFTER provisioning. If provisioning succeeds but the process dies
      before mark, LS retries → re-provision. Idempotent? (yes for create; verify for revoke.)

### Access control / authz
- [ ] `PUBLIC_PREFIXES` now includes `/pricing`, `/api/billing/webhook`, `/billing/success`.
      Confirm none of these `startswith`-shadow a protected route (e.g. does `/billing/success`
      prefix-match anything sensitive? does the matching use exact-or-startswith correctly?).
- [ ] `subscription_cancelled` keeps access until `expired`. Verify a cancelled-then-never-
      expired user can't keep access forever if LS never sends `expired`.
- [ ] Revoked user (`is_active=0`) — confirm EVERY gated route re-checks via
      `authenticate_session`, not a cached cookie decode.

### Magic link / session
- [ ] H1 above.
- [ ] `secure=False` cookie — flag for TLS deploy (see M2 in review).
- [ ] Token TTL: magic 15 min, session 7 days. Confirm `/auth/magic` mints a SESSION token
      (7d), not re-using the 15-min one.
- [ ] `used_magic_tokens` unbounded growth (M1).

### Tier mapping / data
- [ ] `_tier_for` falls back to substring match on `variant_name`/`product_name`. Can a crafted
      variant name map to a higher tier than paid for? (e.g. an "Observer" product whose name
      contains "desk"). Recommend variant-ID-only mapping in prod (`LS_VARIANT_*`).
- [ ] `user_email` from payload is the account key — does LS guarantee it's verified before the
      webhook fires? If not, an unverified email could be provisioned. (M3)

---

## 4. How to run / verify locally

```bash
cd gesh-project && ./venv/bin/python -m pytest tests/test_billing.py -q   # 23/23 expected (post-fix)
```

No network needed: email is monkeypatched, SQLite is a temp file, secret is fixed.

### Suggested NEW tests to add (coverage gaps)
- [ ] H1 regression: a magic token rejected by `authenticate_session`; a session token rejected
      by `consume_magic_token`.
- [ ] Concurrent duplicate delivery (threaded) → exactly one provision.
- [ ] `subscription_updated` with terminal status → revoke (only `created`/`expired` are tested).
- [ ] `_tier_for` substring-escalation guard.
- [ ] Webhook with malformed JSON body but valid signature → 400, not 500.

---

## 5. Decision request

Please return: **APPROVE / REQUEST CHANGES / BLOCK**, the H1 reproduction result, and any
new findings from §3 with severity. Reference `.claude/PRPs/reviews/payment-integration-review.md`
for the full local review.

---

## 6. Round-2 resolutions (applied 2026-06-26 — re-verify)

All six HIGH findings + the three MEDIUMs from the first Codex pass are addressed. Please
re-verify each against the listed code/tests.

| # | Finding | Resolution | Test |
|---|---------|-----------|------|
| H1 | Interchangeable magic/session tokens | `issue_token(typ=…)` binds a `typ` + random `jti` into the signed payload; `verify_token(expected_typ=…)` enforces it. `authenticate_session` requires `session`; `consume_magic_token` requires `magic`. Missing `typ` ⇒ `session` (back-compat grace). | `test_magic_token_rejected_as_session`, `test_session_token_rejected_as_magic`, `test_legacy_typeless_token_treated_as_session` |
| H2 (id) | `data.id` mis-used as sub id | `billing._subscription_id()` resolves per event: `subscription_*` → `data.id`; `subscription_payment_*` / `order_created` → `attributes.subscription_id`. | `test_payment_event_does_not_corrupt_sub_id` |
| H2 (stale) | Old sub disabling newer | `revoke()` ignores events whose `event_sub_id` ≠ stored `billing_sub_id`. | `test_stale_subscription_expiry_does_not_disable_newer_sub` |
| H3 | Unknown product granted access | `_dispatch` refuses provision when `tier is None` → `{action:ignored, reason:unknown_variant}`. **Action: set `LS_VARIANT_*` / `LS_CHECKOUT_PRIMARY` to the real allowlisted variant before launch.** | `test_unknown_variant_is_not_provisioned` |
| H4 | First payment-fail revokes | `subscription_payment_failed` → mark `past_due` (keeps access); revoke only on `subscription_expired`; `subscription_updated(cancelled/past_due/unpaid)` keeps access. | `test_payment_failed_keeps_access_marks_past_due`, `test_updated_cancelled_keeps_access` |
| H5 | Email-fail → stuck user | `welcome_emailed_at` NULL-flag drives reliable re-send; webhook releases the dedup claim if a new/owed user couldn't be emailed (LS retries); `revoke` clears the flag so re-subscribe re-onboards; new rate-limited `POST /auth/request-link` recovery route (5/10min per IP). | `test_failed_onboarding_email_is_not_deduped`, `test_request_link_only_emails_active_user` |
| H6 | Tiers unenforced | **Decision: sell ONE tier now.** `/pricing` collapsed to a single env-driven plan (`LS_PLAN_NAME`/`LS_PLAN_PRICE`/`LS_CHECKOUT_PRIMARY`). Re-introduce tiers once Phase 2 entitlement gating exists. | (manual) |
| M1 | Dedup TOCTOU | `_claim()` = atomic `INSERT OR IGNORE` rowcount gate before dispatch (replaces check-then-act); `_unclaim()` on failure. | covered via H5 retry test |
| M2 | Cookie `secure=False` | `_cookie_secure()` reads `GESH_COOKIE_SECURE` (default **True**); set `0` only for local HTTP. Applied to both `/auth/login` and `/auth/magic`. | (manual) |
| M3 | Tables grow unbounded | `used_magic_tokens` pruned >1d in `_migrate`; `processed_webhook_events` pruned >30d in `_claim`. | (manual) |

**New env knobs:** `GESH_COOKIE_SECURE` (default 1), `LS_PLAN_NAME`, `LS_PLAN_PRICE`,
`LS_CHECKOUT_PRIMARY`, `LS_VARIANT_*` (allowlist).

**Verification after fixes:** `pytest -q` → **218 passed** (was 208 + 10 new); `ruff check
billing.py` clean; `py_compile` ok; `git diff --check` clean. Two pre-existing E501s remain
in `auth_users.py` (lines 96, 263) — untouched by this change.

**Open follow-ups (not blocking, your call on severity):**
- ~~Rate-limit `/auth/request-link`~~ **DONE** — per-IP 5/10min via `_bucket_ok`, returns 429.
- ~~`_tier_for` substring fallback~~ **DONE in round-3** — strict allowlist once any id is set.
- Confirm LS guarantees `user_email` is verified before webhook fires (M3 in review).

---

## 7. Round-3 resolutions (applied 2026-06-26 — re-verify)

Second Codex pass found deeper webhook-lifecycle issues. All addressed.

| # | Finding | Resolution | Test |
|---|---------|-----------|------|
| R3-1 HIGH | Email failure still HTTP 200 → LS never retries | `handle_webhook` raises `billing.RetryableError` on the owed-but-unsent path; `server.py` maps it → **503**. | `test_failed_onboarding_email_raises_retryable_and_is_not_deduped` |
| R3-2 HIGH | Stale guard only on revoke, not activation | Monotonic ordering: `parse_event` reads `attributes.updated_at` → stored `billing_event_at`; `_is_stale` drops any state-changing event older than the last applied one (activation included). | `test_delayed_activation_for_old_sub_is_ignored` |
| R3-3 HIGH | `LS_VARIANT_*` not strict (name fallback fired) | `_tier_for` disables the name-substring fallback whenever ANY `LS_VARIANT_*` id is configured — unknown id ⇒ refused. | `test_strict_allowlist_rejects_name_match_when_ids_configured` |
| R3-4 HIGH | Claim swallows event if crash after `_claim` | Dedup is now an outbox state machine: `pending` on claim, `completed` only on success; a `pending` row past a 300s lease is reclaimed by a later retry. | `test_crash_after_claim_is_reclaimed_after_lease` |
| R3-5 MED | Lifecycle keyed by mutable email → 2 accounts | `billing_sub_id` is authoritative: `_resolve_account` finds the account by sub id and **renames** it on an email change (one account), not a duplicate. | `test_email_change_keeps_single_account` |
| R3-6 MED | `/auth/request-link` no real coverage + no UI | TestClient exercises the route (200, no enumeration, 429 after 5/IP); login page now has an "Email me a sign-in link" action. | `test_request_link_endpoint_and_rate_limit` |
| R3-7 LOW | Doc inconsistency (`payment_failed` revokes) | Fixed §2 flow + this section. | — |

**New env knobs (round-3):** `LS_VARIANT_OBSERVER/TRADER/DESK` now act as a **strict allowlist**.
New columns: `users.billing_event_at`; `processed_webhook_events` gains `status/claimed_at/completed_at`
(idempotent ALTER migrations).

**Residual / explicitly deferred (single-tenant alpha):**
- No background outbox flusher — retries are LS-driven + lease-reclaimed, which is sufficient
  without a worker process. A true transactional outbox is the production upgrade.
- One active subscription per user is assumed (LS permits multiple). The foreign-sub case is
  handled by `updated_at` ordering, not a per-subscription state table.
- Email-change merge falls back (logs a conflict) if the target email is already a different
  account — it never clobbers.

**Verification after round-3:** `pytest -q` → **223 passed** (218 + 5 new); `ruff check
billing.py` clean; `py_compile` ok. Two pre-existing E501s remain in `auth_users.py`
(`hash_password`, `set_active`) — untouched.

---

## 8. Round-4 resolutions (applied 2026-06-26 — re-verify)

Third Codex pass found concurrency/ordering correctness bugs in the round-3 machinery.

| # | Finding | Resolution | Test |
|---|---------|-----------|------|
| R4-1 HIGH | `in_progress` returned 200 → LS stops retrying → 300s reclaim never reached | `handle_webhook` raises `RetryableError` → **503** for `in_progress` (only `completed` 200s); lease shortened **300s → 60s** so a crashed claim is reclaimable inside LS's retry window. | `test_crash_after_claim_is_reclaimed_after_lease` |
| R4-2 HIGH | Stale event renamed the account before being rejected | `_dispatch` now: read-only `_lookup_account` → validate ordering **and** variant → only then `_apply_email_change`. No mutation precedes validation. | `test_stale_event_does_not_rename_before_rejection` |
| R4-3 MED | Lease reclaim not owner-fenced (double dispatch / old worker clobbers newer claim) | Each claim carries a random `owner` token; reclaim is a compare-and-swap on `(owner, claimed_at)` (rowcount gate); `_complete`/`_unclaim` are fenced `WHERE owner IS ?`. | `test_reclaim_is_owner_fenced` |
| R4-4 LOW | Raw ISO-string timestamp compare is format-sensitive | `_parse_dt` parses to aware `datetime` (tolerates trailing `Z`); `_is_stale` compares datetimes. | covered by the ordering tests |

**Schema:** `processed_webhook_events` gains an `owner` column (idempotent ALTER).

**Verification after round-4:** `pytest -q` → **225 passed** (223 + 2 new); `ruff check
billing.py` clean; `py_compile` ok.

**Residual (unchanged, single-tenant alpha):** no background outbox flusher — recovery is
LS-retry + lease-reclaim, now correct end-to-end (503 keeps LS retrying until completion or
lease lapse). A multi-worker deployment is safe via the owner-fenced CAS; a true distributed
queue remains the eventual production upgrade.
