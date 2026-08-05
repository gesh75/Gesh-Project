"""
Guard tests for fetch_snapshot_direct.py — the LLM-free MCP snapshot client that
is step 1/5 (primary) of the daily analysis chain.

Why this file exists: PR #133's MERGE COMMIT silently corrupted this module. It
dedented `Mcp.call` out of `class Mcp` and deleted the
`status, resp_headers, body = _req(` assignment, leaving a file that could not
even parse. CI stayed green because nothing outside tests/ was ever imported,
compiled or linted; the daily chain just logged "direct MCP fetch failed" and
fell back to the paid `claude -p` path on every single run.

So these cover two layers:
  1. the file parses, and Mcp.call is a real bound method   <- the shipped bug
  2. the parse / token / MCP-protocol logic                 <- never had coverage

Everything is offline: `_req` is monkeypatched in every test that would touch
the network, and TOKEN_FILE is redirected to tmp_path by an autouse fixture so
the owner's real .rh_mcp_oauth.json is never read, written or chmod'ed.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# The module raises SystemExit at IMPORT time when GESH_RH_ACCOUNT is unset
# (a deliberate fail-loud after the hardcoded account number was removed), so
# the env var has to exist before the import below.
os.environ.setdefault("GESH_RH_ACCOUNT", "TEST_ACCOUNT_0000")

fsd = importlib.import_module("fetch_snapshot_direct")

MODULE_PATH = Path(fsd.__file__)


@pytest.fixture(autouse=True)
def _isolate_token_file(tmp_path, monkeypatch):
    """Never touch the real .rh_mcp_oauth.json (chmod 600, gitignored, live creds)."""
    monkeypatch.setattr(fsd, "TOKEN_FILE", tmp_path / ".rh_mcp_oauth.json")
    return tmp_path


# ── 1. the shipped bug ───────────────────────────────────────────────────────
def test_module_source_parses():
    """The exact regression: the merged file raised SyntaxError: unmatched ')'."""
    ast.parse(MODULE_PATH.read_text())


def test_mcp_call_is_defined_inside_the_class():
    """`def call` was dedented to module scope, so Mcp instances had no .call."""
    assert "call" in vars(fsd.Mcp), "Mcp.call must be defined INSIDE class Mcp"
    assert callable(fsd.Mcp("tok").call)


def test_mcp_tool_can_reach_call():
    """tool() delegates to self.call — broken while call lived at module scope."""
    mcp = fsd.Mcp("tok")
    calls = []

    def fake_call(method, params=None):
        calls.append((method, params))
        return {"structuredContent": {"ok": True}}

    mcp.call = fake_call
    assert mcp.tool("get_portfolio", {"a": 1}) == {"ok": True}
    assert calls == [("tools/call", {"name": "get_portfolio", "arguments": {"a": 1}})]


# ── 2. _parse_body ───────────────────────────────────────────────────────────
def test_parse_body_plain_json():
    assert fsd._parse_body('{"a": 1}') == {"a": 1}


def test_parse_body_empty_is_empty_dict():
    assert fsd._parse_body("") == {}
    assert fsd._parse_body("   \n  ") == {}


def test_parse_body_sse_single_event():
    assert fsd._parse_body('data: {"a": 1}') == {"a": 1}


def test_parse_body_sse_takes_last_event():
    """MCP streams several events; the final one carries the result."""
    raw = 'event: message\ndata: {"seq": 1}\n\nevent: message\ndata: {"seq": 2}\n'
    assert fsd._parse_body(raw) == {"seq": 2}


def test_parse_body_malformed_is_captured_not_raised():
    out = fsd._parse_body("<html>gateway error</html>")
    assert out["_unparsed"].startswith("<html>")


def test_parse_body_truncates_unparsed_to_500_chars():
    out = fsd._parse_body("x" * 900)
    assert len(out["_unparsed"]) == 500


# ── 3. token persistence ─────────────────────────────────────────────────────
def test_save_tokens_round_trip():
    fsd._save_tokens({"access_token": "abc", "refresh_token": "r"})
    assert fsd._load_tokens()["access_token"] == "abc"


def test_load_tokens_missing_file_is_empty_dict():
    assert fsd._load_tokens() == {}


def test_save_tokens_derives_expires_at_with_300s_safety_margin():
    before = time.time()
    fsd._save_tokens({"access_token": "a", "expires_in": 3600})
    got = fsd._load_tokens()["expires_at"]
    # now + 3600 - 300, allowing for clock drift across the two time.time() reads
    assert before + 3299 <= got <= time.time() + 3300


def test_save_tokens_chmods_600():
    """Live OAuth creds on disk must not be group/world readable."""
    fsd._save_tokens({"access_token": "a"})
    assert oct(fsd.TOKEN_FILE.stat().st_mode)[-3:] == "600"


# ── 4. _access_token ─────────────────────────────────────────────────────────
def _no_network(*a, **k):  # pragma: no cover - only runs if a test regresses
    raise AssertionError("_req must not be called on the cached-token path")


def test_access_token_uses_unexpired_cached_token_without_network(monkeypatch):
    fsd._save_tokens({"client_id": "c", "access_token": "cached", "expires_at": time.time() + 3600})
    monkeypatch.setattr(fsd, "_req", _no_network)
    assert fsd._access_token() == "cached"


def test_access_token_unauthenticated_exits(monkeypatch):
    fsd._save_tokens({})
    with pytest.raises(SystemExit, match="not authenticated"):
        fsd._access_token()


def test_access_token_expired_without_refresh_token_exits():
    fsd._save_tokens({"client_id": "c", "access_token": "old", "expires_at": 0})
    with pytest.raises(SystemExit, match="no refresh_token"):
        fsd._access_token()


def test_access_token_refreshes_and_preserves_identity(monkeypatch):
    """A refresh response often omits refresh_token/client_id — both must persist."""
    fsd._save_tokens(
        {"client_id": "c", "refresh_token": "r", "access_token": "old", "expires_at": 0}
    )
    monkeypatch.setattr(
        fsd,
        "_req",
        lambda *a, **k: (200, {}, {"access_token": "fresh", "expires_in": 3600}),
    )
    assert fsd._access_token() == "fresh"

    saved = fsd._load_tokens()
    assert saved["refresh_token"] == "r", "refresh_token must survive a refresh"
    assert saved["client_id"] == "c", "client_id must survive a refresh"
    assert saved["expires_at"] > time.time()


def test_access_token_refresh_failure_exits(monkeypatch):
    fsd._save_tokens({"client_id": "c", "refresh_token": "r", "expires_at": 0})
    monkeypatch.setattr(fsd, "_req", lambda *a, **k: (400, {}, {"error": "bad"}))
    with pytest.raises(SystemExit, match="refresh failed"):
        fsd._access_token()


# ── 5. Mcp protocol behaviour ────────────────────────────────────────────────
class _Recorder:
    """Captures outbound _req payloads and replays canned responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def __call__(self, url, *, data=None, headers=None, form=False, timeout=30):
        self.sent.append({"url": url, "data": data, "headers": headers or {}})
        return self.responses.pop(0) if self.responses else (200, {}, {"result": {}})


