"""The Syften call, on a mocked vendor (mentions "The author of a post is never stored", the
vendor's half; "A run cut short is not a success", the wait a rate limit names;
source-framework "Logs and errors hold no vendor text, query value or key")."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import respx

from brand_presence import http
from brand_presence.http import NothingTaken, VendorError
from brand_presence.vendors import syften
from brand_presence.vendors.syften import BASE, Syften

ITEMS = f"{BASE}/items/get"
TOKEN_VALUE = "marker-token-value"
MARKER = "marker-vendor-text"
AUTHOR = "marker-author-name"
AFTER = "2026-02-01T12:00:00.000000Z"


def api() -> tuple[Syften, list[float]]:
    waits: list[float] = []
    return Syften(httpx.Client(), TOKEN_VALUE, sleep=waits.append), waits


def match(number: int, **item) -> dict:
    return {"id": f"match-{number}", "matched_on": f"2026-03-0{number}T09:00:00.123456789Z",
            "filter": "sample $tag:sample-tag",
            "item": {"backend": "sample-forum", "text": "a post", "author": AUTHOR, **item}}


@respx.mock
def test_a_page_is_asked_for_with_the_token_and_the_time_and_no_other_way():
    route = respx.post(ITEMS).mock(return_value=httpx.Response(200, json=[match(1)]))
    vendor, _ = api()
    vendor.matches(AFTER)
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {TOKEN_VALUE}"
    # The token is in neither the address nor what is sent.
    assert TOKEN_VALUE not in str(request.url) and TOKEN_VALUE.encode() not in request.content
    assert json.loads(request.content) == {
        "limit": syften.MATCHES_PER_PAGE, "show": "all", "after": AFTER,
    }
    # The page before it in time is asked for by a time just after the oldest in hand: the
    # first the pipeline can write that is later than the one it read.
    oldest = syften.time_of("2026-03-01T09:00:00.123456789Z")
    assert syften.just_after(oldest) == "2026-03-01T09:00:00.123457Z"
    assert syften.time_of(syften.just_after(oldest)) - oldest == timedelta(microseconds=1)
    vendor.matches(AFTER, before=syften.just_after(oldest))
    assert json.loads(route.calls.last.request.content)["before"] == (
        "2026-03-01T09:00:00.123457Z"
    )


@respx.mock
def test_the_author_is_removed_from_every_match_before_it_is_returned():
    said = [
        match(1),
        # Whatever the vendor says of the author, and wherever in the match it says it.
        match(2, author_url=f"https://forum.example/u/{AUTHOR}",
              Author_Name=AUTHOR, replies=[{"author": AUTHOR, "kind": "reply"}]),
        {**match(3), "author": {"name": AUTHOR}},
    ]
    respx.post(ITEMS).mock(return_value=httpx.Response(200, json=said))
    vendor, _ = api()
    found = vendor.matches(AFTER)
    assert AUTHOR not in json.dumps(found)
    # Nothing else of a match goes with it.
    assert [m["id"] for m in found] == ["match-1", "match-2", "match-3"]
    assert found[0] == {"id": "match-1", "matched_on": "2026-03-01T09:00:00.123456789Z",
                        "filter": "sample $tag:sample-tag",
                        "item": {"backend": "sample-forum", "text": "a post"}}
    assert found[1]["item"]["replies"] == [{"kind": "reply"}]


@respx.mock
def test_a_rate_limit_answer_that_names_a_wait_is_handed_to_the_source_at_once():
    # mentions "A named rate limit is waited for until the run's deadline": the source waits,
    # on its run's sleep and by its run's deadline, so the client neither waits nor asks again.
    route = respx.post(ITEMS).mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "7"}, text=MARKER),
        httpx.Response(200, json=[match(1)]),
    ])
    vendor, waits = api()
    with pytest.raises(http.RateLimited) as caught:
        vendor.matches(AFTER)
    assert caught.value.wait == 7
    assert waits == [] and route.call_count == 1
    assert (caught.value.error_class, caught.value.status_code) == (http.LIMIT, 429)
    said = str(caught.value) + repr(caught.value)
    assert MARKER not in said and TOKEN_VALUE not in said
    # The same page asked for again is answered.
    assert [m["id"] for m in vendor.matches(AFTER)] == ["match-1"]
    first, second = (call.request.content for call in route.calls)
    assert first == second


@respx.mock
def test_a_rate_limit_that_names_no_wait_outlasts_the_retries_with_no_vendor_text():
    route = respx.post(ITEMS).mock(return_value=httpx.Response(429, text=MARKER))
    vendor, waits = api()
    with pytest.raises(NothingTaken) as caught:
        vendor.matches(AFTER)
    assert not isinstance(caught.value, http.RateLimited)
    assert (caught.value.error_class, caught.value.status_code) == (http.LIMIT, 429)
    # The framework's growing waits, within its limit, and no further.
    assert route.call_count == http.RETRIES + 1 and waits == list(http.WAITS)
    said = str(caught.value) + repr(caught.value)
    assert MARKER not in said and TOKEN_VALUE not in said


@respx.mock
def test_a_time_out_is_tried_again_because_asking_makes_nothing_at_the_vendor():
    route = respx.post(ITEMS).mock(side_effect=[
        httpx.ReadTimeout("timed out"), httpx.Response(200, json=[]),
    ])
    vendor, _ = api()
    assert vendor.matches(AFTER) == []
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize(("answer", "handed_on"), [
    # What is no JSON is not kept: no author could be taken out of it.
    (httpx.Response(200, text=f"<html>{AUTHOR}</html>"), None),
    (httpx.Response(200, json={"error": MARKER, "author": AUTHOR}), {"error": MARKER}),
    (httpx.Response(200, json=[match(1), MARKER]),
     [{k: v for k, v in match(1).items() if k != "item"}
      | {"item": {"backend": "sample-forum", "text": "a post"}}, MARKER]),
])
def test_an_answer_that_is_no_list_of_matches_is_handed_on_without_an_author(answer, handed_on):
    respx.post(ITEMS).mock(return_value=answer)
    vendor, _ = api()
    found = vendor.matches(AFTER)
    # It is for the source to judge, and to store as unusable. It is no page.
    assert found == handed_on and not syften.is_page(found)
    assert AUTHOR not in json.dumps(found)
    assert syften.is_page([]) and syften.is_page([match(1)])


@respx.mock
def test_a_refusal_holds_a_class_and_a_status_and_nothing_the_vendor_said():
    respx.post(ITEMS).mock(return_value=httpx.Response(401, text=MARKER))
    vendor, _ = api()
    with pytest.raises(VendorError) as caught:
        vendor.matches(AFTER)
    assert (caught.value.error_class, caught.value.status_code) == (http.REFUSED, 401)
    assert MARKER not in str(caught.value) and TOKEN_VALUE not in str(caught.value)


def test_a_time_is_read_as_the_vendor_writes_it_and_written_as_it_reads_one():
    nine = syften.time_of("2026-03-01T09:15:00.123456789Z")
    assert nine == datetime(2026, 3, 1, 9, 15, 0, 123456, tzinfo=UTC)
    # Another zone is the same moment, and no zone is universal time.
    assert syften.time_of("2026-03-01T10:15:00+01:00") == datetime(2026, 3, 1, 9, 15, tzinfo=UTC)
    assert syften.time_of("2026-03-01T09:15:00") == datetime(2026, 3, 1, 9, 15, tzinfo=UTC)
    for no_time in (None, "", "yesterday", 20260301, ["2026-03-01"]):
        assert syften.time_of(no_time) is None
    # What the pipeline writes, it reads back as the same moment.
    east = datetime(2026, 3, 1, 10, 15, tzinfo=timezone(timedelta(hours=1)))
    assert syften.as_time(east) == "2026-03-01T09:15:00.000000Z"
    assert syften.time_of(syften.as_time(nine)) == nine
