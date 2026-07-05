# GESH — Review Handoff (4th pass)

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

- The dashboard sparkline endpoint returned `{"series":[37080.88, 39121.44, …]}`
  — a **bare number array under an un-listed key**, so the key-name masker skipped
  it and a customer saw the owner's real equity curve. This is the **4th instance
  of one class** (Codex found `ecg.equity_history`; this is the same bug shape).
- Fixed by adding `series` to `_MONEY_LIST_KEYS`; customer now sees the curve
  scaled ×2.665 onto the $100K book.

### The structural fix — a live leak sweep (`d21dab9`)

Rather than keep chasing keys, `tests/test_customer_leak_sweep.py` proves the
invariant on **every customer-reachable GET**: mint a real customer session,
fetch the endpoint, and assert **no known owner value** (portfolio total
`$37,522.83`, cash `$509.70`, share counts, each equity-series number) appears
anywhere in the raw body. 24 endpoints + history-detail. **This sweep would have
caught both the ECG and the sparkline leaks.**

Proof it has teeth (not passing vacuously): the same owner total `$37,522.83`
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

- **Test account (customer/observer):** `codex-review@geshfinance.com`
  (revoke after review: `python3 scripts/comp_user.py revoke codex-review@geshfinance.com`).
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
