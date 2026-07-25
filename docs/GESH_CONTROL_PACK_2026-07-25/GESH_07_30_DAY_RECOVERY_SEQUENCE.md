# GESH 07 — 30-Day Recovery Sequence

**Plan start:** 2026-07-25  
**Plan end:** 2026-08-23  
**Primary objective:** stop customer exposure immediately, then reopen only a durable, reconciled, consent-governed model product from an identifiable release  
**Default model source:** synthetic/paper  
**Optional source:** explicitly consented snapshot ingestion after quarantine and sanitization  
**Safe degraded state:** customer model/trade routes return 403 or a clear unavailable response; they never fall back to owner data

## 1. Recovery rules

1. **Containment precedes architecture.** The target model system cannot be used to justify leaving the current leak live.
2. **Dependency order wins.** UI polish, cost basis, and commercial copy cannot delay the durable publication boundary.
3. **No dirty-tree deployment.** The current worktree and live process are not an acceptable release source.
4. **No automatic legacy migration.** Existing owner snapshots, global real trades, and free-text notes do not become model content.
5. **No “sanitized live proxy.”** A request-time allowlist over the latest owner snapshot is a useful defense layer, not the target publication system.
6. **No rollback to exposure.** When a feature release fails, roll back to deny mode, an earlier approved model version, or maintenance mode.
7. **Every gate applies to the exact artifact promoted.** Branch checks, local checks, and live behavior are separate evidence.

## 2. Roles and daily control

Name one person for each role before work starts:

- **Incident commander:** owns P0 scope, decision log, and go/no-go.
- **Security/privacy DRI:** owns containment, consent, legacy restrictions, and sentinel tests.
- **Backend/API DRI:** owns authorization and customer endpoint contracts.
- **Data/publication DRI:** owns schema, migration, ingestion, versions, assignments, and rollback pointers.
- **Financial-integrity DRI:** owns equations, metric definitions, golden fixtures, and quantitative sign-off.
- **Frontend/accessibility DRI:** owns customer bundle, trust labeling, screen reader, keyboard, and device behavior.
- **Release/SRE DRI:** owns clean artifacts, runtime identity, promotion, monitoring, and rollback.
- **Independent reviewer:** signs P0 privacy, financial, and release gates and cannot be the relevant change author.

Run a 15-minute daily recovery review using only: gate state, new evidence, blockers, rollback readiness, and the next critical-path deliverable. Do not report percentage-complete without an acceptance artifact.

## 3. First 24 hours — contain and establish control

### Hours 0–2: declare and preserve

- Open one P0 incident/change record for TEN-01 and TEN-02 (historical IDs A-01 and A-03), recording sanitized evidence, affected routes, evidence time, current live-process start time, current main SHA, local SHA, and PR #105 state.
- Freeze model/trade customer feature changes and public claims. Do not delete existing snapshot or trade data.
- Capture a metadata-only inventory of snapshot files, trade tables, process command/cwd, schema versions, and release configuration. Hash evidence where practical; do not copy sensitive note contents into the incident record.
- Assign all DRIs and the independent reviewer.

**Checkpoint R0:** evidence inventory and pre-change database/config backups exist; access to backups is restricted; restoration has been read-tested.

### Hours 2–6: build the minimal containment release

- Branch from current `origin/main=cef1fe6`, not the dirty audit branch.
- Implement CP-001:
  - non-owner `GET /api/portfolio`, `GET /api/trades`, and `GET /api/trades/stats` return 403;
  - customer dashboard no longer initializes or schedules owner portfolio/ECG/regime/trade loaders;
  - owner behavior remains explicit and tested;
  - denied reads create metadata-only audit events.
- Add a minimal immutable build identity to the artifact/startup log so the containment deploy can be proven.
- Update tests that currently accept customer access to global trade routes. Seed an owner sentinel and patch owner connectors/global trade reads to raise for customer requests.

### Hours 6–12: verify and deploy containment

- Run focused authorization, finance, mutation, customer network, and console tests on the exact artifact.
- Run the full suite. Triage rather than suppress the known environment artifact; fix genuine failures on the release branch.
- Deploy the clean containment artifact. Do not restart the current dirty working tree.
- Verify live SHA, role, and route behavior with minimum-proof customer reads. Load the customer dashboard for ten minutes and confirm zero forbidden requests and zero related console errors.
- Restore and verify the authorized test-account baseline after any controlled mutation.

**Gate G0 — containment:** all three routes are denied to customers; customer UI does not call them; owner connectors/global ledgers are not touched in a customer request; live SHA is known.

**Checkpoint R1:** if the new dashboard fails, keep backend deny mode and serve a maintenance/customer-lite page. The previous exposed backend is not an allowed rollback.

### Hours 12–24: establish the recovery baseline

