"""Search Console API and bulk export windows."""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import ClassVar
from zoneinfo import ZoneInfo

from brand_presence import countries, google_token, status
from brand_presence.client_ref import Brand, ClientRef
from brand_presence.rows import ALL, UNKNOWN, CleanRow, LedgerRow, PullRow, RawRow
from brand_presence.runs import Period, window_of
from brand_presence.sources import search_console_export as export
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
    in_country,
    request_of,
    standard_columns,
)
from brand_presence.sources.brand_names import named_by
from brand_presence.vendors import google_errors, search_console

TOTALS = "totals"
QUERIES = "queries"
PAGES = "pages"
DEVICES = "devices"

GROUPING = {
    TOTALS: ("date", "country"),
    QUERIES: ("date", "country", "query"),
    PAGES: ("date", "country", "page"),
    DEVICES: ("date", "country", "device"),
}


PAGED = (QUERIES, PAGES)

CHECK_GROUPING = ("date",)


SEARCH_TYPE = "web"

NOT_PLACED = "zzz"


PAGE_ROWS = 25_000
MAX_PAGES = 20

DAY_ROWS = 50_000


PACIFIC_TIME = "America/Los_Angeles"


NOT_READY = status.NOT_READY


WEEK_CHECK = status.FINISHED_WEEK_CHECK


FIRST_UNFINISHED = "firstIncompleteDate"


AGGREGATION = "responseAggregationType"


SHAPE = status.NOT_AN_ANSWER
MISSING = status.PART_MISSING
KEYS = status.ROW_KEYS
OUTSIDE = status.OUTSIDE_WINDOW
REPEATED = status.ROW_REPEATED
COUNTRY = status.COUNTRY_NOT_IN_TABLE


CLIENT, COMPETITOR = "client", "competitor"
TERM_BRAND = "brand"
TERM_COMPETITOR = "competitor"
TERM_GENERIC = "generic"


_SHARE_PLACES = Decimal("0.000000001")


class SearchConsoleRaw(RawRow):
    table: ClassVar[str] = "raw.search_console"


    content_date: date


class SearchConsoleTotals(CleanRow):
    table: ClassVar[str] = "stg.search_console_totals"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "web_property_id", "country_code", "search_type", "date", "period",
    )

    web_property_id: str
    search_type: str

    date: date
    clicks: int
    impressions: int

    position: float


class SearchConsoleQueries(CleanRow):
    table: ClassVar[str] = "stg.search_console_queries"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "web_property_id", "country_code", "search_type", "date", "query",
        "period",
    )

    web_property_id: str
    search_type: str
    date: date

    query: str
    clicks: int
    impressions: int
    position: float

    term_type: str | None = None
    subject_brand: str | None = None


class SearchConsolePages(CleanRow):
    table: ClassVar[str] = "stg.search_console_pages"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "web_property_id", "country_code", "search_type", "date", "page",
        "period",
    )

    web_property_id: str
    search_type: str
    date: date
    page: str
    clicks: int
    impressions: int
    position: float


class SearchConsoleDevices(CleanRow):
    table: ClassVar[str] = "stg.search_console_devices"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "web_property_id", "country_code", "search_type", "date", "device",
        "period",
    )

    web_property_id: str
    search_type: str
    date: date

    device: str
    clicks: int
    impressions: int
    position: float


class SearchConsoleExportTotals(SearchConsoleTotals):
    table: ClassVar[str] = "stg.search_console_export_totals"
    position: float | None
    position_sum: int
    anonymized_clicks: int
    anonymized_impressions: int

class SearchConsoleExportQueries(SearchConsoleQueries):
    table: ClassVar[str] = "stg.search_console_export_queries"
    position: float | None
    position_sum: int

class SearchConsoleExportPages(SearchConsolePages):
    table: ClassVar[str] = "stg.search_console_export_pages"
    position: float | None
    position_sum: int

class SearchConsoleExportDevices(SearchConsoleDevices):
    table: ClassVar[str] = "stg.search_console_export_devices"
    position: float | None
    position_sum: int

class SearchConsolePull(PullRow):
    table: ClassVar[str] = "stg.search_console_pulls"
    natural_key: ClassVar[tuple[str, ...]] = ("web_property_id", "search_type", "period")

    web_property_id: str
    search_type: str
    route: str | None = None
    totals_rows: int
    query_rows: int

    page_rows: int | None
    device_rows: int | None

    rows_repeated: int


    click_share: Decimal | None
    impression_share: Decimal | None
    page_click_share: Decimal | None


class SearchConsoleTask(LedgerRow):
    table: ClassVar[str] = "ops.search_console_tasks"


