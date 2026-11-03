"""Fixed GA4 export statements, monthly spans and aggregate parsing."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from google.api_core.exceptions import (
    BadRequest,
    Forbidden,
    GoogleAPICallError,
    NotFound,
    PermissionDenied,
    TooManyRequests,
)
from google.cloud import bigquery

from brand_presence import status
from brand_presence.client_ref import ClientRef, InconsistentRef
from brand_presence.geo_targets import country_name, name_parts
from brand_presence.http import VendorError
from brand_presence.rows import ALL, UNKNOWN, CleanRow
from brand_presence.settings import GA4_EXPORT
from brand_presence.sources.base import (
    Answer,
    Page,
    Request,
    SendContext,
    Verdict,
    in_city,
    in_country,
)

CITY, COUNTRY, REGION = "city", "country", "region"

EXPORT_ENGAGEMENT = "export_engagement"
EXPORT_PAGES = "export_pages"
REPORTS = (EXPORT_ENGAGEMENT, EXPORT_PAGES)


NATIONAL = "national"
STATE = "state"
CITY_LEVEL = "city"
LEVELS = (NATIONAL, STATE, CITY_LEVEL)


REST = "(other)"

ALL_CHANNELS = "(all)"
NOT_SET = "(not set)"

OTHER_PAGES = "(other)"
NO_PAGE = "(not set)"


TABLE_WAIT_DAYS = 3


MAX_BYTES_BILLED = 20 * 1024**3

MAX_MONTHS = 120

MOST_PAGES = 10_000


_DAILY_TABLE = re.compile(r"events_([0-9]{8})")


SHAPE = status.NOT_AN_ANSWER
KEYS = status.ROW_KEYS
OUTSIDE = status.OUTSIDE_WINDOW
REPEATED = status.ROW_REPEATED
QUERY_STRING = status.QUERY_STRING
NO_TABLE = status.NO_DAILY_TABLE


_LIMIT_REASONS = frozenset({"quotaExceeded", "rateLimitExceeded"})
_BYTES_REASON = "bytesBilledLimitExceeded"


class NotAnExport(ValueError):
    def __init__(self):
        super().__init__("a GA4 BigQuery export is a project and its dataset for the property")


@dataclass(frozen=True)
class Export:
    project: str
    dataset: str

    @property
    def dataset_id(self) -> str:
        return f"{self.project}.{self.dataset}"

    @property
    def events(self) -> str:
        return f"`{self.project}.{self.dataset}.events_*`"


def export_of(value: object) -> Export:
    named = re.fullmatch(GA4_EXPORT, value) if isinstance(value, str) else None
    if named is None:
        raise NotAnExport()
    project, dataset = value.split(".")
    return Export(project, dataset)


ENGAGEMENT_STATEMENT = """
WITH events AS (
  SELECT
    user_pseudo_id AS visitor,
    event_date,
    event_name,
    event_timestamp,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_number')
      AS session_number,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec')
      AS engagement_msec,
    (SELECT COALESCE(value.string_value, CAST(value.int_value AS STRING))
     FROM UNNEST(event_params) WHERE key = 'session_engaged') AS session_engaged,
    LOWER(TRIM(geo.country)) AS country,
    LOWER(TRIM(geo.region)) AS region,
    LOWER(TRIM(geo.city)) AS city,
    session_traffic_source_last_click.cross_channel_campaign.default_channel_group
      AS channel_group
  FROM {events}
  WHERE _TABLE_SUFFIX BETWEEN '{first}' AND '{last}'
    AND (@hostname IS NULL OR LOWER(device.web_info.hostname) = @hostname)
),
sessions AS (
  SELECT
    visitor,
    session_id,
    MIN(event_date) AS day,
    ARRAY_AGG(STRUCT(country, region, city) ORDER BY event_timestamp LIMIT 1)[OFFSET(0)]
      AS place,
    ARRAY_AGG(channel_group IGNORE NULLS ORDER BY event_timestamp LIMIT 1)[SAFE_OFFSET(0)]
      AS channel_group,
    MAX(session_number) AS session_number,
    COUNTIF(event_name = 'page_view') AS page_views,
    SUM(IFNULL(engagement_msec, 0)) AS engagement_msec,
    LOGICAL_OR(session_engaged = '1' OR IFNULL(engagement_msec, 0) > 0) AS engaged,
    LOGICAL_OR(event_name IN ('first_visit', 'first_open')) AS first_visit
  FROM events
  WHERE session_id IS NOT NULL
  GROUP BY visitor, session_id
),
placed AS (
  SELECT
    sessions.*,
    ARRAY(
      SELECT AS STRUCT listed.location_id, listed.geo_level
      FROM UNNEST(@places) AS listed
      WHERE sessions.place.country = listed.country
        AND (listed.geo_level = 'national'
          OR (listed.geo_level = 'state' AND sessions.place.region = listed.region)
          OR (listed.geo_level = 'city' AND sessions.place.city = listed.city
              AND (listed.region IS NULL OR sessions.place.region = listed.region)))
    ) AS found
  FROM sessions
),
spread AS (
  SELECT placed.* EXCEPT (found), spot.location_id, spot.geo_level, channel
  FROM placed,
    UNNEST(ARRAY_CONCAT(found, IF(
      EXISTS(SELECT 1 FROM UNNEST(found) AS one WHERE one.geo_level = 'national'), [],
      [STRUCT('(other)' AS location_id, 'national' AS geo_level)]))) AS spot,
    UNNEST([IFNULL(channel_group, '(not set)'), '(all)']) AS channel
),
ambiguous AS (
  SELECT location_id, COUNT(*) AS ambiguous_count
  FROM (
    SELECT location_id, day
    FROM spread
    WHERE geo_level = 'city' AND channel = '(all)'
    GROUP BY location_id, day
    HAVING COUNT(DISTINCT place.region) > 1
  )
  GROUP BY location_id
)
SELECT
  location_id,
  geo_level,
  channel AS channel_group,
  COUNT(*) AS sessions,
  COUNT(DISTINCT IF(engaged, visitor, NULL)) AS active_users,
  COUNT(DISTINCT visitor) AS total_users,
  COUNT(DISTINCT IF(first_visit, visitor, NULL)) AS new_users,
  COUNT(DISTINCT IF(session_number > 1, visitor, NULL)) AS returning_users,
  SUM(page_views) AS page_views,
  CAST(SUM(engagement_msec) AS NUMERIC) / 1000 AS engagement_seconds,
  IFNULL(ANY_VALUE(IF(channel = '(all)', ambiguous_count, NULL)), 0) AS ambiguous_days
