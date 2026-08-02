"""Canonical portfolio valuation — ONE definition of every money number.

Before this module the same quantities were re-derived ad hoc in the API layer,
the report renderer and the dashboard's JavaScript, and they disagreed:

  * "Day change" on the customer book was computed in the browser as
    ``Σ equity × (percent_change / 100)`` where ``percent_change`` is the
    LIFETIME return against average cost — so a long-held book rendered
    "+$271,639 (5920.4%)" (audit Q-02). The percentage beside it was the
    UNWEIGHTED MEAN of per-position lifetime returns, so the dollar figure and
    the percentage were not even functions of each other.
  * The owner snapshot's broker-reported ``total`` differs from
    positions + cash, and the customer model book scaled that broker total —
    so holdings + cash came to $97,902 under a $100,000 headline (audit Q-03).

Rules this module enforces:

  * Money and quantities are ``Decimal``. Floats are accepted at the boundary
    and converted through ``str`` so 0.1 means 0.1, never 0.1000000000000000055.
  * Day change is ALWAYS ``total_equity - prior_close_equity``. Cost basis
    never enters it.
  * Missing inputs produce an explicit UNAVAILABLE state (``None`` + a reason),
    never a fabricated 0.0. A zero that means "we don't know" is the defect this
    module exists to prevent.
  * ``total_equity`` is DERIVED from components. A broker-reported total that
    disagrees is retained as ``reported_total`` with the delta surfaced — it is
    evidence, not an override.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

__all__ = [
    "Position", "Valuation", "to_decimal", "value_positions",
    "MONEY_TOLERANCE", "PERCENT_TOLERANCE",
]

# Rounding tolerances for the reconciliation invariants. Components are stored
# to the cent, so a book of N positions can drift at most N half-cents.
MONEY_TOLERANCE = Decimal("0.05")
PERCENT_TOLERANCE = Decimal("0.01")

_ZERO = Decimal("0")
_CENT = Decimal("0.01")


def to_decimal(value: object) -> Decimal | None:
    """Boundary conversion. Returns None for anything not a finite number.

    Floats go through ``str`` deliberately: ``Decimal(0.1)`` is
    0.1000000000000000055511151231257827, which reintroduces the binary drift
    this module exists to keep out of the accounting path.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
        return d if d.is_finite() else None
    return None


def _money(d: Decimal) -> Decimal:
    return d.quantize(_CENT)


@dataclass(frozen=True)
class Position:
    """One holding, in canonical units.

    quantity        — SHARES. Never a dollar notional, never a weight.
    average_cost    — cost per SHARE. None when the user never supplied one.
    current_price   — last price per share. None when unpriced.
    prior_close     — previous session's closing price per share. None when the
                      feed did not provide one; that disables day change rather
                      than defaulting it to current_price (which would report a
                      flat day we cannot evidence).
    """

    symbol: str
    quantity: Decimal
    average_cost: Decimal | None = None
    current_price: Decimal | None = None
    prior_close: Decimal | None = None
    source: str | None = None
    price_as_of: str | None = None

    @classmethod
    def build(cls, symbol: str, quantity: object, *, average_cost: object = None,
              current_price: object = None, prior_close: object = None,
              source: str | None = None, price_as_of: str | None = None) -> "Position":
        qty = to_decimal(quantity)
        if qty is None:
            raise ValueError(f"{symbol}: quantity is not a number ({quantity!r})")
        ac = to_decimal(average_cost)
        return cls(
            symbol=str(symbol).strip().upper(),
            quantity=qty,
            # 0 is the "unknown cost basis" sentinel used across the legacy
            # payloads; treat it as absent so returns are disabled, not zeroed.
            average_cost=ac if (ac is not None and ac > 0) else None,
            current_price=to_decimal(current_price),
            prior_close=to_decimal(prior_close),
            source=source,
            price_as_of=price_as_of,
        )

    @property
    def priced(self) -> bool:
        return self.current_price is not None and self.current_price > 0

    @property
    def market_value(self) -> Decimal | None:
        return None if not self.priced else _money(self.quantity * self.current_price)

    @property
    def prior_close_value(self) -> Decimal | None:
        if self.prior_close is None or self.prior_close <= 0:
            return None
        return _money(self.quantity * self.prior_close)

    @property
    def cost_basis(self) -> Decimal | None:
        return None if self.average_cost is None else _money(self.quantity * self.average_cost)


