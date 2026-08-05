import importlib
import sys
from types import SimpleNamespace

import pytest


class _Response:
    status_code = 200

    def __init__(self, action):
        self.text = f'{{"ok": true, "action": "{action}"}}'
        self._action = action

    def json(self):
        return {"ok": True, "action": self._action}


@pytest.fixture
def smoke_module(monkeypatch):
    fake_requests = SimpleNamespace(RequestException=Exception, post=None)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    sys.modules.pop("scripts.ls_webhook_smoketest", None)
    return importlib.import_module("scripts.ls_webhook_smoketest")


@pytest.mark.parametrize(
    ("event", "action"),
    [("subscription_created", "provision"), ("subscription_expired", "revoke")],
)
def test_main_expected_semantic_action_succeeds(monkeypatch, smoke_module, event, action):
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "obviously-fake-secret")
    monkeypatch.setattr(sys, "argv", ["ls_webhook_smoketest.py", event])
    monkeypatch.setattr(
        smoke_module.requests,
        "post",
        lambda *args, **kwargs: _Response(action),
    )

    assert smoke_module.main() == 0


@pytest.mark.parametrize("event", ["subscription_created", "subscription_expired"])
@pytest.mark.parametrize("action", ["ignored", "noop", "duplicate"])
def test_main_nonsemantic_success_action_fails(monkeypatch, smoke_module, event, action):
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "obviously-fake-secret")
    monkeypatch.setattr(sys, "argv", ["ls_webhook_smoketest.py", event])
    monkeypatch.setattr(
        smoke_module.requests,
        "post",
        lambda *args, **kwargs: _Response(action),
    )

    assert smoke_module.main() == 1
