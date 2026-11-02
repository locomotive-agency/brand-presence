"""A monthly run uses the last complete calendar month in the configured time zone."""
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Period:
    name: str
    start: date


def new_run_id() -> str:
    return str(uuid.uuid4())


def period_start(name: str) -> date:
    if re.fullmatch(r"[0-9]{4}-W[0-9]{2}", name):
        return date.fromisocalendar(int(name[:4]), int(name[6:]), 1)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", name):
        return date.fromisoformat(name)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", name):
        raise ValueError("period must be YYYY-MM")
    return date.fromisoformat(name + "-01")


def period_for(at: datetime, time_zone: str) -> Period:
    local = at.astimezone(ZoneInfo(time_zone)).date()
    start = (local.replace(day=1) - timedelta(days=1)).replace(day=1)
    return Period(start.strftime("%Y-%m"), start)


def source_period(source, at, time_zone):
    if source == "search-volume":
        return period_for(at, time_zone)
    local = at.astimezone(ZoneInfo(time_zone)).date()
    if source == "mentions":
        return Period(local.isoformat(), local)
    if source == "peec":
        local -= timedelta(days=7)
    year, week, _ = local.isocalendar()
    return Period(f"{year}-W{week:02d}", date.fromisocalendar(year, week, 1))


MONTHLY, WEEKLY, DAILY = "monthly", "weekly", "daily"

def _week(day: date) -> Period:
    iso = day.isocalendar()
    return Period(f"{iso.year}-W{iso.week:02d}", date.fromisocalendar(iso.year, iso.week, 1))

def _month(day: date) -> Period:
    start = day.replace(day=1)
    return Period(start.strftime("%Y-%m"), start)

def _holding(frequency: str, day: date) -> Period:
    """The period of that frequency that holds a date."""
    if frequency == MONTHLY:
        return _month(day)
    if frequency == WEEKLY:
        return _week(day)
    return Period(day.isoformat(), day)

def _frequency_of(period: Period) -> str:
    if _MONTH.fullmatch(period.name):
        return MONTHLY
    if _WEEK.fullmatch(period.name):
        return WEEKLY
    return DAILY

def window_of(period: Period) -> tuple[date, date]:
    """The window of a period: its first and its last date, as calendar dates."""
    frequency = _frequency_of(period)
    if frequency == WEEKLY:
        return period.start, period.start + timedelta(days=6)
    if frequency == MONTHLY:
        following = (period.start + timedelta(days=31)).replace(day=1)
        return period.start, following - timedelta(days=1)
    return period.start, period.start

def periods_before(period: Period, count: int) -> list[Period]:
    """The `count` periods before a period, newest first."""
    frequency = _frequency_of(period)
    found, start = [], period.start
    for _ in range(max(0, count)):
        if frequency == MONTHLY:
            earlier = _month(start - timedelta(days=1))
        elif frequency == WEEKLY:
            earlier = _week(start - timedelta(days=7))
        else:
            day = start - timedelta(days=1)
            earlier = Period(day.isoformat(), day)
        found.append(earlier)
        start = earlier.start
    return found

_MONTH = re.compile(r"[0-9]{4}-[0-9]{2}")
_WEEK = re.compile(r"[0-9]{4}-W[0-9]{2}")
