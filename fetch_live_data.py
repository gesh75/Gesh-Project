#!/usr/bin/env python3
"""
fetch_live_data.py — Standalone Robinhood live portfolio cache writer.

Runs on macOS where ~/.tokens/robinhoodXXX.pickle exists.
Writes gesh_live_cache.json to the gesh-project folder.
Called by:
  • com.gesh.live-data launchd plist  (every 10 min)
  • server.py lifespan startup        (on GESH server boot)
  • server.py /api/portfolio          (rate-limited, every 5 min)
  • daily-portfolio-analysis skill    (Step 1 live-data path)
"""

from __future__ import annotations
import json, os, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

# ── Locate project root (script lives in gesh-project/) ──────────────────────
PROJECT_DIR = Path(__file__).parent.resolve()
CACHE_FILE  = PROJECT_DIR / "gesh_live_cache.json"
CACHE_MAX_AGE_SEC = 1800   # 30 min — beyond this the skill falls back to DB

sys.path.insert(0, str(PROJECT_DIR))
os.chdir(PROJECT_DIR)

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
except ImportError:
    pass  # dotenv optional — env vars may already be set


def write_cache(payload: dict) -> None:
    """Add metadata and write cache file atomically."""
    payload["_cached_at"]     = datetime.now(timezone.utc).isoformat()
    payload["_cache_version"] = 2
    tmp = CACHE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    tmp.replace(CACHE_FILE)   # atomic rename — no partial reads
    print(f"[live_data] ✅ Cache written → {CACHE_FILE}  ({len(payload.get('holdings', []))} holdings)")


def fetch_and_cache(logout_after: bool = True) -> dict:
    """
    Restore the stored Robinhood session (~/.tokens/ pickle), call
    build_data_payload(), write cache, return payload.

    logout_after=False  — keep the session alive after caching (use when
                          called from server.py lifespan so the server's
                          own Robinhood session is not destroyed).

    NOTE: This runs every 10 min from the com.gesh.live-data launchd service,
    so it uses reuse-only login — it NEVER POSTs credentials. A credential
    login on a dead session, repeated every 10 min, would hammer Robinhood's
    login endpoint and rate-limit the account. If the stored session is
    expired this function raises cleanly (leaving the previous cache intact);
    run  `python3 reauth.py`  in Terminal to refresh the session.
    """
    from auth import login, logout
    from data_fetcher import build_data_payload
    import robin_stocks.robinhood as rh

    print("[live_data] Logging into Robinhood …")

    # Check if pickle exists before attempting login — gives a clearer error
    # robin_stocks saves as: ~/.tokens/robinhood{pickle_name}.pickle
    # With pickle_name="robinhood_session" → ~/.tokens/robinhoodrobinhood_session.pickle
    import glob
    pickle_paths = glob.glob(os.path.expanduser("~/.tokens/robinhood*.pickle"))
    if not pickle_paths:
        msg = ("No session pickle found at ~/.tokens/robinhood*.pickle. "
               "Run  python3 reauth.py  in Terminal to create one.")
        print(f"[live_data] ❌ {msg}")
        raise FileNotFoundError(msg)

    try:
        # Reuse-only: never POSTs credentials. This service runs every 10 min;
        # a credential login on a dead session would hammer Robinhood's login
        # endpoint and rate-limit the account. Only reauth.py (interactive) is
        # allowed to perform a credential login.
        login(allow_credentials=False)
        # Sanity-check: try a lightweight API call to confirm the session is live.
        # rh.helper.get_login_state() was removed in newer robin_stocks versions.
        # Instead, fetch account profile — if it returns data we are authenticated.
        test_profile = rh.profiles.load_account_profile(info=None)
        if not test_profile or not isinstance(test_profile, dict):
            raise RuntimeError("Login succeeded but account profile returned empty — pickle may be expired.")
        print(f"[live_data] ✅ Authenticated — account type: {test_profile.get('account_type', '?')}")
    except Exception as auth_err:
        err_str = str(auth_err)
        if any(k in err_str for k in ("token_type", "logged in", "pickle", "expired", "Login failed", "get_login_state")):
            print(f"[live_data] ❌ Auth error (pickle expired?): {auth_err}")
            print("[live_data]    → Run  python3 reauth.py  in Terminal to refresh your session.")
        else:
            print(f"[live_data] ❌ Unexpected auth error: {auth_err}")
        raise

    print("[live_data] Fetching live portfolio …")
    payload = build_data_payload()

    # Enrich equity_summary
    eq   = payload["equity_summary"]
    prev = eq.get("adjusted_equity_previous_close", 0) or 0
    curr = eq.get("portfolio_equity", 0) or 0
    eq["day_change"]     = round(curr - prev, 2)
    eq["day_change_pct"] = round(((curr - prev) / prev * 100) if prev else 0.0, 2)

    # Normalise holding keys so analysis engines find what they expect
    for h in payload.get("holdings", []):
        h.setdefault("price",            h.get("current_price", 0))
        h.setdefault("shares",           h.get("quantity", 0))
        h.setdefault("average_buy_price",h.get("average_buy_price", 0))

    write_cache(payload)

    if logout_after:
        try:
            logout()
        except Exception:
            pass

    return payload