def test_call_returns_result_and_increments_request_id(monkeypatch):
    rec = _Recorder((200, {}, {"result": {"n": 1}}), (200, {}, {"result": {"n": 2}}))
    monkeypatch.setattr(fsd, "_req", rec)
    mcp = fsd.Mcp("tok")

    assert mcp.call("initialize") == {"n": 1}
    assert mcp.call("ping") == {"n": 2}
    assert [s["data"]["id"] for s in rec.sent] == [1, 2]


def test_call_omits_params_key_when_none(monkeypatch):
    """A null `params` is not the same as an absent one in JSON-RPC."""
    rec = _Recorder((200, {}, {"result": {}}))
    monkeypatch.setattr(fsd, "_req", rec)
    fsd.Mcp("tok").call("ping")
    assert "params" not in rec.sent[0]["data"]


def test_call_sends_bearer_token(monkeypatch):
    rec = _Recorder((200, {}, {"result": {}}))
    monkeypatch.setattr(fsd, "_req", rec)
    fsd.Mcp("secret-tok").call("ping")
    assert rec.sent[0]["headers"]["Authorization"] == "Bearer secret-tok"


def test_call_captures_then_replays_session_id(monkeypatch):
    rec = _Recorder(
        (200, {"Mcp-Session-Id": "sess-1"}, {"result": {}}),
        (200, {}, {"result": {}}),
    )
    monkeypatch.setattr(fsd, "_req", rec)
    mcp = fsd.Mcp("tok")

    mcp.call("initialize")
    assert mcp.session == "sess-1"
    mcp.call("ping")
    assert rec.sent[1]["headers"]["Mcp-Session-Id"] == "sess-1"


def test_call_401_exits_with_reauth_hint(monkeypatch):
    monkeypatch.setattr(fsd, "_req", lambda *a, **k: (401, {}, {}))
    with pytest.raises(SystemExit, match="re-run --auth"):
        fsd.Mcp("tok").call("ping")


def test_call_http_error_raises_runtimeerror(monkeypatch):
    monkeypatch.setattr(fsd, "_req", lambda *a, **k: (500, {}, {"oops": 1}))
    with pytest.raises(RuntimeError, match="HTTP 500"):
        fsd.Mcp("tok").call("ping")


def test_call_jsonrpc_error_raises_runtimeerror(monkeypatch):
    monkeypatch.setattr(fsd, "_req", lambda *a, **k: (200, {}, {"error": {"code": -1}}))
    with pytest.raises(RuntimeError, match="error"):
        fsd.Mcp("tok").call("ping")


def test_notify_initialized_sends_no_id(monkeypatch):
    """Notifications are id-less by JSON-RPC spec."""
    rec = _Recorder((202, {}, {}))
    monkeypatch.setattr(fsd, "_req", rec)
    fsd.Mcp("tok").notify_initialized()
    assert "id" not in rec.sent[0]["data"]
    assert rec.sent[0]["data"]["method"] == "notifications/initialized"


