"""The source task workflow: recover, look, collect, plan, submit, store, replace."""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from brand_presence import client_ref, ledger, request_keys, run_record, status
from brand_presence.bigquery import Warehouse, Write, raw_prefix, repeated
from brand_presence.errors import UserError
from brand_presence.http import NothingTaken, OutcomeUnknown, VendorError, classify
from brand_presence.rows import LedgerRow, RawRow
from brand_presence.runs import Period, new_run_id, period_for
from brand_presence.settings import Settings
from brand_presence.sources.base import (
    ERROR,
    PARTIAL,
    SUCCESS,
    Answer,
    Outgoing,
    Parsed,
    Request,
    Result,
    SendContext,
    Source,
    TaskFailed,
    Verdict,
    keyed,
    planned_under,
    request_of,
    stored_request,
)
from brand_presence.state import locked

MARKS = 100
PARSE = status.PARSE
JUDGE = status.JUDGE
CLEAN_WRITE = status.CLEAN_WRITE
RAW_MISSING = status.RAW_MISSING
ROW_REPEATED = status.ROW_REPEATED
UNAVAILABLE_COUNT = "tasks_unavailable"

@dataclass
class World:
    bq: object
    http: httpx.Client
    keys: dict[str, str]
    clock: object = lambda: datetime.now(UTC)
    sleep: object = time.sleep
    status_out: object = print
    wait_seconds: int = 2400


def error_class_of(error):
    if isinstance(error, VendorError | httpx.HTTPError):
        return classify(error)[0]
    return type(error).__name__


