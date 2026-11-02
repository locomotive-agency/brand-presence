"""Mentions: posts that one of a client's filters caught, from Syften.

A run asks for every match since its cursor, page by page, newest first. The cursor is the
newest match time stored. The framework moves it only after every page is stored raw and
the clean rows are written, so a run cut short leaves it where it was. A match is written
once, by its own ID, whichever run pulls it: one already in the facts table is left out of
a page before the page is stored.

A backlog can be larger than one run reaches, so a pull cut short is not started again
from the newest match. Once its pages are written, the oldest and
the newest match times it read are kept beside the cursor, as its resume point. The next
run goes on from there: it asks first for the page before the oldest time read, as the
pull would have, and pages on down to the cursor. Then the cursor moves to the newest time
read, and the point is cleared. A match made meanwhile is newer than that, and the run
after pulls it from the cursor. A point the window has passed ends the pull with nothing
asked. A point or a cursor that cannot be read is passed over by no run: the run stops
before any vendor call, with the class `resume point unreadable` or `cursor unreadable`,
until the administrator clears the row.

Only the matches of the client's own filters are kept: those whose tag the YAML configuration
lists, in the load the run is under. One Syften account can hold the filters of others,
and the vendor cannot be asked for some filters only, so every match of the account is
pulled. A match whose tag is not listed, or that has no tag, is not the client's. It is
left out of its page before anything is stored, and counted as `matches_unlisted`, on the
run's line and in its row. Nothing else of it is kept: not its tag, its text or its ID.
Its time still moves the cursor and asks for the page before it, so it is not pulled
again and the pull still ends. A parse leaves such a match out too, for a raw page stored
before this rule.

Syften turns a pull away with a rate limit after a few pages, and names how long to wait.
The sender waits that long, on the run's own sleep, and asks for the same page again, as
often as the run has time for: only where the wait ends by the run's deadline, the job's
time limit less the last tenth kept to load, write and close the run. A wait that would end
later, or a rate limit that names no wait and outlasts the retries, ends the pull as
`limit`, as any failed page does. Each wait is counted as `rate_limit_waits`, on the run's
line and in its row. No address, body or value of the vendor's is in either.

A pull ends on an empty page, or on a page that holds only the matches of the oldest time
in hand, which each next page is asked to hold again. Any other page that brings nothing
new cuts the pull short, and so does a match whose ID is no text, or a page of new matches
of which no time can be read: the cursor stays, and nothing the vendor still holds is
passed over. A match time later than the run is one that cannot be read.

A match with no time that can be read cannot be kept within the window: nothing says when
its text must go. It is left out of its page before anything is stored, and counted as
`matches_undated`.

The text, the title and the link of a post are kept for the client's mention text days and
no longer. Four things here hold that:

    the window     with no cursor, or one older than the window, a run asks from the start
                   of the window: that number of days before the run
    too old        a match dated before the window is dropped before anything is stored,
                   and counted, after those of a tag that is not listed
    the object     each raw page carries its oldest match date, used by local cleanup
                   and by the raw table's partition expiry
    the check      a run reads the partition expiry of the two tables that hold text, and
                   ends as `error` with the check `window` when either differs from the
                   client's number of days. It calls no vendor and writes nothing then

Nothing here changes a table's options. `brand-presence init` sets the expiry.

A match becomes two rows. Its date is the day, in universal time, of the time the vendor
matched it, which is not the time the post was made. `stg.mention_facts` holds what can be
counted: the date, the time of the post, the community, the type, the language, the brand,
the class and the keyword group. `stg.mention_text` holds the text, the title and the link.
The brand, the class, the keyword group and the country come from the client's filter tags,
in its YAML configuration, when the match is written. A filter that names no client
brand gives `unknown`, and one that names no country gives the country and the location
`unknown`. No tag is written in this file.

A match whose item is no object is refused, and the rest of its page is written. A field of
the item that the match leaves out is null in its row and counted as `match_fields_missing`,
so a field the vendor renames is seen. One it gives as null or empty is null, as the vendor
said. One it gives in another form, such as a number where a text belongs or a time that
cannot be read, is null and counted as `values_unreadable`.

The author of a post is taken out by src/brand_presence/vendors/syften.py, before a match
reaches this file.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import ClassVar

from google.api_core.exceptions import Forbidden, NotFound
from pydantic import BaseModel, Field, ValidationError

from brand_presence import http, status
from brand_presence.bigquery import query, text_batches, texts_parameter
from brand_presence.client_ref import ClientRef, MentionFilter
from brand_presence.http import VendorError
from brand_presence.rows import UNKNOWN, CleanRow, LedgerRow, RawRow, ResumingCursorRow
from brand_presence.runs import Period
from brand_presence.settings import Settings
from brand_presence.sources.base import (
    CURSOR,
    Answer,
    CursorSend,
    Page,
    Parsed,
    Request,
    Resume,
    SendContext,
    Source,
    Target,
    UnreadableState,
    Verdict,
    standard_columns,
)
from brand_presence.vendors import syften
from brand_presence.vendors.syften import Syften

COUNTRY = "country"
TAG = r"[A-Za-z0-9_-]+"
NAME = "mentions"

# The check of the source's own: the text tables expire at the client's mention text days.
WINDOW = status.WINDOW

# The rule a page trips when it is no list of matches.
NO_MATCHES = status.NO_MATCHES

# How a filter carries its tag, in the text the vendor gives back with a match. The form
# is assumed from the existing brand_presence. The YAML configuration takes no tag of another form.
_TAG = re.compile(rf"\$tag:({TAG})")

# The shortest wait for a rate limit, in seconds. A vendor that names a wait of nothing is not
# asked again at once.
SHORTEST_WAIT = 1

# How far ahead of the run a match time may be, for the clocks. One further ahead is not
# a time: it would hold the cursor past every match made before it.
AHEAD = timedelta(minutes=5)

_DAY_MS = 24 * 60 * 60 * 1000


# Row models --------------------------------------------------------------------------------

class MentionsRaw(RawRow):
    """One page of matches, as the vendor gave it, with the authors taken out. Its date is
    that of the oldest match in it, and the table expires by it."""

    table: ClassVar[str] = "raw.mentions"

    content_date: date


class MentionFact(CleanRow):
    """One match: what can be counted of it. It holds nothing of the post itself."""

    table: ClassVar[str] = "stg.mention_facts"
    natural_key: ClassVar[tuple[str, ...]] = ("match_id",)
    item_key: ClassVar[str | None] = "match_id"

    match_id: str = Field(min_length=1)
    match_date: date
    matched_at: datetime
    # When the post was made, where the vendor says. It cannot be read back once the text
    # has expired.
    posted_at: datetime | None
    community: str | None
    sub_community: str | None
    item_type: str | None
    language: str | None
    mention_class: str = Field(min_length=1)
    # Null where the match's filter names no brand it is about.
    subject_brand_id: str | None
    # What the vendor's own filter made of the match, where it says.
    accepted: bool | None
    # Not inferred from vendor text.
    sentiment: str | None
    topics: str | None
    # The keyword group of the match's filter, as the load the run is under gives it; null
    # where the filter names none or the group was not captured.
    keyword_group: str | None = None


class MentionText(CleanRow):
    """One match: the text, the title and the link of its post. The table expires at the
    client's mention text days."""

    table: ClassVar[str] = "stg.mention_text"
    natural_key: ClassVar[tuple[str, ...]] = ("match_id",)
    item_key: ClassVar[str | None] = "match_id"

    match_id: str = Field(min_length=1)
    match_date: date
    text: str | None
    title: str | None
    link: str | None


