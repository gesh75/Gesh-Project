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
PRIVACY_PATH = Path(__file__).parent / "docs" / "GESH_PRIVACY_POLICY_DRAFT.md"

# Everything from these internal-only sections onward is stripped before rendering.
_INTERNAL_MARKERS = (
    "### Compliance notes (internal",
    "### Engineering to-do",
)

# Unresolved annotations in the draft. The source is an ANNOTATED document, so the
# only safe rule is: nothing in square brackets is ever served except a genuine
# markdown link. Anything else is an author-facing note.
#
# HOTFIX 2026-07-23 — this pattern previously required SHOUTING CASE
# (`\[[A-Z][A-Z0-9@]...`) and therefore matched 3 of the 10 bracket tokens in the
# draft. The other 7 were rendered verbatim on the PUBLIC page, including an
# author decision note reading "Geo-blocking the EU is the simplest path for a
# solo operator…" — internal legal strategy, published on a legal document.
# Enumerating placeholder shapes is the wrong approach; redacting the whole class
# is the right one, because a shape you did not anticipate defaults to LEAKING.
_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]+\]")


def _is_content_bracket(md: str, match: re.Match[str]) -> bool:
    """True for brackets that are legitimate CONTENT, not author annotations.

    Exactly two narrow exceptions, both incapable of carrying an instruction:
      * a markdown link — the bracket is followed immediately by "(";
      * a footnote/citation marker — digits only, e.g. "[1]".
    Everything else is treated as an annotation and redacted. The default must
    be "redact", because an unanticipated shape defaulting to "render" is what
    published internal legal strategy on 2026-07-23.
    """
    if md[match.end() : match.end() + 1] == "(":
        return True
    return match.group(0)[1:-1].strip().isdigit()


def _strip_author_note(md: str) -> str:
    """Drop the draft's leading author-instruction blockquote.

    Line 2 of the source is written TO the author ("Have a qualified attorney
    review... Placeholders in [BRACKETS] need your real values"), not to the
    reader — same class as the internal compliance notes, so it must not be
    served. The rendered page carries its own reader-facing DRAFT banner."""
    return "\n".join(ln for ln in md.splitlines() if not ln.lstrip().startswith("> **DRAFT"))


def unfilled_placeholders(md: str) -> list[str]:
    """Unique unresolved [annotation] tokens, in first-seen order.

    Every bracketed span EXCEPT a markdown link counts, in any casing — see the
    _PLACEHOLDER_RE comment for why enumerating shapes was unsafe.
    """
    seen: dict[str, None] = {}
    for m in _PLACEHOLDER_RE.finditer(md):
        if _is_content_bracket(md, m):
            continue
        seen.setdefault(m.group(0), None)
    return list(seen)


def redact_placeholders(md: str) -> str:
    """Remove unresolved annotations from text destined for a reader.

    Detection alone is not enough: the banner naming what is pending does not
    help if the note itself is still rendered in the body. Markdown links are
    preserved. A redacted span leaves a neutral marker so the reader can see the
    document is incomplete rather than silently reading around a hole.
    """

    def _sub(m: re.Match[str]) -> str:
        return m.group(0) if _is_content_bracket(md, m) else "[…]"

    return _PLACEHOLDER_RE.sub(_sub, md)


_BG, _SURFACE, _BORDER = "#080d14", "#0d1520", "#1a2840"
_CYAN, _DIM, _TEXT = "#00d4ff", "#5b7290", "#dfe8f2"


def _strip_internal(md: str) -> str:
    indexes = (md.find(marker) for marker in _INTERNAL_MARKERS)
    present = [idx for idx in indexes if idx != -1]
    return md if not present else md[: min(present)].rstrip() + "\n"


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
            flush_para()
            close_list()
            continue
        if stripped.startswith("### "):
            flush_para()
            close_list()
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            flush_para()
            close_list()
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            flush_para()
            close_list()
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
            continue
        if stripped in ("---", "***", "___"):
            flush_para()
            close_list()
            out.append('<hr style="border:0;border-top:1px solid ' + _BORDER + '">')
            continue
        if stripped.startswith("> "):
            flush_para()
            close_list()
            out.append(
                f'<blockquote style="border-left:3px solid {_CYAN};margin:12px 0;padding:6px 14px;color:{_DIM}">{_inline(stripped[2:])}</blockquote>'
            )
            continue
        if stripped.startswith("- "):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        para.append(stripped)

    flush_para()
    close_list()
    return "\n".join(out)


def policy_footer_html(muted: str = _DIM, accent: str = _CYAN) -> str:
    """The one legal footer every served page uses. Policies that exist but are
    unreachable from the product are not published in any meaningful sense —
    dashboard.html carried ZERO policy links before this (verified 2026-07-19)."""
    link = f"color:{accent};text-decoration:none"
    return (
        f'<div class="gesh-policy-footer" style="margin-top:28px;padding-top:14px;'
        f'border-top:1px solid {_BORDER};color:{muted};font-size:12px;line-height:1.7">'
        f'<a href="/privacy" style="{link}">Privacy Policy</a> · '
        f'<a href="/terms" style="{link}">Terms of Service</a> · '
        f'<a href="/disclaimer" style="{link}">Disclaimer</a><br>'
        "GESH Financial is a research and education tool. Informational purposes only — "
        "<b>not investment advice</b>. Past and simulated performance do not guarantee "
        "future results.</div>"
    )


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
 {policy_footer_html()}
