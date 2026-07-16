# Grok review request — Phases 1–4 (PRs #81–84)

**Reviewer:** Grok (`grok-build`, the GESH coding agent) · **Author:** Claude (Opus 4.8)
**Date:** 2026-07-16 · **Status of all 4 PRs:** OPEN, **merges deliberately held**.

You are reviewing four independent PRs that resolve the current "next steps" for GESH. Each was
built in an **isolated git worktree off `origin/main`** because the shared repo was being
live-edited by other agents at the time (see [Context](#context-you-must-know)). Review them as a
set, but each merges independently.

## What I want back (per phase)

For **each** phase give:
1. a verdict — **APPROVE** / **CHANGES REQUESTED** / **BLOCK**;
2. findings ranked by severity (**CRITICAL** / **HIGH** / **MEDIUM** / **LOW**), each with
   file:line and a concrete failure scenario (inputs → wrong output), not a style opinion;
3. for the security and money-math phases specifically — **try to break it**: find the input or
   deploy state that defeats the fix, or prove an invariant assertion is wrong.

Then one **cross-cutting** section: merge-order + collision risk (below), and any duplication with
the concurrent `codex/full-remediation-product` work.

## Context you must know

- **origin/main tip at authoring:** `57502a4` (PR #80). PR #81's base is `5acce1e` (one behind —
  disjoint region, see below); #82/#83/#84 are based on `57502a4`.
- **Concurrent agents:** two Codex worktrees (`codex/decision-lab-mvp-plan`,
  `codex/full-remediation-product`) plus a live writer were editing `server.py`, `dashboard.html`,
  `price_source.py`, `tests/test_mybook.py` in the main working dir during this work. My PRs touch
  **disjoint** regions, but confirm no merge conflict before landing.
- **To review each branch:**
  ```bash
  git fetch origin
  git diff origin/main...origin/<branch>         # branch-only changes
  git checkout origin/<branch>                    # to run tests
  # venv: /Users/georgigaydarov/Projects/gesh-project/venv/bin/python3.14
  ```

| Phase | PR | Branch | Kind | Files |
|---|---|---|---|---|
| 1 | #81 | `fix/p1-cookie-secure-hardening` | **security (code)** | server.py, tests/test_cookie_secure.py, .gitignore |
| 2 | #82 | `docs/dil-phase0-kit` | docs | docs/superpowers/decision-integrity-lab/* |
| 3 | #83 | `test/kelly-property-tests` | tests (money-math) | tests/test_kelly_invariants.py |
| 4 | #84 | `docs/signal-expectancy-diagnosis` | analysis + script | docs/SIGNAL_EXPECTANCY_DIAGNOSIS_2026-07-16.md, scripts/diagnose_signal_expectancy.py |

---

## Phase 1 — #81 session-cookie `Secure` per-request (SECURITY-CRITICAL)

**Problem:** `Secure` was a single global env flag, and prod `.env` pinned `GESH_COOKIE_SECURE=0`
(a local-http workaround) → httpOnly **session cookies shipped in clear to real HTTPS users**.
**Fix:** derive `Secure` per request. Full `server.py` diff:

```diff
-def _cookie_secure() -> bool:
-    """... Defaults to True; set GESH_COOKIE_SECURE=0 ONLY for local plain-HTTP dev. ..."""
-    return os.getenv("GESH_COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no", "off")
+def _cookie_secure(request: Request | None = None) -> bool:
+    """Resolution order: (1) explicit GESH_COOKIE_SECURE env override when non-empty;
+    (2) else derive per-request from X-Forwarded-Proto / url.scheme; (3) else default secure."""
+    override = os.getenv("GESH_COOKIE_SECURE", "").strip()
+    if override:
+        return override.lower() not in ("0", "false", "no", "off")
+    if request is not None:
+        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
+        if proto:
+            return proto == "https"
+        return request.url.scheme == "https"
+    return True
@@ auth_login
-        secure=_cookie_secure(),
+        secure=_cookie_secure(request),
@@
-def auth_magic(token: str = ""):
+def auth_magic(request: Request, token: str = ""):
@@ auth_magic
-        secure=_cookie_secure(),
+        secure=_cookie_secure(request),
```

**⚠️ Deploy dependency (NOT in the diff — `.env` is gitignored):** the code is inert until
`GESH_COOKIE_SECURE=0` is removed from the production `.env` and `com.gesh.main` is restarted,
because a non-empty override still wins at step (1).

**Break-it questions for you:**
1. **X-Forwarded-Proto trust.** The claim is ":9000 is only reachable via the tunnel that sets the
   header, so spoofing is impossible." Verify against the actual deploy: is there *any* path
   (direct bind, another proxy, local port-forward, a second `set_cookie` elsewhere) where an
   attacker-controlled `X-Forwarded-Proto: https` could matter? A Secure cookie over a spoofed-https
   claim is not itself a downgrade — but flag if the header also drives anything else.
2. **The inverse risk.** Is there a real request path where the tunnel forwards **https** but
   `X-Forwarded-Proto` is absent AND `request.url.scheme` is `http` (uvicorn without
   `--proxy-headers`) → we'd emit a **non-Secure** cookie over TLS? If so the fix under-secures in
   that config. What does the running uvicorn invocation actually do?
3. **Comma-list parse.** `split(",")[0]` takes the first hop. Correct for `X-Forwarded-Proto`? Any
   chance the trusted proxy appends rather than prepends?
4. **Override semantics change.** Old default was `"1"` (secure when unset); new treats empty/unset
   as "derive". Confirm no caller relies on the old unset→secure behavior in a no-request context
   other than the fail-safe `return True`.
5. **Only 2 set_cookie sites?** Grep confirms `auth_login` + `auth_magic`. Any other cookie emission
   (logout delete, a third handler, middleware) that should also be per-request?
6. **Tests** (`tests/test_cookie_secure.py`, 12 cases): do they cover the inverse risk in (2)? Is the
   `_FakeRequest` faithful to Starlette's case-insensitive `Headers`?

---

## Phase 2 — #82 Decision Integrity Lab Phase 0 kit (docs)

Three docs under `docs/superpowers/decision-integrity-lab/` operationalising the plan
`docs/superpowers/plans/2026-07-15-decision-integrity-lab-mvp-CLAUDE.md` (§A gates, §B build order,
§D honesty). No product code by design.

**Fact-check questions for you** (read the plan, then the kit):
1. Are the **gates transcribed correctly**? Specifically: draft→lock **<3 min**, **≥60% ex-ante**,
   **0 SLA breaches**; twin-PMF **≥40% on the *second* question**; the kill gate **≥5/25** interviews.
   Flag any number that drifted from the plan.
2. Is the **§B build order** faithful — interviews → 5 paid concierge loops → MVP code only after
   **two** loops complete? Does the kit anywhere imply building Tasks 1–6 before the concierge runs
   (which the plan explicitly forbids)?
3. Is the **§D honesty line** reproduced **verbatim** ("The automated version of this product would
   not have had that reviewer.")? Is Wizard-of-Oz correctly **rejected**, not endorsed?
4. Any place the kit treats **seat count as a continuation gate** (the plan says it must not)?
5. Compliance: does it correctly gate paid seats behind counsel approval and avoid
   personalized-investment-advice monetization?

---

## Phase 3 — #83 Kelly invariant tests (money-math)

`tests/test_kelly_invariants.py` — 201 checks over `gesh_core.kelly` (via the `kelly` shim),
dependency-free (seeded synthetic-return grid, no `hypothesis`), network-free (injects `returns=` /
monkeypatches `_fetch_returns`).

**The invariants asserted** (your job: are these the RIGHT invariants, and are any assertion
expectations WRONG?):
- `0 ≤ optimal_pct ≤ cap` and `recommended_usd ≥ 0`; `half_kelly == optimal_pct`;
  `optimal ≤ raw_kelly/2 + 0.1` (rounding tol).
- `optimal_pct == 0 ⇒ action == "AVOID"`; persistent-loss series ⇒ AVOID.
- bearish `ai_eval` (AVOID/SELL/REDUCE, or `pct_diff<-15`) **never up-sizes** vs baseline;
  autocorr shrink ∈ `[0.30, 1.0]`.
- positive `analyst_upside_pct` **never shrinks** (≥ baseline − 0.05).
- `run_kelly_analysis`: gross allocation ≤ gross cap; `cash == max(0, 100−allocated)`;
  `portfolio_kelly_score ≥ 0` and finite (no NaN); every `corr_haircut ≤ 1.0`; input not mutated.

**Break-it questions for you:**
1. **Are any of these invariants actually false for the implementation** (i.e., the test would
   wrongly fail on correct code, or wrongly pass on a real bug)? E.g. is `optimal ≤ raw_kelly/2` truly
   guaranteed given the analyst-boost multiplies *after* the blend but the cap is applied last?
2. **Tolerances** (`0.1`, `0.05`, `abs=0.11`): are any loose enough to mask a real regression?
3. **Does it test the right code?** The shim `sys.modules['kelly']=gesh_core.kelly` — confirm the
   test exercises the installed `gesh_core`, not a stale local copy, and that CI installs `gesh_core`
   via the PAT so this runs at all.
4. **Monkeypatch fidelity:** `run_kelly_analysis` internally calls `_fetch_returns`; the fixture
   patches it to return dated `pd.Series`. Does that faithfully exercise the date-aligned
   `_correlation_haircuts` path (vs the positional fallback)?
5. **Coverage gap:** what real Kelly bug would slip **past** all 201 invariants? (e.g. a sign error
   that preserves bounds, an off-by-one in the 60/40 blend weights.) Name one and propose the missing
   assertion.

---

## Phase 4 — #84 signal-expectancy diagnosis + reproducible script

`docs/SIGNAL_EXPECTANCY_DIAGNOSIS_2026-07-16.md` + `scripts/diagnose_signal_expectancy.py`
(read-only, network-free; reads the tracker `paper_outcomes`). Conclusion: **the engine cohort has
no proven edge — do not go live.**

Headline numbers (engine cohort = non-manual, live 2026-07-16): 14 closed, expectancy **−0.51%**,
Sharpe **−0.67**, PF 0.67, OOS consistency **0.0**, win rate **14.3%** vs break-even **20.6%**;
one name (UNH +$138.54) is the whole edge (strip it → −$208.28); GS −$135.78 over 2 re-entries.

**Break-it questions for you:**
1. **Reproduce the numbers.** Run `python3 scripts/diagnose_signal_expectancy.py` against the tracker
   DB (`$GESH_TRACKER_DB` or the sibling `gesh-project-trading/gesh_financial.db`). Do the printed
   expectancy / win-rate / break-even / per-symbol figures match the doc? Flag any mismatch.
2. **Break-even math.** The doc uses `w* = 1/(1+payoff)` with payoff = avg_win/|avg_loss|. Is that the
   correct break-even for this P&L definition (pct returns, not R-multiples)? Does mixing BREAKEVEN
   rows into `n` distort win-rate vs expectancy?
3. **Is the "no edge" conclusion overstated at n=14?** The doc leans on OOS=0.0 and prob-Sharpe 0.38.
   Push back: is 14 trades enough to *conclude* negative edge, or only to *fail to confirm* positive
   edge? Is the wording appropriately hedged, or does it overclaim?
4. **Cohort definition.** Is "non-manual = engine" right? Should `market_scan` be grouped with
   `consensus`, or analysed separately (it's 0/2)? Does the gate DTO's cohort match the script's?
5. **Script correctness/safety:** read-only URI (`mode=ro`) — good. Any injection risk in the
   `--sources` split into the `IN (...)` clause? (It parameterises — confirm.) DB-resolution
   fallbacks sane?
6. **Root-cause validity:** is "Kelly's corr-haircut governs sizing, not the entry decision, so the
   signal re-buys losers" accurate to the code? Is the recommended fix (per-name entry cap + cooldown)
   the highest-leverage one, or is #1 (signal-precision backtest) the real first move?

---

## Cross-cutting

- **Merge order & collision:** #81 touches `server.py` lines ~731–830 (auth cookie). The concurrent
  `codex/full-remediation-product` was rewriting `server.py` around `_latest_close_meta` (~2227),
  `_mybook_analyze_worker` (~2776), `_render_book_report` (~2937) — disjoint, but confirm with
  `git diff origin/main...origin/codex/full-remediation-product -- server.py` before landing #81.
  #83 adds only a test file; #82/#84 are docs/script — all conflict-free.
- **Recommended land order:** #81 (security) first after the `.env` deploy step is ready → then the
  two docs (#82, #84) → then #83. But #81 and the Codex `server.py` work should be reconciled by
  whoever merges second.
- **Suggested output format:** reply with a per-phase verdict block and a single ranked findings
  table; call out anything that should block a merge.
