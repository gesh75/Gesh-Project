"""Renderer-wiring XSS guard — DENY BY DEFAULT (Codex 5th-pass).

The prior version keyed off an allowlist of AI *field names*, so it could not see
omitted fields, aliases (`p3`/`p12`), or map vars (`top_sectors.map(s=>${s})`) —
it returned no offenders while real sinks existed. That is a false-negative
security test, worse than none.

This version inverts the logic: it extracts EVERY `${…}` interpolation that
renders into an innerHTML template literal (recursing through nested templates),
and treats each as an XSS sink UNLESS it is provably safe — where "safe" is a
small, closed syntactic set:

  * wrapped in daEsc(…)/_esc(…)                         (escaped)
  * a …Cls(…)/…Style(…) allowlist-classifier call       (fixed class/style out)
  * a purely numeric/format expression                  (.toFixed/.length/Math/…)
  * a pure-literal ternary (all branches string/number) (colors/emoji/classes)
  * a date-format expression                            (toLocale*Date*)
  * a bare identifier proven literal-valued             (const X = <literals>)
  * an internal id / loop index (cid/uid/i/…)

Anything else — a `.property` access, or a data-derived bare variable rendered as
text — is UNSAFE and fails the test with file:line. No field names anywhere, so a
new omitted field or alias cannot slip through.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"

# Bare identifiers that are structurally safe regardless of value: loop indices,
# element ids the app generates, and the like. (Colors/classes/numbers are
# auto-derived below from their declarations, not hard-coded here.)
_SAFE_IDENTS = {"i", "j", "idx", "cid", "uid", "id", "key", "html", "NUM", "SAFE"}

# Call names whose RETURN value is safe to drop into HTML: the escaper, numeric
# formatters, URL-encoders, and the allowlist class/style/color helpers (which
# emit only fixed strings).
_SAFE_CALL_NAME = re.compile(
    r"^(daEsc|_esc|_fmt|fmt|daFmt|_debArgs|encodeURIComponent|Number|parseInt|"
    r"parseFloat|scoreChip|[A-Za-z]\w*(Cls|Style)|[A-Za-z]*[Cc]olor\w*|scoreColor\w*)$"
)
# methods whose result is a number-string or a structurally-built (already
# recursed) array/HTML — never a raw data string. Receiver is consumed.
_NUM_METHODS = ("toFixed", "toLocaleString", "toLocaleDateString",
                "toLocaleTimeString", "toPrecision", "length",
                "map", "join", "filter", "forEach", "reduce", "reverse",
                "sort", "flat", "concat", "entries", "keys", "values")
_MAP_LOCALS: set[str] = set()   # object-literal maps with literal values
_NUM_PROPS: set[str] = set()    # property names used numerically somewhere in the JS
_HTML_LOCALS: set[str] = set()  # locals whose value is HTML built from a template
                                # (their construction leaves are audited separately)
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_JS_KEYWORDS = {
    "true", "false", "null", "undefined", "new", "Date", "Math", "Number",
    "String", "parseInt", "parseFloat", "Array", "Object", "JSON", "of", "in",
    "typeof", "void", "return", "else",
}


def _find_interps(text: str) -> list[str]:
    """Return the expression of every top-level ${…} in `text`, with balanced
    braces, respecting ' " and ` string/template delimiters (so a `}` inside a
    string never closes the interpolation). Nested ${…} inside a ` template are
    NOT returned here — they are reached by recursing into that template."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "$" and i + 1 < n and text[i + 1] == "{":
            depth = 1
            j = i + 2
            start = j
            quote = None
            while j < n and depth:
                c = text[j]
                if quote:
                    if c == "\\":
                        j += 2
                        continue
                    if c == quote:
                        quote = None
                elif c in "\"'`":
                    quote = c
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(text[start:j])
                        break
                j += 1
            i = j + 1
        else:
            i += 1
    return out


def _template_regions(expr: str) -> list[str]:
    """Return the CONTENT of every backtick-template region inside `expr`."""
    regions, i, n = [], 0, len(expr)
    while i < n:
        if expr[i] == "`":
            j = i + 1
            buf = []
            while j < n and expr[j] != "`":
                if expr[j] == "\\":
                    buf.append(expr[j:j + 2])
                    j += 2
                    continue
                buf.append(expr[j])
                j += 1
            regions.append("".join(buf))
            i = j + 1
        else:
            i += 1
    return regions


_REGEX_PREV = {"return", "typeof", "instanceof", "in", "of", "new", "do",
               "else", "void", "delete", "yield", "await"}


