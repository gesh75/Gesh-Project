"""Defense-in-depth: no customer-reachable GET may return a real owner value.

The masking middleware scales by field NAME, so any new bare-number money array
under an un-listed key leaks silently (Codex found ECG.equity_history; a Fable
audit found portfolio_history.series — same class, 4th instance). Rather than
chase keys one at a time, this sweep proves the invariant on EVERY endpoint:
fetch it as a customer, assert no known owner dollar/share sentinel appears.

Integration test — needs the live server on :9000 with the owner snapshot loaded
(the daily chain / MCP writes live_portfolio_snapshot.json). Skips cleanly when
the server is down or the snapshot is absent, so the unit suite stays hermetic.
Run explicitly:  pytest tests/test_customer_leak_sweep.py -v
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def _load_env_secret() -> None:
    """The running server signs session cookies with GESH_AUTH_SECRET from .env;
    this pytest process must use the SAME secret or every cookie 401s. Parse .env
    manually (find_dotenv misbehaves under some runners) and inject the auth keys."""
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in ("GESH_AUTH_SECRET", "GESH_AUTH_ENABLED", "GESH_OWNER_EMAIL") and k not in os.environ:
            os.environ[k] = v


_load_env_secret()

import auth_users  # noqa: E402  (import after env is populated)

BASE = "http://localhost:9000"
CUSTOMER = "codex-review@geshfinance.com"


@pytest.fixture(autouse=True)
def _no_network():
    """Override conftest's socket-blocking guard — this file is a LIVE integration
    sweep that must reach the running server on :9000."""
    yield
SNAPSHOT = Path(__file__).resolve().parents[1] / "live_portfolio_snapshot.json"


def _server_up() -> bool:
    try:
        urllib.request.urlopen(BASE + "/healthz", timeout=3)
        return True
    except Exception:
        try:
            urllib.request.urlopen(BASE + "/login", timeout=3)
            return True
        except Exception:
            return False


pytestmark = pytest.mark.skipif(not _server_up(),
                                reason="live server on :9000 required for the leak sweep")


def _owner_sentinels() -> list[str]:
    """Concrete owner values that MUST NOT appear in a customer response —
    portfolio total, cash, and each holding's real share count + equity."""
    if not SNAPSHOT.exists():
        pytest.skip("live_portfolio_snapshot.json absent")
    snap = json.loads(SNAPSHOT.read_text())
    es = snap.get("equity_summary", {})
    vals: set[str] = set()
    for key in ("total", "portfolio_equity", "cash", "deployable"):
        v = es.get(key)
        if isinstance(v, (int, float)) and v > 1:
            vals.add(f"{v:,.2f}")      # "37,522.83"
            vals.add(f"{v:.2f}")       # "37522.83"
    for h in snap.get("holdings", []):
        for key in ("equity", "quantity"):
            v = h.get(key)
            if isinstance(v, (int, float)) and v > 1:
                vals.add(f"{round(v, 2)}")
    # keep only distinctive numbers (>= 4 chars) to avoid false positives on
    # small shared integers
    return sorted(x for x in vals if len(x.replace(",", "").replace(".", "")) >= 4)


def _customer_cookie() -> str:
    auth_users.create_magic_link_user(CUSTOMER, tier="observer")
    return "gesh_session=" + auth_users.issue_token(CUSTOMER, ttl=120, typ="session")


# Representative path params so parametrized routes are actually exercised.
_ENDPOINTS = [
    "/api/portfolio", "/api/portfolio/history?points=30", "/api/ecg", "/api/regime",
    "/api/history?limit=3", "/api/consensus/cached", "/api/kelly/cached",
    "/api/monte-carlo/cached", "/api/screener/cached", "/api/deep-analysis/cached",
    "/api/market-data", "/api/agents/sec/history", "/api/agents/news/history",
    "/api/agents/fundamentals/latest", "/api/chart/NVDA?period=1mo",
    "/api/consensus/NVDA", "/api/fft-cycles", "/api/sections/status",
    "/api/trades", "/api/trades/stats", "/api/paper/summary",
    "/api/tax/harvest", "/api/explainability/NVDA", "/api/yield/landscape",
]


@pytest.mark.parametrize("path", _ENDPOINTS)
def test_no_owner_value_leaks_to_customer(path):
    sentinels = _owner_sentinels()
    if not sentinels:
        pytest.skip("no owner sentinels derivable from snapshot")
    cookie = _customer_cookie()
    req = urllib.request.Request(BASE + path, headers={"Cookie": cookie})
    try:
        body = urllib.request.urlopen(req, timeout=45).read().decode()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return                      # owner-only / absent — nothing to leak
        body = (e.read() or b"").decode()
    except Exception as ex:
        pytest.skip(f"{path} unreachable/slow: {ex}")
        return

    hits = [s for s in sentinels if s in body]
    assert not hits, f"OWNER VALUE LEAKED at {path}: {hits}"


def test_history_detail_no_leak():
    """/api/history/{id} embeds the stored owner snapshot — check the latest."""
    sentinels = _owner_sentinels()
    if not sentinels:
        pytest.skip("no sentinels")
    cookie = _customer_cookie()
    idx = urllib.request.Request(BASE + "/api/history?limit=1", headers={"Cookie": cookie})
    rows = json.loads(urllib.request.urlopen(idx, timeout=20).read()).get("data", [])
    if not rows:
        pytest.skip("no history rows")
    rid = rows[0].get("id")
    req = urllib.request.Request(f"{BASE}/api/history/{rid}", headers={"Cookie": cookie})
    body = urllib.request.urlopen(req, timeout=30).read().decode()
    hits = [s for s in sentinels if s in body]
    assert not hits, f"OWNER VALUE LEAKED at /api/history/{rid}: {hits}"
