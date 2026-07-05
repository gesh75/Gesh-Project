# Branch Protection — make the security gates *required*

_Status: DRAFT, ready to apply. Needs **GitHub Pro** for the private repo (free
plan can't require status checks / rulesets on a private repo)._

## Do I need this now? — NO for the solo alpha (decided 2026-07-04)

**Deferred by design.** For a **solo** founder the `scripts/hooks/pre-push` gate
already blocks the common case — a regression can't push unless you consciously
type `git push --no-verify`. That's enough protection to run the ~5-user comped
alpha. **Ship on the hook; do not let $4/mo gate the launch.**

**Adopt GitHub Pro (~$4/mo, ~$40/yr) and apply this ruleset WHEN:**
- the project shows traction and is worth the spend (the plan — "definitely go Pro
  if we see benefit"), **or**
- a collaborator / another agent starts pushing to the repo (the hook is local and
  per-machine — it does nothing for other pushers or PRs).

Until then the pre-push hook is the enforcement. This doc is the one-command
turn-key for the day Pro is on.

---

_The `scripts/hooks/pre-push` gate enforces the same checks locally today, but it
is bypassable (`git push --no-verify`) and per-machine, so it is advisory. This
doc makes the checks **required server-side** — the durable fix the security
review asked for, for when the repo has more than one pusher._

## Why this matters

The customer-isolation leak sweep + the deny-by-default XSS guard are the launch
gate for **ongoing** changes. They only protect you if a push **cannot merge**
while they're red. Until required checks are on:

- a direct push to `main` with `--no-verify` skips the local hook, and
- a red CI run does not block anything.

## Required status checks

| Repo | Workflow | Required check contexts |
|------|----------|-------------------------|
| **gesh75/Gesh-Project** | `tests` | `test`, `customer-isolation` |
| **gesh75/gesh-core** | `CI` | `lint`, `test (3.12)`, `test (3.13)`, `test (3.14)` |

> Context names must match exactly. For a matrix job the context is
> `job (value)` — e.g. `test (3.12)`. Verify current names any time with:
> `gh api repos/gesh75/Gesh-Project/commits/main/check-runs --jq '.check_runs[].name'`

## Apply — Option A: `gh` CLI (fastest, once Pro is on)

Run from anywhere. This turns on a **ruleset** requiring the checks and blocking
direct pushes (PR required). Edit `bypass_actors` if you want to allow yourself
to bypass in a break-glass case.

```bash
# --- gesh-project ---
gh api -X POST repos/gesh75/Gesh-Project/rulesets --input - <<'JSON'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      } },
    { "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "test" },
          { "context": "customer-isolation" }
        ]
      } }
  ]
}
JSON

# --- gesh-core ---
gh api -X POST repos/gesh75/gesh-core/rulesets --input - <<'JSON'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "lint" },
          { "context": "test (3.12)" },
          { "context": "test (3.13)" },
          { "context": "test (3.14)" }
        ]
      } }
  ]
}
JSON
```

`required_approving_review_count: 0` requires a PR but not a second reviewer
(you're solo) — you still can't push a red build to `main`. Raise it later if a
teammate joins. Drop the `pull_request` rule entirely if you want to keep pushing
straight to `main` but still block when checks are red (checks then run on the
push and a red run marks the commit failed — but note: without the PR rule GitHub
can't *prevent* the push itself, only flag it; the PR rule is what makes red
truly un-mergeable).

## Apply — Option B: UI (no CLI)

Repo → **Settings → Rules → Rulesets → New ruleset → New branch ruleset**:
1. **Name** `main-protection`, **Enforcement** Active.
2. **Target branches** → Add target → `main` (default branch).
3. Enable **Require a pull request before merging** (approvals `0` is fine solo).
4. Enable **Require status checks to pass** → **Require branches to be up to date**
   → add the contexts from the table above.
5. Enable **Block force pushes**. Save.

## Verify it's live

```bash
gh api repos/gesh75/Gesh-Project/rulesets --jq '.[].name'          # -> main-protection
# then confirm a red check actually blocks: open a throwaway PR that fails the
# XSS guard and confirm "Merge" is disabled.
```

## Relationship to the local hook

Keep `scripts/hooks/pre-push` (install via `bash scripts/install-git-hooks.sh`) —
it gives fast local feedback before the push. The ruleset is the authoritative
gate. Together: hook = fast + advisory; ruleset = required + unbypassable.
