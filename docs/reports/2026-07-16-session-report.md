# GESH Financial — Engineering Session Report, 2026-07-16

**Prepared for external review (Grok / Gemini / GPT).**
Repo: `gesh75/Gesh-Project` (FastAPI + SQLite AI portfolio-intelligence SaaS; production deployment details omitted). All work merged to `main` and deployed the same day. Test suite grew **880 → 949 tests, all green**. Eight PRs merged: **#72, #74, #75, #76, #77, #78, #79, #80**. *(See the 2026-07-17 addendum at the end for the post-review remediation wave: PRs #81+.)*

---

## Part 1 — Production bug remediation (PR #72, merged `f258b6a`, deployed)

Input was a two-run QA audit of the live app (one partial-fail, one pass). Root-cause investigation found the "pass" was masking a live race.

### 1.1 Cross-symbol price contamination (the NVDA = TSLA = $136.08 bug) — REAL ROOT CAUSE FOUND
- **Diagnosis:** `yfinance 1.2.1 download()` is **not thread-safe** — it resets and fills the module-global `yfinance.shared._DFS` and completion-checks it with `while len(shared._DFS) < len(tickers)`. GESH's cold-book warm (`server._customer_book_payload`) ran 8 concurrent single-ticker downloads via ThreadPoolExecutor, so thread A (NVDA) could be handed thread B's TSLA frame with the ticker column level already dropped — undetectable downstream, then pinned 5 minutes by the close cache. Audit #2 "passed" only because the cache was warm (no concurrency).
- **Fixes (3 layers):**
  - `price_source.py`: `_YF_DOWNLOAD_LOCK` serializes every `yf.download` call (root cause).
  - `server._latest_close_meta`: refuses to flatten a multi-ticker Close frame — selects the requested symbol's column or returns None (defense in depth).
  - `trading/robinhood_quotes.fetch_daily_ohlcv`: rejects a response whose echoed symbol ≠ the request (defense in depth).
- **Tests:** new `tests/test_price_thread_safety.py` — a concurrency test that observed the real 6-way race before the fix (RED), plus multi-frame and symbol-echo guards.

### 1.2 Stale-analysis mismatch on /mybook/report (audit Test 4)
The report rendered CURRENT positions beside the LAST persisted analysis with no comparison (a 40-stock book paired with single-stock TSLA scores). Fix: analyses now persist a book fingerprint (`_fp`); the report renders a prominent **"⚠ Analysis out of date"** banner when the book changed (legacy rows fall back to symbol-set comparison). 3 new tests.

### 1.3 Console `Error: 530` from `loadPortfolioWithECG`
Transient Cloudflare-tunnel 530s now retried (3 attempts, backoff) in `dashboard.html` before logging or flipping the connection badge.

Deployed: merged through CI, `com.gesh.main` restarted, smoke-tested.

---

## Part 2 — Product research (EXA, multi-source)

