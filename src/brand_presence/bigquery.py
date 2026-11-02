"""Raw before clean; transactional replacement by request and natural key."""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery

from brand_presence.rows import CleanRow, RawRow, Row, columns_of
from brand_presence.state import write_once

BATCH_BYTES = 4_000_000



def raw_prefix(source: str, period: str, run_id: str | None = None) -> str:
    """Where a source's raw objects of a period are, and those of one run under it."""
    prefix = f"raw/{source}/{period}/"
    return prefix if run_id is None else f"{prefix}{run_id}/"



@dataclass(frozen=True)
class Write:
    """The clean rows of one usable answer. An answer with no row is still a write: the
    older rows of its request go."""

    request_key: str
    pulled_at: datetime
    rows: Sequence[CleanRow]



def _parameter(name: str | None, kind: str, value: object):
    if kind.startswith("ARRAY<"):
        return bigquery.ArrayQueryParameter(name, kind[len("ARRAY<"):-1], list(value or []))
    return bigquery.ScalarQueryParameter(name, kind, value)



def _struct(model: type[Row], row: Row, names: Sequence[str]) -> bigquery.StructQueryParameter:
    kinds = columns_of(model)
    return bigquery.StructQueryParameter(
        None, *[_parameter(column, kinds[column][0], getattr(row, column)) for column in names]
    )



def rows_parameter(name: str, model: type[Row], rows: Sequence[Row],
                   columns: Sequence[str] | None = None) -> bigquery.ArrayQueryParameter:
    """Rows as one array parameter, each a struct with the model's columns under the types
    the migrations give them. `columns` limits it to some of them."""
    names = list(columns_of(model)) if columns is None else list(columns)
    return bigquery.ArrayQueryParameter(
        name, "STRUCT", [_struct(model, row, names) for row in rows]
    )



def row_batches(model: type[Row], rows: Sequence[Row],
                columns: Sequence[str] | None = None) -> Iterator[list[Row]]:
    """Rows in batches, each of which fits in one request as a parameter. A row is weighed
    as the client sends it. One row heavier than a batch travels alone."""
    names = list(columns_of(model)) if columns is None else list(columns)
    batch: list[Row] = []
    weight = 0
    for row in rows:
        size = len(json.dumps(_struct(model, row, names).to_api_repr()))
        if batch and weight + size > BATCH_BYTES:
            yield batch
            batch, weight = [], 0
        batch.append(row)
        weight += size
    if batch:
        yield batch



def text_batches(values: Sequence[str]) -> Iterator[list[str]]:
    """Text values in batches, each of which fits in one request as a parameter."""
    batch: list[str] = []
    weight = 0
    for value in values:
        # What the client sends around each value is some twenty bytes.
        size = len(value.encode("utf-8")) + 20
        if batch and weight + size > BATCH_BYTES:
            yield batch
            batch, weight = [], 0
        batch.append(value)
        weight += size
    if batch:
        yield batch



def text_parameter(name: str, value: str | None) -> bigquery.ScalarQueryParameter:
    return bigquery.ScalarQueryParameter(name, "STRING", value)



def texts_parameter(name: str, values: Sequence[str]) -> bigquery.ArrayQueryParameter:
    return bigquery.ArrayQueryParameter(name, "STRING", list(values))



def query(bq, sql: str, parameters: Sequence | None = None) -> list[dict]:
    """Run one statement or one script and wait for it: `query` only starts a job."""
    config = bigquery.QueryJobConfig(query_parameters=list(parameters or []))
    return [dict(row) for row in bq.query(sql, job_config=config).result()]



def changed(bq, sql: str, parameters: Sequence | None = None) -> int | None:
    """Run one statement that changes rows and wait for it. Returns how many rows it
    changed, as the job counted them."""
    config = bigquery.QueryJobConfig(query_parameters=list(parameters or []))
    job = bq.query(sql, job_config=config)
    job.result()
    return job.num_dml_affected_rows



def read(bq, model: type[Row], where: str, parameters: Sequence | None = None) -> list:
    """The rows of a model's table that fit, each checked against the model."""
    columns = ", ".join(model.model_fields)
    found = query(bq, f"SELECT {columns} FROM {model.table} WHERE {where}", parameters)
    return [model(**row) for row in found]



