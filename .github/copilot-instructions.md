# GitHub Copilot Instructions

These rules apply to all Copilot suggestions and agent actions in this repository.

---

## Customer and financial-data safety

- Preserve customer isolation and owner/customer boundaries.
- Never expose owner portfolio values, account identifiers, positions, cash,
  recommendations, or private operational data to customers.
- Never fabricate financial values when required source data is unavailable.
- Prefer an explicit typed failure response over invented defaults or silent fallback.
- Preserve money masking, prose redaction, and financial leak-sweep behavior.

---

## Security boundaries

- Preserve authentication, authorization, tenant ownership, CSRF, Origin,
  Content-Type, cookie, session, and request-boundary checks.
- Never weaken fail-closed security behavior merely to make a test pass.
- Never bypass tenant or customer isolation.
- Do not print, expose, commit, or summarize secrets.

---

## Protected runtime and production state

**Never modify or operate:**

- production databases
- `.env` files
- credentials
- billing or Lemon Squeezy state
- brokerage integrations
- customer records
- production services
- launchd jobs
- deployment state
- live portfolio snapshots
- Decision Ledger production data

**Do not run:**

- AI calls
- the daily analysis chain
- brokerage operations
- billing actions
- production deployment or restart commands

---

## Engineering workflow

- Add a regression test for every defect.
- Reproduce the failure before implementing a correction.
- Prefer the smallest behavior-preserving change.
- Run focused tests before the complete suite.
- Report exact test counts and failures.
- Never push directly to `main`.
- Use a feature branch and open a draft pull request.
- Never merge, deploy, clean branches, or delete evidence unless explicitly authorized.
- Do not infer production release state solely from the historical Git worktree HEAD.
- Treat tracked-file manifests and release evidence as authoritative when explicitly documented.

---

## Product and regulatory claims

- Do not claim a proven investment edge.
- Treat performance evidence as hypothetical or simulated unless explicitly proven otherwise.
- Do not convert research language into individualized investment advice.
- Preserve beta, educational, hypothetical-performance, and not-financial-advice framing.
- Do not make legal conclusions.
