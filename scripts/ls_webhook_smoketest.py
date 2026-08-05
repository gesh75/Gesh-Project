#!/usr/bin/env python3
"""ls_webhook_smoketest.py — local end-to-end smoke test of the LS webhook path.

Signs a sample Lemon Squeezy payload with BILLING_WEBHOOK_SECRET and POSTs it to a
running :9000 server's POST /api/billing/webhook — exercising the REAL
verify → claim(dedup) → dispatch → provision → onboarding-email path without needing
the Cloudflare Tunnel or the LS dashboard. Use it in Phase 0 to confirm wiring before
pointing the live test store at the endpoint.

Usage:
    # server must be running (python server.py) and .env must set BILLING_WEBHOOK_SECRET
    python3 scripts/ls_webhook_smoketest.py                       # subscription_created
    python3 scripts/ls_webhook_smoketest.py subscription_expired  # revoke path
    python3 scripts/ls_webhook_smoketest.py --url https://pay.example.com/api/billing/webhook

Exit code 0 only on a 2xx that performs the action expected for the event.
This is a DEV tool — it uses a dummy buyer email and the test signing secret; never
run it against a live store secret.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import requests

_EXPECTED_ACTIONS = {
    "subscription_created": "provision",
    "subscription_resumed": "provision",
    "subscription_unpaused": "provision",
    "subscription_payment_success": "provision",
    "order_created": "provision",
    "subscription_updated": "provision",
    "subscription_expired": "revoke",
    "subscription_cancelled": "mark",
    "subscription_paused": "mark",
    "subscription_payment_failed": "mark",
}


def _payload(event: str, email: str, sub_id: str, variant_id: str) -> dict:
    return {
        "meta": {"event_name": event},
        "data": {
            "id": sub_id,
            "type": "subscriptions",
            "attributes": {
                "user_email": email,
                "status": "active" if not event.endswith("expired") else "expired",
                "customer_id": 424242,
                "variant_id": variant_id,
                "variant_name": "GESH Access",
                "product_name": "GESH",
                "subscription_id": sub_id,
                "updated_at": "2026-06-27T12:00:00.000000Z",
            },
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "event",
        nargs="?",
        default="subscription_created",
        help="LS event_name (default: subscription_created)",
    )
    ap.add_argument("--url", default="http://localhost:9000/api/billing/webhook")
    ap.add_argument("--email", default="smoketest@example.com")
    ap.add_argument("--sub-id", default="sub_smoke_1")
    args = ap.parse_args()

    secret = os.getenv("BILLING_WEBHOOK_SECRET", "").encode()
    if not secret:
        print(
            "ERROR: BILLING_WEBHOOK_SECRET is unset — the handler will fail-close (401). "
            "Set a test-mode secret in .env first.",
            file=sys.stderr,
        )
        return 2

    variant = os.getenv("LS_VARIANT_OBSERVER", "test_variant_id")
    body = json.dumps(_payload(args.event, args.email, args.sub_id, variant)).encode()
    sig = hmac.new(secret, body, hashlib.sha256).hexdigest()

    try:
        resp = requests.post(
            args.url,
            data=body,
            headers={"Content-Type": "application/json", "X-Signature": sig},
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"ERROR: POST failed — is the server running at {args.url}? ({e})", file=sys.stderr)
        return 2

    print(f"HTTP {resp.status_code}  {resp.text[:300]}")
    expected_action = _EXPECTED_ACTIONS.get(args.event)
    ok = False
    try:
        data = resp.json()
        ok = bool(data.get("ok")) and data.get("action") == expected_action
    except ValueError:
        pass
    if 200 <= resp.status_code < 300 and ok:
        print(f"✓ {expected_action} ({args.event})")
        return 0
    print(
        f"✗ webhook did not perform expected action {expected_action!r} — "
        "check signature secret, server logs, and .env wiring",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
