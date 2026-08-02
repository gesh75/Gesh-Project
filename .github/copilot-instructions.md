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

---

## Build and test

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.lock   # runtime pinned; requirements.txt is the source
python -m pip install pytest pytest-cov ruff
python -m pytest -q
```

`requirements.txt` pins **gesh-core from a private repo**. CI rewrites the clone URL with a
`GESH_CORE_PAT` secret; without it the install fails. `openbb` is intentionally excluded from
CI (its resolver backtracks for 20+ minutes) and lives in `requirements-openbb.txt`.

The runtime version comes from **`.python-version`** - this repo deliberately does not declare
`[project] requires-python`.

## Financial-engine rules

- Any change to sizing, gating, or consensus needs a test proving the new behavior on a
  concrete numeric example.
- **Never widen a risk gate to make a test pass.** If a gate blocks, that is the gate working.
- Position sizing and risk bands are canonical in `gesh-core`. Import them; do not re-implement.
- **Cash for sizing:** use settled/deployable cash (`account['portfolio_cash']` /
  `equity_summary['deployable']`). Do **not** size from `withdrawable_amount` - it can read $0
  while deposits are still clearing.
- Price fetching must degrade gracefully across the provider fallback chain. Preserve per-symbol
  `price_source` tagging; a rate-limited all-NaN batch must not silently become "no candidates".
