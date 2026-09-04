"""Dependency-metadata sanity — cheap guards so the install graph cannot silently
rot back into the GATE-1 state (requirements.txt unresolvable from clean), and so
the gesh-core source tree cannot change without a diff in THIS repository.

Mostly offline file reads. The gesh-core provenance tests additionally inspect
the INSTALLED distribution's recorded origin (`direct_url.json`, PEP 610), which
is the only evidence that the pin was actually honoured — comparing two text
files proves they agree with each other, not that pip obeyed them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements.txt"
REQ_OPENBB = ROOT / "requirements-openbb.txt"
LOCK = ROOT / "requirements.lock"

# The pinned gesh-core commit: 56f14d4 (v0.8.2 + PR #35 DCF tests + PR #46
# CI fd-limit). Declared as a LITERAL on purpose — bumping the dependency then
# requires editing this line, so the change is reviewable in the application repo.
GESH_CORE_SHA = "56f14d4e3a8fd527a9637cc7fb1ce2042c11aee0"
_GESH_CORE_RE = re.compile(r"gesh-core @ git\+https://[^@\s]+@([0-9a-zA-Z._-]+)")


def _pins(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*==\s*([^\s;]+)", line)
        if m:
            pins[m.group(1).lower()] = m.group(2)
    return pins


def test_core_requirements_do_not_carry_the_uvicorn_incompatible_openbb():
    """openbb-core requires uvicorn <0.41; the app pins 0.44.0. The meta-package
    must not sit in the core file, or a clean install cannot resolve."""
    text = REQ.read_text()
    assert not re.search(r"(?m)^\s*openbb\b", text), (
        "openbb meta-package back in requirements.txt — it is uvicorn-incompatible; "
        "keep it in requirements-openbb.txt")
    assert not re.search(r"(?m)^\s*openbb-yfinance\b", text)


def test_openbb_yfinance_is_pinned_compatible_with_yfinance():
    """openbb-yfinance 1.6.1+ require yfinance>=1.2.2; 1.6.3+ require >=1.4.0.
    With yfinance pinned to 1.2.1, only 1.6.0 is compatible."""
    core = _pins(REQ.read_text())
    opt = _pins(REQ_OPENBB.read_text())
    assert core.get("yfinance") == "1.2.1", "yfinance pin moved — recheck openbb-yfinance"
    assert opt.get("openbb-yfinance") == "1.6.0", (
        "openbb-yfinance must stay ==1.6.0 while yfinance==1.2.1 (audit GATE 1)")


def test_uvicorn_pin_is_unchanged():
    """The pin that makes openbb incompatible is deliberate (fastapi 0.135.3)."""
    assert _pins(REQ.read_text()).get("uvicorn") == "0.44.0"


def test_lxml_is_declared_so_stripping_openbb_degrades_nothing():
    """lxml only otherwise arrived transitively via openbb (FINRA scrape)."""
    assert "lxml" in _pins(REQ.read_text())


def test_supported_python_is_declared():
    assert (ROOT / ".python-version").read_text().strip().startswith("3.12")


def test_lock_exists_and_matches_the_core_pins():
    """The lock is the reproducible artifact; it must agree with the source pins
    on the versions that mattered to the conflict."""
    assert LOCK.exists(), "requirements.lock missing — regenerate after editing requirements.txt"
    lock = _pins(LOCK.read_text())
    core = _pins(REQ.read_text())
    for pkg in ("uvicorn", "fastapi", "yfinance", "numpy", "pandas"):
        assert lock.get(pkg) == core.get(pkg), f"{pkg} drift: lock {lock.get(pkg)} vs req {core.get(pkg)}"
    assert "openbb" not in lock, "openbb must not be in the core lock"


def test_lock_pins_gesh_core_to_an_immutable_commit():
    """A branch/tag ref is not reproducible; the lock must pin a commit sha."""
    m = re.search(r"gesh-core @ git\+https://[^@]+@([0-9a-f]{40})", LOCK.read_text())
    assert m, "gesh-core in the lock must be pinned to a 40-char commit sha"


# ── gesh-core provenance: declared == locked == installed ────────────────────
# The pin is a full commit SHA, so every install path names exactly one object
# and the three links are directly comparable. These tests assert that chain end
# to end: a symbolic ref (tag or branch) in an install path would leave the
# reader unable to tell, from this repository alone, which commit was built —
# and only the last test can prove what pip actually installed.

def _declared_ref(path: Path) -> str | None:
    m = _GESH_CORE_RE.search(path.read_text())
    return m.group(1) if m else None


def _installed_gesh_core_commit() -> str | None:
    """The commit pip actually recorded for the installed gesh-core.

    PEP 610 writes `direct_url.json` for VCS installs, with the resolved commit
    in `vcs_info.commit_id`. Returns None when gesh-core is absent or was not
    installed from VCS, so the caller can skip rather than fail a source-only
    checkout."""
    from importlib.metadata import Distribution, PackageNotFoundError
    try:
        dist = Distribution.from_name("gesh-core")
    except PackageNotFoundError:
        return None
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    try:
        return (json.loads(raw).get("vcs_info") or {}).get("commit_id")
    except (ValueError, AttributeError):
        return None


def test_requirements_pins_gesh_core_to_the_audited_commit():
    assert _declared_ref(REQ) == GESH_CORE_SHA, (
        f"requirements.txt gesh-core ref is {_declared_ref(REQ)!r}, expected the "
        f"audited commit {GESH_CORE_SHA}")


def test_lock_pins_gesh_core_to_the_same_audited_commit():
    assert _declared_ref(LOCK) == GESH_CORE_SHA, (
        f"requirements.lock gesh-core ref is {_declared_ref(LOCK)!r}, expected "
        f"{GESH_CORE_SHA} — the lock and the source pin must not diverge")


def test_no_symbolic_gesh_core_ref_in_any_install_path():
    """Install paths must name a commit, not a symbolic ref.

    A tag or branch is resolved at install time, so the built commit is not
    determinable from this repository alone — and an annotated tag additionally
    has its own object SHA distinct from the commit it points at, which makes
    install logs ambiguous to read. A 40-char commit SHA removes both problems.
    """
    for path in (REQ, LOCK):
        ref = _declared_ref(path)
        assert ref and re.fullmatch(r"[0-9a-f]{40}", ref), (
            f"{path.name} references gesh-core by a symbolic ref ({ref!r}); "
            "install paths must use a 40-char commit sha")


def test_installed_gesh_core_is_the_pinned_commit():
    """The link the file comparisons cannot prove: what pip actually installed.

    Skips (rather than fails) when gesh-core is absent or came from a source
    checkout — a developer working on gesh-core itself should not be blocked —
    but in CI, where the runtime is installed from the lock, this is the test
    that would catch a silently different dependency tree."""
    installed = _installed_gesh_core_commit()
    if installed is None:
        pytest.skip("gesh-core not installed from VCS (source checkout or absent)")
    assert installed == GESH_CORE_SHA, (
        f"INSTALLED gesh-core is {installed}, but the pin is {GESH_CORE_SHA}. "
        "The environment does not match the locked dependency.")
