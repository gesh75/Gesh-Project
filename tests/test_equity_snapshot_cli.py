"""The customer equity-snapshot recorder's CLI — the piece that was missing.

`customer_equity_history.record_snapshots_for_all_customers()` shipped with no
caller anywhere in the repo, so the customer Book Health panel read 0 snapshots
forever and every holding sat at INSUFFICIENT_HISTORY. These tests lock the
entrypoint that finally drives it, plus the three properties that make it safe to
put on a schedule:

  * THE OWNER IS NEVER RECORDED. Their curve already lives in the owner-only
    `equity_snapshots` table (symbol-keyed, no user column). A second copy inside
    gesh_auth.db would put owner data one join away from a customer response —
    exactly what tests/test_customer_owner_isolation.py forbids. Note the owner
    DOES appear in `customers_with_positions()`; the exclusion happens inside
    `record_snapshot`, so the sweep is where it has to be proven.
  * AN OVER-FREQUENT TICK IS A NO-OP. ECG counts snapshots AS trading sessions,
    so a scheduler that double-writes silently shortens what a "6 consecutive
    losing sessions" verdict means. The interval guard must hold across separate
    CLI invocations, not just within one.
  * ONE USER'S FAILURE NEVER ENDS THE SWEEP. A single upstream pricing hiccup
    must not cost every other tenant their session.

The exit-code contract is asserted too: a PARTIAL failure exits 0 by design (a
launchd job that goes red on one bad ticker gets ignored) and only --strict turns
it into a non-zero.

is_owner is exercised for real here rather than stubbed: GESH_AUTH_ENABLED is
cleared and GESH_OWNER_EMAIL is pointed at the test owner, which drives
auth_users.is_owner down its genuine auth-disabled branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

OWNER = "owner@example.test"
CUST_A = "aaa@example.test"   # sorts FIRST — the failing user in the resilience test
CUST_B = "bbb@example.test"

# Deliberately disjoint from the owner's real book (AMD BAC BKNG BLK COF DELL
# HOOD JD JMKE MPC MU NVDA PANW REGN RTX SCHW): any owner ticker appearing in a
# customer row would be unambiguous contamination.
SYM_A = "ZZZA"
SYM_B = "ZZZB"
OWNER_SYM = "NVDA"

OWNER_SYMBOLS = {
    "AMD", "BAC", "BKNG", "BLK", "COF", "DELL", "HOOD", "JD",
    "JMKE", "MPC", "MU", "NVDA", "PANW", "REGN", "RTX", "SCHW",
}

PRICES = {SYM_A: 10.0, SYM_B: 20.0, OWNER_SYM: 100.0}


def _price_fn(symbol: str) -> float | None:
    return PRICES.get(symbol)


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated auth DB with an owner and two customers, all holding a book."""
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    # Real is_owner, auth-disabled branch — no stub standing in for the guard.
    monkeypatch.setenv("GESH_AUTH_ENABLED", "")
    monkeypatch.setenv("GESH_OWNER_EMAIL", OWNER)
    _au.init_auth_db()
    for email in (CUST_A, CUST_B, OWNER):
        _au.create_user(email, "test-password-1234")
    _au.set_position(CUST_A, SYM_A, 3)
    _au.set_position(CUST_B, SYM_B, 5)
    _au.set_position(OWNER, OWNER_SYM, 10)
    assert _au.is_owner(OWNER) and not _au.is_owner(CUST_A)
    return _au


@pytest.fixture
def cli(au):
    import customer_equity_history as _ceh

    return _ceh


def _parse_kv(line: str) -> dict[str, str]:
    """`equity snapshot sweep: users=2 recorded=2 ...` -> {"users": "2", ...}."""
    _, _, tail = line.partition(":")
    return dict(pair.split("=", 1) for pair in tail.split())


# ─── the CLI runs at all ─────────────────────────────────────────────────────

def test_cli_records_and_prints_machine_readable_summary(cli, au, capsys):
    code = cli.main([cli.RECORD_COMMAND, "--min-interval", "0"], price_fn=_price_fn)
    out = capsys.readouterr().out.strip()

    assert code == cli.EXIT_OK
    fields = _parse_kv(out)
    # Two CUSTOMERS swept, one row each; the owner is not among the users.
    assert fields["users"] == "2"
    assert fields["recorded"] == "2"
    assert fields["failed"] == "0"
    assert fields["unpriced_symbols"] == "0"

    # The rows actually landed, per user, at the real prices.
    hist_a = au.get_user_equity_history(CUST_A, SYM_A, days=30)
    assert len(hist_a) == 1
    assert hist_a[0]["equity"] == pytest.approx(30.0)      # 3 shares x $10
    assert au.get_user_equity_history(CUST_B, SYM_B, days=30)[0]["equity"] == pytest.approx(100.0)


