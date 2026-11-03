"""Ported window workflow: complete pages, replace snapshots, bounded weekly history."""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta

from brand_presence import google_token, ledger, request_keys, status
from brand_presence.bigquery import raw_prefix
from brand_presence.errors import UserError
from brand_presence.http import VendorError
from brand_presence.rows import LedgerRow, RawRow
from brand_presence.runner import _Run
from brand_presence.runs import Period, periods_before, window_of
from brand_presence.sources.base import (
    ERROR,
    PARTIAL,
    SUCCESS,
    Answer,
    Incomplete,
    Request,
    SendContext,
    Verdict,
    assembled,
    keyed,
    pages_of,
    request_of,
    stored_page,
    stored_request,
    with_pages,
)

JUDGE = status.JUDGE
RAW_MISSING = status.RAW_MISSING
NOTHING_PLANNED = "nothing planned"
WRONG_WINDOW = "wrong window"
NOT_ANSWERED = "not answered"
LOAD_PATHS = 10000
RECOVERY_FAILURES = 3
TRANSIENT = (status.CONNECTION, status.TIMEOUT, status.VENDOR_FAULT, status.LIMIT)


class _Stop(UserError):
    def __init__(self, check, line, error_class=None):
        super().__init__(line)


def page_path(source, period, run_id, key, attempt, part, number):
    return raw_prefix(source, period, run_id) + request_keys.page_object_name(
        key, attempt, part, number)


