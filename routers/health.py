"""Public and session-role health probes extracted from server.py (PR1)."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import auth_users

router = APIRouter()

_PROCESS_STARTED_AT = time.time()

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#080d14"/>'
    '<path d="M6 22 L13 14 L18 18 L26 8" fill="none" stroke="#00d4ff" '
    'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


@router.get("/healthz")
def healthz():
    """Liveness probe — public, secret-free. Expand carefully; never add DB paths,
    account identifiers, or portfolio figures here."""
    build = (os.getenv("GESH_BUILD_SHA") or "").strip() or None
    payload = {
        "status": "ok",
        "auth": auth_users.auth_enabled(),
        "uptime_seconds": int(max(0, time.time() - _PROCESS_STARTED_AT)),
    }
    if build:
        payload["build"] = build[:40]
    return payload


@router.get("/api/me")
def whoami(request: Request):
    """Session role for the frontend nav (owner vs customer). No PII, no secrets —
    lets the customer UI render Analyze/Track-Record and hide the owner's book on
    load without racing the portfolio fetch."""
    if not auth_users.auth_enabled():
        return {"role": "owner"}
    email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"role": "owner" if auth_users.is_owner(email) else "customer"}


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Inline SVG favicon (cyan up-trend on the dark house background) — silences the
    /favicon.ico 404. Already in auth_users.PUBLIC_PREFIXES, so it stays public."""
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


@router.get("/api/security/health")
async def api_security_health():
    """API health report — latency, rate-limit hits, last errors."""
    from server import _sanitize, _server_error

    try:
        from security.api_verifier import all_api_health

        return _sanitize({"status": "ok", "sources": all_api_health()})
    except Exception as e:
        raise _server_error(e)
