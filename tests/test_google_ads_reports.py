"""Offline Google Ads boundary tests."""
from __future__ import annotations

from datetime import date

import pytest

from brand_presence.sources import google_ads_reports as reports
from brand_presence.sources.google_ads_reports import DETAILS, REPORTS, Report


def test_the_six_reports_and_what_each_reads():
    assert set(REPORTS) == {reports.CAMPAIGNS, reports.GEOGRAPHY, reports.KEYWORDS,
                            reports.SEARCH_TERMS, reports.GEOGRAPHY_CITIES,
                            reports.GEOGRAPHY_REGIONS}
    assert {name: report.resource for name, report in REPORTS.items()} == {
        "campaigns": "campaign", "geography": "geographic_view", "keywords": "keyword_view",
        "search_terms": "search_term_view", "geography_cities": "geographic_view",
        "geography_regions": "geographic_view"}


    for name, segment in (("geography_cities", "segments.geo_target_city"),
                          ("geography_regions", "segments.geo_target_region")):
        places = list(REPORTS[name].fields)
        places.remove(segment)
        assert places == list(REPORTS["geography"].fields), name

    assert [name for name, report in REPORTS.items()
            if any("search_term" in field for field in report.fields)] == ["search_terms"]
    for report in (*REPORTS.values(), DETAILS):
        assert reports.personal(report) == []
        assert report.fields[:2] == ("customer.id", "customer.currency_code")
    assert reports.ALWAYS == ("campaigns", "geography", "keywords", "search_terms")


def test_a_query_names_every_field_and_the_window():
    asked = reports.query_of(REPORTS["geography"], date(2026, 9, 28), date(2026, 10, 4))
    assert asked == (
        "SELECT customer.id, customer.currency_code, campaign.id, "
        "geographic_view.country_criterion_id, geographic_view.location_type, segments.date, "
        "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
        "FROM geographic_view WHERE segments.date BETWEEN '2026-09-28' AND '2026-10-04'")
    assert reports.query_of(DETAILS) == (
        "SELECT customer.id, customer.currency_code, customer.time_zone FROM customer")


PERSONAL = {
    "a search term": Report("terms", "search_term_view", (
        "customer.id", "search_term_view.search_term", "metrics.clicks")),
    "a search term from another resource": Report("terms", "campaign", (
        "customer.id", "segments.search_term_match_type", "metrics.clicks")),
    "a user list": Report("lists", "campaign", (
        "customer.id", "user_list.name", "metrics.clicks")),
    "an audience": Report("audiences", "campaign", (
        "customer.id", "campaign_audience_view.resource_name", "metrics.clicks")),
    "a customer match list": Report("match", "campaign", (
        "customer.id", "customer_match_user_list_metadata.user_list", "metrics.clicks")),
    "a postal code": Report("places", "geographic_view", (
        "customer.id", "segments.geo_target_postal_code", "metrics.clicks")),
    "a city on the user's location view": Report("places", "user_location_view", (
        "customer.id", "segments.geo_target_city", "metrics.clicks")),
    "the most specific location": Report("places", "geographic_view", (
        "customer.id", "segments.geo_target_most_specific_location", "metrics.clicks")),
    "a metro area": Report("places", "geographic_view", (
        "customer.id", "segments.geo_target_metro", "metrics.clicks")),
    "a city's criterion on the user's location": Report("places", "geographic_view", (
        "customer.id", "user_location_view.city_criterion_id", "metrics.clicks")),
    "a billing detail": Report("billing", "campaign", (
        "customer.id", "billing_setup.payments_account", "metrics.clicks")),


    "a search term in the term report's name on another resource": Report(
        "search_terms", "campaign", (
            "customer.id", "search_term_view.search_term", "metrics.clicks")),
    "a search term's match type in the term report": Report(
        "search_terms", "search_term_view", (
            "customer.id", "search_term_view.search_term",
            "segments.search_term_match_type", "metrics.clicks")),
    "a state segment": Report("places", "geographic_view", (
        "customer.id", "segments.geo_target_state", "metrics.clicks")),
}


@pytest.mark.parametrize("report", PERSONAL.values(), ids=PERSONAL.keys())
def test_a_report_that_asks_for_something_personal_fails(report):


    assert reports.personal(report) != [] or any(
        part in report.resource for part in reports.FORBIDDEN)
    with pytest.raises(ValueError):
        reports.query_of(report, date(2026, 9, 28), date(2026, 10, 4))


def test_the_city_is_allowed_and_nothing_finer():

    city = Report("places", "geographic_view", (
        "customer.id", "geographic_view.country_criterion_id", "segments.geo_target_city",
        "metrics.clicks"))
    assert reports.personal(city) == []
    assert "segments.geo_target_city" in reports.query_of(city, date(2026, 9, 28),
                                                          date(2026, 10, 4))
    for name in ("the most specific location", "a metro area",
                 "a city on the user's location view", "a postal code"):
        report = PERSONAL[name]
        refused = reports.personal(report) or [report.resource]
        assert refused and refused[0] in (*report.fields, report.resource), name


def test_the_region_is_allowed_and_the_search_term_only_in_its_report():


    region = Report("places", "geographic_view", (
        "customer.id", "geographic_view.country_criterion_id", "segments.geo_target_region",
        "metrics.clicks"))
    assert reports.personal(region) == []
    assert "segments.geo_target_region" in reports.query_of(region, date(2026, 9, 28),
                                                            date(2026, 10, 4))
    terms = REPORTS["search_terms"]
    assert reports.personal(terms) == []
    assert reports.query_of(terms, date(2026, 9, 28), date(2026, 10, 4)).startswith(
        "SELECT customer.id, customer.currency_code, campaign.id, ad_group.id, "
        "search_term_view.search_term, segments.date, ")

    renamed = Report("terms", terms.resource, terms.fields)
    assert reports.personal(renamed) == ["search_term_view.search_term"]
    with pytest.raises(ValueError):
        reports.query_of(renamed)


@pytest.mark.parametrize("resource", ["search_term_view", "user_list", "billing_setup",
                                      "campaign; DROP", "Campaign"])
def test_a_report_that_reads_a_personal_or_odd_resource_fails(resource):
    report = Report("odd", resource, ("customer.id", "metrics.clicks"))
    with pytest.raises(ValueError):
        reports.query_of(report)


def test_a_field_that_is_no_field_name_fails():
    report = Report("odd", "campaign", ("customer.id", "metrics.clicks FROM user_list"))
    assert reports.personal(report) == ["metrics.clicks FROM user_list"]


def test_where_a_field_is_in_an_answer():
    assert reports.path_of("metrics.cost_micros") == ("metrics", "costMicros")
    assert reports.path_of("ad_group_criterion.keyword.match_type") == (
        "adGroupCriterion", "keyword", "matchType")
    row = {"metrics": {"costMicros": "5", "clicks": "0"}}
    assert reports.value_of(row, "metrics.cost_micros") == "5"
    assert reports.value_of(row, "metrics.clicks") == "0"
    assert reports.value_of(row, "metrics.impressions") is reports.ABSENT
    assert reports.value_of({"metrics": 3}, "metrics.clicks") is reports.ABSENT
    assert reports.value_of(None, "metrics.clicks") is reports.ABSENT
