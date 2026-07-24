# Remediation — 2026-07-23 · product + penetration audit (P0/P1)

Working document for the remediation of the two authorized audits of
`pay.geshfinance.com` (customer session `finance-review@gesh.app`, role
`customer`, view `model_book`, tier `desk`).

**Status:** all P0 exit criteria met; P1 complete except the items listed under
*Not fixed*. A post-review **hardening pass** follows this document (see the
bottom section) and supersedes §1 B's mechanism and §6's numbers.

**Suite (verified, both configurations):**
`pytest -q --ignore=tests/test_ledger_host.py` → **1,637 passed, 0 failed**;
`PYTHONPATH=../gesh-core/src pytest -q` (nothing excluded) → **1,643 passed,
0 failed**. The excluded module is an environment-drift collection error on
unrelated in-flight branch work — fully diagnosed in hardening §H5.

---

## 0. Method

Every finding was reproduced with a failing automated test **before** any code
was changed, and each root cause was traced to the responsible line rather than
inferred from the audit's hypothesis. Two of the audit's stated causes were
wrong, and are corrected below (§1 A, §1 H).

No production data was modified. No migration was required — see §7.

---

## 1. Root causes

### A. Holdings quantity divergence (`P0`, audit A / Q-01) — **FIXED**

*Observed:* submit 1 share → `/api/mybook` returns 1, `/api/account/export`
returns 2.7971. Submit 2.7971 → export returns 7.8239.

*Audit hypothesis:* "multiple holdings tables / quantity-vs-notional confusion /
a migration factor." **All wrong.** There is exactly one store
(`customer_positions`), one writer (`auth_users.set_position`) and two readers
(`get_positions`, `export_user_data`) that select the same column.

*Actual cause:* `server.py::_customer_view_middleware`. Every `/api/*` JSON
response to a NON-owner session is normalized to a $100k "model book" by
`_scale_money`, which multiplies every key in `_QTY_KEYS` (`shares`, `quantity`,
…) by `_MODEL_BOOK_USD / owner_real_total`. `/api/mybook` is listed in
`_MASK_EXEMPT_PREFIXES`; **`/api/account/export` was not.** So the customer's own
GDPR/CCPA portability export was multiplied by the *owner's* scale factor.

The factor is exactly reproducible from the snapshot on disk:

```
100000 / 35750.87 = 2.7971347…      ← the audit's 2.7971, to 4 dp
2.7971 × 2.7971    = 7.8238…        ← the audit's 7.8239
```

`avg_cost` is not a masked key, so cost basis stayed raw while quantity moved —
the export did not even reconcile with itself.

*Fix:* `/api/account` added to `_MASK_EXEMPT_PREFIXES`. The leak sweep's own
`EXCLUDED` table already documented this endpoint as "mask-EXEMPT by design" —
the intent existed, the implementation did not match it.

> Note the rule this satisfies: nothing divides by 2.7971. The scaling is not
> corrected downstream, it is not applied in the first place.

### B. Customer / owner surface mixing (`P0`, audit A-01 / A-02 / F-03) — **FIXED**

*Cause 1 — the operator console was delivered, then hidden with CSS.*
`templates/dashboard.html` marked operator blocks `.owner-only` and hid them with
`body.customer .owner-only{display:none}`. The markup — Portfolio Value / Buying
Power KPIs, Equity-Curve Gate, Scanner, Paper Trade Log, Risk-Gate dry-run,
Bull/Bear debate, Daily Analysis, and the `/watch` · `/invest` · `/usage` links
("Per-customer LLM spend") — reached every customer and was one devtools toggle
away. Several of its buttons called endpoints that 404 for a customer, which is
the tell that the widget never belonged in that document.

*Fix (superseded — see hardening §H2, which demoted this to a secondary net and
made role-scoped template composition the mechanism):*
`server.py::_strip_owner_only_html` removes those subtrees **server-side**
before the document is sent. It is an explicit tag scanner, not a regex sweep,
because the template contains JS template literals that themselves build
`owner-only` markup — a regex would cut into a backtick string and take the whole
customer app down. `<script>`/`<style>` bodies are copied verbatim; an element
whose matching close cannot be found is left alone (an unstripped element is a
bug, a truncated document is an outage). The nine owner tab panes were marked
`owner-only`, and the customer's landing pane is promoted to `active` in the same
pass since `tab-overview` carried it.

Measured: owner document 311,753 B → customer 276,787 B (88.8% retained), all
script/style tags balanced, customer surface (`tab-home`, `tab-analyze`,
`mybook-nav`, Model Research/Signals/Model Risk) intact.

*Cause 2 — `GET /api/config` had no auth dependency at all* while `POST
/api/config` had `require_api_key`. Customers could read allocation percentages,
per-trade caps, max open positions/contracts, cash buffer, scanner parameters and
the `auto_trade` / `paper_mode` kill switches.

*Fix:* the read now uses the same owner dependency as the write.

