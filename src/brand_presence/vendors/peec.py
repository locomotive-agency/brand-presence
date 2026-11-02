"""Peec Customer API for stored prompts and answers.

Only canonical snake_case fields are read. References and response text are never errors.
All calls are reads. Prompts belong to Peec.
"""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime
from urllib.parse import quote

import httpx

from brand_presence import http, status

BASE = "https://api.peec.ai/customer/v1"
KEY = "peec-api-key"
PAGE_SIZE = 1000
MAX_PAGES = 100
# Channel mapping verified 28 October 2026 by channel description and scraper model.
# Missing or inactive mapped channels still stop; no surface substitution.
CHANNELS = {
    "chatgpt": "openai-0",
    "perplexity": "perplexity-0",
    "gemini": "google-2",
    "ai-overview": "google-0",
}


def reference(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
    return value


def envelope(value: object, *, counted: bool = True) -> list[dict]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
    rows = value["data"]
    if any(not isinstance(row, dict) for row in rows):
        raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
    if counted and (type(value.get("total_count")) is not int or value["total_count"] < 0):
        raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
    if value.get("search_truncated") is True:
        raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
    return rows


class Peec:
    """One dedicated company key. All project reads name the project explicitly."""

    def __init__(self, client: httpx.Client, key: str, *,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 deadline: datetime | None = None):
        if not key:
            raise http.VendorError(status.KEY_MISSING)
        self._client = client
        self._headers = {"x-api-key": key}
        self._sleep, self._clock, self._deadline = sleep, clock, deadline
        self.counts: Counter[str] = Counter()

    def _wait(self, seconds: float) -> None:
        if self._deadline is not None and (
            self._clock().timestamp() + seconds >= self._deadline.timestamp()
        ):
            raise http.VendorError(status.LIMIT, limit_reason="retry_wait_past_deadline",
                                   wait_seconds=int(seconds))
        self._sleep(seconds)

    def _call(self, path: str, *, project: str | None = None,
              params: dict | None = None) -> dict:
        if self._deadline is not None and self._clock() >= self._deadline:
            self.counts["peec_limit_call_deadline"] += 1
            raise http.VendorError(status.LIMIT)
        query = dict(params or {})
        if project is not None:
            query["project_id"] = reference(project)
        def rate_wait() -> None:
            self.counts["rate_limit_waits"] += 1
        try:
            response = http.call(
                self._client, "GET", BASE + path, read=True, creates_work=False,
                wait_header="x-ratelimit-reset",
                sleep=self._wait, headers=self._headers,
                on_rate_wait=rate_wait,
                params=query,
                timeout=30, follow_redirects=False,
            )
        except http.VendorError as error:
            code = error.status_code
            if error.error_class == status.LIMIT:
                self.counts[f"peec_limit_{error.limit_reason}"] += 1
                if error.wait_seconds is not None:
                    self.counts["peec_limit_wait_seconds"] += error.wait_seconds
            if code == 401:
                raise http.VendorError(status.KEY_REFUSED, code) from None
            if code == 403:
                raise http.VendorError(status.PEEC_PROJECT_ACCESS, code) from None
            if code == 404:
                if path.startswith("/chats/"):
                    self.counts["peec_chats_missing"] += 1
                    raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE, code) from None
                raise http.VendorError(status.PEEC_PROJECT_ACCESS, code) from None
            raise
        if response.status_code != 200:
            raise http.VendorError(status.UNEXPECTED_STATUS, response.status_code)
        try:
            value = response.json()
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
        return value

    def projects(self) -> list[dict]:
        # The projects endpoint has no documented total. Require a short final page, bounded.
        rows: list[dict] = []
        seen: set[str] = set()
        for page in range(MAX_PAGES):
            value = self._call("/projects", params={
                "limit": PAGE_SIZE, "offset": page * PAGE_SIZE})
            batch = envelope(value, counted=False)
            for item in batch:
                key = reference(item.get("id"))
                if key in seen:
                    raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
                seen.add(key)
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                return rows
        raise http.VendorError(status.PEEC_PAGE_CAP)

    def _listed(self, path: str, project: str, **params) -> list[dict]:
        rows: list[dict] = []
        seen: set[str] = set()
        total = None
        for page in range(MAX_PAGES):
            value = self._call(path, project=project, params={
                **params, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE})
            batch = envelope(value)
            if total is not None and value["total_count"] != total:
                raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
            total = value["total_count"]
            for item in batch:
                key = reference(item.get("id"))
                if key in seen:
                    raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
                seen.add(key)
            rows.extend(batch)
            if len(rows) == total:
                return rows
            if not batch or len(batch) != PAGE_SIZE or len(rows) > total:
                raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
        raise http.VendorError(status.PEEC_PAGE_CAP)

    def prompts(self, project: str) -> list[dict]:
        return self._listed("/prompts", project)

    def brands(self, project: str) -> list[dict]:
        return self._listed("/brands", project)

    def channels(self, project: str) -> list[dict]:
        # No pagination controls or total are documented for this endpoint.
        return envelope(self._call("/model-channels", project=project), counted=False)

    def chats(self, project: str, prompt: str, channel: str, start: date, end: date,
              offset: int = 0) -> dict:
        if start > end or offset < 0:
            raise http.VendorError(status.PEEC_MAPPING_INVALID)
        return self._call("/chats", project=project, params={
            "prompt_id": reference(prompt), "model_channel_id": reference(channel),
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "sort": "asc", "limit": PAGE_SIZE, "offset": offset,
            "include_archived_prompts": "true",
        })

    def content(self, project: str, chat: str) -> dict:
        return self._call(f"/chats/{quote(reference(chat), safe='')}/content",
                          project=project)