A research agent swept competitor reviews (Simply Wall St, Koyfin, Seeking Alpha, Danelfin, Stock Unlock, TIKR, Motley Fool teardowns), Trustpilot/Reddit sentiment, fintech-dashboard UX guides, AI-trust frameworks, and pricing pages. Headline: **the market pays for verifiable judgment (daily rating + live track record + alerts on the customer's own holdings), not more data or AI prose.** Key risks to avoid: tab sprawl (Koyfin's #1 criticism) and score flapping (Danelfin's #1 Trustpilot complaint).

This produced 6 ADD recommendations and 5 REMOVE/de-emphasize recommendations, all sourced.

---

## Part 3 — Two implementation plans (PR #78)

Written to the repo, TDD-style, with exact code, file paths, and interface contracts:
- `docs/superpowers/plans/2026-07-16-retention-features.md` — Plan A, 7 tasks (ADD 1–6).
- `docs/superpowers/plans/2026-07-16-customer-dashboard-simplification.md` — Plan B, 5 tasks (REMOVE 1–5).

---

## Part 4 — Execution: multi-agent program (all 12 tasks shipped)

Method: subagent-driven development — per wave, a fresh implementer agent in an **isolated git worktree** (TDD, full-suite gate, ruff zero-new-errors gate), then an **independent code-reviewer agent** on the PR diff, then a fix cycle, then CI, then merge. Waves were parallelized where file-conflict domains allowed.

### Wave 1 (parallel)
**PR #74 — signals backend** (Plan A: T1 signal_history, T2 alerts, T7 digest)
- `signal_history.py`: daily per-symbol consensus snapshots + **persistence-gated flip detector** (a verdict change only "confirms" after 2 consecutive days — the anti-flapping rule from research).
- `run_signal_alerts.py` + launchd `com.gesh.signal-alerts` (11:05 ET Mon–Fri): snapshots the union of all customers' watchlist+book symbols, emails **one digest per customer per day** of confirmed flips (idempotent via `alert_log`), typed `alerts_unsub` unsubscribe tokens, `/alerts/unsubscribe` route.
- `run_weekly_digest.py` + launchd `com.gesh.weekly-digest` (Fri 17:15 ET): book value, the week's confirmed signal changes, report/track-record links. **Zero LLM spend** (structurally test-enforced).
- Review findings fixed: per-customer exception isolation in the digest loop; PBKDF2 skipped when the users row exists.

**PR #75 — card trust** (Plan A: T3 provenance, T5 bear case)
- Provenance block on every AI ticker card: `generated_at`, price feed source, model, suspect flag; `buildProvenanceStrip` renderer.
- `bear_case {thesis, invalidators[]}` added to the ticker-card schema — rides the existing single LLM call; invalidators prompt-forced to observable, number-tied conditions.
- Review findings fixed: bear_case capped in concise mode + defaulted per holding (the shared schema leaks into the token-budget-constrained book-analysis path); provenance strip gated to render only when a real provenance block exists.

### Wave 2 (parallel)
**PR #76 — sparkline** (Plan A: T4)
- `GET /api/signals/history/{symbol}` (session-gated, 5-min cache, empty history = 200) + `buildSignalSparkline` inline SVG on the single-ticker card.
- Review findings fixed: leak-sweep test was writing DDL into the **real production `gesh_financial.db`** (signal_history DB path unmocked) — isolated; dead sparkline placeholder removed from book-analysis cards.

**PR #77 — mybook cluster** (Plan A: T6; Plan B: T1, T5)
- `POST /api/mybook/deploy-cash` — "Where does new cash go?": ranks the customer's Kelly ADD gaps against an entered cash amount, floor share counts, staleness-labeled, LLM-free.
- `GET /api/mybook/summary` + customer **Home hero** (default customer tab): book value, top-3 confirmed changes, next-action ladder (add holdings → analyze → re-run stale → view report).
- Book table signal columns: CONSENSUS / KELLY Δ / RISK with honest empty states; `/api/mybook` enriched with `signals`, `kelly_deltas`, `analysis_stale`, `risky_symbols`.
- Review findings fixed: RISK matching switched to word-boundary + case-sensitive (tickers ON/SO/ALL/NOW would false-positive on English prose); deploy-cash wrapped in the standard error handler + None-tolerant Kelly math; report's `:.1f`-on-None latent bug fixed.

### Wave 3
**PR #79 — customer disclosure** (Plan B: T2, T3, T4)
- Report leads each engine section with a plain-language conclusion ("Worst plausible 21-day stretch (95% confidence): −$X on your $Y book." / "N sizing adjustments — largest: SYM ACTION"), full tables folded in `<details>`, print CSS keeps them visible in PDFs; dashboard mirrors the pattern.
- `_lead_and_fold()`: AI prose shows a bold first-sentence lead + folded "Full context"; gemini/ollama commentary verified owner-only (live 403 test).
- News-as-sentiment chip on the ticker card (cache-only, allow-listed `score/mood/article_count/as_of`, **article_count>0 enforced server-side**); the Recent News article feed scoped owner-only. The implementer correctly adapted when the real `/api/news/{symbol}` contract differed from the plan's assumption, and extended the public-contract tests to pin the new block.
- Review findings fixed: "largest" Kelly adjustment now selected by |delta| (was first-by-optimal_pct — could headline a 0.1pp trim over a 14pp add); stale docs reconciled; stale-analysis caveat added to the risk headline itself.

### Final whole-branch review (most capable model) → PR #80
Cross-PR pass over the cumulative diff verified all 12 tasks landed and the assembled card/hero/report surfaces are coherent. It caught **1 Critical + 2 Important** that per-task reviews couldn't see:
- **CRITICAL:** both new cron scripts lacked `load_dotenv()` — launchd doesn't inherit shell env, so `RESEND_API_KEY` would be unset and both email features would **silently no-op forever** (exit 0, indistinguishable from "no flips today"). Fixed with the repo's guarded dotenv pattern + an explicit `email_delivery: DISABLED` marker in job summaries.
- Staleness semantics unified: `_analysis_stale_for` now carries the legacy symbol-set fallback, shared by hero / deploy-cash / book columns / report (they previously could disagree on pre-fingerprint rows).
- The unsubscribe pages promised a re-enable path that didn't exist → shipped `GET/POST /api/preferences` + email-preference checkboxes on the Home pane.

---

## Part 5 — Deployment & live verification

- The production service was restarted on final main and its authentication controls were smoke-tested; host, route, and response details are omitted.
- Authenticated customer endpoints were verified; portfolio values, holding counts, and response data are omitted.
- Test-data cleanup was completed before the production signal-history seed; database details are omitted.
- Both scheduled jobs were installed and exercised in their production execution environment; job labels and output metadata are omitted.

## Part 6 — Incidents handled during the program

1. **Reviewer wiped uncommitted work:** a review agent ran `git checkout HEAD -- server.py` in the main working tree, destroying a parallel session's uncommitted cookie-security rewrite. Recovered from a patch I had saved earlier; all subsequent agents carried an explicit worktree-only rule; the work later survived a merge conflict against the new `/api/preferences` routes and is intact in the working tree (still uncommitted, owned by the other session).
2. **CI flake found and isolated:** the customer-isolation sweep's sentinel `'6.08'` substring-matches inside unrelated computed values (e.g. an ADX of 16.08). Rerun confirmed flake; a fix task was spawned as a separate session (in progress).
3. **Agent killed by API session limit** mid-Wave-3; resumed after reset with no work lost.

## Part 7 — Accepted debt & open items

| Item | Status |
|---|---|
| Weekly digest double-fire dedup table | Accepted debt (weekly cadence, tiny customer count) |
| Extract `price_cache.py` (digest cron imports all of server.py for one function) | Accepted debt — highest-value next fix |
| `auth_users.active_digest_recipients()` helper (encapsulation) | Accepted debt |
| Leak-sweep `'6.08'` substring flake | Fix running in a separate session |
| First real digest email delivery | Verify Friday 17:15 ET (`/tmp/gesh_weekly_digest.log`; `email_delivery: DISABLED` in the summary = env didn't load) |
| Sparkline visual | Needs ≥2 days of snapshots — appears from 2026-07-17 |
| Browser QA of both roles | Test prompt handed to operator (owner + customer sessions, T1–T8 cases) |

## Part 8 — Metrics

| Metric | Value |
|---|---|
| PRs merged today | 8 (#72, #74–#80) |
| Plan tasks shipped | 12 of 12 (+ 3 audit bug fixes) |
| Test suite | 880 → **949 passed** (69 new tests), 0 failures |
| Review findings caught pre-merge | 1 Critical, 8 Important, ~10 Minor — all Critical/Important fixed |
| New scheduled jobs | com.gesh.signal-alerts (daily), com.gesh.weekly-digest (weekly) |
| New API surface | /api/signals/history/{sym}, /api/mybook/summary, /api/mybook/deploy-cash, /api/preferences, /alerts/unsubscribe, /digest/unsubscribe, sentiment block on /api/news/{sym} |
| LLM cost added | Zero new LLM call paths (bear case rides the existing ticker-card call) |

---

*Generated by the orchestrating Claude session, 2026-07-16. Not financial advice; GESH is an informational/educational tool.*

---

## Addendum — 2026-07-17: external-review remediation (Grok / Gemini / Opus 4.8)

The report was reviewed by three external models; every actionable finding was verified and closed:

1. **"Prod runs uncommitted security code" (Opus)** — PR #81 (per-request cookie Secure) rebased onto main, conflict with `/api/preferences` resolved, 11 regression tests added, **merged** (`cc69518`). Working tree clean; deployment consistency was verified without retaining production details in this report.
2. **"The Critical email fix was never exercised" (Gemini + Opus)** — completed an end-to-end delivery proof using a synthetic prior-day verdict, then removed the synthetic data. The holding symbol, job output, delivery provider, and account details are omitted; production delivery was verified rather than assumed.
3. **Digest compose path** — `--dry-run` executed today: 3 customers composed, zero errors.
4. **New-endpoint auth (Gemini)** — verified: deploy-cash / preferences (GET+POST) / summary / signals-history all return **401 unauthenticated**.
5. **Schema migrations (Gemini)** — non-issue by design: `signal_history`/`alert_log`/`digest_log` DDL is lazy-on-connect; `users` columns use idempotent ALTER migration on connect; all verified live.
6. **Digest double-fire dedup (all three reviewers rejected this debt)** — implemented: `digest_log (email, iso_week)` table, one digest per customer per ISO week; dry-runs don't consume the week. Tested.
7. **Legacy staleness loophole (Gemini: same symbols / different shares reads fresh)** — fixed: any analysis without a fingerprint is now always stale (unverifiable ⇒ stale, self-clearing on next run). Tested.
8. **"Check for other tests pointed at the prod DB" (Opus)** — found and fixed a live instance: a mybook signals test was overwriting the real NVDA snapshot row. The overwritten row was restored from records, and a **suite-wide autouse fixture** now isolates `signal_history.DB_PATH` for every test.

Still open (tracked): leak-sweep `'6.08'` substring flake (separate session), `price_cache.py` extraction, batched cold-book price warm (yf lock latency), job-health surfacing, browser QA of both roles (prompt handed to operator).
