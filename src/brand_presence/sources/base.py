"""The task source boundary, requests, answers and clean-row provenance."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import ClassVar

import httpx
from pydantic import ValidationError

from brand_presence import request_keys
from brand_presence.client_ref import BenchmarkSet, ClientRef, InconsistentRef
from brand_presence.rows import UNKNOWN, CleanRow, LedgerRow, RawRow
from brand_presence.runs import Period, period_start

TASKS = "tasks"

@dataclass(frozen=True)
class SendContext:
    keys: Mapping[str, str]
    http: httpx.Client
    bq: object
    started_at: datetime
    sleep: object
    outstanding: tuple[Request, ...] = ()
    out: Callable[[str], None] = print
    settings: object = None
    ref: ClientRef | None = None
    run_id: str | None = None
    deadline: datetime | None = None
    clock: object = None
    tokens: object = None

class Source(ABC):
    name: ClassVar[str]
    raw_model: ClassVar[type[RawRow]]
    clean_models: ClassVar[tuple[type[CleanRow], ...]]
    ledger_model: ClassVar[type[LedgerRow]]
    unavailable: ClassVar[tuple[str, ...]] = ()
    keeping_seconds: ClassVar[int | None] = None
    require_latest_attempt: ClassVar[bool] = False
    durable_pages: ClassVar[bool] = False
    history_periods: ClassVar[int] = 52
    history_requests: ClassVar[int] = 200

    def identity(self, request: Request) -> Mapping[str, object]:
        """The identity fields of a window request, from which its key is made."""
        return {name: request.body[name] for name in self.identity_fields}

    def history_identity(self, request: Request) -> Mapping[str, object]:
        """The measure whose history waits for its current period's successful answer."""
        return self.identity(request)

    def reaches_back(self, request: Request) -> bool:
        """Whether a window request reads dates before its period's first date, up to its
        period's last date (source-framework "A window request may read further back than
        its period"). Such a request is planned, keyed, stored and written under the period
        it was planned for, as any other, and is never sent as history: what it reads is the
        sender's to work out, so a past period's would read the same dates again. Left as
        it is, no request reaches back."""
        return False

    @abstractmethod
    def plan(self, settings: ClientRef, period: Period) -> list[Request]: ...

    @abstractmethod
    def send(self, context: SendContext) -> TaskSend: ...

    @abstractmethod
    def usable(self, answer: Answer) -> Verdict: ...

    @abstractmethod
    def parse(self, raw: RawRow, settings: ClientRef) -> Parsed: ...

@dataclass(frozen=True)
class Target:
    """One benchmark set a request serves, and the location it was asked for. A request
    that two sets share is sent once and parsed once for each."""

    client_brand_id: str
    country_code: str
    location_id: str



@dataclass(frozen=True)
class Request:
    """One vendor request, as `plan` gives it. `body` is what the vendor is asked. It never
    holds a key or a header. `wanted_by` is for a request of many queries that not every
    set wants all of: each query with a set that wants it, as (query, client brand,
    country). The body, the targets and `wanted_by` are what the request's key is a hash
    of, so a request that another set comes to share is a changed request, and is sent.
    A window request's key is made from its identity fields alone, which its source
    names."""

    body: Mapping[str, object]
    targets: tuple[Target, ...] = ()
    wanted_by: tuple[tuple[str, str, str], ...] = ()



@dataclass(frozen=True)
class Outgoing:
    """A request on its way to the vendor. `key` is the key the vendor is given: the
    request's key, with the attempt's number from the second attempt on."""

    key: str
    body: Mapping[str, object]



@dataclass(frozen=True)
class Submitted:
    """What the vendor said to one request of a submission: a task ID, or a refusal with
    its class and the status code the vendor gave it, where it gave one."""

    key: str
    task_id: str | None = None
    error_class: str | None = None
    status_code: int | None = None



@dataclass(frozen=True)
class Answer:
    """One finished answer. `response` is the vendor's answer and nothing else: no key and
    no header. No cost is read from an answer. `content_date` is the date the content is of,
    where it has one. `counts` is what the sender counted on the way to this answer,
    such as items it left out before anything
    was stored. `asked` is
    the body of the request the answer is to. The framework sets it from the ledger before
    a task source's answer is judged, so that `usable` need not trust what the vendor
    echoes; a sender leaves it out, and it is never stored."""

    response: Mapping[str, object]
    content_date: date | None = None
    custom_time: datetime | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    asked: Mapping[str, object] | None = None



