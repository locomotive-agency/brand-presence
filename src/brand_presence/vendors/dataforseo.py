"""The DataForSEO task API. Billable posts are never retried blindly."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import httpx

from brand_presence import http, status
from brand_presence.http import NothingTaken, OutcomeUnknown, VendorError

BASE = "https://api.dataforseo.com/v3"

# The two secrets the vendor's one login is kept in.
LOGIN = "dataforseo-login"
PASSWORD = "dataforseo-password"  # noqa: S105 - the name of a secret, not its value

# The most tasks one post holds.
TASKS_PER_POST = 100

# The vendor's status codes: an envelope or a finished task that is a success, a task that
# was created, and a task that is not finished yet.
OK = 20000
TASK_CREATED = 20100
IN_PROGRESS = (40601, 40602)
# The status code of a finished task the vendor found nothing for, "No Search Results.". An
# ads transparency task gives it for a target that runs no ads, with no result. It is the
# task's own code, at the top level of the task a get returns, which is what is stored.
NO_SEARCH_RESULTS = 40102
# The vendor's codes that say a limit is reached. The one known is an envelope turned away
# by a rate limit, of which the vendor took nothing. Which codes it gives for a spent
# balance or a plan limit is not known (add-run-monitoring, design.md, A12): each is added
# here when it is first seen, with a sample.
LIMIT_CODES = (40202,)
# From this code up, the fault is the vendor's own.
VENDOR_FAULTS_FROM = 50000


@dataclass(frozen=True)
class Posted:
    """What the vendor said to one task of a post: the tag it was sent with, where the
    answer gives it, a task ID or the status code of the refusal, and whether the vendor
    said it created the task, which it may say and give no ID."""

    tag: str | None
    task_id: str | None
    status_code: int | None
    created: bool = False


@dataclass(frozen=True)
class Ready:
    """One task on the ready list, with the tag it was posted with."""

    task_id: str
    tag: str | None


def _code(held: Mapping) -> int | None:
    code = held.get("status_code")
    return code if isinstance(code, int) and not isinstance(code, bool) else None


class DataForSEO:
    """One client's DataForSEO account. `path` in each call is the endpoint's own, such as
    `serp/google/organic`."""

    def __init__(self, client: httpx.Client, login: str, password: str, *,
                 sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._auth = (login, password)
        self._sleep = sleep

    def _call(self, method: str, path: str, *, creates_work: bool = False,
              **request) -> list[dict]:
        """One call, and the tasks of its answer. A call that creates no work is a read, so a
        time-out after it was sent is tried again. Raises VendorError for an envelope that
        is not a success, with the vendor's own code."""
        response = http.call(
            self._client, method, f"{BASE}/{path}", creates_work=creates_work,
            read=not creates_work, limit_codes=LIMIT_CODES,
            sleep=self._sleep, auth=self._auth, **request,
        )
        try:
            envelope = response.json()
        except ValueError:
            envelope = None
        if not isinstance(envelope, dict):
            # An answer that cannot be read says nothing about what the vendor did.
            if creates_work:
                raise OutcomeUnknown(http.OTHER)
            raise VendorError(http.OTHER)
        code = _code(envelope)
        if code != OK:
            if code in LIMIT_CODES:
                raise NothingTaken(http.LIMIT, code)
            if code is None or code >= VENDOR_FAULTS_FROM:
                if creates_work:
                    raise OutcomeUnknown(http.VENDOR_FAULT, code)
                raise VendorError(http.VENDOR_FAULT, code)
            raise VendorError(http.REFUSED, code)
        tasks = envelope.get("tasks")
        if not isinstance(tasks, list):
            # A success that lists no tasks says nothing of what the vendor did.
            if creates_work:
                raise OutcomeUnknown(status.TASKS_MISSING)
            raise VendorError(status.TASKS_MISSING)
        return [task for task in tasks if isinstance(task, dict)]

    def post(self, path: str, tasks: Sequence[Mapping[str, object]]) -> list[Posted]:
        """Post up to 100 tasks in one request, each with its tag. Returns what the vendor
        said to each, in the order of its answer. It is not sent again once it may have
        arrived."""
        if len(tasks) > TASKS_PER_POST:
            raise ValueError(f"a post holds {TASKS_PER_POST} tasks at most")
        said = self._call("POST", f"{path}/task_post", creates_work=True,
                          json=[dict(task) for task in tasks])
        posted = []
        for task in said:
            data = task.get("data")
            tag = data.get("tag") if isinstance(data, dict) else None
            code = _code(task)
            task_id = task.get("id") if code == TASK_CREATED else None
            posted.append(Posted(
                tag=tag if isinstance(tag, str) else None,
                task_id=task_id if isinstance(task_id, str) and task_id else None,
                status_code=code, created=code == TASK_CREATED,
            ))
        return posted

    def ready(self, path: str) -> list[Ready]:
        """The tasks of an endpoint that the vendor lists as ready, with their tags. Raises
        VendorError when the list itself failed at the vendor: a list that could not be
        made says nothing about what is ready."""
        found = []
        for task in self._call("GET", f"{path}/tasks_ready"):
            code = _code(task)
            if code != OK:
                fault = code is None or code >= VENDOR_FAULTS_FROM
                raise VendorError(http.VENDOR_FAULT if fault else http.REFUSED, code)
            for item in task.get("result") or []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    tag = item.get("tag")
                    found.append(Ready(item["id"], tag if isinstance(tag, str) else None))
        return found

    def get(self, path: str, task_id: str, suffix: str = "task_get") -> dict:
        """One task, as the vendor holds it: its own status code and its result."""
        tasks = self._call("GET", f"{path}/{suffix}/{task_id}")
        if not tasks:
            raise VendorError(http.OTHER)
        return tasks[0]

