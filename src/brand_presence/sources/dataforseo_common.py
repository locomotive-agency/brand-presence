"""DataForSEO task submission, recovery and parsing helpers."""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import ClassVar

from pydantic import ValidationError

from brand_presence import status
from brand_presence.client_ref import ClientRef, Location
from brand_presence.rows import CleanRow, RawRow
from brand_presence.sources.base import (
    TASKS,
    Answer,
    Outgoing,
    Parsed,
    SendContext,
    Source,
    Submitted,
    TaskSend,
    Verdict,
)
from brand_presence.vendors import dataforseo
from brand_presence.vendors.dataforseo import DataForSEO

TASK_REFUSED = status.TASK_REFUSED



TASK_ERROR = status.TASK_ERROR



NO_RESULT = status.NO_RESULT



NO_ITEMS = status.NO_ITEMS



UNREADABLE = "values_unreadable"



class DataForSEOSend(TaskSend):
    """The vendor's side of a search demand source's run."""

    batch_size = dataforseo.TASKS_PER_POST

    def __init__(self, context: SendContext, path: str, get_suffix: str = "task_get"):
        self.context = context
        self.path = path
        self.get_suffix = get_suffix
        self.api = DataForSEO(
            context.http, context.keys[dataforseo.LOGIN], context.keys[dataforseo.PASSWORD],
            sleep=context.sleep,
        )

    def submit(self, batch: Sequence[Outgoing]) -> list[Submitted]:
        posted = self.api.post(self.path, [{**item.body, "tag": item.key} for item in batch])
        keys = [item.key for item in batch]
        said = []
        for position, answer in enumerate(posted):
            # By its tag. An answer that gives no tag names its tasks in the order sent,
            # and is trusted only when it names as many as were sent.
            if answer.tag in keys:
                key = answer.tag
            elif answer.tag is None and len(posted) == len(batch):
                key = keys[position]
            else:
                continue
            if answer.task_id:
                said.append(Submitted(key, task_id=answer.task_id))
            elif (answer.created or answer.status_code is None
                  or not 40000 <= answer.status_code < 50000):
                # Created and not named: it may be at the vendor, so it is not posted
                # again. It is of unknown outcome, and looked for by its tag.
                continue
            else:
                said.append(Submitted(key, error_class=TASK_REFUSED,
                                      status_code=answer.status_code))
        # A request the answer left out stays of unknown outcome, and is looked for.
        return said

    def find(self, batch: Sequence[Outgoing]) -> list[Submitted]:
        by_tag = {ready.tag: ready.task_id for ready in self.api.ready(self.path) if ready.tag}
        return [Submitted(item.key, task_id=by_tag[item.key])
                for item in batch if item.key in by_tag]

    def collect(self, task_id: str, key: str) -> Answer | None:
        task = self.api.get(self.path, task_id, self.get_suffix)
        if task.get("status_code") in dataforseo.IN_PROGRESS:
            return None
        return Answer(task)



def usable(task: Mapping[str, object], *, needs_result: bool = True,
           nothing_found: Sequence[int] = (), needs_items: bool = False) -> Verdict:
    """Judge a finished task. It is no answer when its own status code is not the vendor's
    success code, when its result is no list, or an empty one where the request must give
    a result, or, where `needs_items`, when a result holds no list of items. A code in
    `nothing_found` is the vendor's word that it found nothing to list: an answer with no
    rows."""
    code = task.get("status_code")
    if code in nothing_found:
        return Verdict(True)
    if code != dataforseo.OK:
        return Verdict(False, TASK_ERROR)
    result = task.get("result")
    if not isinstance(result, list) or (needs_result and not result):
        return Verdict(False, NO_RESULT)
    if needs_items and any(not isinstance(found.get("items"), list)
                           for found in results_of(task)):
        return Verdict(False, NO_ITEMS)
    return Verdict(True)



class SearchDemandSource(Source):
    """Shared task submission and answer validation for DataForSEO search demand."""

    shape = TASKS
    keeping_seconds = dataforseo.RESULT_RETENTION_SECONDS
    # The endpoint's own path, and what follows it in a get.
    path: ClassVar[str]
    get_suffix: ClassVar[str] = "task_get"
    # Whether a finished task with no result is no answer.
    needs_result: ClassVar[bool] = True
    # The task status codes that say the vendor found nothing to list. A task that finishes
    # with one is a usable answer that writes no row.
    nothing_found: ClassVar[tuple[int, ...]] = ()
    # Whether each result holds a list of items, which a result without one is no answer.
    needs_items: ClassVar[bool] = False

    def send(self, context: SendContext) -> DataForSEOSend:
        return DataForSEOSend(context, self.path, self.get_suffix)

    def usable(self, answer: Answer) -> Verdict:
        return usable(answer.response, needs_result=self.needs_result,
                      nothing_found=self.nothing_found, needs_items=self.needs_items)



def locations_for(settings: ClientRef, source: str, country_code: str) -> list[Location]:
    """The locations of a country that the configuration lists for a source, by their ID."""
    return [place for place in settings.locations
            if place.country_code == country_code and source in place.sources]



def chunks(items: Sequence, size: int) -> list[list]:
    """The items in as few groups of at most `size` as hold them all."""
    return [list(items[at:at + size]) for at in range(0, len(items), size)]



def months_before(first: date, months: int) -> date:
    """The first day of the month that is `months` before the month of `first`."""
    index = first.year * 12 + (first.month - 1) - months
    return date(index // 12, index % 12 + 1, 1)



def months_ending(first: date, months: int) -> tuple[date, date]:
    """The calendar months that end with the month of `first`: the first day of the
    earliest and the last day of the latest."""
    return months_before(first, months - 1), months_before(first, -1) - timedelta(days=1)



def task_of(raw: RawRow) -> dict:
    """The task a raw row holds."""
    return json.loads(raw.response)



def results_of(task: Mapping[str, object]) -> list[dict]:
    """The results of a task, none where it holds none."""
    result = task.get("result")
    return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []



def number(value: object) -> float | None:
    """A figure the vendor gives as a number, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)



def whole(value: object) -> int | None:
    """A count the vendor gives as a whole number, or None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None



def text_of(value: object) -> str | None:
    return value if isinstance(value, str) and value else None



class Rows:
    """The clean rows of one answer, as they are made, and what the answer could not give.
    An item a row model refuses is left out and handed in as a refusal: the rest of the
    answer is written, and the run says the row model, the field and the kind of error.
    What the parse counts goes on the run's output line.
    A value is read once for the answer, not once for each set it serves."""

    def __init__(self) -> None:
        self.rows: list[CleanRow] = []
        self.refused: list[ValidationError] = []
        self.counts: Counter[str] = Counter()

    def add(self, model: type[CleanRow], **values: object) -> None:
        try:
            self.rows.append(model(**values))
        except ValidationError as refused:
            self.refused.append(refused)

    def count(self, name: str, how_many: int = 1) -> None:
        self.counts[name] += how_many

    def read(self, reader: Callable[[object], object], value: object) -> object:
        """A value of the vendor's, as `reader` reads it. Null, or empty text, is None; a
        value in a form `reader` cannot read is None too, and counted as unreadable."""
        found = reader(value)
        if found is None and value is not None and value != "":
            self.count(UNREADABLE)
        return found

    def parsed(self) -> Parsed:
        return Parsed(self.rows, counts={name: n for name, n in self.counts.items() if n},
                      refused=self.refused)
