"""Offline vendor boundary tests."""
from __future__ import annotations

import httpx
import pytest
import respx

from brand_presence import http
from brand_presence.vendors import google_errors

URL = "https://vendor.invalid/report"
MARKER = "marker-text-from-google"


def answer(code: int, status: str = "", *, reason: str | None = None,
           older: str | None = None, **headers) -> httpx.Response:

    error: dict = {"code": code, "message": MARKER, "status": status}
    if reason is not None:
        error["details"] = [{"@type": MARKER, "reason": reason, "metadata": {"held": MARKER}}]
    if older is not None:
        error["errors"] = [{"domain": "usageLimits", "reason": older, "message": MARKER}]
    return httpx.Response(code, json={"error": error}, headers=headers)


def failed(*answers, waits: list | None = None) -> tuple[http.VendorError, respx.Route]:
    route = respx.post(URL).mock(side_effect=list(answers))
    waits = [] if waits is None else waits
    with httpx.Client() as client, pytest.raises(http.VendorError) as caught:
        google_errors.call(client, "POST", URL, sleep=waits.append, json={"asked": True})
    return caught.value, route


def named(error: http.VendorError) -> tuple[str, int | None]:
    return error.error_class, error.status_code


@respx.mock
def test_a_permission_answer_is_access_and_is_asked_once():
    error, route = failed(answer(403, "PERMISSION_DENIED"))
    assert named(error) == (google_errors.ACCESS, 403)
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("given", [
    {"older": "quotaExceeded"}, {"older": "rateLimitExceeded"},
    {"older": "userRateLimitExceeded"}, {"older": "dailyLimitExceeded"},
    {"reason": "RATE_LIMIT_EXCEEDED"},
])
def test_a_403_with_a_quota_reason_is_limit_and_never_access(given):
    error, route = failed(answer(403, "PERMISSION_DENIED", **given))
    assert named(error) == (google_errors.LIMIT, 403)

    assert route.call_count == 1


@respx.mock
def test_a_403_whose_status_says_the_quota_ran_out_is_limit():
    error, _ = failed(answer(403, "RESOURCE_EXHAUSTED"))
    assert named(error) == (google_errors.LIMIT, 403)


@respx.mock
def test_a_403_with_a_reason_that_is_no_quota_reason_is_access():
    error, _ = failed(answer(403, "PERMISSION_DENIED", older="forbidden",
                             reason="IAM_PERMISSION_DENIED"))
    assert named(error) == (google_errors.ACCESS, 403)


