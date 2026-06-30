from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import robin_stocks.robinhood as rh

DEFAULT_WATCHLIST: list[str] = ["MSTR", "SPY", "QQQ"]

# MCP-first snapshot the daily-analysis chain writes (build_snapshot_from_mcp.py). Read as a
# fallback when the live robin_stocks session is down so the dashboard degrades to last-known
# data instead of blanking out. Overridable for tests via GESH_SNAPSHOT_PATH.
_SNAPSHOT_PATH = Path(os.getenv("GESH_SNAPSHOT_PATH",
                                str(Path(__file__).parent / "live_portfolio_snapshot.json")))
# Beyond this age a snapshot is too stale to pass off as the live portfolio — serving it would
# show year-old numbers as "live" (Codex MEDIUM-3). Default 7d covers a long holiday weekend
# between daily-chain runs; override with GESH_SNAPSHOT_MAX_AGE_SECONDS.
_SNAPSHOT_MAX_AGE_SECONDS = float(os.getenv("GESH_SNAPSHOT_MAX_AGE_SECONDS", str(7 * 86400)))


class RobinhoodOfflineError(RuntimeError):
    """Raised when the Robinhood session is missing/expired (every API call 401s,
    so robin_stocks returns None). Lets callers degrade to a clean offline state
    instead of dereferencing None. Recover by running reauth.py interactively."""


def load_snapshot_payload(path: Path | None = None) -> dict[str, Any]:
    """Map the MCP-built snapshot (live_portfolio_snapshot.json) into the exact shape
    build_data_payload() returns, so the dashboard can serve last-known data when the live
    robin_stocks session is offline (the MCP-first fallback — see CLAUDE.md). Marks the result
    `offline=True` with the snapshot's age so the UI can flag it as cached, not live.

    Raises RobinhoodOfflineError if the snapshot is missing/unreadable — the caller then
    surfaces the same clean 503 it would have without any snapshot."""
    p = path or _SNAPSHOT_PATH
    try:
        snap = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise RobinhoodOfflineError(
            f"Robinhood offline and no usable snapshot at {p} ({e})."
        ) from e

    # ── validate (Codex MEDIUM-3): a `{}` or schema-broken snapshot must NOT be served as a
    # fresh $0 portfolio, and a stale / timestamp-less one must NOT be shown as live.
    # Check the top-level type BEFORE any .get() — a JSON `[]`/scalar would otherwise raise
    # AttributeError (→ 500) instead of a clean RobinhoodOfflineError (→ 503) (Codex LOW-2).
    if not isinstance(snap, dict):
        raise RobinhoodOfflineError(f"snapshot at {p} is not a JSON object.")
    eqs = snap.get("equity_summary")
    raw_holdings = snap.get("holdings")
    if not (isinstance(eqs, dict) and isinstance(raw_holdings, list)):
        raise RobinhoodOfflineError(f"snapshot at {p} missing equity_summary/holdings.")

    # Headline equity = ACCOUNT-level (positions + cash), matching the live path and the
    # account-level previous close used for day-change — NOT positions-only portfolio_equity
    # (Codex HIGH-2). Must be a finite, positive number.
    headline = eqs.get("account_equity") or eqs.get("total") or eqs.get("portfolio_equity")
    try:
        headline = float(headline)
    except (TypeError, ValueError):
        raise RobinhoodOfflineError(f"snapshot at {p} has no usable account equity.")
    if not math.isfinite(headline) or headline <= 0:
        raise RobinhoodOfflineError(f"snapshot at {p} equity non-finite/non-positive ({headline}).")

    # Require a parseable, not-too-old timestamp.
    fdt = None
    raw_ts = (snap.get("fetched_at") or "").strip()
    if raw_ts:
        try:
            fdt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if fdt.tzinfo is None:
                fdt = fdt.replace(tzinfo=timezone.utc)
        except ValueError:
            fdt = None
    if fdt is None:
        raise RobinhoodOfflineError(f"snapshot at {p} has no parseable fetched_at.")
    age_s = (datetime.now(timezone.utc) - fdt).total_seconds()
    if age_s > _SNAPSHOT_MAX_AGE_SECONDS:
        raise RobinhoodOfflineError(
            f"snapshot at {p} is {age_s / 86400:.1f}d old "
            f"(> {_SNAPSHOT_MAX_AGE_SECONDS / 86400:.1f}d max).")

    total_equity = sum(float(h.get("equity") or 0) for h in raw_holdings) or 0.0

    holdings = []
    for h in raw_holdings:
        qty = float(h.get("quantity") or 0)
        avg = float(h.get("average_buy_price") or 0)
        equity = float(h.get("equity") or 0)
        price = float(h.get("price") or h.get("current_price") or 0)
        holdings.append({
            "symbol":               h.get("symbol", ""),
            "quantity":             qty,
            "average_buy_price":    avg,
            "equity":               equity,
            "percent_change":       float(h.get("percent_change") or 0),
            "equity_change":        round(equity - avg * qty, 2),
            "current_price":        price,
            "type":                 h.get("type", "stock"),
            "pe_ratio":             h.get("pe_ratio"),
            "portfolio_percentage": round(equity / total_equity * 100, 1) if total_equity else 0.0,
        })

    return {
        "as_of":         raw_ts,
        "offline":       True,                       # not live — served from snapshot
        "source":        snap.get("source", "snapshot"),
        "snapshot_age_seconds": round(age_s),
        "equity_summary": {
            "portfolio_equity":               headline,   # account-level (positions + cash)
            "extended_hours_equity":          float(eqs.get("extended_hours_equity") or 0),
            "adjusted_equity_previous_close": float(eqs.get("adjusted_equity_previous_close") or 0),
            "cash":                           float(eqs.get("cash") or 0),
            "buying_power":                   float(eqs.get("buying_power") or eqs.get("deployable") or 0),
        },
        "holdings":  holdings,
        "watchlist": [],                             # live quotes need RH — empty when offline
    }


