"""Google Ads read reports and account validation."""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

import httpx

from brand_presence import http, status
from brand_presence.http import VendorError

VERSION = "v26"
API = f"https://googleads.googleapis.com/{VERSION}"
TOKEN_URL = "https://oauth2.googleapis.com/token"


DEVELOPER_TOKEN = "google-ads-developer-token"
CLIENT_ID = "google-ads-oauth-client-id"
CLIENT_SECRET = "google-ads-oauth-client-secret"
REFRESH_TOKEN = "google-ads-refresh-token"
LOGIN_CUSTOMER_ID = "google-ads-login-customer-id"
SECRETS = (DEVELOPER_TOKEN, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, LOGIN_CUSTOMER_ID)

SIGN_IN = status.SIGN_IN
REFUSAL_NOT_KNOWN = status.REFUSAL_NOT_KNOWN

SIGNS_OUT = (SIGN_IN, REFUSAL_NOT_KNOWN)
ACCESS = status.ACCESS
LIMIT = status.LIMIT
OTHER = status.OTHER

TRADE_AGAIN = "trade again"


TOKEN_ENDPOINT = "token endpoint"
SEARCH = "search"

ANY_STATUS = None


@dataclass(frozen=True)
class Refusal:
    where: str
    status_code: int | None
    name: str
    outcome: str


REFUSALS = (

    Refusal(TOKEN_ENDPOINT, 400, "invalid_grant", SIGN_IN),
    Refusal(TOKEN_ENDPOINT, 401, "invalid_client", SIGN_IN),

    Refusal(SEARCH, ANY_STATUS, "quotaError", LIMIT),
    Refusal(SEARCH, 429, "RESOURCE_EXHAUSTED", LIMIT),

    Refusal(SEARCH, 401, "OAUTH_TOKEN_EXPIRED", TRADE_AGAIN),
    Refusal(SEARCH, 401, "UNAUTHENTICATED", SIGN_IN),

    Refusal(SEARCH, 403, "DEVELOPER_TOKEN_NOT_APPROVED", SIGN_IN),
    Refusal(SEARCH, 403, "DEVELOPER_TOKEN_PROHIBITED", SIGN_IN),
    Refusal(SEARCH, 403, "DEVELOPER_TOKEN_INVALID", SIGN_IN),

    Refusal(SEARCH, 403, "USER_PERMISSION_DENIED", ACCESS),
    Refusal(SEARCH, 403, "CUSTOMER_NOT_ENABLED", ACCESS),
)


def names_of(answer: httpx.Response | None, where: str) -> frozenset[str]:
    try:
        said = answer.json()
        if where == TOKEN_ENDPOINT:
            error = said.get("error")
            return frozenset({error}) if isinstance(error, str) else frozenset()
        error = said["error"]
        names = {error.get("status")}
        for detail in error.get("details") or []:
            for item in detail.get("errors") or []:
                code = item.get("errorCode") or {}
                for kind, name in code.items():
                    names |= {kind, name}
    except Exception:
        return frozenset()
    return frozenset(name for name in names if isinstance(name, str))


def sorted_as(where: str, status_code: int | None, names: frozenset[str]) -> str | None:
    for row in REFUSALS:
        if row.where == where and row.status_code in (status_code, ANY_STATUS) \
                and row.name in names:
            return row.outcome
    if status_code in (401, 403):
        return REFUSAL_NOT_KNOWN
    return None


def classed(error: VendorError, answer: httpx.Response | None, where: str) -> VendorError:
    outcome = sorted_as(where, error.status_code, names_of(answer, where))
    if outcome is None:
        if error.status_code is None or error.error_class == LIMIT:
            return error
        outcome = OTHER
    return type(error)(outcome, error.status_code)


class _Kept:
    def __init__(self, client: httpx.Client):
        self._client = client
        self.last: httpx.Response | None = None

    def request(self, method: str, url: str, **request) -> httpx.Response:
        self.last = None
        self.last = self._client.request(method, url, **request)
        return self.last


class _Expired(Exception):
    pass


def digits_of(customer_id: str) -> str:
    return re.sub(r"[\s-]", "", customer_id)


def search_url(customer_id: str) -> str:
    return f"{API}/customers/{digits_of(customer_id)}/googleAds:search"


class Session:
    def __init__(self, client: httpx.Client, keys: Mapping[str, str], *,
                 sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._keys = keys
        self._sleep = sleep
        self._access: str | None = None

        self._refused: VendorError | None = None
        self.trades = 0

    def __repr__(self) -> str:
        return f"Session(trades={self.trades}, signed_in={self._access is not None})"

    def _sign_out(self, error: VendorError) -> VendorError:
        if error.error_class in SIGNS_OUT:
            self._refused = error
            self._access = None
        return error

    def trade(self) -> None:
        if self._refused is not None:
            raise VendorError(self._refused.error_class, self._refused.status_code)
        self._access = None
        self.trades += 1
        kept = _Kept(self._client)
        try:
            answer = http.call(
                kept, "POST", TOKEN_URL, read=True, 
                sleep=self._sleep,
                data={"grant_type": "refresh_token",
                      "client_id": self._keys[CLIENT_ID],
                      "client_secret": self._keys[CLIENT_SECRET],
                      "refresh_token": self._keys[REFRESH_TOKEN]},
            )
        except VendorError as error:
            raise self._sign_out(classed(error, kept.last, TOKEN_ENDPOINT)) from None
        try:
            token = answer.json().get("access_token")
        except Exception:
            token = None
        if not isinstance(token, str) or not token:
            raise VendorError(OTHER, answer.status_code)
        self._access = token

    def _headers(self) -> dict[str, str]:
        headers = {
            "authorization": f"Bearer {self._access}",
        }

        if self._keys.get(DEVELOPER_TOKEN):
            headers["developer-token"] = self._keys[DEVELOPER_TOKEN]
        if self._keys.get(LOGIN_CUSTOMER_ID):
            headers["login-customer-id"] = digits_of(self._keys[LOGIN_CUSTOMER_ID])
        return headers

    def _send(self, customer_id: str, body: Mapping[str, object]) -> object:
        kept = _Kept(self._client)
        try:
            answer = http.call(
                kept, "POST", search_url(customer_id), read=True,
                sleep=self._sleep, headers=self._headers(),
                json=dict(body),
            )
        except VendorError as error:
            if sorted_as(SEARCH, error.status_code, names_of(kept.last, SEARCH)) == TRADE_AGAIN:
                raise _Expired from None
            raise self._sign_out(classed(error, kept.last, SEARCH)) from None
        try:
            return answer.json()
        except ValueError:
            return None

    def search(self, customer_id: str, query: str, page_token: str | None = None) -> object:
        if self._refused is not None:
            raise VendorError(self._refused.error_class, self._refused.status_code)
        body: dict[str, object] = {"query": query}
        if page_token is not None:
            body["pageToken"] = page_token
        if self._access is None:
            self.trade()
        try:
            return self._send(customer_id, body)
        except _Expired:
            pass

        self.trade()
        try:
            return self._send(customer_id, body)
        except _Expired:
            raise self._sign_out(VendorError(SIGN_IN, 401)) from None

    def pages(self, customer_id: str, query: str) -> Iterator[object]:
        token: str | None = None
        while True:
            page = self.search(customer_id, query, token)
            yield page
            token = next_page(page)
            if token is None:
                return


def next_page(page: object) -> str | None:
    token = page.get("nextPageToken") if isinstance(page, Mapping) else None
    return token if isinstance(token, str) and token else None