class MentionsTask(LedgerRow):
    table: ClassVar[str] = "ops.mentions_tasks"


class MentionsCursor(ResumingCursorRow):
    table: ClassVar[str] = "ops.mentions_cursor"


class MatchItem(BaseModel):
    """What a match holds of its post, which is an object. A match whose item is anything
    else is refused by this model, and the run says the model, the field and the kind of
    error, never the value."""

    item: dict


# The tables that hold a post's text, and so expire.
TEXT_TABLES = (MentionsRaw.table, MentionText.table)


# Sending ------------------------------------------------------------------------------------

def window_start(started_at: datetime, days: int) -> datetime:
    """The start of the client's window: that number of days before the run."""
    return started_at - timedelta(days=days)


def time_read(written: object, latest: datetime) -> datetime | None:
    """A time of a match, or None where it cannot be read or is later than `latest`."""
    at = syften.time_of(written)
    return at if at is not None and at <= latest else None


class SyftenSend(CursorSend):
    """The vendor's side of a mentions run. One is made for a run, and it keeps what the
    run has seen: the vendor pages backwards from the newest match, by the oldest match
    time in hand."""

    def __init__(self, context: SendContext):
        self.api = Syften(context.http, context.keys[syften.TOKEN], sleep=context.sleep)
        self.bq = context.bq
        self.started_at = context.started_at
        self.sleep = context.sleep
        self.clock = context.clock
        self.deadline = context.deadline
        # The times the pull waited for a rate limit the vendor named.
        self.waits = 0
        self.latest = context.started_at + AHEAD
        self.window_start = window_start(context.started_at,
                                         context.settings.mention_text_days)
        if context.ref is None:
            # With no configuration, no match is the client's: a pull would store nothing and move
            # the cursor past every match.
            raise ValueError("a mentions run needs the YAML configuration's load it is under")
        # The tags of the client's own filters. A match of any other tag is not stored.
        self.listed = frozenset(found.tag for found in context.ref.mention_filters)
        # The oldest match time of the pages pulled so far, and how the vendor wrote it.
        self.oldest: datetime | None = None
        self.oldest_written: str | None = None
        # The newest match time, and how the vendor wrote it.
        self.newest: tuple[datetime, str] | None = None
        self.seen: set[str] = set()
        # Where the last run's pull was cut short: the oldest match time it had read. Its
        # matches of that time are asked for again, and those met then are not counted
        # again.
        self.resumed: datetime | None = None

    def go_on(self, cursor: str | None, point: str | None) -> None:
        """Read the cursor, and go on from where the last run's pull was cut short: the
        page before the oldest match time it had read, with the newest match time it had
        read as where the cursor moves once the pull reaches the cursor. A cursor or a
        point that cannot be read raises `UnreadableState`, and the vendor is asked
        nothing: neither is passed over."""
        if cursor is not None and syften.time_of(cursor) is None:
            raise UnreadableState(status.CURSOR_UNREADABLE)
        if point is None:
            return
        found = resume_of(point)
        if found is None:
            raise UnreadableState(status.RESUME_POINT_UNREADABLE)
        (oldest, oldest_written), newest = found
        self.oldest, self.oldest_written, self.newest = oldest, oldest_written, newest
        self.resumed = oldest

    def resume(self, cursor: str | None) -> Resume | None:
        """Where the next run goes on from: the oldest and the newest match times read, by
        this pull and by the pulls it went on from. Each page down to the oldest is
        written by now, and the next page asked is the one this pull would have asked.
        With no cursor, the next run is given where this pull asked from, which it reads
        as it reads none."""
        if self.oldest_written is None or self.newest is None:
            return None
        point = json.dumps({"oldest": self.oldest_written, "newest": self.newest[1]})
        return Resume(cursor if cursor is not None else self.after(None), point)

    def after(self, cursor: str | None) -> str:
        """Where the pull starts: the cursor, or the start of the window where there is no
        cursor or the cursor is older than the window. A cursor that cannot be read is
        refused, as `go_on` refuses it before the first page."""
        if cursor is None:
            return syften.as_time(self.window_start)
        held = syften.time_of(cursor)
        if held is None:
            raise UnreadableState(status.CURSOR_UNREADABLE)
        return syften.as_time(self.window_start) if held < self.window_start else cursor

    def held(self, match_ids: list[str]) -> set[str]:
        """Those of the matches that are written already."""
        found: set[str] = set()
        for batch in text_batches(sorted(match_ids)):
            found |= {row["match_id"] for row in query(
                self.bq,
                f"SELECT match_id FROM {MentionFact.table} WHERE match_id IN UNNEST(@items)",
                [texts_parameter("items", batch)],
            )}
        return found

    def matches(self, after: str, before: str | None) -> object:
        """One page of matches, as the vendor client gives it. A rate limit that names a
        wait is waited for, and the same page asked for again, while the wait ends by the
        run's deadline. Otherwise the rate limit is raised, and the pull ends there."""
        while True:
            try:
                return self.api.matches(after, before)
            except http.RateLimited as limited:
                wait = max(limited.wait, SHORTEST_WAIT)
                if not self.has_time(wait):
                    raise
                self.waits += 1
                self.sleep(wait)

    def has_time(self, wait: float) -> bool:
        """Whether a wait of that many seconds ends by the run's deadline."""
        return (self.deadline is not None
                and self.clock() + timedelta(seconds=wait) <= self.deadline)

    def counts(self) -> dict[str, int]:
        return {"rate_limit_waits": self.waits} if self.waits else {}

    def no_page(self, said: object, after: str) -> Page:
        """An answer that is no list of matches: stored raw as unusable, and the pull stops
        there. Nothing in it can be older than where the pull started, so it is dated
        there, and what it may hold of a post goes when that date leaves the window."""
        day = syften.time_of(after).date()
        answer = Answer({"answer": said}, content_date=day,
                        custom_time=datetime(day.year, day.month, day.day, tzinfo=UTC))
        return Page(answer, cursor=None, more=False)

    def pull(self, cursor: str | None, page: int) -> Page:
        after = self.after(cursor)
        # The oldest time in hand is asked for again: a match of that same time may have
        # been left off the page before. `seen` drops the ones in hand.
        before = None if self.oldest is None else syften.just_after(self.oldest)
        if before is not None and syften.time_of(before) <= syften.time_of(after):
            # The pull went on from a point older than where it starts: what was left of
            # it has left the window. Nothing is asked, and the pull has reached the end.
            return self.reached(cursor)
        matches = self.matches(after, before)
        if not syften.is_page(matches):
            return self.no_page(matches, after)
        if any(not isinstance(match.get("id"), str) for match in matches):
            # A match that cannot be told from another. The pull is cut short.
            raise VendorError(http.OTHER)
        new: list[tuple[dict, datetime | None]] = []
        for match in matches:
            if match["id"] in self.seen:
                continue
            self.seen.add(match["id"])
            new.append((match, time_read(match.get("matched_on"), self.latest)))
        if matches and not new:
            # Nothing new. It is the end of what the vendor holds when the page is the
            # matches of the oldest time over again, and not the most a page can hold.
            # Anything else is a page the pull cannot get past.
            again = all(syften.time_of(match.get("matched_on")) == self.oldest
                        for match in matches)
            if not again or len(matches) >= syften.MATCHES_PER_PAGE:
                raise VendorError(http.OTHER)
        if new and all(at is None for _, at in new):
            # No time of the page can be read, so the page before it in time cannot be
            # asked for. The pull is cut short, and the cursor stays.
            raise VendorError(http.OTHER)
        for match, at in new:
            if at is None:
                continue
            if self.newest is None or at > self.newest[0]:
                self.newest = (at, match["matched_on"])
            if self.oldest is None or at < self.oldest:
                self.oldest, self.oldest_written = at, match["matched_on"]
        # Dropped before anything is stored, or asked of the facts table: a match whose
        # tag the configuration does not list, then one with no time that can be read, then one
        # older than the window, each counted, and one that is written already.
        listed = [(match, at) for match, at in new if tag_of(match.get("filter")) in self.listed]
        dated = [(match, at) for match, at in listed if at is not None]
        undated = len(listed) - len(dated)
        # One at or before the cursor was met by the run that moved the cursor there, and
        # counted by it. The vendor gives the cursor's own match again, every run.
        known = syften.time_of(cursor) if self.after(cursor) == cursor else None
        # So was one of the oldest time the last run's pull read, where this one went on
        # from it.
        unlisted = sum(1 for match, at in new
                       if tag_of(match.get("filter")) not in self.listed
                       and not (known is not None and at is not None and at <= known)
                       and not (self.resumed is not None and at is not None
                                and at >= self.resumed))
        in_window = [(match, at) for match, at in dated if at >= self.window_start]
        too_old = len(dated) - len(in_window)
        written = self.held([match["id"] for match, _ in in_window])
        kept = [(match, at) for match, at in in_window if match["id"] not in written]
        oldest = min((at.date() for _, at in kept), default=None)
        counts = {name: count for name, count in (
            ("matches_unlisted", unlisted), ("matches_undated", undated),
            ("matches_too_old", too_old)) if count}
        answer = Answer(
            {"matches": [match for match, _ in kept]},
            # A page with no dated match holds no text to expire. Its row has the run's date.
            content_date=oldest or self.started_at.astimezone(UTC).date(),
            custom_time=None if oldest is None else datetime(
                oldest.year, oldest.month, oldest.day, tzinfo=UTC),
            counts=counts,
        )
        return Page(answer, cursor=self.moved(cursor), more=bool(new))

    def moved(self, cursor: str | None) -> str | None:
        """Where the cursor moves if the pull ends here: the newest match time read, and
        only on, never to a time before the one it holds."""
        start = syften.time_of(cursor)
        if self.newest is not None and (start is None or self.newest[0] > start):
            return self.newest[1]
        return None

    def reached(self, cursor: str | None) -> Page:
        """The end of a pull that has nothing left to ask: a page of no match, dated the
        day of the run."""
        answer = Answer({"matches": []}, content_date=self.started_at.astimezone(UTC).date())
        return Page(answer, cursor=self.moved(cursor), more=False)


