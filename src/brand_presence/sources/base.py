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
from brand_presence.rows import CleanRow, LedgerRow, RawRow
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

class Source(ABC):
    name: ClassVar[str]
    raw_model: ClassVar[type[RawRow]]
    clean_models: ClassVar[tuple[type[CleanRow], ...]]
    ledger_model: ClassVar[type[LedgerRow]]
    unavailable: ClassVar[tuple[str, ...]] = ()
    keeping_seconds: ClassVar[int | None] = None

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
    def find(self, batch: Sequence[Outgoing]) -> Mapping[str, str]:
        """Look at the vendor for submissions of unknown outcome. Each is given as it was
        sent, with the key and the body, for a vendor whose look is the same call as a
        submission under the same key. Returns the task ID of each one found, by its
        key."""

    @abstractmethod
    def collect(self, task_id: str, key: str) -> Answer | None:
        """Ask for one task. Returns its answer when the vendor reports it finished, and
        None while it is queued or in progress. Raises `TaskFailed` when the vendor
        reports that it failed."""



def keyed(requests: Sequence[Request], client_id: str, source: str,
          period: str) -> dict[str, Request]:
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