MODELS: dict[str, type[CleanRow]] = {
    TOTALS: SearchConsoleTotals, QUERIES: SearchConsoleQueries, PAGES: SearchConsolePages,
    DEVICES: SearchConsoleDevices,
}


EXPORT_MODELS = {
    TOTALS: SearchConsoleExportTotals, QUERIES: SearchConsoleExportQueries,
    PAGES: SearchConsoleExportPages, DEVICES: SearchConsoleExportDevices,
}


def page_filter(hostname: str) -> str:
    return "^https?://" + hostname.replace(".", "\\.") + "/"


def question(part: str, first: date, last: date, search_type: str,
             hostname: str | None) -> dict:
    asked: dict[str, object] = {
        "startDate": first.isoformat(), "endDate": last.isoformat(),
        "dimensions": list(GROUPING[part]), "type": search_type,
    }
    if hostname is not None:
        asked["dimensionFilterGroups"] = [{"filters": [{
            "dimension": "page", "operator": "includingRegex",
            "expression": page_filter(hostname),
        }]}]
    return asked


def check_question(first: date, last: date, search_type: str) -> dict:
    return {"startDate": first.isoformat(), "endDate": last.isoformat(),
            "dimensions": list(CHECK_GROUPING), "type": search_type}


def country_of(value: object) -> str | None:
    if value == NOT_PLACED:
        return UNKNOWN
    if not isinstance(value, str) or value != value.lower():
        return None
    try:
        return countries.alpha_2(value.upper(), "country")
    except countries.UnknownCountry:
        return None


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _count(value: object) -> object:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _position(value: object) -> object:
    if isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def _figures(row: Mapping[str, object]) -> tuple[object, object, object]:
    return row.get("clicks"), row.get("impressions"), row.get("position")


def _page_rule(page: object) -> str | None:
    if not isinstance(page, Mapping) or "error" in page \
            or not isinstance(page.get(AGGREGATION), str):
        return SHAPE
    rows = page.get("rows")
    if rows is None:
        return None
    if not isinstance(rows, list):
        return SHAPE
    for row in rows:
        if not isinstance(row, Mapping):
            return SHAPE
        keys = row.get("keys")
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            return KEYS
    return None


def _once(part: str, pages: Sequence[Mapping[str, object]],
          window: tuple[date, date]) -> tuple[list[Mapping[str, object]], int] | str:
    first, last = window
    held: dict[tuple[str, ...], Mapping[str, object]] = {}
    repeated = 0
    for page in pages:
        rule = _page_rule(page)
        if rule is not None:
            return rule
        for row in page.get("rows") or []:
            keys = tuple(row["keys"])
            if len(keys) != len(GROUPING[part]):
                return KEYS
            day = _day(keys[0])

            if day is not None and not first <= day <= last:
                return OUTSIDE


            if country_of(keys[1]) is None:
                return COUNTRY
            if keys not in held:
                held[keys] = row
            elif _figures(held[keys]) == _figures(row):
                repeated += 1
            else:
                return REPEATED
    return list(held.values()), repeated


def label(query: str, client_brand_id: str,
          brands: Sequence[Brand], client_brands: set[str]) -> tuple[str, str | None]:
    named = []
    for brand in brands:
        kind = CLIENT if brand.brand_id in client_brands else COMPETITOR
        match = named_by(query, brand)
        if match is None:
            continue
        rank = 0 if brand.brand_id == client_brand_id else 1 if kind == CLIENT else 2
        named.append((rank, -len(match), brand.brand_id, kind))
    if not named:
        return TERM_GENERIC, None
    _, _, brand_id, kind = min(named)
    return (TERM_BRAND if kind == CLIENT else TERM_COMPETITOR), brand_id


def _share(part: int, whole: int) -> Decimal | None:
    if whole == 0:
        return None
    return (Decimal(part) / Decimal(whole)).quantize(_SHARE_PLACES, rounding=ROUND_HALF_EVEN)


