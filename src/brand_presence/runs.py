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
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", name):
        raise ValueError("period must be YYYY-MM")
    return date.fromisoformat(name + "-01")


def period_for(at: datetime, time_zone: str) -> Period:
    local = at.astimezone(ZoneInfo(time_zone)).date()
    start = (local.replace(day=1) - timedelta(days=1)).replace(day=1)
    return Period(start.strftime("%Y-%m"), start)