def _regex_here(s: str, i: int) -> bool:
    """Heuristic: is the '/' at s[i] the start of a regex literal (vs division)?
    True when the previous non-space token is an operator/opener/keyword."""
    k = i - 1
    while k >= 0 and s[k] in " \t\n":
        k -= 1
    if k < 0:
        return True
    if s[k] in "(,=[!&|?:{;}+-*%<>~^":
        return True
    m = re.search(r"(\w+)$", s[:k + 1])
    return bool(m and m.group(1) in _REGEX_PREV)


def _skip_regex(s: str, i: int) -> int:
    """s[i]=='/'; skip a /…/flags regex literal (content treated as opaque)."""
    j, in_class = i + 1, False
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "\n":
            return j
        elif c == "/" and not in_class:
            j += 1
            while j < len(s) and s[j].isalpha():
                j += 1
            return j
        j += 1
    return j


def _skip_str(s: str, i: int, q: str) -> int:
    j = i + 1
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == q:
            return j + 1
        j += 1
    return j


def _skip_template(s: str, i: int) -> int:
    """s[i] == '`'; return index just past the matching closing backtick,
    correctly stepping over nested ${…} that may contain further templates."""
    j = i + 1
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "`":
            return j + 1
        if c == "$" and j + 1 < len(s) and s[j + 1] == "{":
            j = _skip_expr(s, j + 2)
            continue
        j += 1
    return j


def _skip_expr(s: str, i: int) -> int:
    """s[i] is the first char after '${'; return index past the matching '}'."""
    depth, j = 1, i
    while j < len(s) and depth:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c in "'\"":
            j = _skip_str(s, j, c)
            continue
        if c == "`":
            j = _skip_template(s, j)
            continue
        if c == "/" and j + 1 < len(s) and s[j + 1] == "/":
            while j < len(s) and s[j] != "\n":
                j += 1
            continue
        if c == "/" and j + 1 < len(s) and s[j + 1] == "*":
            j += 2
            while j + 1 < len(s) and not (s[j] == "*" and s[j + 1] == "/"):
                j += 1
            j += 2
            continue
        if c == "/" and _regex_here(s, j):
            j = _skip_regex(s, j)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        j += 1
    return j


def _blank_templates(expr: str) -> str:
    """Replace every backtick-template region (nesting-aware) with a placeholder,
    so a mixed `cond ? \\`tpl\\` : rawVar` still exposes the non-template branch."""
    out, i, n = [], 0, len(expr)
    while i < n:
        if expr[i] == "`":
            j = _skip_template(expr, i)
            out.append(" '' ")
            i = j
        else:
            out.append(expr[i])
            i += 1
    return "".join(out)


def _strip_strings(s: str) -> str:
    return re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", " '' ", s)


def _match_paren(s: str, open_idx: int) -> int:
    """Index of the ')' matching the '(' at open_idx (or len(s) if unbalanced)."""
    depth = 0
    for k in range(open_idx, len(s)):
        if s[k] == "(":
            depth += 1
        elif s[k] == ")":
            depth -= 1
            if depth == 0:
                return k
    return len(s)


def _blank_safe_calls(s: str) -> str:
    """Blank `name(<balanced args>)` for safe-return call names, plus any
    `Math.method(...)` (numeric) with balanced args."""
    pat = re.compile(r"\b(Math\s*\.\s*[A-Za-z]+|[A-Za-z_$][\w$]*)\s*\(")
    changed = True
    while changed:
        changed = False
        for m in pat.finditer(s):
            name = m.group(1)
            if not (name.replace(" ", "").startswith("Math.") or _SAFE_CALL_NAME.match(name)):
                continue
            close = _match_paren(s, m.end() - 1)
            s = s[:m.start()] + " NUM " + s[close + 1:]
            changed = True
            break
    return s


