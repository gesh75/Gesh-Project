"""
proof_page.py — public signal-accuracy "proof page" generator.

The Sep-2026 alpha is sold on a documented paper-trade track record. This module
reads the canonical closed-trade table (``paper_outcomes``, written by the trading
engine's ``trading/tracker.py``) and renders a single self-contained HTML page that
shows the full ledger, engine vs manual cohorts, and the AUTHORITATIVE autonomy-gate
progress (the engine's versioned gate_status_dto — engine cohort, 100-close floor).

Design goals:
  * HONEST at 0 closed trades — renders a "building track record · day X of 180"
    state with a visible sample-size banner, never fake numbers.
  * Self-contained — inline SVG equity curve, no external CDN/JS (no SRI surface).
  * Read-only — opens the tracker DB read-only; never writes.
  * Mandatory disclaimers — hypothetical/simulated results + not financial advice.

Usage:
    python3 proof_page.py                       # writes track_record.html
    python3 proof_page.py --db /path/to.db --out track_record.html
"""

from __future__ import annotations

import argparse
import html
import math
import os
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

import seo_pages


# Public page rendered OUTSIDE the dashboard XSS guard, under a CSP that carries
# 'unsafe-inline' — so every DB-sourced string (symbol/source/outcome/timestamps)
# MUST be HTML-escaped at the sink or a `<` in the data executes (8th-pass, Fable
# audit). Numbers are float()/int()-coerced elsewhere and are safe.
def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _wilson_ci_95(
    wins: int,
    n: int,
    *,
    precision: int = 4,
) -> tuple[float, float]:
    """Return the two-sided 95% Wilson score interval for a binomial rate."""
    if n <= 0:
        return (0.0, 0.0)
    z = 1.96
    p = wins / n
    denominator = 1.0 + (z * z / n)
    center = (p + z * z / (2.0 * n)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denominator
    return (
        round(max(0.0, center - margin), precision),
        round(min(1.0, center + margin), precision),
    )


# Tracker lives in the trading engine repo; default to its gesh_financial.db.
DEFAULT_TRACKER_DB = os.getenv(
    "GESH_TRACKER_DB",
    str(Path.home() / "Projects" / "gesh-project-trading" / "gesh_financial.db"),
)
DEFAULT_DAILY_ANALYSIS_DB = os.getenv(
    "GESH_DAILY_ANALYSIS_DB",
    str(Path(__file__).resolve().with_name("gesh_financial.db")),
)
_DAILY_ANALYSIS_DB_UNSET = object()

# Readiness is NEVER derived here: the page consumes the trading engine's versioned
# gate_status_dto() (trading/tracker.py — engine cohort, 100-close floor plus
# expectancy / profit factor / Monte-Carlo / probabilistic+deflated Sharpe / OOS).
# The retired local 30-trade/win-rate/Sharpe/drawdown re-derivation is gone; the
# constants below only shape the fail-CLOSED fallback DTO when that import fails.
GATE_DTO_VERSION_EXPECTED = 2
GATE_REQUIRED_FALLBACK = 100
ENGINE_SOURCES_FALLBACK = ("consensus", "market_scan")
_TRADING_PKG_ALIAS = "gesh_trading_engine"
DAYS_TARGET = 180  # 6-month documented track record (the sales asset)
LAUNCH_DATE = "2026-05-21"  # execution clock start (post bug-fix), per roadmap

# Dark-terminal palette (matches gesh_report.html house style).
_BG, _SURFACE, _BORDER = "#080d14", "#0d1520", "#1a2840"
_CYAN, _GREEN, _YELLOW, _RED, _DIM = "#00d4ff", "#00e87a", "#f0b429", "#ff3c5a", "#5b7290"


_SPY_CACHE: dict[str, Any] = {}
_SPY_CACHE_TTL_S = 3600
_SPY_REQUEST_TIMEOUT_S = 10
_SPY_CACHE_LOCK = threading.Lock()


def _spy_return_since(start_date: str) -> float | None:
    """SPY total return (fraction) from start_date to now, for calendar context.

    It is not exposure-matched to the trade ledger and therefore is never rendered
    as a strategy benchmark or an outperformance claim. The value is TTL-cached
    because the public page renders per request and fails soft to ``None``.
    """
    import time as _time

    hit = _SPY_CACHE.get(start_date)
    if hit is not None and (_time.time() - hit[0]) <= _SPY_CACHE_TTL_S:
        return hit[1]
    # Only one request may refresh the public-page benchmark at a time. Recheck
    # after taking the lock so concurrent cold-cache requests reuse its result.
    with _SPY_CACHE_LOCK:
        hit = _SPY_CACHE.get(start_date)
        if hit is not None and (_time.time() - hit[0]) <= _SPY_CACHE_TTL_S:
            return hit[1]
        try:
            import yfinance as yf
            closes = yf.Ticker("SPY").history(
                period="1y", auto_adjust=True, timeout=_SPY_REQUEST_TIMEOUT_S,
            )["Close"]
            closes = closes[closes.index >= start_date]
            ret = float(closes.iloc[-1] / closes.iloc[0] - 1.0) if len(closes) >= 2 else None
        except Exception:
            ret = None
        _SPY_CACHE[start_date] = (_time.time(), ret)
        return ret


def _days_between(start: str, end: str) -> int:
    d0 = datetime.fromisoformat(start).date()
    d1 = datetime.fromisoformat(end).date()
    return max((d1 - d0).days, 0)


def _trading_home() -> Path:
    """Trading-engine repo root — same GESH_TRADING_HOME sibling-dir convention as
    run_engines2.py / run_ai2.py."""
    return Path(
        os.getenv(
            "GESH_TRADING_HOME",
            str(Path(__file__).parent.parent / "gesh-project-trading"),
        )
    )


def _import_gate_status_fn():
    """Resolve the ENGINE repo's trading.tracker.gate_status_dto.

    The product repo has its OWN `trading` package (risk_gate/state/…) already
    bound in sys.modules inside the server, so a sys.path insert can never reach
    the engine's copy — the engine package is loaded under an alias name via
    importlib instead (the CLAUDE.md "pin via importlib" convention). Separated
    into a seam so tests can stub or fail it deterministically."""
    import importlib
    import importlib.util
    import sys

    pkg_dir = _trading_home() / "trading"
    if _TRADING_PKG_ALIAS not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _TRADING_PKG_ALIAS,
            pkg_dir / "__init__.py",
            submodule_search_locations=[str(pkg_dir)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"trading engine package not found at {pkg_dir}")
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[_TRADING_PKG_ALIAS] = pkg
        try:
            spec.loader.exec_module(pkg)
        except BaseException:
            sys.modules.pop(_TRADING_PKG_ALIAS, None)  # never cache a half-import
            raise
    tracker = importlib.import_module(f"{_TRADING_PKG_ALIAS}.tracker")
    return tracker.gate_status_dto


def _fallback_gate_dto() -> dict[str, Any]:
    """Fail-CLOSED gate when the engine isn't importable: the public page must never
    claim readiness on a degraded path. `closes` is patched by the caller from the
    locally-read ledger so progress copy stays honest."""
    return {
        "version": GATE_DTO_VERSION_EXPECTED,
        "cohort": "engine",
        "cohort_sources": list(ENGINE_SOURCES_FALLBACK),
        "closes": 0,
        "required": GATE_REQUIRED_FALLBACK,
        "criteria": {},
        "pass": False,
        "summary": "autonomy gate unavailable — evaluated fail-closed",
        "eta_note": "",
        "window": "since_launch",
        "as_of": "",
        "fallback": True,
    }


def _load_gate_dto(db_path: str) -> dict[str, Any]:
    """Authoritative autonomy-gate DTO evaluated over `db_path`. Any import/DB
    error degrades to the fail-closed fallback — never an exception, never a pass."""
    try:
        if not Path(db_path).exists():
            # The engine's _conn() would CREATE a missing DB — this page is read-only.
            raise FileNotFoundError(db_path)
        return _import_gate_status_fn()(db_path=db_path)
    except Exception:
        return _fallback_gate_dto()


def _cohort_stats(rows: list[dict]) -> dict[str, Any]:
    """Per-cohort ledger stats (engine vs manual are computed on DISJOINT row sets —
    manual wins can never leak into engine numbers)."""
    closed = len(rows)
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    # `_finite_float` rejects bool/NaN/non-numeric so True cannot invent $1 / 100%.
    # Missing cells stay 0 — the same as the historical `or 0` fallback.
    pnls = [_money(r.get("pnl_pct")) for r in rows]
    realized_pnl_usd = sum(_money(r.get("pnl_usd")) for r in rows)
    gross_entry_notional_usd = sum(
        abs(_money(r.get("qty")) * _money(r.get("entry_price"))) for r in rows
    )
    gross_wins_usd = sum(p for p in (_money(r.get("pnl_usd")) for r in rows) if p > 0)
    gross_losses_usd = -sum(p for p in (_money(r.get("pnl_usd")) for r in rows) if p < 0)
    profit_factor_usd = (
        gross_wins_usd / gross_losses_usd
        if gross_losses_usd > 0
        else (float("inf") if gross_wins_usd > 0 else 0.0)
    )
    sum_trade_returns = sum(pnls)
    pnl_to_gross_entry_notional = (
        realized_pnl_usd / gross_entry_notional_usd if gross_entry_notional_usd else 0.0
    )
    return {
        "closed": closed,
        "wins": wins,
        "losses": sum(1 for r in rows if r["outcome"] == "LOSS"),
        "breakeven": sum(1 for r in rows if r["outcome"] == "BREAKEVEN"),
        "win_rate": round(wins / closed, 4) if closed else 0.0,
        # Compatibility key only. This is a raw sum of individual trade returns,
        # not a portfolio return, because trades overlap and notionals differ.
        "total_pnl_pct": round(sum_trade_returns, 4),
        "sum_trade_returns": round(sum_trade_returns, 4),
        "realized_pnl_usd": round(realized_pnl_usd, 2),
        "gross_entry_notional_usd": round(gross_entry_notional_usd, 2),
        "pnl_to_gross_entry_notional": round(pnl_to_gross_entry_notional, 4),
        # Compatibility alias for the pre-audit API name. The UI uses the exact
        # denominator label and never calls this a portfolio/capital return.
        "return_on_deployed_capital": round(pnl_to_gross_entry_notional, 4),
        "win_rate_ci_95": _wilson_ci_95(wins, closed),
        "cost_sensitivity_20bp_usd": round(gross_entry_notional_usd * 0.002, 2),
        "cost_sensitivity_50bp_usd": round(gross_entry_notional_usd * 0.005, 2),
        "profit_factor_usd": (
            round(profit_factor_usd, 4) if math.isfinite(profit_factor_usd) else profit_factor_usd
        ),
    }


def _finite_float(value: Any, *, positive: bool = False) -> float | None:
    """Strict finite numeric coercion for weakly typed SQLite evidence rows."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or (positive and numeric <= 0):
        return None
    return numeric


def _money(value: Any) -> float:
    """Cohort arithmetic: usable finite number, else 0. Never bool/NaN.

    `_finite_float` already rejects bool / NaN / Inf. Numeric strings still
    parse — the historical `float(x or 0)` path accepted them, and a loosely
    typed SQLite TEXT cell must not silently become $0.
    """
    if isinstance(value, bool):
        return 0.0
    parsed = _finite_float(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            return 0.0
        return numeric if math.isfinite(numeric) else 0.0
    return 0.0


def _daily_analysis_unavailable(reason: str = "ledger unavailable") -> dict[str, Any]:
    """Public-safe failure DTO: no exception text or filesystem path can escape."""
    return {"status": "UNAVAILABLE", "reason": reason}


def _load_daily_analysis_book(db_path: str | os.PathLike | None) -> dict[str, Any]:
    """Read the separate daily-analysis paper book without creating or mutating it.

    Only complete, finite, positive-quantity closes contribute to realized statistics.
    Invalid in-scope rows remain in SQLite and are counted as excluded; rows belonging
    to another scanner source are outside this book and never enter its denominator.
    """
    if db_path is None:
        return {"status": "NOT_CONFIGURED", "reason": "ledger not configured"}
    path = Path(db_path)
    if not path.is_file():
        return _daily_analysis_unavailable()

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        con.row_factory = sqlite3.Row
        try:
            raw_rows = [
                dict(row)
                for row in con.execute(
                    "SELECT symbol, action, shares, entry_price, entry_time, exit_price, "
                    "exit_time, scanner_source, realized_pnl, pnl_pct, outcome "
                    "FROM paper_trades WHERE scanner_source = ? ORDER BY id ASC",
                    ("daily-analysis",),
                ).fetchall()
            ]
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return _daily_analysis_unavailable()

    closed_rows: list[dict[str, Any]] = []
    open_positions = 0
    excluded_invalid_rows = 0
    valid_event_times: list[str] = []
    for row in raw_rows:
        outcome = str(row.get("outcome") or "").upper()
        action = str(row.get("action") or "").upper()
        if action not in {"BUY", "ADD", "TRIM", "SELL"}:
            excluded_invalid_rows += 1
            continue
        shares = _finite_float(row.get("shares"), positive=True)
        entry_price = _finite_float(row.get("entry_price"), positive=True)
        entry_time = str(row.get("entry_time") or "")
        if shares is None or entry_price is None or not entry_time:
            excluded_invalid_rows += 1
            continue
        if outcome == "OPEN":
            if any(
                row.get(field) not in (None, "")
                for field in ("exit_price", "exit_time", "realized_pnl", "pnl_pct")
            ):
                excluded_invalid_rows += 1
                continue
            open_positions += 1
            valid_event_times.append(entry_time)
            continue
        if outcome not in {"WIN", "LOSS", "BREAKEVEN"}:
            excluded_invalid_rows += 1
            continue
        exit_price = _finite_float(row.get("exit_price"), positive=True)
        realized_pnl = _finite_float(row.get("realized_pnl"))
        pnl_pct = _finite_float(row.get("pnl_pct"))
        exit_time = str(row.get("exit_time") or "")
        if exit_price is None or realized_pnl is None or pnl_pct is None or not exit_time:
            excluded_invalid_rows += 1
            continue
        if action in {"BUY", "ADD"}:
            expected_pnl = (exit_price - entry_price) * shares
            expected_pct_points = (exit_price - entry_price) / entry_price * 100.0
        else:  # SELL / TRIM are canonical short-direction closes.
            expected_pnl = (entry_price - exit_price) * shares
            expected_pct_points = (entry_price - exit_price) / entry_price * 100.0
        classification_is_invalid = (
            (outcome == "WIN" and (expected_pnl <= 0.01 or realized_pnl < 0.01))
            or (outcome == "LOSS" and (expected_pnl >= -0.01 or realized_pnl > -0.01))
            or (
                outcome == "BREAKEVEN"
                and (abs(expected_pnl) > 0.01 or abs(realized_pnl) > 0.01)
            )
        )
        if (
            abs(realized_pnl - expected_pnl) > 0.02
            or abs(pnl_pct - expected_pct_points) > 0.02
            or classification_is_invalid
        ):
            excluded_invalid_rows += 1
            continue
        closed_rows.append(
            {
                "qty": shares,
                "entry_price": entry_price,
                "pnl_usd": realized_pnl,
                # `paper_trades.pnl_pct` is stored in percentage points; the tracker
                # book uses fractions. Normalize before sharing cohort math.
                "pnl_pct": pnl_pct / 100.0,
                "outcome": outcome,
            }
        )
        valid_event_times.append(exit_time)

    stats = _cohort_stats(closed_rows)
    return {
        "status": "AVAILABLE",
        **stats,
        "open_positions": open_positions,
        "excluded_invalid_rows": excluded_invalid_rows,
        "as_of": max(valid_event_times, default=""),
    }


def _combined_automated_context(
    engine: dict[str, Any],
    daily_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Arithmetic context only; never consumed by the autonomy gate."""
    if daily_analysis.get("status") != "AVAILABLE":
        return {"available": False}
    closed = int(engine["closed"]) + int(daily_analysis["closed"])
    wins = int(engine["wins"]) + int(daily_analysis["wins"])
    return {
        "available": True,
        "closed": closed,
        "wins": wins,
        "losses": int(engine["losses"]) + int(daily_analysis["losses"]),
        "breakeven": int(engine["breakeven"]) + int(daily_analysis["breakeven"]),
        "win_rate": round(wins / closed, 4) if closed else 0.0,
        "realized_pnl_usd": round(
            float(engine["realized_pnl_usd"]) + float(daily_analysis["realized_pnl_usd"]),
            2,
        ),
        "gross_entry_notional_usd": round(
            float(engine["gross_entry_notional_usd"])
            + float(daily_analysis["gross_entry_notional_usd"]),
            2,
        ),
    }


def load_track_record(
    db_path: str | None = None,
    *,
    daily_analysis_db_path: str | os.PathLike | None | object = _DAILY_ANALYSIS_DB_UNSET,
    launch_date: str = LAUNCH_DATE,
    today: str | None = None,
) -> dict[str, Any]:
    """
    Read closed paper trades and assemble the proof-page data model.

    Returns a dict with: closed_trades, wins, losses, breakeven, win_rate,
    profit_factor, avg_win_pct, avg_loss_pct, total_pnl_pct, sharpe, max_drawdown,
    equity_curve (legacy key for an illustrative sequential trade-return index),
    trades (list), open_positions,
    signal_ratings, days_running, days_target — all-source LEDGER stats — plus
    `engine` / `manual` (disjoint cohort stats) and `gate` (the trading engine's
    versioned engine-cohort autonomy-gate DTO; fail-closed fallback on error).
    """
    tracker_path_was_explicit = db_path is not None
    db_path = db_path or DEFAULT_TRACKER_DB
    if daily_analysis_db_path is _DAILY_ANALYSIS_DB_UNSET:
        # Explicit tracker fixtures/CLI views must not accidentally read the host's
        # production daily ledger. The normal web route supplies neither path and
        # therefore resolves both configured production defaults.
        daily_analysis_db_path = None if tracker_path_was_explicit else DEFAULT_DAILY_ANALYSIS_DB
    today = today or date.today().isoformat()

    rows: list[dict] = []
    open_positions = 0
    signal_ratings = 0
    try:
        # Read-only connect — never create/modify the tracker DB from the product.
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [
                dict(r)
                for r in con.execute(
                    "SELECT symbol, source, action, qty, entry_price, exit_price, entry_at, exit_at,"
                    " pnl_usd, pnl_pct, outcome, hold_days FROM paper_outcomes"
                    " ORDER BY exit_at ASC"
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            rows = []
        for tbl, var in (("paper_positions", "open"), ("signal_overrides", "ratings")):
            try:
                where = " WHERE status='OPEN'" if tbl == "paper_positions" else ""
                n = con.execute(f"SELECT COUNT(*) FROM {tbl}{where}").fetchone()[0]
                if var == "open":
                    open_positions = n
                else:
                    signal_ratings = n
            except sqlite3.OperationalError:
                pass
        con.close()
    except sqlite3.OperationalError:
        rows = []  # DB missing → honest empty state

    # Match the authoritative engine gate's _outcome_rows(days=None) population:
    # since-launch exits with positive quantity only. Legacy/pre-launch/zero-share
    # rows remain in the database for audit history but cannot alter public metrics.
    raw_ledger_rows = rows
    launch_cutoff = f"{launch_date}T00:00:00+00:00"
    rows = [
        r
        for r in raw_ledger_rows
        if str(r.get("exit_at") or "") >= launch_cutoff and float(r.get("qty") or 0) > 0
    ]
    excluded_ledger_rows = len(raw_ledger_rows) - len(rows)

    closed = len(rows)
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    losses = sum(1 for r in rows if r["outcome"] == "LOSS")
    breakeven = sum(1 for r in rows if r["outcome"] == "BREAKEVEN")
    # POLICY (decided 2026-06-08): CONSERVATIVE — BREAKEVEN COUNTS in the denominator
    # (win_rate = wins / all-closed), so a breakeven drags win_rate down. Kept in sync
    # with trading/tracker.py get_win_rate; a harder gate is the safer bias before live.
    win_rate = round(wins / closed, 4) if closed else 0.0

    pnls = [float(r["pnl_pct"] or 0) for r in rows]
    gains = [p for p in pnls if p > 0]
    drops = [p for p in pnls if p < 0]
    profit_factor = (
        round(sum(gains) / abs(sum(drops)), 2) if drops else (float("inf") if gains else 0.0)
    )
    avg_win = round(sum(gains) / len(gains), 4) if gains else 0.0
    avg_loss = round(sum(drops) / len(drops), 4) if drops else 0.0
    total_pnl_pct = round(sum(pnls), 4)

    # Per-trade Sharpe (rf=0), annualised by trade frequency — mirrors tracker.py.
    sharpe = 0.0
    if closed >= 2:
        mean_r = sum(pnls) / closed
        var = sum((p - mean_r) ** 2 for p in pnls) / (closed - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std == 0:
            # [CLAUDE 2026-07-18] audit D-05: match trading/metrics.py — a zero-variance
            # sample is an insufficient sample, not an infinite Sharpe.
            sharpe = 0.0
        else:
            days = max(_days_between(launch_date, today), 1)
            tpy = closed / max(days / 252.0, 0.001)
            sharpe = round((mean_r / std) * math.sqrt(tpy), 4)

    # Cumulative equity curve (starts at 1.0).
    equity_curve = [1.0]
    peak = 1.0
    max_dd = 0.0
    for p in pnls:
        equity_curve.append(round(equity_curve[-1] * (1.0 + p), 6))
        peak = max(peak, equity_curve[-1])
        if peak > 0:
            max_dd = max(max_dd, (peak - equity_curve[-1]) / peak)
    max_dd = round(max_dd, 4)

    # AUTHORITATIVE gate: the engine's versioned DTO (engine cohort only). The old
    # local 30-trade/win-rate/Sharpe/dd re-derivation over the mixed population is
    # retired — it claimed progress manual closes can never provide.
    gate = _load_gate_dto(db_path)
    sources = tuple(str(s) for s in (gate.get("cohort_sources") or ENGINE_SOURCES_FALLBACK))
    engine_rows = [r for r in rows if (r.get("source") or "") in sources]
    manual_rows = [r for r in rows if (r.get("source") or "") not in sources]
    if gate.get("fallback"):
        gate = {**gate, "closes": len(engine_rows)}  # honest progress, still fail-closed

    by_source: dict[str, int] = {}
    for r in rows:
        src = (r.get("source") or "unknown").strip() or "unknown"
        by_source[src] = by_source.get(src, 0) + 1

    engine = _cohort_stats(engine_rows)
    daily_analysis = _load_daily_analysis_book(daily_analysis_db_path)
    all_sources = _cohort_stats(rows)
    return {
        "by_source": by_source,
        "spy_return_period": _spy_return_since(launch_date),
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "total_pnl_pct": total_pnl_pct,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "equity_curve": equity_curve,
        "trades": rows,
        "open_positions": open_positions,
        "signal_ratings": signal_ratings,
        "excluded_ledger_rows": excluded_ledger_rows,
        "days_running": _days_between(launch_date, today),
        "days_target": DAYS_TARGET,
        "engine": engine,
        "manual": _cohort_stats(manual_rows),
        "daily_analysis": daily_analysis,
        "combined_automated": _combined_automated_context(engine, daily_analysis),
        "all_sources": all_sources,
        "gate": gate,
        "generated_at": today,
    }


def _svg_equity_curve(curve: list[float], w: int = 760, h: int = 180) -> str:
    """Inline SVG of an illustrative sequential trade-return index."""
    if len(curve) < 2:
        return (
            f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" '
            f'aria-label="No closed trades yet">'
            f'<rect width="{w}" height="{h}" fill="{_SURFACE}"/>'
            f'<line x1="0" y1="{h // 2}" x2="{w}" y2="{h // 2}" stroke="{_BORDER}" stroke-dasharray="4 4"/>'
            f'<text x="{w // 2}" y="{h // 2 - 8}" fill="{_DIM}" font-family="monospace" '
            f'font-size="13" text-anchor="middle">awaiting first closed trade</text></svg>'
        )
    lo, hi = min(curve), max(curve)
    rng = (hi - lo) or 1.0
    pad = 10
    pts = []
    for i, v in enumerate(curve):
        x = pad + i * (w - 2 * pad) / (len(curve) - 1)
        y = pad + (h - 2 * pad) * (1 - (v - lo) / rng)
        pts.append(f"{x:.1f},{y:.1f}")
    up = curve[-1] >= curve[0]
    stroke = _GREEN if up else _RED
    poly = " ".join(pts)
    area = f"{pad},{h - pad} " + poly + f" {w - pad},{h - pad}"
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" '
        f'aria-label="Illustrative sequential trade-return index">'
        f'<rect width="{w}" height="{h}" fill="{_SURFACE}"/>'
        f'<polygon points="{area}" fill="{stroke}" opacity="0.08"/>'
        f'<polyline points="{poly}" fill="none" stroke="{stroke}" stroke-width="2"/></svg>'
    )


def _gate_chip(label: str, g: dict, fmt: str) -> str:
    ok = g["pass"]
    color = _GREEN if ok else _YELLOW
    mark = "✓" if ok else "•"
    val = fmt.format(g["value"])
    req = fmt.format(g["required"])
    return (
        f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;'
        f'padding:14px 16px;flex:1;min-width:150px">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px">{label}</div>'
        f'<div style="color:{color};font-size:24px;font-weight:700;font-family:monospace;margin:4px 0">{val}</div>'
        f'<div style="color:{_DIM};font-size:11px">{mark} target {req}</div></div>'
    )


def _cohort_line(label: str, key: str, s: dict[str, Any], note: str) -> str:
    """One cohort's ledger stats. label/key/note are code literals; every stat is
    int/float-coerced. data-* attributes are the machine-checkable contract that
    engine and manual numbers are never mixed."""
    ci = s.get("win_rate_ci_95") or (0.0, 0.0)
    realized = float(s.get("realized_pnl_usd") or 0.0)
    gross_notional = float(s.get("gross_entry_notional_usd") or 0.0)
    pnl_to_gross_notional = float(
        s.get("pnl_to_gross_entry_notional", s.get("return_on_deployed_capital", 0.0)) or 0.0
    )
    sum_trade_returns = float(s.get("sum_trade_returns", s.get("total_pnl_pct", 0.0)) or 0.0)
    return (
        f'<div data-cohort="{key}" style="margin:4px 0;color:{_DIM}">'
        f'<b style="color:#dfe8f2">{label}</b> <span>({note})</span> — '
        f'closed <b class="num" style="color:#e8eef6" data-k="{key}-closed">{int(s["closed"])}</b> · '
        f'wins <b class="num" style="color:{_GREEN}" data-k="{key}-wins">{int(s["wins"])}</b> · '
        f'losses <b class="num" style="color:{_RED}" data-k="{key}-losses">{int(s["losses"])}</b> · '
        f'breakeven <b class="num" data-k="{key}-breakeven">{int(s["breakeven"])}</b> · '
        f'win rate <b class="num" style="color:#e8eef6" data-k="{key}-winrate">{float(s["win_rate"]) * 100:.0f}%</b> '
        f"(approx. Wilson 95% interval {float(ci[0]) * 100:.1f}%–{float(ci[1]) * 100:.1f}%; "
        f"not adjusted for overlapping/correlated trades) · "
        f'realized P&amp;L <b class="num" style="color:#e8eef6" data-k="{key}-realized-pnl">'
        f"{_fmt_signed_usd(realized)}</b> · gross entry notional "
        f'<b class="num" style="color:#e8eef6" data-k="{key}-gross-entry-notional">'
        f"{_fmt_usd(gross_notional)}</b> · realized P&amp;L / gross entry notional "
        f'<b class="num" style="color:#e8eef6" data-k="{key}-deployed-return">'
        f"{pnl_to_gross_notional * 100:+.1f}%</b> · sum of individual trade returns "
        f'<b class="num" style="color:#e8eef6" data-k="{key}-pnl">'
        f"{sum_trade_returns * 100:+.1f}%</b> (not a portfolio return)"
        f"</div>"
    )


def _fmt_usd(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_signed_usd(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


def _fmt_spy(v: Any) -> str:
    return f"{v * 100:+.1f}%" if isinstance(v, (int, float)) else "n/a"


def _fmt_sources(by_source: Any) -> str:
    if not by_source:
        return "—"
    return " · ".join(
        f"{_esc(k)} {int(v)}" for k, v in sorted(by_source.items(), key=lambda x: -x[1])
    )


def _automated_scope_html(data: dict[str, Any]) -> str:
    """Render the second automated book and combined arithmetic without gate mixing."""
    daily = data.get("daily_analysis")
    if not isinstance(daily, dict) or daily.get("status") != "AVAILABLE":
        status = daily.get("status") if isinstance(daily, dict) else "NOT_CONFIGURED"
        title = (
            "Second automated book unavailable"
            if status == "UNAVAILABLE"
            else "Second automated book not configured for this view"
        )
        return (
            f'<section data-book="daily-analysis" style="background:{_SURFACE};'
            f"border:1px solid {_YELLOW};border-left:4px solid {_YELLOW};border-radius:10px;"
            f'padding:12px 16px;margin-bottom:20px;font-size:13px">'
            f'<h2 style="font-size:14px;margin:0 0 6px;color:{_YELLOW}">{title}</h2>'
            f'<div style="color:{_DIM}">The separate daily-analysis ledger could not be '
            f"verified, so combined automated totals are unavailable. The engine headline "
            f"and autonomy gate remain engine-only and fail independently.</div></section>"
        )

    combined = data.get("combined_automated")
    if not isinstance(combined, dict) or not combined.get("available"):
        combined = {
            "closed": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "realized_pnl_usd": 0.0,
            "gross_entry_notional_usd": 0.0,
        }
    invalid = int(daily.get("excluded_invalid_rows") or 0)
    integrity_note = (
        f"{invalid} invalid in-scope row(s) preserved but excluded from these totals."
        if invalid
        else "No invalid in-scope rows were excluded from these totals."
    )
    as_of = _esc(daily.get("as_of") or "unknown")
    return (
        f'<section data-book="daily-analysis" style="background:{_SURFACE};'
        f"border:1px solid {_BORDER};border-radius:10px;padding:12px 16px;"
        f'margin-bottom:12px;font-size:13px">'
        f'<h2 style="font-size:14px;margin:0 0 6px">Second automated strategy — '
        f"daily-analysis (separate SQLite book)</h2>"
        f'<div style="color:{_DIM}">closed <b class="num" style="color:#e8eef6" '
        f'data-k="daily-analysis-closed">{int(daily["closed"])}</b> · wins '
        f'<b class="num" style="color:{_GREEN}">{int(daily["wins"])}</b> · losses '
        f'<b class="num" style="color:{_RED}">{int(daily["losses"])}</b> · breakeven '
        f'<b class="num">{int(daily["breakeven"])}</b> · win rate '
        f'<b class="num" style="color:#e8eef6">{float(daily["win_rate"]) * 100:.1f}%</b> · '
        f'realized P&amp;L <b class="num" style="color:#e8eef6" '
        f'data-k="daily-analysis-realized-pnl">'
        f"{_fmt_signed_usd(float(daily['realized_pnl_usd']))}</b> · gross entry notional "
        f'<b class="num" style="color:#e8eef6">'
        f"{_fmt_usd(float(daily['gross_entry_notional_usd']))}</b> · open positions "
        f'<b class="num" style="color:#e8eef6">{int(daily["open_positions"])}</b>.</div>'
        f'<div style="color:{_DIM};font-size:12px;margin-top:7px">This is a different '
        f"automated strategy and accounting ledger. It is not included in the headline or "
        f"autonomy gate above. As-of stored event: {as_of}. {_esc(integrity_note)}</div>"
        f"</section>"
        f'<section data-book="combined-automated" style="background:{_SURFACE};'
        f"border:1px solid {_BORDER};border-radius:10px;padding:12px 16px;"
        f'margin-bottom:20px;font-size:13px">'
        f'<h2 style="font-size:14px;margin:0 0 6px">Combined automated record — '
        f"arithmetic context only</h2>"
        f'<div style="color:{_DIM}">closed <b class="num" style="color:#e8eef6" '
        f'data-k="combined-automated-closed">{int(combined["closed"])}</b> · wins '
        f'<b class="num" style="color:{_GREEN}">{int(combined["wins"])}</b> · losses '
        f'<b class="num" style="color:{_RED}">{int(combined["losses"])}</b> · breakeven '
        f'<b class="num">{int(combined["breakeven"])}</b> · win rate '
        f'<b class="num" style="color:#e8eef6">{float(combined["win_rate"]) * 100:.1f}%</b> · '
        f'realized P&amp;L <b class="num" style="color:#e8eef6" '
        f'data-k="combined-automated-realized-pnl">'
        f"{_fmt_signed_usd(float(combined['realized_pnl_usd']))}</b> · gross entry notional "
        f'<b class="num" style="color:#e8eef6">'
        f"{_fmt_usd(float(combined['gross_entry_notional_usd']))}</b>.</div>"
        f'<div style="color:{_DIM};font-size:12px;margin-top:7px">This adds counts and '
        f"realized dollars across two separate simulated books. It is not a portfolio return, "
        f"TWR, MWR, exposure-matched comparison, or autonomy-gate population.</div></section>"
    )


def render_html(data: dict[str, Any]) -> str:
    """Render the self-contained proof-page HTML from a load_track_record() dict."""
    gate = data["gate"]
    closes = int(gate.get("closes") or 0)
    required = int(gate.get("required") or GATE_REQUIRED_FALLBACK)
    # Belt-and-braces fail-closed: a pass CLAIM needs both the engine gate's verdict
    # AND the full engine sample — a buggy/spoofed DTO can never render "gate met".
    ready = bool(gate.get("pass")) and closes >= required

    crit = gate.get("criteria") or {}
    chip_defs: list[tuple[str, dict, str]] = [
        (
            "Engine Closes",
            {"value": closes, "required": required, "pass": closes >= required},
            "{:.0f}",
        )
    ]
    for label, key, fmt in (
        ("Trade Expectancy", "expectancy", "{:+.4f}"),
        ("Profit Factor (gross $)", "profit_factor", "{:.2f}"),
        ("Trade-sequence DD", "max_drawdown", "{:.0%}"),
        ("Prob. Sharpe", "prob_sharpe", "{:.2f}"),
        ("Deflated Sharpe", "deflated_sharpe", "{:.2f}"),
    ):
        g = crit.get(key)
        if isinstance(g, dict):
            chip_defs.append((label, g, fmt))
    chips = "".join(_gate_chip(label, g, fmt) for label, g, fmt in chip_defs)

    if closes == 0:
        banner_txt = (
            f"Building track record — Day {data['days_running']} of {data['days_target']}. "
            f"0 of {required} engine closed trades recorded. Manual trades are tracked "
            "separately below and never count toward the autonomy gate."
        )
        banner_color = _YELLOW
    elif not ready:
        banner_txt = (
            f"Building track record — Day {data['days_running']} of {data['days_target']}. "
            f"{closes} of {required} engine closed trades. Manual trades are shown "
            "separately and never count toward the gate; metrics are provisional until "
            f"the {required}-close engine autonomy gate is met."
        )
        banner_color = _YELLOW
    else:
        banner_txt = (
            f"Autonomy gate met — {closes} engine closed trades, all readiness "
            f"criteria passed over {data['days_running']} days."
        )
        banner_color = _GREEN

    srcs = _esc(" + ".join(str(s) for s in (gate.get("cohort_sources") or ENGINE_SOURCES_FALLBACK)))
    # HEADLINE = defensible accounting for the strategy being evaluated. A sum of
    # overlapping, unequal-notional trade percentages is deliberately retained only
    # as a labelled diagnostic; it is never presented as portfolio performance.
    eng, man = data["engine"], data["manual"]
    eng_realized = float(eng.get("realized_pnl_usd") or 0.0)
    eng_gross_notional = float(eng.get("gross_entry_notional_usd") or 0.0)
    eng_pnl_to_gross_notional = float(
        eng.get("pnl_to_gross_entry_notional", eng.get("return_on_deployed_capital", 0.0)) or 0.0
    )
    eng_ci = _wilson_ci_95(int(eng["wins"]), int(eng["closed"]), precision=8)
    eng_cost_20 = float(eng.get("cost_sensitivity_20bp_usd") or 0.0)
    eng_cost_50 = float(eng.get("cost_sensitivity_50bp_usd") or 0.0)
    all_sources = data.get("all_sources")
    if not isinstance(all_sources, dict):
        # Recompute dollar accounting when a legacy caller supplies actual rows. If
        # it supplies only old aggregate fields, do not relabel its percentage-based
        # profit factor (or missing dollar fields) as gross-dollar accounting.
        legacy_rows = data.get("trades")
        reported_closed = (
            int(data.get("wins") or 0)
            + int(data.get("losses") or 0)
            + int(data.get("breakeven") or 0)
        )
        if isinstance(legacy_rows, list) and (legacy_rows or reported_closed == 0):
            all_sources = _cohort_stats(legacy_rows)
        else:
            all_sources = {"sum_trade_returns": data.get("total_pnl_pct", 0.0)}
    all_accounting_available = all(
        key in all_sources
        for key in (
            "realized_pnl_usd",
            "gross_entry_notional_usd",
            "profit_factor_usd",
        )
    )
    all_realized = float(all_sources.get("realized_pnl_usd") or 0.0)
    all_gross_notional = float(all_sources.get("gross_entry_notional_usd") or 0.0)
    all_pnl_to_gross_notional = float(
        all_sources.get(
            "pnl_to_gross_entry_notional",
            all_sources.get("return_on_deployed_capital", 0.0),
        )
        or 0.0
    )
    all_sum_trade_returns = float(
        all_sources.get("sum_trade_returns", data.get("total_pnl_pct", 0.0)) or 0.0
    )
    all_profit_factor = float(all_sources.get("profit_factor_usd") or 0.0)
    all_realized_text = (
        _fmt_signed_usd(all_realized) if all_accounting_available else "n/a (legacy input)"
    )
    all_gross_notional_text = (
        _fmt_usd(all_gross_notional) if all_accounting_available else "n/a (legacy input)"
    )
    all_pnl_to_gross_text = (
        f"{all_pnl_to_gross_notional * 100:+.1f}%"
        if all_accounting_available
        else "n/a (legacy input)"
    )
    all_profit_factor_text = (
        f"{all_profit_factor:.2f}" if all_accounting_available else "n/a (legacy input)"
    )
    excluded_ledger_rows = int(data.get("excluded_ledger_rows") or 0)
    eligibility_note = (
        f"{excluded_ledger_rows} legacy/ineligible ledger rows excluded under the same "
        "since-launch and positive-quantity rule as the autonomy gate."
        if excluded_ledger_rows
        else "No ledger rows were excluded by the since-launch, positive-quantity rule."
    )
    headline_html = (
        f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-left:4px solid '
        f"{_GREEN if eng_realized >= 0 else _RED};border-radius:10px;padding:14px 16px;"
        f'margin-bottom:16px">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;'
        f'margin-bottom:6px">Headline — autonomous engine cohort (the gated strategy)</div>'
        f'<div style="display:flex;gap:24px;flex-wrap:wrap;align-items:baseline">'
        f'<div><span style="font-size:24px;font-weight:700;color:'
        f'{_GREEN if eng_realized >= 0 else _RED}" class="num" '
        f'data-k="headline-realized-pnl-usd">{_fmt_signed_usd(eng_realized)}</span> '
        f'<span style="color:{_DIM};font-size:12px">realized paper P&amp;L</span></div>'
        f'<div><span style="font-size:20px;font-weight:700;color:#e8eef6" class="num" '
        f'data-k="headline-gross-entry-notional">{_fmt_usd(eng_gross_notional)}</span> '
        f'<span style="color:{_DIM};font-size:12px">gross entry notional</span></div>'
        f'<div><span style="font-size:20px;font-weight:700;color:#e8eef6" class="num" '
        f'data-k="headline-pnl-to-gross-entry-notional">'
        f"{eng_pnl_to_gross_notional * 100:+.1f}%</span> "
        f'<span style="color:{_DIM};font-size:12px">P&amp;L / gross entry notional</span></div>'
        f"</div>"
        f'<div style="color:{_DIM};font-size:12px;margin-top:8px">'
        f"{int(eng['closed'])} closed paper trades · win rate "
        f"{float(eng['win_rate']) * 100:.0f}% · Approx. Wilson 95% interval "
        f"{float(eng_ci[0]) * 100:.1f}%–{float(eng_ci[1]) * 100:.1f}% "
        f"(not adjusted for overlapping/correlated trades) · "
        f"SPY calendar context {_fmt_spy(data.get('spy_return_period'))} "
        f"(not exposure-matched)</div>"
        f'<div style="color:{_DIM};font-size:12px;margin-top:8px;line-height:1.55">'
        f"P&amp;L / gross entry notional is a capital-efficiency diagnostic, not a portfolio "
        f"return, TWR, or MWR. "
        f"Tracker fills use a slippage model, but its historical application and unmodeled "
        f"costs are not fully uniform; a 20–50 bp additional execution-cost stress would "
        f'reduce results by <b style="color:#e8eef6">'
        f"{_fmt_usd(eng_cost_20)}–{_fmt_usd(eng_cost_50)}</b>. The SPY figure is calendar "
        f"context only and is not an outperformance claim.</div>"
        f'<div style="color:{_DIM};font-size:12px;margin-top:8px;line-height:1.55">'
        f"The all-sources section below also includes {int(man['closed'])} hand-rated MANUAL "
        f"trades. Those trades are not produced by the engine, are not reproducible by a "
        f"subscriber, and never count toward the autonomy gate.</div>"
        f"</div>"
    )
    cohorts_html = (
        headline_html
        + f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;'
        f'padding:12px 16px;margin-bottom:20px;font-size:13px">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;'
        f'letter-spacing:.5px;margin-bottom:6px">Cohorts — the autonomy gate measures '
        f"the engine cohort ONLY</div>"
        + _cohort_line(
            "Engine cohort",
            "engine",
            data["engine"],
            f"sources: {srcs} — the only autonomy-gate population",
        )
        + _cohort_line(
            "Manual cohort",
            "manual",
            data["manual"],
            "rated hand trades — informational, never gates",
        )
        + "</div>"
    )
    automated_scope_html = _automated_scope_html(data)

    def ts(v: Any) -> str:
        return _esc((str(v or "")[:16].replace("T", " ")) or "—")

    rows_html = ""
    for t in reversed(data["trades"]):
        oc = t["outcome"]
        c = _GREEN if oc == "WIN" else (_RED if oc == "LOSS" else _DIM)
        rows_html += (
            f'<tr style="border-bottom:1px solid {_BORDER}">'
            f'<td style="padding:8px 12px">{_esc(t["symbol"])}</td>'
            f'<td style="padding:8px 12px;color:{_DIM}">{_esc(t.get("source", ""))}</td>'
            f'<td style="padding:8px 12px;color:{_DIM}">{_esc(t.get("action", ""))}</td>'
            f'<td style="padding:8px 12px;text-align:right">{float(t.get("qty") or 0):g}</td>'
            f'<td style="padding:8px 12px;color:{_DIM};white-space:nowrap">{ts(t.get("entry_at"))}</td>'
            f'<td style="padding:8px 12px;color:{_DIM};white-space:nowrap">{ts(t.get("exit_at"))}</td>'
            f'<td style="padding:8px 12px;text-align:right">${float(t["entry_price"]):.2f}</td>'
            f'<td style="padding:8px 12px;text-align:right">${float(t["exit_price"]):.2f}</td>'
            f'<td style="padding:8px 12px;text-align:right;color:{c}">'
            f"{_fmt_signed_usd(float(t.get('pnl_usd') or 0))}</td>"
            f'<td style="padding:8px 12px;text-align:right;color:{c}">{float(t["pnl_pct"]) * 100:+.1f}%</td>'
            f'<td style="padding:8px 12px;text-align:right;color:{c};font-weight:600">{_esc(oc)}</td>'
            f'<td style="padding:8px 12px;text-align:right;color:{_DIM}">{float(t.get("hold_days") or 0):.1f}d</td>'
            f"</tr>"
        )
    if not rows_html:
        rows_html = (
            f'<tr><td colspan="12" style="padding:24px;text-align:center;color:{_DIM}">'
            f"No closed trades yet — {data['open_positions']} position(s) open, "
            f"scanning for stop/target exits.</td></tr>"
        )

    disclaimer = (
        "NOT FINANCIAL ADVICE — FOR INFORMATIONAL AND EDUCATIONAL PURPOSES ONLY. "
        "GESH Financial is a self-hosted quantitative research tool, not a registered "
        "investment adviser, and provides no personalized investment advice and owes no "
        "fiduciary duty. All results shown are HYPOTHETICAL / SIMULATED paper trades, not "
        "real money. Simulated results have inherent limitations: fills are not actual "
        "executions, market impact and liquidity cannot be reproduced fully, and actual "
        "results may differ sharply. PAST PERFORMANCE — including simulated performance — "
        "does not guarantee future results. Trading involves substantial risk of loss. "
        "You are solely responsible for your own decisions."
    )

    # B6 compliance additions (insert-only: no figure above or below is changed).
    # Owner decision 2026-10-04: educational content, no counsel gate. The page must still
    # never claim legal review (tests/test_seo_og_and_track_record.py).
    compliance_html = (
        f'<div role="note" data-k="hypothetical-banner" style="background:#1a1405;'
        f"border:1px solid #6b5410;border-left:4px solid #f0b429;border-radius:8px;"
        f'padding:12px 16px;margin-bottom:16px;color:#e8d9b0;font-size:14px">'
        f"<b>HYPOTHETICAL / SIMULATED — NOT LIVE TRADING.</b> Every figure on this page comes "
        f"from paper trades recorded in a tracker ledger, not from real money. "
        f"<b>Past performance is not indicative of future results.</b></div>"
    )
    methodology_extra_html = (
        f'<ul id="methodology-limits" style="color:{_DIM};font-size:12px;line-height:1.6;'
        f'margin:0 0 24px;padding-left:20px">'
        f"<li><b>Universe:</b> every paper trade the tracker logged, labelled by source "
        f"(consensus engine or manual). No fixed index or screening universe is stated.</li>"
        f"<li><b>Date range:</b> execution clock starts {_esc(LAUNCH_DATE)}; ledger generated "
        f"{_esc(str(data['generated_at']))}.</li>"
        f"<li><b>Costs and slippage:</b> tracker fills model 10 bp entry slippage and, since "
        f"2026-07-02, 10 bp exit slippage. Commissions, fees and taxes are not stated on this "
        f"page.</li>"
        f"<li><b>Gross vs. net:</b> profit factor and P&amp;L above are gross-dollar figures. "
        f"No net-of-cost figure is published; the 20–50 bp stress range in the headline shows "
        f"sensitivity only.</li>"
        f"<li><b>Not audited:</b> the ledger is a mutable database and has not been "
        f"independently verified.</li></ul>"
    )

    seo_head = seo_pages.head_tags(
        title="Signal Track Record (Hypothetical) | GESH Financial",
        description=(
            "The public GESH signal track record: logged closes, expectancy, profit factor. "
            "Hypothetical/simulated results — methodology and limitations disclosed."
        ),
        path="/track-record",
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{seo_head}
<style>
 body{{margin:0;background:{_BG};color:#e8eef6;font-family:-apple-system,Segoe UI,Roboto,sans-serif}}
 .wrap{{max-width:880px;margin:0 auto;padding:32px 20px 64px}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:{_DIM};font-size:13px;margin-bottom:20px}}
 .num{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
 table{{width:100%;border-collapse:collapse;font-size:13px;font-family:ui-monospace,monospace}}
 th{{text-align:left;color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;padding:8px 12px;border-bottom:1px solid {_BORDER}}}
 th.r{{text-align:right}}
 a{{color:{_CYAN}}}
</style></head>
<body>
<div class="wrap">
 <h1 style="color:{_CYAN}">GESH Financial — Signal Accuracy Track Record</h1>
 <div class="sub">Paper-trade tracker ledger (sources labeled per trade: consensus = 11-node
   engine, manual = rated entries) · generated {_esc(data["generated_at"])}. Tracker timestamps
   are shown as stored; this mutable SQLite ledger is not independently audited.</div>

 {compliance_html}

 <div style="background:{_SURFACE};border:1px solid {banner_color};border-left:4px solid {banner_color};
   border-radius:8px;padding:14px 16px;margin-bottom:20px;color:#dfe8f2;font-size:14px">{banner_txt}</div>

 <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px">{chips}</div>

 {cohorts_html}

 {automated_scope_html}

 <div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;padding:16px;margin-bottom:8px">
   <div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">
     Illustrative sequential trade-return index — ALL SOURCES BLENDED (engine + manual)</div>
   {_svg_equity_curve(data["equity_curve"])}
   <div style="color:{_DIM};font-size:11px;margin-top:8px;line-height:1.5">Each trade return is
     applied sequentially in close-time order only to illustrate the trade path. Because trades
     overlap and use different notionals, this is not portfolio equity, TWR, or MWR. It includes
     hand-rated manual trades; the autonomy gate uses the ENGINE cohort only.</div>
 </div>
 <div style="display:flex;gap:24px;flex-wrap:wrap;color:{_DIM};font-size:12px;margin-bottom:24px">
   <span><b style="color:#dfe8f2">All sources BLENDED (engine + manual — not the gated strategy):</b></span>
   <span>Wins: <b style="color:{_GREEN}" class="num">{data["wins"]}</b></span>
   <span>Losses: <b style="color:{_RED}" class="num">{data["losses"]}</b></span>
   <span>Breakeven: <b class="num" style="color:{_DIM}">{data["breakeven"]}</b></span>
   <span>Profit factor (gross $): <b class="num" style="color:#e8eef6">{all_profit_factor_text}</b></span>
   <span>Avg win: <b class="num" style="color:{_GREEN}">{data["avg_win_pct"] * 100:+.1f}%</b></span>
   <span>Avg loss: <b class="num" style="color:{_RED}">{data["avg_loss_pct"] * 100:+.1f}%</b></span>
   <span>Realized P&amp;L: <b class="num" style="color:#e8eef6">{all_realized_text}</b></span>
   <span>Gross entry notional: <b class="num" style="color:#e8eef6">{all_gross_notional_text}</b></span>
   <span>Realized P&amp;L / gross entry notional (not portfolio return):
     <b class="num" style="color:#e8eef6">{all_pnl_to_gross_text}</b></span>
   <span>Sum of individual trade returns (not a portfolio return):
     <b class="num" style="color:#e8eef6" data-k="all-sum-trade-returns">{all_sum_trade_returns * 100:+.1f}%</b></span>
   <span>SPY calendar context (not exposure-matched): <b class="num" style="color:#e8eef6">{_fmt_spy(data.get("spy_return_period"))}</b></span>
   <span>By source: <b class="num" style="color:#e8eef6">{_fmt_sources(data.get("by_source"))}</b></span>
   <span>Signal ratings: <b class="num" style="color:#e8eef6">{data["signal_ratings"]}</b></span>
 </div>

 <h2 id="methodology" style="font-size:15px;margin:0 0 8px">Methodology and limitations</h2>
 <div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:8px;padding:12px 16px;
   margin-bottom:12px;color:{_DIM};font-size:12px;line-height:1.6">
   <b style="color:#dfe8f2">Tracker methodology and limits.</b> The current workflow creates a
   paper position at entry and an outcome row when that position closes. The complete
   gate-eligible tracker outcome ledger is shown, including every gate-eligible losing trade.
   The separate daily-analysis book is summarized above and is not part of this row-level
   tracker table. Excluded legacy rows remain in the database for audit history. Entry and exit
   timestamps are UTC fields from the tracker; this page cannot independently attest when a row
   was created or prove the absence of later database edits. Win rate uses the conservative
   policy (breakevens count in the denominator). The autonomy gate is evaluated by the trading
   engine on the ENGINE cohort only — manual trades are reported separately and never count.
   {_esc(eligibility_note)}
   Gate profit factor uses gross-dollar P&amp;L. Gate expectancy, Sharpe, and drawdown are
   trade-sequence diagnostics, not portfolio-return statistics. Current tracker code models
   10 bp entry slippage and, since 2026-07-02, 10 bp exit slippage; the headline applies an
   additional uniform execution-cost stress because the historical sample is not fully uniform.
 </div>
 {methodology_extra_html}

 <h2 style="font-size:15px;margin:0 0 8px">Complete gate-eligible closed-trade ledger</h2>
 <table><thead><tr>
   <th>Symbol</th><th>Source</th><th>Side</th><th class="r">Qty</th>
   <th>Signal (UTC)</th><th>Closed (UTC)</th>
   <th class="r">Entry</th><th class="r">Exit</th>
   <th class="r">P&amp;L $</th><th class="r">Return</th>
   <th class="r">Outcome</th><th class="r">Hold</th>
 </tr></thead><tbody>{rows_html}</tbody></table>

 <p style="margin-top:32px;padding:14px 16px;background:{_SURFACE};border:1px solid {_BORDER};
   border-radius:8px;color:{_DIM};font-size:11px;line-height:1.6">{disclaimer}</p>
 <p style="color:{_DIM};font-size:11px"><a href="/terms">Terms of Service</a> ·
   <a href="/disclaimer">Full Disclaimer</a></p>
</div></body></html>"""


def generate(db_path: str | None = None, out_path: str = "track_record.html") -> str:
    data = load_track_record(db_path)
    html = render_html(data)
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate the GESH signal-accuracy proof page")
    ap.add_argument("--db", default=None, help="tracker SQLite DB (default: trading engine DB)")
    ap.add_argument("--out", default="track_record.html", help="output HTML path")
    args = ap.parse_args()
    out = generate(args.db, args.out)
    d = load_track_record(args.db)
    print(
        f"Wrote {out} — {d['closed_trades']} closed trades "
        f"(engine {d['engine']['closed']} / manual {d['manual']['closed']}), "
        f"gate v{d['gate']['version']} pass={d['gate']['pass']} "
        f"({d['gate']['closes']}/{d['gate']['required']} engine closes)"
    )
