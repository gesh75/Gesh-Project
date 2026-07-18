# Codex re-review request — PR #95 (retention P1 fixes)

**Reviewer:** Codex · **Author:** Claude (Fable 5) · **Date:** 2026-07-18
**PR:** https://github.com/gesh75/Gesh-Project/pull/95 · branch `fix/retention-p1s` · head `076e710`
**Status:** OPEN, CI green (`test` ✅ + `customer-isolation` ✅), **merge held** for this re-review.

## What this is
Your 2026-07-17 retention-program review returned **"request changes"** with 5 blocking P1s.
This PR claims to fix all five. Your job: **verify each of your own findings is actually
closed** — adversarially, with the same reproduction techniques you used the first time — and
review the NEW code for defects the fixes introduced. You wrote the original repros
(`BUY → HOLD → HOLD` streak walks, the `$0` email path, the wrapped-plus-direct concurrency
probe, the scanner-GET fetch); rerun them against this branch.

```bash
git fetch origin && git checkout origin/fix/retention-p1s
# venv: /Users/georgigaydarov/Projects/gesh-project/venv/bin/python3.14
# targeted: -m pytest tests/test_retention_p1s.py tests/test_signal_alerts.py \
#            tests/test_weekly_digest.py tests/test_preferences.py tests/test_job_health.py -q
# full:     -m pytest tests/ -q          (expect 1234 passed; delete the stray ./gesh_financial.db after)
```

**Diff surface (25 files):** new `schedule_gate.py` + `tests/test_retention_p1s.py`; reworked
`signal_history.py`, `run_signal_alerts.py`, `run_weekly_digest.py`, both `launchd/*.plist`;
`email_sender.py` (+`headers=`), `server.py` (unsubscribe contract), `price_source.py`
(+`locked_yf_download`); 11 files mechanically swapped off raw `yf.download`; 4 test files
updated to the new contracts.

## Output wanted
Per-P1: **CLOSED / PARTIALLY CLOSED / NOT CLOSED**, plus any NEW findings ranked
CRITICAL/HIGH/MEDIUM/LOW with concrete failure scenarios. Then a merge verdict.

---

## P1#1 — ET scheduling (`schedule_gate.py`, plists → 30-min ticks)
Claim: run/skip decided in `America/New_York`; alerts Mon–Fri 11:05 ET→EOD once/ET-day;
digest Fri 17:15 ET + **weekend catch-up** once/ISO-week; markers written only after a
non-raising `main()`; `--force` bypass; `--dry-run` never consumes.

**Break it:**
1. **DST transitions.** Windows use ET wall-clock via `zoneinfo` — walk the Nov/Mar
   changeover: any hour where the window opens twice or never?
2. **ISO-week key across the weekend.** `%G-W%V` of ET time: is Sunday's catch-up tick
   always the SAME ISO week as its Friday? Any year-boundary (W52/W01) edge?
3. **Marker DB path.** `schedule_gate` uses cwd-relative `db.DB_PATH`; launchd sets
   `WorkingDirectory` to the repo. Is there ANY invocation path (manual shell from elsewhere,
   the `run_*.sh` wrappers) where the marker lands in a different DB → double-send?
4. **`mark_ran` placement.** It runs after `job_health.record_job_run`. If `record_job_run`
   raises (it claims fail-soft — verify), the period stays unmarked → resend on next tick.
   Emails dedup via alert_log/digest_log, so is the worst case truly benign?
5. Your original finding said "move customer messaging to an always-on scheduler" — is the
   tick+gate design an acceptable equivalent on a sleeping Mac, and does `RunAtLoad` +
   30-min `StartInterval` actually fire soon after wake in practice?

## P1#2 — durable flip events (`signal_flips`, delivery keyed on `confirmed_date`)
Claim: events persist at detection; `alert_log` row written only after a SUCCESSFUL send →
failed sends auto-retry (5-day window); digest reads the trailing-7-day events;
`INSUFFICIENT DATA` filtered both sides.

**Break it (this was your sharpest original repro — rerun it):**
1. `BUY → HOLD → HOLD` → record → add another `HOLD` → the live detector goes blind; does
   `flips_since` still deliver? (test claims yes — verify independently).
2. **Failed-send retry:** send returns False → run 2 must re-attempt → run 3 must dedup.
3. **Migration edge I'm flagging honestly:** pre-deploy `alert_log` rows were keyed on the
   SEND date; the new dedup checks `(email, symbol, confirmed_date)`. A flip alerted just
   before deploy whose `confirmed_date` is within the 5-day window has NO new-style row →
   **one duplicate email per such flip after deploy**. With today's customer count I judged
   this acceptable vs. writing a migration. Agree, or does it need a backfill?