@dataclass(frozen=True)
class Valuation:
    """Every derived money number, plus what could NOT be derived and why.

    A field is None exactly when the inputs did not support computing it. No
    caller may substitute 0.0 for None: `unavailable` lists the reasons so the
    UI can say "not available" and the analysis layer can refuse to run.
    """

    positions_market_value: Decimal
    cash: Decimal
    total_equity: Decimal
    prior_close_positions_value: Decimal | None
    prior_close_equity: Decimal | None
    day_change_amount: Decimal | None
    day_change_percent: Decimal | None
    total_cost_basis: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_pnl_percent: Decimal | None
    reported_total: Decimal | None = None
    reported_total_delta: Decimal | None = None
    unpriced_symbols: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    issues: tuple[str, ...] = field(default_factory=tuple)

    # ── invariants ───────────────────────────────────────────────────────────
    @property
    def reconciles(self) -> bool:
        """total_equity == positions_market_value + cash, and the percentage is
        the amount over prior-close equity. Both are checked, not assumed."""
        return not self.invariant_failures()

    def invariant_failures(self) -> tuple[str, ...]:
        bad: list[str] = []
        if abs(self.total_equity - (self.positions_market_value + self.cash)) > MONEY_TOLERANCE:
            bad.append(
                f"total_equity {self.total_equity} != positions {self.positions_market_value} "
                f"+ cash {self.cash}")
        if (self.day_change_amount is not None and self.day_change_percent is not None
                and self.prior_close_equity):
            implied = self.day_change_amount / self.prior_close_equity * Decimal(100)
            if abs(implied - self.day_change_percent) > PERCENT_TOLERANCE:
                bad.append(
                    f"day_change_percent {self.day_change_percent} != "
                    f"{self.day_change_amount}/{self.prior_close_equity}")
        return tuple(bad)

    def as_dict(self) -> dict:
        """JSON-able view. Decimals become floats ONLY here, at the edge."""
        def f(d: Decimal | None) -> float | None:
            return None if d is None else float(d)

        return {
            "positions_market_value": f(self.positions_market_value),
            "cash": f(self.cash),
            "total_equity": f(self.total_equity),
            "prior_close_positions_value": f(self.prior_close_positions_value),
            "prior_close_equity": f(self.prior_close_equity),
            "day_change_amount": f(self.day_change_amount),
            "day_change_percent": f(self.day_change_percent),
            "day_change_available": self.day_change_amount is not None,
            "total_cost_basis": f(self.total_cost_basis),
            "unrealized_pnl": f(self.unrealized_pnl),
            "unrealized_pnl_percent": f(self.unrealized_pnl_percent),
            "cost_basis_available": self.total_cost_basis is not None,
            "reported_total": f(self.reported_total),
            "reported_total_delta": f(self.reported_total_delta),
            "unpriced_symbols": list(self.unpriced_symbols),
            "unavailable": list(self.unavailable),
            "reconciles": self.reconciles,
            "issues": list(self.issues) + list(self.invariant_failures()),
        }


