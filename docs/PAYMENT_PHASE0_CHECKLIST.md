# Payment — Phase 0 checklist (take money this week, zero code)

**Provider:** Lemon Squeezy (merchant-of-record — handles sales tax/VAT). **Date:** 2026-06-26.

Phase 0 lets you sell **today** using hosted Payment Links + manual provisioning. Phase 1
(the webhook auto-provision in `billing.py`) is already built and tested — you flip to it by
just setting the env vars in step 6. Until then, do the manual steps below.

> **STATUS (2026-06-30): Phase 0 SUPERSEDED — went straight to Phase 1 (webhook auto-provision),
> validated end-to-end in TEST mode and Codex-APPROVED.** Store `geshpro` · one alpha tier
> Observer **$49/mo** (variant `1852694`) · webhook live over the quick tunnel · a real test-card
> purchase → provision → onboarding email → cancel → expire all confirmed. The manual-provision
> steps (§4) were **not** used. **Only remaining gate: §7 — flip `payment=true` at go-live
> (Phase 2: live LS keys + a stable named tunnel on a real domain).** Full record:
> `PAYMENT_GOLIVE_CHECKLIST.md`.

> Steps marked **[you]** need your Lemon Squeezy account and can't be automated from here.

## 1. Create the store & products **[you]**
- [x] Sign up at lemonsqueezy.com, create your store, complete payout/identity. *(store `geshpro`)*
- [x] Create a **subscription** product (monthly) — alpha ships ONE honest tier (not 3):
  - Observer — **$49/mo** *(GESH Access)*
  - ~~Trader — $149/mo~~ · ~~Desk — $399/mo~~ *(deferred until per-tier entitlement gating exists)*
- [x] Note the **variant ID** (Settings → product → variant) — **`1852694`**.

## 2. Get the hosted checkout links **[you]**
- [x] Copy the product's **Share / Buy link** *(`LS_CHECKOUT_PRIMARY`)*.
- [ ] In the checkout's settings, set the **redirect after purchase** to
      `https://<your-domain>/billing/success` (that page already exists). *(optional — landing already shown)*

## 3. Wire the links into the live pricing page
The `/pricing` page renders the alpha tier card; it reads the checkout URL from env. Set:
```
# .env  (gesh-project)
LS_CHECKOUT_PRIMARY=https://geshpro.lemonsqueezy.com/checkout/buy/<uuid>
```
- [x] Set the var, restart :9000, visit `/pricing` → the **Subscribe** button works.
- [ ] Link `/pricing` from `/track-record` (the proof page is the top of funnel). *(polish)*

## 4. Manually provision on each sale (Phase 0 only) — ⨯ SUPERSEDED by Phase 1
*(Not used — webhook auto-provision replaced manual provisioning. Kept for reference.)*
```bash
cd ~/Projects/gesh-project && source venv/bin/activate
python3 auth_users.py create buyer@example.com           # generates a password, prints it
python3 auth_users.py list                               # confirm active
```

## 5. Turn the login wall ON **[you]**
```
# .env
GESH_AUTH_ENABLED=1
GESH_AUTH_SECRET=<32+ random chars>     # e.g. python3 -c "import secrets;print(secrets.token_urlsafe(48))"
```
- [x] Restart :9000. Public routes (`/pricing`, `/track-record`, `/terms`, `/disclaimer`)
      stay open; everything else now requires a session. **Test before announcing.**

## 6. Flip to Phase 1 (webhook auto-provision) — ✅ DONE (TEST mode, Codex-approved)
The webhook handler (`billing.py`), routes (`/api/billing/webhook`, `/auth/magic`), and
schema migration are shipped + tested. Activated:
- [x] Lemon Squeezy → Settings → **Webhooks** → added `…/api/billing/webhook`,
      subscribed to subscription + order events, copied the **signing secret**.
- [x] Set env and restarted: `BILLING_WEBHOOK_SECRET`, `LS_VARIANT_OBSERVER=1852694`,
      `LS_CHECKOUT_PRIMARY`, `RESEND_API_KEY`, `EMAIL_FROM`, `APP_BASE_URL` (quick tunnel).
- [x] Verified with a real test-mode purchase: subscriber row **active** with the right tier,
      magic-link email delivered; cancel kept access to period end; expire **revoked** on next request.
- [x] Manual provisioning retired — provisioning is automatic.

## 7. Flip the launch milestone
- [ ] In `gesh-project-trading/launch_status.json` set `"payment": true`.
- [ ] The Friday tracker (`tracker_weekly.py`) will then report `payment✓` and bump
      commercial-readiness. Don't regex the roadmap prose — `launch_status.json` is the source (rule #34).

---

### Security reminders (already enforced in code)
- Hosted checkout only — **no card data touches :9000** (PCI SAQ-A).
- Webhook is **signature-verified + idempotent**; unsigned calls are rejected (401).
- Secrets live in `.env` only — never commit them.
- Magic-link tokens are short-TTL (15 min) **and single-use** (consumed atomically on
  first use; the cookie gets a separate fresh session token, never the URL token), with
  `Referrer-Policy: no-referrer` on the landing — a leaked link can't be replayed.
