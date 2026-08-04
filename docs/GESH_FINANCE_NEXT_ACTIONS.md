# GESH Financial — Next Actions

**Date:** 2026-07-18 · **Reconciled:** 2026-08-04  
**Audit verdict:** Late-Alpha / Early-Beta — legal counsel + monitoring still gate confident charging

> Prefer [`GAPS_AND_IMPROVEMENTS_2026-08-04.md`](../GAPS_AND_IMPROVEMENTS_2026-08-04.md) for the living priority list.

---

## Top Risks (current)

| Risk | Severity | Impact if Unaddressed |
|---|---|---|
| **Terms of Service is DRAFT** while charging | **Critical** | Legal liability; unenforceable terms |
| **Privacy Policy is DRAFT** (page exists) | **Critical** | GDPR/CCPA / trust — counsel must finalize |
| **No production error tracking** | **High** | Blind between Telegram chain alerts |
| **Autonomy tracker may not accumulate** | **High** | Live-trading gate stuck; proof page empty |
| **Silent Claude snapshot fallback** | **High** | Unexpected LLM spend if `GESH_RH_ACCOUNT` missing |
| **Robinhood / Yahoo fragility** | **High** | Undocumented / rate-limited feeds |
| **gesh-core private dependency** | **Medium** | CI PAT friction |
| **SQLite single-writer** | **Medium** | Scale ceiling under concurrent customers |

### Closed as engineering P0 (do not re-open as “missing”)
- Automated DB backups — `ops_backup.py` + launchd  
- Account deletion + export — `/api/account/delete`, `/api/account/export`  
- Privacy **route** — `/privacy` (content still DRAFT)

---

## P0 — Immediate

| # | Task | Acceptance Criteria | Status |
|---|---|---|---|
| P0-1 | Finalize Terms of Service with attorney | DRAFT removed; dated; refund terms | Open (counsel) |
| P0-2 | Finalize Privacy Policy | Attorney-reviewed text at `/privacy` | Open (counsel); route ✅ |
| P0-3 | Automated DB backup | Nightly integrity-checked copies | **Done** |
| P0-4 | Account deletion | Self-service delete + confirm | **Done** |
| P0-5 | Monitoring / alerting | Sentry (or equiv) + uptime | Open |
| P0-6 | Host tracker write-path | Non-empty `gesh_tracker.db`; closes increment | Open (host verify) |

---

## P1 — Before confident paid launch

| # | Task | Status |
|---|---|---|
| P1-1 | Complete gesh-core or monorepo | Open |
| P1-2 | Coverage → 70%+ | Floor 55% aligned in CI (2026-08-04); raise next |
| P1-3 | DataProvider abstraction | Partial |
| P1-4 | Split `server.py` | Open (~6.5k lines) |
| P1-5 | Docker deployment | Dockerfile added 2026-08-04; compose later |
| P1-6 | Cookie disclosure | Login disclosure added 2026-08-04 |
| P1-7 | Data export | **Done** |
| P1-8 | Refund policy | Open |
| P1-9 | pip-audit + bandit in CI | Added 2026-08-04 |
| P1-10 | Empty-state for new customers | Verify / polish |

---

## Recommended sequence (updated)

```
Counsel (P0-1, P0-2, P1-8)  ||  Host: tracker + snapshot env (P0-6)
        ↓
Sentry / uptime (P0-5)
        ↓
Coverage 60→70 · server.py split · compose · DataProvider
```

---

## What's Working Well (Don't Touch These)

- Billing webhook — idempotent, HMAC-verified, race-safe  
- Auth — PBKDF2, typed tokens, brute-force protection  
- Customer isolation — published model portfolio + leak-sweep gate  
- Risk gate — persisted decisions; paper-first  
- Consensus / Kelly / MC engines + LLM gateway contracts  
- Nightly verified SQLite backups  
- CI required checks on same-repo PRs + whole-repo syntax gate
