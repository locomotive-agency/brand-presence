"""Search volume: Google Ads monthly search volume by keyword and location, from DataForSEO.

A run asks for the three calendar months that end with its period, for every keyword a
benchmark set flags for search volume, in every location of the set's country that the
configuration lists for this source. Keywords are grouped by location into tasks of up to 1,000. A
keyword two sets share is sent once and written once for each set.

A keyword and location whose history was never fetched is also asked for the vendor's full
range, once. "Fetched" is a row in `ops.search_volume_history`, which the sender reads when
it sends and writes when the answer's clean rows are written. `plan` reads no table.

A month a later pull reports again is a new row: the period of the pull is in the key, and
no pull changes a row of an earlier one.

What an answer leaves out is counted on the run's line: a keyword asked with no result
(`keywords_unanswered`), a
result for no keyword asked (`keywords_unmatched`), a result with no list of months
(`keywords_without_months`) and a month whose year or number cannot be read
(`months_unreadable`). A result whose keyword is no text cannot be told from another, and
makes the answer unusable (`keyword unreadable`).
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from typing import ClassVar

from brand_presence import status
from brand_presence.bigquery import query, row_batches, rows_parameter
from brand_presence.client_ref import ClientRef
from brand_presence.rows import CleanRow, LedgerRow, RawRow, Row
from brand_presence.runs import Period
from brand_presence.sources import dataforseo_common as common
from brand_presence.sources.base import (
    Answer,
    Parsed,
    Request,
    SendContext,
    Target,
    Verdict,
    request_of,
    set_of,
    standard_columns,
)

NAME = "search-volume"
PATH = "keywords_data/google_ads/search_volume"

# The most keywords one task holds.
KEYWORDS_PER_TASK = 1000
# The months a run asks for, and the vendor's full range, which a history request asks for.
MONTHS = 3
FULL_RANGE_MONTHS = 48

# What a parse counts.
UNANSWERED = "keywords_unanswered"
UNMATCHED = "keywords_unmatched"
WITHOUT_MONTHS = "keywords_without_months"
MONTHS_UNREADABLE = "months_unreadable"


# Row models --------------------------------------------------------------------------------

class SearchVolumeRaw(RawRow):
    table: ClassVar[str] = "raw.search_volume"


class SearchVolume(CleanRow):
    """One keyword in one location in one calendar month, as one pull reported it."""

    table: ClassVar[str] = "stg.search_volume"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "country_code", "location_id", "keyword", "month", "period",
    )

    keyword: str
    # The first day of the calendar month.
    month: date
    search_volume: int | None
    # The cost and competition figures, as the vendor returns them.
    cpc: float | None
    competition: str | None
    competition_index: int | None
    low_top_of_page_bid: float | None
    high_top_of_page_bid: float | None
    term_type: str
    # The brand the keyword is about, where it is about one.
    subject_brand_id: str | None


class SearchVolumeTask(LedgerRow):
    table: ClassVar[str] = "ops.search_volume_tasks"


class SearchVolumeHistory(Row):
    """A keyword and location whose full range has been fetched and written clean."""

    table: ClassVar[str] = "ops.search_volume_history"

    keyword: str
    location_id: str
    # The history request that fetched it, and its period.
    request_key: str
    period: str
    # The start of the run that wrote its clean rows.
    fetched_at: datetime


# Requests ----------------------------------------------------------------------------------

def requests(settings: ClientRef, window: tuple[date, date] | None = None) -> list[Request]:
    """One request for each task: the flagged keywords of a location, in groups of at most
    1,000, for the sets that hold them. `window` is the first and last day asked for; with
    none the requests are undated, which is enough to count them."""
    # Location -> keyword -> the sets that ask for it there.
    wanted: dict[tuple[str, str, str], dict[str, list[Target]]] = {}
    for benchmark_set in settings.benchmark_sets:
        flagged = [k.keyword for k in benchmark_set.keywords if k.search_volume]
        if not flagged:
            continue
        for place in common.locations_for(settings, NAME, benchmark_set.country_code):
            group = wanted.setdefault(
                (place.location_id, place.canonical_name, benchmark_set.language), {}
            )
            target = Target(benchmark_set.client_brand_id, benchmark_set.country_code,
                            place.location_id)
            for keyword in flagged:
                group.setdefault(keyword, []).append(target)
    found = []
    for (_, canonical_name, language), keywords in sorted(wanted.items()):
        for group in common.chunks(sorted(keywords), KEYWORDS_PER_TASK):
            body: dict[str, object] = {
                "keywords": group, "location_name": canonical_name,
                "language_code": language, "search_partners": False,
            }
            if window is not None:
                body |= {"date_from": window[0].isoformat(), "date_to": window[1].isoformat()}
            targets = dict.fromkeys(target for keyword in group for target in keywords[keyword])
            # Which set wants which keyword. A set that comes to hold a keyword of a task
            # changes neither its body nor its targets, and must change its key.
            wanted_by = tuple((keyword, target.client_brand_id, target.country_code)
                              for keyword in group for target in keywords[keyword])
            found.append(Request(body, tuple(targets), wanted_by))
    return found


def _location_of(request: Request) -> str:
    """The location a request asks for. Every set it serves is asked for the same one."""
    return request.targets[0].location_id


def pairs(asked: Sequence[Request]) -> set[tuple[str, str]]:
    """Every keyword and location the requests ask for."""
    return {(keyword, _location_of(request))
            for request in asked for keyword in request.body["keywords"]}


def full_range_start(last_day: str) -> str:
    """The first day of the vendor's full range that ends with the month of `last_day`."""
    first = date.fromisoformat(last_day).replace(day=1)
    return common.months_before(first, FULL_RANGE_MONTHS - 1).isoformat()


