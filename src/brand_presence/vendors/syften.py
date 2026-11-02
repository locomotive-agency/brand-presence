"""Syften, as the mentions source calls it: sign in, and get one page of matches.

A match is a post one of the client's filters caught. The vendor is asked for the matches
after a time, and gives a page of them, newest first. The page before it in time is asked
for with a time just after the oldest match time in hand, so that a match made at that
same time and left off the page is on the next one. Nothing is made at the vendor by
asking, so a call is tried again under the one policy in src/brand_presence/http.py. A rate limit
answer that names a wait is not waited for there: it is raised as `http.RateLimited`, with
the wait, for the source to wait for while its run has time. One that names no wait is
tried again there.

The match fields read here are: `id`,
`matched_on`, `filter` and `item`, and of the item `timestamp`, `backend`, `backend_sub`,
`type`, `lang`, `text`, `title`, `item_url` and `analysis.accept`. These fields still need
confirmation against a live account.

The author of a post is taken out of every match here, before the match is handed to
anything that could write it. Whatever
the vendor says of the author goes: every field whose name starts with `author`, wherever
in the match it is.

Nothing raised here holds the vendor's text, the request or the token.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from brand_presence import http

BASE = "https://syften.com/api/0.1"

# The secret the vendor's token is kept in.
TOKEN = "syften-api-token"

# The vendor's status codes that say a limit is reached, beside the two every vendor shares
# (src/brand_presence/http.py). None is known yet: each is
# added here when it is first seen, with a sample.
LIMIT_CODES: tuple[int, ...] = ()

# The most matches one page holds.
MATCHES_PER_PAGE = 100
# Every match of the client's filters is asked for, whatever the vendor's own filter made
# of it. What that filter said is on the match.
SHOW = "all"

# The start of every field that says who wrote a post.
AUTHOR = "author"

# The vendor's times carry nine digits after the second. Python reads six.
_FINER_THAN_READ = re.compile(r"(\.\d{6})\d+")
_READ_STEP = timedelta(microseconds=1)


def without_author(held: object) -> object:
    """A match, or any part of one, with every author field taken out."""
    if isinstance(held, dict):
        return {name: without_author(value) for name, value in held.items()
                if not (isinstance(name, str) and name.lower().startswith(AUTHOR))}
    if isinstance(held, list):
        return [without_author(value) for value in held]
    return held


def time_of(text: object) -> datetime | None:
    """A time as the vendor writes it, or None where it is no time. One with no zone is
    taken as the vendor gives every other: in universal time."""
    if not isinstance(text, str) or not text.strip():
        return None
    trimmed = _FINER_THAN_READ.sub(r"\1", text.strip())
    if trimmed[-1] in "Zz":
        trimmed = trimmed[:-1] + "+00:00"
    try:
        found = datetime.fromisoformat(trimmed)
    except ValueError:
        return None
    return found.replace(tzinfo=UTC) if found.tzinfo is None else found.astimezone(UTC)


def as_time(moment: datetime) -> str:
    """A time of the pipeline's own, as the vendor is given one."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def just_after(moment: datetime) -> str:
    """The first time the pipeline can write that is later than a match time it has read.
    A time is read to six digits, so every match of that time is before this one."""
    return as_time(moment + _READ_STEP)


def is_page(said: object) -> bool:
    """Whether the vendor's answer is a page: a list of matches, which may be empty."""
    return isinstance(said, list) and all(isinstance(match, dict) for match in said)


class Syften:
    """One client's Syften account."""

    def __init__(self, client: httpx.Client, token: str, *,
                 sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}
        self._sleep = sleep

    def matches(self, after: str, before: str | None = None) -> object:
        """One page of the matches after a time, newest first, each with its author taken
        out. `before` asks for the page of matches older than that time. An answer that is
        no list of matches is handed on as it came, with every author field taken out, for
        the source to judge (`is_page`); one that is no JSON is handed on as None. Raises
        `http.RateLimited` on a rate limit answer that names a wait."""
        body: dict[str, object] = {"limit": MATCHES_PER_PAGE, "show": SHOW, "after": after}
        if before is not None:
            body["before"] = before
        # A read, whatever its method: asking makes nothing at the vendor, so a time-out
        # after it was sent is tried again.
        response = http.call(self._client, "POST", f"{BASE}/items/get", read=True,
                             limit_codes=LIMIT_CODES, caller_waits=True, sleep=self._sleep,
                             headers=self._headers,
                             json=body)
        try:
            said = response.json()
        except ValueError:
            return None
        return without_author(said)