- Publish the incident containment result internally with exact SHA and smoke evidence.
- Reconstruct PR #105 onto current main in a fresh branch; classify each commit as retain, rewrite, or supersede. Preserve PR #106 dependency state.
- Fix the three genuine local `ai_analyzer` stream/test-double failures. Add the missing-`Content-Type` mutation regression.
- Draft and approve the schema/publication RFC for CP-003–CP-007, including:
  - model portfolios, immutable versions, holdings, model trade events;
  - assignments/entitlements;
  - consent and quarantine;
  - append-only publication events;
  - exact financial invariants;
  - synthetic/paper default and consented ingestion exception;
  - deny-mode and active-pointer rollback.
- Decide and document retention/access treatment for existing snapshot files and internal free-text notes. The default is no migration and no customer accessibility.

**24-hour exit criteria:** G0 is green; runtime identity is visible; one clean remediation branch exists; target schema and legacy-data policy have named approvers; no customer reopening is scheduled yet.

## 4. Days 2–7 — clean release line and approve the durable design

### Days 2–3: make branch and CI evidence trustworthy

- Complete CP-002: resolve PR #105 conflicts against `cef1fe6`, preserve the `gesh-core` v0.6.1 pin, and split changes into reviewable privacy, finance, web-contract, and UI commits.
- Remove test assumptions that encode unsafe behavior:
  - customer 200 responses from global trade routes;
  - late unknown-symbol persistence as success;
  - literal `reconciles=true` without recomputation.
- Require full-suite zero genuine failures, the isolation lane, a clean install, dependency-lock verification, and live-version contract.
- Package from a clean checkout into an immutable artifact/release directory.

### Days 3–5: prove the schema and migration

- Implement CP-003 additively on a production-shaped, scrubbed database copy.
- Exercise upgrade, idempotent rerun, interrupted migration, backup restore, and downgrade-before-first-publication.
- Enforce immutable published versions, allowed transitions, assignment foreign keys, finite numeric constraints, and append-only publication events.
- Create no active version and no customer assignment yet.

**Checkpoint R2:** before any publication data exists, the additive schema can be disabled or rolled back. After audit/publication events exist, retain the schema and roll forward rather than dropping evidence.

### Days 5–7: approve source, consent, and metric contracts

- Implement the synthetic/paper seed generator and CP-004 quarantine skeleton.
- Complete a legacy inventory by checksum and access class. Revoke customer access to old snapshot/global trade sources. Set restricted retention/expiry; do not migrate note text.
- Approve:
  - consent scope and revocation behavior for optional E ingestion;
  - forbidden field list;
  - publication approval separation of duties;
  - exact portfolio/day-change equations;
  - track-record metric definition and benchmark period;
  - final recommendation direction matrix.
- Apply an interim public track-record patch: render non-finite benchmark values as `n/a` and remove any unsupported “portfolio return” claim while the full metric rework is pending.

**Gate G1 — design and migration:** schema and migration rehearsal pass; D is the enabled default; E is disabled without consent; old snapshots/notes have a signed no-auto-migration policy; all P0 contracts have independent approval.

If G1 fails by day 7, keep containment in place and extend the design phase. Do not replace it with the request-time owner allowlist.

## 5. Days 8–14 — implement and prove the publication boundary

### Days 8–10: ingestion and draft validation

- Complete CP-004:
  - deterministic synthetic/paper ingestion;
  - restricted TTL quarantine;
  - consent state machine for E;
  - recursive field sanitization and free-text exclusion;
  - separate creator and approver;
  - append-only audit events.
- Seed sensitive sentinel values in nested test fixtures. Prove none can enter draft/public tables, APIs, HTML, caches, logs, or artifacts.
- Rehearse consent revocation and quarantine expiry.

### Days 10–12: financial publication gate

- Complete CP-007 with decimal arithmetic and the exact identities:
  - every holding market value equals quantity × price;
  - positions equal the sum of holdings;
  - total equals positions plus deliberately published cash;
  - `Σ holding weight_bps + cash_weight_bps = 10,000`, using total model equity as the denominator;
  - day change uses the same universe/cash convention;
  - all values and times are finite and within freshness policy.
- Make any one-cent variance, duplicate/unknown symbol, missing/stale forbidden price, NaN, or unitemized sleeve a publication failure.
- Build good/bad golden fixtures and property tests.

### Days 12–14: publish v1 in staging

- Complete the CP-005 publication transaction and entitlement resolver.
- Publish a new, explicitly labeled synthetic/paper v1. Do not derive it from a legacy owner snapshot.
- Assign only authorized staging test users.
- Verify byte-stable version reads, source labels, content hash/ETag, publication/data times, stale behavior, and denial of draft/revoked/unassigned versions.
- Change an owner snapshot and prove customer bytes do not change.
- Patch all owner connectors to raise and prove customer model reads still work.
- Exercise active-pointer rollback from v2 to v1 under concurrent reads.