def is_history(body: Mapping[str, object]) -> bool:
    """Whether a request asks for the vendor's full range."""
    last_day = body.get("date_to")
    return isinstance(last_day, str) and body.get("date_from") == full_range_start(last_day)


def history_requests(planned: Sequence[Request],
                     held: Collection[tuple[str, str]]) -> list[Request]:
    """The one-off requests beside the plan's: the full range of every keyword and location
    that is not held yet, grouped as the plan groups them. Dated like the plan, they end
    with the same month; an undated plan gives undated requests, for the count."""
    # Location -> keyword -> the sets of the planned requests that hold it.
    lacking: dict[tuple, dict[str, list[Target]]] = {}
    for request in planned:
        body = request.body
        place = (_location_of(request), body["location_name"], body["language_code"],
                 body.get("date_to"))
        for keyword in body["keywords"]:
            if (keyword, place[0]) not in held:
                lacking.setdefault(place, {}).setdefault(keyword, []).extend(request.targets)
    found = []
    for (_, canonical_name, language, last_day), keywords in sorted(
        lacking.items(), key=lambda item: item[0][:3]
    ):
        for group in common.chunks(sorted(keywords), KEYWORDS_PER_TASK):
            body: dict[str, object] = {
                "keywords": group, "location_name": canonical_name,
                "language_code": language, "search_partners": False,
            }
            if last_day is not None:
                body |= {"date_from": full_range_start(last_day), "date_to": last_day}
            targets = dict.fromkeys(target for keyword in group for target in keywords[keyword])
            found.append(Request(body, tuple(targets)))
    return found


# Sending -----------------------------------------------------------------------------------