def test_cli_json_mode_is_parseable_and_leaks_no_identifiers(cli, capsys):
    code = cli.main([cli.RECORD_COMMAND, "--min-interval", "0", "--json"], price_fn=_price_fn)
    raw = capsys.readouterr().out.strip()

    assert code == cli.EXIT_OK
    payload = json.loads(raw)
    assert payload["users"] == 2 and payload["recorded"] == 2

    # A launchd log is a shared plain-text file. The sweep's `reports` list carries
    # customer emails and their tickers; the CLI must summarise, never echo them.
    assert "reports" not in payload
    for secret in (CUST_A, CUST_B, OWNER, SYM_A, SYM_B):
        assert secret not in raw


def test_bare_invocation_refuses_to_write(cli, au, capsys):
    """No verb = no sweep. A scheduled writer must not fire on an accidental run."""
    with pytest.raises(SystemExit) as exc:
        cli.main([], price_fn=_price_fn)
    assert exc.value.code != 0
    assert au.get_user_equity_history(CUST_A, SYM_A, days=30) == []


# ─── the owner is never recorded ─────────────────────────────────────────────

def test_owner_is_excluded_from_the_sweep(cli, au, capsys):
    # Precondition: the owner IS in the sweep's universe, so the exclusion is a
    # real branch and not an accident of an empty candidate list.
    assert OWNER in au.customers_with_positions()

    cli.main([cli.RECORD_COMMAND, "--min-interval", "0"], price_fn=_price_fn)

    assert au.get_user_equity_history(OWNER, OWNER_SYM, days=30) == []
    assert _parse_kv(capsys.readouterr().out.strip())["users"] == "2"


def test_no_owner_symbol_reaches_any_customer_row(cli, au):
    """Blanket sweep: nothing the recorder wrote may carry an owner ticker."""
    cli.main([cli.RECORD_COMMAND, "--min-interval", "0"], price_fn=_price_fn)

    for email in (CUST_A, CUST_B):
        for symbol in OWNER_SYMBOLS:
            assert au.get_user_equity_history(email, symbol, days=30) == [], (
                f"owner ticker {symbol} recorded onto {email}'s curve"
            )


def test_owner_rows_absent_even_when_owner_holds_a_customer_ticker(cli, au):
    """The exclusion is by ACCOUNT, not by symbol — an overlapping book is the
    case where a symbol-based filter would quietly pass owner data through."""
    au.set_position(OWNER, SYM_A, 7)

    cli.main([cli.RECORD_COMMAND, "--min-interval", "0"], price_fn=_price_fn)

    assert au.get_user_equity_history(OWNER, SYM_A, days=30) == []
    # ...while the customer holding the same ticker is unaffected.
    assert len(au.get_user_equity_history(CUST_A, SYM_A, days=30)) == 1


# ─── the interval guard survives separate invocations ────────────────────────

def test_second_run_inside_min_interval_does_not_double_record(cli, au, capsys):
    first = cli.main([cli.RECORD_COMMAND], price_fn=_price_fn)          # default 6h
    assert first == cli.EXIT_OK
    assert _parse_kv(capsys.readouterr().out.strip())["recorded"] == "2"

    second = cli.main([cli.RECORD_COMMAND], price_fn=_price_fn)
    fields = _parse_kv(capsys.readouterr().out.strip())

    assert second == cli.EXIT_OK          # an over-frequent tick is a no-op, not a fault
    assert fields["recorded"] == "0"
    assert fields["skipped"] == "2"
    # ECG counts snapshots AS sessions: a second row here would silently shorten
    # what a loss-streak verdict means.
    assert len(au.get_user_equity_history(CUST_A, SYM_A, days=30)) == 1
    assert len(au.get_user_equity_history(CUST_B, SYM_B, days=30)) == 1


def test_min_interval_zero_forces_a_second_observation(cli, au, capsys):
    """The guard is the DEFAULT, not a hard block — a backfill must still work."""
    cli.main([cli.RECORD_COMMAND], price_fn=_price_fn)
    capsys.readouterr()

    # Distinct price so the second row is distinguishable from a re-read of the first.
    cli.main([cli.RECORD_COMMAND, "--min-interval", "0"],
             price_fn=lambda s: 11.0 if s == SYM_A else PRICES.get(s))
    fields = _parse_kv(capsys.readouterr().out.strip())

    assert fields["recorded"] == "2"
    history = au.get_user_equity_history(CUST_A, SYM_A, days=30)
    assert len(history) == 2
    assert history[0]["equity"] == pytest.approx(33.0)   # newest first: 3 x $11


def test_negative_min_interval_is_rejected(cli, au):
    with pytest.raises(SystemExit) as exc:
        cli.main([cli.RECORD_COMMAND, "--min-interval", "-1"], price_fn=_price_fn)
    assert exc.value.code != 0
    assert au.get_user_equity_history(CUST_A, SYM_A, days=30) == []


