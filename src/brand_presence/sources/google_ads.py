"""Google Ads read reports and account validation."""
from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from typing import ClassVar

from brand_presence import countries, status
from brand_presence.client_ref import ClientRef, InconsistentRef, Location
from brand_presence.http import VendorError
from brand_presence.rows import (
    ALL,
    OTHER_CITY,
    OTHER_REGION,
    UNKNOWN,
    CleanRow,
    LedgerRow,
    PullRow,
    RawRow,
)
from brand_presence.runs import Period, window_of
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
from brand_presence.sources.google_ads_reports import (
    ABSENT,
    ALWAYS,
    CAMPAIGNS,
    DETAILS,
    GEOGRAPHY,
    GEOGRAPHY_CITIES,
    GEOGRAPHY_REGIONS,
    KEYWORDS,
    REPORTS,
    SEARCH_TERMS,
    query_of,
    value_of,
)
from brand_presence.vendors import google_ads

CITY, REGION = "city", "region"
ACCOUNT = "account"
ROWS = "rows"


CRITERION_BASE = 2000


LOCATION_TYPES = {"LOCATION_OF_PRESENCE": "presence", "AREA_OF_INTEREST": "interest"}


CITY_CONSTANT = re.compile(r"geoTargetConstants/([1-9][0-9]*)")


SHARE_FLOOR = 0.0999
LOST_CEILING = 0.9001


SHAPE = status.NOT_AN_ANSWER
MISSING = status.PART_MISSING
PARTIAL = status.PARTIAL_FAILURE
OTHER_CUSTOMER = status.OTHER_CUSTOMER
NO_CURRENCY = status.CURRENCY_MISSING
OTHER_CURRENCY = status.CURRENCY_DIFFERS
OUTSIDE = status.OUTSIDE_WINDOW
REPEATED = status.ROW_REPEATED
COUNTRY = status.COUNTRY_NOT_IN_TABLE


FIELD_MASK = "fieldMask"
PARTIAL_FAILURE = "partialFailureError"


class GoogleAdsRaw(RawRow):
    table: ClassVar[str] = "raw.google_ads"


    content_date: date


class GoogleAdsCampaigns(CleanRow):
    table: ClassVar[str] = "stg.google_ads_campaigns"
    natural_key: ClassVar[tuple[str, ...]] = ("ad_account_id", "campaign_id", "date", "period")

    ad_account_id: str
    campaign_id: int
    campaign_name: str
    channel_type: str
    campaign_status: str

    date: date
    impressions: int
    clicks: int

    cost_micros: int

    conversions: float
    conversions_by_conversion_date: float

    search_impression_share: float | None
    currency_code: str


    absolute_top_impression_percentage: float | None = None
    search_absolute_top_impression_share: float | None = None
    search_rank_lost_impression_share: float | None = None
    search_budget_lost_impression_share: float | None = None

    eligible_impressions: int | None = None
    absolute_top_impressions: int | None = None
    absolute_top_eligible_impressions: int | None = None
    rank_lost_impressions: int | None = None
    budget_lost_impressions: int | None = None

    impression_share_bounded: bool | None = None


class GoogleAdsGeo(CleanRow):
    table: ClassVar[str] = "stg.google_ads_geo"
    natural_key: ClassVar[tuple[str, ...]] = (
        "ad_account_id", "campaign_id", "date", "country_code", "location_type", "period",
    )

    ad_account_id: str
    campaign_id: int

    country_criterion_id: int | None

    location_type: str
    date: date
    impressions: int
    clicks: int
    cost_micros: int
    conversions: float
    currency_code: str


class GoogleAdsGeoCity(CleanRow):
    table: ClassVar[str] = "stg.google_ads_geo_cities"
    natural_key: ClassVar[tuple[str, ...]] = (
        "ad_account_id", "campaign_id", "date", "country_code", "city_id", "location_type",
        "period",
    )

    ad_account_id: str
    campaign_id: int
    country_criterion_id: int | None
    city_criterion_id: int | None
    city_id: str
    location_type: str
    date: date
    impressions: int
    clicks: int
    cost_micros: int
    conversions: float
    currency_code: str


