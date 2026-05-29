# Progress

_Last updated: 2026-05-28_

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

## Done — Operational fixes
- 9000 service was running 6-day-old code → restarted; "sonnet" error cleared.
- 9003 `Unexpected token 'I'` (dry-run plain-text 500) → fixed + verified clean
  in browser (Playwright): both dashboards load, all `/api/*` return 200,
  `/api/config` without key → 401 (auth confirmed live).
- Telegram alerts dead ~3h → malformed token auto-repaired; test message
  delivered; autopilot restarted. Alerts confirmed flowing by Georgi.

## Pending / known
- Old revoked Telegram token remains in **git history** of both repos (dead, so
  low risk). History rewrite is optional.
- `gesh-project-trading/crypto_data.py` has an unrelated uncommitted change
  (left untouched this session).
- No open CRITICAL/HIGH/MEDIUM/LOW from the 2026-05-28 audit.