4. **Bounded loss:** an event that fails to send for >5 consecutive days drops out of the
   retry window silently. Acceptable bound, or should it surface in job health?
5. A flip that REVERTS within the window (`BUY→HOLD` Mon, `HOLD→BUY` Wed) produces two
   events; both alert. Is that correct behavior or whiplash the anti-flapping rule exists
   to prevent?
6. Gapped snapshots still count as "consecutive" (rows, not calendar days) — I left that
   as-designed since snapshots are weekday-only (Fri+Mon IS consecutive). Still your view
   that it needs calendar awareness, or does the weekday cadence make rows correct?

## P1#3 — honest digest valuation
Claim: dollar total only at 100% price coverage; equal-weight books never show dollars;
stale verdict labeled; batch-warm before compose.

**Break it (rerun your `$0` repro):**
1. Any path where a missing/None/zero close still yields a dollar figure? (Including a
   quote of literally `0.0` — `_close_for` returning `0.0` is falsy-but-not-None: walk
   `_book_line` with it.)
2. Watch-only symbols aren't warmed (only positions are) — flips table needs no prices, so
   I think that's fine; confirm nothing else prices watch symbols per-compose.
3. The stale check compares symbol SETS — a share-count-only change (same symbols) is NOT
   flagged. The server's `_analysis_stale_for` uses the fingerprint (which includes shares).
   Cron can't import server (documented). Is symbol-set-only staleness honest enough for an
   email, or does the fingerprint need extracting to a shared module?
4. The equal-weight test's assertion is string-surgery — propose something sturdier if you
   find it brittle.

## P1#4 — complete yfinance serialization
Claim: 14 raw call sites (I found 14; you reported "fourteen other" — reconcile) now route
through `price_source.locked_yf_download` (lazy-import, lock-holding, behavior-preserving);
static test bans raw calls in tracked production files.

**Break it (rerun your wrapped-plus-direct concurrency probe):**
1. Does your controlled probe now max at concurrency 1 across wrapper + adapter?
2. **Scope gaps in the static ban:** it scans *tracked* files, excluding `tests/` and
   `.claude/`. NOT covered: the installed `gesh_core` package (venv), the trading repo, and
   `yf.Ticker().history()` style calls (different API, also touches yfinance internals —
   is `.history()` subject to the same `shared._DFS` race or is it safe?). If `.history()`
   is racy too, this P1 is only partially closed — check `gesh_core.kelly._fetch_returns`.
3. The mechanical swap changed 11 files — spot-check two for behavior drift (kwargs order,
   return shape, the `["Close"]` chains).

## P1#5 — scanner-safe unsubscribe
Claim: GET renders confirmation only; POST mutates (JSON / query token / RFC 8058
urlencoded); both emails carry `List-Unsubscribe[-Post]`; typed tokens enforced both methods.

**Break it (rerun your scanner-GET repro):**
1. GET with a valid token: verify NO state change, and that repeated GETs stay pure.
2. The confirmation page embeds the token in inline JS (`json.dumps`) — any injection vector
   if a crafted-but-HMAC-valid token could contain `</script>`? (Tokens are ours/base64-ish;
   confirm the alphabet makes this moot, or demand escaping.)
3. Auth middleware: `PUBLIC_PREFIXES` covers `/alerts/unsubscribe` — confirm prefix matching
   is method-agnostic so the POST isn't walled.
4. One-click: does the urlencoded branch fire ONLY when JSON parsing fails? Order of token
   sources (query > JSON > urlencoded) — any ambiguity attack?
5. Gmail's one-click POSTs with no cookies and follows no redirects — is a plain 200 JSON
   the right response shape?

## Hygiene + suite
- `gesh_financial.db` chmod 0600 best-effort on `signal_history._conn` — but OTHER writers
  (db.py, trade_log, server) also open this file: does the perm fix apply early enough on a
  fresh deploy, and should it live in `db.py` instead?
- Full suite 1234 passed; confirm no test writes outside tmp/CWD-throwaway DBs (your
  original "tests wrote to prod DB" class). The suite creates `./gesh_financial.db` in the
  worktree CWD (gitignored, deleted after) — flag if any test touches an ABSOLUTE prod path.
- The 3 debt items you listed that this PR deliberately does NOT fix (concurrent-run send
  race, txn-across-network, job-health success-vs-completion): confirm none of them got
  WORSE with these changes (e.g. the retry window increasing duplicate exposure under
  concurrent runs).

**Verdict format:** per-P1 CLOSED/PARTIAL/NOT + new findings table + merge/deploy verdict
(including whether the deploy steps in the PR body are sufficient and safely ordered).