class GoogleAdsGeoRegion(CleanRow):
    table: ClassVar[str] = "stg.google_ads_geo_regions"
    natural_key: ClassVar[tuple[str, ...]] = (
        "ad_account_id", "campaign_id", "date", "country_code", "region_id", "location_type",
        "period",
    )

    ad_account_id: str
    campaign_id: int
    country_criterion_id: int | None
    region_criterion_id: int | None
    region_id: str
    location_type: str
    date: date
    impressions: int
    clicks: int
    cost_micros: int
    conversions: float
    currency_code: str


class GoogleAdsSearchTerms(CleanRow):
    table: ClassVar[str] = "stg.google_ads_search_terms"
    natural_key: ClassVar[tuple[str, ...]] = (
        "ad_account_id", "campaign_id", "ad_group_id", "search_term", "date", "period",
    )

    ad_account_id: str
    campaign_id: int
    ad_group_id: int

    search_term: str
    date: date
    impressions: int
    clicks: int
    cost_micros: int
    conversions: float
    currency_code: str


class GoogleAdsKeywords(CleanRow):
    table: ClassVar[str] = "stg.google_ads_keywords"
    natural_key: ClassVar[tuple[str, ...]] = (
        "ad_account_id", "campaign_id", "ad_group_id", "criterion_id", "date", "period",
    )

    ad_account_id: str
    campaign_id: int
    ad_group_id: int
    criterion_id: int
    keyword_text: str
    match_type: str
    date: date
    impressions: int
    clicks: int
    cost_micros: int
    conversions: float
    search_impression_share: float | None
    currency_code: str


class GoogleAdsPull(PullRow):
    table: ClassVar[str] = "stg.google_ads_pulls"
    natural_key: ClassVar[tuple[str, ...]] = ("ad_account_id", "report", "period")

    ad_account_id: str
    report: str
    currency_code: str


    rows_without_criterion: int | None = None
    criteria_not_in_table: int | None = None


    listed_places_unmatched: int | None = None


class GoogleAdsTask(LedgerRow):
    table: ClassVar[str] = "ops.google_ads_tasks"


MODELS: dict[str, type[CleanRow]] = {
    CAMPAIGNS: GoogleAdsCampaigns, GEOGRAPHY: GoogleAdsGeo, KEYWORDS: GoogleAdsKeywords,
    SEARCH_TERMS: GoogleAdsSearchTerms, GEOGRAPHY_CITIES: GoogleAdsGeoCity,
    GEOGRAPHY_REGIONS: GoogleAdsGeoRegion,
}


PLACES = {
    GEOGRAPHY_CITIES: ("segments.geo_target_city", CITY, "city_id", "city_criterion_id",
                       OTHER_CITY),
    GEOGRAPHY_REGIONS: ("segments.geo_target_region", REGION, "region_id",
                        "region_criterion_id", OTHER_REGION),
}


FOLD_KEY = ("ad_account_id", "campaign_id", "date", "country_code", "location_type")
_FIGURE_NAMES = ("impressions", "clicks", "cost_micros", "conversions")


def fold_unlisted_places(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple, list[Mapping[str, object]]] = {}
    for row in rows:
        groups.setdefault(tuple(row[name] for name in FOLD_KEY), []).append(row)
    folded = []
    for held in groups.values():
        currencies = {row["currency_code"] for row in held}
        if len(currencies) != 1:
            raise ValueError("a fold over two currencies")
        first = held[0]
        folded.append({
            **{name: first[name] for name in FOLD_KEY},
            "country_criterion_id": first["country_criterion_id"],
            "currency_code": first["currency_code"],
            "impressions": sum(row["impressions"] for row in held),
            "clicks": sum(row["clicks"] for row in held),
            "cost_micros": sum(row["cost_micros"] for row in held),
            "conversions": sum(row["conversions"] for row in held),
        })
    return folded


class _Unfit(Exception):
    def __init__(self, rule: str):
        super().__init__(rule)
        self.rule = rule


def _text(row: object, field: str) -> str:
    value = value_of(row, field)
    if not isinstance(value, str) or not value:
        raise _Unfit(SHAPE)
    return value


