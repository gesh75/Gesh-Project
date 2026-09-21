"""First execution tests for trading/alerts.py — Telegram fail-soft.

Loaded by file path so this suite does not need gesh-core
(``trading/__init__.py`` aliases ``trading.state``).

Pins: disabled / missing token / missing chat never hit the network;
transport errors never raise; qty/price 0 render as an em-dash (not $0);
the bot token is assembled at call time, not stored as a module-level URL.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parents[1] / "trading" / "alerts.py"
_SPEC = importlib.util.spec_from_file_location("gesh_trading_alerts", _SRC)
assert _SPEC and _SPEC.loader
alerts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(alerts)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alerts, "ENABLED", True)
    monkeypatch.setattr(alerts, "BOT_TOKEN", "tok_test_not_real")
    monkeypatch.setattr(alerts, "CHAT_ID", "123")


def test_disabled_alerts_succeed_without_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(alerts, "ENABLED", False)
    monkeypatch.setattr(alerts, "BOT_TOKEN", "tok_test_not_real")
    monkeypatch.setattr(alerts, "CHAT_ID", "123")

    def _boom(*_a, **_k):
        raise AssertionError("Telegram must not be called when alerts are off")

    monkeypatch.setattr(alerts.requests, "post", _boom)
    assert alerts._send("hi") is True


def test_missing_token_is_suppressed_without_network(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(alerts, "ENABLED", True)
    monkeypatch.setattr(alerts, "BOT_TOKEN", "")
    monkeypatch.setattr(alerts, "CHAT_ID", "123")

    def _boom(*_a, **_k):
        raise AssertionError("Telegram must not be called without a token")

    monkeypatch.setattr(alerts.requests, "post", _boom)
    assert alerts._send("hi") is False


def test_missing_chat_id_is_suppressed_without_network(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(alerts, "ENABLED", True)
    monkeypatch.setattr(alerts, "BOT_TOKEN", "tok_test_not_real")
    monkeypatch.setattr(alerts, "CHAT_ID", "")

    def _boom(*_a, **_k):
        raise AssertionError("Telegram must not be called without a chat id")

    monkeypatch.setattr(alerts.requests, "post", _boom)
    assert alerts._send("hi") is False


def test_send_posts_html_without_link_preview(enabled, monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def _post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return SimpleNamespace(ok=True, status_code=200, text="ok")

    monkeypatch.setattr(alerts.requests, "post", _post)
    assert alerts._send("fill <b>HOOD</b>", silent=True) is True
    assert captured["url"] == "https://api.telegram.org/bottok_test_not_real/sendMessage"
    assert captured["timeout"] == alerts.TIMEOUT
    assert captured["json"]["chat_id"] == "123"
    assert captured["json"]["text"] == "fill <b>HOOD</b>"
    assert captured["json"]["parse_mode"] == "HTML"
    assert captured["json"]["disable_notification"] is True
    assert captured["json"]["disable_web_page_preview"] is True


def test_send_url_is_not_a_module_level_constant():
    """A traceback must not be able to print a baked-in send URL + token."""
    assert not hasattr(alerts, "SEND_URL")
    assert "api.telegram.org/bot" not in (getattr(alerts, "BOT_TOKEN", "") or "")


def test_http_error_is_false_not_raised(enabled, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        alerts.requests,
        "post",
        lambda *a, **k: SimpleNamespace(ok=False, status_code=400, text="bad"),
    )
    assert alerts._send("hi") is False


def test_transport_error_never_raises(enabled, monkeypatch: pytest.MonkeyPatch):
    def _boom(*_a, **_k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(alerts.requests, "post", _boom)
    assert alerts._send("hi") is False


def test_fill_zero_qty_and_price_render_as_dash(enabled, monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    monkeypatch.setattr(
        alerts,
        "_send",
        lambda text, silent=False: captured.update(text=text, silent=silent) or True,
    )
    alerts.alert_order_fill("HOOD", "BUY", 0.0, 0.0, "paper", "SIMULATED", 0.9)
    assert "Qty       : —" in captured["text"]
    assert "Price     : —" in captured["text"]
    assert "PAPER" in captured["text"]
    assert "0.0000" not in captured["text"]
    assert "$0.00" not in captured["text"]


def test_fill_none_qty_and_price_render_as_dash(enabled, monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    monkeypatch.setattr(
        alerts,
        "_send",
        lambda text, silent=False: captured.update(text=text) or True,
    )
    alerts.alert_order_fill("HOOD", "SELL", None, None, "live", "FILLED", 0.8)
    assert "Qty       : —" in captured["text"]
    assert "Price     : —" in captured["text"]
    assert "LIVE" in captured["text"]


def test_rationale_is_truncated_to_120(enabled, monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    monkeypatch.setattr(
        alerts,
        "_send",
        lambda text, silent=False: captured.update(text=text) or True,
    )
    alerts.alert_order_fill("HOOD", "BUY", 1.5, 10.0, "paper", "SIMULATED", 0.91, "x" * 200)
    reason = [ln for ln in captured["text"].splitlines() if ln.startswith("Reason")][0]
    assert reason.endswith("x" * 120)
    assert len(reason.split(": ", 1)[1]) == 120


def test_rejection_is_silent(enabled, monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    monkeypatch.setattr(
        alerts,
        "_send",
        lambda text, silent=False: captured.update(text=text, silent=silent) or True,
    )
    alerts.alert_rejection(
        "HOOD", "BUY", "POSITION_CAP", {"POSITION_CAP": False, "KILL_SWITCH": True}, 0.4
    )
    assert captured["silent"] is True
    assert "POSITION_CAP" in captured["text"]
    assert "REJECTED" in captured["text"]


@pytest.mark.parametrize(
    "pnl, cap, severity, silent",
    [
        (0.01, 0.01, "NORMAL", True),
        (0.0, 0.01, "NORMAL", True),
        (-0.007, 0.01, "WARNING", False),  # just above -0.75 * cap
        (-0.0075, 0.01, "CRITICAL", False),  # not > -0.75 * cap
        (-0.02, 0.01, "CRITICAL", False),
    ],
)
def test_daily_pnl_severity_bands(
    enabled, monkeypatch: pytest.MonkeyPatch, pnl, cap, severity, silent
):
    captured: dict = {}
    monkeypatch.setattr(
        alerts,
        "_send",
        lambda text, silent=False: captured.update(text=text, silent=silent) or True,
    )
    alerts.alert_daily_pnl(100_000.0, 99_000.0, pnl, cap)
    assert f"Status    : {severity}" in captured["text"]
    assert captured["silent"] is silent