@respx.mock
@pytest.mark.parametrize("given", [

    {"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}, {"older": "insufficientPermissions"},
    {"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "older": "insufficientPermissions"},

    {"reason": "SERVICE_DISABLED"}, {"older": "accessNotConfigured"},
    {"reason": "SERVICE_DISABLED", "older": "accessNotConfigured"},
])
def test_a_403_for_a_missing_scope_or_an_api_that_is_off_is_not_access(given):
    error, route = failed(answer(403, "PERMISSION_DENIED", **given))
    assert named(error) == (http.REFUSED, 403)
    assert error.error_class not in (google_errors.ACCESS, google_errors.LIMIT)
    assert route.call_count == 1


@respx.mock
def test_a_quota_reason_beside_one_of_those_is_still_limit():
    given = answer(403, "RESOURCE_EXHAUSTED", older="accessNotConfigured")
    assert named(failed(given)[0]) == (google_errors.LIMIT, 403)


@respx.mock
def test_a_quota_reason_is_read_past_an_item_that_is_in_no_shape():
    body = '{"error": {"errors": [7, {"reason": "quotaExceeded"}], "details": [null]}}'
    assert named(failed(httpx.Response(403, text=body))[0]) == (google_errors.LIMIT, 403)


def test_the_reasons_that_are_no_refusal_of_a_property_are_no_quota_reasons():
    assert not google_errors._NOT_THE_PROPERTY & google_errors._QUOTA_REASONS


@respx.mock
def test_a_429_is_retried_and_is_limit_when_the_retries_are_spent():
    waits: list = []
    error, route = failed(*[answer(429, "RESOURCE_EXHAUSTED")] * (http.RETRIES + 1), waits=waits)
    assert named(error) == (google_errors.LIMIT, 429)
    assert route.call_count == http.RETRIES + 1 and len(waits) == http.RETRIES

    assert isinstance(error, http.NothingTaken)


@respx.mock
def test_a_429_with_no_body_is_limit_too():
    error, _ = failed(*[httpx.Response(429)] * (http.RETRIES + 1))
    assert named(error) == (google_errors.LIMIT, 429)


@respx.mock
def test_a_429_and_then_an_answer_is_the_answer():
    route = respx.post(URL).mock(side_effect=[answer(429), httpx.Response(200, json={"rows": 1})])
    with httpx.Client() as client:
        got = google_errors.call(client, "POST", URL, sleep=lambda _: None, json={})
    assert got.json() == {"rows": 1} and route.call_count == 2


@respx.mock
def test_a_5xx_keeps_the_frameworks_class():
    error, route = failed(*[answer(503, "UNAVAILABLE")] * (http.RETRIES + 1))
    assert named(error) == (http.VENDOR_FAULT, 503)
    assert route.call_count == http.RETRIES + 1


@respx.mock
@pytest.mark.parametrize(("code", "status"), [
    (400, "INVALID_ARGUMENT"), (401, "UNAUTHENTICATED"), (404, "NOT_FOUND"),
])
def test_another_refusal_keeps_the_frameworks_class(code, status):
    error, _ = failed(answer(code, status))
    assert named(error) == (http.REFUSED, code)


@respx.mock
def test_a_time_out_is_retried_as_a_read_and_keeps_the_frameworks_class():
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    with httpx.Client() as client, pytest.raises(http.VendorError) as caught:
        google_errors.call(client, "POST", URL, sleep=lambda _: None, json={})
    assert named(caught.value) == (http.TIMEOUT, None)
    assert route.call_count == http.RETRIES + 1
    assert not isinstance(caught.value, http.OutcomeUnknown)


@respx.mock
@pytest.mark.parametrize("body", [
    "not json at all", "[]", '{"error": "' + MARKER + '"}', '{"error": {"errors": "text"}}',
    '{"error": {"details": [7, null]}}', '{"error": {"status": 7, "details": [{"reason": 7}]}}',
    '{"error": {"errors": [{"reason": ["SERVICE_DISABLED"]}]}}',
])
def test_a_403_that_is_not_in_googles_shape_is_a_refusal_not_known(body):


    error, _ = failed(httpx.Response(403, text=body))
    assert named(error) == (google_errors.REFUSAL_NOT_KNOWN, 403)


@respx.mock
@pytest.mark.parametrize("given", [
    answer(403), answer(403, "UNAUTHENTICATED"), answer(403, reason="SOMETHING_NEW"),
    answer(403, "FAILED_PRECONDITION", older="somethingElse"),
])
def test_a_403_in_googles_shape_that_says_no_permission_is_a_refusal_not_known(given):
    error, route = failed(given)
    assert named(error) == (google_errors.REFUSAL_NOT_KNOWN, 403)
    assert route.call_count == 1


@respx.mock
def test_the_older_forbidden_reason_alone_is_access():


    error, _ = failed(answer(403, older="forbidden"))
    assert named(error) == (google_errors.ACCESS, 403)


@respx.mock
@pytest.mark.parametrize("given", [
    answer(403, "PERMISSION_DENIED"), answer(403, "PERMISSION_DENIED", older="quotaExceeded"),
    answer(403, MARKER, reason=MARKER, older=MARKER), answer(400, MARKER), answer(404, MARKER),
    answer(403, MARKER, reason="SERVICE_DISABLED"),
    answer(403, MARKER, older="insufficientPermissions"),
    httpx.Response(403, text=MARKER),
])
def test_no_text_of_the_answer_is_in_the_result(given, capsys):
    error, _ = failed(given)
    print(error, repr(error), error.args, vars(error), error.__cause__, error.__context__)
    out = capsys.readouterr()
    assert MARKER not in out.out + out.err
    assert error.__cause__ is None and error.__suppress_context__


@respx.mock
def test_no_text_of_a_5xx_or_a_429_is_in_the_result(capsys):
    for code in (500, 429):
        respx.post(URL).mock(side_effect=[answer(code, MARKER, reason=MARKER)] * 5)
        with httpx.Client() as client, pytest.raises(http.VendorError) as caught:
            google_errors.call(client, "POST", URL, sleep=lambda _: None, json={})
        print(caught.value, repr(caught.value), vars(caught.value))
    out = capsys.readouterr()
    assert MARKER not in out.out + out.err


def test_the_classes_are_the_names_the_specs_give():
    assert (google_errors.ACCESS, google_errors.LIMIT, google_errors.REFUSAL_NOT_KNOWN) == (
        "access", "limit", "refusal not known")
