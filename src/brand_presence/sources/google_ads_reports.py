"""Google Ads read reports and account validation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

CAMPAIGNS = "campaigns"
GEOGRAPHY = "geography"
KEYWORDS = "keywords"
SEARCH_TERMS = "search_terms"
GEOGRAPHY_CITIES = "geography_cities"
GEOGRAPHY_REGIONS = "geography_regions"


@dataclass(frozen=True)
class Report:
    name: str
    resource: str
    fields: tuple[str, ...]


_WHOSE = ("customer.id", "customer.currency_code")

REPORTS: dict[str, Report] = {report.name: report for report in (
    Report(CAMPAIGNS, "campaign", (
        *_WHOSE,
        "campaign.id",
        "campaign.name",
        "campaign.advertising_channel_type",
        "campaign.status",
        "segments.date",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.cost_micros",
        "metrics.conversions",
        "metrics.conversions_by_conversion_date",
        "metrics.search_impression_share",


        "metrics.absolute_top_impression_percentage",
        "metrics.search_absolute_top_impression_share",
        "metrics.search_rank_lost_impression_share",
        "metrics.search_budget_lost_impression_share",
    )),
    Report(GEOGRAPHY, "geographic_view", (
        *_WHOSE,
        "campaign.id",
        "geographic_view.country_criterion_id",
        "geographic_view.location_type",
        "segments.date",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.cost_micros",
        "metrics.conversions",
    )),
    Report(KEYWORDS, "keyword_view", (
        *_WHOSE,
        "campaign.id",
        "ad_group.id",
        "ad_group_criterion.criterion_id",
        "ad_group_criterion.keyword.text",
        "ad_group_criterion.keyword.match_type",
        "segments.date",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.cost_micros",
        "metrics.conversions",
        "metrics.search_impression_share",
    )),


    Report(SEARCH_TERMS, "search_term_view", (
        *_WHOSE,
        "campaign.id",
        "ad_group.id",
        "search_term_view.search_term",
        "segments.date",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.cost_micros",
        "metrics.conversions",
    )),


    Report(GEOGRAPHY_CITIES, "geographic_view", (
        *_WHOSE,
        "campaign.id",
        "geographic_view.country_criterion_id",
        "geographic_view.location_type",
        "segments.geo_target_city",
        "segments.date",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.cost_micros",
        "metrics.conversions",
    )),


    Report(GEOGRAPHY_REGIONS, "geographic_view", (
        *_WHOSE,
        "campaign.id",
        "geographic_view.country_criterion_id",
        "geographic_view.location_type",
        "segments.geo_target_region",
        "segments.date",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.cost_micros",
        "metrics.conversions",
    )),
)}


ALWAYS = (CAMPAIGNS, GEOGRAPHY, KEYWORDS, SEARCH_TERMS)


DETAILS = Report("details", "customer", (
    "customer.id", "customer.currency_code", "customer.time_zone",
))


FORBIDDEN = (
    "search_term", "user_list", "audience", "customer_match", "user_interest",
    "postal_code", "geo_target_metro",
    "geo_target_most_specific_location", "geo_target_county", "geo_target_district",
    "geo_target_airport", "geo_target_canton", "geo_target_province", "geo_target_state",
    "user_location", "city_criterion_id", "metro_criterion_id", "region_criterion_id",
    "most_specific_criterion_id", "billing", "payment", "invoice",
)


TERM_REPORT = (SEARCH_TERMS, "search_term_view")
TERM_FIELD = "search_term_view.search_term"


_FIELD = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _term_report(report: Report) -> bool:
    return (report.name, report.resource) == TERM_REPORT


def personal(report: Report) -> list[str]:
    return [field for field in report.fields
            if not _FIELD.match(field) or (
                any(part in field for part in FORBIDDEN)
                and not (field == TERM_FIELD and _term_report(report)))]


def query_of(report: Report, first: date | None = None, last: date | None = None) -> str:
    if personal(report) or not re.fullmatch(r"[a-z][a-z_]*", report.resource) \
            or (any(part in report.resource for part in FORBIDDEN)
                and not _term_report(report)):
        raise ValueError(f"the report {report.name} asks for a field it may not")
    asked = f"SELECT {', '.join(report.fields)} FROM {report.resource}"
    if first is not None and last is not None:
        asked += (f" WHERE segments.date BETWEEN '{first.isoformat()}' "
                  f"AND '{last.isoformat()}'")
    return asked


def _camel(word: str) -> str:
    head, *rest = word.split("_")
    return head + "".join(part.capitalize() for part in rest)


def path_of(field: str) -> tuple[str, ...]:
    return tuple(_camel(part) for part in field.split("."))


_ABSENT = object()


def value_of(row: object, field: str) -> object:
    held = row
    for step in path_of(field):
        if not isinstance(held, dict) or step not in held:
            return ABSENT
        held = held[step]
    return held


ABSENT = _ABSENT
