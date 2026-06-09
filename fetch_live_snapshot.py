#!/usr/bin/env python3
"""
fetch_live_snapshot.py
Fetches live Robinhood portfolio + prices and writes to live_portfolio_snapshot.json.
Called by the GESH scheduled task before analysis to ensure fresh data.
Run with: /path/to/venv/bin/python3.14 fetch_live_snapshot.py
"""
import os, sys, json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except Exception:
    pass

OUTPUT = BASE_DIR / 'live_portfolio_snapshot.json'

PICKLE_PATH = os.path.expanduser('~/.tokens/robinhoodrobinhood_session.pickle')


def _restore_session() -> bool:
    """Restore the stored Robinhood token from pickle without re-authenticating.
    Mirrors auth.py _restore_pickle_session() — never triggers MFA/device-approval."""
    import pickle
    if not os.path.isfile(PICKLE_PATH):
        return False
    _st = os.stat(PICKLE_PATH)
    if _st.st_uid != os.getuid() or (_st.st_mode & 0o022):
        print('[fetch_live] Refusing unsafe (writable) session file', flush=True)
        return False
    try:
        with open(PICKLE_PATH, 'rb') as fh:
            data = pickle.load(fh)
        authorization = f"{data['token_type']} {data['access_token']}"
    except Exception as e:
        print(f'[fetch_live] Pickle read error: {e}', flush=True)
        return False
    try:
        from robin_stocks.robinhood.helper import (
            set_login_state, update_session, request_get,
        )
        from robin_stocks.robinhood.urls import positions_url
        set_login_state(True)
        update_session('Authorization', authorization)
        # Lightweight auth check
        res = request_get(positions_url(), 'pagination',
                          {'nonzero': 'true'}, jsonify_data=False)
        res.raise_for_status()
        print('[fetch_live] Session restored from pickle OK', flush=True)
        return True
    except Exception as e:
        print(f'[fetch_live] Session restore failed: {e}', flush=True)
        return False


def fetch():
    import robin_stocks.robinhood as r

    if not _restore_session():
        # Only fall back to fresh login if pickle fails
        user   = os.getenv('ROBINHOOD_USER')
        passwd = os.getenv('ROBINHOOD_PASS')
        if not user or not passwd:
            raise RuntimeError('No stored session and ROBINHOOD_USER/PASS not set')
        print('[fetch_live] No stored session — logging in fresh...', flush=True)
        r.login(username=user, password=passwd, expiresIn=86400,
                scope='internal', store_session=True)
        print('[fetch_live] Fresh login OK', flush=True)

    # Profile / cash
    profile = r.profiles.load_portfolio_profile()
    account = r.profiles.load_account_profile()
    # ── Deployable cash — THREE distinct fields, verified against the RH MCP 2026-06-09 ──
    # Confusing these three is the source of the $531/$1,718/$4,031 churn the radar saw:
    #   • portfolio_cash         = settled, UNLEVERAGED cash. Matches the RH MCP's
    #                              buying_power.buying_power (the broker's authoritative
    #                              spendable). THIS is the figure to size against.
    #   • uncleared_deposits     = pending instant-deposit credit (e.g. $3,500). The broker
    #                              only *sometimes* extends it as buying power; it is
    #                              revocable and NOT settled — never size against it as
    #                              durable dry powder. Surfaced separately so reports can
    #                              show "settled $X + $Y pending".
    #   • account['buying_power'] = LEVERAGED Gold-margin buying power (observed $21,632
    #                              while settled was only $1,718). NEVER use this for
    #                              deployability — it would size real trades on margin.
    #                              Kept as a reference field only.
    # Do NOT use withdrawable_amount (reads $0 while a deposit clears) and NEVER
    # account['cash'] (negative margin-accounting figure on Gold accounts — CLAUDE.md rule).
    settled_cash       = float(account.get('portfolio_cash') or 0)
    if settled_cash <= 0:
        settled_cash   = float(profile.get('withdrawable_amount') or 0)
    pending_deposits   = float(account.get('uncleared_deposits') or 0)
    bp_leveraged       = float(account.get('buying_power') or 0)   # incl. Gold margin — reference ONLY
    withdrawable       = float(profile.get('withdrawable_amount') or 0)
    account_equity     = float(profile.get('equity') or 0)         # app headline total

    # Deployable = settled cash, hard-capped so a leveraged buying_power can NEVER inflate
    # it past actual cash. The cap only bites in the impossible margin<settled case; its
    # real job is to make "size against account['buying_power']" structurally unreachable.
    deployable = min(settled_cash, bp_leveraged) if bp_leveraged > 0 else settled_cash
    cash       = deployable   # back-compat alias consumed by radar / gates / report
    margin_buying_power = bp_leveraged  # legacy snapshot key (kept; reference only)
    uncleared_deposits  = pending_deposits  # legacy snapshot key alias

    # Holdings
    positions = r.account.get_open_stock_positions()
    holdings  = []
    total_equity = 0.0

    for pos in positions:
        try:
            qty   = float(pos.get('quantity', 0))
            if qty <= 0:
                continue
            instr = r.stocks.get_instrument_by_url(pos['instrument'])
            sym   = instr['symbol']
            quote = r.stocks.get_latest_price(sym, includeExtendedHours=False)
            price = float(quote[0]) if quote else 0.0
            avg   = float(pos.get('average_buy_price', 0))
            eq    = price * qty
            total_equity += eq
            holdings.append({
                'symbol':             sym,
                'quantity':           qty,
                'price':              round(price, 4),
                'average_buy_price':  round(avg, 4),
                'equity':             round(eq, 4),
                'percent_change':     round((price / avg - 1) * 100, 2) if avg > 0 else 0,
            })
            print(f'  {sym}: {qty} sh @ ${price:.2f}  eq=${eq:.2f}', flush=True)
        except Exception as e:
            print(f'  WARNING: skipping position — {e}', flush=True)

    portfolio_total = total_equity + cash

    snapshot = {
        'fetched_at':    datetime.now(timezone.utc).isoformat(),
        'source':        'robinhood_live',
        'holdings':      holdings,
        'equity_summary': {
            'portfolio_equity':    round(total_equity, 2),       # positions market value
            'cash':                round(cash, 2),               # = deployable (back-compat alias)
            'deployable':          round(deployable, 2),         # canonical sizing figure (settled, unleveraged)
            'settled_cash':        round(settled_cash, 2),       # settled cash = broker unleveraged BP
            'pending_deposits':    round(pending_deposits, 2),   # instant-deposit credit — revocable, not settled
            'buying_power':        round(deployable, 2),         # = deployable (NEVER account['buying_power'])
            'buying_power_leveraged': round(bp_leveraged, 2),    # Gold margin — reference ONLY, never size on this
            'margin_buying_power': round(margin_buying_power, 2),  # legacy key (= buying_power_leveraged)
            'withdrawable_amount': round(withdrawable, 2),       # reference only (reads $0 while deposits clear)
            'uncleared_deposits':  round(uncleared_deposits, 2),  # legacy key (= pending_deposits)
            'total':               round(portfolio_total, 2),
            'account_equity':      round(account_equity, 2),     # app headline total
        },
    }

    OUTPUT.write_text(json.dumps(snapshot, indent=2))
    print(f'[fetch_live] Snapshot written → {OUTPUT}', flush=True)
    print(f'[fetch_live] {len(holdings)} holdings  total=${portfolio_total:,.2f}  cash=${cash:,.2f}', flush=True)
    return snapshot

if __name__ == '__main__':
    fetch()
