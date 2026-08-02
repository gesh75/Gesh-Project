#!/usr/bin/env python3
"""
fetch_snapshot_direct.py — LLM-free snapshot fetch for the GESH daily chain.

Replaces the `claude -p` MCP courier (fetch_snapshot_mcp.sh) with a direct,
deterministic MCP client: OAuth (PKCE + dynamic client registration) against
Robinhood's agent MCP, then three tool calls (get_portfolio,
get_equity_positions, get_equity_quotes). Writes /tmp/gesh_mcp_raw.json in the
EXACT schema build_snapshot_from_mcp.py consumes, then runs that builder.

Why: the claude-CLI path dies with the Claude subscription (OAuth lives in the
CLI's Keychain entry). This client owns its own tokens (.rh_mcp_oauth.json,
chmod 600, gitignored) and refreshes them itself — no LLM, no subscription.

One-time setup (interactive, browser login to Robinhood):
    python3 fetch_snapshot_direct.py --auth
Daily use (exit 0 = live_portfolio_snapshot.json written; non-zero = caller
falls back to fetch_snapshot_mcp.sh / legacy pickle):
    python3 fetch_snapshot_direct.py
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).parent
RAW = Path("/tmp/gesh_mcp_raw.json")
TOKEN_FILE = BASE_DIR / ".rh_mcp_oauth.json"
BUILDER = BASE_DIR / "build_snapshot_from_mcp.py"

MCP_URL = "https://agent.robinhood.com/mcp/trading"
REGISTER_URL = "https://agent.robinhood.com/oauth/trading/register"
AUTH_URL = "https://robinhood.com/oauth"
TOKEN_URL = "https://api.robinhood.com/oauth2/token/"
SCOPE = "internal"

ACCT = __import__("os").getenv("GESH_RH_ACCOUNT", "557370533")
PROTOCOL_VERSION = "2025-03-26"
UA = {"User-Agent": "gesh-snapshot-direct/1.0"}


# ── HTTP helpers ─────────────────────────────────────────────────────────────
def _req(url: str, *, data: dict | None = None, headers: dict | None = None,
         form: bool = False, timeout: int = 30) -> tuple[int, dict, dict]:
    """Minimal urllib POST/GET. Returns (status, headers, parsed_json_or_{})."""
    hdrs = dict(UA)
    hdrs.update(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, dict(resp.headers), _parse_body(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, dict(e.headers or {}), _parse_body(raw)


def _parse_body(raw: str) -> dict:
    """JSON body, or SSE stream (take the last data: event)."""
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("data:") or "\ndata:" in raw:
        last = ""
        for line in raw.splitlines():
            if line.startswith("data:"):
                last = line[5:].strip()
        raw = last or raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_unparsed": raw[:500]}


# ── OAuth: register, authorize (one-time), refresh ──────────────────────────
def _save_tokens(d: dict) -> None:
    d = dict(d)
    if "expires_in" in d:
        d["expires_at"] = time.time() + int(d["expires_in"]) - 300
    TOKEN_FILE.write_text(json.dumps(d, indent=2))
    TOKEN_FILE.chmod(0o600)


def _load_tokens() -> dict:
    return json.loads(TOKEN_FILE.read_text()) if TOKEN_FILE.exists() else {}


def _register_client(redirect_uri: str) -> dict:
    status, _, body = _req(REGISTER_URL, data={
        "client_name": "gesh-snapshot-direct",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    })
    if status not in (200, 201) or not body.get("client_id"):
        raise SystemExit(f"[direct] dynamic client registration failed: HTTP {status} {body}")
    return body


def _auth_flow() -> None:
    """One-time interactive flow: register (fresh), browser login, callback."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    client_id = _register_client(redirect_uri)["client_id"]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = (f"{AUTH_URL}?response_type=code&client_id={client_id}"
           f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
           f"&code_challenge={challenge}&code_challenge_method=S256"
           f"&scope={SCOPE}&state={state}")

    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and q.get("code"):
                got["code"] = q["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"GESH snapshot auth received - you can close this tab.")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Bad callback (state mismatch / no code).")

        def log_message(self, *a):  # quiet
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    srv.timeout = 300
    print(f"[direct] opening browser for Robinhood login…\n  {url}\n"
          f"  (waiting up to 5 min on {redirect_uri})")
    webbrowser.open(url)
    while "code" not in got:
        srv.handle_request()
        if time.time() % 300 < 1:  # crude guard; handle_request has its own timeout
            break
    if "code" not in got:
        raise SystemExit("[direct] no auth code received within 5 minutes — aborting")

    status, _, body = _req(TOKEN_URL, form=True, data={
        "grant_type": "authorization_code",
        "code": got["code"],
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    })
    if status != 200 or not body.get("access_token"):
        raise SystemExit(f"[direct] code exchange failed: HTTP {status} {body}")
    body["client_id"] = client_id
    _save_tokens(body)
    print(f"[direct] tokens saved to {TOKEN_FILE} (chmod 600)")


