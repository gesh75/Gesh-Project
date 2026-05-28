"""
trading/config.py — risk + mode configuration.

All values overridable via environment variables. Defaults are
ULTRA-CONSERVATIVE on purpose — paper mode, low caps, high confidence floor.
Flip PAPER_MODE=false ONLY after weeks of clean paper runs.
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class RiskConfig:
    # --- mode ---
    paper_mode: bool = True
    require_approval: bool = True       # every intent waits for explicit approval before live
    kill_switch_path: str = str(Path.home() / ".gesh" / "KILL")

    # --- caps ---
    max_position_pct: float = 0.02      # 2% of equity per symbol
    max_daily_loss_pct: float = 0.01    # 1% of equity per day
    max_orders_per_day: int = 3
    min_cash_buffer_usd: float = 500.0
    min_confidence: float = 0.75

    # --- data freshness / hours ---
    max_price_age_seconds: int = 300    # reject if last quote > 5 min old
    market_open_hhmm: str = "09:30"
    market_close_hhmm: str = "16:00"
    market_timezone: str = "America/New_York"


def load_risk_config() -> RiskConfig:
    """Build RiskConfig from environment, falling back to ultra-conservative defaults."""
    return RiskConfig(
        paper_mode=_env_bool("PAPER_MODE", True),
        require_approval=_env_bool("REQUIRE_APPROVAL", True),
        kill_switch_path=os.getenv("KILL_SWITCH_PATH", str(Path.home() / ".gesh" / "KILL")),
        max_position_pct=_env_float("MAX_POSITION_PCT", 0.02),
        max_daily_loss_pct=_env_float("MAX_DAILY_LOSS_PCT", 0.01),
        max_orders_per_day=_env_int("MAX_ORDERS_PER_DAY", 3),
        min_cash_buffer_usd=_env_float("MIN_CASH_BUFFER_USD", 500.0),
        min_confidence=_env_float("MIN_CONFIDENCE", 0.75),
        max_price_age_seconds=_env_int("MAX_PRICE_AGE_SECONDS", 300),
        market_open_hhmm=os.getenv("MARKET_OPEN_HHMM", "09:30"),
        market_close_hhmm=os.getenv("MARKET_CLOSE_HHMM", "16:00"),
        market_timezone=os.getenv("MARKET_TIMEZONE", "America/New_York"),
    )