*Product decision — RESOLVED in the hardening pass (§H1):* customers now receive
a **published model portfolio** built by a field allowlist, never the owner
brokerage payload. The paragraph below records the position taken before that
change and is retained for the audit trail.

The `$100k model book` served from
`/api/portfolio` (owner positions scaled, stamped `view: "model_book"`) is an
intentional, documented product surface — customers pay to see the owner's
research. This remediation did not remove it; it made it **reconcile** (§D) and
stopped the operator *tooling* around it from shipping. If the business wants the
owner's positions off customer responses entirely, that is a product call and a
one-line change (`/api/portfolio` → `_OWNER_ONLY_PATHS`) — flagged, not taken.

### C. Day change computed from cost basis (`P0`, audit Q-02) — **FIXED**

*Cause:* `templates/dashboard.html::loadMyBook`

```js
const avgPctChg = pos.reduce((s,p)=>s+(p.percent_change||0),0)/pos.length;
const dayChg    = pos.reduce((s,p)=>s+(p.equity||0)*((p.percent_change||0)/100), 0);
//                                              ↑ LIFETIME return vs average cost
```

labelled `Day:`. `percent_change` is `(price − avg_cost)/avg_cost × 100`. So the
dollar figure was Σ(equity × lifetime return) — not a day change, not a P&L, not
any quantity with meaning — and the percentage beside it was the *unweighted mean*
of per-position lifetime returns, mathematically unrelated to the dollar figure.
Hence "+$271,639 (5920.4%)" with the two mutually inconsistent.

*Fix:* new module `portfolio_valuation.py` is now the single definition of every
money number, in `Decimal`. `day_change = total_equity − prior_close_equity`,
always. Prior closes come from `price_cache.prior_close()`, read out of the
1-month frame `_latest_close_meta` already downloads — no extra network. The
browser no longer computes it; it renders `data.valuation`.

### D. Model book did not reconcile (`P0`, audit Q-03) — **FIXED**

*Cause:* the snapshot's broker-reported `total` covers sleeves the payload does
not itemize:

```
portfolio_equity 26,052.67 + cash 8,948.15 = 35,000.82
broker `total`                              = 35,750.87   (+750.05 unmodelled)
```

`_customer_scale_factor` divided the $100k model book by the **broker total**, so
the headline landed on exactly $100,000 by construction while the components
landed at 35,000.82 × 2.7971 = **$97,902** — the audit's figure to the cent, and
the $2,098 gap is $750.05 × 2.7971.

*Fix:* two-sided. `_reconcile_equity_summary` derives `total`/`account_equity`
from positions + cash and preserves the broker figure as `reported_total` with
`reported_total_delta` and a `reconciles` flag, so the un-modelled sleeve is
visible rather than absorbed. `_scale_factor_from_summary` divides by the same
derived total, so holdings + cash equals the headline for *any* snapshot.

### E. Corrupt valuation could feed the engines (`P0`) — **FIXED**

`_assert_valuation_sound` gates the mybook analysis job. Kelly sizing,
Monte-Carlo VaR and the AI narrative all take equity and weights as ground truth;
running them on a non-reconciling book produces confident, precise, wrong advice.
The invariant failure is structured-logged without any dollar figure (the alert
must not become the leak) and raises `ValuationIntegrityError`.

### F. Chart range ignored (`P1`, audit F-02) — **FIXED**

Two silent causes: (1) the handler had **no `range` parameter** — FastAPI drops
unknown query params, so `?range=1D` was discarded and the `period="1mo"` default
served; (2) `period_map.get(period, "1mo")` fell back to a month for any
unrecognized value, *including the correct-looking uppercase `1D`/`1Y` labels the
UI itself uses*. Together: every window returned the same 21-point month.

*Fix:* `_CHART_RANGES` maps both spellings case-insensitively, adds `ytd`/`5y`/
`max`, and an unsupported window is a typed **400** — answering a different
question than the one asked is a correctness bug, not leniency. The response now
carries `requested_range`, `resolved_period`, `resolved_interval`, `point_count`,
`first_timestamp`, `last_timestamp`, `source`, `data_as_of` so a client can
*verify* it got the window it asked for.

### G. Add-position form concatenation (`P1`, audit F-01) — **FIXED**

*Cause:* `mybookAdd()` cleared the inputs **only on success** (`if(r.ok)`). A
rejected entry stayed in the fields and the next entry was typed onto the end of
it → `{"symbol":"NVDAMSFT","shares":-1003}`.

*Fix:* inputs are reset in a `finally`, success or failure; a `parseQty` strict
parser (no sign, no exponent, no whitespace) replaces bare `Number()`; validation
errors are shown in a new `#mybook-form-error`; the button is disabled in-flight.
Quantities render through `fmtQty` so a `1e-8` position no longer displays as raw
scientific notation. **Scientific notation is not supported in either direction** —
decided and now enforced consistently.

### H. "Duplicate analysis storm" (`P1`, audit H) — **PARTIALLY FIXED / re-diagnosed**