def _access_token() -> str:
    t = _load_tokens()
    if not t.get("client_id"):
        raise SystemExit("[direct] not authenticated — run: python3 fetch_snapshot_direct.py --auth")
    if t.get("access_token") and time.time() < t.get("expires_at", 0):
        return t["access_token"]
    if not t.get("refresh_token"):
        raise SystemExit("[direct] token expired and no refresh_token — re-run --auth")
    status, _, body = _req(TOKEN_URL, form=True, data={
        "grant_type": "refresh_token",
        "refresh_token": t["refresh_token"],
        "client_id": t["client_id"],
    })
    if status != 200 or not body.get("access_token"):
        raise SystemExit(f"[direct] refresh failed (HTTP {status}) — re-run --auth")
    body.setdefault("refresh_token", t["refresh_token"])
    body["client_id"] = t["client_id"]
    _save_tokens(body)
    return body["access_token"]


# ── MCP client ───────────────────────────────────────────────────────────────
class Mcp:
    def __init__(self, token: str):
        self.token = token
        self.session = None
        self._id = 0

def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        hdrs = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        if self.session:
            hdrs["Mcp-Session-Id"] = self.session
            MCP_URL, headers=hdrs,
            data={"jsonrpc": "2.0", "id": self._id, "method": method,
                  **({"params": params} if params is not None else {})})
        if status == 401:
            raise SystemExit("[direct] 401 from MCP (token rejected) — re-run --auth")
        if status >= 400:
            raise RuntimeError(f"[direct] MCP {method}: HTTP {status} {body}")
        if not self.session and resp_headers.get("Mcp-Session-Id"):
            self.session = resp_headers["Mcp-Session-Id"]
        if "error" in body:
            raise RuntimeError(f"[direct] MCP {method} error: {body['error']}")
        return body.get("result", body)

    def tool(self, name: str, arguments: dict) -> dict:
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        if result.get("structuredContent"):
            return result["structuredContent"]
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    continue
        raise RuntimeError(f"[direct] tool {name}: no JSON payload in {json.dumps(result)[:300]}")

    def notify_initialized(self) -> None:
        hdrs = {"Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}"}
        if self.session:
            hdrs["Mcp-Session-Id"] = self.session
        _req(MCP_URL, headers=hdrs,
             data={"jsonrpc": "2.0", "method": "notifications/initialized"})


def fetch() -> None:
    token = _access_token()
    mcp = Mcp(token)
    mcp.call("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "gesh-snapshot-direct", "version": "1.0"},
    })
    mcp.notify_initialized()

    portfolio = mcp.tool("get_portfolio", {"account_number": ACCT})
    positions = mcp.tool("get_equity_positions", {"account_number": ACCT})
    symbols = [p.get("symbol") for p in positions.get("data", {}).get("positions", [])
               if isinstance(p, dict) and p.get("symbol")]
    quotes = mcp.tool("get_equity_quotes", {"symbols": symbols}) if symbols else {"data": {"results": []}}

    raw = {
        "fetched_account": ACCT,
        "portfolio": portfolio.get("data", portfolio),
        "positions": positions.get("data", {}).get("positions", []),
        "quotes": quotes.get("data", {}).get("results", []),
    }
    RAW.write_text(json.dumps(raw))
    print(f"[direct] raw dump: {len(raw['positions'])} positions, "
          f"{len(raw['quotes'])} quotes -> {RAW}")

    r = subprocess.run([sys.executable, str(BUILDER)], cwd=BASE_DIR)
    if r.returncode != 0:
        raise SystemExit("[direct] build_snapshot_from_mcp.py failed")
    print("[direct] snapshot built OK")


if __name__ == "__main__":
    if "--auth" in sys.argv:
        _auth_flow()
    else:
        fetch()
