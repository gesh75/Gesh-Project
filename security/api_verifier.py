"""
security/api_verifier.py — API Verification & Security Layer

Wraps all external API interactions with:
  • Response integrity checks (schema validation, NaN/Inf scrubbing)
  • Rate-limit detection & automatic back-off logging
  • Credential hygiene (no secrets in logs or error messages)
  • Anomaly detection (sudden data spikes, impossible prices)
  • Health reporting for the dashboard

This does NOT replace auth.py — it wraps the responses that come back
from Robinhood, yfinance, FRED, and Reddit AFTER authentication.

Key functions
─────────────
  verify_response(source, data, schema_keys)
      → VerifyResult(ok, issues, sanitised_data)

  check_api_health(source)
      → APIHealthReport with latency, last_error, rate_limit_hits

  rate_limit_guard(source, max_per_minute)
      → bool (True = safe to call, False = throttled)

  sanitise_financial_data(data)
      → data with NaN/Inf replaced and prices range-checked

  log_api_event(source, event_type, detail)
      → writes to security_log table in gesh_financial.db
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ─── Config ───────────────────────────────────────────────────────────────────

# Sanity bounds for equity prices (catches impossible API responses)
PRICE_MIN: float   = 0.001      # below this → flag as anomaly
PRICE_MAX: float   = 1_000_000  # above this → flag as anomaly
VOLUME_MAX: int    = 10_000_000_000  # 10B shares — impossible if higher

# Rate-limit windows (calls per 60 seconds)
RATE_LIMITS: dict[str, int] = {
    "robinhood":  20,
    "yfinance":   30,
    "fred":       60,
    "reddit":     10,
    "alpaca":     60,
    "telegram":   30,
    "default":    30,
}

DB_PATH = Path("/Users/georgigaydarov/Projects/gesh-project-trading/gesh_financial.db")
_FALLBACK_DB = Path("/tmp/gesh_security.db")


def _db() -> Path:
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=3)
        conn.execute("SELECT 1")
        conn.close()
        return DB_PATH
    except Exception:
        return _FALLBACK_DB


# ─── In-memory call tracker ───────────────────────────────────────────────────

_call_log: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
_rate_limit_hits: dict[str, int] = defaultdict(int)
_last_errors: dict[str, str] = {}
_latencies: dict[str, list[float]] = defaultdict(list)


# ─── DB schema ────────────────────────────────────────────────────────────────

def _init_security_log() -> None:
    try:
        with sqlite3.connect(str(_db()), timeout=5) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS security_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TEXT DEFAULT (datetime('now')),
                    source      TEXT,
                    event_type  TEXT,
                    detail      TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sl_source ON security_log(source)")
            conn.commit()
    except Exception:
        pass  # Non-fatal — logging is best-effort


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class VerifyResult:
    ok:             bool
    issues:         list[str] = field(default_factory=list)
    sanitised_data: Any       = None
    anomalies:      list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok":        self.ok,
            "issues":    self.issues,
            "anomalies": self.anomalies,
        }


@dataclass
class APIHealthReport:
    source:           str
    healthy:          bool
    avg_latency_ms:   float
    rate_limit_hits:  int
    last_error:       str
    calls_last_60s:   int
    generated_at:     str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ─── Sanitisation ─────────────────────────────────────────────────────────────

def sanitise_financial_data(data: Any) -> tuple[Any, list[str]]:
    """
    Recursively:
      • Replace float NaN/Inf with None
      • Flag prices outside [PRICE_MIN, PRICE_MAX]
      • Flag volumes above VOLUME_MAX

    Returns (sanitised_data, list_of_anomaly_strings).
    """
    anomalies: list[str] = []

    def _walk(obj: Any, path: str = "") -> Any:
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                anomalies.append(f"NaN/Inf at {path or 'root'}")
                return None
            # Price range check for fields that look like prices
            key_lower = path.lower()
            if any(k in key_lower for k in ("price", "close", "open", "high", "low", "bid", "ask")):
                if obj != 0 and (obj < PRICE_MIN or obj > PRICE_MAX):
                    anomalies.append(f"Suspicious price {obj} at {path}")
            if "volume" in key_lower and obj > VOLUME_MAX:
                anomalies.append(f"Impossible volume {obj:.0f} at {path}")
            return obj
        if isinstance(obj, dict):
            return {k: _walk(v, f"{path}.{k}") for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(v, f"{path}[{i}]") for i, v in enumerate(obj)]
        return obj

    return _walk(data), anomalies


# ─── Schema verification ──────────────────────────────────────────────────────

def verify_response(
    source: str,
    data: Any,
    required_keys: list[str] | None = None,
    optional_keys: list[str] | None = None,
) -> VerifyResult:
    """
    Verify an API response for integrity.

    Parameters
    ----------
    source       : API name for logging ("robinhood", "yfinance", etc.)
    data         : raw response dict / list
    required_keys: list of keys that MUST be present in a dict response
    optional_keys: additional keys to check (non-fatal if missing)

    Returns
    -------
    VerifyResult with ok=True if no critical issues found.
    """
    issues: list[str] = []

    if data is None:
        issues.append(f"{source}: response is None")
        log_api_event(source, "NULL_RESPONSE", "Response was None")
        return VerifyResult(ok=False, issues=issues, sanitised_data=None)

    if isinstance(data, dict) and "error" in data:
        err = str(data["error"])
        issues.append(f"{source}: response contains error: {_mask_secrets(err)}")
        log_api_event(source, "API_ERROR", _mask_secrets(err))
        return VerifyResult(ok=False, issues=issues, sanitised_data=data)

    # Required key check
    if required_keys and isinstance(data, dict):
        missing = [k for k in required_keys if k not in data]
        if missing:
            issues.append(f"{source}: missing required keys: {missing}")

    # Sanitise floats and check for anomalies
    sanitised, anomalies = sanitise_financial_data(data)

    if anomalies:
        for a in anomalies:
            log_api_event(source, "ANOMALY", a)

    ok = len(issues) == 0
    if not ok:
        log_api_event(source, "SCHEMA_FAIL", str(issues))

    return VerifyResult(ok=ok, issues=issues, sanitised_data=sanitised, anomalies=anomalies)


# ─── Rate-limit guard ─────────────────────────────────────────────────────────

def rate_limit_guard(source: str, max_per_minute: int | None = None) -> bool:
    """
    Check whether it's safe to call `source` right now.

    Returns True (safe) or False (throttled).
    Adds the call timestamp to the in-memory log on True.
    """
    limit = max_per_minute or RATE_LIMITS.get(source, RATE_LIMITS["default"])
    now   = time.monotonic()
    window = _call_log[source]

    # Purge entries older than 60 seconds
    while window and (now - window[0]) > 60.0:
        window.popleft()

    if len(window) >= limit:
        _rate_limit_hits[source] += 1
        log_api_event(source, "RATE_LIMIT", f"Throttled ({len(window)}/{limit} calls in last 60s)")
        return False

    window.append(now)
    return True


def record_latency(source: str, ms: float) -> None:
    """Record an API call latency for health reporting."""
    _latencies[source].append(ms)
    if len(_latencies[source]) > 100:
        _latencies[source] = _latencies[source][-100:]


def record_error(source: str, error: str) -> None:
    """Record the last error for a source."""
    _last_errors[source] = _mask_secrets(error)
    log_api_event(source, "ERROR", _mask_secrets(error))


# ─── Health report ────────────────────────────────────────────────────────────

def check_api_health(source: str) -> APIHealthReport:
    """Return an APIHealthReport for the named source."""
    now    = time.monotonic()
    window = _call_log[source]
    while window and (now - window[0]) > 60.0:
        window.popleft()

    lats = _latencies.get(source, [])
    avg_lat = float(sum(lats[-20:]) / max(len(lats[-20:]), 1))

    return APIHealthReport(
        source          = source,
        healthy         = (_rate_limit_hits[source] == 0 and source not in _last_errors),
        avg_latency_ms  = round(avg_lat, 1),
        rate_limit_hits = _rate_limit_hits[source],
        last_error      = _last_errors.get(source, ""),
        calls_last_60s  = len(window),
    )


def all_api_health() -> list[dict[str, Any]]:
    """Health report for all known sources — for the dashboard."""
    sources = list(RATE_LIMITS.keys()) + list(_call_log.keys())
    seen: set[str] = set()
    reports = []
    for s in sources:
        if s == "default" or s in seen:
            continue
        seen.add(s)
        r = check_api_health(s)
        reports.append({
            "source":          r.source,
            "healthy":         r.healthy,
            "avg_latency_ms":  r.avg_latency_ms,
            "rate_limit_hits": r.rate_limit_hits,
            "last_error":      r.last_error,
            "calls_last_60s":  r.calls_last_60s,
        })
    return reports


# ─── Logging ──────────────────────────────────────────────────────────────────

def log_api_event(source: str, event_type: str, detail: str) -> None:
    """Best-effort write to security_log table."""
    try:
        _init_security_log()
        with sqlite3.connect(str(_db()), timeout=3) as conn:
            conn.execute(
                "INSERT INTO security_log (source, event_type, detail) VALUES (?, ?, ?)",
                (source, event_type, detail[:500]),
            )
            conn.commit()
    except Exception:
        pass  # Never let security logging crash the main flow


# ─── Secret masking ───────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    "api_key", "apikey", "token", "secret", "password", "passwd",
    "bearer", "client_id", "client_secret", "access_token", "refresh_token",
]


def _mask_secrets(text: str) -> str:
    """Replace credential values in error strings with ***."""
    import re
    for pat in _SECRET_PATTERNS:
        # Match key=value or "key": "value"
        text = re.sub(
            rf'(?i)({pat}["\s:=]+)[^\s,\'"{{}}]+',
            r'\1***',
            text,
        )
    return text
