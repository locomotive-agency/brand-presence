"""Fixed Search Console export statements and weekly readiness."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import bigquery

from brand_presence import status
from brand_presence.http import VendorError
from brand_presence.runs import Period
from brand_presence.settings import BIGQUERY_EXPORT
from brand_presence.sources.base import Answer, Page, Request, SendContext, WindowSend
from brand_presence.sources.ga4_export import NotAnExport, vendor_error

PARTS = ("totals", "queries", "pages", "devices")
SITE_TABLE = "searchdata_site_impression"
URL_TABLE = "searchdata_url_impression"
TABLES = (SITE_TABLE, URL_TABLE)
SITE_NAMESPACE = "SEARCHDATA_SITE_IMPRESSION"
URL_NAMESPACE = "SEARCHDATA_URL_IMPRESSION"
NAMESPACES = (SITE_NAMESPACE, URL_NAMESPACE)


def dataset_of(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(BIGQUERY_EXPORT, value) is None:
        raise NotAnExport()
    return value


def statement(dataset: str, part: str, hostname: bool = False) -> str:
    dataset = dataset_of(dataset)
    if part not in PARTS:
        raise ValueError("unknown export part")
    by_url = part == "pages" or hostname
    table = URL_TABLE if by_url else SITE_TABLE
    position = "sum_position" if by_url else "sum_top_position"
    dimension = {
        "totals": "",
        "queries": ", query",
        "pages": ", url AS page",
        "devices": ", UPPER(device) AS device",
    }[part]
    grouping = {"totals": "", "queries": ", query", "pages": ", page", "devices": ", device"}[part]
    anonymous = (
        """
  , SUM(IF(is_anonymized_query, clicks, 0)) AS anonymized_clicks
  , SUM(IF(is_anonymized_query, impressions, 0)) AS anonymized_impressions"""
        if part == "totals"
        else ""
    )
    filters = "\n  AND NOT is_anonymized_query AND query IS NOT NULL" if part == "queries" else ""
    if hostname:
        filters += "\n  AND REGEXP_CONTAINS(url, @page_filter)"
    return f"""-- Search Console export: {part}
SELECT data_date AS date, country{dimension},
  SUM(clicks) AS clicks, SUM(impressions) AS impressions,
  SUM({position}) AS position_sum,
  SAFE_DIVIDE(SUM({position}), SUM(impressions)) + 1 AS position{anonymous}
FROM `{dataset}.{table}`
WHERE data_date BETWEEN @first_date AND @last_date
  AND site_url = @site_url AND search_type = 'WEB'{filters}
GROUP BY date, country{grouping}
"""


def ready_statement(dataset: str) -> str:
    return f"""SELECT agenda, namespace, data_date, epoch_version, publish_time
FROM `{dataset_of(dataset)}.ExportLog`
WHERE data_date BETWEEN @first_date AND @last_date
  AND agenda = 'SEARCHDATA'
  AND namespace IN ('{SITE_NAMESPACE}', '{URL_NAMESPACE}')
"""


def page_rule(page: object) -> str | None:
    if not isinstance(page, Mapping) or page.get("export") not in PARTS:
        return status.NOT_AN_ANSWER
    part = page["export"]
    rows = page.get("rows")
    if not isinstance(rows, list) or page.get("responseAggregationType") != "export":
        return status.NOT_AN_ANSWER
    names = {"keys", "clicks", "impressions", "position_sum", "position"}
    if part == "totals":
        names |= {"anonymized_clicks", "anonymized_impressions"}
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != names:
            return status.ROW_KEYS
        keys = row["keys"]
        if (
            not isinstance(keys, list)
            or len(keys) != (2 if part == "totals" else 3)
            or not all(isinstance(key, str) for key in keys)
        ):
            return status.ROW_KEYS
        if tuple(keys) in seen:
            return status.ROW_REPEATED
        seen.add(tuple(keys))
        try:
            date.fromisoformat(keys[0])
        except ValueError:
            return status.NOT_AN_ANSWER
        for name in names - {"keys", "position"}:
            if type(row[name]) is not int or row[name] < 0:
                return status.NOT_AN_ANSWER
        pos = row["position"]
        if row["impressions"] == 0:
            if pos is not None:
                return status.NOT_AN_ANSWER
        elif (
            type(pos) not in (int, float)
            or not math.isfinite(pos)
            or not math.isclose(pos, row["position_sum"] / row["impressions"] + 1)
        ):
            return status.NOT_AN_ANSWER
        if part == "totals" and (
            row["anonymized_clicks"] > row["clicks"]
            or row["anonymized_impressions"] > row["impressions"]
        ):
            return status.NOT_AN_ANSWER
    return None


class ExportSend(WindowSend):
    parts = PARTS

    def __init__(self, context: SendContext):
        self.context = context
        self.locations: dict[str, str] = {}
        self.starts: dict[str, date] = {}
        self.readiness: dict[tuple[str, str, str], tuple[str | None, int | None]] = {}

    def history_length(self, period: Period, planned: Sequence[Request], default: int) -> int:
        counts = []
        for request in planned:
            if "export" not in request.body:
                counts.append(default)
                continue
            dataset = dataset_of(request.body["export"])
            try:
                location = self._location(dataset)
            except VendorError as error:
                if error.error_class == status.ACCESS:
                    continue
                raise
            sql = f"""SELECT MIN(data_date) AS first_date
