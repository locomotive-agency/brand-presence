"""A recording stand-in for the BigQuery client.

It takes the calls the pipeline makes on `google.cloud.bigquery.Client` and keeps a small
table store, so a test can read back what a run record, a ledger or a `ref` table holds. It
runs the statements tests/fakes/sql.py lists and no other. Nothing here reaches Google Cloud.

Named calls, for `refuse` and `fail`:

    query        one call of `query`; the subject is the whole text it was given, and the
                 record keeps its parameters
    statement    one statement of that text, as it is about to run; the subject is its text
    load         a load job; the subject is the table, as <dataset>.<table>, and the
                 record keeps the job's format and its write and create settings
    get_table    reading a table's columns and options; the subject is the table
    update_table changing a table's options; the subject is the table
    get_dataset  reading a dataset's description; the subject is <project>.<dataset>
    list_tables  listing a dataset's tables; the subject is <project>.<dataset>

A dataset of another project, such as a client's GA4 BigQuery export, is set up with
`add_dataset`: its location and the names of its tables, and no rows. Only `get_dataset` and
`list_tables` read it; a statement that reads it is answered with `set_result`. The record
of each `query` keeps the location the job was asked to run in, None where none was given.

The real client's `query` only starts a job: the work is done some time later, and what
went wrong is raised by the job's `result()`. The stand-in does the work at once, so it
insists on the wait instead. A caller that makes its next call before it asked for the
`result()` of its last job gets `NotWaited`, and a job still not waited for when the test
ends fails the test. A dry run changes nothing, raises from `query` itself and needs no
wait. A script that fails inside a transaction leaves no row of it behind.

A request holds 10 MB at most, the statement and its parameters together, as BigQuery's
does. A larger one is refused by `query` itself, before any job exists. `request_limit`
is the number, and a test may lower it.

A session is made by a query whose settings ask for one, and named by the next queries in
their connection properties. A transaction begun in a session stays open from one query
to the next, until it is committed or rolled back or the session is ended with
`CALL BQ.ABORT_SESSION()`. A statement that fails leaves the transaction open: the caller
rolls back. The stand-in keeps one view of each table, so while a session holds a
transaction open a query outside it is `NotUnderstood`, and a transaction still open when
the test ends fails the test.

A table is described as the API describes it, not as SQL names it: INT64 is INTEGER,
FLOAT64 is FLOAT, BOOL is BOOLEAN, a STRUCT is a RECORD with fields, and an array is its
element's type with the mode REPEATED.

A table has a last changed time, which the description gives as the API does, so the client
reads it as the table's `modified`. Making the table sets it, and so does every statement,
load or call that adds, changes or deletes a row of it or changes its columns or options,
at the stand-in's clock. A statement that touches no row leaves it, and a transaction rolled
back puts it back. A test sets it with `set_last_changed_time`, as an outside party that
writes to the table would have moved it.

A test can set the result of a statement it names, with `set_result`: the rows it returns,
or the count of rows it changed. The statement is recorded as any other, and nothing else
is run for it.

Each transaction is recorded, with `transactions()`: the statements that ran inside it and
how it ended, by a commit, a rollback, or an abort when its script failed or its session
ended. Its rows are put back on a rollback and an abort, unless the test said with
`keep_rows_on_rollback` that they stay, as on a project where the rollback did not work.

A test gives what a statement reads with `set_dry_run`: its columns and their types, and
its bytes. A dry run of it gives both, as the job's `schema` and `total_bytes_processed`,
and is not run. A run of it is billed those bytes, and a job whose `maximum_bytes_billed` is
lower fails, as BigQuery's does. The record of each `query` keeps the job's limit beside its
parameters.

A test can also time a named statement out with `time_out`; refuse a transaction with
`refuse_transaction`, as BigQuery refuses one whose table another is writing; and run a
step of its own between two named statements with `between`.

`NotUnderstood` and `NotWaited` are no `Exception` (see `Misuse` in tests/fakes/journal.py).
A statement the stand-in could not read is recorded with the outcome `not understood`.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from google.api_core.exceptions import (
    BadRequest,
    Conflict,
    Forbidden,
    InternalServerError,
    NotFound,
)
from google.cloud import bigquery

from brand_presence.example._offline.fakes.journal import (
    FAILED,
    NOT_UNDERSTOOD,
    REFUSED,
    Journal,
    Misuse,
    StandIn,
    at_the_end,
)
from brand_presence.example._offline.fakes.sql import (
    API_NAMES,
    Engine,
    FakeTable,
    Kind,
    NotUnderstood,
    Outcome,
    parameter_kind,
    parse_type,
    split,
    table_name,
)

_DAY_MS = 24 * 60 * 60 * 1000
# The most one request holds, in bytes: BigQuery's limit for a query job's request.
REQUEST_LIMIT = 10 * 1024 * 1024


class Row(dict):
    """A row of a result. Read as a mapping or by attribute, as the real client's rows are."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class NotWaited(Misuse):
    """A caller went on without asking for the `result()` of its last job. On BigQuery that
    job may not have run yet, or may have failed unseen."""


