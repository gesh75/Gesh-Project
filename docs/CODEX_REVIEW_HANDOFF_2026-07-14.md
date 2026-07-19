# Codex Review Handoff — PRs #30 → #59 (week of 2026-07-08 → 07-14)

**Purpose.** Independent review by Codex of everything merged to `gesh75/Gesh-Project:main`
(and the two sibling PRs in `gesh75/gesh-project-trading`) during the week of 2026-07-08
to 07-14. **We are holding further work until Codex approves.**

**How to review.** Every PR below is squash- or merge-committed to `main`; diff any single
one with `git show <merge-sha>` or `gh pr diff <n>`. CI (`.github/workflows/tests.yml`)
ran green on each: two jobs — `test` (full offline suite) and `customer-isolation` (leak
sweep + XSS wiring + customer view). The private `gesh-core` dependency is installed in CI
via the `GESH_CORE_PAT` secret; in a bare sandbox `kelly.py` / `trading/*` / `trading.state`
are shims that stub out, so a few suites only fully run under CI.

Run locally: `pip install -r ci-requirements.txt && pip install pytest ruff` then
`pytest tests/ -q` and `ruff check tests/`.

---

## 1. Status ledger (#30 → #59)

| PR | Date | Status | Title |
|---|---|---|---|
| #30 | 07-10 | ✅ merged | mybook: bulk import + equal-weight (privacy) mode for customer books |
| #31 | 07-11 | ✅ merged | daily-chain: fall back to last serviceable snapshot instead of aborting |
| #32 | 07-11 | ⛔ closed (superseded by #33) | mybook: reliable async book analysis (early attempt) |
| #33 | 07-11 | ✅ merged | mybook: reliable async customer-book analysis (thread worker, numpy-safe) + LT-macro overlay |
| #34 | 07-11 | ✅ merged | tests: un-red main — snapshot_state CLI test used frozen NOW vs wall-clock |
| #35 | 07-11 | ✅ merged | mybook: guard concurrent book-analysis — 409 instead of duplicate Claude spend |
| #36 | 07-12 | ✅ merged | consensus: wire **LT_Macro as the 12th node** + tests |
| #37 | 07-12 | ✅ merged | macro: make `/api/long-term-macro/cached` a real serve cache |
| #38 | 07-12 | ✅ merged | emit long_term_macro + per-name FFT + consolidation zones to `engine_results.json` |
| #39 | 07-13 | ✅ merged | GPT-5.6 audit remediation (all 10 gaps) + reconcile with main's LT_Macro node |
| #40 | — | n/a | (number never used) |
| #41 | 07-13 | 🟡 **open** | docs: LT_Macro voting-vs-display-only decision (tracking) — **needs a call** |
| #42 | 07-13 | ✅ merged | analyze: company-name → ticker typeahead |
| #43 | 07-13 | ✅ merged | analyze: momentum buy/sell signal (RSI+MACD+Volume) + Fear&Greed fetch fix |
| #44 | 07-13 | ✅ merged | Analyze: trade-setup levels + earnings-beat visualization |
| #45 | 07-13 | ✅ merged | Analyze polish: hero price + 2-up signal row |
| #46 | 07-13 | ✅ merged | Fix name-search look-alike ranking (Apple→AAPL) + 2-dp hero price |
| #47 | 07-13 | ✅ merged | `tier` admin CLI to comp a tester to higher LLM quotas |
| #48 | 07-13 | ✅ merged | Degrade the AI research card honestly on an empty/degenerate response |
| #49 | 07-14 | ✅ merged | Data-quality guards: canonical FV %, P/E "N/M", 52-wk sanity |
| #50 | 07-14 | ✅ merged | Guard the price layer against bad-tick outliers (NVDA $17.16 flash) |
| #51 | 07-14 | ✅ merged | Make ↻ Refresh honest about offline snapshots |
| #52 | 07-14 | ✅ merged | Reset scroll to top on tab switch (Risk tab blank-viewport quirk) |
| #53 | 07-14 | ✅ merged | Customer-friendly header badges: "Powered by Claude" + clearer masking label |
| #54 | 07-14 | ✅ merged | Docs: refresh architecture + roadmap (12-node, customer surface) |
| #55 | 07-14 | ✅ merged | Add `gesh_financial_learning_path.html` — up-to-date product curriculum |
| #56 | 07-14 | ✅ merged | Review /watch /invest /track-record: harden /watch script-payload (parity) |
| #57 | 07-14 | ✅ merged | Property harness for the core money/momentum math (slices 1–2) |
| #58 | 07-14 | ✅ merged | Property harness slice 3 (trade_levels) + **fix two bugs it surfaced** |
| #59 | 07-14 | ✅ merged | Cross-source price-sanity flag — catch whole-series feed errors (NFLX $73.83) |

**Sibling repo `gesh-project-trading` (same week):** #23 (9003 JSON error handler +
paper-order wrap), #24 (project-docs refresh), #25 (surface real error on POST actions +
inline dry-run/paper feedback), #26 (authoritative "Orders Today" count). All ✅ merged.

---

## 2. Grouped by theme (what to scrutinize)

### A. Customer surface — self-serve research (the biggest new surface)
`#30, #42, #43, #44, #45, #46, #48` + earlier #12–#24.
- Any-ticker research: symbol search, momentum (RSI/MACD/Volume), trade-setup levels,
  earnings beat/miss, AI research card; watchlist + book; bulk import + equal-weight privacy.
- **New modules:** `symbol_search.py`, `momentum_signal.py`, `trade_levels.py`,
  `earnings_viz.py`. All pure/mechanical (no LLM) except the AI card.