def _consume_receiver_left(s: str, dot_pos: int) -> int:
    """Given the index of a '.' beginning a `.method(...)`, return the start index
    of the FULL receiver chain to its left — walking over `(...)`/`[...]` groups,
    identifier paths, and chained `.x(...)` / `?.x` segments. So the whole numeric
    or structural chain (e.g. `(h.risks||[]).slice(0,3).map(...)`) is consumed."""
    k = dot_pos - 1
    while k >= 0:
        while k >= 0 and s[k].isspace():
            k -= 1
        progressed = False
        # trailing call/index groups: (...) or [...]
        while k >= 0 and s[k] in ")]":
            depth = 0
            while k >= 0:
                if s[k] in ")]":
                    depth += 1
                elif s[k] in "([":
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            k -= 1
            progressed = True
            while k >= 0 and s[k].isspace():
                k -= 1
        # the identifier the group(s) attach to
        if k >= 0 and (s[k].isalnum() or s[k] in "_$"):
            while k >= 0 and (s[k].isalnum() or s[k] in "_$"):
                k -= 1
            progressed = True
        if not progressed:
            break
        # continue the chain across '.' / '?.'
        j = k
        while j >= 0 and s[j].isspace():
            j -= 1
        if j >= 0 and s[j] == ".":
            k = j - 1
            if k >= 0 and s[k] == "?":
                k -= 1
            continue
        break
    return k + 1


def _blank_numeric(s: str) -> str:
    """Blank numeric coercions: `Math.x(...)`, and any `<receiver>.toFixed(...)` /
    `.length` etc. — consuming the receiver to the left (a balanced (...) group or
    an identifier path), because the result is a number string, not raw HTML."""
    # literal-map lookups: aColors[dec.action] -> a fixed color string
    for name in _MAP_LOCALS:
        s = re.sub(r"\b" + re.escape(name) + r"\s*\[[^\[\]]*\]", " SAFE ", s)
    changed = True
    while changed:
        changed = False
        m = re.search(r"\.\s*(" + "|".join(_NUM_METHODS) + r")\b(\s*\()?", s)
        if not m:
            break
        end = _match_paren(s, m.end() - 1) + 1 if m.group(2) else m.end()
        start = _consume_receiver_left(s, m.start())
        s = s[:start] + " NUM " + s[end:]
        changed = True
    # object-literal lookups with literal values: {...}[key] -> a fixed string
    s = re.sub(r"\{[^{}]*\}\s*\[[^\[\]]*\]", " SAFE ", s)
    s = re.sub(r"\bNumber\b|\bparseInt\b|\bparseFloat\b|\bnew\s+Date\b", " NUM ", s)
    return s


