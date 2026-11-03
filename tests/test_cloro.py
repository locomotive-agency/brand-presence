"""Mocked Cloro calls preserve task IDs, bound retries without blindly repeating
billable requests, and keep vendor text, query values and keys out of logs and errors."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from brand_presence import http
from brand_presence.http import NothingTaken, OutcomeUnknown, VendorError
from brand_presence.vendors import cloro
from brand_presence.vendors.cloro import BASE, Cloro, Taken

BATCH = f"{BASE}/v1/async/task/batch"
KEY_VALUE = "marker-key-value"
MARKER = "marker-vendor-text"

ONE = ("sample/ai-answers/a-week/hash-1", "CHATGPT", {"prompt": "marker-prompt-one"})
TWO = ("sample/ai-answers/a-week/hash-2", "GOOGLE", {"query": "marker-prompt-two"})


def api() -> tuple[Cloro, list[float]]:
    waits: list[float] = []
    return Cloro(httpx.Client(), KEY_VALUE, sleep=waits.append), waits


def results(*said: dict) -> httpx.Response:
    return httpx.Response(200, json={"results": list(said)})


def made(index: int, task_id: str) -> dict:
    return {"success": True, "index": index, "task": {"id": task_id, "status": "QUEUED"},
            "credits": {"creditsToCharge": 5, "creditsCharged": None}}


def exists(index: int, task_id: str | None = None) -> dict:
    said = {"success": False, "index": index,
            "error": {"code": cloro.ALREADY_EXISTS, "message": MARKER}}
    return said if task_id is None else {**said, "task": {"id": task_id}}


@respx.mock
def test_every_call_signs_in_with_the_secret_and_no_other_way():
    batch = respx.post(BATCH).mock(return_value=results(made(0, "task-1")))
    get = respx.get(f"{BASE}/v1/async/task/task-1").mock(
        return_value=httpx.Response(200, json={"task": {"id": "task-1", "status": "QUEUED"}}))
    vendor, _ = api()
    vendor.submit([ONE])
    vendor.get("task-1")
    for route in (batch, get):
        request = route.calls.last.request
        assert request.headers["authorization"] == f"Bearer {KEY_VALUE}"
        # The key is in neither the address nor what is sent.
        assert KEY_VALUE not in str(request.url) and KEY_VALUE.encode() not in request.content


@respx.mock
def test_a_batch_sends_each_task_with_its_key_and_reads_an_id_for_each():
    route = respx.post(BATCH).mock(return_value=results(made(1, "task-2"), made(0, "task-1")))
    vendor, _ = api()
    assert vendor.submit([ONE, TWO]) == [Taken("task-1"), Taken("task-2")]
    assert json.loads(route.calls.last.request.content) == [
        {"taskType": "CHATGPT", "priority": cloro.PRIORITY, "idempotencyKey": ONE[0],
         "payload": {"prompt": "marker-prompt-one"}},
        {"taskType": "GOOGLE", "priority": cloro.PRIORITY, "idempotencyKey": TWO[0],
         "payload": {"query": "marker-prompt-two"}},
    ]


@respx.mock
def test_a_key_the_vendor_reports_as_existing_names_its_task_or_does_not():
    route = respx.post(BATCH)
    vendor, _ = api()
    # With the existing task's ID in the answer: there is a task to collect by.
    route.mock(return_value=results(exists(0, "task-held"), made(1, "task-2")))
    assert vendor.submit([ONE, TWO]) == [Taken("task-held", exists=True), Taken("task-2")]
    # Without it: the key is reported as existing, and nothing names the task. That is
    # neither a task made nor a refusal.
    route.mock(return_value=results(exists(0), made(1, "task-2")))
    said = vendor.submit([ONE, TWO])
    assert said == [Taken(None, exists=True), Taken("task-2")]
    assert not said[0].refused


@respx.mock
def test_a_refused_task_and_a_task_the_answer_leaves_out_are_told_apart():
    route = respx.post(BATCH)
    vendor, _ = api()
    route.mock(return_value=results(
        {"success": False, "index": 0, "error": {"code": "INVALID", "message": MARKER}}))
    # The first was refused. Nothing was said of the second.
    assert vendor.submit([ONE, TWO]) == [Taken(refused=True), None]
    # A result with no index is read by its place only when every task has a result.
    route.mock(return_value=results({"success": True, "task": {"id": "task-1"}}))
    assert vendor.submit([ONE, TWO]) == [None, None]
    route.mock(return_value=results({"success": True, "task": {"id": "task-1"}},
                                    {"success": True, "task": {"id": "task-2"}}))
    assert vendor.submit([ONE, TWO]) == [Taken("task-1"), Taken("task-2")]
    # A task said to be made, with no ID to collect it by, is not counted as made.
    route.mock(return_value=results({"success": True, "index": 0, "task": {}},
                                    {"success": True, "index": 7, "task": {"id": "task-9"}}))
    assert vendor.submit([ONE, TWO]) == [None, None]


@respx.mock
def test_an_engine_down_at_the_vendor_is_told_apart_from_a_refusal():
    route = respx.post(BATCH)
    vendor, _ = api()
    route.mock(return_value=results(
        {"success": False, "index": 0,
         "error": {"code": "MODEL_UNAVAILABLE", "message": MARKER}},
        {"success": False, "index": 1, "error": {"code": "INVALID", "message": MARKER}}))
    said = vendor.submit([ONE, TWO])
    assert said == [Taken(unavailable=True), Taken(refused=True)]
    assert not said[0].refused and not said[1].unavailable
    assert MARKER not in repr(said)
    # A result that names a task is a task, whatever its code says.
    route.mock(return_value=results(
        {"success": False, "index": 0, "task": {"id": "task-1"},
         "error": {"code": "MODEL_UNAVAILABLE", "message": MARKER}}))
    assert vendor.submit([ONE]) == [Taken("task-1")]
    # The code with no word that the task failed says nothing either way.
    route.mock(return_value=results(
        {"index": 0, "error": {"code": "MODEL_UNAVAILABLE", "message": MARKER}}))
    assert vendor.submit([ONE]) == [None]


@respx.mock
def test_a_result_is_read_for_what_it_shows_and_a_refusal_is_not_guessed_at():
    route = respx.post(BATCH)
    vendor, _ = api()
    # A result that names a task is a task with the vendor: with no flag, with the flag
    # set against it, and under a code for an existing key that is not the one known here.
    route.mock(return_value=results({"index": 0, "task": {"id": "task-1", "status": "QUEUED"}}))
    assert vendor.submit([ONE]) == [Taken("task-1")]
    route.mock(return_value=results(
        {"success": False, "index": 0, "task": {"id": "task-1"},
         "error": {"code": "IDEMPOTENCY_KEY_REUSED", "message": MARKER}}))
    assert vendor.submit([ONE]) == [Taken("task-1")]
    # A result that says nothing either way is of unknown outcome, not a refusal.
    for said in ({"index": 0}, {"success": False, "index": 0}, {"success": True, "index": 0},
                 {"index": 0, "error": {"code": "INVALID", "message": MARKER}}):
        route.mock(return_value=results(said))
        assert vendor.submit([ONE]) == [None]
    # A refusal says it failed, and why.
    route.mock(return_value=results(
        {"success": False, "index": 0, "error": {"code": "INVALID", "message": MARKER}}))
    assert vendor.submit([ONE]) == [Taken(refused=True)]


def test_a_batch_of_more_tasks_than_the_vendor_takes_is_refused_before_it_is_sent():
    vendor, _ = api()
    with pytest.raises(ValueError):
        vendor.submit([(f"key-{n}", "CHATGPT", {}) for n in range(cloro.TASKS_PER_BATCH + 1)])


@respx.mock
def test_a_batch_is_sent_again_after_a_read_time_out_because_it_carries_its_keys():
    route = respx.post(BATCH).mock(side_effect=[
        httpx.ReadTimeout("timed out"), results(exists(0, "task-held")),
    ])
    vendor, waits = api()
    # The first try may have arrived. The second is answered with the task it made.
    assert vendor.submit([ONE]) == [Taken("task-held", exists=True)]
    assert route.call_count == 2 and len(waits) == 1
    first, second = (json.loads(call.request.content) for call in route.calls)
    assert first == second


@respx.mock
def test_a_batch_whose_outcome_nothing_says_is_told_apart_from_one_refused():
    route = respx.post(BATCH)
    vendor, _ = api()
    # Time-outs that outlast the retries, and an answer that cannot be read.
    route.mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(OutcomeUnknown) as raised:
        vendor.submit([ONE])
    assert raised.value.error_class == http.TIMEOUT and route.call_count == http.RETRIES + 1
    for answer in (httpx.Response(200, text=MARKER), httpx.Response(200, json={"ok": True})):
        route.mock(side_effect=None, return_value=answer)
        with pytest.raises(OutcomeUnknown):
            vendor.submit([ONE])
    # Turned away by the rate limit: nothing was taken.
    route.mock(return_value=httpx.Response(429, headers={"retry-after": "900"}, text=MARKER))
    with pytest.raises(NothingTaken):
        vendor.submit([ONE])
    # Refused outright, as for a key the vendor does not know.
    route.mock(return_value=httpx.Response(401, text=f"{MARKER} {KEY_VALUE}"))
    with pytest.raises(VendorError) as refused:
        vendor.submit([ONE])
    assert not isinstance(refused.value, OutcomeUnknown | NothingTaken)
    assert (refused.value.error_class, refused.value.status_code) == (http.REFUSED, 401)
    for secret in (MARKER, KEY_VALUE, "marker-prompt-one"):
        assert secret not in str(refused.value) and secret not in repr(refused.value)


@respx.mock
def test_a_get_gives_the_task_as_the_vendor_holds_it_and_is_tried_again():
    route = respx.get(f"{BASE}/v1/async/task/task-1").mock(side_effect=[
        httpx.Response(503, text=MARKER),
        httpx.Response(200, json={
            "task": {"id": "task-1", "status": "completed"},
            "credits": {"creditsToCharge": 5, "creditsCharged": 5},
            "response": {"text": "An answer."},
        }),
    ])
    vendor, _ = api()
    answer = vendor.get("task-1")
    assert route.call_count == 2
    assert cloro.status_of(answer) == cloro.COMPLETED
    assert answer["response"] == {"text": "An answer."}


@respx.mock
def test_a_get_that_cannot_be_read_or_is_refused_is_an_error_with_no_text():
    route = respx.get(f"{BASE}/v1/async/task/task-1")
    vendor, _ = api()
    route.mock(return_value=httpx.Response(200, text=MARKER))
    with pytest.raises(VendorError):
        vendor.get("task-1")
    route.mock(return_value=httpx.Response(404, text=MARKER))
    with pytest.raises(VendorError) as raised:
        vendor.get("task-1")
    assert (raised.value.error_class, raised.value.status_code) == (http.REFUSED, 404)
    assert MARKER not in str(raised.value) and MARKER not in repr(raised.value)


def test_the_state_of_a_task():
    assert cloro.status_of({"task": {"status": "PROCESSING"}}) == cloro.PROCESSING
    assert cloro.status_of({"task": {}}) is None and cloro.status_of({}) is None
    assert cloro.FINISHED == (cloro.COMPLETED, cloro.FAILED)
    # Credits are not read.
    assert not hasattr(cloro, "credits_of")
