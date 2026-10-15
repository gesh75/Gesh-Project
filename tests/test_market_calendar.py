"""NYSE holiday / early-close calendar used by MARKET_HOURS (trading/market_calendar.py).

Pure date math, no network, no package dependency. Dates below are the
published NYSE schedule for 2025-2027.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from trading import market_calendar as mc

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "day",
    [
        _dt.date(2026, 1, 1),    # New Year's Day
        _dt.date(2026, 1, 19),   # MLK Day (3rd Monday Jan)
        _dt.date(2026, 2, 16),   # Presidents' Day (3rd Monday Feb)
        _dt.date(2026, 4, 3),    # Good Friday
        _dt.date(2026, 5, 25),   # Memorial Day (last Monday May)
        _dt.date(2026, 6, 19),   # Juneteenth
        _dt.date(2026, 7, 3),    # Independence Day observed (Jul 4 is Saturday)
        _dt.date(2026, 9, 7),    # Labor Day
        _dt.date(2026, 11, 26),  # Thanksgiving (4th Thursday Nov)
        _dt.date(2026, 12, 25),  # Christmas
        _dt.date(2027, 7, 5),    # Independence Day observed (Jul 4 is Sunday)
        _dt.date(2025, 4, 18),   # Good Friday 2025
        _dt.date(2027, 3, 26),   # Good Friday 2027
    ],
)
def test_full_closures(day: _dt.date) -> None:
    assert mc.is_full_holiday(day) is True
    assert mc.is_trading_day(day) is False


@pytest.mark.parametrize(
    "day",
    [
        _dt.date(2026, 6, 3),    # ordinary Wednesday (FROZEN_NOW in the gate tests)
        _dt.date(2026, 7, 6),    # Monday after the observed Fourth
        _dt.date(2026, 12, 24),  # Christmas Eve is an early close, not a closure
        _dt.date(2026, 11, 27),  # Black Friday is an early close, not a closure
        _dt.date(2026, 10, 12),  # Columbus Day: banks close, NYSE trades
        _dt.date(2026, 11, 11),  # Veterans Day: NYSE trades
    ],
)
def test_open_weekdays(day: _dt.date) -> None:
    assert mc.is_full_holiday(day) is False
    assert mc.is_trading_day(day) is True


def test_weekends_are_not_trading_days() -> None:
    assert mc.is_trading_day(_dt.date(2026, 6, 6)) is False  # Saturday
    assert mc.is_trading_day(_dt.date(2026, 6, 7)) is False  # Sunday


@pytest.mark.parametrize(
    "day",
    [
        _dt.date(2026, 11, 27),  # day after Thanksgiving
        _dt.date(2026, 12, 24),  # Christmas Eve (Thursday)
        _dt.date(2025, 7, 3),    # Jul 3 2025 is a Thursday before a Friday Fourth
        _dt.date(2024, 7, 3),    # Jul 3 2024 is a Wednesday before a Thursday Fourth
    ],
)
def test_early_close_days(day: _dt.date) -> None:
    assert mc.early_close(day) == _dt.time(13, 0)


def test_early_close_not_when_the_eve_is_itself_a_closure_or_weekend() -> None:
    # 2026-07-03 is the observed Fourth (closed), so no 13:00 early close that day.
    assert mc.early_close(_dt.date(2026, 7, 3)) is None
    # 2027-12-24 is the observed Christmas (Sat 25th) → closed, not early.
    assert mc.is_full_holiday(_dt.date(2027, 12, 24)) is True
    assert mc.early_close(_dt.date(2027, 12, 24)) is None
    # Ordinary days have no early close.
    assert mc.early_close(_dt.date(2026, 6, 3)) is None


def test_session_close_prefers_early_close() -> None:
    regular = _dt.time(16, 0)
    assert mc.session_close(_dt.date(2026, 11, 27), regular) == _dt.time(13, 0)
    assert mc.session_close(_dt.date(2026, 6, 3), regular) == regular


def test_easter_anchor_known_values() -> None:
    assert mc.easter_sunday(2025) == _dt.date(2025, 4, 20)
    assert mc.easter_sunday(2026) == _dt.date(2026, 4, 5)
    assert mc.easter_sunday(2027) == _dt.date(2027, 3, 28)