class Transaction:
    """One transaction over several requests, held in a BigQuery session. It commits when
    the block ends and rolls back when the block raises. The session is ended either way,
    which rolls back whatever a failed commit or a failed rollback left open.

    `time_left`, when given, says how many milliseconds a statement may still run: each
    statement's job carries it as its time limit, and BigQuery stops the job after it. The
    commit, the rollback and the session's end carry none."""

    def __init__(self, bq, *, time_left: Callable[[], int] | None = None):
        self.bq = bq
        self.session: str | None = None
        self.time_left = time_left

    def __enter__(self) -> Transaction:
        job = self.bq.query("BEGIN TRANSACTION",
                            job_config=bigquery.QueryJobConfig(create_session=True))
        job.result()
        self.session = job.session_info.session_id
        return self

    def run(self, sql: str, parameters: Sequence | None = None) -> list[dict]:
        """Run one statement inside the transaction, and wait for it."""
        job = self.job(sql, parameters)
        return [dict(row) for row in job.result()]

    def job(self, sql: str, parameters: Sequence | None = None, *,
            maximum_bytes_billed: int | None = None, timed: bool = True):
        """Run one statement inside the transaction and wait for it. Returns the job, which
        says how many rows it changed and how many bytes it was billed."""
        config = bigquery.QueryJobConfig(
            query_parameters=list(parameters or []),
            connection_properties=[bigquery.ConnectionProperty("session_id", self.session)],
        )
        if maximum_bytes_billed is not None:
            config.maximum_bytes_billed = maximum_bytes_billed
        if timed and self.time_left is not None:
            config.job_timeout_ms = self.time_left()
        job = self.bq.query(sql, job_config=config)
        job.result()
        return job

    def _end(self, sql: str) -> None:
        self.job(sql, timed=False)

    def __exit__(self, kind, error, trace) -> bool:
        pending = error
        try:
            if kind is None:
                self._end("COMMIT TRANSACTION")
            else:
                self._end("ROLLBACK TRANSACTION")
        except Exception as cleanup:
            if pending is None:
                pending = cleanup
            else:
                pending.add_note(f"transaction cleanup failed: {type(cleanup).__name__}")
        finally:
            try:
                self._end("CALL BQ.ABORT_SESSION()")
            except Exception as cleanup:
                if pending is None:
                    pending = cleanup
                else:
                    pending.add_note(f"session cleanup failed: {type(cleanup).__name__}")
        if pending is not None and pending is not error:
            raise pending
        return False



class RepeatedRow(ValueError):
    """One answer holds two rows of one model with one natural key. Neither is written:
    nothing says which is the row."""



def _natural_key(model: type[CleanRow], row: CleanRow) -> tuple:
    return (row.period, *(getattr(row, c) for c in model.natural_key if c != "period"))



def repeated(models: Sequence[type[CleanRow]], rows: Sequence[CleanRow]) -> bool:
    """Whether one answer holds two rows of one model with the same natural key."""
    for model in models:
        seen: set[tuple] = set()
        for row in rows:
            if type(row) is not model:
                continue
            key = _natural_key(model, row)
            if key in seen:
                return True
            seen.add(key)
    return False



def _parent_key(parent: type[CleanRow], row: CleanRow) -> tuple:
    """The key of the parent row a part belongs to: the period, then the other columns of
    the parent's natural key."""
    return (row.period, *(getattr(row, c) for c in parent.natural_key if c != "period"))



