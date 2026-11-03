"""Search Console search analytics read client."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from urllib.parse import quote

import httpx

from brand_presence.vendors import google_errors

BASE = "https://searchconsole.googleapis.com/webmasters/v3"
METHOD = "searchAnalytics/query"


FINAL = "final"
ALL = "all"
DATA_STATES = (FINAL, ALL)


_SITE_STARTS = ("sc-domain:", "https://", "http://")


class NotASite(ValueError):


    def __init__(self):
        super().__init__("a Search Console site's address is a domain property or an "
                         "address with its scheme")


class NotADataState(ValueError):


    def __init__(self):
        super().__init__("a search analytics query asks for final data, or for all")


def url_of(site: str) -> str:


    if not (isinstance(site, str) and site.startswith(_SITE_STARTS)):
        raise NotASite()
    return f"{BASE}/sites/{quote(site, safe='')}/{METHOD}"


def query(client: httpx.Client, token, site: str, question: Mapping[str, object], *,
          row_limit: int, start_row: int, data_state: str,
          sleep: Callable[[float], None] = time.sleep) -> dict:


    if data_state not in DATA_STATES:
        raise NotADataState()
    body = {**question, "rowLimit": row_limit, "startRow": start_row, "dataState": data_state}
    return google_errors.call(
        client, "POST", url_of(site), sleep=sleep,
        headers=token.header(), json=body,
    ).json()
