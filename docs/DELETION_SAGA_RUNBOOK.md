# Deletion/Billing Saga — Deploy & Rollback Runbook

## Why this exists

`delete_user_data()` used to drop the `users` row outright, freeing the unique
`billing_sub_id` index. The next Lemon Squeezy renewal then found no account,
recreated it, and emailed a magic link to someone who had asked to be erased. The
saga fixes this with a durable `account_tombstones` table that an old, tombstone-
**unaware** instance would ignore.

## The mixed-version hazard (reproduced)

An instance running code at or before `f7e8790` ignores `account_tombstones` and
**recreates an erased account** — verified: `create_magic_link_user()` on the old
binary against an upgraded DB with a completed tombstone returns `created=True`.

Therefore, during a rolling deploy where old and new instances serve concurrently,
or on a rollback to `f7e8790` **after the first tombstone exists**, the resurrection
defect returns.

## Safe activation — required order

1. **Merge with the flag OFF.** `GESH_DELETION_SAGA_ENABLED` is unset by default;
   the delete endpoint returns `503` and no tombstone is ever written. The code is
   inert. This is why the change can merge before the rollout is planned.
2. **Deploy the new code to every instance and every worker** (web + the
   `saga_recovery` maintenance job). Do not enable the flag yet.
3. **Confirm the cutover is complete.** `GET /healthz` returns
   `deletion.generation == "saga-tombstone-v1"` and `tombstone_aware == true`.
   Check **every** instance behind the load balancer. If any instance omits the
   `deletion` block or reports a different generation, an old build is still live —
   **do not proceed.**
4. **Enable the feature.** Set `GESH_DELETION_SAGA_ENABLED=1` and restart. Only now
   can the first tombstone be written.

## Rollback

- **Before any tombstone exists** (flag never enabled): rolling back to `f7e8790`
  is safe. The additive tables are inert for old code (verified:
  `test_33_new_tables_inert_for_code_that_ignores_them`).
- **After the first tombstone exists**: rolling application code back to a build
  that predates the saga is **UNSAFE** — the old code ignores tombstones and can
  resurrect erased accounts. If a rollback is unavoidable, first set
  `GESH_DELETION_SAGA_ENABLED=0` on the new build to stop new erasures, and treat
  any resurrection as an incident. The additive schema itself never needs to be
  dropped; only the application behaviour is version-sensitive.

## Blue/green cutover

1. Green = new build, flag OFF. Bring green up alongside blue (old).
2. Verify `/healthz` generation on all green instances.
3. Shift traffic blue → green. Blue never had the flag on, so it wrote no tombstone.
4. Once no blue instance serves, set the flag on green and restart green.
5. Keep blue parked (flag-incapable) only until you are certain no rollback to it
   will happen after the first tombstone.

## Recovery job

`python -m saga_recovery` (repository-native scheduled maintenance, mirroring
`billing.py sweep-onboarding`) drives parked sagas and post-tombstone billing
remediations forward. It is idempotent, bounded-batch, backs off, never retries
`manual_review`, and makes no network call inside a DB transaction.