def get_portfolio_holdings() -> pd.DataFrame:
    rows = []

    # ── Stocks ────────────────────────────────────────────────────────────────
    # robin_stocks raises a BARE Exception ("<fn> can only be called when logged in") when the
    # session is dead. Normalize it to RobinhoodOfflineError so the API layer can fall back to
    # the snapshot for offline-only, and let genuine bugs stay 500s (Codex MEDIUM-4).
    try:
        raw: dict[str, Any] = rh.build_holdings()
    except RobinhoodOfflineError:
        raise
    except Exception as e:
        if "logged in" in str(e).lower():
            raise RobinhoodOfflineError(
                "Robinhood session not logged in (build_holdings) — run reauth.py."
            ) from e
        raise
    for symbol, data in (raw or {}).items():
        try:
            pe = float(data.get("pe_ratio") or "nan")
            pe = None if (pe != pe) else pe  # NaN check
        except (TypeError, ValueError):
            pe = None
        rows.append({
            "symbol": symbol,
            "quantity": float(data.get("quantity", 0)),
            "average_buy_price": float(data.get("average_buy_price", 0)),
            "equity": float(data.get("equity", 0)),
            "percent_change": float(data.get("percent_change", 0)),
            "equity_change": float(data.get("equity_change", 0)),
            "current_price": float(data.get("price", 0)),
            "type": data.get("type", "stock"),
            "pe_ratio": pe,
            "portfolio_percentage": float(data.get("percentage", 0)),
        })

    # ── Crypto ────────────────────────────────────────────────────────────────
    try:
        crypto_positions = rh.get_crypto_positions() or []
        for pos in crypto_positions:
            qty = float(pos.get("quantity", 0) or 0)
            if qty <= 0:
                continue
            symbol = (pos.get("currency", {}) or {}).get("code", "")
            if not symbol:
                continue
            cost_bases = pos.get("cost_bases") or []
            avg_cost = float(cost_bases[0].get("direct_cost_basis", 0) or 0) / qty if (cost_bases and qty) else 0.0
            # Fetch live crypto quote
            quote = {}
            try:
                quotes = rh.get_crypto_quote(symbol) or {}
                quote = quotes if isinstance(quotes, dict) else {}
            except Exception:
                pass
            current_price = float(quote.get("mark_price") or quote.get("bid_price") or 0)
            equity = qty * current_price
            prev_price = float(quote.get("open_price") or current_price or 0)
            pct_chg = ((current_price - prev_price) / prev_price * 100) if prev_price else 0.0
            rows.append({
                "symbol": symbol,
                "quantity": qty,
                "average_buy_price": avg_cost,
                "equity": equity,
                "percent_change": round(pct_chg, 2),
                "equity_change": round(equity - (avg_cost * qty), 2),
                "current_price": current_price,
                "type": "cryptocurrency",
                "pe_ratio": None,
                "portfolio_percentage": 0.0,  # recalculated below
            })
    except Exception:
        pass  # crypto fetch failed gracefully — stocks still shown

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("equity", ascending=False).reset_index(drop=True)

    # Recalculate portfolio_percentage based on combined equity
    total_equity = df["equity"].sum()
    if total_equity > 0:
        df["portfolio_percentage"] = (df["equity"] / total_equity * 100).round(1)

    return df

