"""
trading/ — autonomous execution layer for Gesh Financial.

Tiers:
  signal/data    : existing modules (consensus, kelly, monte_carlo, regime, ai_analyzer, ...)
  contract       : TradeIntent dataclass — single typed handoff between tiers
  risk_gate      : 8 hard gates between intent and execution
  execution      : broker wrapper (paper mode now, live mode later)
  state          : 3 SQLite tables (trade_intents, risk_decisions, orders)

Schema initialization is explicit: server.py and autopilot call
init_trading_tables(). Importing this package does not CREATE/DROP against
the operational DB (Astra R7).
"""

from .state import init_trading_tables

__all__ = ["init_trading_tables"]
