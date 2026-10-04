"""Shared shell (CSS, nav, footer, FAQ blocks) for the public marketing pages.

Pure string rendering. No owner data, broker, LLM or billing access.
"""

from __future__ import annotations

import html as _html
from typing import Any

import seo_pages

DISCLAIMER = (
    "Educational illustration — not investment advice. GESH Financial is not a registered "
    "investment adviser. Results shown are hypothetical or simulated and do not guarantee "
    "future results."
)

# Public tool pages (rendered by seo_tools; a test keeps this list in sync with it).
TOOL_LINKS: list[tuple[str, str]] = [
    ("/tools/portfolio-xray", "Portfolio X-Ray"),
    ("/tools/monte-carlo-simulator", "Monte Carlo simulator"),
    ("/tools/kelly-calculator", "Kelly calculator"),
]

_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#080d14;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,system-ui,sans-serif;line-height:1.6}
a{color:#00d4ff}
.wrap{max-width:960px;margin:0 auto;padding:0 20px}
nav.top{border-bottom:1px solid #1a2840;background:#0a111b}
nav.top .wrap{display:flex;gap:20px;align-items:center;flex-wrap:wrap;padding:14px 20px}
nav.top .brand{font-weight:700;color:#00d4ff;text-decoration:none;margin-right:auto}
nav.top a.l{color:#aeb9c7;text-decoration:none;font-size:14px}
nav.top a.l:hover{color:#fff}
nav.top a.cta{background:#00d4ff;color:#03121a;padding:7px 14px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px}
h1{font-size:34px;line-height:1.2;margin:40px 0 12px;color:#fff}
h2{font-size:22px;margin:40px 0 12px;color:#00d4ff}
h3{font-size:16px;margin:0 0 6px;color:#dfe8f2}
p.sub{font-size:18px;color:#aeb9c7;margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.card{background:#0d1520;border:1px solid #1a2840;border-radius:12px;padding:18px}
.card p{margin:6px 0;color:#aeb9c7;font-size:14px}
.tag{display:inline-block;font-size:11px;letter-spacing:.5px;text-transform:uppercase;color:#f0b429;border:1px solid #6b5410;border-radius:999px;padding:1px 8px;margin-bottom:8px}
.num{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#e8eef6}
.btn{display:inline-block;background:#00d4ff;color:#03121a;padding:11px 20px;border-radius:8px;font-weight:700;text-decoration:none}
.btn.alt{background:transparent;color:#00d4ff;border:1px solid #00d4ff}
.note{background:#1a1405;border:1px solid #6b5410;border-left:3px solid #f0b429;border-radius:6px;padding:12px 16px;color:#e8d9b0;font-size:13px;margin:20px 0}
details{background:#0d1520;border:1px solid #1a2840;border-radius:10px;padding:12px 16px;margin:8px 0}
summary{cursor:pointer;font-weight:600;color:#dfe8f2}
details p{color:#aeb9c7;margin:10px 0 0}
footer{border-top:1px solid #1a2840;margin-top:56px;padding:24px 0 48px;color:#667;font-size:12px}
footer a{color:#889}
label{display:block;font-size:12px;color:#8fa3bd;margin:12px 0 4px}
input,select,textarea{width:100%;background:#080d14;border:1px solid #1a2840;border-radius:8px;color:#e8eef6;padding:10px;font-size:14px;font-family:inherit}
textarea{min-height:110px;font-family:ui-monospace,Menlo,monospace}
table.t{width:100%;border-collapse:collapse;font-size:14px}
table.t th,table.t td{text-align:left;padding:7px 10px;border-bottom:1px solid #1a2840}
table.t th{color:#8fa3bd;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
ol.steps{padding-left:20px}
ol.steps li{margin:8px 0;color:#aeb9c7}
.bar{height:10px;background:#00d4ff;border-radius:5px}
.muted{color:#8fa3bd;font-size:13px}
.err{color:#ff6b81;font-size:13px;min-height:16px}
"""


def esc(value: str) -> str:
    return _html.escape(value, quote=True)


def nav_html() -> str:
    tools = '<a class="l" href="/tools">Free tools</a>' if TOOL_LINKS else ""
    return (
        '<nav class="top"><div class="wrap">'
        '<a class="brand" href="/">GESH Financial</a>'
        f'{tools}<a class="l" href="/track-record">Track record</a>'
        '<a class="l" href="/pricing">Pricing</a>'
        '<a class="l" href="/login">Sign in</a>'
        '<a class="cta" href="/pricing">Get started</a>'
        "</div></nav>"
    )


def footer_html() -> str:
    tools = " · ".join(f'<a href="{esc(p)}">{esc(n)}</a>' for p, n in TOOL_LINKS)
    tools_row = f"<p>Free tools: {tools}</p>" if tools else ""
    return (
        f'<footer><div class="wrap"><p>{esc(DISCLAIMER)}</p>{tools_row}'
        '<p><a href="/track-record">Track record</a> · <a href="/pricing">Pricing</a> · '
        '<a href="/terms">Terms</a> · <a href="/privacy">Privacy</a> · '
        '<a href="/disclaimer">Disclaimer</a></p></div></footer>'
    )


def faq_html(items: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in items
    )
    return f'<section aria-labelledby="faq"><h2 id="faq">Frequently asked questions</h2>{rows}</section>'


def faq_ld(items: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in items
        ],
    }


def breadcrumb_ld(trail: list[tuple[str, str]]) -> dict[str, Any]:
    base = seo_pages.site_base_url()
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": base + path}
            for i, (name, path) in enumerate(trail, 1)
        ],
    }


def free_offer() -> dict[str, Any]:
    return {
        "@type": "Offer",
        "price": "0",
        "priceCurrency": "USD",
        "description": "Free tools; paid plans for the full dashboard",
    }


def page(head: str, body: str, *, extra_head: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"{head}<style>{_CSS}</style>{extra_head}</head><body>"
        f'{nav_html()}<main class="wrap">{body}</main>{footer_html()}</body></html>'
    )