# A match into rows ---------------------------------------------------------------------------

def resume_of(point: str) -> tuple[tuple[datetime, str], tuple[datetime, str]] | None:
    """The oldest and the newest match times of a resume point, each read and as the
    vendor wrote it, or None where it cannot be read."""
    try:
        held = json.loads(point)
    except ValueError:
        return None
    if not isinstance(held, dict):
        return None
    oldest, newest = held.get("oldest"), held.get("newest")
    oldest_at, newest_at = syften.time_of(oldest), syften.time_of(newest)
    if oldest_at is None or newest_at is None or oldest_at > newest_at:
        return None
    return (oldest_at, oldest), (newest_at, newest)


def tag_of(filter_text: object) -> str | None:
    """The tag of the filter that caught a match, from the filter's text."""
    found = _TAG.search(filter_text) if isinstance(filter_text, str) else None
    return found.group(1) if found else None


def placed(settings: ClientRef, mapping: MentionFilter) -> Target:
    """The client brand, the country and the location of a match, from its tag's mapping.
    What the mapping does not give is `unknown`, never a guess."""
    country = mapping.country_code or UNKNOWN
    place = next((found for found in settings.locations
                  if found.country_code == country and found.kind == COUNTRY), None)
    return Target(mapping.client_brand_id or UNKNOWN, country,
                  place.location_id if place is not None and country != UNKNOWN else UNKNOWN)


