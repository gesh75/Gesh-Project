# Security & Test Infrastructure

_The customer-isolation + XSS hardening built across the Codex/security review
arc (2026-07-03 → 07-04). This is the reference for what exists, how to run it,
and — most importantly — **how to extend it without re-opening a hole**._

The product model this protects: **customers view the owner's research on a masked
$100K "model book"; they never connect their own brokerage.** Two invariants must
hold forever: (1) no real owner dollar/share value reaches a customer response,
(2) no model- or externally-sourced string reaches `innerHTML` unescaped.

---

## 1. The gate at a glance

| Layer | File | What it enforces |
|-------|------|------------------|
| Masking middleware | `server.py` `_customer_view_middleware` / `_scale_money` | Every customer `/api/*` JSON dict is scaled to the model book and stamped `view:"model_book"`; fails **closed** (500) if scaling throws |
| Owner RH session fence (PR #258) | `price_source.unauthenticated_rh_fallback` + `_customer_view_middleware` | Authenticated customer requests (and the 202 mybook worker) set `_ALLOW_AUTHENTICATED_RH=False` so Yahoo 401 cannot restore the owner pickle. Consensus pool copies ContextVars. Pins: `tests/test_price_source_fallback.py`. Does **not** cover `market_data.rh_analyst` / `rh_fundamentals` (direct `robin_stocks` on an already-live session) |
| Owner-only authz | `server.py` `require_api_key` | Mutating/spend endpoints reject customer sessions (403) |
| Leak sweep (test) | `tests/test_customer_leak_sweep.py` | No owner sentinel appears in any customer-reachable GET; every GET is categorized |
| XSS guard (test) | `tests/test_dashboard_xss_wiring.py` | Deny-by-default: every `${…}` sink in `dashboard.html` must be escaped/proven-safe |
| Masking/authz (test) | `tests/test_customer_view.py` | `_scale_money`, prose redaction, owner-vs-customer, headers, degraded fallbacks |
| CI job | `.github/workflows/tests.yml` → `customer-isolation` | Runs the three test files above on every push/PR |
| Local hook | `scripts/hooks/pre-push` | Same gate before every push (advisory; bypassable) |
| Required check | `docs/BRANCH_PROTECTION.md` | Makes `customer-isolation` un-mergeable when red — **deferred** (needs GitHub Pro; pre-push hook covers the solo alpha, adopt Pro on traction/collaborators) |

Run the whole gate locally:

```bash
./venv/bin/python -m pytest tests/test_customer_leak_sweep.py \
  tests/test_dashboard_xss_wiring.py tests/test_customer_view.py -q
```

---

## 2. Customer-data leak sweep — `tests/test_customer_leak_sweep.py` (4 tests)

**Deterministic, in-process** (Starlette `TestClient`), no live server / network /
skips. It seeds a fixture with **known owner sentinels** (portfolio total, cash,
a fractional quantity `6.078929`, a per-lot equity `101.988616`, historical
equity-series values) and, for every customer-reachable `GET /api/*`:

- asserts `status == 200` — a `401/403/404/5xx` (incl. the fail-closed 500) **fails**, never counts as a pass;
- asserts the `view == "model_book"` marker (proves the response routed through masking);
- asserts **no exact sentinel** appears in the body.

Key tests: `test_all_customer_gets_categorized` derives the route list from
`server.app.routes` — **a new customer GET that nobody categorizes fails the
test**, so endpoints can't silently bypass the sweep. `test_wrong_auth_is_rejected_not_passed`
asserts a bad session → 401 (so 401 is never mistaken for success).
`test_scale_money_masks_every_exact_sentinel` proves masking scales away the exact
(unrounded) sentinels.

**Extending:** add a new `GET /api/*` → add it to `COVERED` (with a concrete
request) or `EXCLUDED` (with a reason), or the categorization test fails.

---

## 3. XSS renderer guard — `tests/test_dashboard_xss_wiring.py` (8 tests)

A **deny-by-default** static analyzer over `templates/dashboard.html`. It parses
every `${…}` interpolation inside HTML-bearing `<script>` template literals
(regex/comment/string-aware, nesting-aware) and treats each as an XSS sink
**unless provably safe**. No field-name allowlist (that had false negatives).

### "Provably safe" — the closed set

An interpolation passes only if it is one of:

- wrapped in `daEsc(…)` / `_esc(…)`;
- a call whose name is in **`_SAFE_CALL_NAME`** — escapers, numeric formatters
  (`fmt`/`_fmt`/`daFmt`/`fmtp`/`Number`/`parseInt`/`parseFloat`), allowlist
  classifiers (`…Cls`/`…Style`/`…Color`/`cls`), and **audited HTML builders**
  whose own template bodies the guard walks (`piCard`/`stockCard`/`voteChip`/
  `scoreBar`/`kellyBar`/`perfChip`/`volBadge`/`build*`/`scoreChip`). A helper that
  `return`s raw data (e.g. `friendlyErr`) is **not** here;
- a numeric/format expression (`.toFixed`/`.toLocaleString`/arithmetic);
- a **numeric-used `(object, prop)`** — proven numeric *for that object* elsewhere
  (`d.pnl_pct.toFixed`, `h.win_rate > 0`, `Number(x.p)`). `x||0` is **not** a proof;
- a pure-literal ternary (all branches string/number literals);
- a `.map(cb).join()` where `cb` is `daEsc`/`_esc` or returns a template/safe expr
  (bare `arr.join(', ')` / `.map(s=>s)` are sinks);
- an HTML-valued local whose **every** assignment is safe (via the safe-set), and
  a bare identifier that is a keyword/loop-index.

Anything else → **flagged**. `depth>40` fails **closed**.

### The 3 soundness rules that were hard-won (don't regress them)

1. **Function calls are trusted only via `_SAFE_CALL_NAME`**, not blanket — an
   arbitrary call can return raw data.
2. **Numeric props are `(object, prop)`-scoped** — a numeric `d.revenue_growth`
   must not vouch for a string `x.revenue_growth`; and `||0` proves nothing.
3. **HTML-valued locals require ALL assignments safe** — there is no `any()`
   template rule (that trusted `r`, which is also `r = await fetch()`).
   Scope-shadowed HTML locals are **renamed** (`recsHtml`, `sectorsHtml`) so a
   name isn't HTML in one function and a raw array in another.

Negative tests lock every bypass shut (`test_guard_is_deny_by_default_no_bypasses`,
`test_num_props_are_object_scoped`, `test_html_local_requires_all_assignments_safe`,
`test_deep_nesting_fails_closed`).

**Extending / fixing a flag:** when the guard flags a `${x}` you added:
- if it renders **data** → wrap it: `${daEsc(x)}` (escaping a number is harmless);
- if it's a **new HTML-builder helper** → confirm its body is a template with
  escaped leaves, then add its name to `_SAFE_CALL_NAME`;
- **never** `daEsc` an HTML-valued local (double-escapes / shows markup) — fix its
  leaves instead, or rename it if the name is scope-shadowed.
After changes, `node --check` the extracted `<script>` to confirm the JS parses.

---

## 4. CI + local hook

- **`.github/workflows/tests.yml`** — two jobs: `test` (full offline suite +
  coverage) and **`customer-isolation`** (the three gate files on a deterministic
  in-process fixture). Both must be green.
- **`scripts/hooks/pre-push`** + **`scripts/install-git-hooks.sh`** — install once
  (`bash scripts/install-git-hooks.sh`); runs the gate before every push, blocks
  on failure. Bypassable with `--no-verify` (emergency only) → hence the required
  check in `docs/BRANCH_PROTECTION.md`.

---

## 5. Known-but-sound trust mechanisms (watch list)

- `_MAP_LOCALS` trusts `const X = { …all string/number literal values… }` maps
  (indexed lookups render a fixed literal). Sound (`const` can't be reassigned;
  worst case an unknown key renders `undefined`), but it's a trust mechanism — if
  you ever make such a map hold non-literal values, the guard would over-trust it.
- The audited-HTML-builder list in `_SAFE_CALL_NAME` is trusted because each
  builder's body is a walked template. If you add a helper that returns raw data,
  do **not** add it to the list.

---

## 6. History / rationale

Full pass-by-pass resolution (masking → semantic-path → deny-by-default →
object-scoped numeric props / all-assignments-safe locals) is in
`docs/REVIEW_HANDOFF_2026-07-03.md`. The customer-readiness context is in
`docs/CUSTOMER_READINESS_REVIEW.md`.