def value_positions(
    positions: list[Position],
    *,
    cash: object = 0,
    prior_close_cash: object = None,
    reported_total: object = None,
) -> Valuation:
    """Compute the canonical valuation for a set of positions.

    ``prior_close_cash`` defaults to ``cash``: intraday cash only moves on a
    trade or a transfer, and a caller that knows better passes the real figure.
    ``reported_total`` is a broker-stated account total — recorded and diffed,
    never used in place of the derived total.
    """
    cash_d = to_decimal(cash) or _ZERO
    prior_cash_d = to_decimal(prior_close_cash)
    if prior_cash_d is None:
        prior_cash_d = cash_d

    unavailable: list[str] = []
    unpriced = tuple(p.symbol for p in positions if not p.priced)
    priced = [p for p in positions if p.priced]

    market_value = _money(sum((p.market_value for p in priced), _ZERO))
    total_equity = _money(market_value + cash_d)

    # ── day change: requires a prior close for EVERY priced position ─────────
    missing_prior = [p.symbol for p in priced if p.prior_close_value is None]
    if missing_prior:
        prior_positions = prior_equity = day_amount = day_pct = None
        unavailable.append(
            "day_change: no prior close for " + ", ".join(sorted(missing_prior)))
    else:
        prior_positions = _money(sum((p.prior_close_value for p in priced), _ZERO))
        prior_equity = _money(prior_positions + prior_cash_d)
        day_amount = _money(total_equity - prior_equity)
        if prior_equity > 0:
            day_pct = (day_amount / prior_equity * Decimal(100)).quantize(Decimal("0.0001"))
        else:
            day_pct = None
            unavailable.append("day_change_percent: prior-close equity is zero")

    # ── cost basis / unrealized: requires a cost for EVERY priced position ───
    missing_cost = [p.symbol for p in priced if p.average_cost is None]
    if missing_cost or not priced:
        cost_basis = unrealized = unrealized_pct = None
        unavailable.append(
            "unrealized_pnl: no cost basis for " + ", ".join(sorted(missing_cost))
            if missing_cost else "unrealized_pnl: no priced positions")
    else:
        cost_basis = _money(sum((p.cost_basis for p in priced), _ZERO))
        unrealized = _money(market_value - cost_basis)
        if cost_basis > 0:
            unrealized_pct = (unrealized / cost_basis * Decimal(100)).quantize(Decimal("0.0001"))
        else:
            unrealized_pct = None

    reported = to_decimal(reported_total)
    reported_delta = None if reported is None else _money(reported - total_equity)

    issues: list[str] = []
    if reported is not None and abs(reported_delta) > MONEY_TOLERANCE:
        # NOT an error: a broker total legitimately includes sleeves we do not
        # model (options, crypto, pending settlement). It IS a fact the caller
        # must be able to show rather than silently paper over.
        issues.append(
            f"broker-reported total {reported} exceeds positions+cash by {reported_delta}")
    if unpriced:
        issues.append("unpriced positions excluded from valuation: " + ", ".join(unpriced))

    return Valuation(
        positions_market_value=market_value,
        cash=_money(cash_d),
        total_equity=total_equity,
        prior_close_positions_value=prior_positions,
        prior_close_equity=prior_equity,
        day_change_amount=day_amount,
        day_change_percent=day_pct,
        total_cost_basis=cost_basis,
        unrealized_pnl=unrealized,
        unrealized_pnl_percent=unrealized_pct,
        reported_total=reported,
        reported_total_delta=reported_delta,
        unpriced_symbols=unpriced,
        unavailable=tuple(unavailable),
        issues=tuple(issues),
    )


@dataclass(frozen=True)
class DayChange:
    """Day change over a TOTAL book (positions + cash), or an explicit absence.

    `amount`/`percent` are None exactly when the inputs did not support the
    calculation — never 0.0, which reads as a flat market. `positions_percent`
    is the positions-only return, exposed separately and named unambiguously so
    it can never be mistaken for the total-book figure.
    """
    amount: Decimal | None
    percent: Decimal | None
    prior_close_equity: Decimal | None
    positions_percent: Decimal | None
    available: bool
    reason: str | None = None


def day_change_from_aggregates(
    *,
    positions_value: object,
    prior_close_positions_value: object,
    cash: object = 0,
    prior_close_cash: object = None,
) -> DayChange:
    """Cash-aware day change from AGGREGATE position values.

    `value_positions` needs per-holding prior closes; the owner snapshot instead
    supplies one aggregate `adjusted_equity_previous_close`. This is the same
    arithmetic for that shape — one canonical definition, not a third formula.

    The customer's book total is positions + cash, so the percentage denominator
    must be the prior-close TOTAL. Using prior-close POSITIONS overstates the
    move by the cash fraction: +8,000 on 88,000 positions with 20,000 cash is
    8.0% of a 100,000 book, not 10.0%.

    `prior_close_cash` defaults to `cash` — matching `value_positions` semantics:
    intraday cash only moves on a trade or transfer, and a caller that knows
    better passes the real figure.
    """
    pos = to_decimal(positions_value)
    prior_pos = to_decimal(prior_close_positions_value)
    cash_d = to_decimal(cash) or _ZERO
    prior_cash = to_decimal(prior_close_cash)
    if prior_cash is None:
        prior_cash = cash_d

    if pos is None or prior_pos is None:
        return DayChange(None, None, None, None, False, "missing_prior_close")

    prior_total = _money(prior_pos + prior_cash)
    amount = _money((pos + cash_d) - prior_total)
    if prior_total <= 0:
        return DayChange(amount, None, prior_total, None, False,
                         "prior_close_equity_not_positive")

    percent = (amount / prior_total * Decimal(100)).quantize(Decimal("0.0001"))
    positions_percent = (
        ((pos - prior_pos) / prior_pos * Decimal(100)).quantize(Decimal("0.0001"))
        if prior_pos > 0 else None)
    return DayChange(amount, percent, prior_total, positions_percent, True, None)