class SearchConsoleSend(WindowSend):
    parts = (TOTALS, QUERIES, PAGES, DEVICES)

    def __init__(self, context: SendContext, token, *, page_rows: int, max_pages: int):
        self.context = context
        self.token = token
        self.page_rows = page_rows
        self.max_pages = max_pages
        self.export = export.ExportSend(context)
        self.api_history_start: date | None = None


        self._unfinished: dict[tuple[str, str], date | str] = {}

    def history_periods(self, period: Period, default: int) -> int:
        self.api_history_start = period.start - timedelta(weeks=default)
        if self.context.ref is None:
            raise ValueError("Search Console history requires its applied setup")
        planned = SearchConsole().plan(self.context.ref, period)
        if not any("export" in request.body for request in planned):
            return default
        return self.export.history_length(period, planned, default)

    def expand(self, planned: Sequence[Request]) -> Sequence[Request]:
        eligible = [request for request in planned if "export" in request.body
                    or self.api_history_start is None
                    or date.fromisoformat(request.body["first_date"]) >= self.api_history_start]
        return self.export.expand(eligible)

    def check(self, group: str, requests: Sequence[Request]) -> str | None:
        if "export" in requests[0].body:
            return self.export.check(group, requests)
        body = requests[0].body
        first, last = date.fromisoformat(body["first_date"]), date.fromisoformat(body["last_date"])
        held = self._unfinished.get((body["address"], body["search_type"]))
        if held is None:
            held = self._unfinished[(body["address"], body["search_type"])] = \
                self._first_unfinished(body, first)
        if isinstance(held, str):
            return held
        return NOT_READY if held <= last else None

    def _first_unfinished(self, body: Mapping[str, object], first: date) -> date | str:
        today = self.context.started_at.astimezone(ZoneInfo(PACIFIC_TIME)).date()
        said = search_console.query(
            self.context.http, self.token, body["address"],
            check_question(min(first, today), today, body["search_type"]),
            row_limit=PAGE_ROWS, start_row=0, data_state=search_console.ALL,
            sleep=self.context.sleep,
        )

        if _page_rule(said) is not None:
            return WEEK_CHECK
        metadata = said.get("metadata")
        named = metadata.get(FIRST_UNFINISHED) if isinstance(metadata, Mapping) else None
        unfinished = _day(named)

        if unfinished is None or unfinished > today:
            return WEEK_CHECK
        return unfinished

    def pull(self, request: Request, part: str, page: int) -> Page:
        if "export" in request.body:
            return self.export.pull(request, part, page)
        body = request.body
        first, last = date.fromisoformat(body["first_date"]), date.fromisoformat(body["last_date"])

        limit = self.page_rows if part in PAGED else PAGE_ROWS
        said = search_console.query(
            self.context.http, self.token, body["address"],
            question(part, first, last, body["search_type"], body["hostname"]),
            row_limit=limit, start_row=(page - 1) * limit, data_state=search_console.FINAL,
            sleep=self.context.sleep,
        )
        rows = said.get("rows") if isinstance(said, Mapping) else None

        more = part in PAGED and isinstance(rows, list) and len(rows) >= limit
        return Page(Answer(said, content_date=first), cursor=None, more=more)


