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

### ✅ Phase 1 RESULTS — validated 2026-06-30 (real LS test-mode webhooks)

Validated with a sanitized Lemon Squeezy test-mode account. Store, subscriber, variant,
subscription, and order identifiers are intentionally omitted.

| # | Event | Outcome | ✅ |
|---|-------|---------|----|
| 1 | `subscription_created` | provisioned `is_active=1 tier=observer`, Resend email sent (`emailed:true`) | ✅ |
| 2/3 | renewal / payment-failed | **fixed** in `13cee2f` — keep tier/access (see below) | ✅ |
| 4/5 | `subscription_cancelled` | `is_active=1`, `billing_status=cancelled` (access to period end) | ✅ |
| 6 | `subscription_expired` | `is_active=0`, `billing_status=expired` (revoked) | ✅ |
| 7 | duplicate delivery | deduped → `action=duplicate` | ✅ |
| 8 | email-down | dead-letter + `billing.py sweep-onboarding` recovers | ✅ |

**Live-test bug fixed (`13cee2f`):** `order_created` carries the variant in `first_order_item`
(not top-level attributes) and `subscription_payment_success` is an invoice with **no** variant
— both were refused as `unknown_variant`, so every monthly renewal would fail. `parse_event`
now reads `first_order_item.variant_id`; `_dispatch` keeps the account's existing tier on
activation events lacking a resolvable variant (never escalates — H3 holds). Test suite made
hermetic (clears ambient `LS_VARIANT_*`).

**Dashboard data (`c3142d1`):** the live robin_stocks session is dead (empty pickle; Robinhood
changed their auth, `reauth.py` can't complete it). `/api/portfolio` now falls back to the
MCP-built `live_portfolio_snapshot.json` (`data_fetcher.load_snapshot_payload`) so the
dashboard shows last-known data instead of blanking — finishing the MCP-first migration for
the live endpoint. Refresh the snapshot via the robinhood-trading MCP →
`build_snapshot_from_mcp.py` (deterministic cash math).

---

## Phase 2 — Go-live cutover (IN PROGRESS — real money)

### Go-live infrastructure — ✅ DONE (2026-06-30)
- [x] **Domain** `geshfinance.com` registered on Cloudflare (Registrar, at-cost, Active).
- [x] **Stable named tunnel** `gesh-pay` (replaces the ephemeral quick tunnel) →
      `https://pay.geshfinance.com/api/billing/webhook`. LaunchAgent `com.gesh.cloudflared`
      (KeepAlive, survives reboots); config `~/.cloudflared/config.yml`. The quick tunnel
      (`com.gesh.cloudflared-quick`) was booted out + plist `.disabled` so it can't
      self-heal-overwrite `APP_BASE_URL`. `.env APP_BASE_URL=https://pay.geshfinance.com`.
- [x] **Email from the domain**: `geshfinance.com` verified in Resend (DKIM + SPF
      auto-configured to Cloudflare DNS); `.env EMAIL_FROM="GESH <access@geshfinance.com>"`.
      Test onboarding email landed in the Gmail **inbox** (not spam).

### Remaining cutover steps — gated on LS store approval
- [ ] **LS store activation (KYC)** — identity/payout submitted, status **IN REVIEW**.
      Cannot accept live cards until Lemon Squeezy approves. *(Their reviewers; nothing to do.)*
- [ ] After approval: toggle LS to **Live mode**; create the live product → **new live variant
      id + checkout URL** (the #1 launch-week bug is mixing test/live ids).
- [ ] Register the **live** webhook at `https://pay.geshfinance.com/api/billing/webhook`; swap
      `.env` to the **live** `BILLING_WEBHOOK_SECRET`, `LS_VARIANT_OBSERVER`, `LS_CHECKOUT_PRIMARY`.
- [ ] **Real-card $49 smoke test** (refund immediately): provision → magic-link login →
      cancel/expire → revoke.
- [ ] Verify the checkout URL can be **disabled fast** if fulfillment breaks.
- [ ] Flip `launch_status.json gates.payment=true`. Monitor the first billing cycle in the
      LS dashboard (webhook 2xx rate, invoices).

### Webhook identity — do not rebind a paying customer (PRs #266 / #274)

`billing_sub_id` is authoritative. A second Lemon Squeezy checkout that uses a
victim's email must not mutate that account.

| Incoming event | Stored account | Handler result |
|---|---|---|
| Activation (`subscription_created` / provision path) whose `sub_id` ≠ stored, account still `is_active` and not already `cancelled` | keep current sub | `{action: ignored, reason: foreign_subscription}` — **ack 2xx**, do not 503 (LS would retry and later bind after a genuine cancel) |
| Mark-only (`subscription_cancelled`, `payment_failed`, pause) or `subscription_updated` whose `sub_id` ≠ stored | keep `billing_status` | `{action: ignored, reason: stale_subscription}` |
| `subscription_expired` / revoke | `revoke(..., event_sub_id=)` | only the **matching** sub revokes |
| Same-sub cancel, then a later distinct activation | cancelled or `is_active=0` | rebind allowed (cancel-then-rebuy) |

A `foreign_subscription` ack with no bind is success, not a missed webhook.
Pins: `tests/test_billing.py` (`foreign_subscription` / stale-mark cases).

---

## Phase 3 — Post-launch (later)

- [ ] Wire `/billing/portal` to LS's hosted `customer_portal` URL from the webhook payload
      (self-serve management, zero custom UI).
- [ ] Tier→feature gating — only once >1 tier is actually sold (today all tiers are
      functionally identical; the pricing page correctly sells one).
- [ ] Surface owed-onboarding count + webhook-failure rate as a glance metric.
