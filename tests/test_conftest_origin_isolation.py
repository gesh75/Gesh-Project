"""Host .env must not leak deployment origins into the offline suite."""

from __future__ import annotations

import os


def test_autouse_clears_app_base_url_leaked_from_dotenv():
    """Regression for the ~64 host-local CSRF failures (CLAUDE.md 2026-07-30).

    `load_dotenv()` at import used to leave APP_BASE_URL=https://pay.geshfinance.com
    in os.environ; TestClient Origin http://testserver then failed every cookie
    mutation. The autouse fixture in conftest must clear it unless a test setenv'd
    a specific value.
    """
    assert os.getenv("APP_BASE_URL") is None
    assert os.getenv("GESH_ALLOWED_ORIGINS") is None
