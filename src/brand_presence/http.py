from __future__ import annotations

import time
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from brand_presence import status

RETRIES = 4
# The waits before each retry, in seconds.
WAITS = (1, 2, 4, 8)
# A longer vendor-requested wait ends the call; a later run may try again.
LONGEST_NAMED_WAIT = 120

# Vendor call errors use the shared status names.
CONNECTION = status.CONNECTION
TIMEOUT = status.TIMEOUT
LIMIT = status.LIMIT
VENDOR_FAULT = status.VENDOR_FAULT
REFUSED = status.REFUSED
OTHER = status.VENDOR_CALL_FAILED
UNEXPECTED_STATUS = status.UNEXPECTED_STATUS


class VendorError(Exception):
    """A vendor call that did not give an answer. It holds a class and a status code, and
    nothing the vendor said."""

    def __init__(self, error_class: str, status_code: int | None = None, *,
                 limit_reason: str | None = None, wait_seconds: int | None = None):
        code = "" if status_code is None else f" (status {status_code})"
        super().__init__(f"{error_class}{code}")
        self.error_class = error_class
        self.status_code = status_code
        self.limit_reason = limit_reason
        self.wait_seconds = wait_seconds


class OutcomeUnknown(VendorError):
    """A request that creates work may have reached the vendor, and nothing says whether
    it did. It must not be sent again by this run."""


class NothingTaken(VendorError):
    """The vendor took nothing of the request: it was never sent, or a rate limit turned it
    away. For a submission that is no attempt, and the next run sends it as the same one."""


class RateLimited(NothingTaken):
    """A 429 that names a wait, handed back to a read that waits for it itself. `wait` is
    the wait the vendor named, in whole seconds. It is a `limit`, and the vendor took
    nothing."""

    def __init__(self, wait: int):
        super().__init__(LIMIT, 429)
        self.wait = wait


# The status codes that say a limit is reached, whatever the vendor: payment required, and
# too many requests.
LIMIT_STATUSES = (402, 429)


def refusal(code: int | None, limit_codes: Collection[int] = ()) -> str:
    """The class of a refusal, from the code the vendor gave it: an HTTP status, or a code
    of the vendor's own from the body of its answer. `limit_codes` is the vendor client's
    list of the codes that say a limit is reached."""
    return LIMIT if code in LIMIT_STATUSES or code in limit_codes else REFUSED


def classify(error: BaseException) -> tuple[str, int | None]:
    """An error class and a status code for anything a vendor call raised."""
    if isinstance(error, VendorError):
        return error.error_class, error.status_code
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        return (VENDOR_FAULT if code >= 500 else refusal(code)), code
    if isinstance(error, httpx.TimeoutException):
        return TIMEOUT, None
    if isinstance(error, httpx.TransportError):
        return CONNECTION, None
    return OTHER, None


class _Unreadable:
    """A wait the answer names in a form that cannot be read."""


UNREADABLE = _Unreadable()


def _named_wait(response: httpx.Response, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                *, header: str = "retry-after") -> int | None | _Unreadable:
    """The wait a rate limit answer names, in whole seconds: as a number of seconds or as
    a date (RFC 9110, Retry-After). None where it names none, and UNREADABLE where it names
    one in neither form."""
    text = response.headers.get(header, "").strip()
    if not text:
        return None
    if text.isascii() and text.isdigit():
        return int(text)
    if header != "retry-after":
        # Vendor reset headers used here document seconds only, never HTTP dates.
        return UNREADABLE
    try:
        until = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return UNREADABLE
    if until is None or until.tzinfo is None:
        return UNREADABLE
    return max(0, int((until - now()).total_seconds() + 0.999))