@dataclass(frozen=True)
class Verdict:
    """Whether an answer is usable, and the name of the rule it trips when it is not."""

    usable: bool
    rule: str | None = None
    counts: Mapping[str, int] = field(default_factory=dict)



@dataclass(frozen=True)
class Parsed:
    """What `parse` gives when it has more to say than rows. `counts` go on the run's
    operational line.
    `refused` holds the refusal of each item the source left out because a row model
    would not take it: the rest of the answer is written, the run says the row model, the
    field and the kind of error. A `parse` that raises leaves the whole answer unwritten."""

    rows: Sequence[CleanRow]
    counts: Mapping[str, int] = field(default_factory=dict)
    refused: Sequence[ValidationError] = ()



class TaskFailed(Exception):
    """The vendor reports that a task failed. It holds an error class and no vendor text."""

    def __init__(self, error_class: str):
        super().__init__(error_class)
        self.error_class = error_class



class TaskSend(ABC):
    """The vendor's side of a task source. The framework calls it, and writes the ledger
    around each call. Every vendor call uses the shared HTTP retry rules."""

    # How many requests one submission holds, and how long to wait between two rounds of
    # collecting.
    batch_size: int = 100
    poll_seconds: int = 30
    # Whether `find` makes a task at the vendor where it holds none: a vendor whose look
    # is the same call as a submission under the same key. The framework then treats a
    # look as a submission: a batch at a time, with the ledger written after each, and
    # never for a request whose period has ended.
    look_submits: bool = False

    def more(self, planned: Sequence[Request]) -> Sequence[Request]:
        """Requests the run sends beside those `plan` gave, from a table of the source's
        own state: what `plan`, which reads no table, cannot know. The framework keys
        them, writes them to the ledger and counts them like the others. It is asked on
        every run, and gives the same request until `written` has recorded it."""
        return ()

    def written(self, requests: Mapping[str, Request]) -> None:  # noqa: B027 - as above
        """Called once the clean rows of these requests are written, with each request
        under its key. A source that keeps a table of its own state records here what it
        now holds. It is told again when the same rows are written again."""

    @abstractmethod
    def submit(self, batch: Sequence[Outgoing]) -> Sequence[Submitted]:
        """Submit one batch. Returns what the vendor said to each request: a task ID, or
        a refusal. A request the answer leaves out is kept as of unknown outcome, and is
        looked for by `find` before it is sent again; that is how one request of a batch
        is reported as unknown. Raises `OutcomeUnknown` when nothing says whether the
        batch arrived, `NothingTaken` when it never did, and any other `VendorError` when
        the vendor refused all of it. It does not try again itself."""

    @abstractmethod
    def find(self, batch: Sequence[Outgoing]) -> Sequence[Submitted]:
        """Look at the vendor for submissions of unknown outcome. Each is given as it was
        sent, with the key and the body, for a vendor whose look is the same call as a
        submission under the same key. Returns each known task ID or refusal with its
        key. Missing outcomes remain unknown."""

    @abstractmethod
    def collect(self, task_id: str, key: str) -> Answer | None:
        """Ask for one task. Returns its answer when the vendor reports it finished, and
        None while it is queued or in progress. Raises `TaskFailed` when the vendor
        reports that it failed."""



def keyed(requests: Sequence[Request], client_id: str, source: str, period: str,
          identity: Callable[[Request], Mapping[str, object]] | None = None,
          ) -> dict[str, Request]:
    """The requests of a period under their keys, in the order they were given. Two
    requests with the same body are one request: it is sent once, for the sets of both.
    The key is a hash of the body and of the sets the request serves, so a request that
    is answered and that a later load gives another set is sent again, and its rows take
    the place of the old ones. The run and the setup loader's count of
    requests both come through here, so the count is of what a run sends.

    `identity` is a window source's: each request is keyed by its identity fields alone.
    Two requests of one identity that differ in anything else are refused."""
    if identity is not None:
        windows: dict[str, Request] = {}
        for request in requests:
            key = request_keys.window_key(client_id, source, period, identity(request))
            held = windows.get(key)
            if held is not None and held != request:
                raise SameIdentity("two requests of one period have the same identity")
            windows[key] = request
        return windows
    merged: dict[str, Request] = {}
    for request in requests:
        body = request_keys.content_hash(request.body)
        held = merged.get(body)
        merged[body] = request if held is None else Request(
            held.body, tuple(dict.fromkeys((*held.targets, *request.targets))),
            tuple(dict.fromkeys((*held.wanted_by, *request.wanted_by))),
        )
    found: dict[str, Request] = {}
    for request in merged.values():
        content = {
            "body": request.body,
            "targets": sorted([t.client_brand_id, t.country_code, t.location_id]
                              for t in request.targets),
            "wanted_by": sorted(list(wanted) for wanted in request.wanted_by),
        }
        found[request_keys.request_key(client_id, source, period, content)] = request
    return found


