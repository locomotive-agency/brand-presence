"""Offline vendor boundary tests."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from brand_presence import google_token, http
from brand_presence.vendors import google_errors, search_console
from google_project import FakeTokens

SITE = "sc-domain:aardvark.example"
URL = search_console.url_of(SITE)
MARKER = "marker-token-that-must-stay-in-the-header"
QUESTION = {"startDate": "2026-02-23", "endDate": "2026-03-01", "dimensions": ["date"],
            "type": "web"}


class OtherPath(BaseException):
    pass


def service(*answers: httpx.Response) -> respx.Route:


    def other(request: httpx.Request):
        raise OtherPath(request.url.raw_path.decode())

    route = respx.post(URL).mock(side_effect=list(answers))
    respx.route().mock(side_effect=other)
    return route


class Reads:
    scope = google_token.SEARCH_CONSOLE_READ_ONLY


def token(tokens: FakeTokens | None = None) -> google_token.AccountToken:
    return google_token.reader(Reads.scope, tokens or FakeTokens(value=MARKER))


def page(rows: int) -> httpx.Response:
    return httpx.Response(200, json={
        "rows": [{"keys": [f"2026-02-2{n + 3}"], "clicks": n, "impressions": 10 * n,
                  "ctr": 0.1, "position": 4.5} for n in range(rows)],
        "responseAggregationType": "byProperty",
    })


def asked(route: respx.Route) -> list[dict]:
    return [json.loads(call.request.content) for call in route.calls]


def ask(client: httpx.Client, *, start_row: int = 0, row_limit: int = 3,
        data_state: str = search_console.FINAL, waits=None) -> dict:
    waits = [] if waits is None else waits
    return search_console.query(client, token(), SITE, QUESTION, row_limit=row_limit,
                                start_row=start_row, data_state=data_state,
                                sleep=waits.append)


def test_the_address_is_the_query_of_the_site_and_of_nothing_else():
    assert URL == ("https://searchconsole.googleapis.com/webmasters/v3/sites/"
                   "sc-domain%3Aaardvark.example/searchAnalytics/query")

    by_address = search_console.url_of("https://shop.aardvark.example/winkel")
    assert by_address.endswith(
        "/sites/https%3A%2F%2Fshop.aardvark.example%2Fwinkel/searchAnalytics/query")
    assert by_address.count("/") == URL.count("/")
    hostile = search_console.url_of("https://badger.example/../../sites?x=1#y")
    assert hostile.count("/") == URL.count("/") and "?" not in hostile and "#" not in hostile

    for wrong in ("", "aardvark.example", "300100200", "../sitemaps", "sites", None, 300):
        with pytest.raises(search_console.NotASite) as caught:
            search_console.url_of(wrong)
        assert str(wrong) == "" or str(wrong) not in str(caught.value)


@respx.mock
def test_a_short_page():
    route = service(page(2))
    with httpx.Client() as client:
        got = ask(client)
    assert len(got["rows"]) == 2


    assert asked(route) == [{**QUESTION, "rowLimit": 3, "startRow": 0, "dataState": "final"}]
    sent, = route.calls
    assert sent.request.method == "POST"
    assert sent.request.headers["authorization"] == f"Bearer {MARKER}"
    assert sent.request.url.raw_path.decode() == (
        "/webmasters/v3/sites/sc-domain%3Aaardvark.example/searchAnalytics/query")


@respx.mock
def test_a_full_page_and_then_a_short_one():
    route = service(page(3), page(1))
    with httpx.Client() as client:
        first = ask(client, start_row=0)
        second = ask(client, start_row=3)
    assert (len(first["rows"]), len(second["rows"])) == (3, 1)
    assert [(body["rowLimit"], body["startRow"]) for body in asked(route)] == [(3, 0), (3, 3)]
    assert {body["dataState"] for body in asked(route)} == {"final"}


@respx.mock
def test_unfinished_data_is_asked_for_by_its_data_state_and_no_other_is_taken():
    route = service(page(1))
    with httpx.Client() as client:
        ask(client, data_state=search_console.ALL)
        for wrong in ("hourly_all", "", None):
            with pytest.raises(search_console.NotADataState):
                ask(client, data_state=wrong)
    assert [body["dataState"] for body in asked(route)] == ["all"]


@respx.mock
def test_a_403_with_a_quota_reason_is_limit_and_is_asked_once():
    text = "marker-text-of-the-quota-refusal"
    route = service(httpx.Response(403, json={"error": {
        "code": 403, "message": text,
        "errors": [{"domain": "usageLimits", "reason": "quotaExceeded", "message": text}],
    }}))
    waits: list = []
    with httpx.Client() as client, pytest.raises(http.VendorError) as caught:
        ask(client, waits=waits)
    assert (caught.value.error_class, caught.value.status_code) == (google_errors.LIMIT, 403)

    assert route.call_count == 1 and waits == []
    assert text not in str(caught.value) + repr(caught.value)


@respx.mock
def test_a_permission_answer():
    text = "marker-text-of-the-refusal"
    route = service(httpx.Response(403, json={"error": {
        "code": 403, "status": "PERMISSION_DENIED", "message": f"{text} {SITE}",
        "errors": [{"domain": "global", "reason": "forbidden", "message": text}],
    }}))
    with httpx.Client() as client, pytest.raises(http.VendorError) as caught:
        ask(client)
    assert (caught.value.error_class, caught.value.status_code) == (google_errors.ACCESS, 403)

    assert route.call_count == 1
    said = str(caught.value) + repr(caught.value)
    assert text not in said and "aardvark" not in said and MARKER not in said


@respx.mock
def test_a_read_time_out_is_asked_again():
    route = service(httpx.ReadTimeout("timed out"), page(1))
    with httpx.Client() as client:
        assert len(ask(client)["rows"]) == 1
    assert route.call_count == 2


@respx.mock
def test_any_other_path_fails_the_test():
    service(page(1))
    site = "sc-domain%3Aaardvark.example"
    with httpx.Client() as client:

        for path in (f"{search_console.BASE}/sites",
                     f"{search_console.BASE}/sites/{site}",
                     f"{search_console.BASE}/sites/{site}/sitemaps",
                     search_console.url_of("sc-domain:badger.example"),
                     "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"):
            with pytest.raises(OtherPath):
                client.post(path, json={})
        with pytest.raises(OtherPath):
            client.get(URL)
        with pytest.raises(OtherPath):
            client.delete(f"{search_console.BASE}/sites/{site}")

        assert len(ask(client)["rows"]) == 1


@respx.mock
def test_the_token_is_asked_for_the_read_only_scope_alone():
    tokens = FakeTokens()
    service(page(1))
    with httpx.Client() as client:
        search_console.query(client, token(tokens), SITE, QUESTION, row_limit=1, start_row=0,
                             data_state=search_console.FINAL, sleep=lambda _: None)
    assert tokens.asked() == [google_token.SEARCH_CONSOLE_READ_ONLY]
