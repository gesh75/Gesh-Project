"""Public SEO surface for pay.geshfinance.com: head tags, robots.txt, sitemap.xml, noindex.

Everything here is PUBLIC and static. This module must never import or read owner
holdings, cash, broker, LLM or billing state (a test enforces the import list).
Routes are registered with ``app.include_router(seo_pages.router)``; the exact public
paths live in ``auth_users.PUBLIC_EXACT_PATHS`` (exact match, never a prefix).
"""

from __future__ import annotations

import html as _html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

DEFAULT_BASE_URL = "https://pay.geshfinance.com"
SITE_NAME = "GESH Financial"
DEFAULT_OG_IMAGE = "gesh-financial"
_HERE = Path(__file__).resolve().parent

router = APIRouter()


# ── base URL ────────────────────────────────────────────────────────────────────


def _is_stable_https_origin(value: str) -> bool:
    try:
        u = urlparse(value)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or "." not in host:
        return False
    if host.endswith(".trycloudflare.com") or host == "localhost":
        return False
    return not re.fullmatch(r"[\d.]+", host)  # bare IPv4


def site_base_url() -> str:
    """Absolute origin for canonical/OG/sitemap URLs.

    Takes APP_BASE_URL only when it is a stable https origin: the dev tunnel script
    rewrites that variable to a rotating trycloudflare URL, which must never leak
    into a canonical tag.
    """
    raw = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    return raw if raw and _is_stable_https_origin(raw) else DEFAULT_BASE_URL


# ── head tags ───────────────────────────────────────────────────────────────────


def _esc(value: str) -> str:
    return _html.escape(value, quote=True)


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"