def stored_request(request: Request, load_number: int, snapshot_id: str) -> str:
    """A request as the ledger and the raw row hold it: its body, the sets it serves and
    the load it was planned under, as JSON text."""
    return json.dumps({
        "body": request.body,
        "targets": [[t.client_brand_id, t.country_code, t.location_id] for t in request.targets],
        "load_number": load_number,
        "snapshot_id": snapshot_id,
    }, sort_keys=True, ensure_ascii=False)



def request_of(text: str) -> Request:
    """The request a ledger row or a raw row holds."""
    held = json.loads(text)
    return Request(body=held["body"], targets=tuple(Target(*t) for t in held["targets"]))



def planned_under(text: str) -> tuple[int, str]:
    """The load number and the snapshot ID a stored request was planned under."""
    held = json.loads(text)
    return held["load_number"], held["snapshot_id"]



def set_of(settings: ClientRef, target: Target) -> BenchmarkSet:
    """The benchmark set a task source's target names, in the load the answer was
    collected under. The request was planned from that load's sets, so a load without it
    is a fault: it raises `InconsistentRef`, and nothing of the answer is written."""
    found = next(
        (s for s in settings.benchmark_sets
         if (s.client_brand_id, s.country_code) == (target.client_brand_id, target.country_code)),
        None,
    )
    if found is None:
        raise InconsistentRef("the load holds no benchmark set a request was planned for")
    return found



def standard_columns(raw: RawRow, settings: ClientRef, target: Target) -> dict[str, object]:
    """The standard columns of a clean row, for one target a raw answer serves. The
    benchmark version is the one in force at the load the answer was collected under. A
    task source's target is a set, which `set_of` reads first and raises on where the load
    lacks it. Any other target is no set: a window source's `all`, an `unknown`, or a
    mention filter's brand in a country it has no set in. Its version is None, which says
    that, and is no guess."""
    version = next(
        (s.version for s in settings.benchmark_sets
         if (s.client_brand_id, s.country_code) == (target.client_brand_id, target.country_code)),
        None,
    )
    return {
        "client_brand_id": target.client_brand_id,
        "country_code": target.country_code,
        "location_id": target.location_id,
        "benchmark_version": version,
        "snapshot_id": raw.snapshot_id,
        "period": raw.period,
        "period_start": period_start(raw.period),
        "request_key": raw.request_key,
        "run_id": raw.run_id,
        "pulled_at": raw.pulled_at,
    }



SUCCESS = "success"



PARTIAL = "partial"



ERROR = "error"



EXIT_SUCCESS = 0



EXIT_ERROR = 1



EXIT_REFUSED = 2



EXIT_PARTIAL = 3



EXIT_CODES = {SUCCESS: EXIT_SUCCESS, PARTIAL: EXIT_PARTIAL, ERROR: EXIT_ERROR}



