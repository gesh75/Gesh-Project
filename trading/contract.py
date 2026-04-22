"""
trading/contract.py — the single typed handoff between signal and execution.

EVERY signal source must produce a TradeIntent (or list thereof).
The risk gate and execution layer ONLY accept TradeIntent.

This is the choke point that lets you swap engines, A/B different strategies,
backtest, or replay history — because the type contract is fixed.
"""

from __future__ import annotations
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Action = Literal["BUY", "SELL", "HOLD"]

SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TradeIntent:
    """One trade idea, fully described, source-tagged, JSON-serializable."""
    symbol: str
    action: Action
    confidence: float                                # 0..1
    source: str                                      # "consensus" | "kelly" | "manual" | ...
    target_position_pct: float | None = None         # desired % of equity (0..1)
    stop_loss: float | None = None                   # absolute price
    take_profit: float | None = None                 # absolute price
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
    generated_at: str = field(default_factory=_utc_now_iso)
    schema_version: int = SCHEMA_VERSION
    intent_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    rationale: str | None = None                     # free-text "why"

    # ---- validation -----------------------------------------------------

    def validate(self) -> list[str]:
        """Return list of human-readable problems. Empty list = valid."""
        problems: list[str] = []
        if self.action not in ("BUY", "SELL", "HOLD"):
            problems.append(f"action must be BUY/SELL/HOLD, got {self.action!r}")
        if not (0.0 <= self.confidence <= 1.0):
            problems.append(f"confidence must be in [0,1], got {self.confidence}")
        if self.target_position_pct is not None and not (0.0 <= self.target_position_pct <= 1.0):
            problems.append(f"target_position_pct must be in [0,1], got {self.target_position_pct}")
        if self.action in ("BUY", "SELL") and self.target_position_pct is None:
            problems.append("BUY/SELL intents require target_position_pct")
        if self.stop_loss is not None and self.stop_loss <= 0:
            problems.append("stop_loss must be > 0")
        if self.take_profit is not None and self.take_profit <= 0:
            problems.append("take_profit must be > 0")
        if not self.symbol or not self.symbol.isascii():
            problems.append(f"symbol invalid: {self.symbol!r}")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Adapters from existing engines ───────────────────────────────────────────

def from_consensus(
    consensus_row: dict[str, Any],
    *,
    max_position_pct: float = 0.02,
) -> TradeIntent:
    """
    Convert one row from consensus.run_consensus_holding() / run_consensus_portfolio()
    into a TradeIntent.

    Mapping:
      verdict STRONG BUY / BUY  → action BUY,  target_pct = max_position_pct * scale
      verdict STRONG SELL / SELL → action SELL, target_pct = 0  (close position)
      verdict HOLD / INSUFFICIENT → action HOLD, target_pct = None

    `scale` favors STRONG over normal:
      STRONG BUY  → 1.0 * cap
      BUY         → 0.5 * cap
    """
    verdict = (consensus_row.get("verdict") or "").upper()
    raw_score = float(consensus_row.get("raw_score", 0))
    # consensus returns confidence as 0..100 percent of nodes that voted
    confidence = float(consensus_row.get("confidence", 0)) / 100.0

    if verdict == "STRONG BUY":
        action: Action = "BUY"
        target = max_position_pct
    elif verdict == "BUY":
        action = "BUY"
        target = round(max_position_pct * 0.5, 6)
    elif verdict in ("SELL", "STRONG SELL"):
        action = "SELL"
        target = 0.0
    else:
        action = "HOLD"
        target = None

    rationale = (
        f"consensus verdict={verdict} raw_score={raw_score:+.2f} "
        f"active_nodes={consensus_row.get('active_nodes')}/{consensus_row.get('total_nodes')}"
    )

    return TradeIntent(
        symbol=consensus_row["symbol"],
        action=action,
        confidence=confidence,
        source="consensus",
        target_position_pct=target,
        time_in_force="day",
        rationale=rationale,
    )


def manual(
    symbol: str,
    action: Action,
    *,
    confidence: float = 1.0,
    target_position_pct: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    rationale: str | None = "manual override",
) -> TradeIntent:
    """Build a manual TradeIntent (used by /api/trading/dry-run and CLI)."""
    return TradeIntent(
        symbol=symbol.upper(),
        action=action,
        confidence=confidence,
        source="manual",
        target_position_pct=target_position_pct,
        stop_loss=stop_loss,
        take_profit=take_profit,
        rationale=rationale,
    )
