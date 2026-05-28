import os
import pickle

import robin_stocks.robinhood as rh
from dotenv import load_dotenv

load_dotenv()


def _session_file_is_safe(path: str) -> bool:
    """Refuse to load a session file that is not owned by us or is group/world-
    writable — deserializing attacker-writable data is an RCE vector."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_uid == os.getuid() and not (st.st_mode & 0o022)

_PICKLE_NAME = "robinhood_session"
# robin_stocks stores the session pickle at ~/.tokens/robinhood<pickle_name>.pickle
_PICKLE_PATH = os.path.join(
    os.path.expanduser("~"), ".tokens", f"robinhood{_PICKLE_NAME}.pickle"
)


def _restore_pickle_session() -> bool:
    """
    Restore the stored Robinhood session from its pickle file and verify the
    access token still works — WITHOUT ever POSTing credentials to Robinhood.

    This mirrors robin_stocks' own cached-session fast path but deliberately
    omits the credential-login fallthrough, so it can never trigger a Robinhood
    device-approval / "new login" alert and never adds load to Robinhood's
    rate-limited login endpoint. The check is on the token's real validity (a
    lightweight authenticated GET), not on the pickle file's age.

    Returns True if a working session was restored, False otherwise.
    """
    if not os.path.isfile(_PICKLE_PATH):
        return False
    if not _session_file_is_safe(_PICKLE_PATH):
        return False
    try:
        with open(_PICKLE_PATH, "rb") as fh:
            data = pickle.load(fh)
        authorization = f"{data['token_type']} {data['access_token']}"
    except (OSError, KeyError, EOFError, pickle.UnpicklingError, AttributeError):
        return False

    try:
        from robin_stocks.robinhood.helper import (  # noqa: PLC0415
            request_get,
            set_login_state,
            update_session,
        )
        from robin_stocks.robinhood.urls import positions_url  # noqa: PLC0415
    except ImportError:
        return False

    set_login_state(True)
    update_session("Authorization", authorization)
    try:
        # One lightweight authenticated GET confirms the token is still valid.
        res = request_get(
            positions_url(), "pagination", {"nonzero": "true"}, jsonify_data=False
        )
        res.raise_for_status()
    except Exception:
        set_login_state(False)
        update_session("Authorization", None)
        return False
    return True


def login(allow_credentials: bool = True) -> None:
    """
    Log in to Robinhood.

    allow_credentials=True  — full login. Reuses the stored session pickle if it
        is still valid; otherwise POSTs the username/password from .env. That
        credential POST makes Robinhood send a device-approval / "new login"
        alert and — because this runs unattended with no MFA code — then fails.
        Run this path at most once a day; it is the only path that can produce
        an alert or add load to Robinhood's login endpoint.

    allow_credentials=False — reuse-only. Restores and verifies the stored
        session pickle and never POSTs credentials. If the stored session is
        missing or expired, raises RuntimeError so the caller can skip Robinhood
        or fall back to a no-login data source. This path never triggers an
        alert and is safe to call on a tight schedule.

    Raises EnvironmentError / RuntimeError if a usable session cannot be established.
    """
    if not allow_credentials:
        if _restore_pickle_session():
            return
        raise RuntimeError(
            "Robinhood reuse-only login: stored session is missing or expired "
            "(no credentials were sent). Caller should skip Robinhood / use a "
            "no-login fallback. Run reauth.py to refresh the session."
        )

    username = os.getenv("ROBINHOOD_USER")
    password = os.getenv("ROBINHOOD_PASS")
    if not username or not password:
        raise EnvironmentError("ROBINHOOD_USER and ROBINHOOD_PASS must be set in your .env file.")

    result = rh.login(
        username=username,
        password=password,
        expiresIn=86400,
        store_session=True,
        pickle_name=_PICKLE_NAME,
    )
    # rh.login() returns None on failure (e.g. MFA required, bad credentials,
    # network error). Verify the session is actually active — don't rely on the
    # absence of an exception alone.
    if result is None or not result.get("access_token"):
        from robin_stocks.robinhood import helper as _rh_helper  # noqa: PLC0415
        if not _rh_helper.LOGGED_IN:
            raise RuntimeError(
                "Robinhood login returned no access_token. The stored session is "
                "expired and MFA is required. Run reauth.py interactively once to "
                "refresh the session."
            )


def logout() -> None:
    rh.logout()
