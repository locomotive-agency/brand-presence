"""Offline GA4 vendor reads."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from brand_presence import google_token, http
from brand_presence.vendors import ga4, google_errors
from google_project import FakeTokens

NUMBER = "300100200"
URL = ga4.url_of(NUMBER)
MARKER = "marker-token-that-must-stay-in-the-header"
REPORT = {"dimensions": [{"name": "date"}], "metrics": [{"name": "sessions"}]}


class OtherPath(BaseException):
    pass


def service(*answers: httpx.Response) -> respx.Route:


    def other(request: httpx.Request):
        raise OtherPath(request.url.path)

    route = respx.post(URL).mock(side_effect=list(answers))
    respx.route().mock(side_effect=other)
    return route


def token(tokens: FakeTokens | None = None) -> google_token.AccountToken:
    return google_token.reader(Reads.scope, tokens or FakeTokens(value=MARKER))


class Reads:
    scope = google_token.ANALYTICS_READ_ONLY


def page(rows: int, total: int) -> httpx.Response:
    return httpx.Response(200, json={
        "rows": [{"dimensionValues": [{"value": f"2026022{n}"}],
                  "metricValues": [{"value": str(n)}]} for n in range(rows)],
        "rowCount": total,
    })


def asked(route: respx.Route) -> list[dict]:
    return [json.loads(call.request.content) for call in route.calls]


def report(client: httpx.Client, *, offset: int = 0, limit: int = 2, waits=None) -> dict:
    waits = [] if waits is None else waits
    return ga4.run_report(client, token(), NUMBER, REPORT, limit=limit, offset=offset,
                          sleep=waits.append)


def test_the_address_is_run_report_of_the_property_and_of_nothing_else():
    assert URL.startswith("https://analyticsdata.googleapis.com/v1beta/")
    assert URL.endswith(f"/{NUMBER}:runReport")


    for wrong in ("", "30010020a", NUMBER + "/../accounts", NUMBER + ":batchRunReports",
                  "sc-domain:aardvark.example", None, 300):
        with pytest.raises(ga4.NotAPropertyNumber) as caught:
            ga4.url_of(wrong)
        assert str(wrong) == "" or str(wrong) not in str(caught.value)


@respx.mock
def test_one_page():
    route = service(page(2, 2))
    with httpx.Client() as client:
        got = report(client)
    assert got["rowCount"] == 2 and len(got["rows"]) == 2


    assert asked(route) == [{**REPORT, "limit": 2, "offset": 0, "returnPropertyQuota": True}]
    sent, = route.calls
    assert sent.request.method == "POST"
    assert sent.request.headers["authorization"] == f"Bearer {MARKER}"


@respx.mock
def test_two_pages_by_offset():
    route = service(page(2, 3), page(1, 3))
    with httpx.Client() as client:
        first = report(client, offset=0)
        second = report(client, offset=2)
    assert (len(first["rows"]), len(second["rows"])) == (2, 1)
    assert [(body["limit"], body["offset"]) for body in asked(route)] == [(2, 0), (2, 2)]
    assert all(body["returnPropertyQuota"] is True for body in asked(route))


@respx.mock
def test_a_429_and_then_a_200():
    waits: list = []
    route = service(httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
                    page(1, 1))
    with httpx.Client() as client:
        got = report(client, waits=waits)
    assert got["rowCount"] == 1
    assert route.call_count == 2 and len(waits) == 1

    first, second = asked(route)
    assert first == second


@respx.mock
def test_a_429_that_outlasts_the_retries_is_limit():
    route = service(*[httpx.Response(429)] * (http.RETRIES + 1))
    with httpx.Client() as client, pytest.raises(http.VendorError) as caught:
        report(client)
    assert (caught.value.error_class, caught.value.status_code) == (google_errors.LIMIT, 429)
    assert route.call_count == http.RETRIES + 1


@respx.mock
def test_a_permission_answer():
    text = "marker-text-of-the-refusal"
    route = service(httpx.Response(403, json={"error": {
        "code": 403, "status": "PERMISSION_DENIED", "message": f"{text} {NUMBER}",
    }}))
    with httpx.Client() as client, pytest.raises(http.VendorError) as caught:
        report(client)
    assert (caught.value.error_class, caught.value.status_code) == (google_errors.ACCESS, 403)

    assert route.call_count == 1
    said = str(caught.value) + repr(caught.value)
    assert text not in said and NUMBER not in said and MARKER not in said


@respx.mock
def test_a_read_time_out_is_asked_again():
    route = service(httpx.ReadTimeout("timed out"), page(1, 1))
    with httpx.Client() as client:
        assert report(client)["rowCount"] == 1
    assert route.call_count == 2


@respx.mock
def test_any_other_path_fails_the_test():
    service(page(1, 1))
    with httpx.Client() as client:

        for path in (f"{ga4.BASE}/properties/{NUMBER}:runRealtimeReport",
                     f"{ga4.BASE}/properties/{NUMBER}:batchRunReports",
                     ga4.url_of("300100201")):
            with pytest.raises(OtherPath):
                client.post(path, json={})
        with pytest.raises(OtherPath):
            client.get(URL)

        assert report(client)["rowCount"] == 1










@respx.mock
def test_the_token_is_asked_for_the_read_only_scope_alone():
    tokens = FakeTokens()
    service(page(1, 1))
    with httpx.Client() as client:
        ga4.run_report(client, token(tokens), NUMBER, REPORT, limit=1, offset=0,
                       sleep=lambda _: None)
    assert tokens.asked() == [google_token.ANALYTICS_READ_ONLY]