# The table a DDL statement names, which BigQuery gives as the job's destination.
DESTINATION = re.compile(
    r"\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)(?:\s+IF\s+NOT\s+EXISTS)?|"
    r"ALTER\s+TABLE(?:\s+IF\s+EXISTS)?)\s+`?([A-Za-z0-9_.-]+)`?",
    re.IGNORECASE,
)


class SessionInfo:
    def __init__(self, session_id: str):
        self.session_id = session_id


class FakeJob:
    """What `query` and the load calls return."""

    def __init__(self, *, rows=None, affected=None, error=None, dry_run=False, output_rows=None,
                 subject: str = "", session: str | None = None, bytes_processed: int = 0,
                 schema: list[bigquery.SchemaField] | None = None):
        self.session_info = SessionInfo(session) if session else None
        self._rows = rows or []
        self._error = error
        self.num_dml_affected_rows = affected
        self.dry_run = dry_run
        self.output_rows = output_rows
        self.total_bytes_processed = bytes_processed
        # A dry run bills nothing. The real client gives None for it.
        self.total_bytes_billed = None if dry_run else bytes_processed
        # The columns a dry run found, where a test gave them; None otherwise.
        self.schema = schema
        self.subject = subject
        # A dry run is over when `query` returns.
        self.waited = dry_run
        # Set where asking for the results would be refused.
        self.results_refused: BaseException | None = None

    def result(self, *args, **kwargs) -> list[Row]:
        self.waited = True
        if self._error is not None:
            raise self._error
        if self.results_refused is not None:
            # The client asks for the results, and the caller may not read the rows of
            # the statement's destination (FakeBigQuery.rows_readable_only).
            raise self.results_refused
        return [Row(row) for row in self._rows]

    # The job's own state, as jobs.get gives it. The stand-in's job is over when `query`
    # returns; looking at it counts as waiting for it.
    def done(self, *args, **kwargs) -> bool:
        self.waited = True
        return True

    @property
    def error_result(self) -> dict | None:
        return None if self._error is None else {"reason": type(self._error).__name__}

    def exception(self, *args, **kwargs) -> BaseException | None:
        self.waited = True
        return self._error

    def __iter__(self):
        return iter(self.result())


# How a transaction ended.
COMMIT = "commit"
ROLLBACK = "rollback"
# Its script failed, or its session was ended, before a commit or a rollback.
ABORT = "abort"


@dataclass
class Transaction:
    """One transaction, from its BEGIN: the statements that ran or were tried inside it, in
    order, and how it ended. `ended` is None while it is open."""

    session: str | None
    statements: list[str] = field(default_factory=list)
    ended: str | None = None


@dataclass
class _Shape:
    pattern: re.Pattern
    columns: dict[str, str] | None
    bytes: int


@dataclass
class _Between:
    first: re.Pattern
    second: re.Pattern
    step: Callable[[], object]
    armed: bool = False


@dataclass
class _Tally:
    """What one query's statements read, and the columns of the last a test described."""

    bytes: int = 0
    schema: list[bigquery.SchemaField] | None = None


def _snapshot(rows: list[dict]) -> list[dict]:
    """The rows of a table as a transaction found them, to put back on a rollback. Each row
    is copied and its values are not: no statement changes a value inside a row. An UPDATE
    puts a copy in its place, and a read is given copies (tests/fakes/sql.py). A deep copy
    of every table at each BEGIN made the suite many times slower."""
    return [row.copy() for row in rows]


def _job_error(kind: type[Exception], reason: str, message: str) -> Exception:
    """An error as the client raises it for a failed job: its reason beside its message."""
    return kind(f"stand-in: {message}", errors=[{"reason": reason, "message": message}])


def _parameters(sent: dict) -> tuple[dict, dict[str, Kind]]:
    """The query parameters of a job's settings, by name: each one's value, and the type
    it was declared with. Both are read from what the client sends to the API, where every
    value travels as text beside its type, so a value that does not fit its type is seen
    here as BigQuery would see it. `sent` is the query part of the settings (`_sent`)."""
    values = {}
    kinds = {}
    for parameter in sent.get("queryParameters") or []:
        name = parameter.get("name")
        kinds[name] = parameter_kind(parameter["parameterType"])
        values[name] = _parameter_value(kinds[name], parameter.get("parameterValue") or {})
    return values, kinds


