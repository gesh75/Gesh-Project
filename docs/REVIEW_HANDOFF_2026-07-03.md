# GESH — Review Handoff (4th → 8th pass)

## 🚦 GO / NO-GO — Founder-Invitation Checklist (8th pass · 2026-07-05)

> **STATUS: NO-GO until PRs #6–#10 are MERGED and the :9000 service is RESTARTED.**
> The 8th adversarial pass (Fable) found a **live P0 customer-data leak** — already
> fixed on a branch but **not yet on `main`**. Inviting before merge+restart would
> expose the owner's real per-position P&L and account equity to paying customers.

| # | Gate | State | Blocks invite? |
|---|------|-------|----------------|
| 1 | **P0 leak** — `equity_change` + `extended_hours_equity` masked on `/api/portfolio` | Fixed in **[#6]** (CI green), **not merged** | **YES — merge + restart** |
| 2 | P1 — trade P&L (`realized_pnl`/`total_realized_pnl`) masked on `/api/trades*` | **[#6]** | latent today (owner is paper-only) |
| 3 | P2 — public insider `shares` no longer over-scaled | **[#6]** | correctness, recommended |
| 4 | P2 — `/track-record` DB strings HTML-escaped | **[#7]** (green) | recommended (public page) |
| 5 | P3 — `/api/portfolio/history` no longer leaks `str(e)` | **[#8]** (green) | recommended |
| 6 | P3 — billing revoke advances watermark (no resurrection) | **[#9]** (green) | recommended |
| 7 | E2E lifecycle proof — pay → magic-link → masked dashboard → cancel → lockout | **[#10]** (green) | — |
| 8 | Both CI jobs (`test` + `customer-isolation`) green on every PR | ✅ | — |
| 9 | Stale 2026-07-03 analysis regenerated | Auto on **Mon 2026-07-06 10:30 ET** sanctioned run | wait for it — do not hand-run |
| 10 | `customer-isolation` a REQUIRED GitHub check | needs GitHub Pro — **advisory**, pre-push hook covers solo alpha | deferred (owner's call) |

**Path to GO:** (1) merge **#6 first** (it closes the live P0), then #7–#10;
(2) restart the service so it drops its in-memory old code:
`launchctl bootout gui/$(id -u)/com.gesh.main && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gesh.main.plist`;
(3) confirm Monday's daily run regenerated the analysis (report ≥60 KB, fresh DB row);
(4) **then** send the founder invites (`docs/FOUNDER_OUTREACH.md`).

[#6]: https://github.com/gesh75/Gesh-Project/pull/6
[#7]: https://github.com/gesh75/Gesh-Project/pull/7
[#8]: https://github.com/gesh75/Gesh-Project/pull/8
[#9]: https://github.com/gesh75/Gesh-Project/pull/9
[#10]: https://github.com/gesh75/Gesh-Project/pull/10

---

_Prepared 2026-07-03 for a fresh Codex/Gemini adversarial pass. Responds to the
**3rd-pass `REQUEST CHANGES`** verdict and covers everything shipped since.
Commit range: `28061e5..d21dab9` on `main` (4 commits), all pushed. **431 tests
passing** (was 405 at the 3rd review; +26, incl. a new live leak sweep)._

---

## TL;DR for the reviewer

The 3rd pass flagged two launch blockers. Both are fixed and, more importantly,
the **whole leak class is now covered by a live integration sweep** instead of
key-by-key patching:

1. **P0-1 (AI price forecasts corrupted by masking)** → fixed with a
   **semantic-path** policy: mask by dict KEY/path, never by string SHAPE.
2. **P0-3 (unescaped AI string sinks / XSS)** → every remaining model-generated
   sink escaped + class-name allowlists.
3. **New, self-found (Fable audit): a 4th instance of the same leak class** —
   `/api/portfolio/history` sparkline `series` leaked raw owner equity. Fixed,
   and a **defense-in-depth sweep** now proves the invariant on every endpoint.

Everything below is the detail + what I want you to try to break.

---

## Prior 3rd-pass findings → resolution

### P0-1 — AI per-share price forecasts were being scaled → FIXED (`a7b04e4`)

- **Was:** `_PURE_MONEY_RE` scaled *every* `$`-amount in AI prose by the model-book
  factor (≈2.665). A per-share forecast `prediction.3m = "$180–210"` became
  `"$480–560"` — corrupt, and misleading to a customer. Root cause: a per-share
  **price range** and a portfolio-dollar **bucket label** are *shape-identical*
  (`$X–$Y`), so no regex on the string shape can tell them apart.
- **Now:** **semantic-path masking** — decide by the field's KEY/path, not its text:
  - **SCALE** only genuine model-dollar fields: money dict-keys, money-list arrays
    (`equity_history`, `equity_series`, `equity_curve`, `series`), and
    `monte_carlo.buckets[].label`.
  - **KEEP raw** per-share fields: `3m`, `12m`, `prediction`, `fair_value`,
    `target`, `estimate`, `current`, `price`, `forecast`, `analyst_target`.
  - **REDACT** any other `$`-amount / "N shares" prose → `$•••` / "a position"
    (fail-safe: if we can't prove a string is a per-share price, we don't emit a
    number at all).
- **Verified:** a customer sees the NVDA 12m forecast in real per-share dollars,
  while portfolio totals read on the $100K book. `tests/test_customer_view.py`
  asserts predictions stay raw and MC buckets scale.

### P0-3 — Unescaped AI/external string sinks → FIXED (`a7b04e4`)

- Escaped every remaining model-generated sink B2 missed: fundamental thesis,
  `company_type`, radar+screener `verdict`, `portfolio_type`/`portfolio_verdict`,
  `sector(s)`, `sector_etf`, SEC `form_type`/`period`, `symbols`.
- Added `saCls()` / `priCls()` **allowlists** so an AI-supplied assessment/priority
  string can't break out of a `class="..."` attribute (joining the existing
  `verdictCls`/`sevCls`/`sentCls`/`verdictRadarCls` allowlists).
- Adversarial `daEsc` test added.

---

## Self-found since the 3rd pass (not in your review)

### P0 — `/api/portfolio/history` sparkline leaked raw owner equity → FIXED (`61ea22a`)

- The dashboard sparkline endpoint returned `{"series":[<redacted>, <redacted>, …]}`
  — a **bare number array under an un-listed key**, so the key-name masker skipped
  it and a customer saw the owner's real equity curve. This is the **4th instance
  of one class** (Codex found `ecg.equity_history`; this is the same bug shape).
- Fixed by adding `series` to `_MONEY_LIST_KEYS`; customer now sees the curve
  scaled ×2.665 onto the $100K book.

### The structural fix — a live leak sweep (`d21dab9`)

Rather than keep chasing keys, `tests/test_customer_leak_sweep.py` proves the
invariant on **every customer-reachable GET**: mint a real customer session,
fetch the endpoint, and assert **no known owner value** (portfolio total, cash,
share counts, or any equity-series number) appears
anywhere in the raw body. 24 endpoints + history-detail. **This sweep would have
caught both the ECG and the sparkline leaks.**

Proof it has teeth (not passing vacuously): the owner total
masks to **exactly `$100,000.00`** in the customer's `/api/portfolio`, and zero
raw sentinels appear. Skips cleanly when the server is down so the unit suite
stays hermetic.

---

## What I want you to try to break

1. **Masking completeness.** Is there any customer-reachable path — GET or the
   error branch of a POST — that can surface a real dollar amount or share count?
   The sweep covers the ~24 GETs I enumerated; if you can name an endpoint or a
   nested field it misses, that's the finding I most want.
2. **The semantic-path policy itself.** Is the KEEP/SCALE/REDACT split in
   `_scale_money` / `_redact_prose` sound, or can you construct a real response
   where a per-share field carries a portfolio-dollar value (or vice-versa) and
   the path-based rule does the wrong thing?
3. **Authz on mutating/spend endpoints.** `require_api_key` accepts an owner
   session cookie OR `X-API-Key`, and rejects an authenticated customer with 403.
   Applied to `/api/paper/*` and the 13 live-run POSTs. Any bypass?
4. **XSS.** Any remaining innerHTML sink fed by AI/news/SEC/external data that
   isn't run through `daEsc()`/`_esc()` or a class allowlist?

---

## Known-open (disclosed, not blockers — tell me if you disagree)

- **CSP still carries `'unsafe-inline'`** for the inline event handlers in
  `dashboard.html`. `connect-src`/`script-src` are pinned to self + jsdelivr
  (blocks exfiltration + external script load), but a nonce-based CSP is the real
  hardening. Tracked, not yet done. *Is `'unsafe-inline'` + self-pinned
  `connect-src` an acceptable interim, given exfiltration is already blocked?*
- **Stale cached AI narrative (P2).** The cached `ai_result` still shows the
  pre-E2/E3 copy (CPI 4.3%, "entire portfolio ranging"). It regenerates on the
  next clean daily run (10:30 ET) or a manual trigger — code is fixed, cache is
  stale. Not a security issue.
- **Architecture question (the honest one).** The masking is generic-middleware
  post-processing of arbitrary JSON. You argued twice this is "a losing game" vs
  per-endpoint customer DTOs. I agree it's the right end state (Phase D: build
  each customer response from model-book inputs, not scale the owner response).
  The sweep test is my interim safety net until that refactor. **Do you consider
  the sweep + semantic-path policy sufficient to onboard ~5 comped alpha users,
  or is the DTO refactor a hard pre-req?** This is the decision I want your call on.

---

## How to reproduce

- **Test account (customer/observer):** use a temporary review customer account
  and revoke it after review with `python3 scripts/comp_user.py revoke <email>`.
- **Run the sweep:** `pytest tests/test_customer_leak_sweep.py -v` (needs the live
  server on :9000 with the owner snapshot loaded; skips otherwise).
- **Full suite:** `./venv/bin/python -m pytest tests/ -q` → 431 passing.
- **Live:** log in via magic link as the test account → masked $100K model-book
  dashboard; the owner never exposes real dollars to a customer session.

---

## 4th-pass resolutions (commit `50f27ec`, 2026-07-04)

Responding to the 4th-pass `REQUEST CHANGES`. All code items landed; **446 tests
pass** and CI is green on **two** jobs (`test` + a new `customer-isolation` gate).

- **P0 — remaining XSS sinks → FIXED.** `daEsc()` now wraps every model/external
  string that reached `innerHTML` raw: prediction 3m/12m badges
  (`dashboard.html:1490`), SEC `action_signal`/`urgency`, news
  `overall_sentiment`/`market_mood`, fundamental momentum-gap/horizon/
  `insider_activity`, kelly/trade `action`, gate `reason`, ECG/error messages,
  cmdk labels. The classifiers (`…Cls`/`…Style`) are confirmed allowlists (fixed
  class/style output), so `class=`/`style=` attrs were already safe.
- **P0 — renderer-wiring test → ADDED.** `tests/test_dashboard_xss_wiring.py`
  parses the real template and FAILS if any known AI/external field is
  interpolated without `daEsc`/`_esc` or an allowlist classifier — it verifies the
  sinks *call* the escaper, not just that a Python reimplementation works. Has a
  synthetic-sink teeth test.
- **P1 — leak sweep is now a CI gate.** `tests/test_customer_leak_sweep.py`
  rewritten to run **in-process (Starlette TestClient) against a seeded
  owner-sentinel fixture** — no port 9000, no network, **no skips**. Runs in the
  new required `customer-isolation` job. Per endpoint it asserts `status==200`
  (a 401/403/404/5xx **fails** — never a pass), `view=="model_book"` (proves the
  response routed through masking), and no exact sentinel. Wrong-auth → 401 is
  asserted as failure. Verified teeth: an identity-scale run leaks
  `37522.83`/`509.7`/`101.99` and the sweep fails.
- **P1 — coverage from `app.routes`.** `test_all_customer_gets_categorized`
  derives the route set from `server.app.routes`; a new customer GET that nobody
  categorizes fails the test. Added the **10 routes** the review found missing
  (fft-cycles/{sym}, gemini/status, config, scanner/consolidation|sector/{sym},
  benchmarks/{period}, crypto-correlation, security/health,
  volatility/compare/{sym}). `/api/ollama/status` is `EXCLUDED` (410 Gone).
- **P1 — exact structured sentinels.** No rounding: the sweep + a direct
  `_scale_money` test assert the synthetic leaks Codex injected — fractional
  quantity `6.078929`, per-lot equity `101.988616`, historical-series `5843.32`
  — all scale away.
- **Nit — gesh-core wheel version.** Bumped `0.4.0 → 0.5.1` and re-tagged; the
  pinned wheel now self-reports `0.5.1`.
- **Stale customer analysis.** Engine fix (`CPIAUCNS`) is already in `regime.py`;
  the cached copy refreshes on the next sanctioned daily run (Mon 10:30 ET)
  before any invitation — not force-regenerated on the July-4 holiday.

**CI note (your correction):** acknowledged — #69 was the first green since **#33**
(not ever); #33 ran 334 tests.

### Still open (disclosed)
- CSP still carries `'unsafe-inline'` for the inline handlers; `connect-src`/
  `script-src` are self-pinned (exfiltration blocked). Nonce-CSP tracked, not done.
- Per-endpoint customer DTOs remain the agreed prerequisite for **wider paid
  rollout** (not the ~5-user comped alpha).

---

## 5th-pass resolutions (commit `69de15b`, 2026-07-04)

Responding to the 5th-pass `REQUEST CHANGES` (XSS still P0; guard had false
negatives). 446 tests pass; both CI jobs green.

- **P0 — raw AI/external sinks → FIXED.** The prior XSS guard used a field-name
  allowlist and so had **confirmed false negatives** (missed `recommendation`,
  `screen_summary`, `market_bias`, aliases `p3`/`p12`, map var `s`). Replaced with
  a **deny-by-default** static guard: it parses every `${…}` in HTML-bearing
  template literals (JS pulled from `<script>`, regex/comment/string-aware,
  nesting-aware) and treats each as a sink **unless provably safe** — `daEsc`/
  `_esc`, an allowlist classifier, a numeric/format expression, a pure-literal
  ternary, a **function call** (its body is walked separately, so an internal raw
  sink like `volBadge`'s was caught and fixed), an **HTML-valued local** (its
  construction leaves are walked separately), or a **numeric-used property**. No
  field names anywhere — an omitted field or alias can't slip through. It also has
  a teeth test and a synthetic-sink test. **112** previously-raw interpolations
  were escaped at their exact positions (every symbol/source/state/regime render,
  the analyst + radar `recommendation`, `top_sectors`, `screen_summary`,
  `market_bias`, gate/scan strings, error/toast messages, `volBadge` tier). `node
  --check` confirms the JS still parses.
- **P1 — guard false negatives → FIXED** by the deny-by-default rewrite above
  (that was the root of the finding).
- **P1 — `customer-isolation` not *required*.** Correct — GitHub rulesets need Pro
  on a private repo, so it can't be a required check on this plan. Mitigation:
  a **pre-push hook** (`scripts/hooks/pre-push`, installed via
  `scripts/install-git-hooks.sh`) runs the isolation + XSS gate locally and blocks
  the push on failure; `--no-verify` bypass is emergency-only. Upgrading to GitHub
  Pro to mark the check required is the durable fix (documented, your call).
- **P2 — runtime version → FIXED.** `gesh_core.__version__` bumped `0.1.0 → 0.5.2`
  to match wheel metadata; pin advanced to `v0.5.2`.
- **Stale analysis** regenerates on the next sanctioned daily run (Mon 10:30 ET)
  before any invitation — not force-run on the holiday.

### Requested focus for re-review
1. The deny-by-default guard's soundness: are the "safe" reductions (function
   call → body-audited, HTML-local → construction-audited, numeric-used property)
   valid, or can you construct a raw sink the guard rules safe?
2. Any customer-reachable render still unescaped that the guard missed.

---

## 6th-pass resolutions (commit `9857f72`, 2026-07-04)

The 5th-pass guard had real bypasses (good catch). Fixed both the sinks and the
guard's over-broad exemptions. 448 tests pass; both CI jobs green.

- **P0 — raw sinks → FIXED.** `friendlyErr(e)` (returns the raw `e.message` for
  non-403 errors) is now `daEsc(friendlyErr(e))` at all 5 call sites; the consensus
  symbol lists are `d.strong_buys.map(daEsc).join(', ')` / `d.sells.map(daEsc)…`;
  the `buildBars`/da-score numeric `${v}` renders are escaped too (`node --check`
  passes).
- **P1 — guard now genuinely deny-by-default.** The four bypasses are closed:
  1. **Arbitrary function calls** are no longer trusted — the call-site exemption
     is restricted to `_SAFE_CALL_NAME` (escapers/formatters/classifiers + a small,
     explicitly-audited list of HTML builders whose *own template bodies the guard
     walks*). `friendlyErr` is deliberately excluded (it `return`s raw data, not a
     template).
  2. **`x || 0`** no longer implies numeric — `A||B` is safe iff *both* operands
     are; genuinely-numeric props stay covered by numeric-*usage* inference.
  3. **`.map(cb).join()`** is callback-aware: safe only if `cb` is `daEsc`/`_esc`
     or returns a template / safe expr. Bare `arr.join(', ')` and `.map(s=>s)` are
     flagged.
  4. **generic `html`** removed from `_SAFE_IDENTS` — it stays safe only via the
     principled HTML-valued-local rule, and I verified there is no raw `html = …`
     assignment (all are template/empty-built).
- **Negative tests** now lock all four bypasses shut (`friendlyErr(e)`,
  `anyHelper(x.data)`, `x.foo||0`, `d.strong_buys.join(', ')`, `.map(s=>s).join`,
  a bare identifier → all asserted **False**; `.map(daEsc).join`, audited builders,
  `daEsc(friendlyErr(e))` → asserted **True**).

Noted (your standing decisions): the pre-push hook is local/bypassable — GitHub
Pro to mark `customer-isolation` required is the durable fix; the stale July-3
analysis regenerates on Monday's sanctioned run before invitations.

---

## 7th-pass resolutions (commit `cea2275`, 2026-07-04)

An adversarial security-agent pass verdict'd today's page as clean/shippable but
found **two latent HIGH soundness gaps** — unsound guard *rules* (not live sinks)
that would silently pass the next field added. Both closed; 451 tests green, both
CI jobs pass.

- **Gap 1 — `_NUM_PROPS` global-by-name (relocated `||0` bypass) → FIXED.** It was
  a set of bare prop *names*, so a numeric `d.revenue_growth` vouched for a string
  `x.revenue_growth` everywhere; and one signal was `.p||<number>` — the exact
  unsoundness the 6th pass removed from `_is_safe`. Now scoped to **(object, prop)
  pairs**, and the `||number` signal is dropped. The residual check matches
  terminal `obj.prop` pairs against the scoped set.
- **Gap 2 — `_HTML_LOCALS` trusted a name on `any()` template assignment → FIXED.**
  `r` was trusted because *one* assignment was a template, despite ~20 raw
  `r = await fetch(...)` assignments. Removed `_HTML_LOCALS` entirely; HTML-valued
  locals now go through the sound safe-set, which requires **all** assignments safe
  (a template blanks to `''` with leaves walked; a raw fetch does not). The two
  genuinely-HTML but **scope-shadowed** locals (`recs`/`sectors`) were renamed
  `recsHtml`/`sectorsHtml` so the name no longer collides with a raw-array
  assignment elsewhere.
- **Gap 3 — `depth > 40` now fails CLOSED** (was fail-open).
- Fixed `_ends_with_call` to select the `.map()`/`.join()` whose paren closes at
  the end (it had picked an inner `.map` on `secItems`).
- Escaped the display-number renders that lost their now-sound numeric proof.
- **Negative tests** lock all three gaps: object-scoped numeric props
  (`x.revenue_growth` flagged even when `d.revenue_growth` is numeric; `a.count||0`
  not trusted); all-assignments-safe (the `r = fetch()` shape rejected);
  depth-fail-closed.

---

## 8th-pass resolutions — final adversarial audit (Fable, 2026-07-05)

A full multi-agent adversarial audit of the **entire customer-facing surface** (6
lenses — XSS, auth/isolation, payment/webhook, financial-correctness, PII/secret
leak, error-handling — each finding adversarially verified against the live code).
10 raw findings; after verification, **6 real issues across 5 logical units**, each
shipped with a failing-first negative test. PRs **#6–#10**, both CI jobs green.

### Found + fixed

| Sev | Finding | Fix (PR) |
|-----|---------|----------|
| **P0** (live) | `/api/portfolio` leaked `equity_change` (per-position owner $ P&L) + `extended_hours_equity` (account equity) — keys absent from `_MONEY_KEYS`, so the recursive masker passed them **raw** to customers. Same leak class as the ECG-array / sparkline-`series` leaks. | `#6` — added the keys; **hardened the leak sweep** (its mock used the covered name `pnl_usd` while production emits `equity_change`, so it was structurally blind) to seed the REAL production keys. |
| **P1** (latent) | `/api/trades` + `/api/trades/stats` leaked `realized_pnl` / `total_realized_pnl` (+ nested best/worst_trade). Latent only because the owner runs paper-only. | `#6` — added to `_MONEY_KEYS`; sweep now seeds an owner trade row. |
| **P2** (correctness) | `/api/market-data` **over-scaled** public insider `shares` by the model-book factor (key `shares` masks owner sizing in `fetch_live_data`/`paper_trading`, but under `insider_trades` it is public data) → customers saw corrupt insider counts. | `#6` — `_PUBLIC_RAW_KEYS = {"insider_trades"}` kept verbatim. |
| **P2** (XSS) | Public `/track-record` (`proof_page.py`) interpolated DB `symbol`/`source`/`outcome`/timestamps into HTML with **no escaping** — outside the dashboard guard; CSP has `'unsafe-inline'`. Low exploitability (engine-written values) but a real stored-XSS surface. | `#7` — `html.escape` on every DB-sourced string sink. |
| **P3** (leak) | `/api/portfolio/history` (customer-reachable) returned `{"error": str(e)}` → a DB failure leaked the sqlite path / "database disk image is malformed" / schema (masker only redacts `$`-prose). | `#8` — log server-side, return generic `"unavailable"`. |
| **P3** (integrity) | `revoke()` used `set_billing(event_at=event_at or None)`; a **timestamp-less** `subscription_expired` left `billing_event_at` unadvanced, so a **reordered** `subscription_updated(active, variant)` (tier ≠ None → skips the H6 guard) passed `_is_stale` and **resurrected** a revoked account. HMAC-gated (no forgery). | `#9` — advance the watermark on revoke (event ts, else received-at now). |

### Reviewed and intentionally kept (not leaks)
- `/api/config` `str(e)` — **owner-gated** (`require_api_key`); intentional validation feedback to the trusted owner.
- `RobinhoodOfflineError` → 503 — a **typed, non-secret** message.
- Billing-webhook 400/503 `str(e)` — returns to an **HMAC-authenticated** Lemon Squeezy (bad signature 401s *before* any `str(e)`); server-to-server, not a browser.
- XSS guard `_MAP_LOCALS` / `_SAFE_CALL_NAME` probed again — no new bypass; the 7th-pass soundness rules hold.

### Also added
- **E2E lifecycle integration test** (`#10`) — pay → magic-link → masked dashboard → replay-rejected → cancel → lockout, across the real code paths.

### Still open (disclosed — deferred, not launch blockers for the ~5-user alpha)
- **CSP `'unsafe-inline'`** on `script-src` (the dashboard uses inline handlers +
  one inline `<script>`); `connect-src`/`img-src`/`script-src` are self-pinned, so
  exfiltration is blocked. Nonce-CSP is the durable fix — tracked, not done.
- **Per-endpoint customer DTOs** — the allowlist-masking approach is sound and swept,
  but a positive-allowlist DTO per endpoint is the agreed prerequisite for **wider
  paid rollout** (not the comped alpha).
- **`customer-isolation` as a REQUIRED GitHub check** — needs GitHub Pro on a private
  repo (free tier can't require checks). The `scripts/hooks/pre-push` gate + green CI
  cover the solo alpha; adopt Pro when a collaborator/agent starts pushing. See
  `docs/BRANCH_PROTECTION.md` (one-command turn-key).
- **Backlog PR-4…7 (gesh-core)** — dispositioned 2026-07-05; none block invitations:
  - **PR-6 (coverage 76→80%): ALREADY MET.** gesh-core's own suite is at **81%**
    (`pytest --cov=gesh_core`; 197 tests). No CI coverage gate exists. Nothing to do.
  - **PR-7 (wash-sale ±30-day backward): data-blocked — do not ship partial.** The IRS
    rule is symmetric; the current `tax_harvester.get_wash_status` models only the
    **forward** window (post-harvest re-buy). The **backward** half needs lot-level
    **purchase dates**, but `/api/tax/harvest` passes only `{symbol, quantity, equity,
    average_buy_price}` — no acquisition date. A correct fix must first plumb recent
    BUY-order dates (Robinhood orders / `trade_log`) into `scan_tax_loss_candidates`;
    a version without that would **mis-state a tax-eligibility number** shown to a
    paying customer. Feature-sized, deferred (not a quick logic fix).
  - **PR-4 (batch `yf.download` + single-connection tax scan):** perf. `market_fetch`
    is one-symbol-per-call; the win requires the consensus-convergence refactor (callers
    batch), noted "future" in the module docstring. Deferred (perf-only, higher risk).
  - **PR-5 (resolver unification + python-dotenv):** infra cleanup. Deferred.

### Next owner
- **Merge order:** #6 first (live P0), then #7–#10; **restart `com.gesh.main`** after
  merge (it runs stale in-memory code). Then verify Monday's analysis regen, then invite.
- All 5 PRs are independent (the two touching `server.py` hit non-overlapping regions),
  mergeable in any order — but #6 is the one that must land before any invite.