class Fields:
    """The fields of a match's item, read one by one. A field the match leaves out is None
    and counted as missing; one it gives as null or empty is None, as the vendor said; one
    it gives in another form is None and counted as unreadable. Nothing of the value is
    kept."""

    def __init__(self) -> None:
        self.missing = 0
        self.unreadable = 0

    def _absent(self, held: Mapping[str, object], name: str) -> bool:
        """Whether the field gives no value: left out, which is counted, or null or empty."""
        if name not in held:
            self.missing += 1
            return True
        return held[name] is None or held[name] == ""

    def text(self, held: Mapping[str, object], name: str) -> str | None:
        if self._absent(held, name):
            return None
        value = held[name]
        if not isinstance(value, str):
            self.unreadable += 1
            return None
        return value

    def time(self, held: Mapping[str, object], name: str, latest: datetime) -> datetime | None:
        if self._absent(held, name):
            return None
        at = time_read(held[name], latest)
        if at is None:
            self.unreadable += 1
        return at

    def flag(self, held: Mapping[str, object], parent: str, name: str) -> bool | None:
        if self._absent(held, parent):
            return None
        outer = held[parent]
        if not isinstance(outer, dict):
            self.unreadable += 1
            return None
        if self._absent(outer, name):
            return None
        value = outer[name]
        if not isinstance(value, bool):
            self.unreadable += 1
            return None
        return value

    def counts(self) -> dict[str, int]:
        return {name: count for name, count in (
            ("match_fields_missing", self.missing), ("values_unreadable", self.unreadable))
            if count}


