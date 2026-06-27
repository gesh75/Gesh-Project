# Payment — Phase 0 checklist (take money this week, zero code)

**Provider:** Lemon Squeezy (merchant-of-record — handles sales tax/VAT). **Date:** 2026-06-26.

Phase 0 lets you sell **today** using hosted Payment Links + manual provisioning. Phase 1
(the webhook auto-provision in `billing.py`) is already built and tested — you flip to it by
just setting the env vars in step 6. Until then, do the manual steps below.

> Steps marked **[you]** need your Lemon Squeezy account and can't be automated from here.

## 1. Create the store & products **[you]**
- [ ] Sign up at lemonsqueezy.com, create your store, complete payout/identity.
- [ ] Create 3 **subscription** products (monthly):
  - Observer — **$49/mo**
  - Trader — **$149/mo**
  - Desk — **$399/mo**
- [ ] For each, note the **variant ID** (Settings → product → variant) — needed in step 6.

## 2. Get the hosted checkout links **[you]**
- [ ] For each product, copy its **Share / Buy link** (e.g. `https://<store>.lemonsqueezy.com/buy/<uuid>`).
- [ ] In each checkout's settings, set the **redirect after purchase** to
      `https://<your-domain>/billing/success` (that page already exists).

## 3. Wire the links into the live pricing page
The `/pricing` page already renders 3 tier cards; it reads checkout URLs from env. Set:
```
# .env  (gesh-project)
LS_CHECKOUT_OBSERVER=https://<store>.lemonsqueezy.com/buy/<observer-uuid>
LS_CHECKOUT_TRADER=https://<store>.lemonsqueezy.com/buy/<trader-uuid>
LS_CHECKOUT_DESK=https://<store>.lemonsqueezy.com/buy/<desk-uuid>
```
- [ ] Set the three vars, restart :9000, visit `/pricing` → the **Subscribe** buttons now work.
- [ ] Link `/pricing` from `/track-record` (the proof page is the top of funnel).

## 4. Manually provision on each sale (Phase 0 only)
When Lemon Squeezy emails you "new subscription":
```bash
cd ~/Projects/gesh-project && source venv/bin/activate
python3 auth_users.py create buyer@example.com           # generates a password, prints it
python3 auth_users.py list                               # confirm active
```
- [ ] Email the buyer their login (or a magic link — see Phase 1).
- [ ] On cancellation/refund: `python3 auth_users.py disable buyer@example.com`.

## 5. Turn the login wall ON **[you]**
```
# .env
GESH_AUTH_ENABLED=1
GESH_AUTH_SECRET=<32+ random chars>     # e.g. python3 -c "import secrets;print(secrets.token_urlsafe(48))"
```
- [ ] Restart :9000. Public routes (`/pricing`, `/track-record`, `/terms`, `/disclaimer`)
      stay open; everything else now requires a session. **Test before announcing.**

## 6. Flip to Phase 1 (webhook auto-provision) — when ready
The webhook handler (`billing.py`), routes (`/api/billing/webhook`, `/auth/magic`), and
schema migration are already shipped + tested. To activate:
- [ ] Lemon Squeezy → Settings → **Webhooks** → add `https://<your-domain>/api/billing/webhook`,
      subscribe to subscription + order events, copy the **signing secret**.
- [ ] Set env and restart:
  ```
  BILLING_WEBHOOK_SECRET=<signing secret>
  LS_VARIANT_OBSERVER=<variant id>   LS_VARIANT_TRADER=<id>   LS_VARIANT_DESK=<id>
  RESEND_API_KEY=<resend key>        EMAIL_FROM="GESH <access@your-domain>"
  APP_BASE_URL=https://<your-domain>
  ```
- [ ] Test with the Lemon Squeezy webhook "Send test" (or a real test-mode purchase):
      a subscriber row should appear (`auth_users.py list`) **active** with the right tier,
      and the magic-link email should arrive. Cancel → user goes **disabled** on next request.
- [ ] Once verified end-to-end, stop manual provisioning — it's automatic now.

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
