# Payment Integration Plan — close the `payment=false` launch gate

**Date:** 2026-06-26 · **Owner:** Georgi · **Status:** PLAN (no code written; decisions needed)
**Goal:** flip `launch_status.json → "payment": true` — the last unblocked Phase-1 milestone
that is purely *engineering* (the other open one, `entry_stream`, is the engine accumulating
trades, not a build).

> Scope note: this is the **highest-leverage** Phase-1 gap I could action without your
> accounts/keys. I did **not** create a Stripe account, wire live keys, or post anything.
> Everything below plugs into code that already exists.

---

## 1. What already exists (the integration is small)

| Asset | File | Relevance |
|---|---|---|
| FastAPI app + middleware login wall | `server.py:185` (`app`), `:200` (wall) | Webhook + pricing routes mount here |
| Per-user auth, **built to issue/revoke on payment** | `auth_users.py` | `create_user` / `disable` / `enable` / `verify_credentials` / `issue_token` |
| Public-route allowlist (wall-exempt) | `auth_users.PUBLIC_PREFIXES` (`:46`) | The webhook + `/pricing` must be added here |
| Public proof page (the sales asset) | `proof_page.py` → `GET /track-record` | Pricing CTA links from here |
| Legal | `legal_pages.py` → `/terms` `/disclaimer` | Checkout must link these |
| Milestone source of truth | `launch_status.json` (rule #34) | Flip `payment` here when live |

`auth_users.users` schema today (`auth_users.py:146`): `id, email, password_hash, is_active,
created_at, last_login_at` — **no billing columns yet** (the one schema change needed).

The product is single-tenant (one Robinhood account; users view the owner's research), so
"tiers" = **feature access**, not per-user portfolios. That keeps billing simple.

---

## 2. Decisions I need from you (these gate the build)

| # | Decision | Options | My recommendation |
|---|---|---|---|
| **D1** | Payment provider | **(a) Lemon Squeezy / Paddle** (merchant-of-record — they handle global sales-tax/VAT/EU compliance + invoicing) · (b) **Stripe** (lowest fees, but *you* own tax via Stripe Tax + remittance) | **(a) Lemon Squeezy** for a solo founder — MoR removes the entire tax-compliance burden, which is real liability for a one-person shop selling to multiple states/countries. Stripe only if you'll operate the tax layer yourself. |
| **D2** | Onboarding after payment | (a) auto-generate password + email it · (b) **magic-link** (email a one-time login link) · (c) customer sets password on a post-checkout page | **(b) magic-link** — no plaintext password emailed, least friction. Reuses `auth_users.issue_token`. |
| **D3** | Tier → feature map | who gets `/watch`, `/invest`, crypto, deep-debate | draft in §5 — confirm/adjust |
| **D4** | Transactional email sender | Resend / Postmark / SMTP | **Resend** (simple API, generous free tier) |
| **D5** | Speed vs. polish | **Phase 0** (zero-code Payment Links, manual provision — take money *this week*) → Phase 1 (webhook auto-provision) | Ship **Phase 0 now**, build Phase 1 in parallel |

Provider choice (D1) drives the SDK and webhook signature scheme but **not** the architecture
below — both are "hosted checkout + signed webhook → provision `auth_users`."

---

## 3. Architecture (provider-agnostic)

```
visitor → /pricing (public)
        → hosted Checkout (Stripe/LemonSqueezy — NO card data touches our server; zero PCI scope)
        → payment succeeds
        → provider POSTs signed webhook → POST /api/billing/webhook (public, signature-verified, idempotent)
              ├─ checkout/subscription_created → auth_users.create_user(email, tier) + email magic-link (D2)
              ├─ subscription_updated          → update tier
              └─ subscription_cancelled / payment_failed → auth_users.disable(email)
        → customer self-serve: /billing/portal → provider's hosted billing portal (cancel/update card)
```

Why hosted checkout: **no card data ever reaches `server.py`** → PCI scope is SAQ-A (the
minimum). Never build a card form.

---

## 4. Concrete build (Phase 1)

### 4a. Schema migration (`auth_users.py` — additive, back-compatible)
```sql
ALTER TABLE users ADD COLUMN tier               TEXT;     -- 'observer' | 'trader' | 'desk'
ALTER TABLE users ADD COLUMN billing_customer_id TEXT;    -- provider customer id
ALTER TABLE users ADD COLUMN billing_sub_id      TEXT;    -- provider subscription id
ALTER TABLE users ADD COLUMN billing_status      TEXT;    -- 'active'|'past_due'|'cancelled'
```
Run as `CREATE TABLE IF NOT EXISTS` + idempotent `ALTER` guarded by a `PRAGMA table_info`
check (mirrors the existing `_DDL` pattern at `auth_users.py:145`). `is_active` stays the
single gate the wall reads; billing webhooks flip it.

### 4b. New module `billing.py`
- `verify_signature(payload, header) -> bool` — HMAC over the raw body with
  `BILLING_WEBHOOK_SECRET`; **reject on mismatch** (constant-time compare, stdlib `hmac`).
- `handle_event(event: dict) -> None` — dispatch by type; **idempotent** via a
  `processed_webhook_events(event_id PK, seen_at)` table (dedup replays).
- `provision(email, tier)` → `auth_users.create_user` (or re-`enable`) + set tier + send
  magic-link via `email_sender.py`.
- `revoke(email)` → `auth_users.disable`.
- Pure/testable: signature + dispatch take raw inputs; no network in unit tests.

### 4c. Routes in `server.py`
```python
@app.get("/pricing", response_class=HTMLResponse)   # PUBLIC — add prefix to PUBLIC_PREFIXES
@app.post("/api/billing/webhook")                    # PUBLIC — signature-gated, NOT cookie-gated
@app.get("/billing/success")                         # post-checkout landing
@app.get("/billing/portal")                          # 302 → provider hosted portal (wall-gated)
```
**Critical:** add `/api/billing/webhook` and `/pricing` to `auth_users.PUBLIC_PREFIXES`
(`auth_users.py:46`) or the login wall will 302/401 the provider's callback.

### 4d. Secrets (`.env` — never hardcode, rule)
```
BILLING_PROVIDER=lemonsqueezy        # or stripe
BILLING_SECRET_KEY=                  # server API key
BILLING_WEBHOOK_SECRET=              # signing secret for verify_signature
RESEND_API_KEY=                      # transactional email (D4)
APP_BASE_URL=https://<your-domain>   # for magic-link + redirect URLs
```

---

## 5. Tier → entitlement map (D3 — confirm)

| Tier | Price | Gets | Enforce |
|---|---|---|---|
| **Observer** | $49/mo | `/track-record`, `/report`, `/watch` (read-only research) | wall-gated, tier≥observer |
| **Trader** | $149/mo | + `/invest` radar, `/api/trading/deep-debate/{sym}` | tier≥trader |
| **Desk** | $399/mo | + crypto signals + BTC/ETH options flow (port 9003 add-on) | tier≥desk |

Enforcement: extend `auth_users.gate_decision` (or a small `require_tier(min_tier)`
dependency) to read the session user's `tier` and compare. Keep `/track-record`, `/terms`,
`/disclaimer` **public** (never paywall the proof page — existing rule).

---

## 6. Security checklist (must hold before `payment=true`)
- [ ] Webhook **signature verified** on every call; unsigned/invalid → 400, no side effects.
- [ ] Webhook **idempotent** (event-id dedup) — providers retry; double-provision must be a no-op.
- [ ] Hosted checkout only — **no card fields in our HTML** (PCI SAQ-A).
- [ ] Secrets in `.env` / secret manager; **never** in code or logs; webhook body not logged raw.
- [ ] Magic-link tokens single-use + short TTL (reuse `issue_token` TTL semantics).
- [ ] Revoke path tested: cancel → `is_active=0` → wall blocks within one request.
- [ ] `/api/billing/webhook` is the **only** new unauthenticated write surface — rate-limit it.

## 7. Test plan
- Unit: `verify_signature` (good/bad/missing sig), `handle_event` dispatch (created→provision,
  cancelled→revoke), idempotency (same event twice → one provision). All mocked, no network.
- Integration: provider CLI (`stripe listen` / LS test webhooks) → `stripe trigger
  checkout.session.completed` → assert user row created + `is_active=1` + magic-link sent (fake sender).
- Regression: existing wall tests still pass; `/pricing` + `/api/billing/webhook` reachable
  unauthenticated; all gated routes still gated.
- Manual: real test-mode purchase end-to-end → login via magic-link → tier features visible.

## 8. Phasing & milestone flip
- **Phase 0 (today, zero-code):** create 3 hosted **Payment Links** (one per tier) on the
  provider; link them from `/track-record` + a static `/pricing`. On payment, you manually run
  `python3 auth_users.py create <email> <pw>` and email access. *Takes real money immediately;*
  no webhook yet. Good enough to validate willingness-to-pay.
- **Phase 1 (this week):** §4 webhook auto-provision + magic-link.
- **Phase 2:** self-serve `/billing/portal` + tier gating (§5).
- **Flip `launch_status.json "payment": true`** once Phase 1 is live and a real test purchase
  provisions + revokes correctly. The Friday tracker (`tracker_weekly.py`) then reports
  `payment✓` and commercial-readiness ticks up.

---

## 9. Estimate
- Phase 0: ~30 min (provider account + 3 Payment Links + a `/pricing` page). **No code review needed.**
- Phase 1: ~1 focused session (`billing.py` + 4 routes + schema migration + email + tests).
- Phase 2: ~½ session.

**Next action:** answer **D1–D5** (especially D1 provider). On D1 I can scaffold Phase 1
(`billing.py` + routes + tests) behind `BILLING_PROVIDER`, with the provider SDK swappable.

---

## 10. Implementation status (SHIPPED — 2026-06-26)

> Decisions landed: **D1 = Lemon Squeezy** (merchant-of-record), **D2 = magic-link**,
> **D4 = Resend**. Phase 1 is live, hardened across 4 Codex review rounds + an EXA-driven
> reliability review. `launch_status.json "payment"` ready to flip after a real test purchase.

**Files:** `billing.py` (webhook orchestrator), `auth_users.py` (typed tokens + billing cols),
`email_sender.py` (Resend), `server.py` (routes + middleware), `tests/test_billing.py` (35 tests, all pass).

**Routes:** `GET /pricing` (public), `GET /billing/success` (public), `POST /api/billing/webhook`
(public, HMAC-gated) — all wall-exempt via `auth_users.PUBLIC_PREFIXES`.

**Security checklist (§6) — status:**
- [x] HMAC-SHA256 over **raw body**, constant-time, **case-folded**, fail-closed (`verify_signature`).
- [x] **Idempotent** — outbox state machine (`pending`→`completed`), owner-fenced reclaim of crashed claims.
- [x] Hosted checkout only — no card fields touch us (PCI SAQ-A); MoR owns tax/VAT.
- [x] Secrets in `.env`; webhook body not logged raw.
- [x] Magic-link tokens **single-use** (`used_magic_tokens`) + 15-min TTL + `typ`-bound (not replayable as a session cookie).
- [x] Revoke path tested — `subscription_expired` → `is_active=0` → wall blocks within one request.
- [x] `/api/billing/webhook` rate-limited (under the `/api/` limiter) — it is the only new unauthenticated write.

**Lifecycle mapping (LS event → action):**
- `subscription_created` / `_resumed` / `_unpaused` / `_payment_success` / `order_created` → **provision** (allowlisted variant required).
- `subscription_payment_failed` → mark `past_due`, **keep access** (dunning grace — never revoke on first failed charge).
- `subscription_cancelled` / `_paused` → mark status, keep access to period end.
- `subscription_expired` → **revoke** (the only true lockout).
- `subscription_updated` → branch on `status` (active/expired/other).

**Reliability hardening (EXA review R4, 2026-06-26):**
- **Async onboarding safety net** — `billing.resend_owed_onboarding()` re-sends the magic link to any
  ACTIVE subscriber still owed one (`welcome_emailed_at IS NULL`), so delivery no longer depends solely
  on LS's 4-attempt retry budget. Idempotent (stamps only on success). Scheduled every 15 min via the
  `com.gesh.onboarding-sweep` LaunchAgent (`sweep_onboarding.sh`).
- **Dead-letter alert** — `billing._alert_dead_letter()` (best-effort, never raises) fires a Telegram
  alert when a paid user can't be onboarded, so a stuck subscriber surfaces immediately.

**Remaining (accepted / future):**
- No time-based replay window — LS sends no signed timestamp; dedup + `event_at` monotonic ordering are the mitigations.
- Tier→feature gating (Phase 2) — alpha sells one honest tier; only `is_active` is enforced today.
- Self-serve `/billing/portal` (Phase 2).