class SearchConsole(Source):
    name = "search-console"
    shape = WINDOW
    durable_pages = True
    history_periods = 65
    history_requests = 500
    raw_model = SearchConsoleRaw
    clean_models = (SearchConsoleTotals, SearchConsoleQueries, SearchConsolePages,
                    SearchConsoleDevices, *EXPORT_MODELS.values(), SearchConsolePull)
    ledger_model = SearchConsoleTask


    identity_fields: ClassVar[tuple[str, ...]] = ("web_property_id",)


    refusals: ClassVar[tuple[str, ...]] = (google_errors.ACCESS, google_errors.LIMIT,
                                           google_errors.REFUSAL_NOT_KNOWN, WEEK_CHECK,
                                           status.STATEMENT_REFUSED)
    ends_run: ClassVar[tuple[str, ...]] = (WEEK_CHECK,)
    scope = google_token.SEARCH_CONSOLE_READ_ONLY


    page_rows: ClassVar[int] = PAGE_ROWS
    max_pages: ClassVar[int] = MAX_PAGES
    day_rows: ClassVar[int] = DAY_ROWS

    def discovers_history(self, settings: ClientRef) -> bool:
        return any(p.bigquery_export is not None for p in settings.web_properties_of(self.name))

    def plan(self, settings: ClientRef, period: Period) -> list[Request]:
        first, last = window_of(period)
        return [
            Request(
                {"web_property_id": web_property.web_property_id,
                 "search_type": SEARCH_TYPE, "address": web_property.address,

                 "hostname": web_property.hostname,
                 **({"export": web_property.bigquery_export}
                    if web_property.bigquery_export is not None else {}),
                 "first_date": first.isoformat(), "last_date": last.isoformat()},

                (Target(web_property.client_brand_id, ALL, ALL),),
            )
            for web_property in settings.web_properties_of(self.name)
        ]

    def identity(self, request: Request) -> Mapping[str, object]:
        identity = self.history_identity(request)
        if "export" in request.body and "epoch_version" in request.body:
            return {**identity, "epoch_version": request.body["epoch_version"]}
        return identity

    def history_identity(self, request: Request) -> Mapping[str, object]:
        identity = super().identity(request)
        return {**identity, "route": "export"} if "export" in request.body else identity

    def group(self, request: Request) -> str:
        return request.body["web_property_id"]

    def window(self, request: Request) -> tuple[date, date]:
        return (date.fromisoformat(request.body["first_date"]),
                date.fromisoformat(request.body["last_date"]))

    def send(self, context: SendContext) -> SearchConsoleSend:
        return SearchConsoleSend(
            context, google_token.reader(SearchConsole.scope, context.tokens),
            page_rows=self.page_rows, max_pages=self.max_pages,
        )

    def usable(self, answer: Answer) -> Verdict:
        rule = (export.page_rule(answer.response)
                if isinstance(answer.response, Mapping) and "export" in answer.response
                else _page_rule(answer.response))
        return Verdict(rule is None, rule)

    def complete(self, request: Request, answer: Answer) -> Verdict:
        read = self._read(request, answer.response.get("parts"))
        return Verdict(False, read) if isinstance(read, str) else Verdict(True)

    def _read(self, request: Request, parts: object,
              ) -> tuple[dict[str, list[Mapping]], int] | str:
        if not isinstance(parts, Mapping) or set(parts) != set(GROUPING) \
                or any(len(parts[part]) != 1 for part in GROUPING if part not in PAGED) \
                or any(not parts[part] for part in PAGED):
            return MISSING
        window = self.window(request)
        read: dict[str, list[Mapping]] = {}
        repeated = 0
        for part in GROUPING:
            if "export" in request.body:
                if len(parts[part]) != 1:
                    return MISSING
                for page in parts[part]:
                    rule = export.page_rule(page)
                    if rule is not None:
                        return rule
                    if page["export"] != part:
                        return SHAPE
            elif any("export" in page for page in parts[part]):
                return SHAPE
            once = _once(part, parts[part], window)
            if isinstance(once, str):
                return once
            read[part], more = once
            repeated += more
        return read, repeated

    def parse(self, raw: RawRow, settings: ClientRef) -> list[CleanRow]:
        request = request_of(raw.request)
        parts = json.loads(raw.response)["parts"]
        read = self._read(request, parts)
        if isinstance(read, str):
            raise ValueError(read)
        held, repeated = read
        target, = request.targets
        standard = standard_columns(raw, settings, target)
        own = {"web_property_id": request.body["web_property_id"],
               "search_type": request.body["search_type"]}
        rows: list[CleanRow] = []
        models = EXPORT_MODELS if "export" in request.body else MODELS
        for part, model in models.items():
            split = GROUPING[part][2:]
            for row in held[part]:
                day, country, *rest = row["keys"]

                placed = in_country(standard, settings, country_of(country))
                labelled = {}
                if part == QUERIES:
                    term_type, subject = label(rest[0], target.client_brand_id, settings.brands,
                                               {s.client_brand_id for s in settings.benchmark_sets})
                    labelled = {"term_type": term_type, "subject_brand": subject}
                rows.append(model(
                    **placed, **own, date=_day(day) or day,
                    **({name: row[name] for name in ("position_sum", "anonymized_clicks",
                                                   "anonymized_impressions") if name in row}
                       if "export" in request.body else {}),
                    **dict(zip(split, rest, strict=True)), **labelled,
                    clicks=_count(row.get("clicks")),
                    impressions=_count(row.get("impressions")),
                    position=_position(row.get("position")),
                ))
        rows.append(self._pull_row(standard, own, request, parts, rows, repeated))
        return rows

    def _pull_row(self, standard: dict, own: dict, request: Request, parts: Mapping,
                  rows: list[CleanRow], repeated: int) -> SearchConsolePull:
        of = {part: [row for row in rows if isinstance(row, model)]
              for part, model in MODELS.items()}


        capped = any(len(parts[part]) >= self.max_pages
                     and len(parts[part][-1].get("rows") or []) >= self.page_rows
                     for part in PAGED)
        a_day = max((max(Counter(row.date for row in of[part]).values(), default=0)
                     for part in PAGED), default=0)
        clicks = sum(row.clicks for row in of[TOTALS])
        first, last = self.window(request)
        return SearchConsolePull(
            **standard, **own, window_start=first, window_end=last,
            route="export" if "export" in request.body else "api",
            rows_written=len(rows), pages=sum(len(held) for held in parts.values()),
            row_limit_hit=(False if "export" in request.body
                           else bool(capped or a_day >= self.day_rows)),
            source_time_zone=PACIFIC_TIME,
            totals_rows=len(of[TOTALS]), query_rows=len(of[QUERIES]),
            page_rows=len(of[PAGES]), device_rows=len(of[DEVICES]), rows_repeated=repeated,
            click_share=_share(sum(row.clicks for row in of[QUERIES]), clicks),
            impression_share=_share(sum(row.impressions for row in of[QUERIES]),
                                    sum(row.impressions for row in of[TOTALS])),
            page_click_share=_share(sum(row.clicks for row in of[PAGES]), clicks),
        )
