# GESH Financial — Launch Readiness Checklist

**Assessment date:** 2026-07-18; revision `dc969264`.
**Allowed statuses:** Ready, Partially ready, Not ready, Not verified.
**Decision:** demo ready; selected beta conditionally ready after P0 closure; public paying launch not ready.

## Product and customer experience

| Gate | Status | Evidence / release condition |
|---|---|---|
| Public login, pricing and terms routes | Partially ready | Routes work; terms are still draft/unreviewed. |
| Core customer research workflow | Partially ready | Manual book, watchlist, ticker and book analysis work; authenticated browser journey not verified. |
| Clear product positioning | Partially ready | Research/simulation labels exist; sizing/action language conflicts with impersonal-research claim. |
| Guided onboarding and first value | Not ready | No risk/goal/disclosure/first-contract onboarding flow. |
| Empty/error/degraded states | Partially ready | Several honest degrade states exist; digest/benchmark/provider paths can misrepresent missing data. |
| Accessibility and keyboard use | Not verified | No automated or full manual audit. |
| Mobile web usability | Not verified | Public pages observed; authenticated responsive roles not tested. |
| Native Android | Not ready | Cleartext LAN prototype, no production auth, zero tests. |
| Customer support workflow | Not ready | No support console, escalation/SLA or customer-visible status. |

## Financial correctness and model risk

| Gate | Status | Evidence / release condition |
|---|---|---|
| Price provenance/freshness on every output | Not ready | Raw provider calls and uneven data-quality envelopes remain. |
| Missing data never becomes zero | Not ready | Digest and benchmark paths violate this rule on current main. |
| Fair-value percentage convention | Ready | Centralized and property-tested. |
| Fair-value estimate methodology | Not ready | Generally AI/model-derived without mandatory inputs/citations. |
| Trade-level invariants | Ready | Positive input and strict stop-entry-target tests pass. |
| Kelly as educational research | Partially ready | Math/caps tested; data/calibration/policy consistency not validated. |
| Monte Carlo as labeled scenario tool | Partially ready | Internally tested; calibration and fallbacks require prominent disclosure. |
| ECG risk alert | Not ready | Known current-main false-alarm defect; #96 unmerged. |
| Customer market value/position weights | Partially ready | Basic position math works; cash/FX/transactions absent. |
| Customer realized/total return | Not ready | No transaction/cash-flow ledger. |
| Tax-loss output | Partially ready | Screening math works; full wash-sale/lot correctness does not. |
| Simulated track-record labeling | Ready | Public page clearly labels hypothetical cohorts and failed gate. |
| Performance/accuracy marketing substantiation | Not ready | Stored engine evidence does not support an edge claim. |

## Security and privacy

| Gate | Status | Evidence / release condition |
|---|---|---|
| Default-deny authentication | Ready | Protected probes fail closed; auth tests pass. |
| Customer tenant isolation | Ready | Leak sweep/masking suite passes. |
| Billing webhook authentication | Ready | HMAC/idempotency/event ordering tests are strong. |
| Sensitive local file permissions | Not ready | Multiple financial artifacts/logs are 0644. |
| Session revocation/device management | Partially ready | User disable works; individual stolen cookie cannot be revoked. |
| Durable rate limiting | Partially ready | In-memory only. |
| XSS defense | Partially ready | Escaping tests pass; CSP allows unsafe inline and DOM sinks remain. |
| CSRF-safe mutation semantics | Not ready | GET unsubscribe mutates state. |
| Secrets management and scanning | Partially ready | No committed secret found and `.env` is 0600; no CI secret scanner/rotation proof. |
| Dependency vulnerability status | Not verified | Audit tool unavailable and environment has conflicts. |
| Privacy notice and consent | Not ready | Missing. |
| Data export/deletion/retention | Not ready | Missing. |
| AI vendor/data-use disclosure | Not ready | Missing. |

## Billing and customer lifecycle

