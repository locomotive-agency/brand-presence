"""Cloro, as the AI answers source calls it: sign in, submit a batch of tasks, get one task.

The asynchronous tasks are used. A batch holds up to 500, and each task carries the key it
is sent under as the vendor's idempotency key. The vendor makes a task for a key once: a
key it has seen is reported as already existing, and no second task is made. So a batch is
sent through src/brand_presence/http.py as a request that carries such a key, and the policy may
send it again.

Whether the vendor names the task it already holds for a key is not known here. The name
is looked for where every other answer of the vendor's has it: the `task` of
the result. A result that reports the key as existing and names no task says so, and the
caller has nothing to collect by.

A result is read for what it shows. One that names a task is a task with the vendor,
whatever its flag or its code. A task is refused only where the result says it failed and
gives an error other than the key existing. A result that shows neither is of unknown
outcome, as an answer that cannot be read is: a refusal uses an attempt and a new key, so
it is not guessed at. One refusal is told apart: the code the vendor gives when the engine
asked is down on its side. The task is unavailable for now, not refused, and the caller
uses no attempt on it.

A task is queued, processing, completed or failed, and nothing else. The credits the vendor
says it charged for a task are not read.

Nothing raised here holds the vendor's text, the request or the key.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import httpx

from brand_presence import http
from brand_presence.http import OutcomeUnknown, VendorError

BASE = "https://api.cloro.dev"

# The secret the vendor's key is kept in.
KEY = "cloro-api-key"

# The vendor's status codes that say a limit is reached, beside the two every vendor shares
# (src/brand_presence/http.py). None is known yet: each is
# added here when it is first seen, with a sample.
LIMIT_CODES: tuple[int, ...] = ()

# The most tasks one batch holds.
TASKS_PER_BATCH = 500
# Every task is sent at the vendor's ordinary priority.
PRIORITY = 1

# The states of a task. The first two are pending and the last two finished. A task in any
# other state, or with none, has failed as `unknown task state`.
QUEUED = "QUEUED"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
PENDING = (QUEUED, PROCESSING)
FINISHED = (COMPLETED, FAILED)

# The vendor's code for a key it has made a task for already.
ALREADY_EXISTS = "RESOURCE_ALREADY_EXISTS"
# The vendor's code for a task it did not make because it cannot reach the engine asked.
UNAVAILABLE = "MODEL_UNAVAILABLE"


@dataclass(frozen=True)
class Taken:
    """What the vendor said to one task of a batch: the ID of the task it made, or of the
    one it holds already where it names it; whether it reports the key as already
    existing; whether it refused the task; and whether it did not make it because the
    engine is unavailable on its side."""

    task_id: str | None = None
    exists: bool = False
    refused: bool = False
    unavailable: bool = False


def _task_id(held: object) -> str | None:
    task = held.get("task") if isinstance(held, dict) else None
    found = task.get("id") if isinstance(task, dict) else None
    return found if isinstance(found, str) and found else None


def status_of(answer: Mapping[str, object]) -> str | None:
    """The state of a task, as its answer gives it."""
    task = answer.get("task")
    status = task.get("status") if isinstance(task, dict) else None
    return status.upper() if isinstance(status, str) else None




class Cloro:
    """One client's Cloro account."""

    def __init__(self, client: httpx.Client, key: str, *,
                 sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._headers = {"Authorization": f"Bearer {key}"}
        self._sleep = sleep

    def submit(self, tasks: Sequence[tuple[str, str, Mapping[str, object]]]
               ) -> list[Taken | None]:
        """Submit up to 500 tasks in one request, each as its idempotency key, its task
        type and its payload. Returns what the vendor said to each, in the order they were
        given, with None for a task its answer does not name."""
        if len(tasks) > TASKS_PER_BATCH:
            raise ValueError(f"a batch holds {TASKS_PER_BATCH} tasks at most")
        response = http.call(
            self._client, "POST", f"{BASE}/v1/async/task/batch", creates_work=True,
            idempotent=True, limit_codes=LIMIT_CODES, sleep=self._sleep, headers=self._headers,
            json=[{"taskType": task_type, "priority": PRIORITY, "idempotencyKey": key,
                   "payload": dict(payload)} for key, task_type, payload in tasks],
        )
        try:
            said = response.json()
        except ValueError:
            said = None
        results = said.get("results") if isinstance(said, dict) else None
        if not isinstance(results, list):
            # An answer that cannot be read says nothing about what the vendor did.
            raise OutcomeUnknown(http.OTHER)
        results = [result for result in results if isinstance(result, dict)]
        taken: list[Taken | None] = [None] * len(tasks)
        for position, result in enumerate(results):
            # By its index. An answer that gives none names its tasks in the order sent,
            # and is trusted only when it names as many as were sent.
            index = result.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                if index is not None or len(results) != len(tasks):
                    continue
                index = position
            if not 0 <= index < len(tasks):
                continue
            error = result.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            task_id = _task_id(result)
            if task_id:
                # A result that names a task: the task is with the vendor, whatever else
                # the result says, and is collected by that ID.
                taken[index] = Taken(task_id, exists=code == ALREADY_EXISTS)
            elif code == ALREADY_EXISTS:
                taken[index] = Taken(exists=True)
            elif result.get("success") is False and code == UNAVAILABLE:
                taken[index] = Taken(unavailable=True)
            elif result.get("success") is False and error:
                taken[index] = Taken(refused=True)
            # Anything else says neither that a task was made nor that it was refused: a
            # task said to be made with no ID, or a result with no word either way. It is
            # left as None, of unknown outcome, and the same key is sent again.
        return taken

    def get(self, task_id: str) -> dict:
        """One task, as the vendor holds it: its state, what it charged and its answer."""
        # A read: it makes nothing at the vendor, so a time-out after it was sent is tried
        # again.
        response = http.call(self._client, "GET", f"{BASE}/v1/async/task/{task_id}",
                             read=True, limit_codes=LIMIT_CODES,
                             sleep=self._sleep,
                             headers=self._headers)
        try:
            said = response.json()
        except ValueError:
            said = None
        if not isinstance(said, dict):
            raise VendorError(http.OTHER)
        return said
