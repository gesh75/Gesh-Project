# GESH Financial — Launch Readiness Checklist

> **Canonical.** A second, independently written audit of the same 2026-07-18 baseline (`dc969264`) formerly shadowed this file at the repo root; it now lives at [`docs/audits/second-pass-2026-07-18/GESH_FINANCE_LAUNCH_CHECKLIST.md`](audits/second-pass-2026-07-18/GESH_FINANCE_LAUNCH_CHECKLIST.md) (moved 2026-10-15). It was never reconciled after 2026-07-19; its IDs do not map to the ones here.

**Date:** 2026-07-18 · **Reconciled:** 2026-08-04  
**Overall Status:** ⚠ PARTIALLY READY (attorney ToS/Privacy + monitoring + autonomy proof remain)

> Living gaps: [`GAPS_AND_IMPROVEMENTS_2026-08-04.md`](../GAPS_AND_IMPROVEMENTS_2026-08-04.md)

---

## Legend

| Status | Icon | Meaning |
|---|---|---|
| Ready | ✅ | Meets launch requirements |
| Partially Ready | ⚠ | Functional but has gaps |
| Not Ready | ❌ | Does not meet minimum requirements |
| Not Verified | ❓ | Cannot confirm without live testing |

---

## Security

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Authentication enforced on all protected routes | ✅ Ready | Auth gate middleware; API key on mutating endpoints | |
| Passwords hashed securely | ✅ Ready | PBKDF2-HMAC-SHA256, 200K rounds | |
| Session tokens signed and expiry-checked | ✅ Ready | HMAC-SHA256, 7-day TTL | |
| Brute-force protection | ✅ Ready | 8 failures / 5 min per IP+email | |
| Rate limiting on API endpoints | ✅ Ready | Per-IP, heavy vs normal buckets | |
| HTTPS enforced | ✅ Ready | Cloudflare tunnel; HSTS header set | |
| CSP headers | ✅ Ready | Restrictive CSP with connect-src 'self' | |
| XSS prevention | ✅ Ready | CSP + template escaping; XSS wiring + report tests | |
| Customer data isolation | ✅ Ready | Model-book / published portfolio; leak-sweep CI gate | |
| Webhook signature verification | ✅ Ready | HMAC-SHA256 with constant-time compare | |
| Dependency vulnerability scanning | ⚠ Partial | `pip-audit` + `bandit` CI job added 2026-08-04 | Triage high findings |
| Server-side session revocation | ❌ Not Ready | Stateless tokens; no forced logout capability | Add session invalidation |
| CSRF protection | ⚠ Partially | JSON-only + CORS + SameSite=Lax; Origin checks on mutations | |

---

## Authentication

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Login with password | ✅ Ready | auth_login() with PBKDF2 verification | |
| Magic link login | ✅ Ready | Single-use, purpose-typed tokens | |
| Logout | ✅ Ready | Cookie deletion | |
| Session expiration | ✅ Ready | 7-day TTL | |
| Password reset | ⚠ Partial | Magic link is the recovery path; disclosed on login | Document as intentional |
| Account deletion | ✅ Ready | `POST /api/account/delete` (confirm body); owner blocked | Was ❌ in Jul-18 audit |

---

## Authorization

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Owner vs customer role separation | ✅ Ready | is_owner(); owner console slot composition | |
| Premium content enforcement (backend) | ✅ Ready | Auth middleware rejects unauthenticated API calls | |
| API key protection for mutating endpoints | ✅ Ready | require_api_key with HMAC compare | |
| Customer quota enforcement | ✅ Ready | Per-customer usage + tier multipliers | |
| Admin panel access control | ⚠ Partially | CLI user management (`auth_users.py`) | Admin UI is P2 |

---

## Financial Accuracy

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Market value calculation | ✅ Ready | qty × price; verified in tests | |
| Consensus engine correctness | ✅ Ready | Extensive consensus tests; regime-weighted | |
| Kelly criterion correctness | ✅ Ready | Kelly + invariants + portfolio-reconciled path | |
| Monte Carlo correctness | ✅ Ready | EWMA vol; GBM; calibration tests | |
| Paper trading P&L | ✅ Ready | Realized P&L tested | |
| Model-book masking accuracy | ✅ Ready | Leak sweep + mask-exempt account paths | |
| Customer book valuation | ✅ Ready | test_mybook* suite | |
| AI hallucination guardrails | ⚠ Partially | LLM contracts/gateway (2026-08); continue hardening | See `docs/LLM_OPERATIONS.md` |

