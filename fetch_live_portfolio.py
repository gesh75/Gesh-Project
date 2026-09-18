"""
fetch_live_portfolio.py — Sandbox-side portfolio data loader
============================================================
Priority fallback chain (fastest/freshest first):

  1. portfolio_live.json   — written by export_portfolio.py on the Mac (freshest)
  2. Robinhood direct      — using the pickle copied by the exporter (no MFA needed)
  3. gesh_financial.db     — DB snapshot (stale, last resort)

The sandbox cannot reach localhost:9000, so we never try that.

Usage in scheduled tasks:
    from fetch_live_portfolio import load_portfolio
    holdings, equity_sum, source, age_minutes = load_portfolio()
"""

from __future__ import annotations

# ── Paths ─────────────────────────────────────────────────────────────────────
import glob as _glob
import json
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_GP_CANDIDATES  = _glob.glob('/sessions/*/mnt/gesh-project')
GP  = Path(_GP_CANDIDATES[0])  if _GP_CANDIDATES  else Path('/sessions/local/mnt/gesh-project')
# Guard against path injection: the glob root must resolve under /sessions/.
if not str(GP.resolve()).startswith('/sessions/'):
    GP = Path('/sessions/local/mnt/gesh-project')

LIVE_JSON      = GP / 'portfolio_live.json'
SESSION_PICKLE = GP / '.rh_session' / 'robinhoodrobinhood_session.pickle'
MAIN_DB        = GP / 'gesh_financial.db'
TMP_DB         = Path('/tmp/gesh_main_copy.db')

# Maximum age of portfolio_live.json before we try to refresh (minutes)
MAX_JSON_AGE_MINUTES = 240  # 4 hours


def _log(msg: str) -> None:
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[portfolio_loader] {ts} — {msg}', flush=True)


def _session_file_is_safe(path: Path) -> bool:
    """Only trust a session pickle owned by this user and not writable by others."""
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_uid == os.getuid() and not (st.st_mode & 0o022)


# ─────────────────────────────────────────────────────────────────────────────
# Source 1: portfolio_live.json
# ─────────────────────────────────────────────────────────────────────────────

def _load_from_json() -> tuple[list, dict, float] | None:
    """Return (holdings, equity_sum, age_minutes) or None if missing/stale."""
    if not LIVE_JSON.exists():
        _log('portfolio_live.json not found')
        return None

    try:
        with open(LIVE_JSON) as f:
            payload = json.load(f)
    except Exception as exc:
        _log(f'portfolio_live.json parse error: {exc}')
        return None

    # Same honesty as _load_from_db: a list/string/number must not crash the
    # fallback chain. AttributeError on .get() used to abort load_portfolio().
    if not isinstance(payload, dict):
        _log('portfolio_live.json is not an object — skipping')
        return None

    exported_at_str = payload.get('exported_at', '')
    try:
        exported_at = datetime.fromisoformat(exported_at_str)
        if exported_at.tzinfo is None:
            exported_at = exported_at.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - exported_at).total_seconds() / 60
    except (ValueError, TypeError):
        # null / int / list stamps are unparseable — treat as stale, same as
        # a garbage string. TypeError used to escape and abort the chain.
        age_min = 9999

    if age_min > MAX_JSON_AGE_MINUTES:
        _log(f'portfolio_live.json is {age_min:.0f} min old (max {MAX_JSON_AGE_MINUTES}) — skipping')
        return None

    holdings   = payload.get('holdings', [])
    equity_sum = payload.get('equity_summary', {})

    # Same honesty as _load_from_db: a dict/string must not be treated as a book.
    if not isinstance(holdings, list) or not holdings:
        _log('portfolio_live.json has no holdings — skipping')
        return None
    if not isinstance(equity_sum, dict):
        _log('portfolio_live.json equity_summary is not an object — skipping')
        return None

    _log(f'✅ Source 1: portfolio_live.json — {len(holdings)} holdings, age {age_min:.0f} min')
    return holdings, equity_sum, age_min


# ─────────────────────────────────────────────────────────────────────────────
# Source 2: Direct Robinhood via copied session pickle
# ─────────────────────────────────────────────────────────────────────────────