- **Review focus:** money-masking correctness (`_MODEL_BOOK_USD` / `_scale_money`), the XSS
  wiring guard (`tests/test_dashboard_xss_wiring.py` — every `${}` innerHTML sink must be
  `daEsc` or a `build*` helper), and the leak sweep (`tests/test_customer_leak_sweep.py`).

### B. Data-quality & resilience (Nikolai's live-audit remediation)
`#48, #49, #50, #58, #59` (+ trading #25).
- **#49** canonical fair-value % (deterministic from estimate+current, ignore the model's
  non-deterministic `pct_diff`); negative P/E → "N/M"; implausible 52-wk range suppressed.
- **#50** `_latest_close` bad-tick guard — rejects a single outlier vs the recent-20 median
  (NVDA $17.16). **Note the limitation:** cannot catch a *whole-series* error.
- **#58** `trade_levels` round-then-filter fix (a support could round to == price) **+** a
  NaN guard — both found by the property harness.
- **#59** cross-source price-sanity: flags a price implausibly outside the RH 52-wk range
  (whole-series errors like NFLX $73.83). **Flags, never suppresses.** `price_sanity.py`.
- **Review focus:** thresholds (½× low / 2× high in #59; 0.5×–2× median in #50; >5× range
  in #49) — are they too loose/tight? All are **fail-open** by design (never blank a card).

### C. Engine — the 12th consensus node
`#36, #37, #38, #33` (+ open **#41**).
- `LT_Macro` (VIX + CNN Fear&Greed + FINRA margin debt) added to `consensus.py` at weight
  **0.5**, wired into the vote, emitted to `engine_results.json` for the agentic-rh bridge.
- **⚠ Open governance question (#41):** `main` votes LT_Macro as a real 0.5 node, but the
  #39 design doc says it should be **display-only until walk-forward validation**. A
  market-wide 0.5 tilt is applied uniformly across the book, unvalidated. **This is the one
  decision we'd like Codex to weigh in on.** See `docs/DECISIONS/lt_macro_voting.md` (#41).

### D. Reliability / correctness fixes
`#31` (serviceable-snapshot fallback), `#33`/`#35` (async book analysis 202+poll, numpy-safe
JSON, 409 concurrency guard), `#34` (frozen-NOW test time-bomb), `#48` (AI card honest
degrade), `#51` (offline Refresh honesty), `#52` (tab-scroll), plus trading #23/#25.

### E. Test harness (coverage theme, additive)
`#57, #58`. Property/invariant tests over hundreds of seeded draws for `monte_carlo`,
`momentum_signal`, `trade_levels` — no `hypothesis` dependency (seeded numpy loops), fully
offline. **#58 caught and fixed 2 real bugs** (the round-then-filter + NaN gaps above).
`kelly` / `risk_gate` property slices are deferred (gesh_core-stubbed in sandbox).

### F. Docs
`#54` (ARCHITECTURE.md + README 11→12 node, customer surface, roadmap state update),
`#55` (learning-path HTML), this handoff.

---

## 3. Highest-value things for Codex to verify

1. **#58 `trade_levels` fix correctness** — round-then-filter and the finite guard. Does the
   long setup (`stop < entry < target`, RR > 0) still hold for *all* inputs? (Property tests
   assert it; confirm the invariant is right, not just that tests pass.)
2. **#49 canonical fair-value %** — is `(estimate − current)/current` the right convention
   everywhere it's rendered (buildFV, buildAICard, both daRender sites)? Sign consistency was
   the reported bug.
3. **#59 / #50 thresholds & fail-open** — confirm a flag/guard can never blank a card or
   drop a real quote, and the thresholds won't false-positive on legitimate volatility.
4. **trading #25 error-detail exposure** — 43 handlers now echo `f"{type(e).__name__}: {e}"`.
   Confirmed owner-only (127.0.0.1 + Host/Origin mutation guard). Is echoing exception text
   acceptable there? (We judged yes; it's an internal console.)
5. **#41 LT_Macro voting** — the governance call above.
6. **Money masking** (`_scale_money` / `_MODEL_BOOK_USD`) still intact after #53's label
   change and #59's fundamentals additions — a customer must never see owner dollars.

---

## 4. Known-open / not-in-scope (deliberately deferred)

- **#41** LT_Macro voting-vs-display-only — needs a decision (see §2C).
- **Operational, not code:** the 9003 trading actions 500 until **Robinhood is
  re-authenticated on that server** — #25 makes the login failure legible, it doesn't log in.
- **NFLX $73.83** — confirmed a whole-series feed error; #59 flags it *if* the RH 52-wk range
  is correct while the price feed is wrong. If both sources are wrong, no cross-check catches it.
- **Big architectural themes (own future efforts):** money-as-`float` → `Decimal` across ~55
  modules; raise real coverage to 70–80%; gesh-core extraction; production deploy story.
  Tracked in `GAPS_AND_IMPROVEMENTS_2026-07-09.md` (2026-07-14 State Update).

---

## 5. Test / CI reference

- Full suite: `pytest tests/ -q` (CI: ~600+ passing on main).
- Lint (CI scope): `ruff check tests/`.
- Customer isolation: `pytest tests/test_customer_leak_sweep.py tests/test_dashboard_xss_wiring.py tests/test_customer_view.py`.
- New this week: `tests/test_{momentum,trade_levels,earnings_viz,symbol_search,price_sanity,mybook_bulk,long_term_macro}*.py`
  and the property harnesses `tests/test_{monte_carlo,momentum,trade_levels}_properties.py`.

_Prepared 2026-07-14 for Codex review. No further changes until approval._