class WindowRun(_Run):
    def __init__(self, source, settings, world, period=None):
        super().__init__(source, settings, world, period)
        self.named = period is not None
        self.again = False
        self.standing = Counter()
        self.refused = {}
        self.stopped = set()
        self.ended_on = None
        self.out_of_time = False
        self.history_sent = 0
        self.deadline = self.started_at + timedelta(seconds=world.wait_seconds)

    def note(self, counts):
        for name in ("raw_rows", "clean_rows"):
            self.counts[name] += counts.get(name, 0)
        super().note({k: v for k, v in counts.items() if k not in ("raw_rows", "clean_rows")})

    def context(self):
        return SendContext(self.world.keys, self.world.http, self.bq, self.started_at,
            self.world.sleep, settings=self.settings, ref=self.ref, run_id=self.run_id,
            deadline=self.deadline, clock=self.clock,
            tokens=self.world.tokens or google_token.ApplicationDefault())

    def tasks(self):
        self.window_status = self.window()

    def judge(self):
        return self.window_status

    def window(self) -> str:
        """The run of a window source: its own period, then, unless the period was named,
        each earlier period as history. Returns the status."""
        source = self.source
        self.ledger = ledger.Ledger(self.bq, source.ledger_model, self.clock)
        self.sender = source.send(self.context())
        # This run holds the lock and has closed what did not finish, so a row still
        # `sending` is a dead run's. Nothing of that attempt is parsed.
        left = 0
        if source.durable_pages:
            self.restore_collected()
            self.recover_pages()
        else:
            left = self.ledger.did_not_finish(self.run_id)
        if left:
            self.lines.append(f"{left} attempt(s) a run left open were marked failed: "
                              f"{ledger.DID_NOT_FINISH}")
        self.finish_pages(self.period.name if self.named else None)

        period = self.period
        requests = self.plan_window(period)
        if not requests:
            # Nothing of its own period, and so no history either: an identity's history
            # waits for its own period's answer.
            raise _Stop(NOTHING_PLANNED, f"check failed: {NOTHING_PLANNED}: the load applied "
                        f"plans no request of {self.source.name} for {period.name}")
        attempts = ledger.to_send(list(requests), self.ledger.of_period(period.name),
                                  again=self.again, refusals=source.refusals)
        unsent = self.send_period(period, requests, attempts)
        self.load_and_write_pages()
        rows = self.ledger.of_period(period.name)
        for key in requests:
            if self.window_answered(key, rows) and not (source.require_latest_attempt
                                                       and key in unsent):
                self.standing["answered"] += 1
            else:
                self.standing["pending" if key in unsent else "failed"] += 1
        if not self.named:
            # An identity's history waits for a good answer to its own period.
            self.history({request_keys.content_hash(source.history_identity(requests[key]))
                          for key in requests
                          if key not in unsent and self.window_answered(key, rows)})
        return self.judge_window()

    def restore_collected(self):
        for row in self.ledger.of_period(self.period.name):
            if row.state != ledger.COLLECTED:
                continue
            if json.loads(row.request)["snapshot_id"] != self.ref.snapshot_id:
                continue
            request = request_of(row.request)
            for path in self.page_paths(row):
                raw = self.warehouse.read_raw(self.source.raw_model, path)
                if raw is not None:
                    held = json.loads(raw.request)
                    self.sender.restore(request, held["part"], held["page"],
                                        json.loads(raw.response))

    def recover_pages(self):
        self.ledger.orphaned(self.run_id)
        current = self.sender
        senders = {self.ref.snapshot_id: current}
        try:
            for row in self.ledger.in_state(ledger.UNKNOWN):
                if self.named and row.period != self.period.name:
                    continue
                if self.ended_on is not None or self.time_up():
                    break
                request = request_of(row.request)
                group = self.source.group(request)
                if group in self.refused:
                    continue
                held = json.loads(row.request)
                ref = self.ref_at(held["load_number"])
                if ref.snapshot_id not in senders:
                    senders[ref.snapshot_id] = self.source.send(replace(self.context(), ref=ref))
                self.sender = senders[ref.snapshot_id]
                checked = self.check_group(group, [request], recovering=row)
                if checked is not None:
                    if group not in self.refused:
                        self.error(checked[0], status=checked[1])
                    continue
                if self.ended_on is not None or self.time_up():
                    break
                period = Period(row.period, self.source.window(request)[0])
                self.pull_request(period, row.request_key, row.attempt, request,
                                  group, recovering=row)
                self.load_and_write_pages()
        finally:
            self.sender = current

    def time_up(self):
        self.out_of_time = self.out_of_time or self.clock() >= self.deadline
        return self.out_of_time

    def window_answered(self, key: str, rows: list[LedgerRow]) -> bool:
        """Use the source's freshness rule for both standing and history eligibility."""
        if self.source.require_latest_attempt:
            latest = max((row for row in rows if row.request_key == key),
                         key=lambda row: row.attempt, default=None)
            return latest is not None and latest.state == ledger.COLLECTED and (
                latest.written_at is not None)
        return ledger.is_answered(key, rows)

    def plan_window(self, period: Period) -> dict[str, Request]:
        """The requests of one period under their keys, each made from its identity fields
        alone. A request that asks for other dates than its period's is a fault of the
        source, and nothing is sent."""
        planned = list(self.source.plan(self.ref, period))
        try:
            planned = list(self.sender.expand(planned))
        except VendorError as error:
            raise _Stop(None, error.error_class, error_class=error.error_class) from None
        finally:
            self.note(self.sender.counts())
        if any(self.source.window(request) != window_of(period) for request in planned):
            self.error(WRONG_WINDOW)
            raise _Stop(WRONG_WINDOW, f"the plan of {period.name} asks for dates that are "
                        "not the period's")
        return keyed(planned, self.settings.client_id, self.source.name, period.name,
                     self.source.identity)

    def history(self, answered: set[str]) -> None:
        """Each earlier period, newest first: what has no usable answer and an attempt
        left is sent, up to the entry's cap, and written under its own period. A request
        of the past that is answered, or out of attempts, is neither sent nor counted."""
        try:
            count = self.sender.history_periods(self.period, self.source.history_periods)
        except VendorError as error:
            raise _Stop(None, error.error_class, error_class=error.error_class) from None
        earlier = periods_before(self.period, count)
        held = self.ledger.of_periods([period.name for period in earlier])
        for period in earlier:
            # A request that reaches back reads its own past, and is never history.
            requests = {key: request for key, request in self.plan_window(period).items()
                        if not self.source.reaches_back(request)}
            rows = held[period.name]
            attempts = ledger.to_send(list(requests), rows, refusals=self.source.refusals)
            unsent = self.send_period(period, requests, attempts, answered=answered)
            if len(unsent) < len(attempts):
                self.load_and_write_pages()
                rows = self.ledger.of_period(period.name)
            for key in requests:
                if key in unsent:
                    self.standing["pending"] += 1
                elif self.window_answered(key, rows):
                    self.standing["answered"] += key in attempts
                elif key in attempts or ledger.is_stored(key, rows):
                    # Sent by this run and not answered, or stored and never written clean.
                    self.standing["failed"] += 1

    def send_period(self, period: Period, requests: Mapping[str, Request],
                    attempts: Mapping[str, int], *,
                    answered: set[str] | None = None) -> set[str]:
        """Send the requests of one period that are to be sent, each as its attempt.
        `answered` is given for a past period: the identities whose own period is answered.
        Returns the keys left unsent for a reason the run can name: they are pending."""
        source = self.source
        history = answered is not None
        unsent: set[str] = set()
        groups = {key: source.group(requests[key]) for key in attempts}
        # What the source's check said of each group, for this period.
        checked: dict[str, tuple[str, int | None] | None] = {}
        for key, attempt in attempts.items():
            request, group = requests[key], groups[key]
            identity = request_keys.content_hash(source.history_identity(request))
            if self.ended_on is not None or group in self.refused:
                unsent.add(key)
                continue
            if history and (identity not in answered or identity in self.stopped
                            or self.history_sent >= source.history_requests):
                unsent.add(key)
                continue
            if self.time_up():
                unsent.add(key)
                continue
            if group not in checked:
                checked[group] = self.check_group(group, [
                    requests[other] for other in attempts if groups[other] == group
                ])
            if self.ended_on is not None or group in self.refused:
                unsent.add(key)
                continue
            if checked[group] is not None:
                # Held back: not sent, no ledger row, no attempt.
                self.error(checked[group][0], status=checked[group][1])
                unsent.add(key)
                continue
            state = self.pull_request(period, key, attempt, request, group)
            if history:
                self.history_sent += 1
                if state != ledger.COLLECTED:
                    # One bad answer of the past stops this identity's history for the run.
                    self.stopped.add(identity)
        return unsent

    def check_group(self, group: str, requests: list[Request], *,
                    recovering: LedgerRow | None = None) -> tuple[str, int | None] | None:
        """Ask the source whether a group's requests of one period are held back: the
        class and the status code to hold them back with, or None. A check that ends with
        a class the source names as a refusal refuses the group for the run."""
        try:
            if recovering is not None:
                part = self.sender.parts[0]
                path = page_path(self.source.name, recovering.period, recovering.run_id,
                                 recovering.request_key, recovering.attempt, part, 1)
                raw = self.warehouse.read_raw(self.source.raw_model, path)
                if raw is not None:
                    self.sender.restore(requests[0], part, 1, json.loads(raw.response))
            held = self.sender.check(group, requests)
        except VendorError as error:
            if recovering is not None:
                self.failed_pull(recovering, error.error_class, error.status_code,
                                 recovering=True, recoverable=error.error_class in TRANSIENT)
            if error.error_class in self.source.refusals:
                self.refuse(group, error.error_class, error.status_code)
                self.error(error.error_class, status=error.status_code)
            return error.error_class, error.status_code
        except Exception as error:  # noqa: BLE001 - one group, not the run
            if recovering is not None:
                self.failed_pull(recovering, type(error).__name__, recovering=True)
            self.lines.append(f"a group's check raised: {type(error).__name__}")
            return type(error).__name__, None
        finally:
            self.note(self.sender.counts())
        if held is not None and recovering is not None:
            self.failed_pull(recovering, held, recovering=True)
        if held in self.source.refusals:
            # A refusal the check gave as its answer is one all the same.
            self.refuse(group, held, None)
            self.error(held)
        return None if held is None else (held, None)

    def refuse(self, group: str, error_class: str, status_code: int | None) -> None:
        """A group refused for the run. A class the source names as ending the run ends
        it: nothing more is sent to any group."""
        self.refused[group] = (error_class, status_code)
        if error_class in self.source.ends_run and self.ended_on is None:
            self.ended_on = error_class

    def pull_request(self, period: Period, key: str, attempt: int, request: Request,
                     group: str, recovering: LedgerRow | None = None) -> str:
        """One attempt at one window request: the `sending` row, then every part and page,
        each stored raw before the next is asked for, then the row's new state, which is
        returned. A request any page of which fails has failed, and is asked again whole."""
        source, sender = self.source, self.sender
        text = (recovering.request if recovering else
                stored_request(request, self.ref.load_number, self.ref.snapshot_id))
        row = recovering or source.ledger_model(
            request_key=key, period=period.name, attempt=attempt, vendor_task_id=None,
            state=ledger.SENDING, run_id=self.run_id, created_at=self.clock(),
            submitted_at=None, collected_at=None, stored_run_id=None, loaded_at=None,
            written_at=None, error_class=None, vendor_status=None, request=text,
        )
        if recovering is None:
            self.ledger.add([row])
            self.counts["requests_sent"] += 1
        pages: list[RawRow] = []
        paths: list[str] = []
        stored: list[tuple[str, int]] = []
        verdict = Verdict(True)
        try:
            for part in sender.parts:
                if sender.skip_part(request, part):
                    stored.append((part, 0))
                    continue
                number = 0
                while verdict.usable and number < max(1, sender.max_pages):
                    number += 1
                    raw, path, more = self.read_page(row, request, part, number)
                    verdict = self.verdict_of(Answer(json.loads(raw.response)))
                    paths.append(path)
                    pages.append(raw)
                    if not more:
                        break
                stored.append((part, number))
                if not verdict.usable:
                    break
        except VendorError as error:
            self.error(error.error_class, status=error.status_code)
            if error.error_class in source.refusals:
                # Nothing more goes to this group in the run, and the attempt is not counted.
                self.refuse(group, error.error_class, error.status_code)
            return self.failed_pull(row, error.error_class, error.status_code,
                                    recovering=recovering is not None,
                                    recoverable=error.error_class in TRANSIENT)
        except Exception as error:  # noqa: BLE001 - one request, not the run
            name = type(error).__name__
            self.error(name)
            self.lines.append(f"a pull could not be made or stored: {name}")
            return self.failed_pull(row, name, recovering=recovering is not None)
        finally:
            self.note(sender.counts())
        complete = with_pages(text, stored)
        if verdict.usable:
            # Every part and page is stored. Now the answer is judged as a whole.
            try:
                whole = assembled(complete, pages)
                verdict = source.complete(request, Answer(json.loads(whole.response)))
                self.note(verdict.counts)
            except Exception:  # noqa: BLE001 - an answer that cannot be judged is no answer
                verdict = Verdict(False, JUDGE)
        if verdict.usable:
            self.counts["answers_usable"] += 1
        else:
            self.counts["answers_unusable"] += 1
            self.error(verdict.rule or ledger.UNUSABLE)
        marked = ledger.changed(
            row, state=ledger.COLLECTED if verdict.usable else ledger.UNUSABLE,
            collected_at=self.clock(), stored_run_id=row.run_id, request=complete,
            error_class=None if verdict.usable else (verdict.rule or ledger.UNUSABLE),
        )
        self.ledger.rewrite([marked])
        self.stored.append((marked, pages, paths))
        return marked.state

    def failed_pull(self, row, error_class, vendor_status=None, *, recovering=False,
                    recoverable=True):
        state = ledger.FAILED
        text = row.request
        if recovering:
            held = json.loads(text)
            failures = held.get("recovery_failures", 0) + 1
            text = json.dumps({**held, "recovery_failures": failures}, sort_keys=True)
            if (recoverable and error_class not in self.source.refusals
                    and failures < RECOVERY_FAILURES):
                state = ledger.UNKNOWN
        self.ledger.rewrite([ledger.changed(row, state=state, request=text,
                                           error_class=error_class, vendor_status=vendor_status)])
        return state

    def read_page(self, row, request, part, number):
        path = page_path(self.source.name, row.period, row.run_id, row.request_key,
                         row.attempt, part, number)
        if self.source.durable_pages:
            raw = self.warehouse.read_raw(self.source.raw_model, path)
            if raw is not None:
                more = json.loads(raw.request)["more"]
                self.sender.restore(request, part, number, json.loads(raw.response))
                return raw, path, more
        page = self.sender.pull(request, part, number)
        verdict = self.verdict_of(page.answer)
        text = stored_page(row.request, part, number)
        held = json.loads(text)
        if self.source.durable_pages:
            text = json.dumps({**held, "more": page.more}, sort_keys=True)
        raw = self.source.raw_model(
            run_id=self.run_id, request_key=row.request_key, period=row.period,
            content_date=page.answer.content_date, pulled_at=self.clock(), request=text,
            response=json.dumps(page.answer.response, ensure_ascii=False),
            usable=verdict.usable, load_number=held["load_number"], snapshot_id=held["snapshot_id"],
        )
        path = self.warehouse.save_raw(
            self.source.name, raw,
            request_keys.page_object_name(row.request_key, row.attempt, part, number),
            storage_run_id=row.run_id,
        )
        self.note(page.answer.counts)
        return raw, path, page.more

    def load_and_write_pages(self) -> None:
        """Load the pages this run stored and has not loaded into the raw table, then
        write the clean rows of the usable answers, each from its complete answer."""
        stored, self.stored = self.stored, []
        if not stored:
            return
        batch: list[str] = []
        for _, _, paths in stored:
            if batch and len(batch) + len(paths) > LOAD_PATHS:
                self.counts["raw_rows"] += self.warehouse.load_raw(
                    self.source.raw_model, batch)
                batch = []
            batch += paths
        self.counts["raw_rows"] += self.warehouse.load_raw(self.source.raw_model, batch)
        now = self.clock()
        loaded = [(ledger.changed(row, loaded_at=now), pages) for row, pages, _ in stored]
        self.ledger.rewrite([row for row, _ in loaded])
        self.write([(row, assembled(row.request, pages)) for row, pages in loaded
                    if row.state == ledger.COLLECTED])

    def page_paths(self, row: LedgerRow) -> list[str]:
        """Where the pages of a stored answer are, from what its ledger row holds: under
        the run that stored it, by part and page. Nothing is listed."""
        pages = pages_of(row.request)
        if row.stored_run_id is None or not pages:
            return []
        return [page_path(self.source.name, row.period, row.stored_run_id, row.request_key,
                          row.attempt, part, number)
                for part, count in pages for number in range(1, count + 1)]

    def finish_pages(self, only: str | None) -> None:
        """Finish what earlier runs of a window source left: stored answers not yet loaded
        are loaded, and usable answers not yet written clean are parsed and written. No
        vendor is asked. `only` keeps it to one period, for a run that was given its period."""
        raw_model = self.source.raw_model
        waiting = [row for row in self.ledger.not_loaded() if only in (None, row.period)]
        for period in sorted({row.period for row in waiting}):
            loaded = self.warehouse.loaded(raw_model, period)
            to_load, done = [], []
            for row in (r for r in waiting if r.period == period):
                paths = self.page_paths(row)
                if (row.stored_run_id, row.request_key) in loaded:
                    done.append(row)
                elif not paths or not all(self.warehouse.has_raw(path) for path in paths):
                    self.error(RAW_MISSING)
                else:
                    to_load += paths
                    done.append(row)
            self.counts["raw_rows"] += self.warehouse.load_raw(raw_model, sorted(set(to_load)))
            now = self.clock()
            self.ledger.rewrite([ledger.changed(row, loaded_at=now) for row in done])
        unwritten = [row for row in self.ledger.not_written() if only in (None, row.period)]
        # Period by period, so that a write that fails holds up its own period alone.
        for period in sorted({row.period for row in unwritten}):
            answers = []
            for row in (r for r in unwritten if r.period == period):
                pages = [self.warehouse.read_raw(raw_model, path)
                         for path in self.page_paths(row)]
                try:
                    answers.append((row, assembled(row.request,
                                                   [page for page in pages if page])))
                except Incomplete:
                    self.error(RAW_MISSING)
            self.write(answers)

    def judge_window(self) -> str:
        """The status, over the requests of the run's own period, the history requests it
        sent, and those it left unsent, which are pending."""
        found = self.standing
        total = found["answered"] + found["failed"] + found["pending"]
        # A window request is a read: its outcome is never unknown.
        self.requests = {
            "answered": found["answered"], "failed": found["failed"],
            "pending": found["pending"],
        }
        self.counts["requests_planned"] = total
        self.counts["requests_failed"] = found["failed"]
        self.counts["requests_pending"] = found["pending"]
        if self.ended_on is not None:
            # A refusal of the whole run, such as a sign-in: whatever was answered first.
            return ERROR
        if found["answered"] == total:
            return SUCCESS
        if not self.errors:
            self.error(NOT_ANSWERED, total - found["answered"])
        return PARTIAL if found["answered"] else ERROR
