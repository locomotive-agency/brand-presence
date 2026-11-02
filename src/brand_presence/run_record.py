"""The running row is written before checking for another run in flight."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib import metadata

from google.cloud import bigquery

from brand_presence import status
from brand_presence.bigquery import changed, query, text_parameter


@dataclass(frozen=True)
class Entry:
    time_limit_seconds: int = 3600

CATALOGUE = {name: Entry() for name in ("search-volume", "reports", "init")}

@dataclass(frozen=True)
class Row:
    run_id: str
    client_id: str | None
    source: str
    started_at: datetime
    image_version: str | None = field(default_factory=lambda: metadata.version("brand-presence"))
    period: str | None = None
    frequency: str | None = "monthly"
    execution: str | None = None

@dataclass(frozen=True)
class Lines:
    out: object = print

def _line(row, ended_as, ended_at, values):
    return json.dumps({"run_id": row.run_id, "source": row.source,
                       "period": row.period, "status": ended_as,
                       "ended_at": ended_at.isoformat(), **values}, default=str)

IN_FLIGHT = status.IN_FLIGHT



DID_NOT_FINISH = status.DID_NOT_FINISH



RUN_ROW_FAILED = status.RUN_ROW_FAILED



ERROR = "error"



_END_COLUMNS = {
    "matches_unlisted": "INT64", "rate_limit_waits": "INT64",
    "load_number": "INT64", "snapshot_id": "STRING",
    "requests_planned": "INT64", "requests_sent": "INT64", "requests_failed": "INT64",
    "requests_pending": "INT64", "requests_unknown": "INT64", "answers_usable": "INT64",
    "answers_unusable": "INT64", "raw_rows": "INT64", "clean_rows": "INT64",
    "error_class": "STRING", "error_count": "INT64", "failed_check": "STRING",
}



class RowNotUpdated(RuntimeError):
    """A run's row was not `running` when the run ended, or was not there: the update
    changed no row."""



def start(bq, row: Row) -> None:
    """Write a run's `running` row."""
    query(
        bq,
        "INSERT INTO ops.runs "
        "(run_id, client_id, source, period, frequency, started_at, status, image_version) "
        "VALUES (@run_id, @client_id, @source, @period, @frequency, @started_at, 'running', "
        "@image_version)",
        [
            text_parameter("run_id", row.run_id),
            text_parameter("client_id", row.client_id),
            text_parameter("source", row.source),
            text_parameter("period", row.period),
            text_parameter("frequency", row.frequency),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", row.started_at),
            text_parameter("image_version", row.image_version),
        ],
    )



def others(bq, run_id: str, started_at: datetime,
           catalogue: Mapping[str, Entry] = CATALOGUE) -> tuple[list[Row], list[Row]]:
    """The other `running` rows, read after this run's own row is written: those in
    flight, and those older than their job's time limit, which never ended. Each is read
    whole, so that the run that closes one can write its status line."""
    rows = query(
        bq,
        "SELECT run_id, client_id, source, started_at, image_version, period, frequency "
        "FROM ops.runs WHERE status = 'running' AND run_id != @run_id",
        [text_parameter("run_id", run_id)],
    )
    # A job the catalogue does not know is given the longest limit it holds.
    longest = max(entry.time_limit_seconds for entry in catalogue.values())
    in_flight, abandoned = [], []
    for row in rows:
        entry = catalogue.get(row["source"])
        limit = timedelta(seconds=entry.time_limit_seconds if entry else longest)
        other = Row(
            run_id=row["run_id"], client_id=row["client_id"], source=row["source"],
            started_at=row["started_at"], image_version=row["image_version"],
            period=row["period"], frequency=row["frequency"], execution=None,
        )
        (in_flight if other.started_at + limit > started_at else abandoned).append(other)
    return in_flight, abandoned



def _checked(values: Mapping[str, object]) -> None:
    unknown = sorted(set(values) - set(_END_COLUMNS))
    if unknown:
        raise ValueError(f"ops.runs records no {', '.join(unknown)}")



def _update(bq, run_id: str, ended_as: str, ended_at: datetime,
            values: Mapping[str, object]) -> bool:
    """End a row that is still `running`. Returns whether there was one: a row another run
    ended first is left as that run wrote it."""
    sets = ["status = @status", "ended_at = @ended_at"]
    parameters = [
        text_parameter("status", ended_as),
        bigquery.ScalarQueryParameter("ended_at", "TIMESTAMP", ended_at),
        text_parameter("run_id", run_id),
    ]
    for column, kind in _END_COLUMNS.items():
        sets.append(f"{column} = @{column}")
        parameters.append(bigquery.ScalarQueryParameter(column, kind, values.get(column)))
    return changed(
        bq,
        f"UPDATE ops.runs SET {', '.join(sets)} WHERE run_id = @run_id AND status = 'running'",
        parameters,
    ) != 0



def end(bq, row: Row, ended_as: str, ended_at: datetime, lines: Lines,
        **values: object) -> None:
    """Give a `running` row its end: the status, the time, and what the run counted. A
    value left out is recorded as null.

    Then write the run's status line, from the same values. Where the update fails, or
    changes no row, the line is written all the same, as an `error`, and the failure is
    raised again."""
    _checked(values)
    try:
        if not _update(bq, row.run_id, ended_as, ended_at, values):
            raise RowNotUpdated(row.run_id)
    except Exception:
        without_row(row, ended_at, lines, **values)
        raise
    lines.out(_line(row, ended_as, ended_at, values))



def without_row(row: Row, ended_at: datetime, lines: Lines, *, ended_as: str = ERROR,
                **values: object) -> None:
    """Write the status line of a run whose row could not be written or updated. Such a run
    ends as `error`, unless the caller says how it did end. Where nothing else names why an
    error is one, the class says the row was not written."""
    _checked(values)
    said = dict(values)
    if (ended_as == ERROR and said.get("error_class") is None
            and said.get("failed_check") is None):
        said["error_class"], said["error_count"] = RUN_ROW_FAILED, 1
    lines.out(_line(row, ended_as, ended_at, said))



def close_abandoned(bq, abandoned: list[Row], ended_at: datetime, lines: Lines) -> None:
    """Close runs that never ended as `error`, with the class `did not finish`. Each gets
    its status line once its row is updated: a killed job wrote none of its own."""
    values = {"error_class": DID_NOT_FINISH, "error_count": 1}
    for other in abandoned:
        # A row that could not be closed stays `running`, and gets its line from the run
        # that does close it. A row another run closed since it was read has its line from
        # that run, with the end time the row holds.
        if _update(bq, other.run_id, ERROR, ended_at, values):
            lines.out(_line(other, ERROR, ended_at, values))

