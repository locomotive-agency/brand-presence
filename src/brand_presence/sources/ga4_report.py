"""GA4 API aggregates and their fixed report definitions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from brand_presence import countries
from brand_presence.rows import UNKNOWN

NOT_SET = "(not set)"


ALLOWED_DIMENSIONS = frozenset({"date", "countryId", "sessionDefaultChannelGroup",
                                "landingPage", "region", "city"})

ALLOWED_METRICS = frozenset({
    "sessions", "engagedSessions", "totalUsers", "newUsers", "screenPageViews",
    "userEngagementDuration", "eventCount", "keyEvents",
})


ALLOWED_FILTERS = frozenset({"hostName"})


REFUSED = frozenset({
    "landingPagePlusQueryString", "pagePathPlusQueryString", "pageLocation", "pageTitle",
    "eventName", "campaignId", "campaignName", "sessionCampaignId", "sessionCampaignName",
    "firstUserCampaignId", "firstUserCampaignName",

    "postalCode", "latitude", "longitude",
})


CUSTOM_PREFIXES = ("customUser:", "customEvent:", "customItem:", "customSession:")


@dataclass(frozen=True)
class Report:
    name: str
    dimensions: tuple[str, ...]
    column: str
    columns: tuple[tuple[str, str], ...]


    filter_by: str

    @property
    def metrics(self) -> tuple[str, ...]:
        return tuple(metric for metric, _ in self.columns)


METRIC_COLUMNS = (
    ("sessions", "sessions"),
    ("engagedSessions", "engaged_sessions"),
    ("totalUsers", "total_users"),
    ("newUsers", "new_users"),
    ("screenPageViews", "page_views"),

    ("userEngagementDuration", "engagement_seconds"),
    ("eventCount", "event_count"),
    ("keyEvents", "key_events"),
)

ENGAGEMENT = Report(
    name="engagement",
    dimensions=("date", "countryId", "sessionDefaultChannelGroup"),
    column="channel_group",
    columns=METRIC_COLUMNS,
    filter_by="hostName",
)

LANDING_PAGES = Report(
    name="landing_pages",
    dimensions=("date", "countryId", "landingPage"),
    column="landing_page",
    columns=METRIC_COLUMNS,
    filter_by="hostName",
)


ENGAGEMENT_CITIES = Report(
    name="engagement_cities",
    dimensions=("date", "countryId", "region", "city"),
    column="city_id",
    columns=METRIC_COLUMNS,
    filter_by="hostName",
)

REPORTS = (ENGAGEMENT, LANDING_PAGES, ENGAGEMENT_CITIES)


class NotAllowed(ValueError):
    def __init__(self, fields: list[str]):
        super().__init__("not on the GA4 allow-list: " + ", ".join(fields))
        self.fields = fields


def off_the_list(report: Report) -> list[str]:
    found = [name for name in report.dimensions if name not in ALLOWED_DIMENSIONS]
    found += [name for name in report.metrics if name not in ALLOWED_METRICS]
    if report.filter_by not in ALLOWED_FILTERS:
        found.append(report.filter_by)
    return list(dict.fromkeys(found))


def country_of(value: object) -> str | None:
    if value == NOT_SET:
        return UNKNOWN
    if isinstance(value, str) and countries.known(value):
        return value
    return None


def body(report: Report, first: date, last: date, hostname: str | None) -> dict:
    wrong = off_the_list(report)
    if wrong:
        raise NotAllowed(wrong)
    asked: dict = {
        "dateRanges": [{"startDate": first.isoformat(), "endDate": last.isoformat()}],
        "dimensions": [{"name": name} for name in report.dimensions],
        "metrics": [{"name": name} for name in report.metrics],
    }
    if hostname is not None:
        asked["dimensionFilter"] = {"filter": {
            "fieldName": report.filter_by,
            "stringFilter": {"matchType": "EXACT", "value": hostname, "caseSensitive": False},
        }}
    return asked