**Checkpoint R3:** v1 is the known-good staging version. Rollback is an atomic pointer change to v1 or deny mode; it never invokes a connector.

**Gate G2 — publication isolation:** publication state machine, consent/sanitization, financial invariants, entitlement, connector-independence, and rollback all pass on the exact staging artifact.

## 6. Days 15–30 — complete customer trust, harden, and release

### Days 15–18: model trades and customer cutover

- Complete CP-006. Customer trade endpoints read only published model trade events. Internal real/paper/autopilot ledgers stay owner-only.
- Create new structured model event reason codes. Do not migrate global real trades or free-text notes.
- Complete CP-008 customer bundle:
  - separate My Book and Published Model navigation/state;
  - no owner operations code or background polling in customer HTML/bundle;
  - explicit model ID/version/source/publication/data/stale labels;
  - clear unavailable/empty/error states.
- Run role-based browser network allowlists and ten-minute console/polling checks.

**Checkpoint R4:** feature flag values are `deny` or `published_store`; there is no `owner_snapshot` value. If customer cutover fails, set `deny` and keep My Book available.

### Days 18–21: close release-blocking trust and hardening gaps

- Complete CP-009 strict media-type and synchronous symbol contracts. Missing/wrong content type must have zero side effects. Unknown symbols create no position, reservation, job, usage, or LLM spend.
- Complete CP-010:
  - versioned capital/exposure-aware return methodology or conservative relabeling;
  - period-matched benchmark;
  - finite-value handling;
  - consistent/labeled PF denominator;
  - engine/manual/blended cohort separation;
  - quality/sample-size-first presentation.
- Complete CP-011. A SELL/REDUCE/AVOID final decision cannot render a long-biased actionable setup. Stale or unresolved conflict yields no actionable setup.
- Begin CP-012 cost basis with null-preserving migration and user correction flow.
- Complete CP-014 origin cache/CSP/error contracts: no-store for authenticated financial/account data, session-aware variation, sanitized API errors, and a nonce/hash-compatible customer script path.
- Complete CP-015 chart contract: expose ALL only as backend `max`, preserve selected range, and make intraday source/freshness explicit.

**Gate G3 — product and quantitative trust:** track-record values reproduce from golden rows; no NaN; benchmark and labels match equations; final action and setup direction cannot conflict; mutation rejection has no persistence; cost basis is never fabricated; authenticated financial/account responses are explicitly non-cacheable; CSP/error contracts pass; every chart label maps to one backend range and carries explicit provenance.

### Days 22–25: accessibility and true mobile verification

- Complete CP-013:
  - semantic `h1`, headings, `nav`, `main`, and landmarks;
  - labels/descriptions/errors for every input;
  - appropriate live regions and focus movement for async analysis/chart/model updates;
  - keyboard-complete critical flows and chart alternative;
  - visible focus, reduced motion, contrast, and 200% zoom.
- Run Playwright/device projects at 320, 390, 768, and desktop widths. This must be actual device emulation, not only desktop window resizing.
- Run manual VoiceOver and keyboard sessions. Preserve screenshots, accessibility trees, and traces as release evidence.

**Gate G4 — accessible customer journey:** no critical/serious automated violations; manual critical flows pass; no horizontal overflow/obscured actions at required sizes; async updates announce exactly once.

### Days 26–27: release-candidate rehearsal

- Complete CP-016 on the exact release candidate:
  - full suite and all release-blocker lanes;
  - clean-install/reproducible artifact;
  - migration on a scrubbed production-shaped copy;
  - publication v1/v2 and rollback;
  - customer/owner role E2E;
  - owner-data sentinel and cache-isolation suite;
  - decimal financial recomputation;
  - track-record/decision golden fixtures;
  - axe, keyboard, and device projects;
  - staging smoke and baseline restoration.
- Conduct an incident simulation: publication validator fails, consent is revoked, benchmark is unavailable, and current active version is stale. Each case must degrade to a safe state.
- Independent reviewer signs privacy, finance, accessibility, migration, and rollback evidence.

**Checkpoint R5:** retain the prior artifact, database backup, v1 active pointer, and deny-mode configuration. Record tested rollback duration and commands in the release manifest.

### Days 28–30: canary and controlled production release

- Promote the signed artifact; verify production SHA, dependency-lock hash, schema version, and active model version before enabling any customer.
- Start with model/trade feature flag in deny mode. Run sanitized production smoke for login, My Book/export parity, model assignment, published holdings/totals, model trades/stats, mutations, chart, recommendation, track record, and accessibility landmarks.
- Enable the published model for the dedicated review account only. Monitor for at least one full market/off-market transition if timing permits.
- Expand only after:
  - no customer call touches an owner connector/global ledger;
  - all financial invariant monitors remain green;
  - no forbidden-data, cache, console, or migration alert fires;
  - model version and source labels stay stable;
  - the review baseline is restored.