def _whole(row: object, field: str) -> int:
    value = value_of(row, field)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise _Unfit(SHAPE)


def _number(row: object, field: str, *, may_be_absent: bool = False) -> float | None:
    value = value_of(row, field)
    if value is ABSENT and may_be_absent:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise _Unfit(SHAPE)


def _day(row: object) -> date:
    value = value_of(row, "segments.date")
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise _Unfit(SHAPE) from None


def country_of(row: object) -> tuple[int | None, str]:
    if value_of(row, "geographic_view.country_criterion_id") is ABSENT:
        return None, UNKNOWN
    criterion = _whole(row, "geographic_view.country_criterion_id")
    try:
        return criterion, countries.from_numeric(criterion - CRITERION_BASE, "country criterion")
    except countries.UnknownCountry:
        return criterion, UNKNOWN


def city_of(row: object, field: str = "segments.geo_target_city") -> int | None:
    value = value_of(row, field)
    if value is ABSENT or value is None or value == "":
        return None
    found = CITY_CONSTANT.fullmatch(value) if isinstance(value, str) else None
    if found is None:
        raise _Unfit(SHAPE)
    return int(found.group(1))


def page_rule(page: object) -> str | None:
    if not isinstance(page, Mapping) or "error" in page:
        return SHAPE
    if page.get(PARTIAL_FAILURE):
        return PARTIAL
    if not isinstance(page.get(FIELD_MASK), str):
        return SHAPE
    results = page.get("results")
    if results is None:
        return None
    if not isinstance(results, list) or not all(isinstance(row, Mapping) for row in results):
        return SHAPE
    return None


def _results(pages: Sequence[object]) -> list[Mapping]:
    rows: list[Mapping] = []
    for page in pages:
        rule = page_rule(page)
        if rule is not None:
            raise _Unfit(rule)
        rows += page.get("results") or []
    return rows


class Account:
    def __init__(self, customer_id: str, currency_code: str, time_zone: str):
        self.customer_id = customer_id
        self.currency_code = currency_code
        self.time_zone = time_zone

    def __repr__(self) -> str:
        return f"Account(currency_code={self.currency_code!r}, time_zone={self.time_zone!r})"


def account_of(page: object) -> Account:
    rows = _results([page])
    if len(rows) != 1:
        raise _Unfit(SHAPE)
    row, = rows
    customer = value_of(row, "customer.id")
    if isinstance(customer, int) and not isinstance(customer, bool):
        customer = str(customer)
    if not isinstance(customer, str) or not customer.isdigit():
        raise _Unfit(SHAPE)
    return Account(customer, _text(row, "customer.currency_code"),
                   _text(row, "customer.time_zone"))


def _whose(row: object, account: Account) -> None:
    customer = value_of(row, "customer.id")
    if str(customer) != account.customer_id:
        raise _Unfit(OTHER_CUSTOMER)
    currency = value_of(row, "customer.currency_code")
    if currency is ABSENT or currency is None or currency == "":
        raise _Unfit(NO_CURRENCY)
    if currency != account.currency_code:
        raise _Unfit(OTHER_CURRENCY)


def _figures(row: object) -> dict[str, object]:
    return {
        "impressions": _whole(row, "metrics.impressions"),
        "clicks": _whole(row, "metrics.clicks"),
        "cost_micros": _whole(row, "metrics.cost_micros"),
        "conversions": _number(row, "metrics.conversions"),
    }


def _nearest(value: float) -> int:
    return math.floor(value + 0.5)


def auction_of(impressions: int, percentage: float | None, share: float | None,
               top_share: float | None, rank_lost: float | None,
               budget_lost: float | None) -> dict[str, object]:
    eligible = _nearest(impressions / share) if share else None
    top = _nearest(impressions * percentage) if percentage is not None else None
    bounded = (share == SHARE_FLOOR or top_share == SHARE_FLOOR
               or rank_lost == LOST_CEILING or budget_lost == LOST_CEILING)
    return {
        "eligible_impressions": eligible,
        "absolute_top_impressions": top,
        "absolute_top_eligible_impressions": (
            _nearest(top / top_share) if top is not None and top_share else None),
        "rank_lost_impressions": (
            _nearest(eligible * rank_lost)
            if eligible is not None and rank_lost is not None else None),
        "budget_lost_impressions": (
            _nearest(eligible * budget_lost)
            if eligible is not None and budget_lost is not None else None),
        "impression_share_bounded": bounded,
    }