FROM spread
LEFT JOIN ambiguous USING (location_id)
GROUP BY location_id, geo_level, channel_group
ORDER BY location_id, channel_group
"""

PAGES_STATEMENT = """
WITH events AS (
  SELECT
    user_pseudo_id AS visitor,
    event_name,
    REGEXP_EXTRACT(
      (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location'),
      r'^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*([^?#]*)') AS page_path,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec')
      AS engagement_msec
  FROM {events}
  WHERE _TABLE_SUFFIX BETWEEN '{first}' AND '{last}'
    AND (@hostname IS NULL OR LOWER(device.web_info.hostname) = @hostname)
),
by_visitor AS (
  SELECT
    CASE WHEN page_path IS NULL THEN '(not set)' WHEN page_path = '' THEN '/'
      ELSE page_path END AS page_path,
    visitor,
    COUNTIF(event_name = 'page_view') AS page_views,
    SUM(IFNULL(engagement_msec, 0)) AS engagement_msec
  FROM events
  GROUP BY page_path, visitor
),
ranked AS (
  SELECT *, DENSE_RANK() OVER (ORDER BY path_views DESC, page_path) AS place
  FROM (SELECT *, SUM(page_views) OVER (PARTITION BY page_path) AS path_views
        FROM by_visitor)
)
SELECT
  IF(place <= @most_pages, page_path, '(other)') AS page_path,
  SUM(page_views) AS page_views,
  COUNT(DISTINCT IF(page_views > 0, visitor, NULL)) AS users,
  CAST(SUM(engagement_msec) AS NUMERIC) / 1000 AS engagement_seconds
FROM ranked
GROUP BY page_path
ORDER BY page_views DESC, page_path
"""

STATEMENTS = {EXPORT_ENGAGEMENT: ENGAGEMENT_STATEMENT, EXPORT_PAGES: PAGES_STATEMENT}


_COUNTS = ("sessions", "active_users", "total_users", "new_users", "returning_users",
           "page_views")


AMBIGUOUS_DAYS = "ambiguous_days"
RESULT_COLUMNS = {
    EXPORT_ENGAGEMENT: ("location_id", "geo_level", "channel_group", *_COUNTS,
                        "engagement_seconds", AMBIGUOUS_DAYS),
    EXPORT_PAGES: ("page_path", "page_views", "users", "engagement_seconds"),
}
_WHOLE_NUMBERS = {EXPORT_ENGAGEMENT: (*_COUNTS, AMBIGUOUS_DAYS),
                  EXPORT_PAGES: ("page_views", "users")}


def statement(report: str, export: Export, first: date, last: date) -> str:
    return STATEMENTS[report].format(
        events=export.events, first=first.strftime("%Y%m%d"), last=last.strftime("%Y%m%d"))


def places_of(settings: ClientRef, source: str) -> list[dict[str, str | None]]:
    found: list[dict[str, str | None]] = []
    for item in sorted(settings.listed_locations, key=lambda one: one.location_id):
        parts = name_parts(item.canonical_name)
        place = {"location_id": item.location_id, "country": parts[-1], "region": None,
                 "city": None}
        if item.kind == COUNTRY:
            found.append({**place, "country": country_name(item.canonical_name),
                          "geo_level": NATIONAL})
        elif item.kind == REGION and source in item.sources:
            found.append({**place, "geo_level": STATE, "region": parts[0]})
        elif item.kind == CITY and source in item.sources:
            found.append({**place, "geo_level": CITY_LEVEL, "city": parts[0],
                          "region": parts[1] if len(parts) == 3 else None})
    return found


_PLACE_FIELDS = ("location_id", "geo_level", "country", "region", "city")


def places_parameter(places: Sequence[Mapping[str, str | None]]
                     ) -> bigquery.ArrayQueryParameter:
    kind = bigquery.StructQueryParameterType(*[
        bigquery.ScalarQueryParameterType("STRING", name=name) for name in _PLACE_FIELDS])
    return bigquery.ArrayQueryParameter("places", kind, [
        bigquery.StructQueryParameter(None, *[
            bigquery.ScalarQueryParameter(name, "STRING", place.get(name))
            for name in _PLACE_FIELDS
        ]) for place in places
    ])


def month_of(day: date) -> date:
    return day.replace(day=1)


def _table_day(digits: str) -> date | None:
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    except ValueError:
        return None


def month_before(month: date) -> date:
    return month_of(month - timedelta(days=1))


def last_day_of(month: date) -> date:
    return month_of(month + timedelta(days=31)) - timedelta(days=1)


def months_to_read(held: Sequence[date], newest_read: date | None, last: date
                   ) -> tuple[list[date], int]:
    end = month_of(last)
    if newest_read is None:
        start = min(held) if held else end
    else:
        start = min(month_of(newest_read), month_before(end))
    months = sorted({month for month in held if start <= month <= end})
    left_out = max(0, len(months) - MAX_MONTHS)
    return months[left_out:], left_out


def _reason(error: GoogleAPICallError) -> str | None:
    for item in getattr(error, "errors", None) or []:
        if isinstance(item, Mapping) and item.get("reason"):
            return item["reason"]
    return None


def vendor_error(error: GoogleAPICallError) -> VendorError:
    code = getattr(error, "code", None)
    reason = _reason(error)
    if reason == _BYTES_REASON:
        return VendorError(status.BYTES_CAP, code)
    if reason in _LIMIT_REASONS or isinstance(error, TooManyRequests):
        return VendorError(status.LIMIT, code)
    if isinstance(error, Forbidden | PermissionDenied | NotFound):
        return VendorError(status.ACCESS, code)
    if isinstance(error, BadRequest):
        return VendorError(status.STATEMENT_REFUSED, code)
    return VendorError(status.VENDOR_FAULT, code)


@dataclass(frozen=True)
class Span:
    location: str | None
    months: tuple[date, ...]
    left_out: int
    through: tuple[date, ...] = ()
    days_without_table: int | None = None


def span_of(location: str | None, days: Sequence[date], newest_read: date | None,
            last: date) -> Span:
    held = sorted(day for day in set(days) if day <= last)
    months, left_out = months_to_read([month_of(day) for day in held], newest_read, last)
    if not months:
        return Span(location, (), left_out)
    start = max(months[0], held[0])
    through = tuple(max(day for day in held if month_of(day) == month) for month in months)
    read = sum(1 for day in held if day >= start)
    return Span(location, tuple(months), left_out, through,
                (last - start).days + 1 - read)


NEWEST_READ = ("SELECT MAX(months_last) FROM stg.ga4_pulls "
               "WHERE web_property_id = @web_property_id AND report = @report")


def _plain(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


class ExportSend:
    def __init__(self, context: SendContext):
        self.context = context
        self._datasets: dict[str, tuple[str | None, frozenset[date]]] = {}
        self._spans: dict[tuple[str, str, str], Span] = {}

    def dataset(self, export: Export) -> tuple[str | None, frozenset[date]]:
        held = self._datasets.get(export.dataset_id)
        if held is None:
            bq = self.context.bq
            try:
                location = bq.get_dataset(export.dataset_id).location
                named = [_DAILY_TABLE.fullmatch(item.table_id)
                         for item in bq.list_tables(export.dataset_id)]
            except GoogleAPICallError as error:
                raise vendor_error(error) from None
            days = frozenset(_table_day(found[1]) for found in named if found is not None)
            held = self._datasets[export.dataset_id] = (location, days - {None})
        return held

    def check(self, request: Request) -> str | None:
        _, days = self.dataset(export_of(request.body["export"]))
        last = date.fromisoformat(request.body["last_date"])
        today = self.context.started_at.astimezone(
            ZoneInfo(self.context.settings.time_zone)).date()
        if last not in days and (today - last).days < TABLE_WAIT_DAYS:
            return status.NOT_READY
        return None

    def newest_read(self, web_property_id: str, report: str) -> date | None:
        config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("web_property_id", "STRING", web_property_id),
            bigquery.ScalarQueryParameter("report", "STRING", report),
        ])
        rows = list(self.context.bq.query(NEWEST_READ, job_config=config).result())
        value = next(iter(dict(rows[0]).values()), None) if rows else None
        return date.fromisoformat(value) if isinstance(value, str) else value

    def span(self, request: Request) -> Span:
        body = request.body
        key = (body["web_property_id"], body["report"], body["last_date"])
        if key not in self._spans:
            location, days = self.dataset(export_of(body["export"]))
            self._spans[key] = span_of(
                location, sorted(days), self.newest_read(body["web_property_id"],
                                                         body["report"]),
                date.fromisoformat(body["last_date"]))
        return self._spans[key]

    def restore(self, request: Request, response: Mapping) -> None:
        saved = response.get("span")
        if saved is None:
            return
        body = request.body
        self._spans[(body["web_property_id"], body["report"], body["last_date"])] = Span(
            location=saved["location"], months=tuple(map(date.fromisoformat, saved["months"])),
            through=tuple(map(date.fromisoformat, saved["through"])),
            left_out=saved["left_out"], days_without_table=saved["days_without_table"],
        )

    def pull(self, request: Request, part: str, page: int) -> Page:
        body = request.body
        report = body["report"]
        span = self.span(request)
        said: dict[str, object] = {"export": report, "month": None, "through": None,
                                   "rows": [], "bytes_billed": None,
                                   "months_left_out": span.left_out,
                                   "days_without_table": span.days_without_table}
        said["span"] = {**asdict(span), "months": [d.isoformat() for d in span.months],
                        "through": [d.isoformat() for d in span.through]}
        if not span.months:
            return Page(Answer(said), cursor=None, more=False)
        month, through = span.months[page - 1], span.through[page - 1]
        hostname = body.get("hostname")
        given = [bigquery.ScalarQueryParameter(
            "hostname", "STRING", hostname.lower() if isinstance(hostname, str) else None)]
        if report == EXPORT_ENGAGEMENT:
            given.append(places_parameter(body["places"]))
        else:
            given.append(bigquery.ScalarQueryParameter("most_pages", "INT64", MOST_PAGES))
        config = bigquery.QueryJobConfig(query_parameters=given,
                                         maximum_bytes_billed=self.context.settings.maximum_bytes_billed)
        sql = statement(report, export_of(body["export"]), month, through)
        try:
            job = self.context.bq.query(sql, job_config=config, location=span.location)
            rows = [{name: _plain(value) for name, value in dict(row).items()}
                    for row in job.result()]
        except GoogleAPICallError as error:
            raise vendor_error(error) from None
        said.update(month=month.isoformat(), through=through.isoformat(), rows=rows,
                    bytes_billed=getattr(job, "total_bytes_billed", None))
        return Page(Answer(said), cursor=None, more=page < len(span.months))


def is_export_page(page: object) -> bool:
    return isinstance(page, Mapping) and page.get("export") in REPORTS


def _iso(value: object) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _seconds(value: object) -> Decimal | None:
    try:
        found = Decimal(value) if isinstance(value, str) else None
    except InvalidOperation:
        return None
    return found if found is not None and found.is_finite() and found >= 0 else None


def page_rule(page: object) -> str | None:
    if not is_export_page(page):
        return SHAPE
    report, rows = page["export"], page.get("rows")
    month, through = page.get("month"), page.get("through")
    if not isinstance(rows, list) or not _whole(page.get("months_left_out")):
        return SHAPE
    if month is None:
        return NO_TABLE if through is None and not rows else SHAPE
    if not _whole(page.get("days_without_table")):
        return SHAPE
    first, last = _iso(month), _iso(through)
    if first is None or last is None or first.day != 1 or month_of(last) != first:
        return SHAPE
    names = set(RESULT_COLUMNS[report])
    for row in rows:
        if not isinstance(row, Mapping):
            return SHAPE

        if set(row) != names:
            return KEYS
        if not all(_whole(row[name]) for name in _WHOLE_NUMBERS[report]) \
                or _seconds(row["engagement_seconds"]) is None:
            return SHAPE
        if report == EXPORT_ENGAGEMENT:
            if row["geo_level"] not in LEVELS or not isinstance(row["location_id"], str) \
                    or not isinstance(row["channel_group"], str) or not row["channel_group"]:
                return SHAPE
        else:
            path = row["page_path"]
            if not isinstance(path, str) or not path:
                return SHAPE
            if "?" in path or "#" in path:
                return QUERY_STRING
    return None


def complete_rule(request: Request, pages: Sequence[object]) -> str | None:
    body = request.body
    report, last = body["report"], date.fromisoformat(body["last_date"])
    places = {place["location_id"]: place["geo_level"] for place in body.get("places") or []}
    months: list[date] = []
    for page in pages:
        rule = page_rule(page)
        if rule is not None:
            return rule
        if page["export"] != report \
                or page["days_without_table"] != pages[0]["days_without_table"]:
            return SHAPE
        month, through = date.fromisoformat(page["month"]), date.fromisoformat(page["through"])
        if through > last:
            return OUTSIDE
        if months and month <= months[-1]:
            return REPEATED
        months.append(month)
        seen: set[tuple[str, ...]] = set()
        for row in page["rows"]:
            if report == EXPORT_ENGAGEMENT:
                place = row["location_id"]
                level = NATIONAL if place == REST else places.get(place)
                if level != row["geo_level"]:
                    return SHAPE
                key = (place, row["channel_group"])
            else:
                key = (row["page_path"],)
            if key in seen:
                return REPEATED
            seen.add(key)
    return None


def judged(request: Request, response: Mapping[str, object]) -> Verdict:
    pages = response.get("parts", {}).get("rows") if isinstance(response, Mapping) else None
    if not isinstance(pages, list) or not pages:
        return Verdict(False, status.PART_MISSING)
    rule = complete_rule(request, pages)
    return Verdict(rule is None, rule)


def engagement_rows(model: type[CleanRow], standard: dict, settings: ClientRef,
                    request: Request, pages: Sequence[Mapping]) -> list[CleanRow]:
    found: list[CleanRow] = []
    for page in pages:
        if page["month"] is None:
            continue
        month, through = date.fromisoformat(page["month"]), date.fromisoformat(page["through"])
        for row in page["rows"]:
            place = row["location_id"]
            if place == REST:
                placed = in_country(standard, settings, UNKNOWN)
            else:
                location = settings.location(place)
                if location is None:
                    raise InconsistentRef("an answer names a location its load does not hold")
                placed = in_country(standard, settings, location.country_code) \
                    if row["geo_level"] == NATIONAL \
                    else in_city(standard, settings, location.country_code, place)
            found.append(model(
                **placed, web_property_id=request.body["web_property_id"], month=month,
                through_date=through, geo_level=row["geo_level"],
                channel_group=row["channel_group"],
                **{name: row[name] for name in _COUNTS},
                engagement_seconds=Decimal(row["engagement_seconds"]),
            ))
    return found


def page_rows(model: type[CleanRow], standard: dict, request: Request,
              pages: Sequence[Mapping]) -> list[CleanRow]:
    found: list[CleanRow] = []
    for page in pages:
        if page["month"] is None:
            continue
        month, through = date.fromisoformat(page["month"]), date.fromisoformat(page["through"])
        for row in page["rows"]:
            found.append(model(
                **{**standard, "country_code": ALL, "location_id": ALL,
                   "benchmark_version": None},
                web_property_id=request.body["web_property_id"], month=month,
                through_date=through, page_path=row["page_path"],
                page_views=row["page_views"], users=row["users"],
                engagement_seconds=Decimal(row["engagement_seconds"]),
            ))
    return found


def unmatched(request: Request, pages: Sequence[Mapping], level: str) -> int | None:
    if request.body["report"] != EXPORT_ENGAGEMENT:
        return None
    listed = {place["location_id"] for place in request.body.get("places") or []
              if place["geo_level"] == level}
    held = {row["location_id"] for page in pages for row in page["rows"]}
    return len(listed - held)


def ambiguous(request: Request, pages: Sequence[Mapping]) -> int | None:
    if request.body["report"] != EXPORT_ENGAGEMENT:
        return None
    rows = [row for page in pages for row in page["rows"]]
    if any(AMBIGUOUS_DAYS not in row for row in rows):
        return None
    return sum(row[AMBIGUOUS_DAYS] for row in rows)


def pull_columns(request: Request, pages: Sequence[Mapping]) -> dict[str, object]:
    read = [date.fromisoformat(page["month"]) for page in pages if page["month"] is not None]
    missing = {page.get("days_without_table") for page in pages}
    billed = [page["bytes_billed"] for page in pages if _whole(page.get("bytes_billed"))]
    other = any(row.get("page_path") == OTHER_PAGES for page in pages for row in page["rows"])
    left_out = max((page["months_left_out"] for page in pages), default=0)
    return {
        "sampled": False, "samples_read": None, "sampling_space": None,
        "other_row": other, "thresholded": False, "rows_reported": None,
        "tokens_used": None, "tokens_left_day": None, "tokens_left_hour": None,
        "data_cut": None, "cut_start": None, "cut_end": None, "empty_reason": None,
        "listed_cities_unmatched": unmatched(request, pages, CITY_LEVEL),
        "listed_states_unmatched": unmatched(request, pages, STATE),
        "listed_countries_unmatched": unmatched(request, pages, NATIONAL),
        "city_days_ambiguous": ambiguous(request, pages),
        "days_without_table": missing.pop() if len(missing) == 1 else None,
        "months_first": min(read) if read else None,
        "months_last": max(read) if read else None,
        "bytes_billed": sum(billed) if billed else None,

        "row_limit_hit": bool(other or left_out),
    }
