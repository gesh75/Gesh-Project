"""P0-B integration: the strict safety-boolean contract from gesh-core.

Proves the product repo actually gets the strict parser it now pins, rather than
trusting the dependency bump. Covers the three safety booleans that gate live
trading — PAPER_MODE, REQUIRE_APPROVAL, MARKET_REGIME_GATE.

Contract (gesh-core d4e9f46, PR #8):
  * missing            -> documented conservative default (all three True)
  * "true"/"false"     -> accepted, case-insensitive, after trimming
  * blank              -> ConfigError
  * 1/0, yes/no, y/n, on/off, malformed -> ConfigError
  * invalid input is NEVER silently coerced to False

Scope note: this file deliberately adds no scheduler, launchd, trading-strategy,
broker or quota coverage. Atomic quota is a separate, later task.
"""

from __future__ import annotations

import json
import re
from importlib.metadata import Distribution, PackageNotFoundError
from pathlib import Path

import pytest

gesh_core_config = pytest.importorskip(
    "gesh_core.trading.config",
    reason="gesh-core not installed in this environment",
)

PINNED_SHA = "a6d287bbb146add4d21523c9c9bffdfb6b3cfc8a"

# Guard against gesh-core installs that predate the strict-config PR (#8).
# pytest.importorskip only catches ImportError; if the module is importable
# but lacks ConfigError, the module-level assignment below would raise
# AttributeError at collection time — crashing the entire suite (76 errors),
# not just this file.  A defensive hasattr check converts that into a clean
# SKIP with a descriptive message. (Issue #140)
if not hasattr(gesh_core_config, "ConfigError"):
    pytest.skip(
        f"gesh-core.trading.config lacks ConfigError — install the pinned commit "
        f"{PINNED_SHA} (PR #8) to run these tests; "
        f"current install predates the strict-boolean parser",
        allow_module_level=True,
    )

ConfigError = gesh_core_config.ConfigError
load_risk_config = gesh_core_config.load_risk_config

SAFETY_KEYS = ("PAPER_MODE", "REQUIRE_APPROVAL", "MARKET_REGIME_GATE")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_safety_env(monkeypatch):
    """Hermetic: remove the three safety vars, restoring inherited values after."""
    for key in SAFETY_KEYS:
        monkeypatch.delenv(key, raising=False)


def _attr(key: str) -> str:
    return key.lower()


# --- 1. missing values preserve conservative defaults ------------------------


def test_missing_values_preserve_conservative_defaults():
    cfg = load_risk_config()
    assert cfg.paper_mode is True
    assert cfg.require_approval is True
    assert cfg.market_regime_gate is True


# --- 2. trimmed, case-insensitive true/false work ----------------------------


@pytest.mark.parametrize("key", SAFETY_KEYS)
@pytest.mark.parametrize(
    "token,expected",
    [
        ("true", True), ("True", True), ("TRUE", True),
        (" true ", True), ("\ttrue\n", True),
        ("false", False), ("False", False), ("FALSE", False),
        (" false ", False), ("\tfalse\n", False),
    ],
)
def test_trimmed_case_insensitive_true_false(monkeypatch, key, token, expected):
    monkeypatch.setenv(key, token)
    assert getattr(load_risk_config(), _attr(key)) is expected


# --- 3. blank, aliases and malformed raise ConfigError -----------------------


@pytest.mark.parametrize("key", SAFETY_KEYS)
@pytest.mark.parametrize(
    "token",
    ["", "   ", "\t", "1", "0", "yes", "no", "y", "n", "on", "off",
     "YES", "NO", "ON", "OFF", "maybe", "truthy", "2", "null", "None"],
)
def test_blank_aliases_and_malformed_raise(monkeypatch, key, token):
    monkeypatch.setenv(key, token)
    with pytest.raises(ConfigError):
        load_risk_config()


# --- 4. invalid input is never silently coerced to False ---------------------


@pytest.mark.parametrize("key", SAFETY_KEYS)
@pytest.mark.parametrize("token", ["", "0", "no", "off", "garbage"])
def test_invalid_input_never_silently_coerced_to_false(monkeypatch, key, token):
    """The pre-P0-B parser mapped every unrecognised token to False, silently
    disabling a safety gate. The strict parser must raise instead — never return
    a config object at all."""
    monkeypatch.setenv(key, token)
    try:
        cfg = load_risk_config()
    except ConfigError:
        return  # correct: refused to build
    pytest.fail(
        f"{key}={token!r} produced a config instead of raising ConfigError "
        f"({_attr(key)}={getattr(cfg, _attr(key))!r}) — silent coercion regression"
    )


# --- 5. the installed package is the pinned merged gesh-core revision --------


def _installed_gesh_core_commit() -> str | None:
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


def test_installed_gesh_core_is_the_pinned_strict_config_revision():
    """Skips on a source checkout (developer working on gesh-core itself); in a
    lock-installed environment this is what proves the strict parser is real."""
    installed = _installed_gesh_core_commit()
    if installed is None:
        pytest.skip("gesh-core not installed from VCS (source checkout or absent)")
    assert installed == PINNED_SHA, (
        f"installed gesh-core is {installed}, pin is {PINNED_SHA}"
    )


def test_both_dependency_files_pin_the_strict_config_revision():
    """File-level proof, independent of what happens to be installed."""
    pattern = re.compile(r"gesh-core @ git\+https://[^@\s]+@([0-9a-f]{40})")
    for name in ("requirements.txt", "requirements.lock"):
        match = pattern.search((ROOT / name).read_text())
        assert match, f"{name} has no 40-char gesh-core commit pin"
        assert match.group(1) == PINNED_SHA, (
            f"{name} pins {match.group(1)}, expected {PINNED_SHA}"
        )


def test_strict_parser_symbols_are_actually_present():
    """Guards against a stale install that resolves but predates PR #8."""
    assert issubclass(ConfigError, ValueError)
    assert hasattr(gesh_core_config, "_parse_strict_bool")
    assert hasattr(gesh_core_config, "_env_strict_bool")