- Keep E ingestion disabled in production until a real consent record, privacy approval, revocation drill, and stripped-snapshot test have independent sign-off.

**Gate G5 — release:** exact artifact is identifiable and all G0–G4 evidence remains green in production canary. Any P0 regression returns the feature to deny mode immediately.

## 7. Release gates

| Gate | Required evidence | Blocks release? | Safe response on failure |
|---|---|---:|---|
| **Privacy/tenant** | Customer cannot reach owner connectors, snapshots, global ledgers, notes, drafts, revoked versions, or another assignment | Yes | Deny model/trade routes |
| **Financial** | Holdings, cash, total, weights, day change, timestamps, model stats, and benchmark independently reconcile | Yes | Reject publication; keep prior valid version or deny |
| **Consent/provenance** | Source kind, methodology, consent where applicable, sanitization version, actors, and immutable publication event exist | Yes | Reject/quarantine draft |
| **Mutation/security** | Strict media type, Origin policy, no-side-effect rejects, symbol-before-spend, cache separation | Yes | Fail closed |
| **Decision integrity** | One versioned final action; setup direction and actionability obey it | Yes | Suppress setup |
| **Track record** | Finite reproducible metrics, correct labels/period/denominator, honest quality-first framing | Yes | Render conservative table/`n/a` |
| **Accessibility/mobile** | Automated plus manual AA evidence and true device projects | Yes for public paid release | Keep internal beta |
| **Release provenance** | Clean reproducible artifact, current-main ancestry, zero blocker failures, live SHA/schema/version match | Yes | Halt promotion |
| **Operations** | Alerts, audit events, backups, active-pointer and artifact rollback drills | Yes | Stay in staging/deny |

## 8. Rollback map

| Failure | First rollback | Data action | Prohibited rollback |
|---|---|---|---|
| Customer receives owner/global data | Set model/trade flag to `deny`; revoke customer route | Preserve sanitized evidence; restrict affected caches | Re-enable dynamic owner snapshot |
| Published version fails reconciliation | Point to prior approved version or deny | Quarantine bad version; append revocation event | Edit published rows in place |
| Consent revoked | Disable affected source and version; assess withdrawal | Preserve consent/audit record; expire raw quarantine | Continue automatic ingest |
| Frontend regression | Serve customer-lite shell with My Book only | Invalidate bad static assets | Restore owner polling behind CSS |
| Schema/migration problem | Stop promotion; restore rehearsed backup before publication, otherwise roll forward | Preserve migration logs and publication events | Drop audit data after publication |
| Track-record/benchmark failure | Render `n/a`/conservative quality table | Recompute derived cache from immutable rows | Show NaN or unsupported return claim |
| Recommendation conflict | Set `actionable=false`; suppress setup | Preserve decision evidence/version | Show conflicting long/short actions |
| Unknown runtime artifact | Remove traffic and redeploy signed artifact | Preserve unknown-SHA logs | Diagnose while serving paid traffic |

## 9. Day-30 definition of recovered

GESH is recovered only if all of the following are true:

1. Customer portfolio and trade reads come exclusively from a durable assigned published version, or are denied.
2. The default model source is synthetic/paper; optional consented ingestion is independently gated and still disabled unless fully approved.
3. Existing owner snapshots, real trades, and free-text notes were not auto-migrated.
4. Every published version is immutable, attributable, consent/provenance-labeled, exactly reconciled, and reversible through an active pointer.
5. My Book, Published Model, owner brokerage, and internal paper/autopilot books have distinct stores, APIs, labels, and frontend state.
6. Track-record math and benchmark are finite, reproducible, and correctly named.
7. Final recommendation and actionable setup cannot contradict.
8. Mutations reject absent/unsupported media types without side effects, and symbols are validated before persistence or spend.
9. Authenticated financial/account responses are explicitly non-cacheable by shared intermediaries, customer script policy no longer depends on broad inline execution, and errors are sanitized.
10. Every visible chart range has one correct backend mapping; ALL maps to `max`; chart source and freshness are explicit.
11. Critical journeys pass keyboard, screen-reader, zoom, and real device-emulation gates.
12. The running artifact, dependency lock, schema, and active model version are visible and match the signed release manifest.
13. A deny-mode, active-version, artifact, and database rollback has been rehearsed.
14. No P0/P1 alert fires during the production canary.

If any item remains false on 2026-08-23, the correct release state is **internal beta with customer model/trade surfaces denied**, not a deadline-driven exception.
