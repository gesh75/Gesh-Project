# GESH Audit Truth-Synchronization Design

**Date:** 2026-07-18  
**Approved direction:** targeted truth synchronization; preserve the current visual identity; no broad refactor or new trading capability.

## Problem

Six owner-facing surfaces contradict the verified audit:

- `GESH_PROJECT_DOCS.html` still reports an arbitrary 85% build score, retired phases, stale service topology and overly broad “Live” module labels.
- `GESH_MONETIZATION_REPORT.html` treats legal classification as settled, implies a larger active cohort than verified, and uses signal-accuracy projections unsupported by the current engine evidence.
- `Crypto_Architecture_Diagram.html` contains stale record counts and promotes an unvalidated 80% win-rate target without showing the local-data and paper-only boundaries.
- `/progress` still renders the retired 30-trade September-alpha countdown instead of the current versioned gate and product-readiness blockers.
- `/watch` calls a paper simulation “Live Trading Watch” and does not make provider degradation visually primary.
- `/invest` asks “where should new cash go?” and labels exact model sizing as suggestions despite the unresolved personalized-output compliance question.

## Audience and page jobs

| Surface | Audience | Single job |
|---|---|---|
| Project docs | Founder/technical reviewer | Show the verified architecture and maturity boundaries. |
| Monetization report | Founder/commercial reviewer | Show what may be sold now, what evidence is missing, and which claims are blocked. |
| Crypto architecture | Founder/technical reviewer | Show the paper-only crypto data flow, current runtime evidence and risk boundaries. |
| `/progress` | Founder | Answer “what is verified, degraded or blocked before paid beta?” |
| `/watch` | Founder | Observe paper evidence and data quality without implying live execution. |
| `/invest` | Founder | Explore hypothetical diversification scenarios without a suitability or action claim. |

## Information architecture

The shared signature is a **readiness rail** with three semantic states:

- **Verified** (`#00e87a`) — backed by current tests/runtime evidence.
- **Degraded** (`#f0b429`) — implemented but incomplete, stale or provider-dependent.
- **Blocked** (`#ff3c5a`) — must not be marketed or used as a launch claim.

The existing navy/cyan instrument-panel palette remains. Cyan identifies navigation and observed data; green is reserved for verified evidence, yellow for uncertainty, and red for blockers. No decorative animation is added. Tables remain responsive through horizontal overflow and every new status conveys text in addition to color.

```text
┌──────────────────────────────────────────────────────────┐
│ Surface title                         as-of / mode         │
│ VERIFIED  n     DEGRADED  n     BLOCKED  n               │
├──────────────────────────────────────────────────────────┤
│ Highest-confidence evidence / current data quality       │
├──────────────────────────────────────────────────────────┤
│ Existing detailed content, relabeled where necessary     │
└──────────────────────────────────────────────────────────┘
```

## Behavior by surface

### `/progress`

Replace deadline theater with three release lanes: Product Beta, Signal Evidence and Operations. Retain read-only weekly history as context, but label its retired 30-trade fields as legacy and never use them for readiness. Load the authoritative versioned gate through `proof_page._load_gate_dto`; when unavailable, show `Not verified`, never a passing fallback. Show the audit’s P0 blockers as static governance facts with an as-of date, while dynamic signal fields come from the gate DTO.

### `/watch`

Rename to **Paper Evidence Watch**. Keep the current data contract and polling. Add a data-quality rail derived from `price_source`, `live_prices_available` and provisional sample state. Change online status from `live` to `updated`; keep the `PAPER ONLY` badge and authoritative gate.

### `/invest`

Rename to **Diversification Scenario Lab**. Keep read-only what-if calculations, but replace “where should cash go,” “suggest,” and “swap ideas” with scenario language. Add a visible statement that outputs do not use goals, liabilities, tax position or risk tolerance and therefore are not suitability-checked. When stale, mark the scenario degraded.

### Static reports

Add a dated audit-truth banner near the top, correct the most material claims in place, and link the detailed audit files. Preserve the surrounding historical record rather than rewriting hundreds of lines. Any retained historical projection is labeled as superseded or unvalidated.

## Notion tracking

Use the existing page `GESH Financial — Project Tracker`, data source `GESH Build Items`, and `GESH Milestones`. Create one milestone for this synchronization and one build item for each of the six surfaces plus verification/Notion closure. Each task carries exact files, acceptance criteria, status and audit context. Append a dated progress update to the tracker rather than replacing its history.

## Security and compliance constraints

- Do not expose customer or owner dollar values in public pages or Notion.
- Do not add public routes or change authorization.
- Do not claim legal classification, accuracy, suitability, live execution or tax correctness.
- Preserve server-side JSON neutralization and client-side escaping.
- Do not read or copy browser cookies, secrets or payment details.
- Treat open PRs as unshipped.

## Verification

- New copy-contract tests fail before implementation and pass after.
- Existing progress/watch/invest unit and endpoint suites pass.
- Static HTML contains the dated audit state and no prohibited claims remain in active/current sections.
- Both repos pass `git diff --check`; HTML parses with the standard-library parser.
- Auth wall remains 302 for unauthenticated `/progress`, `/watch` and `/invest`.
- After a controlled service restart, authenticated visual verification is attempted; if credentials are unavailable, this remains explicitly not verified.

## Out of scope

- Live execution, LT Macro voting, portfolio accounting, broker/customer import, provider-gateway refactor, database migration framework, legal drafting, a new frontend framework or a new visual system.