def rows_of(match: Mapping[str, object], raw: RawRow, settings: ClientRef,
            mapping: MentionFilter, fields: Fields) -> tuple[MentionFact, MentionText]:
    """The two rows of one match, from the listed filter of its tag, with what is missing
    or unreadable counted in `fields`. Raises the refusal of a row model or of
    `MatchItem`, which holds no value of the match."""
    standard = standard_columns(raw, settings, placed(settings, mapping))
    item = MatchItem(item=match.get("item")).item
    # A match time that cannot be read, or is later than the page was pulled, is handed
    # over as it is, for the row model to refuse. A run leaves such a match out before it
    # is stored; a page stored before that rule may hold one.
    latest = raw.pulled_at + AHEAD
    at = time_read(match.get("matched_on"), latest)
    matched_at = at if at is not None else match.get("matched_on")
    match_date = at.date() if at is not None else match.get("matched_on")
    fact = MentionFact(
        **standard, match_id=match.get("id"), match_date=match_date, matched_at=matched_at,
        posted_at=fields.time(item, "timestamp", latest),
        community=fields.text(item, "backend"), sub_community=fields.text(item, "backend_sub"),
        item_type=fields.text(item, "type"), language=fields.text(item, "lang"),
        mention_class=mapping.mention_class, subject_brand_id=mapping.subject_brand_id,
        accepted=fields.flag(item, "analysis", "accept"),
        sentiment=None, topics=None, keyword_group=mapping.keyword_group,
    )
    text = MentionText(
        **standard, match_id=match.get("id"), match_date=match_date,
        text=fields.text(item, "text"), title=fields.text(item, "title"),
        link=fields.text(item, "item_url"),
    )
    return fact, text


