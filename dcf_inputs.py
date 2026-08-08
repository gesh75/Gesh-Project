"""Fundamental inputs for a real DCF — cash-flow, income and balance-sheet pull.

MEASUREMENT/ANALYSIS ONLY (rule #47). Read-only network fetch; places no trades and
writes no DB rows.

Why this exists: the product's `fair_value_estimate` is a field Claude fills in
(`ai_analyzer.py`: "Your fair-value estimate per share"). It is an LLM opinion, not a
model output, yet it drives `fair_value.canonical_pct_diff` -> the Kelly AI penalty ->
the holdings cards. This module fetches the statements a DCF actually needs so that
number can be produced deterministically and audited.

Source: yfinance. OpenBB is the repo's stated primary for fundamentals but is NOT
installed in this venv (CI excludes it — its resolver backtracks for 20+ min), so the
documented yfinance fallback tier is the real path here. Under the corporate VPN every
Yahoo host is SNI-blocked (rule #54) and this returns `available=False` rather than
guessing.

FABRICATION POLICY (rules #45/#51/#52/#56/#59). Every field is either fetched or
explicitly listed in `missing`. Nothing is defaulted to a plausible-looking constant:
a DCF built on an invented capex reads as a real valuation forever after. Callers must
check `.available` before using any figure.

CLI:
    venv/bin/python3 -m dcf_inputs --symbol AMD
    venv/bin/python3 -m dcf_inputs --symbol AMD --json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

# Row labels as yfinance presents them, in preference order.
_REVENUE_ROWS = ("Total Revenue", "Operating Revenue")
_EBITDA_ROWS = ("Normalized EBITDA", "EBITDA")
_CAPEX_ROWS = ("Capital Expenditure",)
_TAX_ROWS = ("Tax Rate For Calcs",)
_CUR_ASSET_ROWS = ("Current Assets",)
_CUR_LIAB_ROWS = ("Current Liabilities",)
_CASH_ROWS = ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
_DEBT_ROWS = ("Total Debt",)
_SHARE_ROWS = ("Ordinary Shares Number", "Share Issued")

MIN_PERIODS = 3


@dataclass(frozen=True)
class DCFInputs:
    """Statement-derived DCF inputs. All monetary values in millions USD."""

    symbol: str
    years: tuple[int, ...] = ()
    revenue: tuple[float, ...] = ()
    ebitda: tuple[float, ...] = ()
    capex: tuple[float, ...] = ()
    nwc: tuple[float, ...] = ()
    tax_rate: float | None = None
    shares_outstanding: float | None = None   # millions
    net_debt: float | None = None             # millions; negative = net cash
    source: str = "yfinance"
    missing: tuple[str, ...] = ()
    error: str | None = None

    @property
    def available(self) -> bool:
        """True only when a DCF can actually be built from these figures."""
        return (
            self.error is None
            and not self.missing
            and len(self.revenue) >= MIN_PERIODS
            and len(self.ebitda) >= MIN_PERIODS
            and len(self.capex) >= MIN_PERIODS
        )


def _pick_row(frame: Any, candidates: tuple[str, ...]) -> Any | None:
    """First matching row from a statement frame, else None. Never guesses."""
    if frame is None or getattr(frame, "empty", True):
        return None
    for label in candidates:
        if label in frame.index:
            return frame.loc[label]
    return None


def _series_to_millions(row: Any, periods: int, absolute: bool = False) -> list[float] | None:
    """Oldest-first list of the last `periods` values, scaled to millions."""
    if row is None:
        return None
    values = [v for v in row.tolist()[:periods] if v is not None and v == v]  # drop NaN
    if len(values) < MIN_PERIODS:
        return None
    out = [float(v) / 1e6 for v in values]
    if absolute:
        out = [abs(v) for v in out]
    return list(reversed(out))  # yfinance is newest-first; DCFModel wants oldest-first


def fetch_dcf_inputs(symbol: str, periods: int = 3) -> DCFInputs:
    """Pull statements and derive DCF inputs. Missing fields are reported, not invented."""
    try:
        import yfinance as yf  # noqa: PLC0415

        ticker = yf.Ticker(symbol)
        income = ticker.financials
        cash = ticker.cashflow
        balance = ticker.balance_sheet
    except Exception as exc:  # noqa: BLE001 — network/VPN failure is expected (rule #54)
        return DCFInputs(symbol=symbol, error=f"{type(exc).__name__}: {exc}")

    if any(getattr(f, "empty", True) for f in (income, cash, balance)):
        return DCFInputs(
            symbol=symbol,
            error="empty statements (Yahoo unreachable, or no coverage for this symbol)",
        )

    missing: list[str] = []

    revenue = _series_to_millions(_pick_row(income, _REVENUE_ROWS), periods)
    if revenue is None:
        missing.append("revenue")
    ebitda = _series_to_millions(_pick_row(income, _EBITDA_ROWS), periods)
    if ebitda is None:
        missing.append("ebitda")
    capex = _series_to_millions(_pick_row(cash, _CAPEX_ROWS), periods, absolute=True)
    if capex is None:
        missing.append("capex")

    # Net working capital = current assets - current liabilities.
    cur_assets = _series_to_millions(_pick_row(balance, _CUR_ASSET_ROWS), periods)
    cur_liab = _series_to_millions(_pick_row(balance, _CUR_LIAB_ROWS), periods)
    if cur_assets is None or cur_liab is None:
        missing.append("nwc")
        nwc: list[float] = []
    else:
        n = min(len(cur_assets), len(cur_liab))
        nwc = [cur_assets[i] - cur_liab[i] for i in range(n)]

    tax_row = _pick_row(income, _TAX_ROWS)
    tax_rate = None
    if tax_row is not None:
        vals = [v for v in tax_row.tolist() if v is not None and v == v]
        if vals:
            tax_rate = float(vals[0])
    if tax_rate is None:
        missing.append("tax_rate")

    share_row = _pick_row(balance, _SHARE_ROWS)
    shares = None
    if share_row is not None:
        vals = [v for v in share_row.tolist() if v is not None and v == v]
        if vals:
            shares = float(vals[0]) / 1e6
    if shares is None:
        missing.append("shares_outstanding")

    debt_row = _pick_row(balance, _DEBT_ROWS)
    cash_row = _pick_row(balance, _CASH_ROWS)
    net_debt = None
    if debt_row is not None and cash_row is not None:
        dv = [v for v in debt_row.tolist() if v is not None and v == v]
        cv = [v for v in cash_row.tolist() if v is not None and v == v]
        if dv and cv:
            net_debt = (float(dv[0]) - float(cv[0])) / 1e6
    if net_debt is None:
        missing.append("net_debt")

    years = tuple(
        reversed([int(str(c)[:4]) for c in list(income.columns)[:periods]])
    )

    return DCFInputs(
        symbol=symbol,
        years=years,
        revenue=tuple(revenue or ()),
        ebitda=tuple(ebitda or ()),
        capex=tuple(capex or ()),
        nwc=tuple(nwc),
        tax_rate=tax_rate,
        shares_outstanding=shares,
        net_debt=net_debt,
        missing=tuple(missing),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--periods", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = fetch_dcf_inputs(args.symbol.upper(), args.periods)
    if args.json:
        print(json.dumps({**asdict(data), "available": data.available}, indent=2))
        return 0 if data.available else 1

    print(f"\nDCF inputs — {data.symbol}   (source: {data.source}, $M)")
    if data.error:
        print(f"  ERROR: {data.error}")
        return 1
    print(f"  years            : {', '.join(map(str, data.years))}")
    print(f"  revenue          : {[round(v) for v in data.revenue]}")
    print(f"  ebitda           : {[round(v) for v in data.ebitda]}")
    print(f"  capex            : {[round(v) for v in data.capex]}")
    print(f"  nwc              : {[round(v) for v in data.nwc]}")
    print(f"  tax_rate         : {data.tax_rate}")
    print(f"  shares (M)       : {data.shares_outstanding}")
    print(f"  net_debt ($M)    : {data.net_debt}"
          f"{'  (net cash)' if (data.net_debt or 0) < 0 else ''}")
    if data.missing:
        print(f"  MISSING          : {', '.join(data.missing)}  -> DCF not buildable")
    print(f"  available        : {data.available}")
    return 0 if data.available else 1


if __name__ == "__main__":
    raise SystemExit(main())
