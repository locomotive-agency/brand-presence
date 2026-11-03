"""Google API error classes under the shared read retry policy."""
from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from brand_presence import http, status
from brand_presence.http import VendorError

ACCESS = status.ACCESS
LIMIT = status.LIMIT
REFUSAL_NOT_KNOWN = status.REFUSAL_NOT_KNOWN


_QUOTA_STATUS = "RESOURCE_EXHAUSTED"


_QUOTA_REASONS = frozenset({
    "quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded",
    "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED",
})


_NOT_THE_PROPERTY = frozenset({
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions",
    "SERVICE_DISABLED", "accessNotConfigured",
})


_PERMISSION_STATUS = "PERMISSION_DENIED"
_PERMISSION_REASONS = frozenset({"forbidden"})


class _Kept:


    def __init__(self, client: httpx.Client):
        self._client = client
        self.last: httpx.Response | None = None

    def request(self, method: str, url: str, **request) -> httpx.Response:
        self.last = None
        self.last = self._client.request(method, url, **request)
        return self.last


def _said(answer: httpx.Response | None) -> tuple[str | None, frozenset[str]]:


    try:
        error = answer.json()["error"]
        status = error.get("status")
        held = [*(error.get("errors") or []), *(error.get("details") or [])]
        reasons = frozenset(item["reason"] for item in held
                            if isinstance(item, dict) and isinstance(item.get("reason"), str))
    except Exception:
        return None, frozenset()
    return (status if isinstance(status, str) else None), reasons


def classed(error: VendorError, answer: httpx.Response | None) -> VendorError:


    status, reasons = _said(answer)
    if error.status_code == 429 or status == _QUOTA_STATUS or reasons & _QUOTA_REASONS:
        error_class = LIMIT
    elif error.status_code != 403 or reasons & _NOT_THE_PROPERTY:
        return error
    elif status == _PERMISSION_STATUS or reasons & _PERMISSION_REASONS:
        error_class = ACCESS
    else:
        error_class = REFUSAL_NOT_KNOWN
    return type(error)(error_class, error.status_code)


def call(client: httpx.Client, method: str, url: str, *,
         sleep: Callable[[float], None] = time.sleep, **request) -> httpx.Response:


    kept = _Kept(client)
    try:
        return http.call(kept, method, url, read=True, sleep=sleep,
                         **request)
    except VendorError as error:
        raise classed(error, kept.last) from None