def _parameter_value(kind: Kind, sent: dict) -> object:
    """A parameter's value, from its text. Text that is no value of the type is kept as
    it is, and refused when the statement uses it."""
    if kind.repeated:
        return [_parameter_value(kind.element, item) for item in sent.get("arrayValues") or []]
    if kind.base == "STRUCT":
        given = sent.get("structValues") or {}
        return {name: _parameter_value(inner, given.get(name) or {})
                for name, inner in kind.fields}
    text = sent.get("value")
    if not isinstance(text, str):
        return text
    try:
        if kind.base == "INT64":
            return int(text)
        if kind.base == "FLOAT64":
            return float(text)
        if kind.base in ("NUMERIC", "BIGNUMERIC"):
            return Decimal(text)
        if kind.base == "BOOL":
            return {"true": True, "false": False}.get(text.lower(), text)
        if kind.base == "TIMESTAMP":
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        if kind.base == "DATE":
            return date.fromisoformat(text)
    except (ArithmeticError, ValueError):
        pass
    return text


def _sent(job_config) -> dict:
    """The query part of a job's settings, as the client sends it."""
    return job_config.to_api_repr().get("query", {}) if job_config is not None else {}


def _session_of(sent: dict) -> str | None:
    """The session a query names in its connection properties, from `_sent`."""
    for found in sent.get("connectionProperties") or []:
        if found.get("key") == "session_id":
            return found.get("value")
    return None


def _load_settings(job_config) -> dict:
    """What a load job was asked to do, for the record. None is a setting left unset."""
    return {
        "source_format": getattr(job_config, "source_format", None),
        "write_disposition": getattr(job_config, "write_disposition", None),
        "create_disposition": getattr(job_config, "create_disposition", None),
        "schema_update_options": list(getattr(job_config, "schema_update_options", None) or []),
    }


