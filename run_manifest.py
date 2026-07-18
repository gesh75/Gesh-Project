"""Immutable provenance contract for one daily-analysis run."""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

UTC = timezone.utc
DEGRADED_AFTER_S_DEFAULT = 3600.0


def _parse_utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("provenance timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RunManifest:
    run_id: str
    input_source: str
    snapshot_as_of: datetime
    started_at: datetime
    degraded: bool
    quote_as_of: Mapping[str, datetime]

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_as_of", _parse_utc(self.snapshot_as_of))
        object.__setattr__(self, "started_at", _parse_utc(self.started_at))
        copied = {str(symbol).upper(): _parse_utc(value) for symbol, value in self.quote_as_of.items()}
        object.__setattr__(self, "quote_as_of", MappingProxyType(copied))

    @classmethod
    def create(
        cls,
        *,
        source: str,
        snapshot_as_of: datetime | str,
        degraded: bool,
        quote_as_of: Mapping[str, datetime | str] | None = None,
        now: datetime | str | None = None,
        run_id: str | None = None,
    ) -> "RunManifest":
        return cls(
            run_id=run_id or str(uuid.uuid4()),
            input_source=source or "UNKNOWN",
            snapshot_as_of=_parse_utc(snapshot_as_of),
            started_at=_parse_utc(now or datetime.now(UTC)),
            degraded=bool(degraded),
            quote_as_of=quote_as_of or {},
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunManifest":
        return cls.create(
            run_id=str(payload["run_id"]),
            source=str(payload.get("input_source") or "UNKNOWN"),
            snapshot_as_of=payload["snapshot_as_of"],
            now=payload["started_at"],
            degraded=bool(payload.get("degraded")),
            quote_as_of=payload.get("quote_as_of") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "input_source": self.input_source,
            "snapshot_as_of": _iso(self.snapshot_as_of),
            "started_at": _iso(self.started_at),
            "degraded": self.degraded,
            "quote_as_of": {symbol: _iso(value) for symbol, value in self.quote_as_of.items()},
        }


def atomic_write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w") as handle:
            json.dump(payload, handle, indent=2, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot_degraded_info(
    snapshot: Mapping[str, Any], threshold_s: float | None = None, *, now: datetime | None = None
) -> dict[str, Any]:
    if threshold_s is None:
        try:
            threshold_s = float(os.environ.get("GESH_DEGRADED_AFTER_S") or DEGRADED_AFTER_S_DEFAULT)
        except (TypeError, ValueError):
            threshold_s = DEGRADED_AFTER_S_DEFAULT
    current = now or datetime.now(UTC)
    try:
        age_s: float | None = (current - _parse_utc(snapshot["fetched_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        age_s = None
    return {
        "degraded": age_s is None or age_s > threshold_s,
        "snapshot_age_seconds": None if age_s is None else int(age_s),
        "snapshot_source": snapshot.get("source", "UNKNOWN"),
    }


def manifest_from_snapshot(
    snapshot: Mapping[str, Any], *, threshold_s: float | None = None, now: datetime | None = None
) -> RunManifest:
    info = snapshot_degraded_info(snapshot, threshold_s, now=now)
    provenance = snapshot.get("quote_provenance") or {}
    quote_times = {
        symbol: item["quote_as_of"]
        for symbol, item in provenance.items()
        if isinstance(item, Mapping) and item.get("quote_as_of")
    }
    return RunManifest.create(
        source=str(snapshot.get("source") or "UNKNOWN"),
        snapshot_as_of=snapshot["fetched_at"],
        degraded=info["degraded"],
        quote_as_of=quote_times,
        now=now,
    )


def report_output_path(base: Path, engine_results: Mapping[str, Any], *, requested: Path) -> Path:
    manifest = engine_results.get("run_manifest") or {}
    degraded = bool(manifest.get("degraded", engine_results.get("degraded", False)))
    if not degraded:
        return requested
    run_id = str(manifest.get("run_id") or "unknown").replace("/", "-")
    return base / f"gesh_report.degraded.{run_id}.html"
