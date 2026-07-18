# GESH Financial — Launch Readiness Checklist

**Date:** 2026-07-18
**Overall Status:** ⚠ PARTIALLY READY (resolve P0 items before continuing paid service)

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
| Authentication enforced on all protected routes | ✅ Ready | Auth gate middleware L397-411; API key on mutating endpoints | |
| Passwords hashed securely | ✅ Ready | PBKDF2-HMAC-SHA256, 200K rounds | |
| Session tokens signed and expiry-checked | ✅ Ready | HMAC-SHA256, 7-day TTL | |
| Brute-force protection | ✅ Ready | 8 failures / 5 min per IP+email | |
| Rate limiting on API endpoints | ✅ Ready | Per-IP, heavy (30/min) vs normal (300/min) | |
| HTTPS enforced | ✅ Ready | Cloudflare tunnel; HSTS header set | |
| CSP headers | ✅ Ready | Restrictive CSP with connect-src 'self' | |
| XSS prevention | ✅ Ready | CSP + template escaping; tested in test_dashboard_xss_wiring.py | |
| Customer data isolation | ✅ Ready | Model-book masking; tested in test_customer_leak_sweep.py (24KB) | |
| Webhook signature verification | ✅ Ready | HMAC-SHA256 with constant-time compare | |
| Dependency vulnerability scanning | ❌ Not Ready | No pip-audit or bandit in CI | Add to CI pipeline |
| Server-side session revocation | ❌ Not Ready | Stateless tokens; no forced logout capability | Add session invalidation |
| CSRF protection | ⚠ Partially | JSON-only + CORS + SameSite=lax; no explicit CSRF tokens | Consider SameSite=Strict |

---

## Authentication

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Login with password | ✅ Ready | auth_login() with PBKDF2 verification | |
| Magic link login | ✅ Ready | Single-use, purpose-typed tokens | |
| Logout | ✅ Ready | Cookie deletion | |
| Session expiration | ✅ Ready | 7-day TTL | |
| Password reset | ❌ Not Ready | No password reset flow | Magic link serves as recovery |
| Account deletion | ❌ Not Ready | No deletion mechanism | GDPR requirement |

---

## Authorization

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Owner vs customer role separation | ✅ Ready | is_owner() check; owner-only paths gated | |
| Premium content enforcement (backend) | ✅ Ready | Auth middleware rejects unauthenticated API calls | |
| API key protection for mutating endpoints | ✅ Ready | require_api_key with HMAC compare | |
| Customer quota enforcement | ✅ Ready | Per-customer usage tracking + quotas | |
| Admin panel access control | ⚠ Partially | No admin panel; user management via CLI only | Consider admin UI for P2 |

---

## Financial Accuracy

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Market value calculation | ✅ Ready | qty × price; verified in tests | |
| Consensus engine correctness | ✅ Ready | 10+ consensus test files; regime-weighted | |
| Kelly criterion correctness | ✅ Ready | test_kelly.py (8KB) + test_kelly_invariants.py (11KB) | |
| Monte Carlo correctness | ✅ Ready | EWMA vol; GBM; calibration tests | |
| Paper trading P&L | ✅ Ready | Correct realized P&L calculation; tested | |
| Model-book masking accuracy | ✅ Ready | 24KB leak sweep test; recursive scaling | |
| Customer book valuation | ✅ Ready | 52KB test coverage in test_mybook.py | |
| AI hallucination guardrails | ⚠ Partially | Structured prompts + forced JSON; no output validation | Add validation |

---

## Market Data Reliability

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Primary data source (yfinance) | ⚠ Partially | Works but fragile (crumb fragility, rate limits) | Add circuit breaker |
| Fallback data source (Robinhood) | ⚠ Partially | price_source.py provides fallback; robin_stocks is undocumented | |
| Offline mode / snapshot fallback | ✅ Ready | load_snapshot_payload() with age validation | |
| Stale data detection | ✅ Ready | MAX_PRICE_AGE_SECONDS, stale_snapshot flag | |
| Price anomaly detection | ✅ Ready | price_sanity.py cross-source check | |
| Data source attribution | ⚠ Partially | provenance block on ticker cards; inconsistent elsewhere | Expand to all endpoints |

---