def _load_from_robinhood() -> tuple[list, dict, float] | None:
    """Try to authenticate with the copied pickle and fetch live data."""
    if not SESSION_PICKLE.exists():
        _log('No session pickle found — skipping direct Robinhood fetch')
        return None
    if not _session_file_is_safe(SESSION_PICKLE):
        _log('Session pickle has unsafe ownership or permissions — skipping direct Robinhood fetch')
        return None

    # Add robin_stocks to path if needed
    for candidate in ['/tmp/rh_libs', '/tmp/pylibs']:
        if candidate not in sys.path and Path(candidate).exists():
            sys.path.insert(0, candidate)

    try:
        import robin_stocks.robinhood as rh
    except ImportError:
        _log('robin_stocks not installed — skipping direct fetch')
        return None

    env_path = GP / '.env'
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except ImportError:
            pass

    user = os.getenv('ROBINHOOD_USER', '')
    pwd  = os.getenv('ROBINHOOD_PASS', '')

    try:
        _log('Attempting Robinhood login via stored session...')
        result = rh.login(
            username=user,
            password=pwd,
            store_session=True,
            pickle_path=str(SESSION_PICKLE.parent),
            pickle_name='robinhood_session',
            expiresIn=86400 * 7,
        )
        if not isinstance(result, dict) or 'access_token' not in result:
            _log(f'Login returned unexpected result: {result}')
            rh.logout()
            return None

        profile   = rh.profiles.load_account_profile() or {}
        portfolio = rh.profiles.load_portfolio_profile() or {}
        raw       = rh.build_holdings() or {}

        cash            = float(profile.get('withdrawable_amount') or 0)
        portfolio_equity = float(portfolio.get('equity') or 0)
        buying_power    = float(profile.get('buying_power') or cash)

        holdings = []
        for symbol, data in raw.items():
            try:
                qty  = float(data.get('quantity') or 0)
                px   = float(data.get('price') or 0)
                eq   = float(data.get('equity') or qty * px)
                avg  = float(data.get('average_buy_price') or 0)
                pct  = float(data.get('percent_change') or 0)
                chg  = float(data.get('equity_change') or 0)
                holdings.append({'symbol': symbol, 'quantity': qty, 'price': px,
                                  'equity': eq, 'average_buy_price': avg,
                                  'percent_change': pct, 'equity_change': chg})
            except (TypeError, ValueError):
                continue

        holdings.sort(key=lambda h: h['equity'], reverse=True)
        total_invested = sum(h['equity'] for h in holdings)
        today_pnl      = sum(h['equity_change'] for h in holdings)

        equity_sum = {
            'portfolio_equity': round(portfolio_equity or total_invested + cash, 2),
            'cash':             round(cash, 2),
            'buying_power':     round(buying_power, 2),
            'total_invested':   round(total_invested, 2),
            'today_pnl':        round(today_pnl, 2),
        }

        rh.logout()

        # Persist fresh data to JSON for next run
        _persist_to_json(holdings, equity_sum)

        _log(f'✅ Source 2: Robinhood live — {len(holdings)} holdings, cash ${cash:,.2f}')
        return holdings, equity_sum, 0.0

    except Exception as exc:
        _log(f'Robinhood direct fetch failed: {exc}')
        try:
            rh.logout()
        except Exception:
            pass
        return None


def _persist_to_json(holdings: list, equity_sum: dict) -> None:
    """Write fresh data to portfolio_live.json so the next run can use it."""
    try:
        payload = {
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'source': 'robinhood_sandbox_direct',
            'holdings': holdings,
            'equity_summary': equity_sum,
            'symbols': [h['symbol'] for h in holdings],
        }
        with open(LIVE_JSON, 'w') as f:
            json.dump(payload, f, indent=2)
        _log(f'Persisted {len(holdings)} holdings → portfolio_live.json')
    except Exception as exc:
        _log(f'Could not persist to JSON (non-fatal): {exc}')


# ─────────────────────────────────────────────────────────────────────────────
# Source 3: DB snapshot (last resort)
# ─────────────────────────────────────────────────────────────────────────────