@dataclass
class Result:
    """How a run ended. `lines` are plain lines for the job's output; none holds vendor
    text, a query value or a key."""

    status: str
    run_id: str = ""
    period: str | None = None
    failed_check: str | None = None
    error_class: str | None = None
    error_count: int | None = None
    # The status code the vendor gave with the error class, where it gave one.
    vendor_status: int | None = None
    # The name of the secret a `key` check stopped on.
    secret: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    # What the source counted.
    source_counts: dict[str, int] = field(default_factory=dict)
    # A window source's run alone: the pulls it wrote that were sampled, and those that hit
    # a row limit.
    pull_counts: dict[str, int] = field(default_factory=dict)
    # Operational lines beside the run's own, each as its fields: the requests the vendor
    # rejected, by the location they were asked for.
    extra: list[dict[str, object]] = field(default_factory=list)
    # A window source's run alone, and operational lines too: each group its source
    # reported as refused, with the class and the status code of the refusal.
    refused_groups: list[dict[str, object]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    refused: bool = False

    @property
    def exit_code(self) -> int:
        return EXIT_REFUSED if self.refused else EXIT_CODES[self.status]


CURSOR = "cursor"

@dataclass(frozen=True)
class Page:
    """One page of a pull. `more` says another page follows. For a cursor source `cursor`
    is where the next run starts if the pull ends with this page; a window source, whose
    page is one page of one part of a request, leaves it None."""

    answer: Answer
    cursor: str | None
    more: bool

@dataclass(frozen=True)
class Resume:
    """Where a cursor source's pull, cut short, goes on from on the next run. `cursor` is
    the cursor the next run is given: the one this run was given, or, where it had none,
    where this pull asked from. `point` is the source's own, and only it reads it."""

    cursor: str
    point: str

class UnreadableState(Exception):
    """A cursor source's own state cannot be read: its cursor, or the point its last pull
    was cut short at. It holds an error class and no value of the state. The run stops
    before any vendor call, and the row stays until the administrator clears it."""

    def __init__(self, error_class: str):
        super().__init__(error_class)
        self.error_class = error_class

class CursorSend(ABC):
    """The vendor's side of a cursor source."""

    # The most pages one run pulls.
    max_pages: int = 1000

    def write_pending(self, pages: int) -> bool:
        """Whether to write and release the pending pages before pulling another.
        Only the archive backfill opts in, at line boundaries and a bounded page count.
        The default keeps the daily pull's single write after all pages. A failed write
        stops the pull; no cursor or resume point moves past unwritten pages."""
        return False

    @abstractmethod
    def pull(self, cursor: str | None, page: int) -> Page:
        """One page of what the vendor holds after the cursor. With no cursor it is the
        source's to say where a first run starts."""

    def counts(self) -> Mapping[str, int]:
        """What the sender counted over the whole pull that belongs to no one page, such
        as the waits for a rate limit, under names src/brand_presence/logs.py lists as a source's
        counts. The framework reads it once, when the pull ends, however it ended."""
        return {}

    def go_on(self, cursor: str | None, point: str | None) -> None:  # noqa: B027
        """Told, before the first page, the cursor the run holds and where the last run's
        pull was cut short: the point of its `Resume`, or None. Only a source whose cursor
        model is a `ResumingCursorRow` is ever given a point. Raises `UnreadableState` with
        a named class where either cannot be read, and the run asks the vendor nothing:
        neither is passed over. A source with nothing to read in its cursor leaves this as
        it is."""

    def resume(self, cursor: str | None) -> Resume | None:
        """Where the next run goes on from, when this pull is cut short, given the cursor
        this run was given. The framework asks once the pull has ended short of the end
        and every page it stored is written clean, and keeps it beside the cursor. None
        leaves the next run to start from the cursor, as a source that never resumes
        does."""
        return None


WINDOW = "window"

class WindowSend(ABC):
    """The vendor's side of a window source. A request is answered at once, in the named
    parts below and in numbered pages inside each part. The framework asks for one page at
    a time, stores each raw as it arrives, and writes the ledger around the whole request.
    Every call goes through src/brand_presence/http.py, marked as a read."""

    # The parts of every request, in the order they are pulled.
    parts: tuple[str, ...] = ("rows",)
    # The most pages of one part that are read. A part that still has more is cut there;
    # the source flags its pull row, and the answer is still an answer.
    max_pages: int = 1

    def history_periods(self, period: Period, default: int) -> int:
        """Number of weekly history periods; export senders may discover their own start."""
        return default

    def expand(self, planned: Sequence[Request]) -> Sequence[Request]:
        """Discover source-owned identities under the run lock before requests are keyed.

        The default preserves pure plans. A source with vendor-owned identities records
        their versioned state itself and captures the version in each returned request.
        A refused discovery stops the run; no partial list may be planned.
        """
        return planned

    def check(self, group: str, requests: Sequence[Request]) -> str | None:
        """Asked before anything is sent for a group and a period, with the group's requests
        of that period that the run would send. Returns the error class to hold them back
        with, or None to let them through. It may call the vendor. A `VendorError` it
        raises holds the group back with the error's class, or refuses the group for the
        run where the source names that class as a refusal. A source with nothing to check
        leaves this as it is."""
        return None

    def counts(self) -> Mapping[str, int]:
        """Drain counts after each check or request, including a failed call."""
        return {}

    def skip_part(self, request: Request, part: str) -> bool:
        """An empty dependent part has no vendor response to fetch or fabricate."""
        return False

    def restore(self, request: Request, part: str, page: int, response: Mapping):
        """Restore pagination state from a durable page before reading the next one."""
        return None

    def written(self, requests: Mapping[str, Request]) -> None:  # noqa: B027 - as TaskSend
        """Called once the clean rows of these requests are written, with each request
        under its key."""

    @abstractmethod
    def pull(self, request: Request, part: str, page: int) -> Page:
        """One page of one part of a request. Pages are numbered from 1. A page that is no
        page of rows, as a block page is, is handed on as it came: it is the source's to
        judge. Raises `VendorError` when the call failed after the retries."""

class SameIdentity(ValueError):
    """Two window requests of one period have the same identity fields and are not the same
    request. One of them would never be sent, so the plan is refused."""

def stored_page(text: str, part: str, page: int) -> str:
    """A stored request, as the raw row of one page holds it."""
    return json.dumps({**json.loads(text), "part": part, "page": page}, sort_keys=True,
                      ensure_ascii=False)

def page_of(text: str) -> tuple[str, int]:
    """The part and the page number a page's raw row holds."""
    held = json.loads(text)
    return held["part"], held["page"]

def with_pages(text: str, pages: Sequence[tuple[str, int]]) -> str:
    """A stored request with the complete answer's pages: each part and how many pages of
    it were stored, in the order the parts were pulled."""
    return json.dumps({**json.loads(text), "pages": [list(pair) for pair in pages]},
                      sort_keys=True, ensure_ascii=False)

def pages_of(text: str) -> list[tuple[str, int]] | None:
    """The parts of a complete answer and the pages stored of each, or None where the
    stored request names none: the answer was never complete."""
    held = json.loads(text).get("pages")
    return None if held is None else [(part, count) for part, count in held]

class Incomplete(ValueError):
    """The raw rows given are not every part and page of one complete answer."""

def assembled(text: str, pages: Sequence[RawRow]) -> RawRow:
    """One complete answer as `parse` is given it, from the raw rows of its pages. `text`
    is the request the ledger row holds, with its pages. The answer's response is
    `{"parts": {<part>: [<page>, ...]}}`, each part's pages in order; it is pulled at the
    time of its last page. Raises `Incomplete` when a page is missing or held twice."""
    expected = pages_of(text)
    if expected is None:
        raise Incomplete("the ledger row names no pages")
    by_place: dict[tuple[str, int], RawRow] = {}
    for row in pages:
        place = page_of(row.request)
        if place in by_place:
            raise Incomplete("a page is held twice")
        by_place[place] = row
    wanted = [(part, number) for part, count in expected for number in range(1, count + 1)]
    if not wanted or set(wanted) != set(by_place):
        raise Incomplete("the pages held are not the pages of the answer")
    parts = {part: [json.loads(by_place[(part, number)].response)
                    for number in range(1, count + 1)]
             for part, count in expected}
    last = max(by_place.values(), key=lambda row: row.pulled_at)
    held = json.loads(text)
    held.pop("pages")
    return type(last)(
        run_id=last.run_id, request_key=last.request_key, period=last.period,
        content_date=by_place[wanted[0]].content_date, pulled_at=last.pulled_at,
        request=json.dumps(held, sort_keys=True, ensure_ascii=False),
        response=json.dumps({"parts": parts}, ensure_ascii=False), usable=True,
        load_number=last.load_number, snapshot_id=last.snapshot_id,
    )


def in_country(columns: Mapping[str, object], settings: ClientRef,
               country_code: str) -> dict[str, object]:
    if country_code == UNKNOWN:
        location_id = UNKNOWN
    else:
        location_id = next(
            (item.location_id for item in settings.locations
             if item.kind == "country" and item.country_code == country_code),
            None,
        )
        if location_id is None:
            raise InconsistentRef("the load holds no location of kind country for a country "
                                  "an answer reports")
    version = next(
        (s.version for s in settings.benchmark_sets
         if (s.client_brand_id, s.country_code) == (columns["client_brand_id"], country_code)),
        None,
    )
    return {**columns, "country_code": country_code, "location_id": location_id,
            "benchmark_version": version}


def in_city(columns: Mapping[str, object], settings: ClientRef, country_code: str,
            city_location_id: str | None) -> dict[str, object]:
    placed = in_country(columns, settings, country_code)
    if city_location_id is not None:
        placed["location_id"] = city_location_id
    return placed