## Billing

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Checkout flow | ✅ Ready | Hosted Lemon Squeezy checkout; no card data touches server | |
| Webhook processing | ✅ Ready | HMAC-verified, idempotent, claim/reclaim state machine | |
| Account provisioning on payment | ✅ Ready | Create user + magic link on subscription_created | |
| Account revocation on churn | ✅ Ready | Only on subscription_expired (not payment_failed) | |
| Payment failure handling | ✅ Ready | Past-due status; no premature revocation | |
| Subscription cancellation | ✅ Ready | Access continues to period end | |
| Test mode vs production | ✅ Ready | Launch_status.json shows live store + live webhook | |
| Refund policy | ❌ Not Ready | No refund terms stated anywhere | Must add |
| Customer invoice access | ❌ Not Ready | No link to LS customer portal | Add link |

---

## Privacy

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Privacy policy | ❌ Not Ready | No /privacy route exists | Must create |
| Cookie disclosure | ❌ Not Ready | Session cookie set without consent | Must add |
| Data retention policy | ❌ Not Ready | No documented retention periods | Must define |
| Account deletion | ❌ Not Ready | No self-service deletion | Must implement |
| Data export / portability | ❌ Not Ready | No export mechanism | Must implement |
| Consent for financial data processing | ❌ Not Ready | Implicit only (no explicit consent) | Must add |

---

## Compliance

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Terms of Service | ❌ Not Ready | Labeled "DRAFT — not yet legally reviewed" | Must finalize with attorney |
| "Not financial advice" disclaimers | ✅ Ready | Present on all public pages, emails, and analysis output | |
| Hypothetical performance disclaimers | ✅ Ready | CFTC-style language on track record page | |
| Risk disclosure | ✅ Ready | ToS §5 + §6 | |
| AI disclosure | ✅ Ready | AI-generated timestamps + model attribution | |

---

## Monitoring

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Health check endpoint | ✅ Ready | GET /healthz returns {status, auth} | Expand with dependency checks |
| Error tracking | ❌ Not Ready | No APM or error tracking service | Add Sentry or equivalent |
| Uptime monitoring | ❌ Not Ready | No external uptime check | Add uptime monitor |
| Log aggregation | ❌ Not Ready | Logs to stdout/file only | Add structured logging |
| Performance monitoring | ❌ Not Ready | No response time tracking | Add APM |

---

## Backups

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Automated database backup | ❌ Not Ready | No backup process exists | Must implement |
| Backup verification | ❌ Not Ready | No backup testing | Must implement |
| Documented restore procedure | ❌ Not Ready | No restore documentation | Must document |
| Backup encryption | ❌ Not Ready | Not applicable until backups exist | Implement with backups |

---

## Testing

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Unit test suite | ✅ Ready | 1,214 tests pass (74s), 93 test files | |
| CI pipeline | ✅ Ready | GitHub Actions with 2 jobs; customer-isolation gate | |
| Coverage threshold | ⚠ Partially | 55% floor; target is 70-80%+ | Raise to 70% |
| Security tests | ⚠ Partially | No bandit/pip-audit in CI; XSS + leak sweep in tests | Add security lint |
| E2E / browser tests | ❌ Not Ready | No Playwright/Selenium tests | Consider adding |
| Load / performance tests | ❌ Not Ready | No load testing | Consider adding |

---

## Customer Support

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Support contact | ⚠ Partially | Owner email in ToS; no support page | Add support contact in dashboard |
| FAQ / documentation | ⚠ Partially | docs/index.html exists; no customer-facing docs | Add customer help docs |
| Issue reporting | ❌ Not Ready | No in-app feedback or issue reporting | Consider adding |

---

## Performance

| Item | Status | Evidence | Notes |
|---|---|---|---|
| API response times | ❓ Not Verified | No performance monitoring data | Verify under load |
| Dashboard load time | ❓ Not Verified | 303KB HTML file; no bundle optimization | Verify; consider code splitting |
| Background job reliability | ⚠ Partially | APScheduler-based; no job monitoring dashboard | Add job health visibility |
| Database query performance | ⚠ Partially | SQLite with WAL; indexes on key tables | Verify under concurrent load |

---

## Mobile Experience

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Responsive design | ❓ Not Verified | No responsive testing evidence | Test critical flows on mobile |
| Touch-friendly UI | ❓ Not Verified | Dashboard designed for desktop | Verify touch targets |
| Mobile viewport | ❓ Not Verified | meta viewport tag present | Test at 375px / 414px |

---

## Production Deployment

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Production server binding | ✅ Ready | 127.0.0.1 only; tunnel for external | |
| HTTPS termination | ✅ Ready | Cloudflare tunnel | |
| Process management | ⚠ Partially | launchd plists; no Docker/systemd | Add container option |
| Rollback procedure | ❌ Not Ready | No documented rollback process | Must document |
| Deployment documentation | ⚠ Partially | README.md + setup.sh; incomplete for production | Expand for production |