# ── 6. Mcp.tool payload extraction ───────────────────────────────────────────
def test_tool_prefers_structured_content(monkeypatch):
    monkeypatch.setattr(
        fsd,
        "_req",
        lambda *a, **k: (
            200,
            {},
            {
                "result": {
                    "structuredContent": {"from": "structured"},
                    "content": [{"type": "text", "text": '{"from": "text"}'}],
                }
            },
        ),
    )
    assert fsd.Mcp("tok").tool("t", {}) == {"from": "structured"}


def test_tool_falls_back_to_text_block_json(monkeypatch):
    monkeypatch.setattr(
        fsd,
        "_req",
        lambda *a, **k: (200, {}, {"result": {"content": [{"type": "text", "text": '{"ok": 1}'}]}}),
    )
    assert fsd.Mcp("tok").tool("t", {}) == {"ok": 1}


def test_tool_skips_non_json_text_blocks(monkeypatch):
    """A leading prose block must not abort extraction of a later JSON block."""
    monkeypatch.setattr(
        fsd,
        "_req",
        lambda *a, **k: (
            200,
            {},
            {
                "result": {
                    "content": [
                        {"type": "text", "text": "here you go:"},
                        {"type": "text", "text": '{"ok": 2}'},
                    ]
                }
            },
        ),
    )
    assert fsd.Mcp("tok").tool("t", {}) == {"ok": 2}


def test_tool_without_any_json_payload_raises(monkeypatch):
    monkeypatch.setattr(
        fsd,
        "_req",
        lambda *a, **k: (200, {}, {"result": {"content": [{"type": "image", "data": "..."}]}}),
    )
    with pytest.raises(RuntimeError, match="no JSON payload"):
        fsd.Mcp("tok").tool("t", {})


# ── 7. account-number resolution & hygiene ───────────────────────────────────
REPO_ROOT = MODULE_PATH.parent


def test_account_number_prefers_environment(monkeypatch):
    monkeypatch.setenv("GESH_RH_ACCOUNT", "ENV_WINS")
    assert fsd._account_number() == "ENV_WINS"


def test_account_number_falls_back_to_dotenv(monkeypatch, tmp_path):
    """
    Removing the hardcoded default was necessary but NOT sufficient: the var is
    set nowhere on the host, so the module exited on every run. Reading the
    gitignored .env is what actually makes the LLM-free tier work.
    """
    monkeypatch.delenv("GESH_RH_ACCOUNT", raising=False)
    (tmp_path / ".env").write_text('OTHER=1\nGESH_RH_ACCOUNT="FROM_DOTENV"\n')
    monkeypatch.setattr(fsd, "BASE_DIR", tmp_path)
    assert fsd._account_number() == "FROM_DOTENV"


def test_account_number_missing_everywhere_exits_with_actionable_message(monkeypatch, tmp_path):
    monkeypatch.delenv("GESH_RH_ACCOUNT", raising=False)
    monkeypatch.setattr(fsd, "BASE_DIR", tmp_path)  # no .env here
    with pytest.raises(SystemExit, match="GESH_RH_ACCOUNT not set"):
        fsd._account_number()


def test_account_number_is_never_hardcoded_in_tracked_code():
    """
    A real Robinhood account number shipped as a getenv()/shell fallback default
    in both snapshot fetchers. Neither may carry a literal account again — the
    value belongs in .env, which is gitignored.
    """
    for name in ("fetch_snapshot_direct.py", "fetch_snapshot_mcp.sh"):
        src = (REPO_ROOT / name).read_text()
        # No 9-digit literal sitting in a default-value position.
        assert not re.search(r"GESH_RH_ACCOUNT[:=][-]?\s*[\"']?\d{6,}", src), (
            f"{name} appears to hardcode an account number as a fallback default"
        )


def test_fetch_raw_dump_is_private_and_removed_after_builder(monkeypatch, tmp_path):
    """Brokerage data is readable only by this process and survives no build."""

    class FakeMcp:
        def __init__(self, token):
            pass

        def call(self, method, params):
            return {}

        def notify_initialized(self):
            pass

        def tool(self, name, arguments):
            if name == "get_portfolio":
                return {"data": {"cash": "123.45"}}
            if name == "get_equity_positions":
                return {"data": {"positions": [{"symbol": "FAKE"}]}}
            return {"data": {"results": [{"symbol": "FAKE"}]}}

    observed = {}

    def fake_run(command, *, cwd):
        raw_path = Path(command[2])
        observed["path"] = raw_path
        observed["mode"] = raw_path.stat().st_mode & 0o777
        observed["data"] = json.loads(raw_path.read_text())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(fsd, "_access_token", lambda: "fake-token")
    monkeypatch.setattr(fsd, "Mcp", FakeMcp)
    monkeypatch.setattr(fsd.subprocess, "run", fake_run)
    monkeypatch.setattr(fsd.tempfile, "tempdir", str(tmp_path))

    fsd.fetch()

    assert observed["mode"] == 0o600
    assert observed["data"]["fetched_account"] == "TEST_ACCOUNT_0000"
    assert not observed["path"].exists()
