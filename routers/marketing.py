"""Public routes for the free-tool email capture (double opt-in). See marketing_capture.

Every response is ``noindex`` and ``no-store``; token pages also send
``Referrer-Policy: no-referrer`` so a token in the URL never leaks onward.

Exact public paths (auth_users.PUBLIC_EXACT_PATHS):
    POST /api/marketing/subscribe   JSON  (progressive-enhancement fetch)
    POST /marketing/subscribe       form  (no-JS fallback; /api/* is JSON-only by the CSRF guard)
    GET|POST /marketing/confirm     GET = interstitial only, POST = the state change
    GET|POST /marketing/unsubscribe GET = interstitial only, POST = state change / RFC 8058
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

import marketing_capture as mc
import seo_layout as L

router = APIRouter()

BODY_MAX_BYTES = 4096
_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}

_STATUS_FOR_KIND = {
    "accepted": 200,
    "invalid": 400,
    "no_consent": 400,
    "rate_limited": 429,
    "unavailable": 200,
}
_MESSAGE_FOR_KIND = {
    "accepted": mc.GENERIC_ACCEPTED,
    "invalid": mc.MSG_INVALID,
    "no_consent": mc.MSG_NO_CONSENT,
    "rate_limited": mc.MSG_RATE_LIMITED,
    "unavailable": mc.MSG_UNAVAILABLE,
}


class _TooLarge(Exception):
    pass


async def _read_capped(request: Request, limit: int = BODY_MAX_BYTES) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _TooLarge
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise _TooLarge
    return bytes(body)


def _truthy(value: object) -> bool:
    return value is True or (
        isinstance(value, str) and value.strip().lower() in {"1", "true", "on", "yes"}
    )


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _page(title: str, paragraphs: list[str], extra: str = "", status: int = 200) -> HTMLResponse:
    head = f'<meta name="robots" content="noindex,nofollow"><title>{L.esc(title)} | GESH Financial</title>'
    body = (
        f"<h1>{L.esc(title)}</h1>"
        + "".join(f"<p>{L.esc(p)}</p>" for p in paragraphs)
        + extra
        + '<p><a href="/tools">Back to the free tools</a></p>'
    )
    return HTMLResponse(L.page(head, body), status_code=status, headers=dict(_HEADERS))


def _json(content: dict, status: int = 200) -> JSONResponse:
    headers = dict(_HEADERS)
    if status == 429:
        headers["Retry-After"] = str(mc.IP_WINDOW)
    return JSONResponse(content, status_code=status, headers=headers)


def _outcome_json(outcome: mc.SubscribeOutcome) -> JSONResponse:
    kind = outcome.kind
    return _json(
        {"ok": kind == "accepted", "message": _MESSAGE_FOR_KIND[kind]}
        | ({"unavailable": True} if kind == "unavailable" else {}),
        _STATUS_FOR_KIND[kind],
    )


async def _subscribe(
    request: Request, background: BackgroundTasks, fields: dict[str, object]
) -> mc.SubscribeOutcome:
    outcome = await run_in_threadpool(
        lambda: mc.request_subscription(
            email=fields.get("email"),
            consent=_truthy(fields.get("consent")),
            source=fields.get("source"),
            ip=_ip(request),
            honeypot=fields.get("website"),
        )
    )
    if outcome.job is not None:
        # Sent after the response so a slow provider cannot change response timing for
        # new vs. existing addresses (enumeration-safe).
        background.add_task(mc.send_confirmation, outcome.job)
    return outcome


@router.post("/api/marketing/subscribe", include_in_schema=False)
async def api_subscribe(request: Request, background: BackgroundTasks) -> JSONResponse:
    try:
        raw = await _read_capped(request)
    except _TooLarge:
        return _json({"ok": False, "message": mc.MSG_INVALID}, 413)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    outcome = await _subscribe(request, background, data if isinstance(data, dict) else {})
    return _outcome_json(outcome)


@router.post("/marketing/subscribe", include_in_schema=False)
async def form_subscribe(request: Request, background: BackgroundTasks) -> HTMLResponse:
    try:
        raw = await _read_capped(request)
    except _TooLarge:
        return _page("Something went wrong", [mc.MSG_INVALID], status=413)
    fields: dict[str, object] = dict(parse_qsl(raw.decode("utf-8", "replace")))
    outcome = await _subscribe(request, background, fields)
    title = "Check your email" if outcome.kind == "accepted" else "Weekly learning note"
    return _page(title, [_MESSAGE_FOR_KIND[outcome.kind]], status=_STATUS_FOR_KIND[outcome.kind])


# ── token pages: GET never mutates (mail scanners prefetch links) ───────────────


async def _token_from(request: Request, token_qs: str) -> str:
    token = (token_qs or "").strip()
    if token:
        return token
    try:  # RFC 8058 one-click and our own forms keep the token in the URL; accept a body too
        raw = await _read_capped(request)
    except _TooLarge:
        return ""
    return dict(parse_qsl(raw.decode("utf-8", "replace"))).get("token", "").strip()


def _interstitial(action: str, token: str, title: str, lead: str, button: str) -> HTMLResponse:
    form = (
        f'<form method="post" action="{L.esc(action)}?token={L.esc(token)}">'
        f'<p><button class="btn" type="submit">{L.esc(button)}</button></p></form>'
    )
    return _page(title, [lead], extra=form)


def _invalid(what: str) -> HTMLResponse:
    return _page(
        "This link is not valid",
        [
            f"This {what} link is invalid or has expired. You can request a new one from any free tool page."
        ],
        status=400,
    )


@router.get("/marketing/confirm", include_in_schema=False)
async def confirm_page(token: str = "") -> HTMLResponse:
    if not await run_in_threadpool(mc.token_is_valid, token, mc.TYP_CONFIRM):
        return _invalid("confirmation")
    return _interstitial(
        "/marketing/confirm",
        token,
        "Confirm your email",
        "Press the button to confirm that you want the GESH weekly learning note. "
        "It is educational information, not investment advice, and at most weekly.",
        "Confirm my email",
    )


@router.post("/marketing/confirm", include_in_schema=False)
async def confirm_do(request: Request, token: str = "") -> Response:
    result = await run_in_threadpool(mc.confirm, await _token_from(request, token))
    if result == "invalid":
        return _invalid("confirmation")
    return _page(
        "You are subscribed" if result == "changed" else "Already confirmed",
        ["Your email is confirmed. Every email includes a link to unsubscribe at any time."],
    )


@router.get("/marketing/unsubscribe", include_in_schema=False)
async def unsubscribe_page(token: str = "") -> HTMLResponse:
    if not await run_in_threadpool(mc.token_is_valid, token, mc.TYP_UNSUB):
        return _invalid("unsubscribe")
    return _interstitial(
        "/marketing/unsubscribe",
        token,
        "Unsubscribe?",
        "Press the button to stop the GESH weekly learning note.",
        "Unsubscribe",
    )


@router.post("/marketing/unsubscribe", include_in_schema=False)
async def unsubscribe_do(request: Request, token: str = "") -> Response:
    result = await run_in_threadpool(mc.unsubscribe, await _token_from(request, token))
    if result == "invalid":
        return _invalid("unsubscribe")
    return _page("You are unsubscribed", ["You will not receive the weekly learning note."])
