#!/usr/bin/env python3
"""One-command live smoke of the customer surface (PRs #18-#24) against a RUNNING
:9000 server. Run on the host after setting the env (Alpaca keys, auth wall):

    ./venv/bin/python3 smoke_customer_surface.py                       # wall OFF (dev/solo)
    ./venv/bin/python3 smoke_customer_surface.py --email you@x --password '...'   # wall ON
    ./venv/bin/python3 smoke_customer_surface.py --with-llm            # ALSO spend 2 Claude calls

Prints one PASS/FAIL/SKIP line per step with the reason; exits non-zero if any
step FAILs (SKIPs are fine — they mean a key/wall isn't configured yet, and the
line says which). Read-only except a temporary watchlist entry + book position,
both restored to their prior state on exit.
"""

from __future__ import annotations

import argparse
import sys
import time

import requests

SMOKE_SYM = "AAPL"  # liquid STOCK — ETFs (SPY) have no analyst ratings/PE, which would false-FAIL those steps
RESULTS: list[tuple[str, str, str]] = []


def record(status: str, step: str, why: str = "") -> None:
    RESULTS.append((status, step, why))
    pad = {"PASS": "\033[32mPASS\033[0m", "FAIL": "\033[31mFAIL\033[0m",
           "SKIP": "\033[33mSKIP\033[0m"}[status]
    print(f"  [{pad}] {step}" + (f" — {why}" if why else ""))


