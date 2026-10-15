"""NYSE trading-day calendar: full closures and 13:00 ET early closes.

Pure date arithmetic from the published NYSE holiday rules. No network, no
third-party package, so ``trading.risk_gate`` stays importable and testable
offline. Rules encoded:

Full closures
  New Year's Day (Jan 1; Sunday → Monday; a Saturday Jan 1 is NOT observed)
  Martin Luther King Jr. Day (3rd Monday of January)
  Presidents' Day (3rd Monday of February)
  Good Friday (Friday before Easter Sunday)
  Memorial Day (last Monday of May)
  Juneteenth (Jun 19, weekend → nearest weekday)
  Independence Day (Jul 4, weekend → nearest weekday)
  Labor Day (1st Monday of September)
  Thanksgiving (4th Thursday of November)
  Christmas (Dec 25, weekend → nearest weekday)

Early closes (13:00 ET)
  Day after Thanksgiving
  Christmas Eve (Dec 24) when it is a weekday and not itself a closure
  Jul 3 when it is a weekday and not itself a closure

Columbus Day and Veterans Day are bank holidays; the NYSE trades. Unscheduled
closures (national mourning, weather) are not knowable in advance and are not
modelled; the DATA_FRESH gate is the backstop for those.
"""

from __future__ import annotations

import datetime as _dt

EARLY_CLOSE = _dt.time(13, 0)


def easter_sunday(year: int) -> _dt.date:
    """Western (Gregorian) Easter via the anonymous algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return _dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """n-th occurrence (1-based) of ``weekday`` (Mon=0) in ``month``."""
    first = _dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + _dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> _dt.date:
    nxt = _dt.date(year + (month == 12), (month % 12) + 1, 1)
    last = nxt - _dt.timedelta(days=1)
    return last - _dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: _dt.date) -> _dt.date:
    """Weekend holiday → nearest weekday (Sat → Fri, Sun → Mon)."""
    if day.weekday() == 5:
        return day - _dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + _dt.timedelta(days=1)
    return day


def full_holidays(year: int) -> frozenset[_dt.date]:
    """Every NYSE full-closure date in ``year``."""
    days: set[_dt.date] = set()
    new_year = _dt.date(year, 1, 1)
    if new_year.weekday() == 6:
        days.add(new_year + _dt.timedelta(days=1))
    elif new_year.weekday() < 5:
        days.add(new_year)
    days.add(_nth_weekday(year, 1, 0, 3))
    days.add(_nth_weekday(year, 2, 0, 3))
    days.add(easter_sunday(year) - _dt.timedelta(days=2))
    days.add(_last_weekday(year, 5, 0))
    days.add(_observed(_dt.date(year, 6, 19)))
    days.add(_observed(_dt.date(year, 7, 4)))
    days.add(_nth_weekday(year, 9, 0, 1))
    days.add(_nth_weekday(year, 11, 3, 4))
    days.add(_observed(_dt.date(year, 12, 25)))
    return frozenset(days)


def is_full_holiday(day: _dt.date) -> bool:
    return day in full_holidays(day.year)


def is_trading_day(day: _dt.date) -> bool:
    return day.weekday() < 5 and not is_full_holiday(day)


def early_close(day: _dt.date) -> _dt.time | None:
    """13:00 ET when ``day`` is a scheduled early close, else None."""
    if not is_trading_day(day):
        return None
    year = day.year
    if day == _nth_weekday(year, 11, 3, 4) + _dt.timedelta(days=1):
        return EARLY_CLOSE
    if day in (_dt.date(year, 12, 24), _dt.date(year, 7, 3)):
        return EARLY_CLOSE
    return None


def session_close(day: _dt.date, regular_close: _dt.time) -> _dt.time:
    """The session's actual close: the early close when scheduled, else ``regular_close``."""
    early = early_close(day)
    return early if early is not None and early < regular_close else regular_close