def get_total_equity() -> dict[str, float]:
    profile = rh.load_portfolio_profile()
    account = rh.load_account_profile()

    # Unauthorized / expired session: robin_stocks returns None for both calls.
    # Fail fast with a typed error so the API degrades to a clean offline 503
    # instead of an AttributeError -> 500 on account.get(...) below.
    if not profile or not account:
        raise RobinhoodOfflineError(
            "Robinhood session unauthorized or expired — run reauth.py to restore live data."
        )

    # GOLD ACCOUNT WARNING:
    #   account["buying_power"] = Gold margin extended credit (~$17k) — NEVER use for cash display
    #   profile["withdrawable_amount"] = None for Gold accounts — unreliable
    #   margin_balances["portfolio_cash"] = real uninvested cash (e.g. $535.44) — USE THIS
    #   margin_balances["unallocated_margin_cash"] = same as portfolio_cash for cash accounts
    margin = account.get("margin_balances") or {}
    real_cash = (
        float(margin.get("portfolio_cash") or 0)
        or float(margin.get("unallocated_margin_cash") or 0)
        or float(profile.get("withdrawable_amount") or 0)
    )

    return {
        "portfolio_equity":               float(profile.get("equity", 0) or 0),
        "extended_hours_equity":          float(profile.get("extended_hours_equity", 0) or 0),
        "adjusted_equity_previous_close": float(profile.get("adjusted_equity_previous_close", 0) or 0),
        "cash":                           real_cash,      # real uninvested cash (not Gold margin)
        "buying_power":                   real_cash,      # same — real deployable cash
    }

def get_watchlist_quotes(symbols: list[str] | None = None) -> pd.DataFrame:
    if symbols is None:
        symbols = DEFAULT_WATCHLIST
    quotes: list[dict] = rh.get_quotes(symbols) or []
    rows = []
    for sym, q in zip(symbols, quotes):
        if not q:
            continue
        last = float(q.get("last_trade_price") or q.get("last_extended_hours_trade_price") or 0)
        prev = float(q.get("previous_close", 0) or 0)
        chg = last - prev
        chg_p = (chg / prev * 100) if prev else 0.0
        rows.append({"symbol": sym, "last_trade_price": last, "ask_price": float(q.get("ask_price", 0) or 0), "bid_price": float(q.get("bid_price", 0) or 0), "previous_close": prev, "change": round(chg, 4), "change_pct": round(chg_p, 2)})
    return pd.DataFrame(rows)

def build_data_payload(watchlist: list[str] | None = None) -> dict[str, Any]:
    holdings_df = get_portfolio_holdings()
    equity_info = get_total_equity()
    watchlist_df = get_watchlist_quotes(watchlist)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "equity_summary": equity_info,
        "holdings": holdings_df.to_dict(orient="records"),
        "watchlist": watchlist_df.to_dict(orient="records"),
    }
