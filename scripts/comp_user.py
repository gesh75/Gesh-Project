#!/usr/bin/env python3
"""comp_user.py — grant (or revoke) a complimentary founding-user account.

Comp accounts are observer-tier subscribers WITHOUT a Lemon Squeezy subscription:
they log in via the same magic-link flow as paying customers, see the same
model-book-masked dashboard, and are structurally IGNORED by both the webhook
revoke path and the nightly reconcile (no billing_sub_id → never touched).

Usage (repo root):
    python3 scripts/comp_user.py grant  friend@example.com      # create + email link
    python3 scripts/comp_user.py link   friend@example.com      # re-send a fresh link
    python3 scripts/comp_user.py revoke friend@example.com      # disable access
    python3 scripts/comp_user.py list                           # comp'd accounts
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import auth_users  # noqa: E402
import billing  # noqa: E402
import email_sender  # noqa: E402


def grant(email: str) -> int:
    created = auth_users.create_magic_link_user(email, tier="observer")
    ok = email_sender.send_magic_link(email, billing._magic_link(email), tier="observer")
    if ok:
        from datetime import datetime, timezone
        auth_users.set_welcome_emailed(email, datetime.now(timezone.utc).isoformat())
    print(f"✓ {email}: {'created' if created else 're-activated'} (observer, comp) — "
          f"magic link {'EMAILED' if ok else 'NOT sent (check RESEND_API_KEY)'}")
    return 0 if ok else 1


def link(email: str) -> int:
    u = auth_users.get_user(email)
    if not u or not u.get("is_active"):
        print(f"✗ {email} is not an active user — use `grant` first")
        return 1
    ok = email_sender.send_magic_link(email, billing._magic_link(email),
                                      tier=u.get("tier") or "observer")
    print(f"{'✓ fresh link emailed to' if ok else '✗ send failed for'} {email}")
    return 0 if ok else 1


def revoke(email: str) -> int:
    auth_users.set_active(email, False)
    print(f"✓ {email} disabled (revoked on next request)")
    return 0


def list_comps() -> int:
    rows = [u for u in auth_users.list_users() if not (auth_users.get_user(u["email"]) or {}).get("billing_sub_id")]
    if not rows:
        print("(no comp accounts)")
    for u in rows:
        state = "active" if u["is_active"] else "DISABLED"
        print(f"  {u['email']:36} {state:9} last_login={u['last_login_at'] or '—'}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] in ("grant", "link", "revoke"):
        return {"grant": grant, "link": link, "revoke": revoke}[argv[0]](argv[1].strip().lower())
    if argv and argv[0] == "list":
        return list_comps()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