FROM `{dataset}.ExportLog`
WHERE agenda = 'SEARCHDATA'
  AND namespace IN ('{SITE_NAMESPACE}', '{URL_NAMESPACE}')
  AND data_date <= @last_date"""
            try:
                config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter(
                    "last_date", "DATE", request.body["last_date"])],
                    maximum_bytes_billed=self.context.settings.maximum_bytes_billed)
                rows = list(self.context.bq.query(
                    sql, job_config=config, location=location).result())
            except GoogleAPICallError as error:
                raise vendor_error(error) from None
            if len(rows) != 1 or type(rows[0].get("first_date")) is not date:
                raise VendorError(status.FINISHED_WEEK_CHECK)
            first = rows[0]["first_date"]
            self.starts[dataset] = first
            monday = first - timedelta(days=first.weekday())
            counts.append(max(0, (period.start - monday).days // 7))
        return max(counts, default=default)

    def expand(self, planned: Sequence[Request]) -> Sequence[Request]:
        expanded = []
        for request in planned:
            body = request.body
            if "export" not in body:
                expanded.append(request)
                continue
            if (body["export"] in self.starts
                    and date.fromisoformat(body["last_date"]) < self.starts[body["export"]]):
                continue
            try:
                held, epoch = self._readiness(request)
            except VendorError as error:
                if error.error_class == status.ACCESS:
                    expanded.append(request)
                    continue
                raise
            if held == status.FINISHED_WEEK_CHECK:
                raise VendorError(held)
            expanded.append(replace(request, body={**body, "epoch_version": epoch}))
        return expanded

    def _location(self, dataset: str) -> str:
        if dataset not in self.locations:
            try:
                location = self.context.bq.get_dataset(dataset).location
            except GoogleAPICallError as error:
                raise vendor_error(error) from None
            if not isinstance(location, str) or not location:
                raise VendorError(status.FINISHED_WEEK_CHECK)
            self.locations[dataset] = location
        return self.locations[dataset]

    def _query(self, request: Request, sql: str, *, data: bool = False) -> list[dict]:
        body = request.body
        parameters = [
            bigquery.ScalarQueryParameter(name, "DATE", body[name])
            for name in ("first_date", "last_date")
        ]
        if data:
            parameters.append(bigquery.ScalarQueryParameter("site_url", "STRING", body["address"]))
            if body["hostname"] is not None:
                from brand_presence.sources.search_console import page_filter

                parameters.append(
                    bigquery.ScalarQueryParameter(
                        "page_filter", "STRING", page_filter(body["hostname"])
                    )
                )
        config = bigquery.QueryJobConfig(
            query_parameters=parameters,
            maximum_bytes_billed=self.context.settings.maximum_bytes_billed
        )
        try:
            job = self.context.bq.query(
                sql, job_config=config, location=self._location(dataset_of(body["export"]))
            )
            return [dict(row) for row in job.result()]
        except GoogleAPICallError as error:
            raise vendor_error(error) from None

    def check(self, group: str, requests: Sequence[Request]) -> str | None:
        for request in requests:
            held, _ = self._readiness(request)
            if held is not None:
                return held
        return None

    def _readiness(self, request: Request) -> tuple[str | None, int | None]:
        body = request.body
        key = (body["export"], body["first_date"], body["last_date"])
        if key in self.readiness:
            return self.readiness[key]
        try:
            rows = self._query(request, ready_statement(body["export"]))
        except ValueError:
            return status.FINISHED_WEEK_CHECK, None
        first, last = (date.fromisoformat(body[name]) for name in ("first_date", "last_date"))
        held = set()
        for row in rows:
            if (
                set(row) != {"agenda", "namespace", "data_date", "epoch_version", "publish_time"}
                or row["agenda"] != "SEARCHDATA"
                or type(row["data_date"]) is not date
                or not first <= row["data_date"] <= last
                or row["namespace"] not in NAMESPACES
                or type(row["epoch_version"]) is not int
                or row["epoch_version"] < 0
                or not isinstance(row["publish_time"], datetime)
                or row["publish_time"].utcoffset() is None
            ):
                return status.FINISHED_WEEK_CHECK, None
            held.add((row["data_date"], row["namespace"]))
        expected = {
            (first + timedelta(days=n), namespace)
            for n in range((last - first).days + 1)
            for namespace in NAMESPACES
        }
        result = ((None, max(row["epoch_version"] for row in rows)) if expected <= held
                  else (status.NOT_READY, None))
        self.readiness[key] = result
        return result

    def pull(self, request: Request, part: str, page: int) -> Page:
        if page != 1:
            raise ValueError("an export part has one aggregate result")
        body = request.body
        rows = self._query(
            request, statement(body["export"], part, body["hostname"] is not None), data=True
        )
        converted = []
        for row in rows:
            dimension = {
                "totals": (),
                "queries": ("query",),
                "pages": ("page",),
                "devices": ("device",),
            }[part]
            day = row.pop("date", None)
            keys = [day.isoformat() if type(day) is date else day, row.pop("country", None)]
            keys.extend(row.pop(name, None) for name in dimension)
            converted.append({"keys": keys, **row})
        return Page(
            Answer(
                {"export": part, "responseAggregationType": "export", "rows": converted},
                content_date=date.fromisoformat(body["first_date"]),
            ),
            cursor=None,
            more=False,
        )