def read_row(report: str, row: object, account: Account,
             window: tuple[date, date]) -> tuple[dict[str, object], str]:
    _whose(row, account)
    day = _day(row)
    first, last = window
    if not first <= day <= last:
        raise _Unfit(OUTSIDE)
    held: dict[str, object] = {"campaign_id": _whole(row, "campaign.id"), "date": day,
                               **_figures(row), "currency_code": account.currency_code}
    country = UNKNOWN
    if report == CAMPAIGNS:
        shares = {name: _number(row, f"metrics.{name}", may_be_absent=True) for name in (
            "search_impression_share", "absolute_top_impression_percentage",
            "search_absolute_top_impression_share", "search_rank_lost_impression_share",
            "search_budget_lost_impression_share")}
        held |= {
            "campaign_name": _text(row, "campaign.name"),
            "channel_type": _text(row, "campaign.advertising_channel_type"),
            "campaign_status": _text(row, "campaign.status"),
            "conversions_by_conversion_date": _number(
                row, "metrics.conversions_by_conversion_date"),
            **shares,
            **auction_of(held["impressions"], shares["absolute_top_impression_percentage"],
                         shares["search_impression_share"],
                         shares["search_absolute_top_impression_share"],
                         shares["search_rank_lost_impression_share"],
                         shares["search_budget_lost_impression_share"]),
        }
    elif report == SEARCH_TERMS:
        held |= {
            "ad_group_id": _whole(row, "ad_group.id"),
            "search_term": _text(row, "search_term_view.search_term"),
        }
    elif report in (GEOGRAPHY, *PLACES):
        criterion, country = country_of(row)
        kind = LOCATION_TYPES.get(value_of(row, "geographic_view.location_type"))
        if kind is None:
            raise _Unfit(SHAPE)
        held |= {"country_criterion_id": criterion, "location_type": kind}
        if report in PLACES:
            field, _, _, criterion_column, _ = PLACES[report]
            held[criterion_column] = city_of(row, field)
    elif report == KEYWORDS:
        held |= {
            "ad_group_id": _whole(row, "ad_group.id"),
            "criterion_id": _whole(row, "ad_group_criterion.criterion_id"),
            "keyword_text": _text(row, "ad_group_criterion.keyword.text"),
            "match_type": _text(row, "ad_group_criterion.keyword.match_type"),
            "search_impression_share": _number(row, "metrics.search_impression_share",
                                               may_be_absent=True),
        }
    else:
        raise _Unfit(SHAPE)
    return held, country


def read_answer(request: Request, parts: object,
                ) -> tuple[Account, list[tuple[dict[str, object], str]], int] | str:
    if not isinstance(parts, Mapping) or set(parts) != {ACCOUNT, ROWS} \
            or not isinstance(parts[ACCOUNT], list) or len(parts[ACCOUNT]) != 1 \
            or not isinstance(parts[ROWS], list) or not parts[ROWS]:
        return MISSING
    body = request.body
    report = body["report"]
    window = (date.fromisoformat(body["first_date"]), date.fromisoformat(body["last_date"]))
    model = MODELS.get(report)
    if model is None:
        return SHAPE
    try:
        account = account_of(parts[ACCOUNT][0])
        rows = [read_row(report, row, account, window) for row in _results(parts[ROWS])]
    except _Unfit as unfit:
        return unfit.rule


    as_criterion = {"city_id": "city_criterion_id", "region_id": "region_criterion_id"}
    named = tuple(as_criterion.get(name, name)
                  for name in model.natural_key if name not in ("period", "ad_account_id"))
    keys = [tuple(columns.get(name, country) for name in named) for columns, country in rows]
    if len(set(keys)) != len(keys):
        return REPEATED
    return account, rows, len(parts[ROWS])


