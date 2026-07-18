from __future__ import annotations

from datetime import datetime, timezone

import build_snapshot_from_mcp
import fetch_live_snapshot


def test_mcp_snapshot_preserves_provider_quote_time_and_session() -> None:
    item = {
        "quote": {
            "symbol": "NVDA",
            "last_trade_price": "202.64",
            "venue_last_trade_time": "2026-07-17T19:59:59.997524406Z",
        }
    }
    meta = build_snapshot_from_mcp.quote_provenance(item, "transport-17")
    assert meta == {
        "quote_as_of": "2026-07-17T19:59:59.997524406Z",
        "provider": "robinhood_mcp",
        "session": "transport-17",
        "timestamp_basis": "venue_last_trade_time",
    }


def test_live_snapshot_uses_provider_quote_time_not_retrieval_time() -> None:
    retrieved = datetime(2026, 7, 17, 20, 5, tzinfo=timezone.utc)
    quote = {
        "symbol": "MU",
        "last_trade_price": "120.50",
        "updated_at": "2026-07-17T20:00:01Z",
    }
    price, meta = fetch_live_snapshot.extract_live_quote(quote, retrieved)
    assert price == 120.50
    assert meta["quote_as_of"] == "2026-07-17T20:00:01Z"
    assert meta["provider"] == "robinhood_live"
    assert meta["timestamp_basis"] == "updated_at"


def test_missing_provider_timestamp_is_explicit_not_transport_disguised() -> None:
    retrieved = datetime(2026, 7, 17, 20, 5, tzinfo=timezone.utc)
    _, meta = fetch_live_snapshot.extract_live_quote(
        {"symbol": "MU", "last_trade_price": "120.50"}, retrieved
    )
    assert meta["quote_as_of"] is None
    assert meta["retrieved_at"] == retrieved.isoformat()
    assert meta["timestamp_basis"] == "unavailable"