---

## Market Data Reliability

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Primary / fallback quotes | ⚠ Partially | price_source + cache + sanity guards | Full DataProvider abstraction still open |
| Offline mode / snapshot fallback | ✅ Ready | MCP-first chain + snapshot age checks | |
| Stale data detection | ✅ Ready | MAX_PRICE_AGE_SECONDS, stale flags | |
| Price anomaly detection | ✅ Ready | price_sanity + bad-tick guards | |
| Direct MCP snapshot tier | ✅ Ready | `GESH_RH_ACCOUNT` via env/.env | Alert if silent LLM fallback |

---

## Billing

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Checkout / webhook / provision / revoke | ✅ Ready | Lemon Squeezy HMAC + idempotency | |
| Past-due grace | ✅ Ready | Revoke only on subscription_expired | |
| Refund policy | ❌ Not Ready | Still need explicit refund terms | Attorney / ToS |
| Customer invoice access | ❌ Not Ready | No LS portal link yet | Add link |

---

## Privacy

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Privacy policy page | ⚠ Partial | `/privacy` live; content still **DRAFT** pending attorney | Was ❌ “no route” |
| Cookie disclosure | ⚠ Partial | Login page discloses session cookie + links Privacy (2026-08-04) | Formal consent banner optional |
| Data retention policy | ❌ Not Ready | No documented retention periods | Must define |
| Account deletion | ✅ Ready | Self-service delete endpoint | |
| Data export / portability | ✅ Ready | `GET /api/account/export` | Was ❌ |
| Consent for financial data processing | ❌ Not Ready | Implicit only | Counsel |

---

## Compliance

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Terms of Service | ❌ Not Ready | Still labeled DRAFT — attorney review required | Do not remove banner without counsel |
| "Not financial advice" disclaimers | ✅ Ready | Public pages, emails, analysis | |
| Hypothetical performance disclaimers | ✅ Ready | Track-record page | |
| Risk / AI disclosure | ✅ Ready | ToS sections + model attribution | |

---

## Monitoring

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Health check endpoint | ✅ Ready | `GET /healthz` — status, auth, uptime_seconds | |
| Error tracking | ❌ Not Ready | No Sentry/APM | Add when DSN available |
| Uptime monitoring | ❌ Not Ready | No external uptime check | |
| Log aggregation | ❌ Not Ready | /tmp + launchd logs | |
| Performance monitoring | ❌ Not Ready | No APM | |

---

## Backups

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Automated database backup | ✅ Ready | `ops_backup.py` + `com.gesh.backup` (02:30) | Was ❌ |
| Backup verification | ✅ Ready | PRAGMA integrity_check per copy | |
| Documented restore procedure | ✅ Ready | Docstring in `ops_backup.py` | |
| Path portability | ✅ Ready | `GESH_HOME` / `GESH_TRADING_HOME` (no hardcoded /Users) | 2026-08-04 |
| Backup encryption | ❌ Not Ready | Filesystem perms 0600/0700 only | Optional at-rest encryption |

---

## Testing

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Unit test suite | ✅ Ready | Large offline suite; customer-isolation gate | |
| CI pipeline | ✅ Ready | PR required checks + syntax gate (post-#143) | |
| Coverage threshold | ⚠ Partially | Floor **55%** (CI aligned 2026-08-04); target 70–80% | |
| Security tests | ⚠ Partially | Leak/XSS + pip-audit/bandit job | |
| E2E / browser / load tests | ❌ Not Ready | | |

---

## Production Deployment

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Production server binding | ✅ Ready | 127.0.0.1; tunnel for external | |
| HTTPS termination | ✅ Ready | Cloudflare tunnel | |
| Process management | ⚠ Partially | launchd; Dockerfile added 2026-08-04 | Compose still open |
| Rollback / deploy docs | ⚠ Partially | README + OPS_LAYER | Expand |

---

## Remaining launch blockers (honest)

1. Attorney-final ToS + Privacy (remove DRAFT).  
2. Error tracking (Sentry or equivalent).  
3. Autonomy tracker accumulating real closed paper trades on the host.  
4. Refund policy language.