def jsonld_script(doc: dict[str, Any]) -> str:
    """One JSON-LD <script>; <, > and & are \\u-escaped so data can never close the tag."""
    body = (
        json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f'<script type="application/ld+json">{body}</script>'


def head_tags(
    *,
    title: str,
    description: str,
    path: str,
    og_image: str = DEFAULT_OG_IMAGE,
    og_type: str = "website",
    robots: str | None = None,
    jsonld: list[dict[str, Any]] | None = None,
) -> str:
    """<title> + description + canonical + Open Graph + Twitter (+ robots, JSON-LD)."""
    base = site_base_url()
    url = base + path
    image = f"{base}/og/{og_image}.png"
    parts = [
        f"<title>{_esc(title)}</title>",
        f'<meta name="description" content="{_esc(description)}">',
    ]
    if robots:
        parts.append(f'<meta name="robots" content="{_esc(robots)}">')
    og_desc = _trim(description, 200)
    parts += [
        f'<link rel="canonical" href="{_esc(url)}">',
        f'<meta property="og:site_name" content="{_esc(SITE_NAME)}">',
        f'<meta property="og:title" content="{_esc(title)}">',
        f'<meta property="og:description" content="{_esc(og_desc)}">',
        f'<meta property="og:type" content="{_esc(og_type)}">',
        f'<meta property="og:url" content="{_esc(url)}">',
        f'<meta property="og:image" content="{_esc(image)}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{_esc(title)}">',
        f'<meta name="twitter:description" content="{_esc(og_desc)}">',
        f'<meta name="twitter:image" content="{_esc(image)}">',
    ]
    parts += [jsonld_script(d) for d in (jsonld or [])]
    return "\n".join(parts)


def software_application_ld(offers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """SoftwareApplication JSON-LD. Never emits ratings or reviews."""
    doc: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": SITE_NAME,
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web",
        "url": site_base_url() + "/",
        "featureList": (
            "Daily portfolio report, AI scores, Monte Carlo risk simulation, Kelly position sizing"
        ),
    }
    if offers:
        doc["offers"] = offers
    return doc


# ── sitemap ─────────────────────────────────────────────────────────────────────

# (path, source files whose mtime is the page's lastmod). Only PUBLIC, indexable
# marketing URLs belong here — a test fetches every entry logged-out and asserts 200.
SITEMAP_PAGES: list[tuple[str, tuple[str, ...]]] = [
    ("/", ("seo_landing.py", "seo_layout.py", "seo_pages.py")),
    ("/track-record", ("proof_page.py", "seo_pages.py")),
    ("/pricing", ("server.py", "seo_pages.py")),
    ("/tools", ("seo_tools.py", "seo_layout.py")),
    ("/tools/portfolio-xray", ("seo_tools.py", "seo_tools_js.py", "seo_tools_data.py")),
    ("/tools/monte-carlo-simulator", ("seo_tools.py", "seo_tools_js.py")),
    ("/tools/kelly-calculator", ("seo_tools.py", "seo_tools_js.py")),
]


def _lastmod(sources: tuple[str, ...]) -> str:
    stamps = [(_HERE / s).stat().st_mtime for s in sources if (_HERE / s).exists()]
    when = max(stamps) if stamps else 0.0
    return datetime.fromtimestamp(when, tz=timezone.utc).strftime("%Y-%m-%d")


def sitemap_xml() -> str:
    base = site_base_url()
    rows = "".join(
        f"  <url><loc>{_esc(base + path)}</loc><lastmod>{_lastmod(src)}</lastmod></url>\n"
        for path, src in SITEMAP_PAGES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</urlset>\n"
    )


def robots_txt() -> str:
    return (
        "# GESH Financial robots.txt\n"
        "# AI crawler decision: DEFAULT = ALLOWED. Only public marketing, tool and\n"
        "# track-record pages are open; the private app routes are disallowed below.\n"
        "# OWNER CHOICE: to opt out of AI crawlers, uncomment the blocks below.\n"
        "# User-agent: GPTBot\n"
        "# Disallow: /\n"
        "# User-agent: ClaudeBot\n"
        "# Disallow: /\n"
        "# User-agent: PerplexityBot\n"
        "# Disallow: /\n"
        "# User-agent: Google-Extended\n"
        "# Disallow: /\n"
        "# /login is intentionally NOT disallowed: it is noindex'd, and a crawler can\n"
        "# only honour noindex on a page it is allowed to fetch.\n"
        "\n"
        "User-agent: *\n"
        "Disallow: /app\n"
        "Disallow: /admin\n"
        "Disallow: /onboarding\n"
        "Disallow: /api\n"
        "\n"
        f"Sitemap: {site_base_url()}/sitemap.xml\n"
    )


@router.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
def robots() -> PlainTextResponse:
    return PlainTextResponse(robots_txt())


@router.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap() -> Response:
    return Response(content=sitemap_xml(), media_type="application/xml")


# ── Open Graph images (static, committed; regenerate with scripts/gen_og_images.py) ──

# Served from /og/<name>.png — deliberately NOT under /api (robots.txt disallows /api and
# social crawlers must be able to fetch these). Whitelisted names only; no path params.
OG_IMAGES: tuple[str, ...] = (
    DEFAULT_OG_IMAGE,
    "portfolio-xray",
    "monte-carlo-simulator",
    "kelly-calculator",
)
OG_DIR = _HERE / "static" / "og"


def og_paths() -> tuple[str, ...]:
    return tuple(f"/og/{name}.png" for name in OG_IMAGES)


def _register_og(name: str) -> None:
    def view() -> Response:
        try:
            data = (OG_DIR / f"{name}.png").read_bytes()
        except OSError:
            return Response(status_code=404)
        return Response(
            content=data,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    router.add_api_route(f"/og/{name}.png", view, methods=["GET", "HEAD"], include_in_schema=False)


for _name in OG_IMAGES:
    _register_og(_name)


# ── noindex for everything that is not an indexable marketing page ──────────────

# Pages that SHOULD be indexed. Everything else (dashboard, /api/*, /login, owner
# views, auth/billing endpoints) gets `X-Robots-Tag: noindex`. "/" is exempt here
# because the handler decides: landing = indexable, logged-in dashboard = noindex.
INDEXABLE_EXACT: frozenset[str] = frozenset(
    {
        "/robots.txt",
        "/sitemap.xml",
        "/favicon.ico",
        "/terms",
        "/privacy",
        "/disclaimer",
        "/congress-watch",
    }
    | {path for path, _ in SITEMAP_PAGES}  # every sitemap URL is indexable by definition
    | set(og_paths())
)
INDEXABLE_PREFIXES: tuple[str, ...] = ("/static/",)


def is_indexable_path(path: str) -> bool:
    return path in INDEXABLE_EXACT or path.startswith(INDEXABLE_PREFIXES)


async def noindex_middleware(request: Request, call_next):
    resp = await call_next(request)
    if not is_indexable_path(request.url.path):
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return resp