# The source --------------------------------------------------------------------------------

class MentionsSource(Source):
    name = NAME
    shape = CURSOR
    raw_model = MentionsRaw
    clean_models = (MentionFact, MentionText)
    cursor_model = MentionsCursor
    ledger_model = MentionsTask
    recorded_counts = ("matches_unlisted", "rate_limit_waits")

    def __init__(self, sender: Callable[[SendContext], CursorSend] | None = None):
        self.sender = sender

    def checks(self, bq, settings: Settings) -> tuple[str, str] | None:
        """The two tables that hold text expire at the client's mention text days, or the
        run stops. The expiry is read and never set here."""
        wanted = settings.mention_text_days
        for name in TEXT_TABLES:
            try:
                partitioning = bq.client.get_table(bq.settings.table(name)).time_partitioning
            except (NotFound, Forbidden):
                return WINDOW, f"the partition expiry of {name} could not be read"
            expires = partitioning.expiration_ms if partitioning is not None else None
            if expires is None:
                return WINDOW, (f"{name} has no partition expiry, and the client's mention "
                                f"text days is {wanted}")
            if int(expires) != wanted * _DAY_MS:
                return WINDOW, (
                    f"{name} expires at {int(expires) / _DAY_MS:g} days, and the client's "
                    f"mention text days is {wanted}; `brand-presence init` sets it"
                )
        return None

    def plan(self, settings: ClientRef, period: Period) -> list[Request]:
        # A pull is planned by its cursor.
        return []

    def send(self, context: SendContext) -> CursorSend:
        return SyftenSend(context) if self.sender is None else self.sender(context)

    def usable(self, answer: Answer) -> Verdict:
        if isinstance(answer.response.get("matches"), list):
            return Verdict(True)
        return Verdict(False, NO_MATCHES)

    def parse(self, raw: RawRow, settings: ClientRef) -> Parsed:
        rows: list[CleanRow] = []
        refused: list[ValidationError] = []
        counts: dict[str, int] = {}
        filters = {found.tag: found for found in settings.mention_filters}
        unlisted = 0
        for match in json.loads(raw.response)["matches"]:
            mapping = filters.get(tag_of(match.get("filter")) or "")
            if mapping is None:
                # Not the client's: a page stored before such matches were left out of it.
                unlisted += 1
                continue
            # What one match lacks is counted only once both its rows are made.
            fields = Fields()
            try:
                fact, text = rows_of(match, raw, settings, mapping, fields)
            except ValidationError as error:
                # The match is left out and said; the rest of the page is written.
                refused.append(error)
                continue
            rows += [fact, text]
            for name, count in fields.counts().items():
                counts[name] = counts.get(name, 0) + count
        if unlisted:
            counts["matches_unlisted"] = unlisted
        return Parsed(rows, counts=counts, refused=refused)
