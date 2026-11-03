"""GA4 report field allow-list tests."""
from __future__ import annotations

import dataclasses
import json
import re
from datetime import date
from pathlib import Path

import pytest

from brand_presence.rows import UNKNOWN
from brand_presence.sources import ga4_report
from brand_presence.sources.ga4_report import (
    ENGAGEMENT,
    ENGAGEMENT_CITIES,
    LANDING_PAGES,
    NOT_SET,
    REPORTS,
    Report,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ga4_reports"
MONDAY, SUNDAY = date(2026, 2, 23), date(2026, 3, 1)


PERSONAL_WORDS = frozenset({
    "id", "audience", "cohort", "age", "gender", "interest", "interests", "city", "region",
    "metro", "continent", "latitude", "longitude", "page", "screen", "landing", "name",
    "path", "title", "url", "campaign", "device", "client", "hostname", "item",
    "transaction", "keyword", "term", "query", "location", "postal", "code",
})


NOT_PERSONAL = frozenset({"countryId", "screenPageViews", "landingPage", "hostName", "city",
                          "region"})
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")


def is_personal(name: str) -> bool:
    if ":" in name or name.lower().startswith("custom"):
        return True
    words = {word.lower() for word in _WORD.findall(name)}
    return name not in NOT_PERSONAL and bool(words & PERSONAL_WORDS)


def fixture(name: str) -> Report:
    held = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return Report(name=held["name"], dimensions=tuple(held["dimensions"]),
                  column=held["column"],
                  columns=tuple(tuple(pair) for pair in held["columns"]),
                  filter_by=held["filter_by"])


def personal(names) -> list[str]:
    return sorted(name for name in names if is_personal(name))


FIXTURE_REPORTS = [
    ("custom_user_dimension", "customUser:membership_tier", ENGAGEMENT),
    ("landing_page_query_string", "landingPagePlusQueryString", LANDING_PAGES),
    ("postal_code", "postalCode", ENGAGEMENT_CITIES),
    ("latitude", "latitude", ENGAGEMENT_CITIES),
]


@pytest.mark.parametrize("name, field, real", FIXTURE_REPORTS)
def test_a_fixture_report_with_a_field_off_the_list_fails_and_the_field_is_named(
    name, field, real
):
    report = fixture(name)

    assert report.name == real.name
    assert set(report.dimensions) - set(real.dimensions) == {field}
    assert (report.column, report.columns, report.filter_by) == (
        real.column, real.columns, real.filter_by)
    assert ga4_report.off_the_list(report) == [field]
    assert personal(report.dimensions) == [field]

    for hostname in (None, "shop.example"):
        with pytest.raises(ga4_report.NotAllowed) as caught:
            ga4_report.body(report, MONDAY, SUNDAY, hostname)
        assert caught.value.fields == [field] and field in str(caught.value)


def test_a_city_is_on_the_list_and_passes():


    report = fixture("city")
    assert ga4_report.off_the_list(report) == []
    assert ga4_report.body(report, MONDAY, SUNDAY, None)["dimensions"][-1] == {"name": "city"}


def test_a_metric_or_a_filter_off_the_list_fails_too():
    more = dataclasses.replace(
        ENGAGEMENT, columns=(*ENGAGEMENT.columns, ("customEvent:basket_value", "basket")))
    assert ga4_report.off_the_list(more) == ["customEvent:basket_value"]
    by_city = dataclasses.replace(LANDING_PAGES, filter_by="cityId")
    assert ga4_report.off_the_list(by_city) == ["cityId"]
    with pytest.raises(ga4_report.NotAllowed):
        ga4_report.body(by_city, MONDAY, SUNDAY, "shop.example")

    split = dataclasses.replace(ENGAGEMENT,
                                dimensions=("date", "countryId", "hostName"))
    assert ga4_report.off_the_list(split) == ["hostName"]


def test_the_reports_fields_are_all_on_the_list_and_the_list_holds_nothing_more():
    assert REPORTS == (ENGAGEMENT, LANDING_PAGES, ENGAGEMENT_CITIES)
    assert [ga4_report.off_the_list(report) for report in REPORTS] == [[], [], []]
    assert (ENGAGEMENT.name, LANDING_PAGES.name, ENGAGEMENT_CITIES.name) == (
        "engagement", "landing_pages", "engagement_cities")
    assert ENGAGEMENT.dimensions == ("date", "countryId", "sessionDefaultChannelGroup")
    assert LANDING_PAGES.dimensions == ("date", "countryId", "landingPage")
    assert ENGAGEMENT_CITIES.dimensions == ("date", "countryId", "region", "city")
    assert (ENGAGEMENT.column, LANDING_PAGES.column, ENGAGEMENT_CITIES.column) == (
        "channel_group", "landing_page", "city_id")
    for report in REPORTS:
        assert report.metrics == (
            "sessions", "engagedSessions", "totalUsers", "newUsers", "screenPageViews",
            "userEngagementDuration", "eventCount", "keyEvents",
        )
        assert report.filter_by == "hostName"

    assert ga4_report.ALLOWED_DIMENSIONS == {name for report in REPORTS
                                             for name in report.dimensions}
    assert ga4_report.ALLOWED_METRICS == set(ENGAGEMENT.metrics)
    assert ga4_report.ALLOWED_FILTERS == {"hostName"}


def test_the_list_holds_no_custom_field_and_nothing_personal():
    listed = (ga4_report.ALLOWED_DIMENSIONS | ga4_report.ALLOWED_METRICS
              | ga4_report.ALLOWED_FILTERS)
    assert not [name for name in listed if name.startswith(ga4_report.CUSTOM_PREFIXES)]
    assert not [name for name in listed if ":" in name]
    assert personal(listed) == []

    for name in ("customUser:tier", "customEvent:plan", "userId", "audienceName",
                 "cohortNthDay", "userAgeBracket", "userGender", "brandingInterest",
                 "cityId", "postalCode", "latitude", "longitude", "pagePath",
                 "unifiedScreenName",
                 "landingPagePlusQueryString", "pageLocation", "pageTitle", "eventName",
                 "sessionCampaignName", "deviceCategory", "pageViewsById"):
        assert personal([name]) == [name]


    assert personal(ga4_report.REFUSED) == sorted(ga4_report.REFUSED)
    assert not ga4_report.REFUSED & listed

    assert NOT_PERSONAL <= listed
    assert {name for name in listed
            if {w.lower() for w in _WORD.findall(name)} & PERSONAL_WORDS} == NOT_PERSONAL
    assert "customUser:" in ga4_report.CUSTOM_PREFIXES


def test_a_request_holds_its_report_and_one_window_and_every_country():
    for report in REPORTS:
        body = ga4_report.body(report, MONDAY, SUNDAY, None)
        assert body == {
            "dateRanges": [{"startDate": "2026-02-23", "endDate": "2026-03-01"}],
            "dimensions": [{"name": name} for name in report.dimensions],
            "metrics": [{"name": name} for name in report.metrics],
        }


def test_a_hostname_is_the_one_filter_and_only_where_one_is_given():
    for report in REPORTS:
        plain = ga4_report.body(report, MONDAY, SUNDAY, None)
        body = ga4_report.body(report, MONDAY, SUNDAY, "shop.example")
        assert body["dimensionFilter"] == {"filter": {"fieldName": "hostName", "stringFilter": {
            "matchType": "EXACT", "value": "shop.example", "caseSensitive": False}}}
        assert {k: v for k, v in body.items() if k != "dimensionFilter"} == plain


def test_the_country_of_a_row_is_its_code_unknown_or_none():
    assert ga4_report.country_of("NL") == "NL"
    assert ga4_report.country_of(NOT_SET) == UNKNOWN

    for wrong in ("QZ", "", "nl", "NLD", None, 7):
        assert ga4_report.country_of(wrong) is None
