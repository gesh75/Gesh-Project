# Codex Review Handoff — Renewal-variant fix + Dashboard MCP fallback (:9000)

**Author:** Georgi · **Date:** 2026-06-30 · **Repo:** `gesh-project` (main, pushed)
**Reviewer role:** Adversarially verify. Two surfaces: (1) a payment-webhook tier-mapping change
that touches **access control / entitlement** — assume real money; hunt for an **escalation**,
a wrong-tier grant, or a renewal that wrongly revokes. (2) a dashboard data **fallback** — hunt
for **data contamination** (stale data persisted as live), an error masked, or an info leak.

This is a follow-up to `docs/CODEX_REVIEW_PAYMENT.md` (the original LS integration, already
reviewed across rounds 1–6). **Only the changes below are new.**

> **STATUS — ✅ APPROVED (`a25d0e6..6311c2c`).**
> Round 1: REQUEST-CHANGES (2 HIGH, 2 MEDIUM) → fixed `9ec2294`.
> Round 2: REQUEST-CHANGES (1 MEDIUM, 1 LOW) → fixed `6311c2c`.
> Round 3: **APPROVE** — no remaining actionable findings; 256 full-suite tests pass; ruff /
> py_compile / diff-check / gitleaks clean. One-subscription-per-email is an accepted alpha
> boundary. Resolutions in **§6** (round 1) and **§7** (round 2).

---

## 0. Scope — what to review

Commit range: **`a25d0e6..HEAD`** (4 functional commits; ignore the `ci:` ones).

```bash
git diff a25d0e6..HEAD -- billing.py data_fetcher.py server.py tests/
```

| File | Δ | What changed |
|------|---|--------------|
| `billing.py` | +33 | `_variant_id()` (first_order_item fallback) + `_dispatch` tier-keep on no-variant activations |
| `data_fetcher.py` | +76 | `load_snapshot_payload()` — map MCP snapshot → live payload shape |
| `server.py` | +22 | `/api/portfolio` falls back to the snapshot when robin_stocks is offline |
| `tests/test_billing.py` | +91 | +3 variant/renewal tests + hermetic `LS_VARIANT_*` fixture |
| `tests/test_data_fetcher_fallback.py` | +70 | +5 fallback tests |

Relevant commits: `13cee2f`, `59a07fc` (billing), `c3142d1` (dashboard).

---

## 1. CHANGE A — Billing: variant resolution + renewal tier-keep

### Why
A **real** LS test-mode purchase showed two events refused as `unknown_variant`:
- `order_created` — the variant id is in `data.attributes.first_order_item.variant_id`, **not**
  top-level `attributes.variant_id`.
- `subscription_payment_success` — the data object is a **subscription-invoice with no variant
  at all**. Every monthly **renewal** would have been refused → wrong, and noisy.

Provisioning still worked (via `subscription_created`), so no access was wrongly granted — but
renewals/orders were silently ignored.

### The fix (`billing.py`)
1. `parse_event` → `_variant_id(attrs)`: returns `attributes.variant_id`, else
   `attributes.first_order_item.variant_id`.
2. `_dispatch`: for an **activation** event (`_ACTIVATE_EVENTS` or `subscription_updated` with
   an active-ish status) that maps to `tier=None`, **keep the tier already on the resolved
   account** instead of refusing. Refuse (`unknown_variant`) only when `tier is None` **and**
   there is no resolvable account.

### 🔴 Adversarial questions — find the escalation
1. **Can the tier-keep fallback ESCALATE a tier?** The intent (H3) is it only ever re-grants a
   tier an allowlisted activation previously set. Verify: is there any path where
   `user["tier"]` used in the fallback was set by something OTHER than an allowlisted
   `subscription_created`/`subscription_updated`? Can a crafted `order_created`/
   `payment_success` cause a higher tier than the account legitimately holds?
