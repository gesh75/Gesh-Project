"""
allocation_config.py — Buying Power Allocation & Scanner Settings

Mirrors the Trading Hub config panel:
  - Buying power allocation (stocks vs options)
  - Max $ per trade, max open positions
  - Scanner price/volume filters
  - Auto-trade gate (ON/OFF)
  - Extended hours trading flag
  - Scanner timeframe mode (daily / 1HR / 5MIN / premarket)

Stored in: gesh_config.json (project root)
All settings persist between sessions.

Usage:
    from allocation_config import load_config, save_config, validate_action

    cfg = load_config()
    ok, reason = validate_action("BUY", "NVDA", trade_usd=3500, open_positions=2)
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).parent / "gesh_config.json"

# ─── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict[str, Any] = {
    # Buying power allocation
    "total_buying_power":       None,      # set at runtime from Robinhood profile
    "stock_allocation_pct":     70.0,      # % of buying power for stocks
    "options_allocation_pct":   30.0,      # % of buying power for options

    # Per-trade hard caps
    "max_stock_per_trade":      5000.0,    # max $ on a single stock trade
    "max_options_per_trade":    2000.0,    # max $ on a single options trade
    "max_open_positions":       5,         # max simultaneous open positions
    "max_open_contracts":       3,         # max simultaneous open options contracts
    "min_cash_buffer":          500.0,     # never deploy below this cash level

    # Scanner filters
    "scanner_price_min":        0.10,      # min stock price for scanner
    "scanner_price_max":        10.00,     # max price ceiling — enforced ONLY in PRICE_CAPPED_MODES (penny universe)
    "scanner_volume_min":       200_000,   # min daily volume
    "scanner_timeframe":        "daily",   # daily | 1HR | 5MIN | premarket | forex-1HR | forex-4HR

    # Execution gates
    "auto_trade":               False,     # True = live execution enabled
    "extended_hours":           True,      # pre-market & after-hours
    "paper_mode":               False,     # True = all actions route to paper_trades

    # Portfolio screener universe
    "screener_universe":        "default", # default | aggressive | conservative | forex

    # Metadata
    "last_updated":             None,
    "version":                  "1.1",
}

# Scanner modes where the scanner_price_max ceiling is meaningful (penny-stock
# universe). Intraday/forex modes intentionally have NO upper price cap.
PRICE_CAPPED_MODES = {"stock"}

# ─── Load / Save ──────────────────────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    """Load config from disk, merging with defaults for any missing keys."""
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_PATH) as f:
        stored = json.load(f)

    # Forward-compatible: fill in any new keys from DEFAULT_CONFIG
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(stored)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config to disk with timestamp."""
    from datetime import datetime, timezone
    cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def update_config(**kwargs) -> dict[str, Any]:
    """
    Update specific config keys and save.

    Example:
        update_config(max_stock_per_trade=8000, auto_trade=False)
    """
    cfg = load_config()
    for key, value in kwargs.items():
        if key not in DEFAULT_CONFIG:
            raise ValueError(f"Unknown config key: '{key}'. Valid keys: {list(DEFAULT_CONFIG)}")
        cfg[key] = value
    save_config(cfg)
    return cfg


def reset_config() -> dict[str, Any]:
    """Reset to factory defaults."""
    save_config(DEFAULT_CONFIG.copy())
    return DEFAULT_CONFIG.copy()


# ─── Action Validation Gate ───────────────────────────────────────────────────

