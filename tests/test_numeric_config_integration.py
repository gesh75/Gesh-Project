"""R6 product integration: numeric safety config from pinned gesh-core.

Proves the product shims actually receive fail-closed numeric validation,
not only the boolean P0-B contract.
"""

from __future__ import annotations

import pytest

gesh_core_config = pytest.importorskip(
    "gesh_core.trading.config",
    reason="gesh-core not installed in this environment",
)

if not hasattr(gesh_core_config, "parse_unit_interval"):
    pytest.skip(
        "gesh-core.trading.config lacks parse_unit_interval — install the R6 pin",
        allow_module_level=True,
    )

ConfigError = gesh_core_config.ConfigError
effective_config = gesh_core_config.effective_config
load_risk_config = gesh_core_config.load_risk_config


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    for key in (
        "PAPER_MODE",
        "REQUIRE_APPROVAL",
        "MARKET_REGIME_GATE",
        "MAX_DAILY_LOSS_PCT",
        "MAX_POSITION_PCT",
        "KELLY_MAX_GROSS_PCT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_infinite_max_loss_fails_via_product_shim(monkeypatch):
    from trading.config import load_risk_config as shim_load

    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "inf")
    with pytest.raises(ConfigError) as exc:
        shim_load()
    assert "MAX_DAILY_LOSS_PCT" in str(exc.value)


def test_negative_kelly_gross_fails_via_product_shim(monkeypatch):
    import kelly as kelly_mod

    monkeypatch.setenv("KELLY_MAX_GROSS_PCT", "-0.25")
    with pytest.raises(ConfigError):
        kelly_mod._max_gross_exposure()


def test_conservative_defaults_still_load():
    cfg = load_risk_config()
    assert cfg.paper_mode is True
    assert cfg.max_daily_loss_pct == 0.01
    assert cfg.max_position_pct == 0.02
    assert cfg.max_orders_per_day == 3


def test_numeric_limit_is_in_config_hash(monkeypatch):
    base = effective_config()
    assert "MAX_DAILY_LOSS_PCT" in base.settings
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "0.02")
    changed = effective_config()
    assert base.config_hash != changed.config_hash
    assert changed.settings["MAX_DAILY_LOSS_PCT"].raw_input == "<redacted>"
    assert "0.02" not in changed.settings["MAX_DAILY_LOSS_PCT"].raw_input
