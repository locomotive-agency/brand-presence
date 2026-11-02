"""Mocked DataForSEO calls: bounded retries, safe paid submissions and redacted errors."""
from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from brand_presence import http, status
from brand_presence.http import NothingTaken, OutcomeUnknown, VendorError
from brand_presence.vendors import dataforseo
from brand_presence.vendors.dataforseo import BASE, DataForSEO, Posted, Ready

PATH = "keywords_data/google_ads/search_volume"
LOGIN, PASSWORD = "marker-login", "marker-password"
MARKER = "marker-vendor-text"


def api() -> tuple[DataForSEO, list[float]]:
    waits: list[float] = []
    return DataForSEO(httpx.Client(), LOGIN, PASSWORD, sleep=waits.append), waits


def envelope(tasks: list[dict], code: int = 20000) -> httpx.Response:
    return httpx.Response(200, json={"status_code": code, "status_message": MARKER,
                                     "tasks": tasks})


def created(tag: str, task_id: str) -> dict:
    return {"id": task_id, "status_code": 20100, "status_message": "Task Created.",
            "data": {"tag": tag}, "result": None}


@respx.mock
def test_every_call_signs_in_with_the_two_secrets_and_no_other_way():
    post = respx.post(f"{BASE}/{PATH}/task_post").mock(return_value=envelope([]))
    ready = respx.get(f"{BASE}/{PATH}/tasks_ready").mock(return_value=envelope([]))
    get = respx.get(f"{BASE}/{PATH}/task_get/task-1").mock(return_value=envelope([{"id": "t"}]))
    vendor, _ = api()
    vendor.post(PATH, [{"keyword": "a"}])
    vendor.ready(PATH)
    vendor.get(PATH, "task-1")
    wanted = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
    for route in (post, ready, get):
        request = route.calls.last.request
        assert request.headers["authorization"] == wanted
        # Neither secret is in the address or in what is sent.
        assert LOGIN not in str(request.url) and PASSWORD not in str(request.url)
        assert LOGIN.encode() not in request.content and PASSWORD.encode() not in request.content


@respx.mock
def test_a_post_sends_its_tasks_as_one_list_and_reads_an_id_or_a_status_for_each():
    route = respx.post(f"{BASE}/{PATH}/task_post").mock(return_value=envelope([
        created("key-1", "task-1"),
        {"id": "task-2", "status_code": 40501, "status_message": MARKER,
         "data": {"tag": "key-2"}, "result": None},
    ]))
    vendor, _ = api()
    posted = vendor.post(PATH, [{"keyword": "a", "tag": "key-1"}, {"keyword": "b", "tag": "key-2"}])
    assert json.loads(route.calls.last.request.content) == [
        {"keyword": "a", "tag": "key-1"}, {"keyword": "b", "tag": "key-2"},
    ]
    # A task the vendor did not create has no ID here, whatever ID its answer holds.
    assert posted == [Posted("key-1", "task-1", 20100, created=True),
                      Posted("key-2", None, 40501)]


@respx.mock
def test_a_post_does_not_read_the_cost_the_vendor_gives_for_a_task():
    respx.post(f"{BASE}/{PATH}/task_post").mock(return_value=envelope([
        {**created("key-1", "task-1"), "cost": 0.0006}, created("key-2", "task-2"),
        {**created("key-3", "task-3"), "cost": 0},
    ]))
    vendor, _ = api()
    posted = vendor.post(PATH, [{"keyword": "a"}, {"keyword": "b"}, {"keyword": "c"}])
    # The cost is not read.
    assert posted == [Posted("key-1", "task-1", 20100, created=True),
                      Posted("key-2", "task-2", 20100, created=True),
                      Posted("key-3", "task-3", 20100, created=True)]


def test_a_post_of_more_tasks_than_the_vendor_takes_is_refused_before_it_is_sent():
    vendor, _ = api()
    with pytest.raises(ValueError):
        vendor.post(PATH, [{"keyword": str(n)} for n in range(dataforseo.TASKS_PER_POST + 1)])


@respx.mock
def test_a_post_is_not_sent_again_after_a_read_time_out():
    route = respx.post(f"{BASE}/{PATH}/task_post").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    vendor, waits = api()
    with pytest.raises(OutcomeUnknown) as raised:
        vendor.post(PATH, [{"keyword": "a", "tag": "key-1"}])
    assert route.call_count == 1 and waits == []
    assert raised.value.error_class == http.TIMEOUT


@respx.mock
def test_a_billable_post_is_not_retried_on_an_http_server_error():
    route = respx.post(f"{BASE}/{PATH}/task_post").respond(503)
    vendor, waits = api()
    with pytest.raises(OutcomeUnknown):
        vendor.post(PATH, [{"keywords": ["example"], "tag": "example"}])
    assert route.call_count == 1 and waits == []


@respx.mock
def test_a_read_time_out_on_the_ready_list_and_on_a_get_is_tried_again():
    ready = respx.get(f"{BASE}/{PATH}/tasks_ready").mock(side_effect=[
        httpx.ReadTimeout("timed out"),
        envelope([{"status_code": 20000,
                   "result": [{"id": "task-1", "tag": "key-1"}, {"id": "task-2"}]},
                  {"status_code": 20000, "result": None}]),
    ])
    get = respx.get(f"{BASE}/{PATH}/task_get/advanced/task-1").mock(side_effect=[
        httpx.Response(503, text=MARKER),
        envelope([{"id": "task-1", "status_code": 20000, "cost": 0.002, "result": [{"x": 1}]}]),
    ])
    vendor, _ = api()
    assert vendor.ready(PATH) == [Ready("task-1", "key-1"), Ready("task-2", None)]
    assert vendor.get(PATH, "task-1", "task_get/advanced")["result"] == [{"x": 1}]
    assert ready.call_count == 2 and get.call_count == 2