class GoogleAdsSend(WindowSend):
    parts = (ACCOUNT, ROWS)

    max_pages = sys.maxsize

    def __init__(self, context: SendContext):
        self.context = context
        self.session = google_ads.Session(context.http, context.keys, sleep=context.sleep)
        self._customers: dict[str, str] | None = None

        self._details: dict[str, object] = {}
        self._checked: dict[str, str | None] = {}

        self._failed: dict[str, VendorError] = {}

        self._next: dict[str, str | None] = {}

    def customer_of(self, ad_account_id: str) -> str:
        if self._customers is None:
            if self.context.ref is None:
                raise InconsistentRef("the run handed the sender no load")
            self._customers = {account.ad_account_id: account.customer_id
                               for account in self.context.ref.ad_accounts}
        customer = self._customers.get(ad_account_id)
        if customer is None:
            raise InconsistentRef("a request names an ad account its load does not hold")
        return customer

    def details(self, ad_account_id: str) -> object:
        if ad_account_id not in self._details:
            self._details[ad_account_id] = self.session.search(
                self.customer_of(ad_account_id), query_of(DETAILS))
        return self._details[ad_account_id]

    def check(self, group: str, requests: Sequence[Request]) -> str | None:
        if group in self._failed:
            failed = self._failed[group]
            raise VendorError(failed.error_class, failed.status_code)
        if group not in self._checked:
            try:
                page = self.details(group)
            except VendorError as error:
                self._failed[group] = error
                raise
            try:
                account = account_of(page)
            except _Unfit as unfit:
                self._checked[group] = unfit.rule
            else:
                wanted = google_ads.digits_of(self.customer_of(group))
                self._checked[group] = (None if account.customer_id == wanted
                                        else status.OTHER)
        return self._checked[group]

    def restore(self, request, part, page, response):
        if part == ACCOUNT:
            self._details[request.body["ad_account_id"]] = response
        else:
            asking = json.dumps(dict(request.body), sort_keys=True)
            self._next[asking] = google_ads.next_page(response)

    def pull(self, request: Request, part: str, page: int) -> Page:
        body = request.body
        first = date.fromisoformat(body["first_date"])
        if part == ACCOUNT:
            return Page(Answer(self.details(body["ad_account_id"]), content_date=first),
                        cursor=None, more=False)
        asking = json.dumps(dict(body), sort_keys=True)
        token = None if page == 1 else self._next.get(asking)
        said = self.session.search(
            self.customer_of(body["ad_account_id"]),
            query_of(REPORTS[body["report"]], first, date.fromisoformat(body["last_date"])),
            token,
        )
        self._next[asking] = google_ads.next_page(said)
        return Page(Answer(said, content_date=first), cursor=None,
                    more=self._next[asking] is not None)


