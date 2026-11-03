"""GA4 API aggregates and their fixed report definitions."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping

import httpx

from brand_presence.vendors import google_errors

BASE = "https://analyticsdata.googleapis.com/v1beta"
METHOD = "runReport"


class NotAPropertyNumber(ValueError):
    def __init__(self):
        super().__init__("a GA4 property's address is its number")


def url_of(property_number: str) -> str:
    if not (isinstance(property_number, str) and property_number.isascii()
            and property_number.isdigit()):
        raise NotAPropertyNumber()
    return f"{BASE}/properties/{property_number}:{METHOD}"


def run_report(client: httpx.Client, token, property_number: str,
               report: Mapping[str, object], *, limit: int, offset: int,
               sleep: Callable[[float], None] = time.sleep) -> dict:
    body = {**report, "limit": limit, "offset": offset, "returnPropertyQuota": True}
    return google_errors.call(
        client, "POST", url_of(property_number), sleep=sleep,
        headers=token.header(), json=body,
    ).json()
