from __future__ import annotations
import robin_stocks.robinhood as rh
import pandas as pd
from datetime import datetime, timezone
from typing import Any

DEFAULT_WATCHLIST: list[str] = ["MSTR", "SPY", "QQQ"]

def get_portfolio_holdings() -> pd.DataFrame:
    rows = []

    # ── Stocks ────────────────────────────────────────────────────────────────
    raw: dict[str, Any] = rh.build_holdings()
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
