"""email_sender.py — minimal transactional email for the :9000 product.

Sends via Resend's HTTP API (https://resend.com) using the already-present `requests`
dependency — no new SDK. Used by billing.py to deliver the magic-link onboarding email
after a successful payment.

DEV NO-OP: if RESEND_API_KEY is unset, send_email logs the message and returns False
instead of raising, so the webhook path stays testable locally without a live key.

Env:
    RESEND_API_KEY     — Resend API key (https://resend.com/api-keys)
    EMAIL_FROM         — verified sender, e.g. "GESH <access@yourdomain.com>"
    APP_BASE_URL       — public base URL used to build links (e.g. https://gesh.example.com)
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"


def _from_address() -> str:
    return os.getenv("EMAIL_FROM", "GESH <onboarding@example.com>")


def is_configured() -> bool:
    """True when a real Resend API key is present — i.e. send_email will actually
    attempt delivery instead of silently taking the DEV NO-OP path. Callers that
    schedule email jobs (e.g. run_signal_alerts.py, run_weekly_digest.py) use this
    to distinguish "no key configured" from "nothing to send" in their summaries."""
    return bool(os.getenv("RESEND_API_KEY", "").strip())


def send_email(to: str, subject: str, html: str) -> bool:
    """Send one HTML email. Returns True on a 2xx from Resend, False otherwise
    (including the no-key dev path). Never raises — email failure must not 500 a webhook."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.warning("email_sender: RESEND_API_KEY unset — NOT sending to %s (subject=%r)",
                       to, subject)
        return False
    try:
        import requests  # local import: keeps module import-light + testable
        resp = requests.post(
            _RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": _from_address(), "to": [to], "subject": subject, "html": html},
            timeout=15,
        )
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.error("email_sender: Resend %s sending to %s — %s",
                         resp.status_code, to, resp.text[:200])
        return ok
    except Exception as e:  # noqa: BLE001 — never let email break the caller
        logger.error("email_sender: send failed to %s — %s", to, e)
        return False


def send_magic_link(to: str, link: str, tier: str | None = None) -> bool:
    """Onboarding email with a one-time login link (no password)."""
    tier_line = f"<p>Your plan: <strong>{tier}</strong></p>" if tier else ""
    html = (
        "<div style=\"font-family:system-ui,sans-serif;max-width:480px;margin:auto\">"
        "<h2>Welcome to GESH Financial</h2>"
        "<p>Your access is ready. Click below to sign in — no password needed.</p>"
        f"{tier_line}"
        f"<p><a href=\"{link}\" style=\"display:inline-block;background:#00d4ff;"
        "color:#001018;padding:12px 20px;border-radius:8px;text-decoration:none;"
        "font-weight:600\">Sign in to GESH</a></p>"
        "<p style=\"color:#667;font-size:13px\">This link expires shortly. If it lapses, "
        "use the login page to request a new one.</p>"
        "<hr><p style=\"color:#889;font-size:12px\">NOT FINANCIAL ADVICE — FOR "
        "INFORMATIONAL PURPOSES ONLY.</p></div>"
    )
    return send_email(to, "Your GESH access", html)
