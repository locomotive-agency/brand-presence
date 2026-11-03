"""Offline Google Ads boundary tests."""
from __future__ import annotations

import json
import traceback
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from brand_presence import status
from brand_presence.http import NothingTaken, VendorError
from brand_presence.vendors import google_ads
from google_ads_samples import SAMPLES, answer_of


def customer_id(number):
    return str(1000000000 + number)


CUSTOMER = customer_id(3)
LOGIN = customer_id(7)
QUERY = "SELECT customer.id FROM customer"


def keys() -> dict[str, str]:

    return {name: f"marker-{name}-{index}" for index, name in enumerate(google_ads.SECRETS)}


ACCESS_TOKENS = ("marker-access-token-one", "marker-access-token-two")


class OtherPath(BaseException):
    pass


def mocked(trades: list[httpx.Response] | None = None,
           searches: list[httpx.Response] | None = None) -> tuple[respx.Route, respx.Route]:
    def other(request: httpx.Request):
        raise OtherPath(request.url.raw_path.decode())

    trade = respx.post(google_ads.TOKEN_URL).mock(side_effect=trades or [
        httpx.Response(200, json={"access_token": token, "expires_in": 3599,
                                  "token_type": "Bearer"}) for token in ACCESS_TOKENS])
    search = respx.post(google_ads.search_url(CUSTOMER)).mock(side_effect=searches or [])
    respx.route().mock(side_effect=other)
    return trade, search


def page(rows: int, next_token: str | None = None) -> httpx.Response:
    body: dict = {"results": [{"customer": {"id": "7"}, "metrics": {"clicks": str(n)}}
                              for n in range(rows)],
                  "fieldMask": "customer.id,metrics.clicks"}
    if next_token is not None:
        body["nextPageToken"] = next_token
    return httpx.Response(200, json=body)


def session(waits: list[float] | None = None) -> google_ads.Session:
    return google_ads.Session(httpx.Client(), keys(),
                              sleep=(waits if waits is not None else []).append)


def every_text(error: BaseException, kept: object) -> str:

    return "".join(traceback.format_exception(error)) + repr(error) + repr(kept)


def test_the_addresses_are_the_trade_and_the_search_of_the_version():
    assert google_ads.TOKEN_URL == "https://oauth2.googleapis.com/token"
    assert google_ads.search_url(CUSTOMER) == (
        f"https://googleads.googleapis.com/{google_ads.VERSION}/customers/"
        f"{CUSTOMER.replace('-', '')}/googleAds:search")

    assert google_ads.VERSION.startswith("v") and google_ads.VERSION[1:].isdigit()


@respx.mock
def test_a_trade_and_a_search():
    trade, search = mocked(searches=[page(2), page(1)])
    kept = session()
    first = kept.search(CUSTOMER, QUERY)
    kept.search(CUSTOMER, QUERY)
    assert len(first["results"]) == 2

    assert trade.call_count == 1
    form = parse_qs(trade.calls[0].request.content.decode())
    assert form == {"grant_type": ["refresh_token"],
                    "client_id": [keys()[google_ads.CLIENT_ID]],
                    "client_secret": [keys()[google_ads.CLIENT_SECRET]],
                    "refresh_token": [keys()[google_ads.REFRESH_TOKEN]]}
    assert search.call_count == 2
    sent = search.calls[0].request
    assert sent.method == "POST"
    assert json.loads(sent.content) == {"query": QUERY}


    assert sent.headers["authorization"] == f"Bearer {ACCESS_TOKENS[0]}"
    assert sent.headers["developer-token"] == keys()[google_ads.DEVELOPER_TOKEN]
    assert sent.headers["login-customer-id"] == "".join(
        character for character in keys()[google_ads.LOGIN_CUSTOMER_ID]
        if character not in "- ")

    for value in (*keys().values(), *ACCESS_TOKENS):
        assert value not in repr(kept)


@respx.mock
def test_the_login_customer_id_is_sent_as_ten_digits():
    trade, search = mocked(searches=[page(0)])
    with_hyphens = {**keys(), google_ads.LOGIN_CUSTOMER_ID: LOGIN}
    google_ads.Session(httpx.Client(), with_hyphens, sleep=[].append).search(CUSTOMER, QUERY)
    assert search.calls[0].request.headers["login-customer-id"] == LOGIN.replace("-", "")
    assert len(LOGIN.replace("-", "")) == 10


