"""billing_reconcile.py — nightly Lemon Squeezy ↔ gesh_auth.db reconciliation.

Webhooks are the hot path, but a delivery can be silently lost (downtime, tunnel
outage, bad deploy). This job makes LEMON SQUEEZY the source of truth once a day:

  1. Pull every subscription for the store from the LS API (stdlib urllib, no SDK).
  2. For each sub, heal drift in gesh_auth.db:
       ACCESS statuses (active / on_trial / past_due / cancelled / paused)
         → user must exist, be active, and carry the variant's tier
           (missing user → billing.provision(): creates + owes the magic-link
            email, which the 15-min onboarding sweep then delivers).
       ENDED statuses (expired / unpaid)
         → a still-active user with THIS billing_sub_id is revoked.
  3. NEVER touches accounts without a matching LS subscription — comp'd /
     manual / owner accounts are structurally out of scope.

Run:  python3 billing_reconcile.py            (prints a summary; exit 1 on API failure)
Cron: LaunchAgent com.gesh.billing-reconcile (daily 03:30 ET) → /tmp/gesh_billing_reconcile.log

Env:  LEMON_SQUEEZY_API_KEY (required) · LS_STORE_ID (default 421028)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Any

import auth_users
import billing

logger = logging.getLogger(__name__)

_API = "https://api.lemonsqueezy.com"
# LS statuses that mean "keep access": cancelled/paused retain access until period
# end (revocation arrives later as subscription_expired), matching billing.py.
_ACCESS_STATUSES = {"active", "on_trial", "past_due", "cancelled", "paused"}
_ENDED_STATUSES = {"expired", "unpaid"}


def _ls_get(path: str) -> dict[str, Any]:
    key = os.getenv("LEMON_SQUEEZY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("LEMON_SQUEEZY_API_KEY is unset")
    req = urllib.request.Request(
        _API + path,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/vnd.api+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_all_subscriptions(store_id: str) -> list[dict[str, Any]]:
    """Every subscription for the store, following JSON:API pagination."""
    subs: list[dict[str, Any]] = []
    path = f"/v1/subscriptions?filter%5Bstore_id%5D={store_id}&page%5Bsize%5D=100"
    while path:
        d = _ls_get(path)
        subs.extend(d.get("data", []))
        nxt = (d.get("links") or {}).get("next")
        path = nxt.replace(_API, "") if nxt else None
    return subs


def reconcile(store_id: str | None = None) -> dict[str, Any]:
    """Diff LS subscriptions against gesh_auth.db and heal. Returns a summary."""
    store = (store_id or os.getenv("LS_STORE_ID", "421028")).strip()
    subs = fetch_all_subscriptions(store)

    summary: dict[str, Any] = {"checked": len(subs), "provisioned": [],
                               "revoked": [], "tier_fixed": [], "skipped": []}
    for s in subs:
        a = s.get("attributes", {})
        sub_id = str(s.get("id", ""))
        email = (a.get("user_email") or "").strip().lower()
        status = (a.get("status") or "").strip().lower()
        variant = a.get("variant_id")
        if not email or not sub_id:
            summary["skipped"].append({"sub": sub_id, "reason": "no email"})
            continue

        tier = billing._tier_for(variant, a.get("variant_name", ""), a.get("product_name", ""))
        # A Lemon Squeezy billing email is mutable; the subscription id is the
        # authoritative account identity.  Prefer the bound account so an email
        # change cannot create a second, unbound login or evade later revocation.
        user = auth_users.get_user_by_sub_id(sub_id) or auth_users.get_user(email)
        account_email = user["email"] if user else email

        if status in _ENDED_STATUSES:
            # Revoke ONLY the account bound to THIS subscription (never comp'd rows).
            if user and user.get("is_active") and user.get("billing_sub_id") == sub_id:
                auth_users.set_active(account_email, False)
                auth_users.set_billing(account_email, status="expired")
                summary["revoked"].append(account_email)
                logger.warning("reconcile: revoked %s (LS sub %s is %s)",
                               account_email, sub_id, status)
            continue

        if status in _ACCESS_STATUSES:
            if tier is None:
                summary["skipped"].append({"sub": sub_id, "reason": "unknown variant"})
                logger.warning("reconcile: sub %s (%s) has un-allowlisted variant %s — skipped",
                               sub_id, email, variant)
                continue
            if user is None or not user.get("is_active"):
                billing.provision(account_email, tier,
                                  str(a.get("customer_id") or "") or None,
                                  sub_id, event_at=a.get("updated_at") or "")
                summary["provisioned"].append(account_email)
                logger.warning("reconcile: provisioned %s tier=%s (missed webhook? LS sub %s %s)",
                               account_email, tier, sub_id, status)
            elif user.get("billing_sub_id") == sub_id and user.get("tier") != tier:
                auth_users.set_billing(account_email, tier=tier)
                summary["tier_fixed"].append(account_email)
                logger.warning("reconcile: fixed tier for %s -> %s", account_email, tier)

    healed = len(summary["provisioned"]) + len(summary["revoked"]) + len(summary["tier_fixed"])
    summary["healed"] = healed
    logger.info("reconcile: %d subs checked, %d healed", len(subs), healed)
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] reconcile — %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except Exception:  # noqa: BLE001 — env may already be set by the caller
        pass
    try:
        summary = reconcile()
    except (urllib.error.URLError, RuntimeError) as e:
        logger.error("reconcile FAILED: %s", e)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
