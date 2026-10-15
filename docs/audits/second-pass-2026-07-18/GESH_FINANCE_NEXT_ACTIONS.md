# GESH Financial — Next Actions

## Top ten, in exact dependency order

| # | Action | Owner profile | Time box | Completion evidence |
|---:|---|---|---:|---|
| 1 | Freeze public performance/advice claims and obtain securities/fintech counsel review of the real customer flows. | Founder + counsel | 1–2 weeks | Approved product-language matrix, reviewed terms/privacy/refund disclosures and a documented decision on sizing/deploy-cash/Congress/tax surfaces. |
| 2 | Create the data map and privacy lifecycle. | Founder + privacy counsel/engineer | 1 week | Vendors/data fields/purposes/retention mapped; consent versions, export and deletion acceptance tests defined. |
| 3 | Lock down local data immediately. | Engineer | 1 day | Sensitive dirs 0700/files 0600, pickle retired or protected, logs redacted; automated permission test passes. |
| 4 | Add versioned migrations, encrypted off-host backup and restore drill. | Engineer/operator | 1–2 weeks | Clean restore on isolated host, checksum/integrity verified, recovery time recorded, rollback runbook rehearsed. |
| 5 | Build one data-quality/provider gateway and remove direct yfinance use from product and shared core. | Engineer | 2 weeks | Static ban, concurrency/bad-tick/outage tests, uniform provenance/freshness/coverage envelope, `pip check` clean. |
| 6 | Merge only verified fixes for ECG and retention; fix timezone and billing reconciliation alerting. | Engineer | 3–5 days | Original reproductions pass on deployed `main`; actual next-run times are ET-correct; billing outage triggers/recoveries are visible. |
| 7 | Quarantine legacy paper trading and reconcile advertised risk controls with enforcement. | Engineer + product | 1 week | One typed paper engine; long-exit/short-entry accounting tests; every visible config has an enforced decision code or is removed. |
| 8 | Establish staging and required authenticated E2E/quality gates. | Engineer | 2 weeks | Customer A/B/owner Playwright journeys, accessibility smoke, app Ruff, no-new mypy debt, dependency/secret scans and branch review protection. |
| 9 | Complete customer lifecycle before adding features. | Product/engineer | 2 weeks | Guided first-value flow, session control, billing portal/cancel/refund/invoice, notification history and support runbook pass staging. |
| 10 | Build the Decision Contract + Truth Ledger MVP; measure second-contract creation. | Product/engineer | 4–6 weeks | Every contract has immutable cited evidence, invalidation/review conditions and quality metadata; first beta cohort produces activation/retention evidence. |

## First 72 hours

1. Add a temporary launch banner/terms acknowledgement stating “experimental research; simulated evidence; no live trading; no tax or investment advice.” This is risk reduction, not a substitute for counsel.
2. Change existing sensitive artifact permissions and default `umask` without moving/deleting production data; take a verified backup first.
3. Add an owner alert for the already-observed billing reconciliation failure and manually confirm the next successful run.
4. Re-review #95 and #96 against their original reproductions; do not count a PR with mixed CI as fixed.
5. Stop using any legacy paper-trade performance number in public proof until records are provenance-tagged by engine/version.

## Founder decisions needed this week

- **Customer:** target a self-directed, high-intent investor with 10–40 positions who fears inconsistent decisions and hindsight bias—not a day trader seeking alerts.
- **Promise:** improve decision discipline and evidence traceability, not returns.
- **Scope:** research book first; either explicitly defer brokerage/accounting or fund a real ledger. Do not imply both.
- **Pricing:** keep one $49 founding-beta plan; no tier proliferation until retention data shows a distinct job-to-be-done.
- **Mobile:** label the Android app prototype and exclude it from launch; prioritize authenticated responsive web.
- **Automation:** keep LT Macro display-only and live execution disabled until preregistered validation and governance approve them.

## Metrics to instrument before the paid beta

| Funnel | Metric | Initial target |
|---|---|---:|
| Acquisition | Qualified interview → invited beta | Measure, no vanity target |
| Activation | First Decision Contract confirmed within 24h | ≥60% |
| Early value | Contract with cited evidence + review date | 100% of activated |
| Retention lead | Second contract/revision within 30 days | ≥50% |
| Core loop | Completed integrity loops per active customer/month | ≥2 |
| Learning | Eligible contracts completing Regret Replay | ≥40% after feature launch |
| Trust | Material incorrect/misleading financial outputs | 0; incident every occurrence |
| Data quality | Actionable responses meeting coverage/freshness SLO | ≥99% |
| Delivery | Alert/digest successful or visibly dead-lettered | ≥99% |
| Commercial | 90-day retained paying beta customers | ≥35% before scaling acquisition |

## Do not do next

- Do not add more AI personas, screeners, indicators or consensus nodes.
- Do not ship the current Android app.
- Do not enable live execution or LT Macro voting.
- Do not market win rate, alpha, “AI picks,” personalized allocation or tax safety.
- Do not migrate databases/frameworks merely for architectural appearance before recovery, provenance and the customer loop are fixed.
- Do not merge open PRs based on descriptions; rerun the original failure cases and deploy checks.

## 30-day outcome

At the end of 30 days, GESH should have: counsel-defined scope, published privacy/terms, protected data, recoverable databases, one price-quality gateway, closed known alert/ECG/billing defects, a staging E2E gate and a testable Decision Contract schema. If those are not complete, keep the product in demo/private-pilot mode.