# ─── one failing user never ends the sweep ───────────────────────────────────

def _boom_for_a(symbol: str) -> float | None:
    if symbol == SYM_A:
        raise RuntimeError("upstream pricing blew up for this tenant")
    return PRICES.get(symbol)


def test_a_single_failing_user_does_not_abort_the_sweep(cli, au, capsys):
    # CUST_A is swept before CUST_B, so B is only reached if the sweep survived A.
    universe = au.customers_with_positions()
    assert universe.index(CUST_A) < universe.index(CUST_B)

    code = cli.main([cli.RECORD_COMMAND, "--min-interval", "0"], price_fn=_boom_for_a)
    fields = _parse_kv(capsys.readouterr().out.strip())

    assert code == cli.EXIT_OK            # a partial failure is not a red job
    assert fields["failed"] == "1"
    assert fields["recorded"] == "1"
    assert au.get_user_equity_history(CUST_A, SYM_A, days=30) == []
    assert len(au.get_user_equity_history(CUST_B, SYM_B, days=30)) == 1


def test_strict_turns_a_partial_failure_into_a_non_zero_exit(cli, au, capsys):
    code = cli.main([cli.RECORD_COMMAND, "--min-interval", "0", "--strict"],
                    price_fn=_boom_for_a)
    fields = _parse_kv(capsys.readouterr().out.strip())

    assert code == cli.EXIT_FAILED
    assert fields["failed"] == "1"
    # ...and the surviving tenant was still recorded: --strict changes the exit
    # code, never the work done.
    assert len(au.get_user_equity_history(CUST_B, SYM_B, days=30)) == 1


def test_an_unpriced_symbol_is_reported_not_stored_as_zero(cli, au, capsys):
    """A missing price is MISSING DATA, never a $0 observation — a stored zero
    reads back as a total wipeout on the equity curve (audit D-07)."""
    cli.main([cli.RECORD_COMMAND, "--min-interval", "0"],
             price_fn=lambda s: None if s == SYM_A else PRICES.get(s))
    fields = _parse_kv(capsys.readouterr().out.strip())

    assert fields["unpriced_symbols"] == "1"
    assert au.get_user_equity_history(CUST_A, SYM_A, days=30) == []


def test_sweep_failure_exits_non_zero_and_summary_line_names_only_the_type(
    cli, monkeypatch, capsys
):
    """A store/config fault IS a real failure — the sweep did not run at all.

    Scope note: this locks the PRINTED summary line, which is the machine-readable
    contract and is fully composed by the CLI. The underlying exception text is
    still handed to the logger by design (see main()), so this is deliberately NOT
    a claim that the whole log file is identifier-free.
    """
    def _explode(**_kwargs: object) -> dict:
        raise RuntimeError(f"database is locked for {CUST_A}")

    monkeypatch.setattr(cli, "record_snapshots_for_all_customers", _explode)
    code = cli.main([cli.RECORD_COMMAND], price_fn=_price_fn)
    out = capsys.readouterr().out

    assert code == cli.EXIT_FAILED
    assert "error=RuntimeError" in out
    assert CUST_A not in out


# ─── the ops wrapper + plist template ────────────────────────────────────────

def test_wrapper_derives_its_root_and_calls_the_verb(repo_root: Path):
    script = repo_root / "record_equity_snapshots.sh"
    body = script.read_text()

    assert script.stat().st_mode & 0o111, "wrapper must be executable"
    assert 'cd "$(dirname "$0")"' in body, "root must be derived, not hardcoded"
    assert "/Users/" not in body, "no hardcoded /Users path in tracked code"
    assert "record-snapshots" in body


def test_plist_is_a_template_that_is_not_installed(repo_root: Path):
    template = repo_root / "com.gesh.equity-snapshots.plist.template"
    body = template.read_text()

    # Named as a template, and carrying a placeholder rather than a machine path.
    assert template.name.endswith(".plist.template")
    assert "__GESH_HOME__" in body
    assert "/Users/" not in body
    # The bootstrap command is documented so installing stays an operator action.
    assert "launchctl bootstrap" in body
    assert "record_equity_snapshots.sh" in body
    # Weekdays only: 1=Mon .. 5=Fri, no Sat(6)/Sun(7) entry.
    assert body.count("<key>Weekday</key>") == 5
    for weekday in range(1, 6):
        assert f"<key>Weekday</key><integer>{weekday}</integer>" in body
    assert "<integer>6</integer>" not in body and "<integer>7</integer>" not in body


def test_plist_template_is_not_installed_under_launchagents():
    """Belt and braces: this change must not have loaded anything."""
    installed = Path.home() / "Library" / "LaunchAgents" / "com.gesh.equity-snapshots.plist"
    assert not installed.exists(), "the template must not be installed by this change"
