"""
trading/ — autonomous execution layer for Gesh Financial.

Tiers:
  signal/data    : existing modules (consensus, kelly, monte_carlo, regime, ai_analyzer, ...)
  contract       : TradeIntent dataclass — single typed handoff between tiers
  risk_gate      : 8 hard gates between intent and execution
  execution      : broker wrapper (paper mode now, live mode later)
  state          : 3 SQLite tables (trade_intents, risk_decisions, orders)

Boot order: state.init_trading_tables() runs on first import.
"""

from .state import init_trading_tables

# Auto-init the schema so callers never have to remember.
init_trading_tables()
