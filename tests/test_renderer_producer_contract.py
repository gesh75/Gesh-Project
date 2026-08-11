"""Every key a customer renderer reads must exist in what its producer emits.

This bug class has shipped to production TWICE:

  1. The Scanner/Intelligence panes read `score`, `form`, `filed`, `rr`, `ratio`
     — none of which any producer emits. The panes rendered a column of bare
     tickers and em-dashes.
  2. The SEC column reads `summary`, but every producer nests it at
     `analysis.summary`, so the body was blank under a correct header.

Both passed the XSS guard, the owner-isolation guard, the leak sweep and CI,
because all of those check STRUCTURE and SAFETY — none checks SEMANTICS. A
renderer that reads a key nobody emits is perfectly safe and perfectly useless.

So the fix for the class, not the instance: extract the keys each renderer reads
straight from the shipped template, then compare them against the REAL producer
output. Keys are extracted automatically so a new pane cannot silently skip the
check; only the renderer -> producer mapping is declared by hand.

If this test fails, the renderer is lying to a paying customer about their money.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"

# Keys that are NOT row data: locals, helpers, JS builtins, DOM. Excluded so the
# extractor yields the data contract and not the language.
_NOT_DATA = {
    "length", "map", "join", "filter", "slice", "split", "push", "toFixed",
    "innerHTML", "innerText", "forEach", "reduce", "sort", "keys", "values",
    "toUpperCase", "toLowerCase", "trim", "replace", "includes", "concat",
    "classList", "style", "dataset", "textContent", "getTime",
}


def _fn_body(name: str) -> str:
    """Source of one top-level `function name(...) { ... }` from the template."""
    src = TEMPLATE.read_text()
    start = src.find(f"function {name}(")
    assert start != -1, f"renderer {name} not found in dashboard.html"
    i = src.find("{", start)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces in {name}")


def _keys_read_off(body: str, var: str) -> set[str]:
    """Property names read off `var` inside a function body."""
    found = set(re.findall(rf"\b{re.escape(var)}\.([A-Za-z_]\w*)", body))
    return {k for k in found if k not in _NOT_DATA}


# ─── the declared contracts ──────────────────────────────────────────────────
# (renderer, row-variable, callable returning ONE representative real row)

def _sec_row():
    """A real SEC row, via the real producer, off a SEEDED table.

    Seeding is not optional. conftest isolates the databases per test, so calling
    the producer against ambient state returns [] and the contract check SKIPS —
    silently protecting nothing. That is the identical blindness that hid the
    /api/consensus ECG leak (see tests/test_consensus_symbol_ecg_leak.py): an
    isolated empty table makes the dangerous path look safe.
    """
    import sec_agent
    import server

    # Write through the APP's own persistence rather than hand-rolled SQL: it
    # creates the table and owns the column layout, so this seed cannot drift
    # away from the real schema the way a copied CREATE TABLE would.
    sec_agent._save_to_db(
        "TSLA",
        {
            "form_type": "8-K",
            "filing_date": "2026-07-22T00:00:00",
            "filing_url": "https://www.sec.gov/example",
        },
        {"summary": "Seeded filing summary.", "sentiment": "NEUTRAL",
         "action_signal": "NEUTRAL", "urgency": "LOW"},
    )
    rows = server._customer_sec_block(["TSLA"], 30)["results"]
    return rows[0] if rows else None


def _fundamentals_row():
    import fundamental_agent as fa

    rows = [r for r in (fa._fetch_fundamentals_batch(["AMD"]) or []) if not r.get("error")]
    return rows[0] if rows else None


def _sector_row():
    import server

    block = server.batch_sector_check(["AMD"])
    pool = (block.get("hot") or []) + (block.get("cold") or [])
    return pool[0] if pool else None


CONTRACTS = [
    ("buildBookIntelligencePane", "secF", _sec_row, "SEC filing row"),
    ("buildBookIntelligencePane", "fundF", _fundamentals_row, "fundamentals row"),
    ("buildBookScannerPane", "h", _sector_row, "sector pre-filter row"),
]


@pytest.mark.parametrize("fn,var,producer,label", CONTRACTS, ids=[c[3] for c in CONTRACTS])
def test_renderer_only_reads_keys_the_producer_emits(fn, var, producer, label):
    """The renderer must not read a key the producer never sets.

    NOTE this asserts on a REAL producer row, not a fixture. A fixture written by
    the same author who wrote the renderer only proves the renderer survives the
    shape that author imagined — which is precisely how both shipped bugs passed.
    """
    row = producer()
    if row is None:
        pytest.skip(f"no live {label} available in this environment")

    read = _keys_read_off(_fn_body(fn), var)
    assert read, f"extractor found no keys for {var} in {fn} — the check would be vacuous"

    missing = sorted(k for k in read if k not in row)
    assert not missing, (
        f"{fn} reads {missing} off a {label}, which the producer does not emit. "
        f"Producer emits: {sorted(row)}. "
        f"The pane will render blanks or em-dashes for those fields."
    )


def test_at_least_the_seeded_contract_always_runs():
    """A file of skips protects nothing.

    Two of the three contracts above call producers that need network, so they
    SKIP in the offline suite and in CI. Only the SEC contract is seeded and
    therefore always executes. If that ever starts skipping too, this whole file
    silently stops guarding anything — which is the same failure mode as the
    isolated-empty-table blindness it was written to escape. So assert it.
    """
    assert _sec_row() is not None, (
        "the seeded SEC contract could not produce a row — the renderer/producer "
        "check is now entirely skips and is protecting nothing"
    )


def test_extractor_actually_works():
    """Guards the guard: if _keys_read_off silently returned nothing, every
    assertion above would pass vacuously against any amount of breakage."""
    body = "function x(d){ return `${d.alpha} ${d.beta}` + d.gamma.length; }"
    assert _keys_read_off(body, "d") == {"alpha", "beta", "gamma"}