def load_cache(max_age_sec: int = CACHE_MAX_AGE_SEC) -> dict | None:
    """
    Load cache file if it exists and is fresh.
    Returns None if missing or stale (triggers DB fallback in skill).
    """
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data["_cached_at"])
        age_sec   = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age_sec > max_age_sec:
            print(f"[live_data] Cache is {age_sec/60:.0f} min old — stale, will fall back to DB")
            return None
        print(f"[live_data] ✅ Cache hit — {age_sec/60:.1f} min old  ({len(data.get('holdings',[]))} holdings)")
        return data
    except Exception as e:
        print(f"[live_data] Cache read error: {e}")
        return None


def write_snapshot_from_screenshot(
    holdings: list[dict],
    total_value: float,
    buying_power: float,
    day_change: float,
    day_chg_pct: float,
    source: str = "screenshot",
) -> dict:
    """
    Write a live cache from manually observed data (Robinhood screenshot / UI).

    Each holding dict must contain at minimum:
        symbol, quantity, price   (equity is computed automatically)

    This is the fallback used by the daily-portfolio-analysis skill when
    robin_stocks auth is unavailable (expired MFA pickle or sandbox context).

    Example usage:
        from fetch_live_data import write_snapshot_from_screenshot
        write_snapshot_from_screenshot(
            holdings=[
                {"symbol": "NVDA", "quantity": 24, "price": 235.58},
                {"symbol": "IONQ", "quantity": 97.06, "price": 56.84},
                ...
            ],
            total_value=28663.82,
            buying_power=300.72,
            day_change=643.08,
            day_chg_pct=2.30,
            source="screenshot-2026-05-14",
        )
    """
    enriched: list[dict] = []
    for h in holdings:
        price = float(h.get("price", h.get("current_price", 0)))
        qty   = float(h.get("quantity", h.get("shares", 0)))
        eq    = round(price * qty, 2)
        enriched.append({
            "symbol":            h["symbol"],
            "quantity":          qty,
            "price":             price,
            "current_price":     price,
            "equity":            eq,
            "shares":            qty,
            "average_buy_price": float(h.get("average_buy_price", 0)),
            "day_change_pct":    float(h.get("day_chg_pct", h.get("day_change_pct", 0))),
        })

    payload = {
        "holdings": enriched,
        "equity_summary": {
            "portfolio_equity":              round(sum(h["equity"] for h in enriched), 2),
            "portfolio_value":               total_value,
            "buying_power":                  buying_power,
            "withdrawable_amount":           buying_power,
            "day_change":                    day_change,
            "day_change_pct":                day_chg_pct,
            "adjusted_equity_previous_close": round(total_value - day_change, 2),
        },
        "_source": source,
    }
    write_cache(payload)
    return payload


if __name__ == "__main__":
    # ── CLI: python3 fetch_live_data.py --snapshot  → inject last screenshot ──
    if "--snapshot" in sys.argv:
        # Hardcoded current snapshot (update from Robinhood UI when stale)
        _holdings = [
            {"symbol": "MSTR", "quantity": 16.00,  "price": 187.60,  "day_chg_pct": 5.38},
            {"symbol": "NVDA", "quantity": 24.00,  "price": 235.58,  "day_chg_pct": 4.32},
            {"symbol": "QQQ",  "quantity": 3.08,   "price": 721.53,  "day_chg_pct": 0.95},
            {"symbol": "MU",   "quantity": 2.50,   "price": 787.53,  "day_chg_pct": -2.00},
            {"symbol": "AMD",  "quantity": 6.00,   "price": 451.35,  "day_chg_pct": 1.31},
            {"symbol": "IONQ", "quantity": 97.06,  "price": 56.84,   "day_chg_pct": 2.86},
            {"symbol": "EEM",  "quantity": 74.99,  "price": 67.41,   "day_chg_pct": 0.30},
            {"symbol": "APP",  "quantity": 3.00,   "price": 472.75,  "day_chg_pct": 4.24},
            {"symbol": "MSFT", "quantity": 2.00,   "price": 409.22,  "day_chg_pct": 0.99},
        ]
        write_snapshot_from_screenshot(
            holdings=_holdings,
            total_value=28663.82,
            buying_power=300.72,
            day_change=643.08,
            day_chg_pct=2.30,
            source="screenshot-2026-05-14",
        )
        sys.exit(0)

    try:
        payload = fetch_and_cache()
        print(f"[live_data] Portfolio equity: ${payload['equity_summary'].get('portfolio_equity', 0):,.2f}")
        print(f"[live_data] Holdings: {[h['symbol'] for h in payload.get('holdings', [])]}")
        print(f"[live_data] Buying power: ${payload['equity_summary'].get('buying_power', 0):,.2f}")
        print(f"[live_data] Day change: ${payload['equity_summary'].get('day_change', 0):+,.2f} ({payload['equity_summary'].get('day_change_pct', 0):+.2f}%)")
    except Exception as e:
        print(f"[live_data] ❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)
