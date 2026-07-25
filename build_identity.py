"""Validated, non-secret identity for an immutable GESH release build."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

SERVICE_NAME = "gesh-financial"
BUILD_INFO_FILENAME = "GESH_BUILD_INFO.json"
BUILD_INFO_KEYS = frozenset(("service", "commit_sha", "built_at", "dirty"))
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
_UTC_SECOND_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_MAX_BUILD_INFO_BYTES = 4096


class BuildIdentityError(ValueError):
    """The release identity is missing, malformed, or not immutable."""


def validate_build_info(value: object) -> dict:
    """Return the exact public allowlist or raise ``BuildIdentityError``.

    Release identity is deliberately stricter than generic JSON metadata:
    unknown keys are rejected, the SHA is a full lowercase Git object ID, the
    timestamp is UTC to the second, and ``dirty`` must be the literal false
    boolean. This prevents a runtime file from becoming a general-purpose
    environment/branch/path disclosure channel.
    """
    if not isinstance(value, dict) or set(value) != BUILD_INFO_KEYS:
        raise BuildIdentityError("build identity keys are invalid")
    if value.get("service") != SERVICE_NAME:
        raise BuildIdentityError("build identity service is invalid")
    commit_sha = value.get("commit_sha")
    if not isinstance(commit_sha, str) or not _FULL_SHA_RE.fullmatch(commit_sha):
        raise BuildIdentityError("build identity SHA is invalid")
    built_at = value.get("built_at")
    if not isinstance(built_at, str) or not _UTC_SECOND_RE.fullmatch(built_at):
        raise BuildIdentityError("build identity timestamp is invalid")
    try:
        datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise BuildIdentityError("build identity timestamp is invalid") from exc
    if value.get("dirty") is not False:
        raise BuildIdentityError("dirty release identity is prohibited")
    return {
        "service": SERVICE_NAME,
        "commit_sha": commit_sha,
        "built_at": built_at,
        "dirty": False,
    }


def load_build_info(path: Path) -> dict:
    """Read and validate a fixed release sidecar without leaking its contents."""
    try:
        if path.stat().st_size > _MAX_BUILD_INFO_BYTES:
            raise BuildIdentityError("build identity file is too large")
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except BuildIdentityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError("build identity cannot be read") from exc
    return validate_build_info(value)
