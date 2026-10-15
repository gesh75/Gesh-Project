# GESH Financial — Security and Privacy Review

**Date/baseline:** 2026-07-18, `main` at `dc969264`.
**Method:** code review, route/auth tracing, secret-pattern scan, file-mode inspection, Bandit, dependency consistency checks, unauthenticated HTTP probes, public-browser headers/pages and targeted tests. No destructive penetration test was performed.

## Security verdict

The web authentication and tenant-isolation design is credible for a limited beta. No confirmed auth bypass, SQL injection, cross-customer leak or raw-exception API leak was found. The largest risks are around the protected data *outside* the request path: local file permissions, privacy governance, an insecure Android prototype, incomplete session/rate-limit controls, and weak operational recovery.

Public paid launch is **not security/privacy ready** until SEC-01 through SEC-04 are closed.

## Findings

| ID | Severity | Finding | Evidence | Remediation |
|---|---|---|---|---|
| SEC-01 | High | Sensitive financial artifacts are world-readable to local users. | Mode 0644 observed on `gesh_financial.db`, snapshots, generated reports and multiple logs; the DB includes financial/trading history and notification addresses. | Private 0700 data/log dirs, 0600 files, restrictive `umask`, atomic-write chmod, permission tests and migration of existing files. |
| SEC-02 | High | Privacy controls and transparency are absent. | No privacy policy, consent/version ledger, data retention, export or deletion; customer holdings can be sent to Anthropic. | Data map/DPIA, counsel-reviewed privacy notice, vendor/subprocessor disclosure, explicit consent where needed, retention schedule, export and deletion. |
| SEC-03 | High | Android permits cleartext and is not production-authenticated. | `android/GeshFinancial/app/src/main/AndroidManifest.xml` sets `usesCleartextTraffic=true`; `RetrofitClient.kt` defaults to a private HTTP URL; no login/session flow. | Do not distribute this build. Enforce HTTPS/network-security config, secure environment selection, production auth and mobile API authorization tests. |
| SEC-04 | High | Pickle persistence plus permissive file mode creates a local credential/code-execution risk. | `auth.py`/`fetch_live_snapshot.py` load pickle after owner/mode checks, but an inspected session artifact was 0644. | Replace pickle with a non-executable format and OS keychain/token store; meanwhile enforce 0600 and authenticated integrity before deserialization. |
| SEC-05 | Medium | Signed sessions are not individually revocable. | Seven-day cookie has no server-side session/JTI record; logout only removes browser cookie. | Hashed session registry, revocation, rotation, last-used/device view, sign-out-all and short idle timeout for owner. |
| SEC-06 | Medium | Rate limiting is local-memory only. | Login/API limiter state resets on restart and is not shared across workers. | Central durable limiter, trusted proxy handling, event logging, backoff and multi-worker tests. |
| SEC-07 | Medium | CSP allows `unsafe-inline` while dashboard uses numerous HTML sinks. | Observed CSP and `templates/dashboard.html`; static escape wiring test passes but defense in depth is weak. | Nonce/hash CSP, external scripts, DOM-safe render primitives and browser payload suite. |
| SEC-08 | Medium | State-changing unsubscribe endpoints use GET. | `/alerts/unsubscribe`, `/digest/unsubscribe` on current `main`. | Confirmation GET plus CSRF-protected POST; scope/expire/replay-protect tokens. |
| SEC-09 | Medium | External XML parsers lack hardened resource limits. | `news_agent.py` and `sec_agent.py` use ElementTree on third-party responses. | `defusedxml`, strict content-length/time/depth limits and adversarial fixtures. |
| SEC-10 | Medium | Dependency security status is unknown and installed constraints conflict. | `pip-audit` unavailable; `pip check` reports OpenBB incompatibilities with yfinance/FastAPI/uvicorn. | Lock/hashes, clean reproducible build, `pip check` and OSV/pip-audit/Dependabot CI, patch SLA. |
| SEC-11 | Medium | No tested disaster-recovery security controls. | No encrypted off-host backup, restore drill, migration rollback or recovery access procedure. | Encrypted versioned backup with separate credentials, quarterly restore, retention and recovery audit log. |
| SEC-12 | Medium | Logs lack a demonstrated redaction/retention boundary. | High-volume plaintext `/tmp` logs; generated artifacts and AI activity logs are locally readable. | Structured redacted logging, private directory, rotation/retention, no emails/positions in normal logs, access controls. |
| SEC-13 | Medium | Prompt and source-integrity policy is informal. | News/third-party text enters AI prompts; tools are constrained, but no injection/provenance adversarial gate exists. | Delimit untrusted text, forbid instruction following from sources, attach provenance, validate output facts, maintain prompt-injection regression corpus. |
| SEC-14 | Low | CORS/CSRF model relies heavily on browser cookie behavior. | Credentials are disabled for configured CORS, SameSite=Lax is set, but no uniform CSRF-token middleware. | Add explicit origin/CSRF checks to cookie-authenticated mutations and tests for cross-site form/navigation cases. |
| SEC-15 | Informational | Authentication primitives are thoughtfully implemented. | PBKDF2-HMAC-SHA256 200k, HMAC-signed expiring single-use magic links, HttpOnly/SameSite cookies, active-user lookup, generic failures. | Preserve through refactor; add rotation/versioning and external review. |
| SEC-16 | Informational | Customer isolation has strong automated evidence. | Session-derived email scoping, money masking, customer leak sweep and XSS renderer tests all passed. | Keep as required CI and add authenticated browser tenant tests. |
| SEC-17 | Informational | Billing webhook controls are strong. | Raw-body HMAC, constant-time compare, strict variant allowlist, ordering/idempotency and generic error responses. | Add key rotation/runbook and maintain replay tests. |
| SEC-18 | Informational | No committed secrets were found. | Secret-pattern scan and `.gitignore`; `.env` mode 0600. | Add automated secret scanning and documented rotation. |