class FakeBigQuery(StandIn):
    service = "bigquery"
    calls = ("query", "statement", "load", "get_table", "update_table", "get_dataset",
             "list_tables")

    def __init__(self, journal: Journal | None = None, *, project: str = "stand-in-project",
                 location: str = "EU",
                 clock: Callable[[], datetime] | None = None):
        super().__init__(journal)
        self.project = project
        self.location = location
        self.clock = clock or (lambda: datetime.now(UTC))
        self.tables: dict[str, FakeTable] = {}
        self._engine = Engine(self.tables, lambda: self.clock())
        self._handlers: list[tuple] = []
        self._stepper: Stepper | None = None
        self._lock = threading.RLock()
        # Each caller's last job, by thread, until it is seen to have been waited for.
        self._last_job: dict[int, FakeJob] = {}
        self.request_limit = REQUEST_LIMIT
        # Each open session, with the rows as they were when its transaction began, or
        # None while it has no transaction open.
        self._sessions: dict[str, dict[str, tuple[list[dict], datetime | None]] | None] = {}
        self._session_numbers = itertools.count(1)
        # Every transaction begun, and the one open in each session.
        self._transactions: list[Transaction] = []
        self._session_transactions: dict[str, Transaction] = {}
        self._shapes: list[_Shape] = []
        self._betweens: list[_Between] = []
        # Statements refused inside a transaction: the pattern and how many more times.
        self._refusals: list[list] = []
        # Tables whose rows a rollback leaves, by pattern.
        self._kept: list[re.Pattern] = []
        # The tables whose rows the caller may read, or None for every table.
        self._readable: set[str] | None = None
        # Datasets of other projects, by <project>.<dataset>: their location and tables.
        self._datasets: dict[str, tuple[str, list[str]]] = {}
        at_the_end(self._never_waited_for)
        at_the_end(self._left_open)

    def _refusal(self) -> BaseException:
        return Forbidden("stand-in: refused")

    def rows_readable_only(self, tables) -> None:
        """Let the caller read the rows of these tables alone, as a job account that holds
        the table data role on them and the schema role elsewhere. BigQuery answers a
        request for a statement's results only where the caller may read the rows of the
        job's destination, which for CREATE TABLE, CREATE VIEW and ALTER TABLE is the table
        made or changed. The statement itself still works."""
        self._readable = {table_name(table) for table in tables}

    def _results_refused(self, query: str) -> BaseException | None:
        if self._readable is None:
            return None
        made = DESTINATION.match(query)
        if made and table_name(made.group(1)) not in self._readable:
            return Forbidden(f"stand-in: no right to read the rows of {made.group(1)}")
        return None

    def _failure(self) -> BaseException:
        return InternalServerError("stand-in: failed")

    # What a test sets up and reads back. None of it is recorded. -----------------------

    def create_table(self, name: str, columns: dict[str, str], *,
                     partition_column: str | None = None,
                     partition_expiration_days: float | None = None,
                     required: tuple[str, ...] = ()) -> FakeTable:
        """Put a table in the store, as an applied migration would have."""
        kinds = {column: parse_type(kind).text() for column, kind in columns.items()}
        table = FakeTable(table_name(name), kinds, set(required), partition_column)
        if partition_expiration_days is not None:
            table.options["partition_expiration_days"] = partition_expiration_days
        table.last_changed_time = self.clock()
        self.tables[table.name] = table
        return table

    def create_dataset(self, dataset, exists_ok=False):
        identity = f"{dataset.project}.{dataset.dataset_id}"
        if identity not in self._datasets:
            self.add_dataset(identity, location=dataset.location)
        found = bigquery.Dataset(identity)
        found.location = self._datasets[identity][0]
        return found

    def add_dataset(self, dataset_id: str, *, location: str,
                    tables: list[str] | tuple[str, ...] = ()) -> None:
        """Make a dataset another project holds, with its location and its tables' names."""
        self._datasets[dataset_id] = (location, list(tables))

    def put(self, name: str, rows: list[dict]) -> None:
        """Add rows to a table, as an earlier run would have left them."""
        table = self._engine.data_table(table_name(name))
        checked = [self._engine.check_row(table, row) for row in rows]
        table.rows.extend(checked)
        self._engine.changed(table, len(checked))

    def rows(self, name: str) -> list[dict]:
        """A copy of a table's rows, in the order they were written."""
        return copy.deepcopy(self._engine.data_table(table_name(name)).rows)

    def last_changed_time(self, name: str) -> datetime | None:
        """When a table was last changed, as its description gives it."""
        return self._engine.table(table_name(name)).last_changed_time

    def set_last_changed_time(self, name: str, when: datetime) -> None:
        """Move a table's last changed time, as a party outside the pipeline that writes to
        it would. Its rows are left as they are."""
        if when.tzinfo is None:
            raise ValueError("a last changed time is a time with its zone")
        self._engine.table(table_name(name)).last_changed_time = when

    def set_result(self, match: str, rows: list[dict] | None = None, *,
                   affected: int | None = None) -> None:
        """Answer every statement in which the pattern is found with the rows given, or
        with a count of rows changed, and run nothing for it. A result set later for the
        same statement is the one given."""
        if (rows is None) == (affected is None):
            raise ValueError("a result is rows or a count of rows changed, and not both")
        pattern = re.compile(match)

        def answer(stand_in, text, values):
            return copy.deepcopy(rows) if affected is None else affected

        # Before every handler set earlier, so the latest result is the one found first.
        self._handlers.insert(0, (pattern, answer))

    def on(self, match: str, handler: Callable) -> None:
        """Answer statements in which the pattern is found with `handler(self, text,
        parameters)`, in place of the stand-in's own reading. It returns rows, a count of
        rows changed, or None."""
        self._handlers.append((re.compile(match), handler))

    def set_dry_run(self, match: str, columns: dict[str, str] | None = None, *,
                    bytes_read: int = 0) -> None:
        """Say what a statement in which the pattern is found reads: its columns, by name
        with their SQL types, and its bytes. A dry run of it gives both and runs nothing. A
        run of it is billed the bytes, and fails when they are more than the job's limit."""
        if bytes_read < 0:
            raise ValueError("a statement cannot read fewer than no bytes")
        given = None if columns is None else dict(columns)
        # Before every one set earlier, so the latest is the one found first.
        self._shapes.insert(0, _Shape(re.compile(match), given, bytes_read))

    def time_out(self, match: str, *, times: int | None = None) -> None:
        """Make a statement in which the pattern is found run past its job's time limit, so
        the job fails as BigQuery stops it. `times` as for `fail`."""
        self.fail("statement", match, times=times, error=lambda: _job_error(
            InternalServerError, "timeout", "Job execution was cancelled: Job timed out"
        ))

    def refuse_transaction(self, match: str, *, times: int | None = None) -> None:
        """Refuse a statement in which the pattern is found when it runs inside a
        transaction, as BigQuery refuses one whose table another transaction is writing.
        `times` is how many are refused; None means every one. The transaction is left to
        end as any whose statement failed."""
        self._refusals.append([re.compile(match), times])

    def between(self, first: str, second: str, step: Callable[[], object]) -> None:
        """Call `step` once, after a statement in which `first` is found has run and before
        the next one in which `second` is found runs. The step may make calls of its own,
        and waits for each. Dry runs do not count."""
        self._betweens.append(_Between(re.compile(first), re.compile(second), step))

    def keep_rows_on_rollback(self, match: str | None = None) -> None:
        """Leave the rows of the tables whose name holds the pattern, every table for None,
        when a transaction is rolled back or aborted."""
        self._kept.append(re.compile(match or ""))

    def transactions(self) -> list[Transaction]:
        """Every transaction begun, in order. Copies: what a test does with them changes
        nothing here."""
        with self._lock:
            return copy.deepcopy(self._transactions)

    def statements(self, match: str | None = None) -> list[str]:
        """Every statement that ran or was tried, one by one, in order. Dry runs apart."""
        return [c.subject for c in self.journal.of(self.service, "statement", match)
                if not c.detail.get("dry_run")]

    def dry_runs(self, match: str | None = None) -> list[str]:
        return [c.subject for c in self.journal.of(self.service, "statement", match)
                if c.detail.get("dry_run")]

    def loads(self, match: str | None = None) -> list:
        return self.journal.of(self.service, "load", match)

    def stepper(self, match: str | None = None, timeout: float = 5.0) -> Stepper:
        """Run callers one BigQuery call at a time (see `Stepper`)."""
        self._stepper = Stepper(match, timeout)
        return self._stepper

    # Waiting for a job --------------------------------------------------------------------

    def _waited_for_the_last(self) -> None:
        """Called at the start of each call: the caller's last job must have been waited
        for."""
        job = self._last_job.pop(threading.get_ident(), None)
        if job is not None and not job.waited:
            raise NotWaited(
                f"result() was never asked for, and the caller went on: {job.subject}"
            )

    def _started(self, job: FakeJob) -> FakeJob:
        self._last_job[threading.get_ident()] = job
        return job

    def _never_waited_for(self) -> list[str]:
        return [f"NotWaited: result() was never asked for: {job.subject}"
                for job in self._last_job.values() if not job.waited]

    def _left_open(self) -> list[str]:
        return [f"a transaction was left open in {name}: the tables show rows nobody "
                "committed" for name, saved in self._sessions.items() if saved is not None]

    # The client's calls -----------------------------------------------------------------

    def query(self, query: str, job_config=None, location: str | None = None,
              **_) -> FakeJob:
        dry = bool(getattr(job_config, "dry_run", False))
        self._waited_for_the_last()
        self._gate(query)
        with self._lock:
            start = len(self.journal.calls)
            try:
                # Read once: the client makes a deep copy of its settings each time.
                sent = _sent(job_config)
                given, kinds = _parameters(sent)
                limit = getattr(job_config, "maximum_bytes_billed", None)
                # The client holds the time limit as text, as the API sends it.
                timeout = getattr(job_config, "job_timeout_ms", None)
                error = self._check("query", query, dry_run=dry, parameters=given,
                                    maximum_bytes_billed=limit, location=location,
                                    job_timeout_ms=None if timeout is None else int(timeout))
                size = len(query.encode("utf-8")) + len(json.dumps(sent))
                if error is None and size > self.request_limit:
                    # The API refuses the request itself: no job is made.
                    self.journal.remark(start, self.service, "query", FAILED)
                    raise BadRequest("stand-in: the request is larger than a request may be")
                session = _session_of(sent)
                if error is None and sent.get("createSession"):
                    session = f"session-{next(self._session_numbers)}"
                    self._sessions[session] = None
                elif error is None and session is not None and session not in self._sessions:
                    error = BadRequest("stand-in: no such session")
                rows: list[dict] = []
                affected = None
                tally = _Tally()
                if error is None:
                    open_in = [name for name, saved in self._sessions.items()
                               if saved is not None and name != session]
                    if open_in and not dry:
                        raise NotUnderstood(
                            f"{open_in[0]} holds a transaction open, and the stand-in keeps "
                            f"one view of each table: {query}"
                        )
                    try:
                        last = self._script(query, given, kinds, dry, session,
                                            limit=limit, tally=tally)
                        rows, affected = last.rows or [], last.affected
                    except Exception as exc:  # noqa: BLE001 - handed to result()
                        error = exc
            except NotUnderstood:
                # Not `ok`: the statement was not run, and not `failed` either.
                self.journal.remark(start, self.service, "statement", NOT_UNDERSTOOD)
                self.journal.remark(start, self.service, "query", NOT_UNDERSTOOD)
                raise
        if dry and error is not None:
            raise error
        job = FakeJob(
            rows=rows, affected=affected, error=error, dry_run=dry, subject=query,
            session=session if session in self._sessions else None,
            bytes_processed=tally.bytes, schema=tally.schema,
        )
        if error is None and not dry:
            job.results_refused = self._results_refused(query)
        return self._started(job)

    def _script(self, text: str, params: dict, kinds: dict[str, Kind], dry: bool,
                session: str | None = None, *, limit: int | None = None,
                tally: _Tally | None = None) -> Outcome:
        last = Outcome()
        tally = tally if tally is not None else _Tally()
        # A session's transaction goes on from where its last query left it.
        saved = self._sessions.get(session) if session else None
        open_now = self._session_transactions.get(session) if session else None
        try:
            for statement in split(text):
                words = statement.upper().split()
                if not dry:
                    self._step_before(statement, inside=saved is not None)
                # The three are whole statements. With a word more, a semicolon is
                # missing, and the engine below refuses to read what follows.
                control = words[0] if words[1:] in ([], ["TRANSACTION"]) else None
                ends_session = words == ["CALL", "BQ.ABORT_SESSION()"]
                if open_now is not None and control is None and not ends_session:
                    # Recorded as tried, whether it then runs or fails.
                    open_now.statements.append(statement)
                start = len(self.journal.calls)
                error = self._check("statement", statement, dry_run=dry)
                if error is not None:
                    raise error
                if ends_session:
                    if session is None:
                        raise BadRequest("stand-in: there is no session to end")
                    # What was not committed goes with the session.
                    if saved is not None:
                        self._restore(saved)
                    if open_now is not None:
                        open_now.ended = ABORT
                    saved = open_now = None
                    del self._sessions[session]
                    self._session_transactions.pop(session, None)
                    session = None
                elif control == "BEGIN":
                    if saved is not None:
                        raise BadRequest("stand-in: a transaction is already open")
                    saved = {name: (_snapshot(t.rows), t.last_changed_time)
                             for name, t in self.tables.items()}
                    if not dry:
                        open_now = Transaction(session)
                        self._transactions.append(open_now)
                elif control in ("COMMIT", "ROLLBACK"):
                    if saved is None:
                        raise BadRequest("stand-in: no transaction is open")
                    if control == "ROLLBACK":
                        self._restore(saved)
                    if open_now is not None:
                        open_now.ended = COMMIT if control == "COMMIT" else ROLLBACK
                    saved = open_now = None
                else:
                    # BigQuery takes a temporary table of the session inside a transaction,
                    # and no other statement that makes or changes a table.
                    temporary = words[:2] in (["CREATE", "TEMP"], ["CREATE", "TEMPORARY"])
                    if saved is not None and words[0] in ("CREATE", "ALTER") and not temporary:
                        raise BadRequest("stand-in: a table cannot change inside a transaction")
                    refused = (self._refused_inside(statement)
                               if saved is not None and not dry else None)
                    if refused is not None:
                        self.journal.remark(start, self.service, "statement", REFUSED)
                        raise refused
                    last = self._read_or_run(statement, params, kinds, dry, limit, tally,
                                             start)
                if not dry:
                    self._step_after(statement)
            if saved is not None and session is None:
                raise BadRequest("stand-in: the script ended inside a transaction")
        except BaseException:
            if session is not None:
                # The transaction stays open, for the caller to roll back.
                self._sessions[session] = saved
                self._keep_open(session, open_now)
            elif saved is not None:
                self._restore(saved)
                if open_now is not None:
                    open_now.ended = ABORT
            raise
        if session is not None:
            self._sessions[session] = saved
            self._keep_open(session, open_now)
        return last

    def _keep_open(self, session: str, open_now: Transaction | None) -> None:
        if open_now is None:
            self._session_transactions.pop(session, None)
        else:
            self._session_transactions[session] = open_now

    def _read_or_run(self, statement: str, params: dict, kinds: dict[str, Kind], dry: bool,
                     limit: int | None, tally: _Tally, start: int) -> Outcome:
        """Run a statement, or answer a dry run of one a test described, counting what it
        reads against the job's limit."""
        shape = next((found for found in self._shapes if found.pattern.search(statement)),
                     None)
        if shape is not None:
            if shape.columns is not None:
                tally.schema = [_field(name, parse_type(kind))
                                for name, kind in shape.columns.items()]
            if dry:
                tally.bytes += shape.bytes
                return Outcome()
            if limit is not None and shape.bytes > int(limit):
                self.journal.remark(start, self.service, "statement", FAILED)
                raise _job_error(
                    InternalServerError, "bytesBilledLimitExceeded",
                    f"Query exceeded limit for bytes billed: {limit}. "
                    f"{shape.bytes} or higher required.",
                )
            tally.bytes += shape.bytes
        return self._statement(statement, params, kinds, dry)

    def _refused_inside(self, statement: str) -> BaseException | None:
        """The error for a statement a test said to refuse inside a transaction, or None."""
        for refusal in self._refusals:
            pattern, times = refusal
            if pattern.search(statement) and times != 0:
                if times is not None:
                    refusal[1] = times - 1
                return BadRequest(
                    "stand-in: Transaction is aborted due to concurrent update against a "
                    "table it writes"
                )
        return None

    def _step_before(self, statement: str, *, inside: bool) -> None:
        for between in list(self._betweens):
            if between.armed and between.second.search(statement):
                self._betweens.remove(between)
                if inside:
                    raise NotUnderstood(
                        "a step inside an open transaction would be shown rows nobody "
                        f"committed, and the stand-in keeps one view of each table: {statement}"
                    )
                between.step()
                # The step's own calls are waited for, like any caller's.
                self._waited_for_the_last()

    def _step_after(self, statement: str) -> None:
        for between in self._betweens:
            if not between.armed and between.first.search(statement):
                between.armed = True

    def _restore(self, saved: dict[str, tuple[list[dict], datetime | None]]) -> None:
        for name, (rows, changed) in saved.items():
            if any(kept.search(name) for kept in self._kept):
                continue
            if name in self.tables:
                self.tables[name].rows[:] = rows
                self.tables[name].last_changed_time = changed

    def _statement(self, statement: str, params: dict, kinds: dict[str, Kind],
                   dry: bool) -> Outcome:
        for pattern, handler in self._handlers:
            if pattern.search(statement):
                if dry:
                    return Outcome()
                answer = handler(self, statement, params)
                if isinstance(answer, int) and not isinstance(answer, bool):
                    return Outcome(affected=answer)
                return Outcome(rows=answer)
        return self._engine.run(statement, params, kinds, dry=dry)

    def load_table_from_json(self, json_rows, destination, job_config=None, **_) -> FakeJob:
        """Load rows given as objects. The real client sets the format itself, whatever
        the settings say."""
        name = table_name(str(destination))
        self._waited_for_the_last()
        self._gate(name)
        with self._lock:
            settings = {**_load_settings(job_config),
                        "source_format": bigquery.SourceFormat.NEWLINE_DELIMITED_JSON}
            self._enter("load", name, rows=len(json_rows), source="json", **settings)
            return self._started(self._load(name, list(json_rows), settings))

    def _load(self, name: str, rows: list[dict], settings: dict) -> FakeJob:
        # A load job could make the table, replace it or add columns to it. No code
        # outside a migration may, so the stand-in loads into a table that is there, as
        # it is, and reads no load that asks for more.
        if settings["write_disposition"] == bigquery.WriteDisposition.WRITE_TRUNCATE:
            raise NotUnderstood(
                f"a load that replaces {name} can change its columns; the stand-in only adds"
            )
        if settings["schema_update_options"]:
            raise NotUnderstood(f"a load that changes the columns of {name} is not read")
        subject = f"load into {name}"
        try:
            table = self._engine.data_table(name)
            if (settings["write_disposition"] == bigquery.WriteDisposition.WRITE_EMPTY
                    and table.rows):
                raise Conflict(f"stand-in: {name} already holds rows")
            checked = [self._engine.check_row(table, row) for row in rows]
        except Exception as exc:  # noqa: BLE001 - handed to result(), as the client does
            return FakeJob(error=exc, subject=subject)
        table.rows.extend(checked)
        self._engine.changed(table, len(checked))
        return FakeJob(output_rows=len(checked), subject=subject)

    def get_table(self, table, **_) -> bigquery.Table:
        name = table_name(_reference_text(table))
        self._waited_for_the_last()
        with self._lock:
            self._enter("get_table", name)
            return self._described(name)

    def _described(self, name: str) -> bigquery.Table:
        """A table's columns, partitioning and expiry, as the real client describes them:
        with the API's names for the types, which are not the names SQL uses."""
        found = self._engine.table(name)
        dataset, short = name.split(".")
        schema = [
            _field(column, found.kind(column), column in found.required)
            for column in found.columns
        ]
        real = bigquery.Table(f"{self.project}.{dataset}.{short}", schema=schema)
        # How many rows it holds is part of the description, as the API gives it, and so is
        # when it was last changed, in milliseconds since 1970 as text.
        real._properties["numRows"] = str(len(found.rows))
        if found.last_changed_time is not None:
            real._properties["lastModifiedTime"] = str(
                int(found.last_changed_time.timestamp() * 1000)
            )
        if found.partition_column is not None:
            days = found.partition_expiration_days
            real.time_partitioning = bigquery.TimePartitioning(
                field=found.partition_column,
                expiration_ms=None if days is None else int(days * _DAY_MS),
            )
        if found.view_sql is not None:
            real.view_query = found.view_sql
        return real

    def get_dataset(self, dataset_ref, **_) -> bigquery.Dataset:
        """A dataset `add_dataset` made, with its location. Any other is not there."""
        name = str(dataset_ref)
        self._waited_for_the_last()
        with self._lock:
            self._enter("get_dataset", name)
            if name not in self._datasets:
                raise NotFound(f"stand-in: no dataset {name}")
            found = bigquery.Dataset(name)
            found.location = self._datasets[name][0]
            return found

    def list_tables(self, dataset, **_) -> list:
        """The tables of a dataset `add_dataset` made, as the client lists them."""
        name = str(dataset)
        self._waited_for_the_last()
        with self._lock:
            self._enter("list_tables", name)
            if name not in self._datasets:
                raise NotFound(f"stand-in: no dataset {name}")
            project, short = name.split(".")
            return [bigquery.table.TableListItem({"tableReference": {
                "projectId": project, "datasetId": short, "tableId": table}})
                for table in self._datasets[name][1]]

    def update_table(self, table: bigquery.Table, fields, **_) -> bigquery.Table:
        """Only a table's partition expiry can be changed this way."""
        name = table_name(_reference_text(table))
        self._waited_for_the_last()
        with self._lock:
            self._enter("update_table", name, fields=list(fields))
            found = self._engine.data_table(name)
            if list(fields) != ["time_partitioning"]:
                raise NotUnderstood(f"the stand-in changes a partition expiry only: {fields}")
            partitioning = table.time_partitioning
            if found.partition_column is None or partitioning is None:
                raise BadRequest(f"stand-in: {name} is not partitioned")
            ms = partitioning.expiration_ms
            found.options["partition_expiration_days"] = None if ms is None else ms / _DAY_MS
            found.last_changed_time = self.clock()
            return self._described(name)

    def insert_rows_json(self, *args, **kwargs):
        raise AssertionError(
            "streamed rows cannot be updated at once; the pipeline writes by load job or DML"
        )

    insert_rows = insert_rows_json

    # Stepping -----------------------------------------------------------------------------

    def _gate(self, subject: str) -> None:
        if self._stepper is not None:
            self._stepper.arrive(subject)