class _Run:
    def __init__(self, source: Source, settings: Settings, world: World,
                 period: Period | None = None):
        self.source = source
        self.settings = settings
        self.world = world
        self.bq = world.bq
        self.clock = world.clock
        self.started_at = self.clock()
        self.period = period or period_for(self.started_at, settings.time_zone)
        self.run_id = new_run_id()
        self.row = run_record.Row(self.run_id, settings.client_id, source.name,
                                  self.started_at, period=self.period.name)
        self.status_lines = run_record.Lines(world.status_out)
        self.warehouse = Warehouse(self.bq, settings.state_path)
        self.ledger = ledger.Ledger(self.bq, source.ledger_model, self.clock)
        self.ref = None
        self._refs = {}
        self.sender = None
        self.counts = Counter()
        self.errors = Counter()
        self.statuses = {}
        self.source_counts = Counter()
        self.rejected = Counter()
        self.skipped = set()
        self.left_out = 0
        self.lines = []
        self.stored = []
        self.planned = []

    def error(self, name, count=1, status=None):
        self.errors[name] += count
        if status is not None:
            self.statuses[name] = status

    def note(self, counts):
        self.source_counts.update(counts)

    def ref_at(self, load_number):
        if load_number not in self._refs:
            self._refs[load_number] = client_ref.read_at(self.bq, load_number)
        return self._refs[load_number]

    def checks(self):
        active, abandoned = run_record.others(self.bq, self.run_id, self.started_at)
        run_record.close_abandoned(self.bq, abandoned, self.clock(), self.status_lines)
        if active:
            raise UserError("another run is in flight; wait for it to end or expire")
        self.ref = client_ref.apply(self.bq, self.settings, self.started_at)
        self._refs[self.ref.load_number] = self.ref
        self.sender = self.source.send(SendContext(
            self.world.keys, self.world.http, self.bq, self.started_at, self.world.sleep,
            outstanding=tuple(request_of(row.request)
                              for row in self.ledger.outstanding(self.period.name)),
        ))

    def go(self):
        run_record.start(self.bq, self.row)
        result = Result(ERROR, self.run_id, self.period.name)
        try:
            self.checks()
            self.tasks()
            result.status = self.judge()
        except Exception as error:
            self.error(error_class_of(error))
            self.lines.append(str(error) if isinstance(error, UserError)
                              else f"the run raised: {error_class_of(error)}")
        result.counts = {name: self.counts[name] for name in (
            "requests_planned", "requests_sent", "requests_failed", "requests_pending",
            "requests_unknown", "answers_usable", "answers_unusable", "raw_rows", "clean_rows",
        )}
        result.source_counts = dict(self.source_counts)
        result.lines = self.lines
        if self.errors:
            result.error_class, result.error_count = self.errors.most_common(1)[0]
        run_record.end(self.bq, self.row, result.status, self.clock(), self.status_lines,
                       load_number=self.ref.load_number if self.ref else None,
                       snapshot_id=self.ref.snapshot_id if self.ref else None,
                       error_class=result.error_class, error_count=result.error_count,
                       **result.counts)
        return result

    def tasks(self):
        self.ledger.orphaned(self.run_id)
        self.finish()
        self.look_for_unknown()
        self.collect_round()
        # Settle recovered history before deciding whether more history is needed.
        self.load_and_write()
        self.stored = []
        planned = self.source.plan(self.ref, self.period)
        requests = keyed([*planned, *self.sender.more(planned)], self.settings.client_id,
                          self.source.name, self.period.name)
        self.planned = list(requests)
        self.counts["requests_planned"] = len(requests)
        attempts = ledger.to_submit(self.planned, self.ledger.of_period(self.period.name))
        wanted = [(key, attempt, requests[key]) for key, attempt in attempts.items()]
        size = max(1, self.sender.batch_size)
        deadline = self.started_at + timedelta(seconds=self.world.wait_seconds)
        for at in range(0, len(wanted), size):
            if self.clock() > deadline:
                break
            self.submit(wanted[at:at + size])
        while True:
            waiting, asked = self.collect_round()
            if not waiting or not asked:
                break
            if self.clock() + timedelta(seconds=self.sender.poll_seconds) > deadline:
                break
            self.world.sleep(self.sender.poll_seconds)
        self.load_and_write()

    def judge(self):
        found = ledger.period_counts(self.planned, self.ledger.of_period(self.period.name))
        self.counts["requests_pending"] = found.pending
        self.counts["requests_unknown"] = found.unknown
        self.counts["requests_failed"] = found.failed
        if self.planned and found.answered == len(self.planned) and not self.errors:
            return SUCCESS
        return PARTIAL if found.answered else ERROR

    def submit(self, batch: list[tuple[str, int, Request]]) -> None:
        """One submission: the `sending` rows, the call, and what the vendor said, written
        before anything else is sent."""
        now = self.clock()
        rows = {
            request_keys.sent_key(key, attempt): self.source.ledger_model(
                request_key=key, period=self.period.name, attempt=attempt, vendor_task_id=None,
                state=ledger.SENDING, run_id=self.run_id, created_at=now, submitted_at=None,
                collected_at=None, stored_run_id=None, loaded_at=None, written_at=None,
                error_class=None, vendor_status=None,
                request=stored_request(request, self.ref.load_number, self.ref.snapshot_id),
            )
            for key, attempt, request in batch
        }
        requests = {request_keys.sent_key(key, attempt): request
                    for key, attempt, request in batch}
        self.ledger.add(list(rows.values()))
        outgoing = [Outgoing(sent, request.body) for sent, request in requests.items()]
        try:
            said = {item.key: item for item in self.sender.submit(outgoing)}
        except NothingTaken as error:
            # Never sent, or turned away by a rate limit: the vendor saw no request, so
            # this was no attempt. The rows go, and the next run sends the same attempt.
            self.error(error.error_class, len(rows), error.status_code)
            self.ledger.remove(list(rows.values()))
            return
        except VendorError as error:
            # No answer the run can trust leaves the requests `unknown`; a refusal of the
            # whole submission leaves them `rejected`. Neither is sent again by this run.
            self.counts["requests_sent"] += len(batch)
            unknown = isinstance(error, OutcomeUnknown)
            state = ledger.UNKNOWN if unknown else ledger.REJECTED
            self.error(error.error_class, len(rows), error.status_code)
            if not unknown:
                for request in requests.values():
                    self.reject(request, error.error_class, error.status_code)
            self.ledger.rewrite([
                ledger.changed(row, state=state, error_class=error.error_class,
                               vendor_status=error.status_code)
                for row in rows.values()
            ])
            return
        self.counts["requests_sent"] += len(batch)
        after = self.clock()
        changed = []
        for sent, row in rows.items():
            answer = said.get(sent)
            if answer is None:
                # The vendor answered and did not name it.
                changed.append(ledger.changed(row, state=ledger.UNKNOWN))
            elif answer.task_id:
                changed.append(ledger.changed(row, state=ledger.SUBMITTED,
                                              vendor_task_id=answer.task_id,
                                              submitted_at=after))
            else:
                name = answer.error_class or ledger.REJECTED
                self.error(name, status=answer.status_code)
                if name in self.source.unavailable:
                    # Not taken for now: no attempt used, and the request stays pending.
                    self.source_counts[UNAVAILABLE_COUNT] += 1
                else:
                    self.reject(requests[sent], name, answer.status_code)
                changed.append(ledger.changed(row, state=ledger.REJECTED, error_class=name,
                                              vendor_status=answer.status_code))
        self.ledger.rewrite(changed)


    def reject(self, request: Request, error_class: str, status: int | None) -> None:
        """Count a rejected request against each location it was asked for. The run gives
        one operational line for each, with the location's ID and no query value."""
        for location in dict.fromkeys(target.location_id for target in request.targets):
            self.rejected[(location, error_class, status)] += 1


    def look_for_unknown(self) -> None:
        """A ready-list miss leaves the request unknown, never eligible for payment."""
        rows = self.ledger.in_state(ledger.UNKNOWN)
        if rows:
            self.look(rows)

    def look(self, rows: list[LedgerRow]) -> bool:
        """One look, and what it found written to the ledger. False when the vendor gave
        no answer to it: its rows are still unknown."""
        sent = {request_keys.sent_key(row.request_key, row.attempt): row for row in rows}
        try:
            # Each as it was sent: the key, and the body its ledger row holds.
            found = self.sender.find([
                Outgoing(key, request_of(row.request).body) for key, row in sent.items()
            ])
        except VendorError as error:
            self.error(error.error_class, status=error.status_code)
            return False
        except Exception as error:  # noqa: BLE001 - still unknown, and the run goes on
            self.error(type(error).__name__)
            self.lines.append(f"the look for lost submissions raised: {type(error).__name__}")
            return False
        now = self.clock()
        self.ledger.rewrite([
            ledger.changed(row, state=ledger.SUBMITTED, vendor_task_id=found[key],
                           submitted_at=row.submitted_at or now, error_class=None)
            if found.get(key) else
            row
            for key, row in sent.items()
        ])
        lost = sum(1 for key in sent if not found.get(key))
        if lost:
            self.error(ledger.LOST, lost)
        return True


    def collect_round(self) -> tuple[int, int]:
        """Ask for every submitted task once, of whatever period. A finished one is stored
        raw and marked; one in progress stays `submitted`; one past the vendor's keeping
        time is `expired`. A task that raises anything else is counted under the error's
        type, left `submitted` and not asked again by this run. Returns how many are still
        waiting, and how many of those the vendor could be asked for."""
        waiting = asked = 0
        changed: list[LedgerRow] = []
        try:
            for row in self.ledger.in_state(ledger.SUBMITTED):
                saved = self.warehouse.read_raw(self.source.raw_model, self.raw_path(row))
                if saved is not None:
                    marked = ledger.changed(
                        row, state=ledger.COLLECTED if saved.usable else ledger.UNUSABLE,
                        collected_at=saved.pulled_at, stored_run_id=saved.run_id,
                    )
                    changed.append(marked)
                    self.stored.append((marked, saved, self.raw_path(row)))
                    continue
                now = self.clock()
                if ledger.is_expired(row, 30 * 24 * 60 * 60, now):
                    self.error(ledger.EXPIRED)
                    changed.append(ledger.changed(row, state=ledger.EXPIRED,
                                                  error_class=ledger.EXPIRED))
                    continue
                sent = request_keys.sent_key(row.request_key, row.attempt)
                if sent in self.skipped:
                    waiting += 1
                    continue
                try:
                    answer = self.sender.collect(row.vendor_task_id, sent)
                except TaskFailed as failed:
                    self.error(failed.error_class)
                    changed.append(ledger.changed(row, state=ledger.FAILED,
                                                  error_class=failed.error_class))
                    continue
                except VendorError as error:
                    self.error(error.error_class, status=error.status_code)
                    waiting += 1
                    continue
                except Exception as error:  # noqa: BLE001 - one task, not the run
                    self.unforeseen(sent, error, "asked for")
                    waiting += 1
                    continue
                asked += 1
                if answer is None:
                    waiting += 1
                    continue
                # Stored first. Only then is the task marked.
                try:
                    verdict, raw, path = self.store(row, answer)
                except Exception as error:  # noqa: BLE001 - one task, not the run
                    self.unforeseen(sent, error, "stored")
                    waiting += 1
                    continue
                marked = ledger.changed(
                    row, state=ledger.COLLECTED if verdict.usable else ledger.UNUSABLE,
                    collected_at=raw.pulled_at, stored_run_id=raw.run_id,
                    error_class=None if verdict.usable else verdict.rule,
                )
                changed.append(marked)
                self.stored.append((marked, raw, path))
                if len(changed) >= MARKS:
                    self.ledger.rewrite(changed)
                    changed = []
        finally:
            # Whatever ended the round, what it stored is marked.
            self.ledger.rewrite(changed)
        return waiting, asked


    def unforeseen(self, sent: str, error: Exception, doing: str) -> None:
        name = error_class_of(error)
        self.error(name)
        self.skipped.add(sent)
        self.lines.append(f"a task could not be {doing}: {name}; it stays submitted")


    def verdict_of(self, answer: Answer) -> Verdict:
        try:
            return self.source.usable(answer)
        except Exception:  # noqa: BLE001 - an answer that cannot be judged is no answer
            return Verdict(False, JUDGE)


    def count(self, answer: Answer, verdict: Verdict) -> None:
        """Count one answer, once it is stored."""
        if verdict.usable:
            self.counts["answers_usable"] += 1
        else:
            self.counts["answers_unusable"] += 1
            self.error(verdict.rule or ledger.UNUSABLE)
        self.note(answer.counts)


    def store(self, row: LedgerRow, answer: Answer) -> tuple[Verdict, RawRow, str]:
        """Write one answer to the bucket as it arrives, under this run's own path."""
        # Judged with what was asked beside it, which the ledger holds.
        verdict = self.verdict_of(replace(answer, asked=request_of(row.request).body))
        load_number, snapshot_id = planned_under(row.request)
        raw = self.source.raw_model(
            run_id=self.run_id, request_key=row.request_key, period=row.period,
            content_date=answer.content_date, pulled_at=self.clock(), request=row.request,
            response=json.dumps(answer.response, ensure_ascii=False), usable=verdict.usable,
            load_number=load_number, snapshot_id=snapshot_id,
        )
        path = self.warehouse.save_raw(
            self.source.name, raw, request_keys.object_name(row.request_key, row.attempt),
            storage_run_id=row.run_id,
        )
        self.count(answer, verdict)
        return verdict, raw, path


    def load_and_write(self) -> None:
        """Load what this run stored into the raw table, then write the clean rows of the
        usable answers, then record the written time."""
        if not self.stored:
            return
        self.counts["raw_rows"] += self.warehouse.load_raw(
            self.source.raw_model, [path for _, _, path in self.stored]
        )
        now = self.clock()
        loaded = [(ledger.changed(row, loaded_at=now), raw) for row, raw, _ in self.stored]
        self.ledger.rewrite([row for row, _ in loaded])
        self.write([(row, raw) for row, raw in loaded if raw.usable])


    def parsed(self, raw: RawRow) -> list | None:
        """The clean rows of a raw answer, or None when it cannot be parsed. What is said
        about a refusal is the row model, the field and the kind of error, never the
        value."""
        try:
            found = self.source.parse(raw, self.ref_at(raw.load_number))
            given = found if isinstance(found, Parsed) else Parsed(found)
            rows = list(given.rows)
        except ValidationError as refused:
            self.error(PARSE)
            self.refusal(refused, "an answer was not parsed")
            return None
        except Exception as error:  # noqa: BLE001 - named by its type alone
            self.error(PARSE)
            self.lines.append(f"an answer was not parsed: {type(error).__name__}")
            return None
        if any(type(row) not in self.source.clean_models for row in rows):
            self.error(PARSE)
            self.lines.append("an answer was not parsed: a row of a model the source does "
                              "not list")
            return None
        if repeated(self.source.clean_models, rows):
            self.error(ROW_REPEATED)
            return None
        self.note(given.counts)
        # Items the source left out: the rest of the answer is written.
        for refused in given.refused:
            self.error(PARSE)
            self.refusal(refused, "an item was left out")
        if given.refused:
            self.left_out += len(given.refused)
            self.source_counts["items_refused"] += len(given.refused)
        return rows


    def refusal(self, refused, what):
        self.lines.append(f"{what}: {len(refused.errors())} invalid field(s)")

    def write(self, answers: list[tuple[LedgerRow, RawRow]]) -> None:
        """Parse usable answers and replace their clean rows in one transaction. A failed
        write leaves the answers stored raw, to be finished by the next run."""
        if not answers:
            return
        periods = {row.period for row, _ in answers}
        known = [row for period in sorted(periods) for row in self.ledger.of_period(period)]
        # One answer for each request: that of its latest attempt. With `--again` an older
        # attempt may finish in the same run as a newer one, and its rows must not stay.
        latest: dict[str, LedgerRow] = {}
        for row, _ in answers:
            if row.request_key not in latest or row.attempt > latest[row.request_key].attempt:
                latest[row.request_key] = row
        writes, done, written = [], [], {}
        older: dict[str, list[LedgerRow]] = {}
        for row, raw in sorted(answers, key=lambda pair: pair[1].pulled_at):
            if ledger.superseded(row, known):
                done.append(row)
                continue
            if latest[row.request_key] is not row:
                older.setdefault(row.request_key, []).append(row)
                continue
            rows = self.parsed(raw)
            if rows is None:
                continue
            writes.append(Write(raw.request_key, raw.pulled_at, rows))
            written[raw.request_key] = request_of(row.request)
            done.append(row)
        # An older attempt is done with once the newer one's rows are written.
        done += [row for key, rows in older.items() if key in written for row in rows]
        try:
            self.counts["clean_rows"] += self.warehouse.replace(self.source.clean_models, writes)
        except Exception as error:  # noqa: BLE001 - the answers stay raw
            self.error(CLEAN_WRITE)
            self.lines.append(f"the clean rows were not written: {type(error).__name__}")
            return
        if written and self.sender is not None:
            try:
                self.sender.written(written)
            except Exception as error:  # noqa: BLE001 - the rows are written all the same
                self.error(type(error).__name__)
                self.lines.append("the source's record of what was written was not made: "
                                  f"{type(error).__name__}")
                return
        now = self.clock()
        self.ledger.rewrite([ledger.changed(row, written_at=now) for row in done])


    def raw_path(self, row: LedgerRow) -> str | None:
        """Where the raw answer of a collected task is: under the run that stored it."""
        return raw_prefix(self.source.name, row.period, row.run_id) + (
            request_keys.object_name(row.request_key, row.attempt)
        )


    def finish(self) -> None:
        """Finish what earlier runs left: stored answers not yet loaded are loaded, and
        usable answers not yet written clean are parsed and written. Nothing is submitted
        and no vendor is asked. Each object is found by the path its ledger row gives, so
        nothing is listed."""
        raw_model = self.source.raw_model
        waiting = self.ledger.not_loaded()
        for period in sorted({row.period for row in waiting}):
            loaded = self.warehouse.loaded(raw_model, period)
            to_load, done = [], []
            for row in (r for r in waiting if r.period == period):
                path = self.raw_path(row)
                if (row.stored_run_id, row.request_key) in loaded:
                    done.append(row)
                elif path is None or not self.warehouse.has_raw(path):
                    self.error(RAW_MISSING)
                else:
                    to_load.append(path)
                    done.append(row)
            self.counts["raw_rows"] += self.warehouse.load_raw(raw_model, sorted(set(to_load)))
            now = self.clock()
            self.ledger.rewrite([ledger.changed(row, loaded_at=now) for row in done])
        unwritten = self.ledger.not_written()
        # Period by period, so that a write that fails holds up its own period alone.
        for period in sorted({row.period for row in unwritten}):
            answers = []
            for row in (r for r in unwritten if r.period == period):
                path = self.raw_path(row)
                raw = None if path is None else self.warehouse.read_raw(raw_model, path)
                if raw is None:
                    self.error(RAW_MISSING)
                else:
                    answers.append((row, raw))
            self.write(answers)



def run(source: Source, settings: Settings, world: World, *, period: Period | None = None):
    with locked(settings.state_path):
        return _Run(source, settings, world, period).go()
