"""[CLAUDE V2 2026-07-19] A non-empty UNPARSEABLE provider quote_as_of must NOT
crash the run — it is dropped so the freshness gate fails closed on that symbol."""
from __future__ import annotations

import run_manifest


def _snap(prov):
    return {"source": "robinhood_mcp", "fetched_at": "2026-07-19T10:00:00Z",
            "quote_provenance": prov}


def test_unparseable_quote_as_of_is_skipped_not_fatal():
    snap = _snap({
        "NVDA": {"quote_as_of": "2026-07-19T09:59:00Z"},   # good
        "POL":  {"quote_as_of": "N/A"},                     # corrupt — must be dropped
        "AMD":  {"quote_as_of": None},                      # missing — already dropped
    })
    m = run_manifest.manifest_from_snapshot(snap)     # must NOT raise
    assert "NVDA" in m.quote_as_of
    assert "POL" not in m.quote_as_of      # unparseable dropped -> gate fails closed on POL
    assert "AMD" not in m.quote_as_of