| Gate | Status | Evidence / release condition |
|---|---|---|
| Hosted checkout implementation | Ready | Lemon Squeezy integration and allowlist exist. |
| Real charge during this release audit | Not verified | No audit purchase performed. |
| Provision/revoke webhook lifecycle | Ready | Synthetic E2E and focused tests pass. |
| Onboarding email recovery | Ready | Sweep loaded, numerous successful exits; launchd delivery previously demonstrated. |
| Nightly source-of-truth reconciliation | Partially ready | Historically successful; latest run failed DNS with no proven alert. |
| Customer billing portal/cancellation | Not ready | Missing. |
| Refund workflow | Not ready | Not implemented/verified. |
| Invoice/tax document access | Not ready | Not surfaced. |
| Pricing/entitlement clarity | Partially ready | One public plan is clear; internal tiers have indistinct entitlements. |

## Reliability, data and operations

| Gate | Status | Evidence / release condition |
|---|---|---|
| Main service/tunnel health | Ready | Public routes and local service responded during audit. |
| Scheduled analysis/alerts/digest | Partially ready | Loaded and basic runs succeed; timezone/retry/data coverage defects remain. |
| Timezone/DST correctness | Not ready | launchd uses host-local time while docs promise ET. |
| Structured monitoring and alerting | Partially ready | Owner health summaries exist; coverage/error budgets/external alerts incomplete. |
| Log privacy/rotation | Not ready | Plaintext `/tmp` logs, high failure volume, no demonstrated retention/redaction policy. |
| Versioned schema migrations | Not ready | Runtime inline DDL only. |
| Automated encrypted backups | Not ready | Not found. |
| Restore drill | Not verified | No evidence. |
| Staging environment | Not ready | Not found. |
| Automated deploy/rollback | Not ready | Manual git pull/restart. |
| Incident response/runbook | Partially ready | Operational docs exist; no complete customer/privacy incident drill. |
| Provider outage behavior | Partially ready | Some fallbacks; actionability/coverage is not standardized. |
| Capacity/load target | Not verified | No load/concurrency test. |

## Engineering quality gates

| Gate | Status | Evidence / release condition |
|---|---|---|
| Python unit/integration suite | Ready | 1,214 tests pass. |
| Coverage threshold/depth | Partially ready | 56% total; critical provider/jobs/mobile paths have low or zero coverage. |
| Customer-isolation CI | Ready | Required check exists and local suite passes. |
| Application lint | Not ready | Ruff excludes application code. |
| Type checking | Not ready | 46 focused mypy errors and no CI gate. |
| Dependency consistency | Not ready | `pip check` fails. |
| Dependency/security scan CI | Not ready | Missing. |
| Authenticated browser E2E | Not ready | Missing. |
| Android CI/tests | Not ready | No CI and unit test task has no sources. |
| Branch protection/review | Partially ready | Two checks required; up-to-date/review/admin protections incomplete. |
| Database concurrency/leak testing | Not ready | Hundreds of resource warnings; no full writer contention gate. |

## Compliance launch gate

| Gate | Status | Evidence / release condition |
|---|---|---|
| Securities/fintech legal review | Not ready | Terms explicitly require it. |
| Personalized-output classification | Not ready | Customer-specific sizing/actions conflict with terms. |
| Simulated performance disclosure | Partially ready | Page language strong; counsel/advertising review not completed. |
| Tax-output review | Not ready | Screening limitations and definitive language require review. |
| Market/news data licensing review | Not verified | Rights to public redistribution not established. |
| Refund/cancellation/consumer terms | Not ready | Product lifecycle and reviewed disclosures absent. |
| Accessibility/legal web requirements | Not verified | No audit. |

## Go/no-go rules

- **Demo:** GO, using synthetic/non-sensitive data and explicit simulation/research wording.
- **Private unpaid pilot:** GO with named limitations, manual support and no Android/live-trading claims.
- **Invitation-only paid beta:** NO-GO until all P0 items in `GESH_FINANCE_PRODUCT_ROADMAP.md` and counsel/privacy gates are closed.
- **Public paid launch:** NO-GO until every “Not ready” Critical/High dependency is closed and the full staging checklist is evidenced.
- **Live/autonomous trading:** NO-GO; intentionally outside scope.