class SearchVolumeSend(common.DataForSEOSend):
    """Adds the history requests of the keywords and locations the source does not hold,
    until it is told that their rows are written."""

    def __init__(self, context: SendContext):
        super().__init__(context, PATH)
        self._held: set[tuple[str, str]] | None = None

    def held(self) -> set[tuple[str, str]]:
        """Every keyword and location whose history is fetched. Read once a run."""
        if self._held is None:
            self._held = {
                (row["keyword"], row["location_id"]) for row in query(
                    self.context.bq,
                    f"SELECT keyword, location_id FROM {SearchVolumeHistory.table}",
                )
            }
        return self._held

    def more(self, planned: Sequence[Request]) -> list[Request]:
        outstanding = [r for r in self.context.outstanding if is_history(r.body)]
        reserved = pairs(outstanding) - self.held()
        line = f"Reserved history pairs: {len(reserved)}"
        if reserved:
            sample = sorted(reserved)[:5]
            line += f"; first {len(sample)}: " + ", ".join(repr(pair) for pair in sample)
        self.context.out(line)
        return history_requests(planned, self.held() | reserved)

    def written(self, written: Mapping[str, Request]) -> None:
        held = self.held()
        rows = []
        for key, request in written.items():
            if not is_history(request.body):
                continue
            period = key.split("/")[2]
            for pair in sorted(pairs([request]) - held):
                rows.append(SearchVolumeHistory(
                    keyword=pair[0], location_id=pair[1], request_key=key, period=period,
                    fetched_at=self.context.started_at,
                ))
                held.add(pair)
        columns = ", ".join(SearchVolumeHistory.model_fields)
        for batch in row_batches(SearchVolumeHistory, rows):
            query(
                self.context.bq,
                f"INSERT INTO {SearchVolumeHistory.table} ({columns}) "
                f"SELECT {columns} FROM UNNEST(@rows)",
                [rows_parameter("rows", SearchVolumeHistory, batch)],
            )


# The source --------------------------------------------------------------------------------

class SearchVolumeSource(common.SearchDemandSource):
    name = NAME
    path = PATH
    raw_model = SearchVolumeRaw
    clean_models = (SearchVolume,)
    ledger_model = SearchVolumeTask

    def plan(self, settings: ClientRef, period: Period) -> list[Request]:
        return requests(settings, common.months_ending(period.start, MONTHS))

    def history(self, before: ClientRef | None, after: ClientRef) -> list[Request]:
        held = set() if before is None else pairs(requests(before))
        return history_requests(requests(after), held)

    def send(self, context: SendContext) -> SearchVolumeSend:
        return SearchVolumeSend(context)

    def usable(self, answer: Answer) -> Verdict:
        verdict = super().usable(answer)
        if verdict.usable and any(not isinstance(result.get("keyword"), str)
                                  for result in common.results_of(answer.response)):
            return Verdict(False, status.KEYWORD_UNREADABLE)
        return verdict

    def parse(self, raw: RawRow, settings: ClientRef) -> Parsed:
        request = request_of(raw.request)
        rows = common.Rows()
        # The vendor gives a keyword back in lower case.
        asked = {keyword.casefold() for keyword in request.body["keywords"]}
        answered: set[str] = set()
        # Each month of each keyword asked, as it is read, once for the answer.
        read: dict[str, list[dict[str, object]]] = {}
        for result in common.results_of(common.task_of(raw)):
            folded = result["keyword"].casefold()
            if folded not in asked:
                rows.count(UNMATCHED)
                continue
            answered.add(folded)
            months = result.get("monthly_searches")
            if not isinstance(months, list):
                rows.count(WITHOUT_MONTHS)
                continue
            figures = {
                "cpc": rows.read(common.number, result.get("cpc")),
                "competition": rows.read(common.text_of, result.get("competition")),
                "competition_index": rows.read(common.whole, result.get("competition_index")),
                "low_top_of_page_bid": rows.read(common.number,
                                                 result.get("low_top_of_page_bid")),
                "high_top_of_page_bid": rows.read(common.number,
                                                  result.get("high_top_of_page_bid")),
            }
            for month in months:
                held = month if isinstance(month, dict) else {}
                year, number = common.whole(held.get("year")), common.whole(held.get("month"))
                if year is None or number is None or not 1 <= number <= 12:
                    rows.count(MONTHS_UNREADABLE)
                    continue
                read.setdefault(folded, []).append({
                    **figures, "month": date(year, number, 1),
                    "search_volume": rows.read(common.whole, held.get("search_volume")),
                })
        rows.count(UNANSWERED, len(asked - answered))
        for target in request.targets:
            benchmark_set = set_of(settings, target)
            standard = standard_columns(raw, settings, target)
            # A keyword asked for another set of the same location is not this set's.
            for keyword in benchmark_set.keywords:
                if not keyword.search_volume:
                    continue
                for values in read.get(keyword.keyword.casefold(), []):
                    rows.add(SearchVolume, **standard, **values, keyword=keyword.keyword,
                             term_type=keyword.term_type, subject_brand_id=keyword.brand_id)
        return rows.parsed()
