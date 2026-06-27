# Payment Go-Live Checklist (Lemon Squeezy)

Path from the shipped-but-unwired payment code to validated test-mode payments, then
(later, deliberately) to live money. Scope of the current session: **Phase 0–1 only —
configure + validate in TEST mode. No real charges.**

Sources: [LS Testing & Going Live](https://docs.lemonsqueezy.com/guides/developer-guide/testing-going-live) ·
[LS Webhook Simulation](https://docs.lemonsqueezy.com/guides/developer-guide/webhooks) ·
[LS subscription launch checklist](https://gist.github.com/qihaze123/e84d325435f7d70a03a38542cadd601e) ·
[Stripe Go-Live](https://stripe.com/docs/development/checklist).

Code state: `billing.py` + `auth_users.py` + `server.py:/api/billing/webhook` shipped and
tested (`tests/test_billing.py` 35/35). Reliability nets live: dead-letter Telegram alert +
15-min onboarding sweep (`com.gesh.onboarding-sweep`).

---

## Phase 0 — Prerequisites (test mode)

- [ ] **0.1 Activate LS store** (test mode by default). Create the subscription product +
      **one** alpha-tier variant. Record: test **checkout URL**, **variant id**, **signing secret**.
      Also create a **`daily`-interval** test product so renewal (`subscription_payment_*`)
      events can be triggered next-day (LS only simulates payment events after a real renewal).
- [ ] **0.2 Cloudflare Tunnel → `localhost:9000`:**
      ```bash
      brew install cloudflared
      cloudflared tunnel login
      cloudflared tunnel create gesh-pay
      cloudflared tunnel route dns gesh-pay pay.<yourdomain>
      # ~/.cloudflared/config.yml:
      #   tunnel: gesh-pay
      #   credentials-file: /Users/<you>/.cloudflared/<UUID>.json
      #   ingress:
      #     - hostname: pay.<yourdomain>
      #       service: http://localhost:9000
      #     - service: http_status:404
      launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gesh.cloudflared.plist
      ```
      Plist already created at `~/Library/LaunchAgents/com.gesh.cloudflared.plist`
      (KeepAlive, logs `/tmp/gesh_cloudflared.log`). Then set `gates.public_endpoint=true`
      in `launch_status.json`.
- [ ] **0.3 Register LS test webhook** → `https://pay.<yourdomain>/api/billing/webhook`.
      Subscribe to exactly: `subscription_created`, `subscription_updated`,
      `subscription_cancelled`, `subscription_expired`, `subscription_payment_success`,
      `subscription_payment_failed`, `order_created`.
- [ ] **0.4 Fill TEST-mode `.env`** (scaffold already appended): `BILLING_WEBHOOK_SECRET`,
      `LS_VARIANT_OBSERVER`, `LS_CHECKOUT_PRIMARY`, `APP_BASE_URL=https://pay.<yourdomain>`,
      `RESEND_API_KEY`, `EMAIL_FROM` (verified Resend sender — an unverified sender makes the
      onboarding email silently no-op). Restart the :9000 service after editing.
- [ ] **0.5 Local smoke test** (before involving LS at all):
      ```bash
      python3 server.py &                       # or via com.gesh.main
      python3 scripts/ls_webhook_smoketest.py            # expect: ✓ provision
      python3 scripts/ls_webhook_smoketest.py subscription_expired   # expect: ✓ revoke
      ```

**Exit:** LS dashboard test-ping returns `2xx` over the tunnel; smoke test prints `✓`.

---

## Phase 1 — Test-mode E2E validation (evidence matrix)

Drive each row via a real **test-card** purchase + the LS subscription side-panel
**"Simulate event"**. Capture the evidence column before checking the box.

| # | Trigger | Expected behavior | Evidence to capture |
|---|---------|-------------------|---------------------|
| 1 | `subscription_created` / `order_created` | provision + magic-link email | user `is_active=1`, `welcome_emailed_at` set, magic-link login works |
| 2 | `subscription_payment_success` (after daily renewal) | access extended, **no second email** | idempotent; one email only |
| 3 | `subscription_payment_failed` | **keep access**, mark `past_due` | NO lockout (H4 fix) |
| 4 | `subscription_updated` (cancel-at-period-end) | access retained, status recorded | user still active |
| 5 | `subscription_cancelled` | access runs to period end | active until expiry |
| 6 | `subscription_expired` | **revoke** | next request → 401/redirect |
| 7 | Duplicate replay (resend same delivery) | single side-effect | handler returns `duplicate`; no double-provision |
| 8 | Email provider down (unset `RESEND_API_KEY`, re-fire #1) | dead-letter alert + sweep recovers | Telegram fires; `python3 billing.py sweep-onboarding` delivers once key restored |

**Exit:** all 8 rows pass; LS dashboard shows the production endpoint consistently at `2xx`.
**STOP HERE this cycle.** Leave `launch_status.json gates.payment=false`.

---

## Phase 2 — Go-live cutover (LATER — separate, deliberate session; real money)

> Not in scope now. Listed so the path is known.

- [ ] Toggle LS store to **Live mode**; **"Copy to Live Mode"** the product → **new variant
      id + new checkout URL** (the #1 launch-week bug is mixing test/live ids).
- [ ] Swap `.env` to **live** signing secret, **live** `LS_VARIANT_*`, **live**
      `LS_CHECKOUT_PRIMARY`. Register the **live** webhook endpoint.
- [ ] **Real-card smoke test** (real money, immediate refund): provision → magic-link →
      login → cancel/expire → revoke.
- [ ] Verify the checkout URL can be **disabled fast** if fulfillment breaks.
- [ ] Flip `launch_status.json gates.payment=true`. Monitor the first billing cycle in the
      LS dashboard (webhook 2xx rate, invoices).

---

## Phase 3 — Post-launch (later)

- [ ] Wire `/billing/portal` to LS's hosted `customer_portal` URL from the webhook payload
      (self-serve management, zero custom UI).
- [ ] Tier→feature gating — only once >1 tier is actually sold (today all tiers are
      functionally identical; the pricing page correctly sells one).
- [ ] Surface owed-onboarding count + webhook-failure rate as a glance metric.
