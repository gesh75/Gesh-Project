# GESH Financial — Security Review

**Date:** 2026-07-18 | **Methodology:** Static code analysis + architecture review

---

## Security Posture Summary

The GESH codebase has undergone **multiple security audits** (Codex rounds 1-4, GPT-5.6, Grok, Fable) and shows strong evidence of security-aware engineering. The security posture is **above average for an alpha/beta product**, with several notable strengths:

- HMAC-signed session tokens with constant-time comparison
- PBKDF2 with 200K rounds for password hashing
- Content Security Policy (CSP) with restricted connect-src/script-src
- HSTS on HTTPS connections
- Per-IP rate limiting with separate heavy/normal buckets
- Login brute-force throttling (8 failures / 5 min window)
- Customer data isolation via model-book masking (tested by dedicated CI gate)
- Fail-closed patterns throughout (billing, auth, price validation)
- Generic error responses (never leaks internal details)
- XSS prevention tested in `test_dashboard_xss_wiring.py` (31KB)

---

## Security Register

| ID | Severity | Vulnerability | Evidence | Potential Impact | Remediation |
|---|---|---|---|---|---|
| SEC-01 | **Medium** | Owner email hardcoded as fallback | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L699: `os.getenv("GESH_OWNER_EMAIL", "georgy75@gmail.com")` | Email disclosure if env var unset | Move to required env var; fail if unset when auth enabled |
| SEC-02 | **Medium** | SQLite database files world-readable by default | gesh_auth.db, gesh_financial.db have no OS-level permission hardening | Password hashes, session data exposed to local users | Set 0600 file permissions on DB files at creation |
| SEC-03 | **Low** | CORS allows localhost:9000 but credentials=False | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L634-640 | Appropriate for current deployment (local + tunnel) | Tighten to production domain when public |
| SEC-04 | **Informational** | CSP allows 'unsafe-inline' for scripts | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L606-614 | Required for current inline script architecture | Migrate to nonce-based CSP when frontend is refactored |
| SEC-05 | **Medium** | No CSRF protection on POST endpoints | POST /auth/login, /api/mybook etc. rely on JSON-only bodies + CORS | State-changing actions could be forged via DNS rebinding | Add SameSite=Strict on auth cookie or implement CSRF tokens |
| SEC-06 | **Low** | Session token has no server-side revocation | Tokens are stateless HMAC-signed; logout only deletes the cookie | A stolen token remains valid until expiry (7 days) | Add server-side session invalidation table |
| SEC-07 | **Medium** | No pip-audit or dependency vulnerability scanning in CI | [tests.yml](file:///Users/georgigaydarov/Projects/gesh-project/.github/workflows/tests.yml) — no security lint step | Known CVEs in dependencies may go undetected | Add pip-audit and/or bandit to CI pipeline |
| SEC-08 | **Low** | Rate limiter is in-process (single-worker) | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L220-265 | Restarting the server resets all rate limit counters | Acceptable for single-instance; use Redis if scaling |
| SEC-09 | **Low** | .env file in project root | .env with credentials present in repo root | Should be gitignored (verified: .gitignore includes .env) | Verified: `.gitignore` includes `.env` — no action needed |
| SEC-10 | **Informational** | `gesh_auth.secret` file stores HMAC key | [auth_users.py](file:///Users/georgigaydarov/Projects/gesh-project/auth_users.py) L76-83 | Auto-generated, 0600 permissions set | Acceptable for self-hosted; document in ops guide |
| SEC-11 | **Medium** | Webhook signature uses `hmac.new` (not `hmac.HMAC`) | [billing.py](file:///Users/georgigaydarov/Projects/gesh-project/billing.py) L87 | This is Python's `hmac.new()` — functionally correct, but unusual capitalization | Verify this is the correct API call (it is: `hmac.new` = `hmac.HMAC`) |
| SEC-12 | **Low** | No Content-Type validation on JSON endpoints | POST endpoints parse `await request.json()` without checking Content-Type header | Could receive unexpected content types | Add Content-Type validation middleware |
| SEC-13 | **Medium** | AI prompt injection risk via user-supplied symbols | Ticker symbols are validated via regex but user text in customer book positions flows to AI prompts | Crafted "symbols" could inject prompt instructions | Symbols are regex-validated (`^[A-Z0-9.\-=^]{1,15}$`) — actual risk is low |
| SEC-14 | **Low** | Pickle session for Robinhood (robin_stocks) | auth.py uses pickle-based session storage | Pickle deserialization can execute arbitrary code if file is tampered | Accepted risk from robin_stocks library; not user-facing |
| SEC-15 | **Informational** | Server binds 127.0.0.1 only | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L4097 | Correct — never binds 0.0.0.0; tunnel handles external access | No action needed |
| SEC-16 | **Low** | Magic link token TTL = 15 minutes | MAGIC_LINK_TTL_SECONDS = 15 * 60 in billing.py | Appropriate duration; single-use enforced | No action needed |

---

## Authentication Security Assessment

| Control | Implementation | Status |
|---|---|---|
| Password hashing | PBKDF2-HMAC-SHA256, 200K rounds, 16-byte salt | ✅ Strong |
| Session tokens | HMAC-signed with SHA256, expiry-checked | ✅ Strong |
| Constant-time comparison | `hmac.compare_digest()` used throughout | ✅ Strong |
| Magic link single-use | `consume_magic_token()` atomically spends token | ✅ Strong |
| Token purpose binding | `typ` field in token prevents session↔magic confusion | ✅ Strong |
| Brute-force protection | Per-IP + per-(IP,email) throttling, 8/5min | ✅ Strong |
| Cookie security | httpOnly=True, samesite=lax, secure=True on HTTPS | ✅ Strong |
| Referrer-Policy on magic link | `no-referrer` header on redirect | ✅ Strong |
| PBKDF2 on event loop | Offloaded to `asyncio.to_thread` | ✅ Good design |

---

## Data Privacy Assessment

| Data Type | Storage | Protection | Risk |
|---|---|---|---|
| Owner portfolio (RH positions) | In-memory (data_fetcher) + live_portfolio_snapshot.json | Customer sees model-book masked version | Low — masking is well-tested |
| Customer positions | gesh_auth.db:customer_positions | Per-user isolation by email key | Low — tested in test_mybook.py |
| Customer analyses | gesh_auth.db:customer_analyses | Per-user isolation by email | Low |
| Usage events | gesh_auth.db:usage_events | Per-user; no PII in event detail | Low |
| Passwords | gesh_auth.db:users.password_hash | PBKDF2 hashed, never stored in plaintext | Low |
| Email addresses | gesh_auth.db:users.email | Stored in plaintext (required for login) | Medium — no encryption at rest |
| Financial data in browser | Session cookie only; no localStorage | httpOnly cookie prevents JS access | Low |
| AI prompts/responses | Not persisted beyond usage log | Token counts + cost only in llm_usage.jsonl | Low |
| Webhook payloads | Dedup table stores hash only, not payload | No customer financial data stored from webhooks | Low |

---

## Dependency Risk Assessment

| Package | Version | Risk | Notes |
|---|---|---|---|
| robin-stocks | 3.4.0 | **High** | Undocumented API wrapper; pickle session; can break without notice |
| yfinance | 1.2.1 | **Medium** | Scraping-based; rate limited; crumb fragility |
| anthropic | 0.93.0 | Low | Official SDK; well-maintained |
| fastapi | 0.135.3 | Low | Major framework; actively maintained |
| openbb | ≥4.3.0 | Medium | Heavy dependency; CI strips it; Pydantic deprecation warnings |
| praw (Reddit) | 7.8.1 | Low | Optional; OAuth-based |
| gesh-core | v0.5.2 (git) | **Medium** | Private repo; CI requires PAT; installation friction |

---

## Recommendations Priority

### Immediate (P0)
1. Add pip-audit to CI pipeline
2. Document SQLite file permissions in deployment guide

### Short-term (P1)
3. Add server-side session invalidation for forced logout
4. Add Content-Type validation middleware
5. Consider SameSite=Strict for auth cookie

### Medium-term (P2)
6. Migrate to nonce-based CSP (remove 'unsafe-inline')
7. Add bandit static analysis to CI
8. Implement rate limit persistence across restarts (Redis or SQLite)