def validate_action(
    action: str,
    symbol: str,
    trade_usd: float,
    open_positions: int,
    current_cash: float | None = None,
    is_options: bool = False,
    current_price: float | None = None,
) -> tuple[bool, str]:
    """
    Gate check before executing or recommending a trade.
    Returns (allowed: bool, reason: str).

    Checks applied:
      1. Auto-trade gate (if auto_trade=False, always block live execution)
      2. Max $ per trade
      3. Max open positions
      4. Cash buffer floor
      5. Scanner price filter (if price provided)
    """
    cfg = load_config()

    # 1. Auto-trade gate
    if not cfg["auto_trade"] and action in ("BUY", "ADD", "TRIM", "SELL"):
        return False, "Auto-trade is OFF — review and execute manually"

    # 2. Max $ per trade
    if is_options:
        cap = cfg["max_options_per_trade"]
        cap_label = "options"
    else:
        cap = cfg["max_stock_per_trade"]
        cap_label = "stock"

    if trade_usd > cap:
        return False, (
            f"Trade size ${trade_usd:,.0f} exceeds max {cap_label} per trade "
            f"(${cap:,.0f}). Reduce position."
        )

    # 3. Max open positions
    if action in ("BUY", "ADD") and open_positions >= cfg["max_open_positions"]:
        return False, (
            f"Already at max open positions ({cfg['max_open_positions']}). "
            "Close a position before opening another."
        )

    # 4. Cash buffer
    if current_cash is not None and action in ("BUY", "ADD"):
        remaining = current_cash - trade_usd
        if remaining < cfg["min_cash_buffer"]:
            return False, (
                f"Trade would leave ${remaining:,.0f} cash — below "
                f"${cfg['min_cash_buffer']:,.0f} minimum buffer. Reduce size."
            )

    # 5. Price filter (scanner mode)
    if current_price is not None and cfg["scanner_timeframe"] != "daily":
        if current_price < cfg["scanner_price_min"]:
            return False, (
                f"{symbol} price ${current_price:.2f} below scanner minimum "
                f"${cfg['scanner_price_min']:.2f}"
            )
        # Upper price ceiling is intended only for the penny-stock universe.
        # Enforce it for price-capped modes (mode-aware, not a hardcoded $50
        # magic number); a non-positive max disables the ceiling entirely.
        price_max = cfg.get("scanner_price_max") or 0
        if (
            price_max > 0
            and cfg["scanner_timeframe"] in PRICE_CAPPED_MODES
            and current_price > price_max
        ):
            return False, (
                f"{symbol} price ${current_price:.2f} above scanner maximum "
                f"${price_max:.2f}"
            )

    return True, "OK"


def get_allocation_summary(buying_power: float | None = None) -> dict:
    """
    Return a human-readable allocation summary for the report header.
    If buying_power is passed, calculates dollar amounts.
    """
    cfg = load_config()
    bp = buying_power or cfg.get("total_buying_power") or 0

    stock_alloc   = bp * cfg["stock_allocation_pct"]   / 100
    options_alloc = bp * cfg["options_allocation_pct"] / 100
    remaining     = bp - stock_alloc - options_alloc

    return {
        "total_buying_power":    bp,
        "stock_allocation_usd":  round(stock_alloc, 2),
        "stock_allocation_pct":  cfg["stock_allocation_pct"],
        "options_allocation_usd": round(options_alloc, 2),
        "options_allocation_pct": cfg["options_allocation_pct"],
        "remaining_usd":         round(remaining, 2),
        "max_stock_per_trade":   cfg["max_stock_per_trade"],
        "max_options_per_trade": cfg["max_options_per_trade"],
        "max_open_positions":    cfg["max_open_positions"],
        "auto_trade":            cfg["auto_trade"],
        "extended_hours":        cfg["extended_hours"],
        "paper_mode":            cfg["paper_mode"],
        "scanner_timeframe":     cfg["scanner_timeframe"],
        "min_cash_buffer":       cfg["min_cash_buffer"],
    }


# ─── Scanner timeframe helpers ────────────────────────────────────────────────

SCANNER_MODES = {
    "daily":     {"period": "60d",  "interval": "1d",  "label": "Daily Scanner",     "desc": "Key levels + sweep setups"},
    "1HR":       {"period": "5d",   "interval": "1h",  "label": "1HR Scanner",       "desc": "Multi-timeframe setups"},
    "5MIN":      {"period": "1d",   "interval": "5m",  "label": "5MIN Scanner",      "desc": "Intraday momentum setups"},
    "premarket": {"period": "1d",   "interval": "5m",  "label": "Premarket Scanner", "desc": "Unusual volume + big moves"},
    "stock":     {"period": "30d",  "interval": "1d",  "label": "Stock Scanner",     "desc": "Penny stocks $0.50–$10"},
    "forex-1HR": {"period": "5d",   "interval": "1h",  "label": "Forex 1HR Scan",    "desc": "Majors, minors + metals"},
    "forex-4HR": {"period": "10d",  "interval": "4h",  "label": "Forex 4HR Scan",    "desc": "Higher timeframe setups"},
    "forex-daily":{"period": "60d", "interval": "1d",  "label": "Forex Daily Scan",  "desc": "Daily trend setups"},
}


def get_scanner_params() -> dict:
    """Return the yfinance period/interval params for the active scanner timeframe."""
    cfg = load_config()
    mode = cfg.get("scanner_timeframe", "daily")
    return SCANNER_MODES.get(mode, SCANNER_MODES["daily"])


if __name__ == "__main__":
    cfg = load_config()
    print("Current config:")
    for k, v in cfg.items():
        print(f"  {k}: {v}")

    ok, reason = validate_action("BUY", "NVDA", trade_usd=3500, open_positions=2, current_cash=6000)
    print(f"\nValidation: {ok} — {reason}")
