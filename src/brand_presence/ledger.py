"""Durable task states. A submission is recorded before the vendor is called."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from google.cloud import bigquery

from brand_presence import status
from brand_presence.bigquery import (
    query,
    read,
    row_batches,
    rows_parameter,
    text_parameter,
    texts_parameter,
)
from brand_presence.rows import LedgerRow

SENDING = "sending"



SUBMITTED = "submitted"



REJECTED = status.REJECTED



UNKNOWN = "unknown"



COLLECTED = "collected"



UNUSABLE = status.UNUSABLE



FAILED = "failed"



EXPIRED = status.EXPIRED



IN_FLIGHT = (SENDING, SUBMITTED, UNKNOWN)



ATTEMPTS = 3



LOST = status.LOST



@dataclass(frozen=True)
class PeriodCounts:
    """Where the planned requests of a period stand."""

    answered: int = 0
    pending: int = 0
    unknown: int = 0
    failed: int = 0



class Ledger:
    """One task source's ledger."""

    def __init__(self, bq, model: type[LedgerRow], clock: Callable[[], datetime]):
        self.bq = bq
        self.model = model
        self.table = model.table
        self.clock = clock

    # Reading ----------------------------------------------------------------------------

    def of_period(self, period: str) -> list[LedgerRow]:
        return read(self.bq, self.model, "period = @period", [text_parameter("period", period)])

    def of_periods(self, periods: Sequence[str]) -> dict[str, list[LedgerRow]]:
        """The rows of several periods in one read, by period. A period with no row is
        there with none."""
        found: dict[str, list[LedgerRow]] = {period: [] for period in periods}
        if periods:
            for row in read(self.bq, self.model, "period IN UNNEST(@periods)",
                            [texts_parameter("periods", list(periods))]):
                found[row.period].append(row)
        return found

    def in_state(self, state: str) -> list[LedgerRow]:
        """Every row in a state, of whatever period, oldest first."""
        rows = read(self.bq, self.model, "state = @state", [text_parameter("state", state)])
        return sorted(rows, key=lambda row: (row.created_at, row.request_key, row.attempt))

    def not_loaded(self) -> list[LedgerRow]:
        """Stored answers that are not in the raw table yet."""
        return read(self.bq, self.model, "state IN UNNEST(@states) AND loaded_at IS NULL",
                    [texts_parameter("states", [COLLECTED, UNUSABLE])])

    def not_written(self) -> list[LedgerRow]:
        """Usable answers, loaded, whose clean rows are not written yet."""
        return read(
            self.bq, self.model,
            "state = @state AND loaded_at IS NOT NULL AND written_at IS NULL",
            [text_parameter("state", COLLECTED)],
        )

    def outstanding(self, period: str, keeping_seconds: int | None = None) -> list[LedgerRow]:
        """Work from another period that still reserves its history pairs. Unknown and
        expired attempts release their reservations after the vendor's retention time."""
        rows = read(
            self.bq, self.model,
            "period != @period AND state IN UNNEST(@states) AND written_at IS NULL",
            [text_parameter("period", period),
             texts_parameter("states", [*IN_FLIGHT, COLLECTED, EXPIRED])],
        )
        now = self.clock()
        return [row for row in rows if row.state not in (UNKNOWN, EXPIRED)
                or not is_expired(row, keeping_seconds, now)]

    # Writing ----------------------------------------------------------------------------

    def add(self, rows: Sequence[LedgerRow]) -> None:
        """Insert rows: the `sending` rows of a submission, before it is sent."""
        columns = ", ".join(self.model.model_fields)
        for batch in row_batches(self.model, rows):
            query(
                self.bq,
                f"INSERT INTO {self.table} ({columns}) SELECT {columns} FROM UNNEST(@rows)",
                [rows_parameter("rows", self.model, batch)],
            )

    def _delete(self, rows: Sequence[LedgerRow]) -> tuple[list[str], list]:
        """The statements and the parameters that delete rows by their request key, period
        and attempt."""
        groups: dict[tuple[str, int], list[str]] = {}
        for row in rows:
            groups.setdefault((row.period, row.attempt), []).append(row.request_key)
        statements: list[str] = []
        parameters: list = []
        for number, ((period, attempt), keys) in enumerate(sorted(groups.items())):
            statements.append(
                f"DELETE FROM {self.table} WHERE period = @period_{number} "
                f"AND attempt = @attempt_{number} AND request_key IN UNNEST(@keys_{number})"
            )
            parameters += [
                text_parameter(f"period_{number}", period),
                bigquery.ScalarQueryParameter(f"attempt_{number}", "INT64", attempt),
                texts_parameter(f"keys_{number}", sorted(keys)),
            ]
        return statements, parameters

    def rewrite(self, rows: Sequence[LedgerRow]) -> None:
        """Put each row in place of the one with its request key, period and attempt. Each
        batch of rows that fits in one request is one transaction."""
        columns = ", ".join(self.model.model_fields)
        for batch in row_batches(self.model, rows):
            statements, parameters = self._delete(batch)
            statements.append(
                f"INSERT INTO {self.table} ({columns}) SELECT {columns} FROM UNNEST(@rows)"
            )
            parameters.append(rows_parameter("rows", self.model, batch))
            script = ["BEGIN TRANSACTION", *statements, "COMMIT TRANSACTION"]
            query(self.bq, ";\n".join(script) + ";", parameters)

    def remove(self, rows: Sequence[LedgerRow]) -> None:
        """Take rows out: the `sending` rows of a submission the vendor took nothing of.
        They were no attempt."""
        for batch in row_batches(self.model, rows):
            statements, parameters = self._delete(batch)
            script = ["BEGIN TRANSACTION", *statements, "COMMIT TRANSACTION"]
            query(self.bq, ";\n".join(script) + ";", parameters)

    def orphaned(self, run_id: str) -> int:
        """Rows an earlier run left as `sending`: its submission may have arrived. They are
        `unknown` now, and are looked for before anything is submitted. Called once the
        run holds the lock, so no other run of the source is sending."""
        left = [row for row in self.in_state(SENDING) if row.run_id != run_id]
        self.rewrite([row.model_copy(update={"state": UNKNOWN}) for row in left])
        return len(left)



