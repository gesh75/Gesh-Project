"""Operator JavaScript must not ship in the customer bundle.

The DOM composition landed first; the operator's JS FUNCTION BODIES kept
shipping to every customer as dead code that still disclosed internal route
paths, and the command palette still carried operator actions. This asserts the
residue is ZERO — it replaces the earlier test that deliberately pinned the
residue as a tracked limitation.
"""

from __future__ import annotations

import re

import pytest

import server

OWNER_FUNCTIONS = [
    "daRunAnalysis", "daLoadHistory", "daLoadRun", "daAutoLoadLatest", "daRender",
    "runScreener", "loadCachedScreener", "runAllAgents", "loadIntelHistory",
    "loadScannerConfig", "saveScannerTf", "loadTradeStats", "runDryRun",
    "renderGateVerdict", "runConsensus", "runKelly", "runMonteCarlo",
    "loadCachedConsensus", "loadCachedKelly", "loadCachedMonteCarlo",
    "tryLoadCachedAnalysis", "runMainDebate", "loadECG",
]
OWNER_ENDPOINTS = [
    "/api/trading/dry-run", "/api/paper/run", "/api/trades/stats",
    "/api/agents/fundamentals", "/api/screener", "/api/config", "/api/history",
    "/api/kelly", "/api/monte-carlo", "/api/ecg", "/api/deep-analysis",
]
CUSTOMER_REQUIRED = [
    "analyzeTicker", "mybookAnalyze", "loadMyBook", "buildAICard",
    "buildStancePanel", "switchTab", "openCmdk", "renderCmdk", "loadWatchlist",
]


@pytest.fixture(scope="module")
def customer_doc() -> str:
    return server._render_dashboard("customer")


@pytest.fixture(scope="module")
def owner_doc() -> str:
    return server._render_dashboard("owner")


@pytest.mark.parametrize("fn", OWNER_FUNCTIONS)
def test_no_owner_function_definition_in_customer_bundle(customer_doc, fn):
    assert not re.search(rf"function\s+{fn}\b", customer_doc), (
        f"operator function {fn}() still defined in the customer bundle")


# GET /api/kelly/cached is customer-or-owner. The operator surface is the
# unsuffixed POST. The cache path contains the operator prefix as a substring,
# so drop it before the prefix check.
_CUSTOMER_CACHE_PATHS = ("/api/kelly/cached",)


def _without_customer_caches(doc: str) -> str:
    for path in _CUSTOMER_CACHE_PATHS:
        doc = doc.replace(path, "")
    return doc


@pytest.mark.parametrize("endpoint", OWNER_ENDPOINTS)
def test_no_owner_endpoint_string_in_customer_bundle(customer_doc, endpoint):
    assert endpoint not in _without_customer_caches(customer_doc), (
        f"operator endpoint {endpoint} referenced in the customer bundle")


def test_customer_signals_pane_reads_the_published_kelly_cache(customer_doc):
    assert "/api/kelly/cached" in customer_doc
    assert "function kellyUninformativeBanner" in customer_doc
    assert "function loadBookKellyNote" in customer_doc


def test_command_palette_has_no_operator_actions_for_a_customer(customer_doc):
    """CMDK_ACTIONS is empty in the shared bundle; the owner script appends to it."""
    m = re.search(r"const CMDK_ACTIONS\s*=\s*(\[[^\n]*\]);", customer_doc)
    assert m, "CMDK_ACTIONS declaration not found"
    assert m.group(1).strip() == "[]", f"customer palette carries actions: {m.group(1)[:120]}"
    assert "CMDK_ACTIONS.push" not in customer_doc


def test_command_palette_is_populated_for_the_owner(owner_doc):
    assert "CMDK_ACTIONS.push" in owner_doc
    assert "Run Daily Analysis" in owner_doc


@pytest.mark.parametrize("fn", OWNER_FUNCTIONS)
def test_owner_keeps_every_operator_function(owner_doc, fn):
    assert re.search(rf"function\s+{fn}\b", owner_doc), f"owner lost {fn}()"


@pytest.mark.parametrize("fn", CUSTOMER_REQUIRED)
def test_customer_bundle_keeps_its_own_functions(customer_doc, fn):
    assert re.search(rf"function\s+{fn}\b", customer_doc), f"customer lost {fn}()"


def test_customer_bundle_has_no_unguarded_call_to_a_removed_function(customer_doc):
    """The page must initialize without a ReferenceError. Every surviving call
    site for a removed function has to be `typeof fn === 'function'` guarded."""
    unguarded = []
    for fn in OWNER_FUNCTIONS:
        for m in re.finditer(rf"\b{fn}\s*\(", customer_doc):
            line_start = customer_doc.rfind("\n", 0, m.start()) + 1
            line = customer_doc[line_start:customer_doc.find("\n", m.start())]
            if f"typeof {fn}" in line or f"{fn}&&" in line or f"{fn} &&" in line:
                continue
            if line.lstrip().startswith("//") or line.lstrip().startswith("/*"):
                continue
            unguarded.append(f"{fn}: {line.strip()[:100]}")
    assert not unguarded, "unguarded call(s) to an owner-only function:\n" + "\n".join(unguarded)


def test_both_documents_have_balanced_scripts(customer_doc, owner_doc):
    for name, doc in (("customer", customer_doc), ("owner", owner_doc)):
        assert doc.lower().count("<script") == doc.lower().count("</script>"), name
        assert doc.count("</html>") == 1, name


def test_owner_script_slot_is_absent_from_the_customer_document(customer_doc):
    assert "GESH_SLOT" not in customer_doc
    assert "OPERATOR-ONLY JAVASCRIPT" not in customer_doc


def test_customer_render_never_reads_the_owner_partial(monkeypatch):
    monkeypatch.setattr(server, "_owner_blocks",
                        lambda: pytest.fail("owner partial read for a customer render"))
    server._render_dashboard("customer")