@respx.mock
def test_a_search_of_two_pages_follows_the_token_of_the_first():
    trade, search = mocked(searches=[page(3, next_token="invented-page-two"), page(1)])
    pages = list(session().pages(CUSTOMER, QUERY))
    assert [len(found["results"]) for found in pages] == [3, 1]
    assert [json.loads(call.request.content) for call in search.calls] == [
        {"query": QUERY}, {"query": QUERY, "pageToken": "invented-page-two"}]
    assert trade.call_count == 1


@respx.mock
def test_a_page_that_is_no_json_is_handed_on_as_no_answer():
    mocked(searches=[httpx.Response(200, text="<html>not an answer</html>")])
    assert list(session().pages(CUSTOMER, QUERY)) == [None]


@respx.mock
def test_a_429_that_names_a_wait_is_waited_out_and_sent_again():
    trade, search = mocked(searches=[
        httpx.Response(429, headers={"retry-after": "7"}, json={"error": {
            "code": 429, "status": "RESOURCE_EXHAUSTED"}}),
        page(1)])
    waits: list[float] = []
    found = session(waits).search(CUSTOMER, QUERY)
    assert len(found["results"]) == 1
    assert waits == [7]
    assert search.call_count == 2 and trade.call_count == 1


EXPECTED = {
    "invalid_grant": status.SIGN_IN,
    "invalid_client": status.SIGN_IN,
    "quotaError": status.LIMIT,
    "RESOURCE_EXHAUSTED": status.LIMIT,
    "UNAUTHENTICATED": status.SIGN_IN,
    "DEVELOPER_TOKEN_NOT_APPROVED": status.SIGN_IN,
    "DEVELOPER_TOKEN_PROHIBITED": status.SIGN_IN,
    "DEVELOPER_TOKEN_INVALID": status.SIGN_IN,
    "USER_PERMISSION_DENIED": status.ACCESS,
    "CUSTOMER_NOT_ENABLED": status.ACCESS,

    "off the table": status.REFUSAL_NOT_KNOWN,
}


def test_every_row_of_the_table_has_a_sample_and_an_expected_class():
    names = {row.name for row in google_ads.REFUSALS}
    assert names == (set(EXPECTED) - {"off the table"}) | {"OAUTH_TOKEN_EXPIRED"}
    assert set(SAMPLES) == set(EXPECTED) | {"OAUTH_TOKEN_EXPIRED"}

    assert {row.outcome for row in google_ads.REFUSALS} <= {
        status.SIGN_IN, status.ACCESS, status.LIMIT, google_ads.TRADE_AGAIN}


@pytest.mark.parametrize("name", sorted(EXPECTED))
@respx.mock
def test_each_known_refusal_has_its_class(name):
    sample = SAMPLES[name]
    if sample["where"] == google_ads.TOKEN_ENDPOINT:
        trade, search = mocked(trades=[answer_of(sample)])
    else:
        trade, search = mocked(searches=[answer_of(sample)] * 5)
    with pytest.raises(VendorError) as caught:
        session().search(CUSTOMER, QUERY)
    assert caught.value.error_class == EXPECTED[name]
    assert caught.value.status_code == sample["status"]

    if sample["status"] == 429:
        assert isinstance(caught.value, NothingTaken)


def test_the_table_is_matched_on_status_code_and_name_never_on_text():

    names = frozenset({"USER_PERMISSION_DENIED"})
    assert google_ads.sorted_as(google_ads.SEARCH, 403, names) == status.ACCESS
    assert google_ads.sorted_as(google_ads.SEARCH, 400, names) is None

    assert google_ads.sorted_as(google_ads.SEARCH, 400, frozenset(
        {"the user USER_PERMISSION_DENIED"})) is None


    assert google_ads.sorted_as(google_ads.SEARCH, 401, frozenset()) == status.REFUSAL_NOT_KNOWN
    assert google_ads.sorted_as(google_ads.SEARCH, 403, frozenset()) == status.REFUSAL_NOT_KNOWN
    assert google_ads.sorted_as(google_ads.TOKEN_ENDPOINT, 401, frozenset()) \
        == status.REFUSAL_NOT_KNOWN
    assert google_ads.sorted_as(google_ads.SEARCH, 404, frozenset()) is None


@respx.mock
def test_a_refusal_nobody_listed_signs_the_session_out_and_nothing_more_is_sent():
    trade, search = mocked(searches=[answer_of(SAMPLES["off the table"])])
    kept = session()
    with pytest.raises(VendorError) as caught:
        kept.search(CUSTOMER, QUERY)
    assert (caught.value.error_class, caught.value.status_code) == (
        status.REFUSAL_NOT_KNOWN, 403)

    for other in (CUSTOMER, customer_id(5)):
        with pytest.raises(VendorError) as again:
            kept.search(other, QUERY)
        assert again.value.error_class == status.REFUSAL_NOT_KNOWN
    assert (trade.call_count, search.call_count) == (1, 1)