def check(step: str, ok: bool, why_fail: str = "", why_pass: str = "") -> bool:
    record("PASS" if ok else "FAIL", step, why_pass if ok else why_fail)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:9000")
    ap.add_argument("--email", default=None, help="login email (wall ON)")
    ap.add_argument("--password", default=None, help="login password (wall ON)")
    ap.add_argument("--with-llm", action="store_true",
                    help="also exercise the two Claude-spend endpoints (real API cost)")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    s = requests.Session()
    print(f"Smoking customer surface at {base}\n")

    # ── 0. server up + wall state ────────────────────────────────────────────
    try:
        hz = s.get(f"{base}/healthz", timeout=10).json()
    except Exception as e:
        record("FAIL", "healthz", f"server unreachable: {e}")
        return _summary()
    wall_on = bool(hz.get("auth"))
    check("healthz", hz.get("status") == "ok", why_pass=f"auth wall {'ON' if wall_on else 'OFF'}")

    # ── 1. session ───────────────────────────────────────────────────────────
    if wall_on:
        if not (args.email and args.password):
            record("FAIL", "login", "wall is ON — pass --email/--password")
            return _summary()
        r = s.post(f"{base}/auth/login", json={"email": args.email, "password": args.password}, timeout=10)
        if not check("login", r.status_code == 200 and r.json().get("ok"),
                     f"HTTP {r.status_code}: {r.text[:120]}"):
            return _summary()
    role = None
    r = s.get(f"{base}/api/me", timeout=10)
    why = f"HTTP {r.status_code}"
    if wall_on and r.status_code in (302, 401, 403) and base.startswith("http://"):
        # The session cookie is set with Secure=True by default (production is
        # HTTPS) and clients refuse to send Secure cookies over plain http.
        why += (" — login succeeded but the Secure session cookie can't transit "
                "plain HTTP: set GESH_COOKIE_SECURE=0 for local http smoke, or "
                "use the https URL")
    if check("/api/me", r.status_code == 200, why):
        role = r.json().get("role")
        record("PASS", "session role", role or "?")

    # ── 2. per-ticker mechanical cards (no LLM) ──────────────────────────────
    for path, expect_key in [(f"/api/regime/{SMOKE_SYM}", "regime"),
                             (f"/api/fundamentals/{SMOKE_SYM}", None),
                             (f"/api/analyst/{SMOKE_SYM}", None),
                             (f"/api/news/{SMOKE_SYM}", None)]:
        r = s.get(f"{base}{path}", timeout=60)
        ok = r.status_code == 200 and r.json().get("status") == "ok"
        detail = "" if ok else f"HTTP {r.status_code}: {r.text[:120]}"
        if ok and expect_key:
            ok = expect_key in (r.json().get("data") or {})
            detail = "" if ok else f"missing '{expect_key}' in data"
        check(path, ok, detail)

    # ── 3. charts: daily always; intraday tells you if Alpaca is wired ──────
    r = s.get(f"{base}/api/chart/{SMOKE_SYM}?period=1mo", timeout=60)
    check("chart 1mo", r.status_code == 200 and len(r.json().get("prices") or []) > 2,
          f"HTTP {r.status_code} / {len((r.json() if r.status_code == 200 else {}).get('prices') or [])} points")
    r = s.get(f"{base}/api/chart/{SMOKE_SYM}?period=1d", timeout=60)
    if r.status_code == 200 and r.json().get("intraday_unavailable"):
        record("SKIP", "chart 1d (intraday)", "honest empty state — set ALPACA_API_KEY/SECRET to light up 1D/5D")
    else:
        check("chart 1d (intraday via Alpaca)",
              r.status_code == 200 and len(r.json().get("prices") or []) > 2,
              f"HTTP {r.status_code}")

    # ── 4. watchlist roundtrip (restores prior state) ────────────────────────
    before = set((s.get(f"{base}/api/watchlist", timeout=10).json().get("data")) or [])
    r = s.post(f"{base}/api/watchlist", json={"symbol": SMOKE_SYM}, timeout=10)
    check("watchlist add", r.status_code == 200 and SMOKE_SYM in (r.json().get("data") or []),
          f"HTTP {r.status_code}")
    if SMOKE_SYM not in before:
        s.delete(f"{base}/api/watchlist/{SMOKE_SYM}", timeout=10)

    # ── 5. book roundtrip (restores prior state) ─────────────────────────────
    book0 = (s.get(f"{base}/api/mybook", timeout=60).json().get("data") or {})
    prior = next((p for p in book0.get("positions", []) if p["symbol"] == SMOKE_SYM), None)
    r = s.post(f"{base}/api/mybook", json={"symbol": SMOKE_SYM, "shares": 1}, timeout=60)
    ok = r.status_code == 200
    if ok:
        pos = next((p for p in (r.json().get("data") or {}).get("positions", [])
                    if p["symbol"] == SMOKE_SYM), None)
        ok = pos is not None and pos.get("equity", 0) > 0
    check("book add + live valuation", ok, f"HTTP {r.status_code}: {r.text[:120]}")

    # ── 6. LLM surfaces (opt-in — real Claude spend) ─────────────────────────
    if args.with_llm:
        r = s.post(f"{base}/api/analyze-ticker", json={"symbol": SMOKE_SYM}, timeout=180)
        if r.status_code in (401, 503):
            record("SKIP", "AI ticker card", f"HTTP {r.status_code} — fail-closed gate "
                   "(wall OFF needs GESH_API_KEY; anonymous needs login)")
        else:
            check("AI ticker card", r.status_code == 200 and "scores" in (r.json().get("data") or {}),
                  f"HTTP {r.status_code}: {r.text[:120]}")
        # Book analysis is ASYNC: the first call returns 202 {status: running}
        # and the client polls the SAME endpoint until 200 (the run outlasts the
        # tunnel's ~100s limit; a warm per-book cache can return 200 at once).
        r = s.post(f"{base}/api/mybook/analyze", json={}, timeout=300)
        deadline = time.monotonic() + 300
        while r.status_code == 202 and time.monotonic() < deadline:
            time.sleep(3)
            r = s.post(f"{base}/api/mybook/analyze", json={}, timeout=300)
        if r.status_code in (401, 503):
            record("SKIP", "book analysis", f"HTTP {r.status_code} — fail-closed gate")
        elif r.status_code == 202:
            check("book analysis (deep AI + Kelly + MC)", False,
                  "still 202 (running) after 300s poll budget")
        else:
            ok = r.status_code == 200 and "analysis" in (r.json().get("data") or {})
            check("book analysis (deep AI + Kelly + MC)", ok, f"HTTP {r.status_code}: {r.text[:150]}")
    else:
        record("SKIP", "AI ticker card + book analysis", "pass --with-llm to exercise (real API cost)")

    # ── 7. report + usage ────────────────────────────────────────────────────
    r = s.get(f"{base}/mybook/report", timeout=60)
    check("/mybook/report", r.status_code == 200 and "not financial advice" in r.text.lower(),
          f"HTTP {r.status_code}")
    r = s.get(f"{base}/usage", timeout=10)
    if role == "customer":
        check("/usage blocked for customer", r.status_code == 403, f"HTTP {r.status_code} (expected 403)")
    else:
        check("/usage owner page", r.status_code == 200 and "Customer Usage" in r.text,
              f"HTTP {r.status_code}")

    # ── restore the book to its prior state ──────────────────────────────────
    if prior is None:
        s.delete(f"{base}/api/mybook/{SMOKE_SYM}", timeout=10)
    else:
        s.post(f"{base}/api/mybook",
               json={"symbol": SMOKE_SYM, "shares": prior["quantity"],
                     "avg_cost": prior.get("average_buy_price") or None}, timeout=60)

    return _summary()


def _summary() -> int:
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    skips = [r for r in RESULTS if r[0] == "SKIP"]
    print(f"\n{len(RESULTS) - len(fails) - len(skips)} passed · {len(skips)} skipped · {len(fails)} FAILED")
    for _, step, why in fails:
        print(f"  ✗ {step}: {why}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
