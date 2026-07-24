"""Dependency-metadata sanity — cheap guards so the install graph cannot silently
rot back into the GATE-1 state (requirements.txt unresolvable from clean).

No network: these read the tracked requirement files, not the installed env.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements.txt"
REQ_OPENBB = ROOT / "requirements-openbb.txt"
LOCK = ROOT / "requirements.lock"


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