class GoogleAds(Source):
    name = "google-ads"
    shape = WINDOW
    durable_pages = True
    history_periods = 52
    history_requests = 60
    raw_model = GoogleAdsRaw
    clean_models = (GoogleAdsCampaigns, GoogleAdsGeo, GoogleAdsGeoCity, GoogleAdsGeoRegion,
                    GoogleAdsKeywords, GoogleAdsSearchTerms, GoogleAdsPull)
    ledger_model = GoogleAdsTask


    identity_fields: ClassVar[tuple[str, ...]] = ("ad_account_id", "report")


    refusals: ClassVar[tuple[str, ...]] = (status.ACCESS, status.LIMIT, status.SIGN_IN,
                                           status.REFUSAL_NOT_KNOWN)


    ends_run: ClassVar[tuple[str, ...]] = (status.SIGN_IN, status.REFUSAL_NOT_KNOWN)

    def listed_cities(self, settings: ClientRef) -> tuple[Location, ...]:
        return self.listed(settings, CITY)

    def listed(self, settings: ClientRef, kind: str) -> tuple[Location, ...]:
        return tuple(item for item in settings.listed_locations
                     if item.kind == kind and self.name in item.sources)

    def plan(self, settings: ClientRef, period: Period) -> list[Request]:
        first, last = window_of(period)
        asked = {*ALWAYS, *(report for report, (_, kind, *_) in PLACES.items()
                            if self.listed(settings, kind))}
        return [
            Request(
                {"ad_account_id": account.ad_account_id, "report": report,
                 "first_date": first.isoformat(), "last_date": last.isoformat()},

                (Target(account.client_brand_id or UNKNOWN, ALL, ALL),),
            )
            for account in settings.ad_accounts
            for report in REPORTS
            if report in asked
        ]

    def group(self, request: Request) -> str:
        return request.body["ad_account_id"]

    def window(self, request: Request) -> tuple[date, date]:
        return (date.fromisoformat(request.body["first_date"]),
                date.fromisoformat(request.body["last_date"]))

    def send(self, context: SendContext) -> GoogleAdsSend:
        return GoogleAdsSend(context)

    def usable(self, answer: Answer) -> Verdict:
        rule = page_rule(answer.response)
        return Verdict(rule is None, rule)

    def complete(self, request: Request, answer: Answer) -> Verdict:
        read = read_answer(request, answer.response.get("parts"))
        return Verdict(False, read) if isinstance(read, str) else Verdict(True)

    def parse(self, raw: RawRow, settings: ClientRef) -> list[CleanRow]:
        request = request_of(raw.request)
        read = read_answer(request, json.loads(raw.response)["parts"])
        if isinstance(read, str):
            raise ValueError(read)
        account, held, pages = read
        target, = request.targets
        standard = standard_columns(raw, settings, target)
        body = request.body
        report = body["report"]
        model = MODELS[report]
        counts: dict[str, int | None] = {}
        if report in (GEOGRAPHY, *PLACES):
            counts["rows_without_criterion"] = sum(
                1 for columns, _ in held if columns["country_criterion_id"] is None)
            counts["criteria_not_in_table"] = sum(
                1 for columns, country in held
                if columns["country_criterion_id"] is not None and country == UNKNOWN)
        if report in PLACES:
            rows, counts["listed_places_unmatched"] = self._places(
                report, standard, settings, body["ad_account_id"], held)
        else:
            rows = [
                model(**{**in_country(standard, settings, country),
                         "benchmark_version": None},
                      ad_account_id=body["ad_account_id"], **columns)
                for columns, country in held
            ]
        first, last = self.window(request)
        rows.append(GoogleAdsPull(
            **{**standard, "benchmark_version": None},
            ad_account_id=body["ad_account_id"], report=report,
            currency_code=account.currency_code, window_start=first, window_end=last,
            rows_written=len(rows), pages=pages, row_limit_hit=False,
            source_time_zone=account.time_zone, **counts,
        ))
        return rows

    def _places(self, report: str, standard: dict, settings: ClientRef, ad_account_id: str,
                held: list[tuple[dict[str, object], str]]) -> tuple[list[CleanRow], int]:
        _, kind, place_column, criterion_column, other = PLACES[report]
        model = MODELS[report]
        listed = {(int(item.google_criterion_id), item.country_code): item
                  for item in self.listed(settings, kind) if item.google_criterion_id}
        rows: list[CleanRow] = []
        rest: list[dict[str, object]] = []
        matched: set[str] = set()
        for columns, country in held:
            item = listed.get((columns[criterion_column], country))
            if item is None:
                rest.append({**columns, "ad_account_id": ad_account_id,
                             "country_code": country})
                continue
            matched.add(item.location_id)
            rows.append(model(
                **{**in_city(standard, settings, country, item.location_id),
                   "benchmark_version": None},
                ad_account_id=ad_account_id, **{place_column: item.location_id}, **columns))
        for folded in fold_unlisted_places(rest):
            country = folded.pop("country_code")
            rows.append(model(
                **{**in_city(standard, settings, country, None), "benchmark_version": None},
                **{place_column: other, criterion_column: None}, **folded))
        countries_held = {country for _, country in held}
        unmatched = sum(1 for item in listed.values()
                        if item.country_code in countries_held
                        and item.location_id not in matched)
        return rows, unmatched
