# GESH — Customer-Readiness Review (for external code review)

_Prepared 2026-07-03 for a fresh Codex/adversarial pass **before** any customer
tests the product. Covers everything shipped after the first Codex review's
`REQUEST CHANGES` verdict. Commit range: `eb16b24..08d8b76` on `main` (16
commits), all pushed, 401 tests passing, verified in a live browser session._

## Context

GESH is a live, single-tenant AI research subscription (FastAPI + SQLite +
Anthropic). It went live on Lemon Squeezy 2026-07-03 ($49/mo, real-card
round-trip verified). The product model: **customers view the owner's research
on a masked $100K "model book"; they never connect their own brokerage.** The
first Codex review returned **REQUEST CHANGES** on two P0s (customer-data
leakage, XSS/key storage). This document is the response, for re-review.

## Prior Codex findings → resolution

### P0 — Customer masking leaked the real portfolio → FIXED (A1)
- **Was:** generic key-name scaling missed `current_equity`, `median`, `var_95`,
  Monte-Carlo bucket labels, and every dollar in AI prose ($36,970 visible to a
  customer).
- **Now:** money-key set extended with all distribution-outcome fields, plus
  `_scale_prose()` scales **every `$`-amount and "N shares"** inside every string
  of a masked response. Fail-closed: unknown real total → dollars zero; masking
  exception → 500, never a raw-dollar fallthrough.
- **Verified live (customer session):** MC median $102,676 on the $100K book;
  zero real-dollar matches across `/api/monte-carlo/cached`, `/api/history`,
  `/api/consensus/cached`. `commit 116a201`.

### P0 — Stored-XSS → trading-API-key theft → FIXED (B1 + B2)
- **B1:** `GESH_API_KEY` removed from `localStorage` entirely; the browser no
  longer stores or prompts for it. Authorization now rides the **HttpOnly owner
  session cookie** (`require_api_key` accepts an owner session OR the `X-API-Key`
  header for scripts, and **rejects authenticated customers with 403**). Applied
  to `/api/paper/run`, `/api/paper/check-closes`, and all 13 live-run analysis
  POSTs (which spend the owner's LLM budget + overwrite the shared engine cache).
  Verified: customer POST → 403, owner passes with only the cookie. `commit e95c3a7`.
- **B2:** security-headers middleware — CSP with `connect-src/img-src/script-src`
  pinned to self (blocks XSS exfiltration + external-script even with inline
  handlers), `X-Frame-Options DENY`, `nosniff`, `Referrer-Policy`,
  `Permissions-Policy`, HSTS over HTTPS. 9 untrusted AI/news/SEC innerHTML sinks
  escaped with `daEsc()`. Chart.js pinned with SRI. `commit de848d4`.

### P1 items → resolved
- **Customers could mutate shared state / spend** → owner-only gate above (B1).
- **ECG 503'd into a forever-spinner** → snapshot fallback + explicit degraded
  state (`_resolved_payload`), never 503. `commit e128108`.
- **`view` disclosure dropped by the second loader** → single shared
  `applyViewDisclosure()`. `commit 7bca678`.
- **`gesh_auth.secret` not gitignored** → added. **`/watch` internal cockpit
  visible to customers** → owner-only 403 + hidden link (`commit eb16b24`).

## Found & fixed during a live browser walk (driving the customer session)

These were NOT in the original review — caught by loading the real customer
session in Playwright and inspecting console + DOM:

1. **CRITICAL — the Signals tab (core 11-node consensus + Kelly) was dead for
   customers.** `renderConsensus`/`renderKelly` expect summary fields that only a
   *live* portfolio run produces; the cached daily-chain shape is per-symbol only,
   so `.length` threw and an **empty `catch{}` swallowed it silently**, leaving a
   "Click Run Consensus" placeholder (a button hidden for customers). Fixed by
   deriving every summary field from the holdings; catches now log. `commit 9ee4a22`.
2. **CSP blocked Google Fonts** (self-inflicted in B2) — whole dashboard in
   fallback fonts. CSP now allows the fonts hosts. Console: 0 errors. `commit e33030b`.
3. **`/api/regime` 503'd** when its 4h cache went stale — same class as ECG;
   now serves stale cache as a degraded snapshot. `commit e33030b`.
4. **Radar badge falsely claimed "100+ Stocks · 20 Best Picks"** vs actual "10
   screened · 6 picks" — copy now count-agnostic. `commit e33030b`.

## Polish (Phase E)

- **E1** KPI value clipping + mobile 390px (single-col, `clamp()` values,
  wrapping header). Verified at 390px + 1440px. `commit c8870e8`.
- **E2** CPI switched to `CPIAUCNS` (BLS-headline basis, was `CPIAUCSL`) +
  auditable label "CPI YoY X% as of YYYY-MM (FRED CPIAUCNS)". `commit d6acabb`.
- **E3** AI regime-narrative consistency directive (no "entire portfolio ranging"
  then naming trending names). `commit 9f48075`.
- **E4** Model/API cost report (`docs/MODEL_API_COST_REPORT.md`). `commit 08d8b76`.

## Regression coverage added

Customer-isolation + authz + masking are now tested (`tests/test_customer_view.py`,
24 cases): owner-vs-customer masking per endpoint, MC + prose masking, `/watch`
+ `/report`/`/invest`/`/progress` owner-only 403, mutating/spend endpoints
customer-403 + owner-pass + fail-closed, security headers, ECG degraded fallback.
Full suite: **401 passing.**

## Known-but-legitimate (NOT bugs, verified)

- **ECG SUSPENDED/COOLING states** are real drawdown signals — the code derives
  streaks from the equity series (not `percent_change`); that old false-suspend
  bug was already fixed. IONQ "SUSPENDED −20.3%" is a genuine signal.

## Deferred (not launch-blocking; documented for the reviewer)

- **Phase D — user-editable portfolios** (1–20 positions, kill the `reauth.py`
  hard-dep, paper-first). The main dashboard is still owner-book-centric (masked);
  letting customers analyze their *own* tickers is a planned expansion, not yet
  built. `GET /api/consensus/{symbol}` already runs the engine on any ticker.
- Intelligence agents (SEC/news/fundamental) have never populated cached data →
  honest "not yet published" empty state; running them is owner-only + slow.
- Tunnel reliability: cloudflared drops under the corporate-VPN DNS block; a
  local-`/ready` watchdog mitigates (`commit ed8341b`), but the real fix is a VPN
  split-tunnel exception for `argotunnel.com` (machine config).

## Requested review focus

1. Any remaining path where a customer session can see a real dollar amount or
   share count (masking completeness — is scaling arbitrary JSON + prose sound,
   or should responses be built from model-book inputs per-endpoint?).
2. The owner-session authz model on mutating/spend endpoints (`require_api_key`).
3. CSP strength vs the `'unsafe-inline'` needed for the inline handlers.
4. The cached-vs-live data-shape mismatch class (Signals bug) — other renderers
   that assume live-only fields?