@respx.mock
def test_an_access_refusal_does_not_sign_the_session_out():
    trade, search = mocked(searches=[answer_of(SAMPLES["USER_PERMISSION_DENIED"]), page(1)])
    kept = session()
    with pytest.raises(VendorError) as caught:
        kept.search(CUSTOMER, QUERY)
    assert caught.value.error_class == status.ACCESS
    assert len(kept.search(CUSTOMER, QUERY)["results"]) == 1


@respx.mock
def test_a_server_error_after_the_retries_is_other():
    trade, search = mocked(searches=[httpx.Response(500, json={"error": {
        "code": 500, "status": "INTERNAL"}})] * 5)
    with pytest.raises(VendorError) as caught:
        session().search(CUSTOMER, QUERY)
    assert (caught.value.error_class, caught.value.status_code) == (status.OTHER, 500)
    assert search.call_count == 5


@respx.mock
def test_the_token_runs_out_and_is_traded_once_more():
    expired = answer_of(SAMPLES["OAUTH_TOKEN_EXPIRED"])
    trade, search = mocked(searches=[page(1), expired, page(2), page(3)])
    kept = session()
    kept.search(CUSTOMER, QUERY)
    second = kept.search(CUSTOMER, QUERY)
    kept.search(CUSTOMER, QUERY)
    assert len(second["results"]) == 2
    assert trade.call_count == 2
    sent = [call.request.headers["authorization"] for call in search.calls]


    assert sent == [f"Bearer {ACCESS_TOKENS[0]}", f"Bearer {ACCESS_TOKENS[0]}",
                    f"Bearer {ACCESS_TOKENS[1]}", f"Bearer {ACCESS_TOKENS[1]}"]
    assert [json.loads(call.request.content) for call in search.calls][1:3] == [
        {"query": QUERY}, {"query": QUERY}]


@respx.mock
def test_the_new_token_is_refused_too():
    expired = answer_of(SAMPLES["OAUTH_TOKEN_EXPIRED"])
    trade, search = mocked(searches=[expired, expired])
    kept = session()
    with pytest.raises(VendorError) as caught:
        kept.search(CUSTOMER, QUERY)
    assert (caught.value.error_class, caught.value.status_code) == (status.SIGN_IN, 401)
    assert (trade.call_count, search.call_count) == (2, 2)
    with pytest.raises(VendorError):
        kept.search(CUSTOMER, QUERY)
    assert (trade.call_count, search.call_count) == (2, 2)


@respx.mock
def test_a_trade_with_no_token_in_its_answer_is_a_failure():
    mocked(trades=[httpx.Response(200, json={"token_type": "Bearer"})])
    with pytest.raises(VendorError) as caught:
        session().search(CUSTOMER, QUERY)
    assert caught.value.error_class == status.OTHER


@pytest.mark.parametrize("searches", [
    [answer_of(SAMPLES["off the table"])],
    [answer_of(SAMPLES["OAUTH_TOKEN_EXPIRED"])] * 2,
    [answer_of(SAMPLES["USER_PERMISSION_DENIED"])],
    [httpx.Response(500, json={"error": {"code": 500}})] * 5,
])
@respx.mock
def test_the_access_token_and_the_keys_are_in_no_exception_text(searches):
    mocked(searches=searches)
    kept = session()
    with pytest.raises(VendorError) as caught:
        kept.search(CUSTOMER, QUERY)
    shown = every_text(caught.value, kept)
    for value in (*keys().values(), *ACCESS_TOKENS, CUSTOMER, CUSTOMER.replace("-", "")):
        assert value not in shown

    assert ACCESS_TOKENS[0] in every_text(VendorError(ACCESS_TOKENS[0]), kept)


@respx.mock
def test_a_refused_trade_shows_no_key():
    mocked(trades=[answer_of(SAMPLES["invalid_grant"])])
    kept = session()
    with pytest.raises(VendorError) as caught:
        kept.search(CUSTOMER, QUERY)
    shown = every_text(caught.value, kept)
    for value in keys().values():
        assert value not in shown


@respx.mock
def test_current_project_auth_omits_legacy_developer_header():
    supplied = keys()
    supplied.pop(google_ads.DEVELOPER_TOKEN)
    supplied.pop(google_ads.LOGIN_CUSTOMER_ID)
    _, search = mocked(searches=[page(1)])
    with httpx.Client() as client:
        google_ads.Session(client, supplied).search(CUSTOMER, QUERY)
    assert 'developer-token' not in search.calls[0].request.headers
    assert 'login-customer-id' not in search.calls[0].request.headers