</div></body></html>"""


_cache: dict[str, str] = {}


def render_terms_html(use_cache: bool = True) -> str:
    """Render the public Terms of Service & Disclaimer page (internal notes stripped)."""
    if use_cache and "terms" in _cache:
        return _cache["terms"]
    try:
        md = _strip_author_note(TERMS_PATH.read_text(encoding="utf-8"))
        body = _md_to_html(_strip_internal(md))
    except FileNotFoundError:
        body = (
            "<h1>Terms of Service &amp; Disclaimer</h1><p>This research tool is provided for "
            "informational and educational purposes only and is NOT FINANCIAL ADVICE. "
            "Results are HYPOTHETICAL/SIMULATED; PAST PERFORMANCE does not guarantee future results.</p>"
        )
    page = _page("Terms of Service", body)
    if use_cache:
        _cache["terms"] = page
    return page


def render_disclaimer_html(use_cache: bool = True) -> str:
    """Short public disclaimer — informational/educational only, not advice."""
    if use_cache and "disclaimer" in _cache:
        return _cache["disclaimer"]
    body = f"""<h1>Disclaimer</h1>
<p><strong>Last updated:</strong> 2026-08-23</p>
<p><strong>GESH Financial is a research and education tool. It is not financial,
investment, tax, or legal advice.</strong> Nothing on this site is a recommendation,
solicitation, or offer to buy or sell any security, and nothing is an endorsement
that any transaction is suitable for you.</p>
<p>GESH is not a registered investment adviser. It does not manage money, does not
have access to your brokerage account, and <strong>never places live orders</strong>.
You are solely responsible for every trade you make.</p>
<p>If you enter your own holdings, the software runs the same general-purpose models
on the numbers you typed. Model tags such as BUY, SELL, ADD, TRIM, or HOLD are
research labels, not instructions.</p>
<p>Performance figures, win rates, and track-record pages are
<strong>hypothetical / simulated</strong> unless clearly labeled otherwise.
Past and simulated performance do not guarantee future results. You can lose money.</p>
<p>AI-generated commentary can be incomplete or wrong. Market data can be delayed
or inaccurate. The service is provided as-is.</p>
<p>The full contract is the <a href="/terms">Terms of Service</a>.
Privacy practices are in the <a href="/privacy">Privacy Policy</a>.</p>"""
    page = _page("Disclaimer", body)
    if use_cache:
        _cache["disclaimer"] = page
    return page


def render_privacy_html(use_cache: bool = True) -> str:
    """Render the public Privacy Policy page.

    Author-facing DRAFT instructions are stripped. If the source still contains
    unresolved [placeholder] tokens, a pending-details banner is shown so the
    page cannot read as complete. Filled policies render without a DRAFT stamp
    and still must not claim attorney review.
    """
    if use_cache and "privacy" in _cache:
        return _cache["privacy"]
    try:
        md = _strip_author_note(PRIVACY_PATH.read_text(encoding="utf-8"))
        pending = unfilled_placeholders(md)
        # Redact BEFORE rendering — detecting an annotation but still printing it
        # is what leaked internal legal strategy onto the public page (2026-07-23).
        body = _md_to_html(redact_placeholders(_strip_internal(md)))
    except FileNotFoundError:
        pending = []
        body = (
            "<h1>Privacy Policy</h1><p>Our privacy policy is being finalized. "
            "We collect your email, the positions you enter, and usage metering "
            "data; analysis inputs are processed by third-party AI providers "
            "(Anthropic, Google). Contact us for details or to request deletion.</p>"
        )

    banner = ""
    if pending:
        # COUNT ONLY — never the annotation text. Printing the raw tokens is how
        # the geo-blocking decision note reached the public page even after the
        # body was redacted (2026-07-23): the banner re-leaked what the body hid.
        n = len(pending)
        note = (
            f'<p style="color:{_DIM};font-size:13px">'
            f"{n} detail{'' if n == 1 else 's'} in this document "
            f"{'is' if n == 1 else 'are'} still pending final confirmation "
            f"(shown as […]). Ask us if any of them matter to you.</p>"
        )
        banner = (
            f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-left:3px solid '
            f'{_CYAN};border-radius:6px;padding:12px 16px;margin:0 0 20px">'
            "<b>Some details are still being confirmed.</b> This policy describes how "
            f"your data is handled today and may be updated.{note}</div>"
        )
    page = _page("Privacy Policy", banner + body)
    if use_cache:
        _cache["privacy"] = page
    return page