2. **Account resolution under the fallback.** `_lookup_account` resolves by `sub_id` then
   email. With the new `order_created` path (which has **no** `subscription_id` → `sub_id=None`),
   resolution is by **email only**. Can an attacker send a signed-looking `order_created` for a
   **victim's email** to act on the victim's account? (Signature is HMAC-gated — but confirm
   the email field can't be decoupled from the signed body.)
3. **`first_order_item` variant mismatch.** If an order's `first_order_item.variant_id` is a
   *different* allowlisted product than the user's subscription, does `order_created` grant the
   wrong tier? Is that a real risk given the single-tier alpha, and is it correctly bounded?
4. **Ordering vs. variant validation.** Confirm the stale-event (`_is_stale`) check still runs
   **before** the tier-keep/refuse logic and before `_apply_email_change` (no mutation on a
   stale or unknown event).
5. **Renewal that should revoke.** `subscription_payment_success` keeps access. Is there any LS
   state where a `payment_success` should NOT keep access (e.g., arriving after an expiry)? Does
   monotonic ordering protect that?

### Test-isolation note to sanity-check
The `test_billing.py` fixture now `delenv`s `LS_VARIANT_*` because a populated `.env` was
leaking into pytest and flipping `_tier_for` to strict-allowlist mode. Confirm this is a *test*
hermeticity fix and does **not** mask a production behavior (i.e., production still wants
allowlist mode when ids are configured).

---

## 2. CHANGE B — Dashboard: MCP-snapshot fallback for `/api/portfolio`

### Why
The live robin_stocks session is dead (0-byte pickle; Robinhood changed auth; `reauth.py`
can't complete). `/api/portfolio` 500/503'd → dashboard blanked. This finishes the MCP-first
migration: serve last-known data from `live_portfolio_snapshot.json` instead.

### The fix
- `data_fetcher.load_snapshot_payload()` maps the snapshot into `build_data_payload()`'s exact
  shape; enriches holdings (`equity_change`, `current_price`, recomputed
  `portfolio_percentage`); marks `offline=True` + `snapshot_age_seconds`. Raises
  `RobinhoodOfflineError` if the snapshot is missing/corrupt.
- `server.py /api/portfolio`: `try` live → on **any** exception, serve snapshot; if the snapshot
  is also unusable, surface the original live error. **Only logs equity to the ECG for LIVE
  data** (`if not payload.get("offline")`).

### 🔴 Adversarial questions — find the contamination / leak
1. **ECG contamination.** The guard `if not payload.get("offline"): _maybe_log_snapshot(...)` is
   the only thing stopping a stale snapshot value from being persisted as a fresh equity tick
   (which would corrupt the drawdown/ECG curve). Verify the guard is correct and that no other
   code path logs snapshot data as live. Could `offline` ever be falsy on snapshot data?
2. **Broad `except Exception`.** The endpoint catches *any* live failure and falls back. Does
   this mask real bugs (e.g., a 500 in `build_data_payload` unrelated to RH offline) by quietly
   serving stale data? Should the fallback be scoped to `RobinhoodOfflineError` + the specific
   `build_holdings` "not logged in" error only?
3. **Staleness signalling.** `offline=True` + `snapshot_age_seconds` are returned, but does any
   consumer (frontend, ECG, alerts, billing) treat snapshot data as authoritative/live? Is a
   24h-old snapshot served indefinitely with no hard cap? Should there be a max-age beyond which
   it 503s instead of serving very stale numbers?
4. **Path / input.** `_SNAPSHOT_PATH` is `GESH_SNAPSHOT_PATH`-overridable. Confirm it's
   server-config only (never user-influenced) — no path-traversal/file-read surface.
5. **Single-tenant exposure.** The snapshot is Georgi's real portfolio, served to authenticated
   subscribers (single-tenant product, by design). Confirm there's no path where the snapshot is
   served **without** the login wall (`/api/portfolio` is gated; verify no public alias).
6. **Money-math trust.** `load_snapshot_payload` does NOT recompute cash — it reads the
   builder's `equity_summary` (`build_snapshot_from_mcp.py` emits `deployable` = settled/
   unleveraged, avoiding the Gold margin-BP trap). Confirm the mapping doesn't reintroduce a
   leveraged figure (it maps `buying_power`/`deployable`, never `buying_power_leveraged`).

---

## 3. Phase 1 validation context (what was proven live)

Full lifecycle validated against **real LS test-mode webhooks** (store `geshpro`, variant
`1852694`, $49/mo): provision → onboarding email (Resend) → cancel (keeps access to period-end)
→ expire (revokes); plus dedup + email-down recovery. `launch_status.json gates.payment` stays
**false**. See `docs/PAYMENT_GOLIVE_CHECKLIST.md` § "Phase 1 RESULTS".

---

## 4. Verify

```bash
# tests (expect all green)
./venv/bin/python -m pytest tests/test_billing.py tests/test_data_fetcher_fallback.py -q

# billing logic — confirm no escalation, renewal kept, unknown still refused
./venv/bin/python -m pytest tests/test_billing.py -q -k "variant or payment_success or order_created"

# dashboard fallback shape + offline markers
./venv/bin/python -m pytest tests/test_data_fetcher_fallback.py -q
```

Current state: 47 tests green; `main` pushed to origin.

---

## 5. Output requested

For each finding: **severity (CRITICAL/HIGH/MEDIUM/LOW)**, file:line, the concrete exploit or
incorrect-behavior scenario, and a suggested fix. Decision: **APPROVE / APPROVE-WITH-COMMENTS /
REQUEST-CHANGES / BLOCK**. Focus fire on **§1 escalation** and **§2 ECG contamination** — those
are the two places a subtle bug actually costs money or corrupts the track record.

---

## 6. Round 2 — Resolutions (commit `9ec2294`)

All four Round-1 findings fixed. Full suite **251 passed**; `/api/portfolio` verified live
(headline `$38,920.51`, day_change `-$88.79` — matches the account-level value Codex computed).

| # | Round-1 finding | Fix | Regression test |
|---|-----------------|-----|-----------------|
| **HIGH-1** | foreign renewal (different `sub_id`, shared email, no variant) kept tier → `provision()` overwrote `billing_sub_id` → real sub's expiry then ignored as stale | tier retention now gated on **exact `ev["sub_id"] == user["billing_sub_id"]`** (`billing.py` `_dispatch`); non-matching/absent sub falls through to `unknown_variant` refusal — no provision, no sub overwrite | `test_foreign_renewal_does_not_replace_subscription` |
| **HIGH-2** | headline equity mapped from **positions-only** `portfolio_equity`, compared to an account-level previous close → overstated day P&L | `load_snapshot_payload` maps headline from **`account_equity` → `total` → `portfolio_equity`** (`data_fetcher.py`) | `test_headline_equity_is_account_level`, `test_account_equity_falls_back_to_total_then_portfolio` |
| **MEDIUM-3** | stale/empty snapshot served as live indefinitely; `{}` accepted as `$0` portfolio | validate schema + finite-positive equity (reject `{}`), require parseable `fetched_at`, reject age > `GESH_SNAPSHOT_MAX_AGE_SECONDS` (default 7d); dashboard shows amber **"Cached · as of … (Nh old)"** not green "Live" (`data_fetcher.py`, `templates/dashboard.html`) | `test_empty_snapshot_rejected`, `test_zero_equity_snapshot_rejected`, `test_stale_snapshot_beyond_max_age_rejected`, `test_timestampless_snapshot_rejected` |
| **MEDIUM-4** | broad `except Exception` served stale data even for a real bug | `get_portfolio_holdings` normalizes the bare "not logged in" → `RobinhoodOfflineError`; `/api/portfolio` falls back **only** on `RobinhoodOfflineError`; any other exception → 500 to monitoring (`data_fetcher.py`, `server.py`) | `test_build_data_payload_offline_raises_robinhood_offline`, `test_real_bug_in_holdings_is_not_swallowed` |

**Note on HIGH-1 scope:** the fix bounds the *no-variant renewal* path. A `subscription_created`
with a valid variant for a second sub on the same email still re-points the single-account model
to the newer sub — that is a legitimate new subscription, not the silent-replacement bug. Flag
if you think multi-sub-per-email needs an explicit policy beyond the alpha's one-sub model.

---

## 7. Round 2 — Resolutions (commit `6311c2c`)

Round 1 verified the three backend resolutions; round 2 found two residual issues — both fixed.
Full suite **256 passed**; in-scope ruff (`tests/**`) clean; `data_fetcher.py` ruff-clean.

| # | Round-2 finding | Fix | Regression test |
|---|-----------------|-----|-----------------|
| **MEDIUM-1** | the real startup/interval loader `loadPortfolioWithECG()` still called `setConn(true);setTs()` unconditionally → cached snapshots shown as green "Live" during normal operation (round 1 only fixed the unused `loadPortfolio()`) | both loaders consolidated through one offline-aware `applyPortfolioConn(d)` helper; unconditional pattern removed (`templates/dashboard.html`) | `test_dashboard_loaders_are_offline_aware` (contract guard: both loaders delegate to the helper, buggy pattern absent) |
| **LOW-2** | `load_snapshot_payload` called `snap.get()` before `isinstance(snap, dict)` → a JSON `[]`/scalar raised `AttributeError` (500) instead of `RobinhoodOfflineError` (503) | top-level type check moved **before** any field access (`data_fetcher.py`); ruff import-order nit fixed | `test_non_object_snapshot_rejected` (`[]`, `42`, string, `null`) |

**Note on the worktree:** `CLAUDE.md` and `run_daily_analysis.sh` show as modified — these are
pre-existing, unrelated edits from other work, intentionally left out of every commit in this
review range. The reviewed diff (`a25d0e6..HEAD`) does not touch them.