@respx.mock
def test_an_envelope_that_is_no_success_is_an_error_with_the_vendors_code_and_no_text():
    respx.get(f"{BASE}/{PATH}/tasks_ready").mock(return_value=envelope([], code=40100))
    vendor, _ = api()
    with pytest.raises(VendorError) as raised:
        vendor.ready(PATH)
    assert (raised.value.error_class, raised.value.status_code) == (http.REFUSED, 40100)
    for secret in (MARKER, LOGIN, PASSWORD):
        assert secret not in str(raised.value) and secret not in repr(raised.value)


@respx.mock
def test_a_ready_list_that_failed_is_an_error_and_not_an_empty_list():
    route = respx.get(f"{BASE}/{PATH}/tasks_ready")
    vendor, _ = api()
    # The envelope is a success; the list's own task is not, and holds no result.
    for code, error_class in ((50000, http.VENDOR_FAULT), (40400, http.REFUSED),
                              (None, http.VENDOR_FAULT)):
        route.mock(return_value=envelope([{"status_code": code, "status_message": MARKER,
                                           "result": None}]))
        with pytest.raises(VendorError) as raised:
            vendor.ready(PATH)
        assert (raised.value.error_class, raised.value.status_code) == (error_class, code)
        assert MARKER not in str(raised.value) and MARKER not in repr(raised.value)
    # A list that was made and holds nothing is an empty list.
    route.mock(return_value=envelope([{"status_code": 20000, "result": None}]))
    assert vendor.ready(PATH) == []


@respx.mock
def test_a_post_the_vendor_did_not_take_is_told_apart_from_one_it_may_have_taken():
    route = respx.post(f"{BASE}/{PATH}/task_post")
    vendor, _ = api()
    # Turned away by the rate limit: nothing was taken, and it is no attempt.
    route.mock(return_value=envelope([], code=40202))
    with pytest.raises(NothingTaken) as limited:
        vendor.post(PATH, [{"keyword": "a"}])
    assert limited.value.status_code == 40202
    # A fault of the vendor's own, or an answer that cannot be read: nothing says whether
    # the tasks were made.
    for answer in (envelope([], code=50000), httpx.Response(200, text=MARKER)):
        route.mock(return_value=answer)
        with pytest.raises(OutcomeUnknown):
            vendor.post(PATH, [{"keyword": "a"}])
    # Refused outright: the tasks were not made.
    route.mock(return_value=envelope([], code=40200))
    with pytest.raises(VendorError) as refused:
        vendor.post(PATH, [{"keyword": "a"}])
    assert not isinstance(refused.value, OutcomeUnknown | NothingTaken)
    assert refused.value.status_code == 40200


@respx.mock
def test_a_success_that_lists_no_tasks_is_an_error_and_never_nothing():
    # A success with no task list says nothing about what the vendor did. A post may
    # have made the tasks, so they are looked for by tag.
    post = respx.post(f"{BASE}/{PATH}/task_post")
    ready = respx.get(f"{BASE}/{PATH}/tasks_ready")
    get = respx.get(f"{BASE}/{PATH}/task_get/task-1")
    vendor, _ = api()
    for body in ({"status_code": 20000, "status_message": MARKER},
                 {"status_code": 20000, "status_message": MARKER, "tasks": None},
                 {"status_code": 20000, "status_message": MARKER, "tasks": {"id": "t"}}):
        for route in (post, ready, get):
            route.mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(OutcomeUnknown) as posted:
            vendor.post(PATH, [{"keyword": "a", "tag": "key-1"}])
        assert posted.value.error_class == status.TASKS_MISSING
        for call in (lambda: vendor.ready(PATH), lambda: vendor.get(PATH, "task-1")):
            with pytest.raises(VendorError) as raised:
                call()
            assert not isinstance(raised.value, OutcomeUnknown)
            assert raised.value.error_class == status.TASKS_MISSING
            assert MARKER not in str(raised.value)
    # An empty list is a list: nothing was posted, and nothing is ready.
    for route in (post, ready):
        route.mock(return_value=envelope([]))
    assert vendor.post(PATH, [{"keyword": "a"}]) == [] and vendor.ready(PATH) == []


@respx.mock
def test_a_task_said_to_be_created_with_no_id_is_marked_created():
    respx.post(f"{BASE}/{PATH}/task_post").mock(return_value=envelope([
        {**created("key-1", "task-1"), "id": None}, {**created("key-2", "task-2"), "id": ""},
    ]))
    vendor, _ = api()
    assert vendor.post(PATH, [{"keyword": "a"}, {"keyword": "b"}]) == [
        Posted("key-1", None, 20100, created=True), Posted("key-2", None, 20100, created=True)]


@respx.mock
def test_a_get_with_no_task_in_its_answer_is_an_error():
    respx.get(f"{BASE}/{PATH}/task_get/task-9").mock(return_value=envelope([]))
    vendor, _ = api()
    with pytest.raises(VendorError):
        vendor.get(PATH, "task-9")
