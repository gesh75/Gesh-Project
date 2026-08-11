"""Per-user equity history and the customer Equity Curve Gate.

The owner console's ECG panel is driven by `equity_snapshots` in
gesh_financial.db — a table with NO user column, so `get_equity_history(symbol)`
returns the OWNER's curve to whoever asks. That is why customers have had no ECG
(see tests/test_customer_owner_isolation.py, which pins the requirement).

This module supplies the missing input. Its ONLY data source is
`customer_positions` valued at public closes, stored per user in
`customer_equity_snapshots` (gesh_auth.db). It never reads the owner table, and
the recorder refuses to write rows for the owner account: the owner's curve
already lives in the owner-only table, and a second copy inside the customer
database is precisely the leak the isolation contract forbids.

Two correctness rules are inherited from `gesh_core.ecg` and this repo's history
of breaking them:

  * `_loss_streak` takes per-snapshot PERCENT CHANGES, never raw equity levels.
    Levels are always positive, so a level-fed streak pins to 0 and the streak
    circuit breaker silently never fires. `compute_ecg` derives the changes from
    the equity series itself, which is why `percent_change` (the vs-cost LEVEL
    column that caused the original bug) is not even stored here.
  * History must be ordered oldest -> newest before the running-peak math.
    `compute_ecg` reverses what it is handed, so the history function must return
    NEWEST FIRST — the same contract as `db.get_equity_history`.

Honesty about coverage
----------------------
`gesh_core.ecg` reports a holding with fewer than MIN_SNAPSHOTS (2) observations
as state "HEALTHY" carrying an "Insufficient history" *message*. A customer panel
renders the STATE, so a two-day-old account would show a confident green pill.
Here an under-covered holding gets its own state, ``INSUFFICIENT_HISTORY``, with
`drawdown_pct` / `loss_streak` set to None rather than 0.0 — a zero reads as a
real measurement of "no drawdown".

``MIN_SNAPSHOTS_FOR_VERDICT`` is 7 because that is the first window in which
every verdict the panel can display is actually reachable: SUSPENDED-by-streak
needs LOSS_STREAK_SUSPENDED (6) consecutive negative changes, and n changes need
n+1 snapshots. Below 7 a "HEALTHY" would be structurally incapable of the worst
finding it claims to rule out.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from typing import Callable

import auth_users
import ecg

logger = logging.getLogger(__name__)

# Snapshots needed before a holding gets a real verdict. See the module docstring
# for the derivation from ecg.LOSS_STREAK_SUSPENDED.
MIN_SNAPSHOTS_FOR_VERDICT = ecg.LOSS_STREAK_SUSPENDED + 1

# Observations to read back per symbol. Matches the owner ECG's window.
HISTORY_DAYS = 30

# A scheduler may tick far more often than a trading session. ECG counts
# snapshots AS sessions, so over-sampling shortens what a "6 consecutive losing
# snapshots" verdict actually means. Six hours keeps an intraday scheduler to at
# most one row per session while tolerating a missed tick.
DEFAULT_MIN_INTERVAL_SECONDS = 6 * 3600

INSUFFICIENT = "INSUFFICIENT_HISTORY"

# Worst-first: the portfolio gate is the worst verdict among holdings that HAVE a
# verdict. A holding still accumulating is neither a healthy vote nor a mask.
_SEVERITY = ("SUSPENDED", "COOLING", "HEALTHY")


def _default_price_fn(symbol: str) -> float | None:
    """Public last close, through the shared per-ticker cache. Imported lazily so
    this module stays importable (and unit-testable) without the pricing stack."""
    import price_cache

    return price_cache._cached_close(symbol)


# ─── recording (what a scheduler calls) ──────────────────────────────────────

def record_snapshot(
    email: str,
    *,
    price_fn: Callable[[str], float | None] | None = None,
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    at: str | None = None,
) -> dict:
    """Value one customer's book at public closes and store the result.

    This is the callable a scheduler drives; nothing here installs or modifies a
    schedule. Returns a report rather than raising, so a sweep over many users
    can keep going:

        {"email", "recorded", "skipped", "reason", "at", "unpriced"}

    `min_interval_seconds` makes an over-frequent tick a cheap no-op (pass 0 to
    force). `at` pins the timestamp for a deterministic backfill.
    """
    report = {"email": email, "recorded": 0, "skipped": False, "reason": None,
              "at": None, "unpriced": []}

    # The owner is excluded by construction, not by filtering downstream: their
    # equity curve is already persisted in the owner-only `equity_snapshots`
    # table, and duplicating it into the customer database would put owner data
    # one join away from a customer response.
    if auth_users.is_owner(email):
        report.update(skipped=True, reason="owner_excluded")
        return report

    positions = auth_users.get_positions(email)
    if not positions:
        report.update(skipped=True, reason="empty_book")
        return report

    if min_interval_seconds > 0:
        last = auth_users.last_equity_snapshot_at(email)
        if last and _seconds_since(last) < min_interval_seconds:
            report.update(skipped=True, reason="within_interval")
            return report

    fetch = price_fn or _default_price_fn
    rows, unpriced = [], []
    for position in positions:
        symbol = str(position.get("symbol") or "").strip().upper()
        try:
            price = fetch(symbol)
            shares = float(position.get("shares") or 0)
        except (TypeError, ValueError) as exc:
            logger.debug("equity snapshot: unusable position %s: %s", symbol, exc)
            unpriced.append(symbol)
            continue
        if price is None or price <= 0 or shares <= 0:
            unpriced.append(symbol)
            continue
        rows.append({"symbol": symbol, "equity": shares * float(price),
                     "current_price": float(price)})

    stamp = at or datetime.now(timezone.utc).isoformat()
    report["recorded"] = auth_users.record_equity_snapshots(email, rows, at=stamp)
    report["unpriced"] = unpriced
    report["at"] = stamp if report["recorded"] else None
    return report


def record_snapshots_for_all_customers(
    *,
    price_fn: Callable[[str], float | None] | None = None,
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
) -> dict:
    """Sweep every active customer holding a book. The scheduler entrypoint.

    One user's failure never aborts the sweep — a single upstream hiccup must not
    cost everybody else their session. Returns
    {"users", "recorded", "skipped", "failed", "reports"}.
    """
    summary = {"users": 0, "recorded": 0, "skipped": 0, "failed": 0,
               "reports": []}
    for email in auth_users.customers_with_positions():
        if auth_users.is_owner(email):
            continue
        summary["users"] += 1
        try:
            report = record_snapshot(
                email, price_fn=price_fn, min_interval_seconds=min_interval_seconds
            )
        except Exception as exc:  # noqa: BLE001 — one tenant must not end the sweep
            logger.warning("equity snapshot failed for a customer: %s", exc)
            summary["failed"] += 1
            continue
        if not report["recorded"]:
            summary["skipped"] += 1
            if report["unpriced"] and not report["reason"]:
                summary["failed"] += 1
        summary["recorded"] += report["recorded"]
        summary["reports"].append(report)
    return summary


def _seconds_since(iso_timestamp: str) -> float:
    """Age of an ISO-8601 stamp in seconds. A stamp we cannot parse is treated as
    infinitely old — a broken row must not wedge the recorder shut forever."""
    try:
        parsed = datetime.fromisoformat(str(iso_timestamp))
    except (TypeError, ValueError):
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


# ─── the customer ECG ────────────────────────────────────────────────────────

def compute_book_ecg(email: str, *, days: int = HISTORY_DAYS) -> dict:
    """Equity Curve Gate over ONE customer's book, from that customer's history.

    Reads `customer_positions` and `customer_equity_snapshots` for this user and
    nothing else — no owner table, no live quotes, no network. Returns the
    `ecg.compute_ecg` shape plus the coverage block the panel needs to be honest:

        {states, portfolio_gate, portfolio_message, snapshots_required,
         history_days, coverage: {ready, accumulating, total}, accumulating: [..]}
    """
    positions = auth_users.get_positions(email)
    histories = {
        p["symbol"]: auth_users.get_user_equity_history(email, p["symbol"], days=days)
        for p in positions
    }

    def history_fn(symbol: str) -> list[dict]:
        # Newest-first, exactly as compute_ecg expects: it reverses this before
        # the running-peak math.
        return histories.get(symbol, [])

    holdings = [
        {
            "symbol": p["symbol"],
            # Latest stored equity, so the under-covered branch of compute_ecg
            # still shows the customer a real number instead of a zero.
            "equity": (histories.get(p["symbol"]) or [{}])[0].get("equity", 0) or 0,
        }
        for p in positions
    ]

    computed = ecg.compute_ecg(holdings, history_fn)

    states, accumulating = [], []
    for state in computed.get("states", []):
        symbol = state.get("symbol")
        available = len(histories.get(symbol, []))
        if available >= MIN_SNAPSHOTS_FOR_VERDICT:
            states.append({**state, "snapshots_required": MIN_SNAPSHOTS_FOR_VERDICT,
                           "snapshots_needed": 0})
            continue
        # gesh_core reports thin coverage as HEALTHY-with-a-message. A panel
        # renders the state, so say it in the state. A corrupt-history SUSPENDED
        # is NOT downgraded: bad data is a finding, not missing data.
        if state.get("state") == "SUSPENDED":
            states.append({**state, "snapshots_required": MIN_SNAPSHOTS_FOR_VERDICT,
                           "snapshots_needed": max(0, MIN_SNAPSHOTS_FOR_VERDICT - available)})
            continue
        needed = MIN_SNAPSHOTS_FOR_VERDICT - available
        accumulating.append(symbol)
        states.append({
            "symbol": symbol,
            "state": INSUFFICIENT,
            # None, never 0.0: a zero here reads as a measured "no drawdown".
            "drawdown_pct": None,
            "loss_streak": None,
            "snapshots_available": available,
            "snapshots_required": MIN_SNAPSHOTS_FOR_VERDICT,
            "snapshots_needed": needed,
            "equity_history": [
                round(float(r["equity"]), 2) for r in reversed(histories.get(symbol, []))
            ],
            "message": (
                f"Not enough history yet — {available} of {MIN_SNAPSHOTS_FOR_VERDICT} "
                f"snapshots. {needed} more before {symbol} gets an equity-curve verdict."
            ),
        })

    ready = [s for s in states if s["state"] != INSUFFICIENT]
    gate, message = _portfolio_gate(ready, accumulating, total=len(states))
    return {
        "states": states,
        "portfolio_gate": gate,
        "portfolio_message": message,
        "snapshots_required": MIN_SNAPSHOTS_FOR_VERDICT,
        "history_days": days,
        "coverage": {
            "ready": len(ready),
            "accumulating": len(accumulating),
            "total": len(states),
        },
        "accumulating": accumulating,
    }


def _portfolio_gate(
    ready: list[dict], accumulating: list[str], *, total: int
) -> tuple[str, str]:
    """Worst READY holding wins; a still-accumulating name is never a healthy vote."""
    pending = (
        f" {len(accumulating)} still accumulating "
        f"({MIN_SNAPSHOTS_FOR_VERDICT} snapshots needed)."
        if accumulating else ""
    )
    if not total:
        return INSUFFICIENT, (
            "No positions on your book yet — add holdings and the equity curve "
            "starts accumulating."
        )
    if not ready:
        return INSUFFICIENT, (
            f"Equity-curve gate is still accumulating history: 0 of {total} holdings "
            f"have the {MIN_SNAPSHOTS_FOR_VERDICT} snapshots needed for a verdict."
        )
    states = [s["state"] for s in ready]
    for level in _SEVERITY:
        if level in states:
            count = states.count(level)
            if level == "SUSPENDED":
                return level, f"{count} holding(s) SUSPENDED. Review immediately.{pending}"
            if level == "COOLING":
                return level, f"{count} holding(s) cooling. Monitor closely.{pending}"
            return level, (
                f"All {count} holding(s) with enough history are within healthy "
                f"equity-curve bounds.{pending}"
            )
    return INSUFFICIENT, f"No equity-curve verdict available yet.{pending}"


# ─── CLI (the scheduled recorder entrypoint) ─────────────────────────────────
#
# `record_snapshots_for_all_customers` is the scheduler entrypoint, and until now
# it had NO caller anywhere in the repo — which is why /api/mybook/ecg reports
# "0 of 7 snapshots" forever: nothing ever writes one. This is that caller.
# Shape mirrors billing.py's `sweep-onboarding` CLI: a positional verb, a
# `__main__` block that loads .env and configures logging, and a one-line
# key=value summary a log grep can parse.
#
# The verb is required (a bare `python customer_equity_history.py` must not write
# to a customer's book by accident).

RECORD_COMMAND = "record-snapshots"

EXIT_OK = 0
EXIT_FAILED = 1


def _cli_summary(summary: dict, *, min_interval_seconds: float) -> dict[str, float | int]:
    """Aggregate counters only — NEVER an email, NEVER a symbol.

    `record_snapshots_for_all_customers` returns a `reports` list carrying one
    customer email (and their unpriced tickers) per entry. This process's stdout
    lands in a plain-text launchd log, so echoing that list would write the
    subscriber roster and pieces of their books to /tmp — the same data the
    isolation contract exists to keep out of shared surfaces. The module's own
    failure path already logs "failed for a customer" without the address; the
    CLI keeps that discipline and reports counts.

    `unpriced_symbols` is DERIVED here (summed over `reports`); the sweep itself
    returns only the four counters plus the reports list.
    """
    reports = summary.get("reports") or []
    return {
        "users": int(summary.get("users", 0)),
        "recorded": int(summary.get("recorded", 0)),
        "skipped": int(summary.get("skipped", 0)),
        "failed": int(summary.get("failed", 0)),
        "unpriced_symbols": sum(len(r.get("unpriced") or []) for r in reports),
        "min_interval_seconds": min_interval_seconds,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="customer_equity_history.py",
        description="Record one equity observation per customer holding (per-user, "
                    "owner excluded by construction).",
    )
    parser.add_argument(
        "command", choices=[RECORD_COMMAND],
        help="record-snapshots: sweep every active customer with a book.",
    )
    parser.add_argument(
        "--min-interval", type=float, default=DEFAULT_MIN_INTERVAL_SECONDS,
        metavar="SECONDS",
        help="Skip a user whose newest observation is younger than this "
             f"(default {DEFAULT_MIN_INTERVAL_SECONDS:g}). 0 forces a write — use only "
             "for a backfill: ECG counts snapshots AS sessions, so over-sampling "
             "shortens what a loss-streak verdict means.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit the summary as a JSON object instead of key=value.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero when any individual user failed. Off by default: the "
             "sweep is built so one tenant's bad ticker never costs the others "
             "their session, and a launchd job that goes red on that gets ignored.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    price_fn: Callable[[str], float | None] | None = None,
) -> int:
    """Run the sweep and print a machine-readable summary. Returns the exit code.

    Exit codes:
      0 — the sweep ran. Includes the all-skipped no-op (an over-frequent tick is
          the designed steady state, not a fault) and a PARTIAL failure.
      1 — the sweep could not run at all (an exception escaped), or --strict was
          passed and at least one user failed.
      2 — argparse usage error (argparse's own convention).

    `price_fn` is an injection point for tests and for a backfill driven from a
    stored price series; production leaves it None and the recorder uses the
    shared per-ticker close cache.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.min_interval < 0:
        parser.error("--min-interval must be >= 0")   # argparse exits 2

    try:
        summary = record_snapshots_for_all_customers(
            price_fn=price_fn, min_interval_seconds=args.min_interval
        )
    except Exception as exc:  # noqa: BLE001 — the CLI boundary reports, never traces
        # The PRINTED summary carries the exception TYPE only, so the machine-readable
        # line stays identifier-free like every other one. The full text still goes to
        # the logger, matching what record_snapshots_for_all_customers already does for
        # a per-user failure — an underlying store/config error is not something this
        # layer can promise is free of identifiers, and swallowing it would leave a
        # scheduled job undiagnosable.
        logger.error("equity snapshot sweep failed: %s", exc)
        print(f"equity snapshot sweep: error={type(exc).__name__}")
        return EXIT_FAILED

    payload = _cli_summary(summary, min_interval_seconds=args.min_interval)
    if args.as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("equity snapshot sweep: " + " ".join(f"{k}={v:g}" if isinstance(v, float)
                                                   else f"{k}={v}"
                                                   for k, v in payload.items()))
    if args.strict and payload["failed"]:
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
