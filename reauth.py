#!/usr/bin/env python3
"""
reauth.py — Interactive Robinhood re-authentication.

Run this in Terminal whenever the pickle session expires:
    cd ~/Projects/gesh-project && source venv/bin/activate && python3 reauth.py

What it does:
  1. Logs into Robinhood interactively (handles MFA / device approval)
  2. Stores a fresh session pickle in ~/.tokens/
  3. Immediately writes gesh_live_cache.json with your live portfolio
  4. Prints a summary so you can confirm it worked

After running this, the background com.gesh.live-data launchd service
will pick up the fresh pickle and resume normal operation automatically.
"""

from __future__ import annotations
import os, sys, json
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_DIR))
os.chdir(PROJECT_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
except ImportError:
    pass

print("=" * 60)
print("  GESH Robinhood Re-Authentication")
print("=" * 60)
print()

import robin_stocks.robinhood as rh

username = os.getenv("ROBINHOOD_USER")
password = os.getenv("ROBINHOOD_PASS")

if not username or not password:
    print("❌  ROBINHOOD_USER and ROBINHOOD_PASS not found in .env")
    sys.exit(1)

print("Logging in to Robinhood ...")
print("→ If Robinhood sends a device approval notification, approve it now.")
print()

try:
    result = rh.login(
        username=username,
        password=password,
        expiresIn=86400 * 7,     # 7-day session
        store_session=True,
        pickle_name="robinhood_session",
    )
    if not result:
        print("❌  Login failed — check credentials in .env and try again.")
        sys.exit(1)

    print("✅  Login successful! Session pickle saved.")
    print()

except Exception as e:
    print(f"❌  Login error: {type(e).__name__} (details suppressed)")
    sys.exit(1)

# Already logged in — build portfolio payload directly (no second login needed)
print("Fetching live portfolio and writing cache ...")
try:
    sys.path.insert(0, str(PROJECT_DIR))
    from data_fetcher import build_data_payload
    from fetch_live_data import write_cache

    payload = build_data_payload()

    # Enrich with day change
    eq   = payload["equity_summary"]
    prev = eq.get("adjusted_equity_previous_close", 0) or 0
    curr = eq.get("portfolio_equity", 0) or 0
    eq["day_change"]     = round(curr - prev, 2)
    eq["day_change_pct"] = round(((curr - prev) / prev * 100) if prev else 0.0, 2)

    # Normalise holding keys
    for h in payload.get("holdings", []):
        h.setdefault("price",             h.get("current_price", 0))
        h.setdefault("shares",            h.get("quantity", 0))
        h.setdefault("average_buy_price", h.get("average_buy_price", 0))

    write_cache(payload)

    print()
    print(f"✅  Cache written successfully!")
    print(f"    Equity:        ${eq.get('portfolio_equity', 0):>10,.2f}")
    print(f"    Buying power:  ${eq.get('buying_power', 0):>10,.2f}")
    print(f"    Day change:    ${eq.get('day_change', 0):>+10,.2f}  ({eq.get('day_change_pct', 0):+.2f}%)")
    print(f"    Holdings:      {[h['symbol'] for h in payload.get('holdings', [])]}")
    print()
    print("The background live-data service will now refresh every 10 min automatically.")
except Exception as e:
    print(f"⚠️  Cache write failed: {type(e).__name__}")
    print("    Login succeeded but cache refresh failed — the launchd service will retry on next cycle.")

print()
print("Done ✅")