## Auth and authorization trace

1. Public paths are explicitly allowlisted; other routes enter the auth middleware in `server.py`/`auth.py`.
2. A valid signed session maps to an active user. Customer storage operations derive the tenant key from that identity rather than trusting a request email.
3. Owner-only routes use owner/API-key checks. Customer tests assert absence of owner dollars and inaccessible owner APIs.
4. Billing webhook is public by necessity but verifies the raw body before parsing/provisioning.

The architecture is **fail-closed for ordinary web access**. Remaining work is lifecycle hardening—not a rewrite of the authentication system.

## Data inventory and privacy boundary

| Data | Location/use | Concern |
|---|---|---|
| Email, tier, subscription identifiers | `gesh_auth.db` | Personal/billing metadata; mode 0600 but no retention/export/delete process. |
| Customer book/watchlist/preferences | `gesh_auth.db` | Financial-interest data; sent into some analysis/notification flows. |
| Owner holdings, equity and trade history | `gesh_financial.db`, JSON/HTML snapshots | Highly sensitive; several artifacts mode 0644. |
| AI prompts/outputs | Transmitted to model vendors; results cached/stored | Usage log avoids prompt content, but vendor disclosure/consent/retention is missing. |
| Payment data | Lemon Squeezy hosted checkout | Application appears not to store card data; verify production configuration and contractual roles. |
| Email delivery | Resend | Subprocessor disclosure and retention required. |

## Static and runtime security evidence

- Bandit: 1 High, 38 Medium, 67 Low. The High SHA-1 use is non-authentication idempotency. Relevant Medium findings were manually triaged above.
- No successful dependency vulnerability audit: classify vulnerability status as **Not verified**, not clean.
- Public responses exposed HSTS at the edge, CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and a referrer policy.
- Local unauthenticated protected requests returned 401 or login redirect.
- Both SQLite integrity checks returned `ok`; integrity is not confidentiality or recoverability.

## Security launch gate

Before any public paid launch, require evidence for all of the following:

- [ ] SEC-01/02/03/04 closed with tests and migration evidence.
- [ ] Threat model covers visitor, customer, owner, provider, billing webhook, scheduled jobs and local-host compromise.
- [ ] Dependency and secret scans run in CI with no unresolved Critical/High issues.
- [ ] Authenticated browser tests prove two-customer isolation and owner denial.
- [ ] Backup restore succeeds into an isolated host and secrets are rotated independently.
- [ ] Incident response lists detection, containment, notification and recovery owners.
- [ ] Privacy/terms versions and consent records are queryable.
- [ ] Logs demonstrate redaction and retention, and sensitive files are 0600.
