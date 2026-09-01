"""
export_portfolio.py — Mac-side Robinhood data exporter
=======================================================
Runs on the Mac (via LaunchAgent at 10:25 AM daily).
Fetches live portfolio data and writes two artefacts the sandbox can read:

  1. portfolio_live.json   — holdings, equity, profile, watchlist quotes
  2. .rh_session/robinhood_session.pickle  — reusable session token so the
                             sandbox can authenticate without MFA

Usage:
    cd ~/Projects/gesh-project && source venv/bin/activate
    python export_portfolio.py

Called automatically by com.gesh.portfolio-export LaunchAgent.
"""

import json
import os
import shutil
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON   = os.path.join(PROJECT_DIR, 'portfolio_live.json')
SESSION_DIR   = os.path.join(PROJECT_DIR, '.rh_session')
# PICKLE_NAME must stay a hardcoded constant — never derive it from external
# input, or the path built from it below becomes a traversal vector.
PICKLE_NAME   = 'robinhood_session'
MAC_PICKLE    = os.path.expanduser(f'~/.tokens/robinhood{PICKLE_NAME}.pickle')
PROJECT_PICKLE = os.path.join(SESSION_DIR, f'robinhood{PICKLE_NAME}.pickle')


def _log(msg: str) -> None:
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def _normalize_holding(symbol: str, data: dict) -> dict | None:
    """Coerce one RH holding row. Returns None on unparseable fields (skip, don't abort)."""
    try:
        qty   = float(data.get('quantity') or 0)
        price = float(data.get('price') or 0)
        eq    = float(data.get('equity') or qty * price)
        avg   = float(data.get('average_buy_price') or 0)
        pct   = float(data.get('percent_change') or 0)
        equity_change = float(data.get('equity_change') or 0)
    except (TypeError, ValueError):
        return None
    return {
        'symbol':            symbol,
        'quantity':          qty,
        'price':             price,
        'equity':            eq,
        'average_buy_price': avg,
        'percent_change':    pct,
        'equity_change':     equity_change,
    }


def _build_equity_summary(
    portfolio_equity: float,
    holdings: list,
    raw_buying_power: float,
    raw_withdrawable: float,
) -> dict:
    """Assemble equity_summary. Never treat Gold leveraged buying_power as cash."""
    total_invested = sum(h['equity'] for h in holdings)
    today_pnl      = sum(h['equity_change'] for h in holdings)
    # Real cash = portfolio equity minus invested equity.
    # On Gold accounts buying_power includes margin so we never use it as cash.
    cash = round(max(0.0, (portfolio_equity or total_invested) - total_invested), 2)
    return {
        'portfolio_equity': round(portfolio_equity or total_invested + cash, 2),
        'cash':             cash,
        'buying_power':     round(raw_withdrawable or raw_buying_power or cash, 2),
        'total_invested':   round(total_invested, 2),
        'today_pnl':        round(today_pnl, 2),
        'today_pnl_pct':    (
            round(today_pnl / (total_invested - today_pnl) * 100, 2)
            if (total_invested - today_pnl) > 0 else 0
        ),
    }


def export() -> dict:
    import robin_stocks.robinhood as rh

    os.makedirs(SESSION_DIR, exist_ok=True)

    _log('Logging in to Robinhood...')
    rh.login(
        username=os.getenv('ROBINHOOD_USER'),
        password=os.getenv('ROBINHOOD_PASS'),
        expiresIn=86400 * 7,
        store_session=True,
        pickle_name=PICKLE_NAME,
    )
    _log('Login OK')

    # ── Portfolio profile ──────────────────────────────────────────────────────
    profile = rh.profiles.load_account_profile() or {}
    portfolio = rh.profiles.load_portfolio_profile() or {}

    portfolio_equity = float(portfolio.get('equity') or 0)
    # Gold accounts: buying_power includes margin, withdrawable_amount is None.
    # Compute real cash as portfolio_equity - total invested (filled after holdings fetch).
    # Store raw API fields for diagnostics.
    _raw_buying_power = float(profile.get('buying_power') or 0)
    _raw_withdrawable = float(profile.get('withdrawable_amount') or 0)
    _log(f'Cash fields — withdrawable={_raw_withdrawable} buying_power={_raw_buying_power} portfolio_equity={portfolio_equity}')

    # ── Holdings ───────────────────────────────────────────────────────────────
    _log('Fetching holdings...')
    raw_holdings = rh.build_holdings()
    holdings = []
    for symbol, data in raw_holdings.items():
        row = _normalize_holding(symbol, data)
        if row is not None:
            holdings.append(row)

    # Sort by equity descending
    holdings.sort(key=lambda h: h['equity'], reverse=True)

    # ── Watchlist quotes ───────────────────────────────────────────────────────
    _log('Fetching watchlist...')
    try:
        watchlist = rh.get_watchlist_by_name('Default') or {}
        wl_symbols = [item.get('symbol') for item in watchlist.get('results', []) if item.get('symbol')]
        wl_quotes  = {}
        if wl_symbols:
            for sym in wl_symbols[:20]:
                q = rh.get_latest_price(sym)
                if q:
                    wl_quotes[sym] = float(q[0])
    except Exception as exc:
        _log(f'Watchlist fetch failed (non-fatal): {exc}')
        wl_quotes = {}

    # ── Assemble output ────────────────────────────────────────────────────────
    equity_summary = _build_equity_summary(
        portfolio_equity, holdings, _raw_buying_power, _raw_withdrawable,
    )
    cash           = equity_summary['cash']
    total_invested = equity_summary['total_invested']

    payload = {
        'exported_at':      datetime.now(timezone.utc).isoformat(),
        'source':           'robinhood_live',
        'holdings':         holdings,
        'equity_summary':   equity_summary,
        'watchlist_quotes': wl_quotes,
        'symbols':          [h['symbol'] for h in holdings],
    }

    # ── Write JSON ─────────────────────────────────────────────────────────────
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(payload, f, indent=2)
    _log(f'Wrote {OUTPUT_JSON}')

    # ── Copy session pickle so sandbox can reuse it ────────────────────────────
    if os.path.isfile(MAC_PICKLE):
        shutil.copy2(MAC_PICKLE, PROJECT_PICKLE)
        _log(f'Copied session pickle → {PROJECT_PICKLE}')
    else:
        _log(f'Pickle not found at {MAC_PICKLE} — sandbox will use JSON only')

    _log(f'Export complete: {len(holdings)} holdings, equity ${total_invested + cash:,.2f}, cash ${cash:,.2f}')
    for h in holdings:
        pct = h['equity'] / (total_invested + cash) * 100 if (total_invested + cash) > 0 else 0
        chg_s = f"{h['percent_change']:+.2f}%" if h['percent_change'] else ''
        print(f"  {h['symbol']:6s} {h['quantity']:8.2f} sh  ${h['price']:8.2f}  ${h['equity']:9,.2f} ({pct:.1f}%) {chg_s}")

    rh.logout()
    return payload


if __name__ == '__main__':
    try:
        export()
        sys.exit(0)
    except Exception as exc:
        print(f'FATAL: {exc}', file=sys.stderr)
        sys.exit(1)
