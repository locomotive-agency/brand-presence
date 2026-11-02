"""Rules for finished AI answers. Error pages and captchas are unusable.
A results page with no overview can be a usable answer.

The vendor calls a task completed when the engine gave something back. What came back may
be an error page, a block page or a captcha. Each rule below has a name, and an answer that
trips one is stored raw, counted under the rule's name, never written clean, and asked again
within the period.

These rules cover the response shapes in tests/fixtures/cloro/. Add or change a rule
with a sample that trips it when another engine error page is observed.

A results page on which no AI overview was shown is an answer, and trips no rule. It is one
only where the page holds a result: a page with none shows nothing was read, and is what a
block page of the search engine looks like here.

A missing aioverview key means no overview. An explicit null is unreadable.

An answer is judged as the vendor gives it: its task, with the state, and its response.
Which engine was asked is taken from the request, which the caller holds. The task type the
vendor echoes on the task is read only where no request is given, in any letter case.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from brand_presence import status
from brand_presence.vendors import cloro

# The vendor's task type for the engine that reads the AI overview of a results page, and
# the key of its response that holds the overview, where one was shown.
OVERVIEW_TASK = "GOOGLE"
OVERVIEW_KEY = "aioverview"

# A text shorter than this, with no source beside it, is no answer.
MIN_LENGTH = 40
# A block page is short. A text longer than this that holds one of the phrases is an answer
# that speaks of them.
BLOCK_PAGE_LENGTH = 300
# What block pages and captchas say, in lower case with single spaces.
BLOCK_PHRASES = (
    "verify you are human",
    "verify that you are human",
    "are you a robot",
    "not a robot",
    "captcha",
    "unusual traffic",
    "access denied",
    "request blocked",
    "checking your browser",
    "enable javascript",
    "too many requests",
    "something went wrong",
    "service unavailable",
)

_SPACE = re.compile(r"\s+")


# What an answer holds -----------------------------------------------------------------------

def is_overview(answer: Mapping[str, object]) -> bool:
    """Whether the answer is of the engine that reads the AI overview, by the task type the
    vendor echoes on the task. A caller that holds the request asks that instead."""
    task = answer.get("task")
    echoed = task.get("taskType") if isinstance(task, dict) else None
    return isinstance(echoed, str) and echoed.upper() == OVERVIEW_TASK


def content_of(answer: Mapping[str, object], overview: bool) -> Mapping[str, object] | None:
    """The part of an answer that holds its text and its sources: the response of a chat
    engine, or the overview on a results page. None where there is none, and where the
    overview is unreadable, which a rule makes unusable first."""
    response = answer.get("response")
    if not isinstance(response, dict):
        return None
    if not overview:
        return response
    shown = response.get(OVERVIEW_KEY)
    return shown if isinstance(shown, dict) else None


def has_results(answer: Mapping[str, object]) -> bool:
    """Whether a results page holds a result of any kind beside the overview: a list, under
    any name, with at least one item in it."""
    response = answer.get("response")
    if not isinstance(response, dict):
        return False
    return any(
        isinstance(held, list) and any(isinstance(item, dict) and item for item in held)
        for name, held in response.items() if name != OVERVIEW_KEY
    )


def no_overview(answer: Mapping[str, object], overview: bool) -> bool:
    """Whether the answer is a page of the overview engine with no overview on it: its
    response holds no key `aioverview`. A task with no response at all is not one: nothing
    says a page was read."""
    response = answer.get("response")
    return overview and isinstance(response, dict) and OVERVIEW_KEY not in response


def overview_unreadable(answer: Mapping[str, object], overview: bool) -> bool:
    """Whether the answer is a page of the overview engine whose key `aioverview` is there
    and holds no overview: null, a text, a list."""
    response = answer.get("response")
    return (overview and isinstance(response, dict) and OVERVIEW_KEY in response
            and not isinstance(response[OVERVIEW_KEY], dict))


def none_shown(answer: Mapping[str, object], overview: bool) -> bool:
    """Whether the answer is a results page on which no AI overview was shown: a page
    with results and no overview."""
    return no_overview(answer, overview) and has_results(answer)


def text_of(content: Mapping[str, object] | None) -> str:
    """The text of an answer, with no space around it. Empty where there is none."""
    text = content.get("text") if content is not None else None
    return text.strip() if isinstance(text, str) else ""


def links_of(content: Mapping[str, object] | None) -> tuple[list[str], int]:
    """The addresses of the pages an answer cites, in its own order, and how many of its
    sources could not be read: a `sources` that is no list, or a source that is no object
    or has no address as text. An answer with no `sources` cites nothing."""
    sources = content.get("sources") if content is not None else None
    if sources is None:
        return [], 0
    if not isinstance(sources, list):
        return [], 1
    links = [source["url"] for source in sources
             if isinstance(source, dict) and isinstance(source.get("url"), str) and source["url"]]
    return links, len(sources) - len(links)


def sources_of(content: Mapping[str, object] | None) -> list[str]:
    """The addresses of the pages an answer cites, in its own order."""
    return links_of(content)[0]


# The rules ----------------------------------------------------------------------------------

def _task_failed(answer: Mapping[str, object], overview: bool) -> bool:
    return cloro.status_of(answer) != cloro.COMPLETED


def _vendor_error(answer: Mapping[str, object], overview: bool) -> bool:
    task, response = answer.get("task"), answer.get("response")
    return bool(
        answer.get("error")
        or (isinstance(task, dict) and task.get("error"))
        or (isinstance(response, dict) and response.get("error"))
    )


def _overview_unreadable(answer: Mapping[str, object], overview: bool) -> bool:
    return overview_unreadable(answer, overview)


def _no_results(answer: Mapping[str, object], overview: bool) -> bool:
    return no_overview(answer, overview) and not has_results(answer)


def _no_text(answer: Mapping[str, object], overview: bool) -> bool:
    if no_overview(answer, overview):
        return False
    return not text_of(content_of(answer, overview))


def _block_page(answer: Mapping[str, object], overview: bool) -> bool:
    text = _SPACE.sub(" ", text_of(content_of(answer, overview))).lower()
    return 0 < len(text) <= BLOCK_PAGE_LENGTH and any(phrase in text for phrase in BLOCK_PHRASES)


def _too_short(answer: Mapping[str, object], overview: bool) -> bool:
    shown = content_of(answer, overview)
    return 0 < len(text_of(shown)) < MIN_LENGTH and not sources_of(shown)


@dataclass(frozen=True)
class Rule:
    """One rule: its name, which is what an unusable answer is counted under, and whether
    an answer trips it, given whether the overview engine was asked."""

    name: str
    trips: Callable[[Mapping[str, object], bool], bool]


# In the order they are tried. An answer is counted under the first it trips.
RULES = (
    # The vendor reports that the task failed. Its answer is kept raw like any other.
    Rule(status.TASK_FAILED, _task_failed),
    # A completed answer that holds an error object of the vendor's.
    Rule(status.VENDOR_ERROR, _vendor_error),
    # A page of the overview engine whose `aioverview` is there and is no overview.
    Rule(status.OVERVIEW_UNREADABLE, _overview_unreadable),
    # A page of the overview engine with no overview and no result: an empty or a blocked
    # page, not a page on which none was shown.
    Rule(status.NO_RESULTS, _no_results),
    # The text is missing or empty.
    Rule(status.NO_TEXT, _no_text),
    # The text is only a known block or captcha phrase.
    Rule(status.BLOCK_PAGE, _block_page),
    # A few words and no source.
    Rule(status.TOO_SHORT, _too_short),
)


def tripped(answer: Mapping[str, object], overview: bool | None = None) -> str | None:
    """The name of the first rule an answer trips, or None for an answer that trips none.
    `overview` says whether the overview engine was asked; with None it is read from the
    task type the vendor echoes."""
    asked = is_overview(answer) if overview is None else overview
    return next((rule.name for rule in RULES if rule.trips(answer, asked)), None)
