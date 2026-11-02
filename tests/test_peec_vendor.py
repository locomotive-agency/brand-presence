"""Peec's documented wire shape with invented references. No network is allowed."""
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from brand_presence import http, status
from brand_presence.vendors import peec

PROJECT = "sample-project-reference"
PROMPT = "sample-prompt-reference"
CHAT = "sample-chat-reference"
TOKEN = "sample-peec-key-value"


@pytest.fixture
def api():
    with httpx.Client() as client:
        yield peec.Peec(client, TOKEN, sleep=lambda _: None)


@respx.mock
@pytest.mark.parametrize("method,path,body", [
    ("projects", "/projects", {"data": [{"id": PROJECT, "country_code": "NL"}]}),
    ("brands", "/brands", {"data": [], "total_count": 0}),
    ("prompts", "/prompts", {"data": [], "total_count": 0}),
    ("channels", "/model-channels", {"data": []}),
])
def test_metadata_uses_documented_path_and_header(api, method, path, body):
    route = respx.get(peec.BASE + path).respond(200, json=body)
    result = getattr(api, method)(*(() if method == "projects" else (PROJECT,)))
    assert result == body["data"]
    request = route.calls.last.request
    assert request.headers["x-api-key"] == TOKEN
    assert "authorization" not in request.headers
    assert TOKEN not in str(request.url)
    if method != "projects":
        assert request.url.params["project_id"] == PROJECT


@respx.mock
def test_chat_listing_has_explicit_filters(api):
    route = respx.get(peec.BASE + "/chats").respond(200, json={"data": [], "total_count": 0})
    api.chats(PROJECT, PROMPT, "openai-0", date(2026, 3, 2), date(2026, 3, 8), 1000)
    assert dict(route.calls.last.request.url.params) == dict(
        project_id=PROJECT, prompt_id=PROMPT, model_channel_id="openai-0",
        start_date="2026-03-02", end_date="2026-03-08", limit="1000", offset="1000",
        sort="asc", include_archived_prompts="true")


@respx.mock
def test_detail_is_one_read(api):
    route = respx.get(peec.BASE + f"/chats/{CHAT}/content").respond(200, json={"id": CHAT})
    assert api.content(PROJECT, CHAT) == {"id": CHAT}
    assert route.calls.last.request.url.params["project_id"] == PROJECT
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("code,expected", [
    (401, status.KEY_REFUSED), (403, status.PEEC_PROJECT_ACCESS),
    (402, status.LIMIT), (404, status.PEEC_PAGINATION_INCOMPLETE),
    (400, status.REFUSED), (302, status.UNEXPECTED_STATUS),
])
def test_read_refusals_are_named_and_redacted(api, code, expected):
    respx.get(peec.BASE + f"/chats/{CHAT}/content").respond(code, text="private-vendor-message")
    with pytest.raises(http.VendorError) as error:
        api.content(PROJECT, CHAT)
    assert error.value.error_class == expected
    assert "private" not in str(error.value)


@respx.mock
@pytest.mark.parametrize("body", [{}, {"data": []}, {"data": [], "totalCount": 0},
                                   {"data": [], "total_count": True},
                                   {"data": {}, "total_count": 0}])
def test_metadata_never_infers_empty_or_accepts_alternate_fields(api, body):
    respx.get(peec.BASE + "/prompts").respond(200, json=body)
    with pytest.raises(http.VendorError, match="peec response unreadable"):
        api.prompts(PROJECT)


@respx.mock
def test_metadata_pages_require_stable_total_and_unique_ids(api, monkeypatch):
    monkeypatch.setattr(peec, "PAGE_SIZE", 1)
    route = respx.get(peec.BASE + "/brands").mock(side_effect=[
        httpx.Response(200, json={"data": [{"id": "sample-brand"}], "total_count": 2}),
        httpx.Response(200, json={"data": [{"id": "sample-brand"}], "total_count": 2}),
    ])
    with pytest.raises(http.VendorError, match="peec pagination incomplete"):
        api.brands(PROJECT)
    assert route.call_count == 2


@respx.mock
def test_reset_header_is_seconds_and_waits_fit_deadline():
    start = datetime(2026, 3, 9, tzinfo=UTC)
    waits = []
    route = respx.get(peec.BASE + "/brands").mock(side_effect=[
        httpx.Response(429, headers={"X-RateLimit-Reset": "12"}),
        httpx.Response(200, json={"data": [], "total_count": 0}),
    ])
    with httpx.Client() as client:
        api = peec.Peec(client, TOKEN, sleep=waits.append, clock=lambda: start,
                        deadline=start + timedelta(seconds=20))
        assert api.brands(PROJECT) == []
        assert waits == [12] and api.counts["rate_limit_waits"] == 1
        route.respond(429, headers={"X-RateLimit-Reset": "30"})
        with pytest.raises(http.VendorError, match="limit"):
            api.brands(PROJECT)
        assert waits == [12]


@respx.mock
@pytest.mark.parametrize("reset", ["Wed, 21 Oct 2026 07:28:00 GMT", "unknown", "-1"])
def test_reset_header_never_accepts_dates_or_invented_units(api, reset):
    route = respx.get(peec.BASE + "/projects").respond(429,
        headers={"X-RateLimit-Reset": reset, "Retry-After": "1"})
    with pytest.raises(http.VendorError) as caught:
        api.projects()
    assert caught.value.error_class == status.LIMIT
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("code,reset,remaining,cause,seconds,calls", [
    (402, None, 100, "plan_cap", 0, 1),
    (429, "30", 20, "rate_limit_past_deadline", 30, 1),
    (429, "121", 200, "rate_limit_wait_too_long", 121, 1),
    (429, "unknown", 200, "rate_limit_reset_unreadable", 0, 1),
    (429, "1", 200, "rate_limit_retries_exhausted", 1, 5),
    (429, None, 200, "rate_limit_retries_exhausted", 8, 5),
    (503, None, 1, "retry_wait_past_deadline", 1, 1),
    (200, None, 0, "call_deadline", 0, 0),
])
def test_every_limit_path_has_a_safe_cause_and_wait(code, reset, remaining, cause, seconds, calls):
    start = datetime(2026, 3, 9, tzinfo=UTC)
    route = respx.get(peec.BASE + "/projects").respond(code,
        headers={} if reset is None else {"X-RateLimit-Reset": reset},
        json={"data": []})
    with httpx.Client() as client:
        api = peec.Peec(client, TOKEN, sleep=lambda _: None, clock=lambda: start,
                        deadline=start + timedelta(seconds=remaining))
        with pytest.raises(http.VendorError, match="limit"):
            api.projects()
    assert route.call_count == calls
    assert api.counts[f"peec_limit_{cause}"] == 1
    assert api.counts["peec_limit_wait_seconds"] == seconds