def call(client: httpx.Client, method: str, url: str, *, creates_work: bool = False,
         idempotent: bool = False, read: bool = False, limit_codes: Collection[int] = (),
         caller_waits: bool = False,
         wait_header: str = "retry-after",
         on_rate_wait: Callable[[], None] | None = None,
         sleep: Callable[[float], None] = time.sleep, **request) -> httpx.Response:
    """Make one vendor call under the retry policy, and return its answer.

    `creates_work` marks a request that makes billable work at the vendor, such as a post of
    tasks. `idempotent` says it carries a key the vendor uses to refuse a repeat. `read`
    marks a call that only reads, whatever its method: it is the one mark that lets a
    time-out after sending be tried again where no key guards the request.
    `limit_codes` is the vendor client's list of status codes that say a limit is reached,
    beside the two every vendor shares. `caller_waits` marks a read whose caller waits
    for a named wait itself: a 429 that
    names one raises `RateLimited` at once. Raises VendorError: OutcomeUnknown where the
    request may have arrived, on this try or on an earlier one, and NothingTaken where no
    try was sent or a rate limit turned every one away.
    """
    if read and creates_work:
        raise ValueError("a call that creates work at the vendor is not a read")
    if caller_waits and not read:
        raise ValueError("only a read waits for a rate limit itself")
    blind = creates_work and not idempotent
    # Whether a try of a request that creates work may have reached the vendor.
    arrived = False
    retries = 0
    while True:
        rate_wait = False
        last = retries >= RETRIES
        wait: float = WAITS[min(retries, len(WAITS) - 1)]
        try:
            response = client.request(method, url, **request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            # Raised before the request was sent, so trying again repeats nothing.
            if last:
                if arrived:
                    raise OutcomeUnknown(CONNECTION) from None
                raise NothingTaken(CONNECTION) from None
        except httpx.TransportError as error:
            # The request was sent, or may have been.
            error_class = TIMEOUT if isinstance(error, httpx.TimeoutException) else CONNECTION
            if blind or (creates_work and last):
                raise OutcomeUnknown(error_class) from None
            if last or not (read or creates_work):
                # Not marked as a read and guarded by no key: not sent again.
                raise VendorError(error_class) from None
            arrived = creates_work
        else:
            code = response.status_code
            if code == 429:
                named = _named_wait(response, header=wait_header)
                if named is not None and not isinstance(named, _Unreadable) and caller_waits:
                    raise RateLimited(named)
                if last or named is UNREADABLE or (
                        named is not None and named > LONGEST_NAMED_WAIT):
                    reason = ("rate_limit_retries_exhausted" if last else
                              "rate_limit_reset_unreadable" if named is UNREADABLE else
                              "rate_limit_wait_too_long")
                    seconds = (None if named is UNREADABLE else
                               int(wait) if named is None else named)
                    if arrived:
                        raise OutcomeUnknown(LIMIT, code,
                                             limit_reason=reason, wait_seconds=seconds)
                    raise NothingTaken(LIMIT, code, limit_reason=reason, wait_seconds=seconds)
                wait = wait if named is None else named
                rate_wait = True
            elif code >= 500:
                if blind:
                    raise OutcomeUnknown(VENDOR_FAULT, code)
                if last:
                    if creates_work:
                        raise OutcomeUnknown(VENDOR_FAULT, code)
                    raise VendorError(VENDOR_FAULT, code)
                arrived = creates_work
            elif code >= 400:
                error_class = refusal(code, limit_codes)
                raise VendorError(error_class, code,
                                  limit_reason="plan_cap" if error_class == LIMIT else None)
            elif 200 <= code < 300:
                return response
            else:
                # A redirect is not followed and an informational code is no answer: what
                # came back is not the vendor's answer, and is not read as one.
                if creates_work:
                    raise OutcomeUnknown(UNEXPECTED_STATUS, code)
                raise VendorError(UNEXPECTED_STATUS, code)
        retries += 1
        try:
            sleep(wait)
        except VendorError as error:
            if error.error_class == LIMIT and rate_wait:
                error.limit_reason = "rate_limit_past_deadline"
                error.wait_seconds = int(wait)
            raise
        if rate_wait and on_rate_wait is not None:
            on_rate_wait()