def _field(name: str, kind: Kind, required: bool = False) -> bigquery.SchemaField:
    """A column as tables.get gives it."""
    mode = "REPEATED" if kind.repeated else "REQUIRED" if required else "NULLABLE"
    return bigquery.SchemaField(
        name, API_NAMES.get(kind.base, kind.base), mode=mode,
        fields=[_field(inner_name, inner) for inner_name, inner in kind.fields],
    )


def _reference_text(table) -> str:
    if isinstance(table, str):
        return table
    return f"{table.project}.{table.dataset_id}.{table.table_id}"


class StepError(AssertionError):
    """The callers did not do what the order of steps needs."""


class _Caller:
    def __init__(self, name: str):
        self.name = name
        self.turn = threading.Semaphore(0)
        self.parked = threading.Event()
        self.ended = False
        self.free = False
        self.waiting_on: str | None = None
        self.result = None
        self.error: BaseException | None = None
        self.thread: threading.Thread | None = None


class Stepper:
    """Runs two or more callers one BigQuery call at a time, in an order the test gives.

    A caller is a function, run on a thread of its own. It stops before each `query` and
    each load it makes. `step(name)` lets that caller make the one call it waits on, and
    comes back when the caller has stopped before its next call or has ended. So between
    two steps exactly one call has happened, and the test decides whose.

        steps = bq.stepper(match=r"ops\\.runs")
        steps.caller("a", lambda: run_one())
        steps.caller("b", lambda: run_two())
        steps.run(["a", "b", "a", "b"])
        ended = steps.finish()

    With `match`, only calls whose statement or table holds the pattern are stops; every
    other call goes straight through.
    """

    def __init__(self, match: str | None = None, timeout: float = 5.0):
        self._pattern = re.compile(match) if match is not None else None
        self._timeout = timeout
        self._callers: dict[str, _Caller] = {}
        self._by_thread: dict[int, _Caller] = {}

    def caller(self, name: str, function: Callable[[], object]) -> None:
        """Start a caller. It runs up to its first stop."""
        if name in self._callers:
            raise ValueError(f"a caller is already named {name}")
        caller = _Caller(name)
        self._callers[name] = caller

        def work() -> None:
            self._by_thread[threading.get_ident()] = caller
            try:
                caller.result = function()
            except BaseException as exc:  # noqa: BLE001 - kept for the test to read
                caller.error = exc
            finally:
                caller.ended = True
                caller.waiting_on = None
                caller.parked.set()

        caller.thread = threading.Thread(target=work, name=f"stand-in-{name}", daemon=True)
        caller.thread.start()
        self._wait(caller)

    def arrive(self, subject: str) -> None:
        """Called by the stand-in on the caller's own thread, before a call."""
        caller = self._by_thread.get(threading.get_ident())
        if caller is None or caller.free:
            return
        if self._pattern is not None and not self._pattern.search(subject):
            return
        caller.waiting_on = subject
        caller.parked.set()
        if not caller.turn.acquire(timeout=self._timeout):
            raise StepError(f"{caller.name} was never given its turn")

    def waiting_on(self, name: str) -> str | None:
        """The statement or table of the call a caller waits on, or None when it ended."""
        return self._callers[name].waiting_on

    def ended(self, name: str) -> bool:
        return self._callers[name].ended

    def step(self, name: str) -> None:
        caller = self._callers[name]
        if caller.ended:
            raise StepError(f"{name} has ended and has no call left to make")
        caller.parked.clear()
        caller.turn.release()
        self._wait(caller)

    def run(self, order) -> None:
        for name in order:
            self.step(name)

    def finish(self) -> dict[str, _Caller]:
        """Let every caller go on to its end, one after the other in the order they were
        started. Returns each caller, with its `result` or its `error`."""
        for caller in self._callers.values():
            caller.free = True
            caller.turn.release()
            caller.thread.join(self._timeout)
            if caller.thread.is_alive():
                raise StepError(f"{caller.name} did not end")
        return dict(self._callers)

    def _wait(self, caller: _Caller) -> None:
        if not caller.parked.wait(self._timeout):
            raise StepError(f"{caller.name} neither reached a call nor ended")


def interleavings(steps: dict[str, int]) -> list[tuple[str, ...]]:
    """Every order in which the callers can take their steps, each keeping its own order.
    Two callers with two steps each give six."""
    names = [name for name, count in steps.items() for _ in range(count)]
    return sorted(set(itertools.permutations(names)))