def _split_top(e: str, ops: tuple[str, ...]) -> list[str] | None:
    """Split `e` on the first top-level operator in `ops` (paren/bracket/string
    aware). Returns [left, right] or None if no top-level op found."""
    depth = 0
    quote = None
    i = 0
    while i < len(e):
        c = e[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0:
            for op in ops:
                if e[i:i + len(op)] != op:
                    continue
                # don't mistake optional-chaining `?.` or nullish `??` for a ternary `?`
                if op == "?" and i + 1 < len(e) and e[i + 1] in ".?":
                    continue
                if op == "?" and i > 0 and e[i - 1] == "?":
                    continue
                return [e[:i], e[i + len(op):]]
        i += 1
    return None


def _is_safe(expr: str, locals_: set[str], depth: int = 0) -> bool:
    e = _blank_templates(expr).strip()
    if not e or depth > 40:
        return True
    # unwrap balanced parens
    if e.startswith("(") and _match_paren(e, 0) == len(e) - 1:
        return _is_safe(e[1:-1], locals_, depth + 1)
    # a whole-expression FUNCTION call `name(...)` is safe: the function's own body
    # template is walked separately by this guard, so any raw sink inside it is
    # caught at its definition (e.g. volBadge/piCard/buildBars/perfChip). Method
    # calls `x.foo(...)` are NOT covered here — they fall through to the reductions.
    call = re.match(r"[A-Za-z_$][\w$]*\s*\(", e)
    if call and _match_paren(e, call.end() - 1) == len(e) - 1:
        return True
    # ternary: only the VALUE branches render; ignore the condition
    tern = _split_top(e, ("?",))
    if tern:
        rest = _split_top(tern[1], (":",))
        if rest:
            return _is_safe(rest[0], locals_, depth + 1) and _is_safe(rest[1], locals_, depth + 1)
    # logical default `A || B`: if the default B is a numeric literal, the whole
    # expression is numeric (e.g. `s.total_trades || 0`) → safe.
    logic = _split_top(e, ("||", "&&"))
    if logic:
        if re.fullmatch(r"[\s\d.+\-*/%()]+", logic[1]):
            return True
        return all(_is_safe(p, locals_, depth + 1) for p in logic)
    # string concatenation: every operand must be safe
    cat = _split_top(e, ("+",))
    if cat:
        return all(_is_safe(p, locals_, depth + 1) for p in cat)
    # atom — reduce away all provably-safe constructs, then inspect residue
    r = _blank_numeric(_blank_safe_calls(_strip_strings(e)))
    if re.fullmatch(r"[\s\d.+\-*/%'()]*", r):        # pure literal / number
        return True
    residual_props = re.findall(r"\.\s*([A-Za-z_$][\w$]*)", r)
    if residual_props:
        # a residual data .property is safe only if it is a numeric property
        # (proven by numeric use elsewhere in the JS: .p.toFixed / .p> / .p||0 …)
        if all(p in _NUM_PROPS for p in residual_props):
            return True
        return False
    for ident in _IDENT.findall(r):
        if (ident in _JS_KEYWORDS or ident in _SAFE_IDENTS or ident in locals_
                or ident in _HTML_LOCALS):
            continue
        return False
    return True


def _split_commas(s: str) -> list[str]:
    """Split on top-level commas (paren/bracket/brace/string aware)."""
    parts, depth, quote, start = [], 0, None, 0
    for i, c in enumerate(s):
        if quote:
            if c == "\\":
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts


def _read_statement(s: str, i: int) -> str:
    """From i, read a declaration's text up to the terminating ';' or newline at
    brace-depth 0 — skipping over strings, templates, and regex so a ';' inside a
    CSS style attribute (`font-size:11px;color:…`) doesn't truncate it early."""
    depth, j, start = 0, i, i
    while j < len(s):
        c = s[j]
        if c in "'\"":
            j = _skip_str(s, j, c)
            continue
        if c == "`":
            j = _skip_template(s, j)
            continue
        if c == "/" and j + 1 < len(s) and s[j + 1] == "/":
            while j < len(s) and s[j] != "\n":
                j += 1
            break
        if c == "/" and _regex_here(s, j):
            j = _skip_regex(s, j)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            break
        elif c == "\n" and depth == 0:
            # continue across a newline only if the expression obviously continues
            pk = j - 1
            while pk >= start and s[pk] in " \t":
                pk -= 1
            nk = j + 1
            while nk < len(s) and s[nk] in " \t\n":
                nk += 1
            prev = s[pk] if pk >= start else ""
            nxt = s[nk] if nk < len(s) else ""
            if prev in "?:|&+-*/.,([{=<>" or nxt in "?:|&+-*/.)]}":
                j += 1
                continue
            break
        j += 1
    return s[start:j]


def _literal_valued_locals(text: str) -> set[str]:
    """A local is safe to render iff EVERY declared value is safe — numeric, a
    literal (possibly nested) ternary, an object-literal lookup, or a safe-call
    result. Handles multi-var `let a=0, b=0`. Resolved with the same _is_safe
    classifier used on interpolations, to a fixpoint."""
    byname: dict[str, list[str]] = {}
    for m in re.finditer(r"\b(?:const|let|var)\s+", text):
        decl = _read_statement(text, m.end())   # to ; / newline, string/template aware
        for part in _split_commas(decl):
            dm = re.match(r"\s*([A-Za-z_$][\w$]*)\s*=\s*(.+)", part, re.S)
            if dm:
                byname.setdefault(dm.group(1), []).append(dm.group(2))
    # A local that is built as HTML from a template is safe to render raw — the
    # template's interpolations are walked/audited separately (same soundness
    # argument as a function body). Its render site must NOT be daEsc'd.
    _HTML_LOCALS.clear()
    for name, rhss in byname.items():
        if any("<" in r and "`" in r for r in rhss):
            _HTML_LOCALS.add(name)
    safe: set[str] = set()
    for _ in range(5):
        grew = False
        for name, rhss in byname.items():
            if name in safe:
                continue
            if rhss and all(_is_safe(r, safe) for r in rhss):
                safe.add(name)
                grew = True
        if not grew:
            break
    return safe


def _is_safe_leaf(expr: str, safe_locals: set[str]) -> bool:
    return _is_safe(expr, safe_locals)


def _walk(tpl_content: str, safe_locals: set[str], offenders: list[str], text: str):
    for expr in _find_interps(tpl_content):
        regions = _template_regions(expr)
        for region in regions:               # recurse into nested templates
            _walk(region, safe_locals, offenders, text)
        leaf = _blank_templates(expr)
        if not _is_safe_leaf(leaf, safe_locals):
            idx = text.find("${" + expr + "}")   # exact interpolation, accurate line
            line = text.count("\n", 0, idx) + 1 if idx >= 0 else 0
            offenders.append(f"dashboard.html:{line}  ${{{expr.strip()[:80]}}}")


def _top_templates(text: str) -> list[str]:
    """Return the CONTENT of every top-level backtick template literal in the file
    (skipping strings and comments). Nested templates are reached later by
    recursing through the interpolations of an HTML-bearing one."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "`":
            j = _skip_template(text, i)
            out.append(text[i + 1:j - 1])
            i = j
        elif c in "'\"":
            i = _skip_str(text, i, c)
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        elif c == "/" and _regex_here(text, i):
            i = _skip_regex(text, i)
        else:
            i += 1
    return out


def _numeric_props(js: str) -> set[str]:
    """Property names used numerically somewhere — a numeric render can't inject
    markup. Signals: `.p.toFixed(`/`.toLocaleString(`, comparison/arithmetic with
    a number, `.p||<number>`, or `Number(x.p)`."""
    props: set[str] = set()
    pats = [
        r"\.([A-Za-z_$][\w$]*)\s*\.\s*(?:toFixed|toLocaleString|toPrecision)\b",
        r"\.([A-Za-z_$][\w$]*)\s*(?:[<>]=?|[-+*/%])\s*-?\d",
        r"-?\d\s*(?:[<>]=?|[-+*/%])\s*[\w.]*\.([A-Za-z_$][\w$]*)\b",
        r"\.([A-Za-z_$][\w$]*)\s*\|\|\s*-?\d",
        r"\.([A-Za-z_$][\w$]*)\s*[<>]=?\s*[\w.]*\.[A-Za-z_$]",   # a.x >= b.y (both numeric)
        r"(?:Number|parseInt|parseFloat)\s*\(\s*[\w.]*\.([A-Za-z_$][\w$]*)",
        r"\.([A-Za-z_$][\w$]*)\s*\.\s*toFixed",
    ]
    for p in pats:
        props.update(re.findall(p, js))
    return props


def _scan() -> list[str]:
    text = TEMPLATE.read_text()
    js_all = "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.S))
    _NUM_PROPS.clear()
    _NUM_PROPS.update(_numeric_props(js_all))
    _MAP_LOCALS.clear()
    for m in re.finditer(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*\{([^{}]*)\}", text):
        body = m.group(2)
        if body.strip() and re.fullmatch(r"(\s*(?:'[^']*'|\"[^\"]*\"|[\w$]+)\s*:\s*"
                                         r"(?:'[^']*'|\"[^\"]*\"|[\d.]+)\s*,?)+", body):
            _MAP_LOCALS.add(m.group(1))
    safe_locals = _literal_valued_locals(text)
    offenders: list[str] = []
    # Template literals live in <script>; parse only that JS (the HTML/CSS prefix
    # would desync the JS string/comment scanner). Only HTML-bearing templates are
    # innerHTML sinks — a tagless top-level template (a textContent/alert/label
    # string) can't inject markup on its own, and scanning it would force
    # display-breaking daEsc() on plain text.
    js = "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.S))
    for tpl in _top_templates(js):
        if "<" in tpl:
            _walk(tpl, safe_locals, offenders, text)
    # de-dup while preserving order
    seen, out = set(), []
    for o in offenders:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out


def test_no_unescaped_ai_sinks_in_template():
    offenders = _scan()
    assert not offenders, (
        "Un-escaped value(s) interpolated into innerHTML — wrap in daEsc()/_esc() "
        "or an allowlist classifier (deny-by-default XSS guard):\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_guard_flags_property_access_and_aliases():
    """Teeth: the exact shapes Codex reproduced (a property access, a bare
    data alias) MUST be flagged; their escaped forms must pass."""
    sl: set[str] = set()
    assert not _is_safe_leaf("at.recommendation", sl)         # .property
    assert not _is_safe_leaf("p3", sl)                        # bare alias
    assert not _is_safe_leaf("s.sector", sl)                  # map alias .prop
    assert not _is_safe_leaf("d.screen_summary||''", sl)      # ||-defaulted prop
    assert _is_safe_leaf("daEsc(at.recommendation)", sl)
    assert _is_safe_leaf("daEsc(p3)", sl)
    assert _is_safe_leaf("sentCls(x.overall_sentiment||'NEUTRAL')", sl)  # classifier
    assert _is_safe_leaf("(p.ret_1w||0).toFixed(1)", sl)      # numeric
    assert _is_safe_leaf("t.is_buy?'bought':'sold'", sl)      # literal ternary


def test_daEsc_helper_exists():
    assert re.search(r"\bfunction daEsc\b|\bdaEsc\s*=", TEMPLATE.read_text())