def changed(row: LedgerRow, **values: object) -> LedgerRow:
    return row.model_copy(update=values)



def is_expired(row: LedgerRow, keeping_seconds: int | None, now: datetime) -> bool:
    """Whether a submitted task is past the vendor's keeping time."""
    if keeping_seconds is None:
        return False
    since = row.submitted_at or row.created_at
    return since + timedelta(seconds=keeping_seconds) < now



def _by_request(rows: Sequence[LedgerRow]) -> dict[str, list[LedgerRow]]:
    found: dict[str, list[LedgerRow]] = {}
    for row in sorted(rows, key=lambda row: row.attempt):
        found.setdefault(row.request_key, []).append(row)
    return found



def is_unavailable(row: LedgerRow, unavailable: Sequence[str]) -> bool:
    """Whether an attempt was rejected with a class its source names as the vendor being
    unavailable for it. It used no attempt, and its request is pending."""
    return row.state == REJECTED and row.error_class in unavailable



def to_submit(planned: Sequence[str], rows: Sequence[LedgerRow], *,
              again: bool = False, unavailable: Sequence[str] = ()) -> dict[str, int]:
    """Which planned requests a run submits, each with its attempt's number.

    A request that is with the vendor, or may be, or that has a usable answer, is not
    submitted. Expired paid tasks are not retried. Rejected, failed or unusable tasks get up to
    three attempts; an attempt rejected as unavailable keeps its number and is not one of
    the three. With `again`, every request is, as a new attempt."""
    attempts = _by_request(rows)
    found: dict[str, int] = {}
    for key in planned:
        made = attempts.get(key, [])
        number = (made[-1].attempt + 1) if made else 1
        if again:
            found[key] = number
            continue
        states = {row.state for row in made}
        if states & {*IN_FLIGHT, COLLECTED, EXPIRED}:
            continue
        if sum(1 for row in made if not is_unavailable(row, unavailable)) >= ATTEMPTS:
            continue
        found[key] = number
    return found



def is_answered(key: str, rows: Sequence[LedgerRow]) -> bool:
    """Whether a request has a usable answer whose clean rows are written, by any run."""
    return any(row.request_key == key and row.state == COLLECTED
               and row.written_at is not None for row in rows)



def is_stored(key: str, rows: Sequence[LedgerRow]) -> bool:
    """Whether a request has a usable answer stored, written clean or not."""
    return any(row.request_key == key and row.state == COLLECTED for row in rows)



def period_counts(planned: Sequence[str], rows: Sequence[LedgerRow], *,
                  unavailable: Sequence[str] = ()) -> PeriodCounts:
    """Where the planned requests of a period stand when a run ends. A request is answered
    when an attempt at it is usable and its clean rows are written, by any run. One whose
    last attempt was rejected as unavailable, or that was never sent, is pending: the
    next run sends it again."""
    attempts = _by_request(rows)
    answered = pending = unknown = failed = 0
    for key in planned:
        made = attempts.get(key, [])
        if any(row.state == COLLECTED and row.written_at is not None for row in made):
            answered += 1
        elif not made or (made[-1].state in (SENDING, SUBMITTED)
                          or is_unavailable(made[-1], unavailable)):
            pending += 1
        elif made and made[-1].state == UNKNOWN:
            unknown += 1
        else:
            failed += 1
    return PeriodCounts(answered, pending, unknown, failed)



def superseded(row: LedgerRow, rows: Sequence[LedgerRow]) -> bool:
    """Whether a later attempt at the same request already has its clean rows written. An
    older answer that arrives after it must not take its place."""
    return any(
        other.request_key == row.request_key and other.period == row.period
        and other.attempt > row.attempt and other.written_at is not None
        for other in rows
    )
