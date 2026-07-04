"""Renderer-wiring XSS guard: prove the TEMPLATE escapes every AI/external field.

Codex 4th-pass P0: the old daEsc test only exercised a Python reimplementation of
the JS helper — it never verified that each template sink actually *calls* it. A
field can be escaped in theory and raw in practice. This test parses the real
templates/dashboard.html and, for every known model-/external-supplied string
field, asserts it is NEVER interpolated raw inside a `${...}` sink: it must be
wrapped in daEsc()/_esc(), or fed to an allowlist classifier (…Cls/…Style, which
emit only fixed class/style strings). A newly-added raw sink fails this test.

This is a static/structural check (no browser), which is the right tool: it guards
the wiring at the exact place regressions happen — the template string literals.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"

# Fields that carry model-generated or external (Robinhood/yfinance/SEC/news) text.
# Any of these appearing inside a ${...} interpolation must be escaped or classified.
AI_TEXT_FIELDS = [
    "verdict", "action", "thesis", "summary", "narrative", "rationale", "note",
    "reason", "sector", "sectors", "company_type", "portfolio_type",
    "portfolio_verdict", "action_signal", "urgency", "overall_sentiment",
    "market_mood", "investment_horizon", "momentum_narrative", "market_summary",
    "snowflake_summary", "form_type", "assessment", "priority", "severity",
    "label", "message", "analyst_note", "insider_activity", "prediction",
    "key_findings", "red_flags", "positive_signals", "key_themes",
]

# Wrappers that make a sink safe: the escaper, or an allowlist classifier/style
# helper that returns only a fixed set of class/style strings (never raw input).
_SAFE_WRAPPER = re.compile(
    r"(daEsc|_esc|"
    r"[a-zA-Z]*Cls|[a-zA-Z]*Style|sentCls|sigCls|urgCls|predCls|verdictCls|"
    r"sevCls|saCls|priCls|verdictRadarCls|daVerdictStyle|daPriStyle)\s*\("
)

# Match a full ${ ... } interpolation (no nested braces — the template uses none
# deeper than one level inside these sinks).
_INTERP = re.compile(r"\$\{([^{}]*)\}")


def _raw_sinks() -> list[tuple[int, str]]:
    """Return (line_no, interpolation) for every ${...} that references an AI text
    field WITHOUT a safe wrapper around that field."""
    text = TEMPLATE.read_text()
    field_re = re.compile(r"\.(?:" + "|".join(AI_TEXT_FIELDS) + r")\b")
    offenders: list[tuple[int, str]] = []
    for m in _INTERP.finditer(text):
        body = m.group(1)
        if not field_re.search(body):
            continue
        # Safe if the whole interpolation routes through daEsc/_esc/an allowlist.
        # Require the wrapper to actually enclose the field reference: we treat the
        # interpolation as safe when a safe wrapper call appears and the field is
        # inside its argument list. Conservatively, if ANY unwrapped field token
        # sits at top level (not preceded by a safe-wrapper call on the same
        # segment), flag it.
        if _field_is_wrapped(body, field_re):
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        offenders.append((line_no, m.group(0)))
    return offenders


def _field_is_wrapped(body: str, field_re: re.Pattern) -> bool:
    """True iff every AI-field reference in `body` is enclosed by a safe wrapper
    call. Splits on the field positions and checks each has an unclosed safe
    wrapper open to its left within the same interpolation."""
    for fm in field_re.finditer(body):
        # Safe if this occurrence is a COMPARISON, not a render: `x.action==='BUY'`
        # emits the ternary's fixed literal, never the field text.
        after = body[fm.end():].lstrip()
        if after[:2] in ("==", "!=") or after[:3] in ("===", "!=="):
            continue
        left = body[: fm.start()]
        # Count safe-wrapper openings vs closes to the left of the field. If a
        # safe wrapper is currently "open" (more '(' from safe wrappers than ')'),
        # the field is inside it.
        safe_opens = len(_SAFE_WRAPPER.findall(left))
        if safe_opens == 0:
            return False
        # crude but effective: ensure there is an unbalanced '(' after the last
        # safe wrapper call (i.e. the field sits inside the wrapper's parens).
        last = list(_SAFE_WRAPPER.finditer(left))[-1]
        after = left[last.end():]
        if after.count(")") > after.count("("):
            return False
    return True


def test_no_raw_ai_field_sinks_in_template():
    offenders = _raw_sinks()
    assert not offenders, (
        "Un-escaped AI/external field(s) interpolated into innerHTML — wrap in "
        "daEsc()/_esc() or an allowlist classifier:\n"
        + "\n".join(f"  dashboard.html:{ln}  {frag}" for ln, frag in offenders)
    )


def test_detector_catches_a_synthetic_raw_sink(tmp_path):
    """Prove the guard has teeth: a deliberately raw `${x.verdict}` is flagged,
    and the same wrapped in daEsc() is not."""
    raw = "${x.verdict}"
    wrapped = "${daEsc(x.verdict)}"
    field_re = re.compile(r"\.(?:verdict)\b")
    assert not _field_is_wrapped(raw[2:-1], field_re)
    assert _field_is_wrapped(wrapped[2:-1], field_re)


def test_daEsc_helper_exists_in_template():
    """The escaper the sinks depend on must be defined in the template."""
    text = TEMPLATE.read_text()
    assert re.search(r"\bfunction daEsc\b|\bdaEsc\s*=", text), "daEsc() not defined"
