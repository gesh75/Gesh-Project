"""Codex #6 — truthful decision provenance: {price_quality, data_sources, decision_degraded}.

The field only has value if it is TRUE. price_source routes Yahoo-primary →
Robinhood-fallback and used to discard which branch won, so any data_sources value would
have been guessed from config — the exact fabrication the field exists to surface. The
source now rides on the frame via pandas `.attrs`, and these tests lock the contract:

  * a feed is named ONLY when it demonstrably served the bars,
  * an unattributable frame yields [] and degrades the decision — never a default feed,
  * a MIXED Yahoo+RH splice says so (combine_first inherits the LEFT frame's attrs, so
    without an explicit stamp the merged frame would silently claim "yahoo"),
  * the bad-tick guard's substitution — previously silent — is reported.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

import price_source
import server

# ── the frame-level stamp ─────────────────────────────────────────────────────

def test_frame_source_none_when_unstamped():
    """A frame this module never stamped is UNATTRIBUTABLE — not 'yahoo'."""
    assert price_source.frame_source(pd.DataFrame({"Close": [1.0]})) is None
    assert price_source.frame_source(None) is None


def test_stamp_survives_copy():
    """The TTL cache stores/returns .copy() — the source must survive it, or every
    cache hit would silently become unattributable."""
    f = price_source._stamp(pd.DataFrame({"Close": [1.0]}), "robinhood")
    assert price_source.frame_source(f.copy()) == "robinhood"


# ── the reconciled decision_quality block, fed by REAL provenance ─────────────
# The unified block/shape comes from decision_quality.py (Codex's PR #65); what these
# lock is that it is fed OBSERVED feed identity rather than the hardcoded
# ["price_source", "rh_fundamentals_52w"] constant it originally shipped with — a value
# that was identical whether Yahoo or the RH fallback served the number.

def _meta(source=None, substituted=False):
    return {"source": source, "substituted": substituted}


def test_data_sources_never_defaults_to_a_feed():
    """The core honesty rule: unattributable → no feed named, and degraded."""
    blk = server._quality_block(_meta(None), False, "")
    assert blk["data_sources"] == []
    assert blk["decision_degraded"] is True
    assert any("unattributable" in r for r in blk["reasons"])


def test_real_feed_is_named_not_a_constant():
    blk = server._quality_block(_meta("yahoo"), False, "")
    assert blk["data_sources"] == ["yahoo"]          # the feed, not "price_source"
    assert blk["decision_degraded"] is False
    assert blk["price_quality"] == "ok"


def test_robinhood_fallback_is_not_degraded_on_its_own():
    """RH is a legitimate feed, not a defect — degraded is reserved for numbers we
    have a concrete reason to distrust."""
    blk = server._quality_block(_meta("robinhood"), False, "")
    assert blk["data_sources"] == ["robinhood"]
    assert blk["decision_degraded"] is False


def test_mixed_splice_reports_both_feeds():
    blk = server._quality_block(_meta("yahoo+robinhood"), False, "")
    assert blk["data_sources"] == ["yahoo", "robinhood"]


def test_suspect_quote_degrades_and_names_the_52wk_check():
    blk = server._quality_block(_meta("yahoo"), True, "far below the 52-wk low")
    assert blk["decision_degraded"] is True
    assert blk["price_quality"] == "suspect"
    assert "rh_fundamentals_52w" in blk["data_sources"]   # named only because it ran
    assert any("52-wk" in r for r in blk["reasons"])


def test_bad_tick_substitution_is_reported_and_degrades():
    """The guard can return a prior close / the median instead of the latest quote.
    That substitution used to be invisible."""
    blk = server._quality_block(_meta("yahoo", substituted=True), False, "")
    assert blk["decision_degraded"] is True
    assert any("bad tick" in r for r in blk["reasons"])


def test_52wk_feed_not_claimed_when_check_did_not_run():
    blk = server._quality_block(_meta("yahoo"), False, "")
    assert "rh_fundamentals_52w" not in blk["data_sources"]


# ── end-to-end: the source read off the real frame ────────────────────────────

def _frame(n=30, source=None):
    f = pd.DataFrame({"Close": np.linspace(100.0, 110.0, n)})
    return price_source._stamp(f, source) if source else f


def test_latest_close_meta_reports_real_source(monkeypatch):
    monkeypatch.setattr(price_source, "yf_download",
                        lambda *a, **k: _frame(source="robinhood"), raising=True)
    price, meta = server._latest_close_meta("NVDA")
    assert price == 110.0
    assert meta["source"] == "robinhood" and meta["substituted"] is False


def test_latest_close_meta_unattributable_when_frame_unstamped(monkeypatch):
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: _frame(), raising=True)
    _price, meta = server._latest_close_meta("NVDA")
    assert meta["source"] is None                    # honest unknown, not "yahoo"


def test_latest_close_meta_flags_bad_tick_substitution(monkeypatch):
    # a $17 flash against a ~$200 series → the guard substitutes; that must be visible
    arr = [200.0] * 25 + [17.16]
    f = price_source._stamp(pd.DataFrame({"Close": arr}), "yahoo")
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: f, raising=True)
    price, meta = server._latest_close_meta("NVDA")
    assert price != 17.16                            # outlier rejected
    assert meta["substituted"] is True               # and SAID so


def test_latest_close_wrapper_unchanged(monkeypatch):
    """The plain _latest_close contract must stay byte-identical for its ~15 callers."""
    monkeypatch.setattr(price_source, "yf_download",
                        lambda *a, **k: _frame(source="yahoo"), raising=True)
    assert server._latest_close("NVDA") == 110.0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return TestClient(server.app, raise_server_exceptions=False)


def test_levels_endpoint_carries_provenance(client, monkeypatch):
    monkeypatch.setattr(server, "_latest_close_meta",
                        lambda s: (100.0, {"source": "yahoo", "substituted": False}), raising=True)
    monkeypatch.setattr(server, "_price_quality", lambda s, p: (False, ""), raising=True)
    monkeypatch.setattr("trade_levels.compute",
                        lambda sym, current_price=None: {"available": True, "symbol": sym,
                                                         "price": current_price, "support": [],
                                                         "resistance": [], "pivots": {},
                                                         "swing_high_60d": 1, "swing_low_60d": 1,
                                                         "setup": {}, "note": ""}, raising=False)
    d = client.get("/api/levels/NVDA").json()["data"]
    dq = d["decision_quality"]
    assert dq["data_sources"] == ["yahoo"]          # observed feed, not a constant
    assert dq["decision_degraded"] is False
