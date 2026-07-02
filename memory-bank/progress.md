# Progress

_Last updated: 2026-07-02_

## Done — Money-path audit + remediation (2026-07-02)

Principal-level audit (6 dimensions) of consensus/kelly/market_data/risk_bands/
ai_analyzer + execution tier, then implemented all accepted fixes. Uncommitted.

- **PR1 (CRITICAL)** Paper-book ledger: `paper_open_positions()` in gesh-core
  state; paper SELL/BUY sizing reads the paper book, not real RH holdings —
  fixes "0 closed trades / phantom qty=0" (execution.py:207 SKIPPED path).
- **PR2 (CRITICAL)** Working stop path: `paper_stops` table + record/active/
  deactivate; execution records stops on paper BUY fills; close_risk_check
  sweeps paper book; stop SELLs now pass `validate()` (target 0.0).
- **PR3 (HIGH)** price_source `_usable_rows()` — all-NaN Yahoo-429 batches now
  trigger the RH fallback and are never cached; RH session memo retries after
  300s; `_spy_above_ma`/fft_cycles/screen_market routed through price_source.
- **PR4 (HIGH, reconciled)** Conviction gating via `min_entry_score` raw-score
  floor ported into product autopilot (08:30 + 09:35). First attempt blended
  conviction into `from_consensus` confidence — broke engine-repo
  test_entry_conviction_floor; reverted to participation-only (shared contract).
- **PR5 (HIGH)** Kelly node gets SIMPLE returns (was log — understated mu by
  ~sigma²/2); RSI saturates to 100 on all-gains windows; OHLCV cache row-aligned.
- **PR6** Candidates persisted via job marker; atomic temp+replace writes in
  run_ai2/run_engines2/macro_connector; fresh-DB creation warning in db_path.
- **PR7** All Anthropic clients bounded (240s/2 retries); compact JSON payloads;
  screener prompt contradiction fixed (`SCREENER_PICK_COUNT=6`); screener on
  Haiku; usage log + optional `LLM_DAILY_BUDGET_USD` guard.
- market_data: 1 history fetch per symbol (was 5); enrichment 4 workers +
  `enrich_error` tags.
- New tests: test_paper_book (8), test_autopilot_stop_sweep (4),
  test_price_source_nan_guard (3), test_consensus_money_fixes (4),
  test_market_data_returns (4), test_autopilot_entry_floor (3) + gesh-core
  contract semantics test. Suites: product 361 ✓, gesh-core 168 ✓.
- **Not done (deferred)**: PR8 dual-repo autopilot extraction into gesh-core
  (L effort); trading-repo full suite hangs on network-bound tests (pre-existing).

## Done — Security audit & remediation (2026-05-28)

Audited `gesh-project` high-risk files (auth, server endpoints, data/money layer,
live fetch/autopilot) with parallel `security-reviewer` agents. Findings:
**3 CRITICAL, 11 HIGH, 8 MEDIUM, 8 LOW** — all remediated.

### CRITICAL
- Scrubbed committed Telegram bot token from `trading/alerts.py` (both repos).
- API-key auth + input validation on `/api/config` and all trade endpoints.
- `chmod 600` + `.gitignore` for the Robinhood session token file and live
  portfolio JSON caches.

### HIGH (selected)
- DATA_FRESH gate fails closed; `as_of` stamped in `build_data_payload`.
- ORDER_RATE excludes REJECTED orders; retry-safe idempotency key.
- Generic 500 handler; per-IP rate limiting; session-file owner/perm guard
  before deserialization.
- yfinance interval/period + ticker symbol whitelisting; glob path guard.

### MEDIUM/LOW
- Market-hours fail-closed; kill-switch path no longer leaked; `limit` caps;
  PII removed from logs; explicit CORS; env-var parse hardening; symbol length.

### Commits / pushes
- `gesh-project` → `7841ea8` (pushed to origin/main).
- `gesh-project-trading` → `3dccff3` dry-run JSON fix + token scrub (pushed).

## Done — Git history scrub + lost-code recovery (2026-05-29)

- **Purged the dead Telegram token from git history** of both repos with
  `git filter-repo --replace-text` (regex on the token shape, secret never
  handled). Verified gone: `git log --all -S` + full-history grep both empty.
  Force-pushed: gesh-project `e9a0f6e→dafcf9c`, gesh-project-trading
  `3dccff3→30e1ce5`. Deleted stale remote branch
  `claude/ai-financial-observability-tool-8uo6w`. Backups: `/tmp/*.bundle`.
- **Side effect + full recovery:** `filter-repo --force` reset the trading
  working tree, discarding the uncommitted `crypto_data.py` edit (no reflog/
  stash/blob — unrecoverable via git). Recovered it from the running 9003
  service's cached bytecode `__pycache__/crypto_data.cpython-314.pyc`:
  disassembled, reconstructed the source, and verified **byte-for-byte
  equivalent** (600/600 instrs). Committed `daf71ee` + pushed.
  - Restored edit: POL `MATIC-USD→POL-USD`, UNI `UNI1-USD→UNI-USD`, and a
    CoinGecko OHLC fallback in `fetch_ohlcv` (free tier, ≤365d) for when a
    Yahoo ticker delists/misresolves.
- 9003 service restarted (PID 38854); recovered module imports clean.

## Done — Operational fixes
- 9000 service was running 6-day-old code → restarted; "sonnet" error cleared.
- 9003 `Unexpected token 'I'` (dry-run plain-text 500) → fixed + verified clean
  in browser (Playwright): both dashboards load, all `/api/*` return 200,
  `/api/config` without key → 401 (auth confirmed live).
- Telegram alerts dead ~3h → malformed token auto-repaired; test message
  delivered; autopilot restarted. Alerts confirmed flowing by Georgi.

## Pending / known
- (done 2026-05-29) Old Telegram token purged from git history of both repos;
  force-pushed. GitHub may still surface old commits by direct SHA until it
  GCs — harmless since the token is revoked.
- (done 2026-05-29) `gesh-project-trading/crypto_data.py` recovered + committed
  (`daf71ee`).
- No open CRITICAL/HIGH/MEDIUM/LOW from the 2026-05-28 audit.

## Lesson — never run `filter-repo --force` on a dirty tree
It silently discards uncommitted tracked changes (no git recovery path).
Always `git stash`/commit working-tree edits first. This time the running
service's cached `.pyc` saved us — won't always be available.
