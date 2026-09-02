#!/usr/bin/env python3
"""
reauth.py — refresh owner portfolio data via Robinhood MCP.

The robin_stocks pickle login is retired. Robinhood rejects ROBINHOOD_USER/PASS
from .env ('Unable to log in with provided credentials') and a 0-byte pickle
raises KeyError token_type. The dashboard and the daily chain use the OAuth MCP
client instead.

    ./venv/bin/python3 reauth.py                  # same as fetch_snapshot_direct.py
    ./venv/bin/python3 fetch_snapshot_direct.py --auth   # one-time MCP OAuth
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
DIRECT = PROJECT_DIR / "fetch_snapshot_direct.py"

print("=" * 60)
print("  GESH Robinhood data refresh (MCP, not pickle)")
print("=" * 60)
print()
print("Pickle login is retired. Refreshing live_portfolio_snapshot.json")
print("via fetch_snapshot_direct.py (Robinhood agent MCP OAuth).")
print()
if not DIRECT.is_file():
    print(f"❌  missing {DIRECT}")
    sys.exit(1)

raise SystemExit(subprocess.call([sys.executable, str(DIRECT), *sys.argv[1:]]))
