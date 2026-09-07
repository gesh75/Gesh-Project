"""Consensus Node 12 votes + the ECG fail-soft wrapper.

``crypto_risk_summary`` weighting is owned by #238 / test_crypto_risk_weighted.
This file covers the *node* contract: high BTC overlap must vote BEARISH,
missing data must ABSTAIN (not NEUTRAL — a silent 0 moves consensus), and
``node_ecg_safe`` must never crash a run when ECG/db is broken.

Loaded from source so this file does not import price_source / gesh-core.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SRC = (Path(__file__).resolve().parents[1] / "crypto_correlation.py").read_text()
_WANTED = {"node_crypto_correlation", "_holding_equity", "node_ecg_safe"}
_CONSTS = {
    "HIGH_CORR_THRESHOLD",
    "LOW_CORR_THRESHOLD",
    "DEFAULT_LOOKBACK",
    "BULLISH",
    "NEUTRAL",
    "BEARISH",
    "ABSTAIN",
}


def _load():
    tree = ast.parse(_SRC)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            chunk = ast.get_source_segment(_SRC, node)
            assert chunk is not None
            chunks.append(chunk)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _CONSTS:
                    chunk = ast.get_source_segment(_SRC, node)
                    assert chunk is not None
                    chunks.append(chunk)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in _CONSTS:
                chunk = ast.get_source_segment(_SRC, node)
                assert chunk is not None
                chunks.append(chunk)
    ns: dict[str, Any] = {
        "Any": Any,
        "get_crypto_correlations": lambda *a, **k: {},
    }
    exec("\n\n".join(chunks), ns)  # noqa: S102 — extracted production helpers only
    return ns


_NS = _load()
node_crypto_correlation = _NS["node_crypto_correlation"]
_holding_equity = _NS["_holding_equity"]
node_ecg_safe = _NS["node_ecg_safe"]


def test_extracted_helpers_and_vote_constants():
    assert set(_NS) >= _WANTED | _CONSTS
    assert _NS["BULLISH"] == 1
    assert _NS["NEUTRAL"] == 0
    assert _NS["BEARISH"] == -1
    assert _NS["ABSTAIN"] is None
    assert _NS["HIGH_CORR_THRESHOLD"] == pytest.approx(0.70)
    assert _NS["LOW_CORR_THRESHOLD"] == pytest.approx(0.25)


def _btc(symbol: str, btc, eth=None):
    eth_map = {symbol: eth} if eth is not None else {}
    return {"BTC-USD": {symbol: btc}, "ETH-USD": eth_map}


@pytest.mark.parametrize(
    "btc,expected",
    [
        (0.75, -1),
        (0.70, -1),  # inclusive HIGH
        (0.69, 0),
        (0.50, 0),
        (0.26, 0),
        (0.25, 1),  # inclusive LOW
        (0.20, 1),
    ],
)
def test_node_crypto_vote_thresholds(monkeypatch, btc, expected):
    monkeypatch.setitem(
        _NS, "get_crypto_correlations", lambda symbols, lookback_days=90: _btc(symbols[0], btc)
    )
    vote, detail = node_crypto_correlation("MSTR")
    assert vote == expected
    assert "BTC=" in detail


def test_node_crypto_missing_btc_abstains_not_neutral(monkeypatch):
    monkeypatch.setitem(
        _NS, "get_crypto_correlations", lambda *a, **k: {"BTC-USD": {}, "ETH-USD": {}}
    )
    vote, detail = node_crypto_correlation("NVDA")
    assert vote is None
    assert "no data" in detail.lower()


def test_node_crypto_exception_abstains(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("yahoo timeout")

    monkeypatch.setitem(_NS, "get_crypto_correlations", _boom)
    vote, detail = node_crypto_correlation("COIN")
    assert vote is None
    assert "yahoo timeout" in detail


def test_holding_equity_key_priority_and_rejects_non_positive():
    assert _holding_equity({"equity": 10, "market_value": 99}) == pytest.approx(10.0)
    assert _holding_equity({"market_value": 7}) == pytest.approx(7.0)
    assert _holding_equity({"value": 3}) == pytest.approx(3.0)
    assert _holding_equity({"marketValue": 2}) == pytest.approx(2.0)
    assert _holding_equity({"equity": 0, "market_value": 5}) == pytest.approx(5.0)
    assert _holding_equity({"equity": "nope", "value": 4}) == pytest.approx(4.0)
    assert _holding_equity({"equity": None}) == 0.0
    assert _holding_equity({}) == 0.0


def test_node_ecg_safe_insufficient_history_abstains(monkeypatch):
    ecg = ModuleType("ecg")
    ecg._compute_drawdown_from_peak = lambda _eq: 0.0
    ecg._loss_streak = lambda _eq: 0
    ecg._classify = lambda _dd, _st: "HEALTHY"
    db = ModuleType("db")
    db.get_equity_history = lambda symbol, days=30: [{"equity": 100.0}] * 4
    monkeypatch.setitem(sys.modules, "ecg", ecg)
    monkeypatch.setitem(sys.modules, "db", db)
    vote, detail = node_ecg_safe("NVDA")
    assert vote is None
    assert "insufficient" in detail.lower()


def test_node_ecg_safe_suspended_is_bearish(monkeypatch):
    ecg = ModuleType("ecg")
    ecg._compute_drawdown_from_peak = lambda _eq: 40.0
    ecg._loss_streak = lambda _eq: 6
    ecg._classify = lambda _dd, _st: "SUSPENDED"
    db = ModuleType("db")
    db.get_equity_history = lambda symbol, days=30: [{"equity": 100.0 - i} for i in range(10)]
    monkeypatch.setitem(sys.modules, "ecg", ecg)
    monkeypatch.setitem(sys.modules, "db", db)
    vote, detail = node_ecg_safe("IONQ")
    assert vote == -1
    assert "SUSPENDED" in detail


@pytest.mark.parametrize("status,expected", [("CAUTION", 0), ("HEALTHY", 1)])
def test_node_ecg_safe_caution_and_healthy(monkeypatch, status, expected):
    ecg = ModuleType("ecg")
    ecg._compute_drawdown_from_peak = lambda _eq: 5.0
    ecg._loss_streak = lambda _eq: 1
    ecg._classify = lambda _dd, _st: status
    db = ModuleType("db")
    db.get_equity_history = lambda symbol, days=30: [{"equity": 100.0}] * 8
    monkeypatch.setitem(sys.modules, "ecg", ecg)
    monkeypatch.setitem(sys.modules, "db", db)
    vote, _detail = node_ecg_safe("AAPL")
    assert vote == expected


def test_node_ecg_safe_import_failure_abstains(monkeypatch):
    monkeypatch.setitem(sys.modules, "ecg", None)
    vote, detail = node_ecg_safe("NVDA")
    assert vote is None
    assert detail.startswith("ECG unavailable:")