def _load_from_db() -> tuple[list, dict, float] | None:
    """Read latest analysis_run from gesh_financial.db (may be stale)."""
    import shutil

    db_path = None
    if TMP_DB.exists():
        db_path = TMP_DB
    elif MAIN_DB.exists():
        try:
            shutil.copy2(MAIN_DB, TMP_DB)
            db_path = TMP_DB
        except Exception as exc:
            _log(f'DB copy failed: {exc}')
            return None

    if db_path is None:
        _log('No DB found')
        return None

    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        cur  = conn.cursor()
        cur.execute('SELECT run_at, payload_json FROM analysis_runs ORDER BY run_at DESC LIMIT 1')
        row = cur.fetchone()
        conn.close()

        if not row:
            _log('DB has no analysis_runs')
            return None

        run_at_str, payload_json = row
        payload    = json.loads(payload_json)
        if not isinstance(payload, dict):
            _log('Malformed payload_json — expected object')
            return None
        holdings   = payload.get('holdings', [])
        if not isinstance(holdings, list):
            _log('Malformed payload_json — holdings not a list')
            return None
        equity_sum = payload.get('equity_summary', {})

        try:
            run_at  = datetime.fromisoformat(run_at_str)
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            age_min = (datetime.now(timezone.utc) - run_at).total_seconds() / 60
        except ValueError:
            age_min = 9999

        _log(f'⚠️  Source 3: DB snapshot — {len(holdings)} holdings, age {age_min:.0f} min ({age_min/60:.1f} hours) — DATA MAY BE STALE')
        warnings.warn(
            f'Using DB snapshot from {run_at_str} ({age_min:.0f} min ago). '
            f'Run export_portfolio.py on your Mac to get live data.',
            UserWarning, stacklevel=3
        )
        return holdings, equity_sum, age_min

    except Exception as exc:
        _log(f'DB read failed: {exc}')
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def load_portfolio() -> tuple[list[dict], dict[str, Any], str, float]:
    """
    Load portfolio data using the best available source.

    Returns:
        holdings     — list of holding dicts (symbol, quantity, price, equity, …)
        equity_sum   — dict with portfolio_equity, cash, buying_power, today_pnl, …
        source       — 'json_live' | 'robinhood_direct' | 'db_snapshot'
        age_minutes  — how old the data is (0 = just fetched)

    Raises:
        RuntimeError if all sources fail.
    """
    # 1. Fresh JSON from Mac exporter
    result = _load_from_json()
    if result:
        h, eq, age = result
        return h, eq, 'json_live', age

    # 2. Direct Robinhood via copied pickle
    result = _load_from_robinhood()
    if result:
        h, eq, age = result
        return h, eq, 'robinhood_direct', age

    # 3. DB snapshot (stale fallback)
    result = _load_from_db()
    if result:
        h, eq, age = result
        return h, eq, 'db_snapshot', age

    raise RuntimeError(
        'All portfolio data sources failed.\n'
        'Fix: Run  python export_portfolio.py  on your Mac, then retry.'
    )


def print_staleness_warning(source: str, age_min: float) -> None:
    """Print a prominent warning if data is stale."""
    if source == 'db_snapshot' and age_min > 60:
        print('\n' + '='*70)
        print(f'⚠️  STALE DATA WARNING — source: {source}, age: {age_min:.0f} min ({age_min/60:.1f}h)')
        print('   Analysis results may not reflect your current portfolio.')
        print('   Fix: Run  python export_portfolio.py  on your Mac at 10:25 AM')
        print('='*70 + '\n')
    elif source != 'json_live' and source != 'robinhood_direct':
        print(f'[data] source={source}, age={age_min:.0f} min')


if __name__ == '__main__':
    # Quick test
    holdings, equity_sum, source, age = load_portfolio()
    print(f'\nSource: {source}  |  Age: {age:.0f} min')
    print(f"Portfolio: ${equity_sum.get('portfolio_equity',0):,.2f}  Cash: ${equity_sum.get('cash',0):,.2f}")
    print(f"Holdings ({len(holdings)}):")
    total = equity_sum.get('portfolio_equity', 1)
    for h in holdings:
        print(f"  {h['symbol']:6s}  {h['quantity']:8.2f} sh  ${h['price']:8.2f}  ${h['equity']:9,.2f}  ({h['equity']/total*100:.1f}%)")