*The audit's reading is not supported by the code.* `/api/mybook/analyze`
implements a documented **202 + poll** contract: the client re-POSTs the same
endpoint every 3s (`attempt < 110`, a 5.5-minute budget sized to outlast the
Cloudflare tunnel's ~100s limit). ~15 POSTs over a 45-second run is the contract
working, not duplicate submission. Server-side, a unique index
(`ux_mybook_jobs_active_user`) already makes a second concurrent job impossible.

*Real defect found:* there was no in-flight guard, so a second click on any of
the four entry points ("Model Research", "Model Risk", "Signals", Run) started a
**second poll loop** against the same job — doubling request volume for no
benefit. Fixed with `_mybookAnalyzeInFlight`.

### I. CSRF / content-type (`P1`) — **FIXED**

The cookie is HttpOnly + `SameSite=Lax`, which already withholds it from
cross-site POSTs — so this was defense in depth, not an open hole. But the API
*accepted* `Content-Type: text/plain`, which is precisely what makes a
cross-origin POST a CORS "simple request" with no preflight; that left the API one
SameSite regression away from exploitable.

*Fix:* `_csrf_guard_middleware` — a mutation carrying a body must declare
`application/json` (else **415**), and a mutation whose `Origin`/`Referer` names
another host is refused (**403**). A *present-but-unparseable* Origin is a
refusal, not an absence. Absent Origin is allowed: browsers always send it on
cross-origin mutations, while legitimate headless callers send none and
authenticate with `X-API-Key`. The Lemon Squeezy webhook is exempt (HMAC-
authenticated, cross-origin by design). `GESH_ALLOWED_ORIGINS` covers proxies
that rewrite Host.

### J. Fabricated values for unknown tickers (`P1`, audit BUG-05) — **FIXED**

yfinance returns a fully-shaped row for symbols it has never heard of:
`recommendation: "Hold"`, `num_analysts: 0`, every number null; and
`company_name` echoing the ticker back with all metrics null. Both rendered as
real cards with a `yfinance · just now` source label.

*Fix:* `_analyst_row_is_empty` (coverage requires ≥1 analyst **or** ≥1 price
target) and `_fundamentals_row_is_empty` (structural: any field that is not pure
request-echo and carries a value counts as knowledge — deliberately *not* a
name allow-list, which rots and starts 404-ing real companies). No coverage is a
**404**, not a Hold.

*Env-var leak:* `earnings_viz.get_earnings` returned the customer-facing string
`"no earnings source (set FINNHUB_API_KEY for VPN-safe data)"`. Operational detail
moved to a `diagnostic` key which `/api/earnings/{symbol}` **pops and logs**, never
ships.

### K. Verdict contradiction (`P2`, audit BUG-03) — **FIXED**

New `verdict_reconciliation.py`: pure, rule-based, no LLM/network, so the same
inputs always produce the same stance and every branch is testable. Combines the
mechanical signal, valuation view, AI thesis, risk gate and regime into ONE
stance (`ACCUMULATE` / `WATCH` / `HOLD` / `REDUCE` / `NO_STANCE`) plus the
evidence table. **Insufficient data is `NO_STANCE`, never a silent `HOLD`.** The
risk gate can only ever tighten. Confidence is derived from node agreement,
**halved on conflict**, and its basis is stated — the audited case had a
60%-confidence STRONG BUY beside an 80%-confidence HOLD, i.e. a number carrying
no information. Surfaced by `buildStancePanel` above the AI card; when layers
disagree, both readings stay visible and the disagreement is stated.

### L. Data freshness contradictions (`P2`, audit BUG-07) — **FIXED**

`_attach_freshness` emits one `data_freshness` block (`state`: live / cached /
stale / unknown, `label`, `data_as_of`, `age_seconds`,
`max_expected_age_seconds`, `source`, `is_live`) and **derives** the legacy
`offline` / `stale_snapshot` booleans from it, so the combination the audit found
(`offline:true` + `stale_snapshot:false` + "LIVE MARKET DATA") is now
unrepresentable for any input.

### M. Track-record framing (`P2`, audit Q-05) — **FIXED**

The page led with a blended "+22.2% vs SPY +0.9%" cumulative curve while the
autonomy-gated ENGINE cohort — the strategy a subscriber buys — was −7.7% (PF
0.65, 12% win rate), carried by two large manual HOOD trades a subscriber could
not reproduce.

*Fix:* a headline block leads with the **engine cohort** return, sample size, win
rate and the SPY benchmark, and names the blended figure and its manual-trade
count in the same breath. The equity curve is relabelled ALL SOURCES BLENDED. **No
historical data was altered** — only which number a reader meets first, and that
neither can be read without its scope.

### N. Raw JSON error bodies (`P2`, audit BUG-08) — **FIXED**

A `StarletteHTTPException` handler renders an HTML page for requests that *ask*
for HTML (`Accept: text/html`, non-`/api/` path) and keeps the exact JSON contract
for everyone else. 5xx `detail` is never rendered (it can carry internal context).

---

## 2. Files changed

| File | Change |
|---|---|
| `portfolio_valuation.py` | **new** — canonical Decimal valuation + invariants |
| `verdict_reconciliation.py` | **new** — pure stance reconciliation |
| `server.py` | mask exemption, owner-only HTML stripper, `/api/config` gate, canonical book payload, equity reconciliation, freshness block, chart ranges, CSRF middleware, unknown-symbol guards, error-page handler, valuation integrity gate |
| `templates/dashboard.html` | server-strippable owner panes, canonical day change, form reset/parse/format, analyze in-flight guard, stance panel |
| `price_cache.py` | `prior_close()` + `_prior_close_from_series()` + batch warm |
| `proof_page.py` | engine-cohort headline, blended curve relabelled |
| `earnings_viz.py` | customer copy / operator `diagnostic` split |
| `tests/test_customer_leak_sweep.py` | `/api/config` COVERED → EXCLUDED (with reason) |
| `tests/test_earnings_viz.py` | asserts the leak is gone, diagnostic retained |

## 3. Tests added (12 files, 146 tests)

`test_holdings_integrity` · `test_portfolio_valuation` ·
`test_owner_surface_isolation` · `test_day_change_contract` ·
`test_model_book_reconciliation` · `test_chart_range_contract` ·
`test_csrf_hardening` · `test_unknown_symbol_honesty` · `test_error_pages` ·
`test_verdict_reconciliation` · `test_track_record_presentation` ·
`test_data_freshness`

Each reproduces its audit finding as a failing test first. Regression guards use
an **arbitrary** scale factor (3.0 / 7.5), never the observed 2.7971, so they
prove "no scaling at all" rather than "not that one scaling".

## 4. API contract changes

| Endpoint | Before | After |
|---|---|---|
| `GET /api/account/export` | masked (quantities × owner factor) | raw, caller-scoped |
| `GET /api/config` | 200 to anyone | **403** for non-owner |
| `GET /api/chart/{sym}` | `range` ignored; unknown `period` → 1mo | `range` honoured; unknown → **400**; + verification metadata |
| `GET /api/mybook` | — | + `valuation` block; `percent_change`/`equity_change` may be `null` |
| `GET /api/portfolio` | `total` = broker figure | `total` derived; + `reported_total`, `reported_total_delta`, `reconciles`, `data_freshness`, `day_change_available` |
| `GET /api/analyst/{sym}` | 200 with fabricated `Hold` | **404** when no coverage |
| `GET /api/fundamentals/{sym}` | 200 with empty card | **404** when no data |
| `POST /api/analyze-ticker` | — | + `reconciliation` block |
| mutating `/api/*` | any content-type | **415** on non-JSON body; **403** cross-origin |

**Breaking for clients that treat `day_change`/`percent_change` as always-numeric:**
they are now `null` when unavailable. This is intentional — a 0.0 that means "we
don't know" is the defect. `day_change_available` / `cost_basis_available` are the
flags to branch on.

## 5. Security changes

- `/api/account/*` no longer transformed by owner-book masking (correctness *and*
  privacy: an export must be a faithful copy).
- Owner operations console removed server-side, not hidden with CSS.
- `GET /api/config` owner-gated.
- CSRF: content-type allowlist + Origin/Referer validation on all `/api/*` mutations.
- Internal env-var names removed from customer copy; provider diagnostics log-only.
- 5xx detail never rendered into an error page.
- New masked keys enrolled (`reported_total`, `reported_total_delta`, and the
  valuation block) — **the existing leak sweep caught these passing through raw
  the moment they were added**, which is the guard working as designed.

## 6. Test results (superseded by hardening §H5)

```
1551 passed, 16 warnings          (--ignore=tests/test_ledger_host.py)
ruff: 14 errors in server.py — identical count at HEAD; zero introduced
```

### Known unrelated breakage

`tests/test_ledger_host.py` fails to **collect**: `ledger_host.py` imports
`gesh_core.ledger`, which is not installed in this venv. Both files are untracked
work-in-progress from the current branch (`phase4b-ledger-production-wiring`) and
predate this session. Not touched.

## 7. Migration & rollback

**No migration.** The storage layer was never wrong — `customer_positions` holds
exactly what users submitted. The corruption was a presentation-layer transform
on one endpoint. Nothing needs converting, and a mass-conversion based on the
2.7971 observation would have *created* the corruption it was meant to fix.

**Rollback:** every change is code-only and revertible per-file. The highest-risk
item is the HTML stripper; `git revert` of the `server.py` hunk plus the nine
`owner-only` pane markers in `dashboard.html` restores the prior behavior exactly
(CSS hiding still present and untouched).

**DB backup before any local run against real data:**
```bash
cp -p gesh_auth.db gesh_auth.db.bak_$(date +%Y%m%d_%H%M%S)
cp -p gesh_financial.db gesh_financial.db.bak_$(date +%Y%m%d_%H%M%S)
```

## 8. Manual verification

1. `launchctl kickstart -k gui/$(id -u)/com.gesh.main`
2. As a **customer**: view-source on `/` → no `owner-only`, no `s-equity`,
   no `/watch|/invest|/usage`; `GET /api/config` → 403.
3. As a **customer**: add a position, then `GET /api/account/export` → the same
   share count you typed.
4. Enter `NVDA / -100` → rejected; then `MSFT / 3` → payload is `MSFT / 3`.
5. `GET /api/chart/NVDA?range=1D` vs `?range=1y` → different `point_count`,
   `first_timestamp`, `resolved_interval`.
6. `GET /api/analyst/ZZZQQ` → 404, not "Hold".
7. `curl -X POST .../api/mybook -H 'Content-Type: text/plain'` → 415.
8. As **owner**: dashboard unchanged, all tabs present.

## 9. Not fixed / deferred

| Item | Why |
|---|---|
| RSI mismatch (Momentum 76.2 vs Regime 64.5) | Different lookbacks by design; needs a product decision on which to display, not a code fix. |
| Intraday `1D`/`5D` bars require an Alpaca key | Pre-existing infrastructure gap; the endpoint now reports `intraday_unavailable` honestly. |
| Tooltips / glossary for every metric | UX work, explicitly out of scope for a defect remediation. |
| Contrast / a11y / mobile pass | Deferred from both audit rounds; needs a dedicated pass. |
| `/api/portfolio` serving owner positions at all | Product decision — see §1 B. |
| News-headline indirect prompt injection | Surface identified; no controllable test harness. |
| Cookie `SameSite`/`Secure` in production | Verified in code (`Lax` + per-request `Secure`); needs confirmation against the live tunnel. |

---

# Hardening pass (same day, post-review)

## H1. Published model-portfolio boundary

`model_portfolio.py` — a **build-up allowlist** serializer. `/api/portfolio`
returns it to any non-owner session; the owner brokerage payload never leaves
for a customer, transformed or otherwise.

Published: `model_portfolio_id`, content-hash `version`, `published_at`,
`data_as_of`, `holdings` (symbol · quantity · current_price · equity ·
portfolio_percentage · type), `cash`, `positions_market_value`,
`calculated_total_equity`, `source_label`, `methodology`, `stale`, `freshness`,
day-change block.

Excluded **by construction**: brokerage/account identifiers, quote provenance
(which carried per-quote `session` ids and the `robinhood_mcp` provider tag),
buying power / deployable / margin / withdrawable / pending deposits, average
cost, unrealised P&L, ECG + drawdown, risk-gate and paper state.

`test_a_new_owner_field_cannot_leak` is the load-bearing test: a field added to
the owner payload tomorrow cannot reach a customer, because it was never named.
`publish()` does **not** scale — the masking middleware stays the single scaler,
asserted by `test_dollars_are_scaled_exactly_once`.

**Remaining work (required follow-up):** a durable, versioned publication
pipeline with its own storage and an editorial publish step. Today `version` is
a content hash of the live composition, which is stable and meaningful but is
not an editorial act.

## H2. HTML stripping demoted to a secondary net

The operator console now lives in `templates/owner_console.html` — 18 blocks,
spliced into `<!--GESH_SLOT:name-->` markers by `server._render_dashboard(role)`
**for an owner session only**. The customer document is *assembled* without it.

* `test_customer_render_never_opens_the_partial` — rendering for a customer
  fails the test if the owner partial is even read.
* `test_stripping_is_now_a_no_op_for_the_customer_render` — the shell contains
  no owner-only markup at all, so the retained `_strip_owner_only_html` has
  nothing left to do. If it ever does, someone put operator UI back in the
  shell; fix that rather than lean on the strip.
* Widget coverage: Open Positions/KPI strip, Risk-Gate, Scanner config, Paper
  Trade Log, Bull/Bear, Daily Analysis, LLM-spend link, plus every owner pane id
  and owner endpoint reference, asserted absent from the delivered DOM.

**Honest limit, asserted not hidden:** operator JS *function bodies* still ship
in the shared `<script>` bundle. No control invokes them (`handlers == 0` for
every operator action) and every endpoint they name is authorization-gated, so
this is internal-route-path disclosure, not privilege or data leakage.
`test_known_residue_is_tracked_not_hidden` pins the current residue so the
follow-up — extracting operator JS into its own slot-loaded bundle — can be
verified as a reduction to zero. **Required follow-up.**

## H3. Reconciliation, all five branches

`_reconcile_equity_summary` now always serves the calculated total and records
the broker figure as diagnostic only:

| broker total | reconciles | `valuation_source_state` |
|---|---|---|
| greater than components (the audited +$750.05) | false | `degraded` |
| lower than components | false | `degraded` |
| exact | true | `calculated` |
| within rounding tolerance | true | `calculated` |
| absent | true | `calculated` |

Variance is logged as a **percentage only** — an alert about a dollar leak must
not become one (`test_variance_is_logged_without_dollar_figures`). An unpriced
position is carried with a null price and never contributes an invented 0.

## H4. Cookie + CSRF, as verified in code

| Attribute | Value |
|---|---|
| Name | `gesh_session` |
| HttpOnly | yes, unconditional at both `set_cookie` sites |
| SameSite | `Lax` — the primary CSRF defense; the middleware is depth |
| Secure | per-request `_cookie_secure()`: forced on for a non-localhost Host when `APP_BASE_URL` is https (a bypassed proxy cannot downgrade), else derived from `X-Forwarded-Proto`; `GESH_COOKIE_SECURE` overrides |
| Path | `/` |
| Domain | **not set** — host-only, never sent to a sibling subdomain |
| Max-Age | `auth_users.TOKEN_TTL_SECONDS` |

Mutation requirements, each tested: `application/json` for any body (415
otherwise) · same-origin `Origin`/`Referer`, with a present-but-unparseable
value refused rather than treated as absent (403) · absent Origin allowed, and
that decision documented as deliberate (browsers always send it cross-origin;
headless clients authenticate with `X-API-Key`) · session required (401) · role
scope enforced independently (customer → 403 on `/api/config`) · tenant scope
holds under a valid request. No `/api/*` route uses form encoding, so the JSON
rule breaks no existing flow; `/login` is outside the guard's `/api/` scope.

**No CSRF token** is implemented. With `SameSite=Lax` + Origin validation +
content-type allowlist, a token would add little; if the cookie ever moves to
`SameSite=None`, a token becomes mandatory.

## H5. Test-suite claim, precisely

```bash
# A — venv as installed, one module excluded
./venv/bin/python -m pytest -q --ignore=tests/test_ledger_host.py
#   → 1637 passed, 16 warnings

# B — with the pinned gesh-core on the path, NOTHING excluded
PYTHONPATH=../gesh-core/src ./venv/bin/python -m pytest -q
#   → 1643 passed, 16 warnings
```

`tests/test_ledger_host.py` fails **collection** in configuration A:

```
ModuleNotFoundError: No module named 'gesh_core.ledger'
  ledger_host.py:15  from gesh_core.ledger.adapters.daily_analysis import ...
```

**Diagnosis — environment drift, not code.** `requirements.txt` pins
`gesh-core @ v0.6.0`; the venv has **0.5.2** installed, which predates the
`ledger` subpackage. The sibling checkout `../gesh-core` is at 0.6.0 and *has*
it. Proven without mutating the venv: configuration B collects and passes all 6
of its tests.

**Pre-existing and untouched by this remediation.** `ledger_host.py` and its
test are untracked work-in-progress on `phase4b-ledger-production-wiring`; this
remediation imports nothing from `gesh_core.ledger` and changes none of its
dependencies.

**Safe fix, not applied here:** `./venv/bin/pip install -r requirements.txt`
(or `pip install -e ../gesh-core`). Not run because it upgrades `gesh_core.db`,
`kelly`, `ecg` and `trading` from 0.5.2 → 0.6.0 for the whole suite mid-review,
and changing the shared environment is the maintainer's call.

**Release risk:** if CI installs from `requirements.txt` it will get 0.6.0 and
the suite is fully green; if CI reuses a cached 0.5.2 venv it will hit this
collection error on a module unrelated to this branch. Verify the CI venv is
rebuilt from the pin before merge.

## H6. Follow-ups created by this pass

1. Durable versioned publication pipeline for the model portfolio (H1).
2. Extract operator JS into a slot-loaded bundle; then delete
   `test_known_residue_is_tracked_not_hidden` (H2).
3. Rebuild the CI/dev venv from the `gesh-core` pin (H5).

---

# Consolidated hardening pass (reviewer work order)

Run in a clean sibling worktree (`../gesh-project-audit-hardening`) from the
committed remediation HEAD. The original dirty worktree was never touched.

## Environment — `requirements.txt` is NOT installable from clean

`python3 -m venv` + `pip install -r requirements.txt` on Python 3.14.6 fails:

```
error: resolution-too-deep     (pip, twice — also with --only-binary=:all:)
```

`uv` diagnoses it precisely:

```
you require yfinance==1.2.1 and openbb-yfinance>=1.6.0;
openbb-yfinance>=1.6.3 depends on yfinance>=1.4.0  ->  unsatisfiable
```

`openbb-yfinance` is floor-pinned (`>=1.6.0`) and now resolves to 1.6.3, which
requires a yfinance the file pins away. **A fresh CI install fails today.** The
original venv predates the drift (it holds 1.6.2) and its own freeze is also
internally inconsistent (`openbb-core==1.6.8` wants `uvicorn<0.41`, `uvicorn==0.44.0`
installed).

Clean alternative used, and named as such: the original venv's exact `pip freeze`
installed with `--no-deps` into `.venv-review` — fully pinned, isolated, and
reproducible, but **not** proof that `requirements.txt` installs. **RELEASE RISK.**

Environment: Python 3.14.6 · gesh-core 0.5.2 (the committed pin is v0.5.3; the
v0.6.0 bump lives only in the dirty worktree with the ledger work) · pytest 9.0.3.

## Baseline and final counts (tracked files only, nothing excluded)

| | command | collected | passed | failed | skipped |
|---|---|---|---|---|---|
| baseline `09f757c` | `pytest -q` | 1637 | 1637 | 0 | 0 |
| final | `pytest -q` | 1937 | 1937 | 0 | 0 |

The earlier 1551/1559/1629/1643 figures were provisional: they were measured in
the dirty worktree where `tests/test_ledger_host.py` (untracked, +6) and its
`gesh_core.ledger` dependency were sometimes present. **1637 is the true
tracked-only baseline**, and no module is excluded from either run.

## Reviewer findings

| Finding | Verdict |
|---|---|
| Raw owner payload reaches customer ANALYSIS routes | **Rejected** — every analysis route (`/api/analyze`, `/api/deep-analysis`, `/api/kelly`, `/api/monte-carlo`, `/api/consensus`, `/api/screener`, trading) returns 403 to a customer. Route-level evidence in `test_customer_cannot_reach_the_analysis_engines_at_all`. |
| Ten customer GETs consume the raw owner payload | **Confirmed, fixed** — but as defence in depth: a negative control (boundary reverted) showed none of the ten ECHOED a sentinel. Real second-order defect fixed: they computed on the broker's unreconciled total. |
| CSRF: charset rejected | **Rejected** — the media type was already split on `;`. Now explicitly tested. |
| CSRF: absent Origin allowed | **Confirmed, fixed** — cookie-auth now requires it; API-key callers exempt. |
| CSRF: Host header self-authorising | **Confirmed, fixed** — trust now comes from `APP_BASE_URL`/`GESH_ALLOWED_ORIGINS`. |
| CSRF: scheme/port not compared | **Confirmed, fixed** — canonical `scheme://host:port`. |
| Owner JS in customer bundle | **Confirmed, fixed** — 26 functions + palette actions moved; residue zero. |
| Duplicate analysis jobs possible | **Rejected** — `BEGIN IMMEDIATE` + partial unique index already guarantee one active job. No new machinery; proof added. |
| `content_version` not canonical | **Confirmed, fixed**. |
| `/api/portfolio` contract break in the frontend | **Confirmed, fixed** — `renderStats(d.equity_summary)` threw for every customer. |
| `reconciles` hardcoded true in `publish()` | **Partially fixed** — true *by construction* there (the total IS positions + cash); the computed invariant lives in `portfolio_valuation` and `_reconcile_equity_summary`, which emits `valuation_source_state: degraded`. |
| Unknown-symbol honesty across all engines | **Partially fixed** — analyst/fundamentals/earnings corrected earlier; consensus/FFT/momentum/levels not individually re-audited this pass. Remaining risk. |

## Cookie configuration (verified in code, not from memory)

`gesh_session` · HttpOnly always · SameSite=Lax · Secure per-request
(`_cookie_secure`: forced on for a non-localhost Host when `APP_BASE_URL` is
https, else derived from `X-Forwarded-Proto`; `GESH_COOKIE_SECURE` overrides) ·
Path `/` · **no Domain** (host-only) · Max-Age `TOKEN_TTL_SECONDS`.
No CSRF token: with SameSite=Lax + mandatory Origin + content-type allowlist one
adds nothing. It becomes mandatory if the cookie ever moves to `SameSite=None`.

## Remaining risks

1. **`requirements.txt` does not install from clean** (above). Highest-priority
   follow-up; blocks reproducible CI.
2. Unknown-symbol honesty not re-audited for consensus / FFT / momentum / levels.
3. Durable versioned publication pipeline for the model portfolio still absent;
   `version` is a content fingerprint.
4. `server.py` remains a ~4,900-line monolith; commits cannot be split by concern
   without interactive staging.

---

# Final closure pass — three release gates (2026-07-24)

Clean sibling worktree `../gesh-project-audit-hardening`, branch
`fix/audit-p0-p1-final`. The original dirty worktree was never touched.

## GATE 1 — reproducible dependency installation

**Supported runtime:** Python **3.12** (CI `python-version: "3.12"`; ruff
`target-version = "py311"` is the floor). pyproject is deliberately
non-packaging, so the runtime is declared via a new `.python-version` (3.12)
rather than a `[project] requires-python`.

**Root cause (two parts):**
1. `openbb-yfinance>=1.6.0` (unbounded floor) resolved to 1.6.3, which requires
   `yfinance>=1.4.0`, against the pinned `yfinance==1.2.1`. Only
   openbb-yfinance==1.6.0 is compatible with 1.2.1.
2. `openbb>=4.3.0` (meta-package) is IRRECONCILABLE with the stack: every
   openbb-core requires uvicorn <0.25 or <0.41, the app pins uvicorn==0.44.0
   for fastapi 0.135.3. openbb was only ever installed `--no-deps`, its import
   is guarded (yfinance fallback), and CI already stripped it.

**Fix (smallest safe change — every server pin and gesh-core v0.5.3 untouched):**
- `requirements.txt`: removed the two openbb lines. lxml (the only transitive
  openbb provided, for the FINRA scrape) was already declared explicitly.
- `requirements-openbb.txt` (new): the optional integration, `openbb-yfinance`
  pinned to 1.6.0, documented as not co-installable with the pinned uvicorn.
  openbb is relocated, not deleted — matching how the code and CI already treat it.
- `requirements.lock` (new): fully-resolved `pip freeze` from the clean 3.12
  install; gesh-core pinned to the v0.5.3 commit sha.
- CI: install `requirements.txt` directly + `pip check` in both jobs.
- `tests/test_dependency_sanity.py`: offline guards against regressing the graph.

**Clean proof (NO `--no-deps`, NO PYTHONPATH, no sibling checkout):**
```
python3.12 -m venv .venv-clean && . .venv-clean/bin/activate
pip install -r requirements.txt      # builds gesh-core 0.5.3 from the git tag
pip check                            # → No broken requirements found.
pytest --collect-only -q             # → 1982 tests collected
pytest -q                            # → 1982 passed
```
Private-repo note: gesh-core is a private git dependency; CI needs `GESH_CORE_PAT`
(already wired in the workflow). It resolved here via the workstation's git
credentials — not a PYTHONPATH override.

## GATE 2 — unified unknown / unavailable-symbol semantics

Re-audited all 11 per-symbol engines against a valid-but-nonexistent ticker.
**No engine fabricates** (no fake Hold/RSI/levels/Falling — verified). The defect
was contract inconsistency: 7 compute-engines each returned a different
unavailable shape wrapped in `status:"ok"` with a fabricated `as_of`.

Unified to two explicit shapes: **404** (analyst, fundamentals, chart) and
**`{"status":"unavailable", reason_code, symbol, data_as_of:null, source:null}`**
(consensus, regime, momentum, fft-cycles, earnings, levels, news). `reason_code`
distinguishes `symbol_not_found` (no market identity) from `data_unavailable`
(real symbol, provider down). News is gated on price, not emptiness. A genuinely
neutral result for a real symbol (CASE C) flows through untouched as `status:ok`.

**Endpoint × case matrix** (`tests/test_unknown_symbol_matrix.py`, 24 cases):

| engine | CASE A unknown | CASE B provider down | CASE C neutral |
|---|---|---|---|
| analyst / fundamentals / chart | 404 | (n/a) | real data |
| consensus | unavailable · symbol_not_found | — | HOLD passes as ok |
| regime | unavailable · symbol_not_found | — | RANGING+RSI passes as ok |
| momentum / fft / levels | unavailable · symbol_not_found | data_unavailable | real data |
| earnings | unavailable · symbol_not_found | data_unavailable | real data |
| news | unavailable · symbol_not_found | — | empty list passes as ok |

## GATE 3 — computed reconciliation contract

`model_portfolio.publish()` hardcoded `reconciles: True`. MODEL A: it now runs
the SAME holdings through the canonical valuation and copies a computed verdict.

**Invariant matrix** (`tests/test_reconciliation_contract.py`):

| source condition | reconciles | valuation_status | degraded_reason |
|---|---|---|---|
| exact | true | reconciled | null |
| broker total ≠ components (valid) | true | reconciled | null (delta = source degradation, server-side) |
| missing current price (None / 0) | false | degraded | missing_price |
| non-finite price / cash / equity | false | unavailable | non_finite_value |
| non-numeric composition (garbage) | false | unavailable | non_finite_value (no 500) |
| cash-only | true | reconciled | null |

Downstream is gated: the mybook analysis refuses a non-reconciling book via
`_assert_valuation_sound` (reads the canonical `portfolio_valuation.reconciles`).
Response carries `valuation_status` + a customer-safe `degraded_reason`;
diagnostics stay in logs.

## Final validation (clean 3.12 env)

```
pytest --collect-only -q  → 1982 collected
pytest -q                 → 1982 passed, 0 failed
pip check                 → No broken requirements found.
git diff --check          → clean
ruff check tests/         → All checks passed
ruff check server.py      → 14 pre-existing (identical to c2f7866; 0 introduced)
```

Commits (three, focused): `20c1f3b` build (deps) · `f28dcce` fix(symbols) ·
`b2793fe` fix(valuation).

## Remaining risks

1. gesh-core is a private git dependency — CI requires `GESH_CORE_PAT`.
2. openbb integration (`requirements-openbb.txt`) remains not co-installable with
   the pinned uvicorn; the OpenBB code path only runs in an isolated env. The app
   falls back to yfinance (the default, tested).
3. Durable versioned publication pipeline for the model portfolio still absent;
   `version` is a content fingerprint (documented as such).
4. `server.py` remains a ~5,000-line monolith.
