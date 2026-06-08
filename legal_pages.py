"""
legal_pages.py — render the public /terms and /disclaimer pages.

TERMS_OF_SERVICE.md is the single source of truth. This module strips the internal
"Compliance notes" section (legal strategy that must not be served to users),
converts the remaining markdown to a self-contained dark-theme HTML page, and
caches the result. Dependency-free (no markdown library on the running server).
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

TERMS_PATH = Path(__file__).parent / "TERMS_OF_SERVICE.md"

# Everything from this internal marker onward is stripped before rendering.
_INTERNAL_MARKER = "### Compliance notes (internal"

_BG, _SURFACE, _BORDER = "#080d14", "#0d1520", "#1a2840"
_CYAN, _DIM, _TEXT = "#00d4ff", "#5b7290", "#dfe8f2"


def _strip_internal(md: str) -> str:
    idx = md.find(_INTERNAL_MARKER)
    return md if idx == -1 else md[:idx].rstrip() + "\n"


def _inline(text: str) -> str:
    """Escape HTML, then apply inline markdown (bold, italic, code)."""
    t = _html.escape(text, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    t = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<em>\1</em>", t)
    return t


def _md_to_html(md: str) -> str:
    """Minimal block-level markdown → HTML (headings, hr, blockquote, lists, paras)."""
    out: list[str] = []
    para: list[str] = []
    in_list = False

    def flush_para():
        nonlocal para
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para = []

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in md.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_para(); close_list(); continue
        if stripped.startswith("### "):
            flush_para(); close_list(); out.append(f"<h3>{_inline(stripped[4:])}</h3>"); continue
        if stripped.startswith("## "):
            flush_para(); close_list(); out.append(f"<h2>{_inline(stripped[3:])}</h2>"); continue
        if stripped.startswith("# "):
            flush_para(); close_list(); out.append(f"<h1>{_inline(stripped[2:])}</h1>"); continue
        if stripped in ("---", "***", "___"):
            flush_para(); close_list(); out.append('<hr style="border:0;border-top:1px solid ' + _BORDER + '">'); continue
        if stripped.startswith("> "):
            flush_para(); close_list()
            out.append(f'<blockquote style="border-left:3px solid {_CYAN};margin:12px 0;padding:6px 14px;color:{_DIM}">{_inline(stripped[2:])}</blockquote>')
            continue
        if stripped.startswith("- "):
            flush_para()
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        para.append(stripped)

    flush_para(); close_list()
    return "\n".join(out)


def _page(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>GESH — {title}</title>
<style>
 body{{margin:0;background:{_BG};color:{_TEXT};font-family:-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.6}}
 .wrap{{max-width:760px;margin:0 auto;padding:32px 22px 64px}}
 h1{{color:{_CYAN};font-size:24px}} h2{{font-size:18px;margin-top:28px}} h3{{font-size:15px;color:{_DIM}}}
 code{{background:{_SURFACE};border:1px solid {_BORDER};border-radius:4px;padding:1px 5px;font-size:13px}}
 a{{color:{_CYAN}}} li{{margin:4px 0}}
 .back{{color:{_DIM};font-size:13px;display:inline-block;margin-bottom:16px}}
</style></head><body><div class="wrap">
 <a class="back" href="/">← Dashboard</a>
 {body_html}
</div></body></html>"""


_cache: dict[str, str] = {}


def render_terms_html(use_cache: bool = True) -> str:
    """Render the public Terms of Service & Disclaimer page (internal notes stripped)."""
    if use_cache and "terms" in _cache:
        return _cache["terms"]
    try:
        md = TERMS_PATH.read_text(encoding="utf-8")
        body = _md_to_html(_strip_internal(md))
    except FileNotFoundError:
        body = ("<h1>Terms of Service &amp; Disclaimer</h1><p>This research tool is provided for "
                "informational and educational purposes only and is NOT FINANCIAL ADVICE. "
                "Results are HYPOTHETICAL/SIMULATED; PAST PERFORMANCE does not guarantee future results.</p>")
    page = _page("Terms & Disclaimer", body)
    if use_cache:
        _cache["terms"] = page
    return page