class Warehouse:
    """The local raw files and tables of one client's project, as one run uses them."""

    def __init__(self, bq, state_dir: Path):
        self.bq = bq
        self.state_dir = state_dir
        self.counts: Counter[str] = Counter()

    def save_raw(self, source: str, row: RawRow, name: str,
                 storage_run_id: str) -> str:
        path = raw_prefix(source, row.period, storage_run_id) + name
        write_once(self.state_dir / path, row.model_dump_json() + "\n")
        return path

    def has_raw(self, path: str) -> bool:
        return (self.state_dir / path).is_file()

    def read_raw(self, model: type[RawRow], path: str) -> RawRow | None:
        try:
            text = (self.state_dir / path).read_text()
        except FileNotFoundError:
            return None
        return model.model_validate_json(text)

    def load_raw(self, model: type[RawRow], paths: Sequence[str]) -> int:
        if not paths:
            return 0
        rows = [self.read_raw(model, path) for path in paths]
        if any(row is None for row in rows):
            raise FileNotFoundError("a raw answer is missing")
        columns = ", ".join(model.model_fields)
        with Transaction(self.bq) as transaction:
            for row in rows:
                transaction.run(
                    f"DELETE FROM {model.table} WHERE run_id = @run_id "
                    "AND request_key = @request_key AND request = @request",
                    [text_parameter("run_id", row.run_id),
                     text_parameter("request_key", row.request_key),
                     text_parameter("request", row.request)],
                )
                transaction.run(
                    f"INSERT INTO {model.table} ({columns}) "
                    f"SELECT {columns} FROM UNNEST(@rows)",
                    [rows_parameter("rows", model, [row])],
                )
        return len(rows)

    def loaded(self, model: type[RawRow], period: str) -> set[tuple[str, str]]:
        """The run and the request key of every raw row of a period."""
        found = query(
            self.bq, f"SELECT run_id, request_key FROM {model.table} WHERE period = @period",
            [text_parameter("period", period)],
        )
        return {(row["run_id"], row["request_key"]) for row in found}

    def raw_rows(self, model: type[RawRow], period: str,
                 keys: Sequence[str] | None = None) -> list[RawRow]:
        """The raw rows of a period, oldest first; with `keys`, those of these requests."""
        where, parameters = "period = @period", [text_parameter("period", period)]
        if keys is not None:
            where += " AND request_key IN UNNEST(@keys)"
            parameters.append(texts_parameter("keys", keys))
        return sorted(read(self.bq, model, where, parameters), key=lambda row: row.pulled_at)

    # Clean rows -------------------------------------------------------------------------

    def replace(self, models: Sequence[type[CleanRow]], writes: Sequence[Write]) -> int:
        """Write the clean rows of usable answers by the replace rule, in one transaction
        over all the models' tables. Returns the number of rows inserted."""
        if not writes:
            return 0
        strays = [type(row).__name__ for write in writes for row in write.rows
                  if type(row) not in models]
        if strays:
            raise ValueError(f"a row of a model the source does not list: {strays[0]}")
        # The run leaves out an answer that repeats a natural key, and counts it. One that
        # reaches here is refused whole: no row of it is picked over another.
        if any(repeated(models, write.rows) for write in writes):
            raise RepeatedRow("an answer holds two rows with one natural key")
        # One answer for each request: the newest. The rows of an older answer to the same
        # request go with the delete by its key, and none of them is put back.
        latest: dict[str, Write] = {}
        for write in sorted(writes, key=lambda write: write.pulled_at):
            latest[write.request_key] = write
        writes = sorted(latest.values(), key=lambda write: write.pulled_at)
        keys = sorted(latest)
        # Worked out before the transaction is opened, so a refused row opens none.
        plans: list[tuple[type[CleanRow], list[str], dict[tuple, CleanRow]]] = []
        for model in models:
            key_columns = [c for c in model.natural_key if c != "period"]
            # One row for each natural key in each period: the newest answer's. One answer
            # holds no key twice: that is refused above.
            newest: dict[tuple, CleanRow] = {}
            for write in writes:
                for row in write.rows:
                    if type(row) is not model:
                        continue
                    measure = tuple(getattr(row, column) for column in key_columns)
                    if any(value is None for value in measure):
                        raise ValueError(f"{model.__name__}: a natural key holds no null")
                    newest[(None if model.item_key else row.period, *measure)] = row
            plans.append((model, key_columns, newest))
        # A table of parts follows its parent. A part is written only with the answer that
        # gives its parent row: where two answers being written hold the same parent row,
        # the parts of the older one go with it.
        rows_of = {model: newest for model, _, newest in plans}
        for model, _, newest in plans:
            if model.parent is None:
                continue
            if model.parent not in rows_of:
                raise ValueError(f"{model.__name__}: its parent is not a model of the source")
            parents = rows_of[model.parent]
            for key, row in list(newest.items()):
                parent = parents.get(_parent_key(model.parent, row))
                if parent is not None and parent.request_key != row.request_key:
                    del newest[key]
        inserted = 0
        with Transaction(self.bq) as transaction:
            for model, key_columns, newest in plans:
                for batch in text_batches(keys):
                    transaction.run(
                        f"DELETE FROM {model.table} WHERE request_key IN UNNEST(@request_keys)",
                        [texts_parameter("request_keys", batch)],
                    )
                for period in sorted({key[0] for key in newest}):
                    of_period = [row for key, row in newest.items() if key[0] == period]
                    if not key_columns:
                        transaction.run(f"DELETE FROM {model.table} WHERE period = @period",
                                        [text_parameter("period", period)])
                        continue
                    for batch in row_batches(model, of_period, key_columns):
                        where = "" if model.item_key else "period = @period AND "
                        parameters = [] if model.item_key else [text_parameter("period", period)]
                        transaction.run(
                            f"DELETE FROM {model.table} WHERE {where}"
                            f"STRUCT({', '.join(key_columns)}) IN UNNEST(@measures)",
                            [*parameters, rows_parameter("measures", model, batch, key_columns)],
                        )
                if model.parent is not None:
                    # Every part of each parent row being written, by the parent's key:
                    # also those the new parent row has no part for.
                    parent = model.parent
                    parent_columns = [c for c in parent.natural_key if c != "period"]
                    parents = rows_of[parent]
                    for period in sorted({key[0] for key in parents}):
                        of_period = [row for key, row in parents.items() if key[0] == period]
                        for batch in row_batches(parent, of_period, parent_columns):
                            transaction.run(
                                f"DELETE FROM {model.table} WHERE period = @period "
                                f"AND STRUCT({', '.join(parent_columns)}) IN UNNEST(@measures)",
                                [text_parameter("period", period),
                                 rows_parameter("measures", parent, batch, parent_columns)],
                            )
                columns = ", ".join(model.model_fields)
                for batch in row_batches(model, list(newest.values())):
                    transaction.run(
                        f"INSERT INTO {model.table} ({columns}) "
                        f"SELECT {columns} FROM UNNEST(@rows)",
                        [rows_parameter("rows", model, batch)],
                    )
                    inserted += len(batch)
        return inserted
