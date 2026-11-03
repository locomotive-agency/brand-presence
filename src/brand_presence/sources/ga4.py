"""GA4 API aggregates and their fixed report definitions."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from pydantic import field_validator

from brand_presence import google_token, status
from brand_presence.client_ref import ClientRef, Location
from brand_presence.rows import ALL, OTHER_CITY, CleanRow, LedgerRow, PullRow, RawRow
from brand_presence.runs import Period, window_of
from brand_presence.sources import ga4_report
from brand_presence.sources.base import (
    WINDOW,
    Answer,
    Page,
    Request,
    SendContext,
    Source,
    Target,
    Verdict,
    WindowSend,
    in_city,
    in_country,
    request_of,
    standard_columns,
)
from brand_presence.sources.ga4_report import (
    ENGAGEMENT,
    ENGAGEMENT_CITIES,
    LANDING_PAGES,
    METRIC_COLUMNS,
    Report,
)
from brand_presence.vendors import ga4, google_errors

CITY, COUNTRY_KIND = "city", "country"
ROWS = "rows"

PAGE_ROWS = 100_000
MAX_PAGES = 10


HEADERS = status.HEADERS
RESTRICTED = status.RESTRICTED
OUTSIDE = status.OUTSIDE_WINDOW
REPEATED = status.ROW_REPEATED
COUNTRY = status.COUNTRY_NOT_IN_TABLE
QUERY_STRING = status.QUERY_STRING
METADATA_MISSING = status.METADATA_MISSING
ROW_COUNT_MISSING = status.ROW_COUNT_MISSING


EMPTY_REASONS = ("NO_DATA",)
OTHER_REASON = "other"


CUT = "dataCut"
CUT_START, CUT_END = "startDate", "endDate"


_WHOLE_DIGITS, _DECIMAL_PLACES = 29, 9


def _fits_the_column(value: Decimal) -> Decimal:
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("not a finite number")
    if -exponent > _DECIMAL_PLACES or len(digits) + exponent > _WHOLE_DIGITS:
        raise ValueError("more digits than the column holds")
    return value


_FIGURES = tuple(column for _, column in METRIC_COLUMNS)


class Ga4Raw(RawRow):
    table: ClassVar[str] = "raw.ga4"


class Ga4Engagement(CleanRow):
    table: ClassVar[str] = "stg.ga4_engagement"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "web_property_id", "country_code", "date", "channel_group",
        "period",
    )

    web_property_id: str

    date: date
    channel_group: str
    sessions: Decimal
    engaged_sessions: Decimal
    total_users: Decimal
    new_users: Decimal
    page_views: Decimal

    engagement_seconds: Decimal
    event_count: Decimal
    key_events: Decimal

    @field_validator(*_FIGURES)
    @classmethod
    def _fits(cls, value: Decimal) -> Decimal:
        return _fits_the_column(value)


class Ga4LandingPage(CleanRow):
    table: ClassVar[str] = "stg.ga4_landing_pages"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "web_property_id", "country_code", "date", "landing_page",
        "period",
    )

    web_property_id: str

    date: date
    landing_page: str
    sessions: Decimal
    engaged_sessions: Decimal
    total_users: Decimal
    new_users: Decimal
    page_views: Decimal

    engagement_seconds: Decimal
    event_count: Decimal
    key_events: Decimal

    @field_validator(*_FIGURES)
    @classmethod
    def _fits(cls, value: Decimal) -> Decimal:
        return _fits_the_column(value)


class Ga4EngagementCity(CleanRow):
    table: ClassVar[str] = "stg.ga4_engagement_cities"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "web_property_id", "country_code", "city_id", "date", "period",
    )

    web_property_id: str

    date: date
    city_id: str
    sessions: Decimal
    engaged_sessions: Decimal
    total_users: Decimal | None
    new_users: Decimal | None
    page_views: Decimal

    engagement_seconds: Decimal
    event_count: Decimal
    key_events: Decimal

    @field_validator(*_FIGURES)
    @classmethod
    def _fits(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _fits_the_column(value)


MODELS: dict[str, type[CleanRow]] = {
    ENGAGEMENT.name: Ga4Engagement, LANDING_PAGES.name: Ga4LandingPage,
    ENGAGEMENT_CITIES.name: Ga4EngagementCity,
}

USER_COUNTS = ("total_users", "new_users")


class Ga4Pull(PullRow):
    table: ClassVar[str] = "stg.ga4_pulls"
    natural_key: ClassVar[tuple[str, ...]] = ("web_property_id", "report", "period")

    web_property_id: str
    report: str


    sampled: bool | None
    samples_read: int | None
    sampling_space: int | None

    other_row: bool | None
    thresholded: bool | None

    rows_reported: int | None
    tokens_used: int | None
    tokens_left_day: int | None
    tokens_left_hour: int | None
    data_cut: bool | None
    cut_start: date | None
    cut_end: date | None

    empty_reason: str | None


    listed_cities_unmatched: int | None = None




    listed_countries_unmatched: int | None = None


    city_days_ambiguous: int | None = None



    def is_sampled(self) -> bool:
        return self.sampled is True


class Ga4Task(LedgerRow):
    table: ClassVar[str] = "ops.ga4_tasks"


def _pages(response: Mapping[str, object]) -> list[dict]:
    if "parts" in response:
        return list(response["parts"][ROWS])
    return [response]


def _whole(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _day(value: object, form: str = "%Y%m%d") -> date | None:
    try:
        return datetime.strptime(value, form).date()
    except (TypeError, ValueError):
        return None


def _figure(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return Decimal(value)
    except InvalidOperation:
        return value


def _metadata(page: Mapping[str, object]) -> Mapping[str, object]:
    held = page.get("metadata")
    return held if isinstance(held, Mapping) else {}


def _quota(page: Mapping[str, object], bucket: str, field: str) -> int | None:
    quota = page.get("propertyQuota")
    held = quota.get(bucket) if isinstance(quota, Mapping) else None
    return _whole(held.get(field)) if isinstance(held, Mapping) else None


def tokens_used(page: Mapping[str, object]) -> int | None:
    return _quota(page, "tokensPerDay", "consumed")


def _names(headers: object) -> list[object]:
    return [h.get("name") if isinstance(h, Mapping) else None for h in headers or []]


def _page_rule(page: Mapping[str, object], report: Report | None) -> str | None:
    restricted = _metadata(page).get("schemaRestrictionResponse")
    if isinstance(restricted, Mapping) and restricted.get("activeMetricRestrictions"):
        return RESTRICTED


    if report is None or _names(page.get("dimensionHeaders")) != list(report.dimensions) \
            or _names(page.get("metricHeaders")) != list(report.metrics):
        return HEADERS
    for row in page.get("rows") or []:
        if len(row["dimensionValues"]) != len(report.dimensions) \
                or len(row["metricValues"]) != len(report.metrics):
            return HEADERS
    if not isinstance(page.get("metadata"), Mapping):
        return METADATA_MISSING
    if page.get("rows") and _whole(page.get("rowCount")) is None:
        return ROW_COUNT_MISSING


    page_at = report.dimensions.index("landingPage") if "landingPage" in report.dimensions \
        else None
    for row in page.get("rows") or []:
        values = [value.get("value") for value in row["dimensionValues"]]


        if ga4_report.country_of(values[1]) is None:
            return COUNTRY
        split = None if page_at is None else values[page_at]
        if isinstance(split, str) and "?" in split:
            return QUERY_STRING
    return None


class Ga4Send(WindowSend):
    parts = (ROWS,)


    max_pages = MAX_PAGES

    def __init__(self, context: SendContext, reports: Mapping[str, Report], token):
        self.context = context
        self.reports = reports
        self.token = token

    def pull(self, request: Request, part: str, page: int) -> Page:
        body = request.body
        asked = ga4_report.body(
            self.reports[body["report"]], date.fromisoformat(body["first_date"]),
            date.fromisoformat(body["last_date"]), body["hostname"],
        )
        offset = (page - 1) * PAGE_ROWS
        said = ga4.run_report(self.context.http, self.token, body["address"], asked,
                              limit=PAGE_ROWS, offset=offset, sleep=self.context.sleep)


        count = _whole(said.get("rowCount"))
        more = bool(said.get("rows")) and count is not None and offset + PAGE_ROWS < count


        return Page(Answer(said, content_date=date.fromisoformat(body["first_date"])),
                    cursor=None, more=more and page < MAX_PAGES)


class Ga4(Source):
    name = "ga4"
    shape = WINDOW
    durable_pages = True
    history_periods = 104
    history_requests = 500
    ends_run = ()
    raw_model = Ga4Raw
    clean_models = (Ga4Engagement, Ga4LandingPage, Ga4EngagementCity, Ga4Pull)
    ledger_model = Ga4Task


    identity_fields: ClassVar[tuple[str, ...]] = ("web_property_id", "report")


    refusals: ClassVar[tuple[str, ...]] = (google_errors.ACCESS, google_errors.LIMIT,
                                           google_errors.REFUSAL_NOT_KNOWN,
                                           )
    scope = google_token.ANALYTICS_READ_ONLY


    reports: ClassVar[tuple[Report, ...]] = ga4_report.REPORTS

    def listed_cities(self, settings: ClientRef) -> tuple[Location, ...]:
        return tuple(item for item in settings.listed_locations
                     if item.kind == CITY and self.name in item.sources)

    def reports_for(self, settings: ClientRef) -> tuple[Report, ...]:
        cities = bool(self.listed_cities(settings))
        return tuple(report for report in self.reports
                     if report is not ENGAGEMENT_CITIES or cities)

    def plan(self, settings: ClientRef, period: Period) -> list[Request]:
        first, last = window_of(period)
        requests = []
        for web_property in settings.web_properties_of(self.name):
            for report in self.reports_for(settings):
                requests.append(Request(
                    {"web_property_id": web_property.web_property_id,
                     "report": report.name, "address": web_property.address,

                     "hostname": web_property.hostname,
                     "first_date": first.isoformat(), "last_date": last.isoformat()},

                    (Target(web_property.client_brand_id, ALL, ALL),),
                ))
        return requests

    def _report(self, name: object) -> Report | None:
        return next((report for report in self.reports if report.name == name), None)

    def group(self, request: Request) -> str:
        return request.body["web_property_id"]

    def window(self, request: Request) -> tuple[date, date]:
        return (date.fromisoformat(request.body["first_date"]),
                date.fromisoformat(request.body["last_date"]))

    def send(self, context: SendContext) -> Ga4Send:
        return Ga4Send(context, {report.name: report for report in self.reports},
                       google_token.reader(Ga4.scope, context.tokens))

    def usable(self, answer: Answer) -> Verdict:
        for page in _pages(answer.response):
            named = _names(page.get("dimensionHeaders"))
            report = next((r for r in self.reports if list(r.dimensions) == named), None)
            rule = _page_rule(page, report)
            if rule is not None:
                return Verdict(False, rule)
        return Verdict(True)

    def complete(self, request: Request, answer: Answer) -> Verdict:
        verdict = self.usable(answer)
        if not verdict.usable:
            return verdict
        report = self._report(request.body["report"])
        for page in _pages(answer.response):
            rule = _page_rule(page, report)
            if rule is not None:
                return Verdict(False, rule)
        first, last = self.window(request)

        seen: set[tuple[str, ...]] = set()
        for page in _pages(answer.response):
            for row in page.get("rows") or []:
                values = tuple(value["value"] for value in row["dimensionValues"])
                read = _day(values[0])

                if read is not None and not first <= read <= last:
                    return Verdict(False, OUTSIDE)
                if values in seen:
                    return Verdict(False, REPEATED)
                seen.add(values)
        return Verdict(True)

    def parse(self, raw: RawRow, settings: ClientRef) -> list[CleanRow]:
        request = request_of(raw.request)
        pages = json.loads(raw.response)["parts"][ROWS]
        target, = request.targets
        standard = standard_columns(raw, settings, target)
        report = self._report(request.body["report"])
        model = MODELS[report.name]
        web_property = request.body["web_property_id"]
        if report is ENGAGEMENT_CITIES:
            rows, unmatched, ambiguous = self._cities(standard, settings, web_property, pages)
            read = sum(len(page.get("rows") or []) for page in pages)
            return [*rows, self._pull_row(standard, request, pages, len(rows), read=read,
                                          unmatched=unmatched, ambiguous=ambiguous)]
        rows: list[CleanRow] = []
        for page in pages:
            for row in page.get("rows") or []:
                day, country, split = (value["value"] for value in row["dimensionValues"])
                figures = {column: _figure(value["value"]) for (_, column), value
                           in zip(report.columns, row["metricValues"], strict=True)}

                placed = in_country(standard, settings, ga4_report.country_of(country))
                rows.append(model(
                    **placed, web_property_id=web_property,
                    date=_day(day) or day, **{report.column: split}, **figures,
                ))
        countries = None
        if report is ENGAGEMENT:
            held = {row.country_code for row in rows}
            countries = sum(1 for item in settings.listed_locations
                            if item.kind == COUNTRY_KIND and item.country_code not in held)
        rows.append(self._pull_row(standard, request, pages, len(rows),
                                   countries=countries))
        return rows

    def _cities(self, standard: dict, settings: ClientRef, web_property: str,
                pages: list[dict]) -> tuple[list[CleanRow], int, int]:
        listed = self.listed_cities(settings)
        named = {item.location_id: _city_name(item) for item in listed}
        answered = [
            (values, [_figure(value["value"]) for value in row["metricValues"]])
            for page in pages for row in page.get("rows") or []
            for values in [tuple(value["value"] for value in row["dimensionValues"])]
        ]

        matched: dict[int, Location] = {}
        found: set[str] = set()
        ambiguous = 0
        for day in dict.fromkeys(values[0] for values, _ in answered):
            for item in listed:
                city, region = named[item.location_id]
                fits = [at for at, (values, _) in enumerate(answered)
                        if values[0] == day and values[1] == item.country_code
                        and values[3].strip().lower() == city and at not in matched]
                if len(fits) > 1 and region is not None:
                    fits = [at for at in fits if answered[at][0][2].strip().lower() == region]
                if len(fits) == 1:
                    matched[fits[0]] = item
                    found.add(item.location_id)
                elif len(fits) > 1:
                    ambiguous += 1
        rows: list[CleanRow] = []
        folded: dict[tuple[str, str], list[list[object]]] = {}
        for at, (values, figures) in enumerate(answered):
            day, country = values[0], ga4_report.country_of(values[1])
            if at in matched:
                item = matched[at]
                rows.append(Ga4EngagementCity(
                    **in_city(standard, settings, country, item.location_id),
                    web_property_id=web_property, date=_day(day) or day,
                    city_id=item.location_id,
                    **{column: value for (_, column), value
                       in zip(ENGAGEMENT_CITIES.columns, figures, strict=True)}))
            else:
                folded.setdefault((day, country), []).append(figures)
        for (day, country), held in folded.items():
            sums = {column: None if column in USER_COUNTS else _sum([f[at] for f in held])
                    for at, (_, column) in enumerate(ENGAGEMENT_CITIES.columns)}
            rows.append(Ga4EngagementCity(
                **in_city(standard, settings, country, None),
                web_property_id=web_property, date=_day(day) or day, city_id=OTHER_CITY,
                **sums))
        countries = {ga4_report.country_of(values[1]) for values, _ in answered}
        unmatched = sum(1 for item in listed
                        if item.country_code in countries and item.location_id not in found)
        return rows, unmatched, ambiguous

    def _pull_row(self, standard: dict, request: Request, pages: list[dict],
                  written: int, *, read: int | None = None, unmatched: int | None = None,
                  ambiguous: int | None = None, countries: int | None = None) -> Ga4Pull:
        read = written if read is None else read
        said = [_metadata(page) for page in pages]
        sampling = next((entry for held in said
                         for entry in held.get("samplingMetadatas") or []
                         if isinstance(entry, Mapping)), None)
        cut = next((held[CUT] for held in said if held.get(CUT)), None)
        reason = next((held["emptyReason"] for held in said if held.get("emptyReason")), None)
        reported = next((_whole(page.get("rowCount")) for page in reversed(pages)
                         if page.get("rowCount") is not None), None)
        used = [tokens for tokens in map(tokens_used, pages) if tokens is not None]
        other = any(held.get("dataLossFromOtherRow") is True for held in said)
        first, last = self.window(request)
        return Ga4Pull(
            **standard, web_property_id=request.body["web_property_id"],
            report=request.body["report"], window_start=first, window_end=last,
            rows_written=written, pages=len(pages),

            row_limit_hit=bool((reported is not None and reported > read) or other or cut),
            source_time_zone=next((held["timeZone"] for held in said
                                   if isinstance(held.get("timeZone"), str)), None),
            sampled=sampling is not None,
            samples_read=None if sampling is None else _whole(sampling.get("samplesReadCount")),
            sampling_space=None if sampling is None else _whole(
                sampling.get("samplingSpaceSize")),
            other_row=other,
            thresholded=any(held.get("subjectToThresholding") is True for held in said),
            rows_reported=reported,
            tokens_used=sum(used) if used else None,
            tokens_left_day=next((left for left in (
                _quota(page, "tokensPerDay", "remaining") for page in reversed(pages)
            ) if left is not None), None),
            tokens_left_hour=next((left for left in (
                _quota(page, "tokensPerHour", "remaining") for page in reversed(pages)
            ) if left is not None), None),

            data_cut=True if cut is not None else None,
            cut_start=_day(cut.get(CUT_START), "%Y-%m-%d") if isinstance(cut, Mapping) else None,
            cut_end=_day(cut.get(CUT_END), "%Y-%m-%d") if isinstance(cut, Mapping) else None,
            empty_reason=None if reason is None else (
                reason if reason in EMPTY_REASONS else OTHER_REASON),
            listed_cities_unmatched=unmatched,
            city_days_ambiguous=ambiguous,
            listed_countries_unmatched=countries,
        )


def _city_name(item: Location) -> tuple[str, str | None]:
    parts = tuple(part.strip().lower() for part in item.canonical_name.split(","))
    return parts[0], parts[1] if len(parts) == 3 else None


def _sum(figures: list[object]) -> object:
    bad = next((value for value in figures if not isinstance(value, Decimal)), None)
    return bad if bad is not None else sum(figures, Decimal(0))
