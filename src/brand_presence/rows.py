"""Row models and BigQuery column types, ported from the source pipeline."""
from __future__ import annotations

import types
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brand_presence import status

ALL = "all"



RUN_STATUSES = ("running", "success", "partial", "error")



LEDGER_STATES = (
    "sending", "submitted", "unknown", "collected", status.UNUSABLE, "failed",
    status.REJECTED, status.EXPIRED,
)



_COLUMN_TYPES = {
    str: "STRING", int: "INT64", float: "FLOAT64", Decimal: "NUMERIC", bool: "BOOL",
    date: "DATE", datetime: "TIMESTAMP",
}



class Row(BaseModel):
    """The base of every row model. A model of a table sets `table`; one that only holds
    columns others share leaves it out."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True,
    )

    table: ClassVar[str | None] = None      # <dataset>.<table>

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if not cls.model_config.get("hide_input_in_errors"):
            raise TypeError(f"{cls.__name__}: a row model hides input values in its errors")
        columns_of(cls)



def columns_of(model: type[Row]) -> dict[str, tuple[str, bool]]:
    """A model's columns as a migration would give them: the type, and whether the column
    is required. A field that may be None is a nullable column, a list is an array, and an
    array is never required. Raises TypeError for a field no column type stands for."""
    found = {}
    for name, info in model.model_fields.items():
        annotation = info.annotation
        nullable = False
        if get_origin(annotation) in (Union, types.UnionType):
            members = [a for a in get_args(annotation) if a is not type(None)]
            nullable = len(members) < len(get_args(annotation))
            annotation = members[0] if len(members) == 1 else None
        if get_origin(annotation) is list:
            inner = get_args(annotation)
            kind = _COLUMN_TYPES.get(inner[0]) if len(inner) == 1 else None
            if kind is None or nullable:
                raise TypeError(f"{model.__name__}.{name}: an array column is a list of one "
                                "plain type, and is never None")
            found[name] = (f"ARRAY<{kind}>", False)
            continue
        if annotation not in _COLUMN_TYPES:
            raise TypeError(f"{model.__name__}.{name}: no column type stands for its type")
        found[name] = (_COLUMN_TYPES[annotation], not nullable)
    return found



class CleanRow(Row):
    """Every clean row carries its brand, country and location. A row about a brand
    other than the client brand adds
    `subject_brand_id`.

    A table's model names its natural key: the columns that make a row one measure. The
    key holds the period.

    A table whose rows are parts of another table's row names that table's model as its
    `parent`: the brands in an answer are parts of the answer. Its own key holds every
    column of the parent's key, and its rows follow the parent's: a write of a parent row
    takes out every part of the row it replaces, whether or not the new row has parts."""

    natural_key: ClassVar[tuple[str, ...]] = ()
    item_key: ClassVar[str | None] = None
    parent: ClassVar[type[CleanRow] | None] = None
    # Whether a row may carry the reserved `all`. Only a pull row may.
    may_span_every_country: ClassVar[bool] = False

    # The client brand the row was measured for. Never null, and never a guessed brand.
    client_brand_id: str = Field(min_length=1)
    country_code: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    # Null where the row belongs to no benchmark set.
    benchmark_version: int | None
    snapshot_id: str
    period: str
    period_start: date
    request_key: str
    run_id: str
    pulled_at: datetime

    @model_validator(mode="after")
    def _all_on_a_pull_alone(self):
        # A pull row of a request for every country carries `all` as its country and its
        # location, and no benchmark version. Its clean rows carry their own countries.
        spans = (self.country_code == ALL, self.location_id == ALL)
        if not any(spans):
            return self
        if not self.may_span_every_country:
            raise ValueError("only a pull row carries the reserved country and location all")
        if not all(spans) or self.benchmark_version is not None:
            raise ValueError("a pull of every country carries all as its country and its "
                             "location, and no benchmark version")
        return self

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if cls.table is None:
            return
        problems = key_problems(cls)
        if problems:
            raise TypeError("; ".join(problems))



def key_problems(model: type[CleanRow]) -> list[str]:
    """What is wrong with the natural key a clean table's model names."""
    name, key = model.__name__, model.natural_key
    if not key:
        return [f"{name}: a clean table's model names its natural key"]
    problems = [f"{name}: the natural key names {column}, which is no column of the model"
                for column in key if column not in model.model_fields]
    if len(set(key)) != len(key):
        problems.append(f"{name}: the natural key names a column twice")
    if model.item_key is None:
        if "period" not in key:
            problems.append(f"{name}: the natural key holds the period")
    elif model.item_key not in key or "period" in key:
        problems.append(
            f"{name}: a cursor source's key holds the item ID, {model.item_key}, and no period"
        )
    parent = model.parent
    if parent is not None:
        if parent.table is None or parent.parent is not None or model.item_key is not None:
            problems.append(f"{name}: a parent is a clean table of a task source, with no "
                            "parent of its own")
        problems += [f"{name}: the natural key leaves out {column}, of its parent's key"
                     for column in parent.natural_key if column not in key]
    return problems



class RawRow(Row):
    """A row of `raw.<source>`: one stored answer, with the load it was collected under. A
    rebuild takes its settings from that load number."""

    run_id: str
    request_key: str
    period: str
    # The date the content is of, where the answer has one. A table that expires is
    # partitioned by it, and its model narrows this to a date: the migration reader takes
    # no expiry on a nullable partition column.
    content_date: date | None
    pulled_at: datetime
    # The request's body and the vendor's answer, as JSON text. Never a key or a header.
    request: str
    response: str
    # A window source stores a row for each page, and this is the verdict on that page
    # alone. Whether the whole answer is usable is the state of its ledger row.
    usable: bool
    load_number: int
    snapshot_id: str



class LedgerRow(Row):
    """A row of `ops.<source>_tasks`: one attempt at one request."""

    request_key: str
    period: str
    attempt: int
    vendor_task_id: str | None
    state: str
    # The run that submitted it.
    run_id: str
    # The request as it was planned: its body, the sets it serves and the load it was
    # planned under, as JSON text. A task collected by a later run is stored raw with it.
    request: str
    created_at: datetime
    submitted_at: datetime | None
    collected_at: datetime | None
    # The run that stored the raw answer, which is the run in its object's path.
    stored_run_id: str | None
    # When the raw answer was loaded into the raw table, and when its clean rows were written.
    loaded_at: datetime | None
    written_at: datetime | None
    error_class: str | None
    # The status code the vendor gave with a refusal, where it gave one.
    vendor_status: int | None

    @field_validator("state")
    @classmethod
    def _a_ledger_state(cls, value: str) -> str:
        if value not in LEDGER_STATES:
            raise ValueError("not a state of the ledger")
        return value



class RunRow(Row):
    """A row of ops.runs: one run of a source or of a service job."""

    table: ClassVar[str] = "ops.runs"

    run_id: str
    client_id: str
    source: str
    period: str | None = None
    frequency: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    status: str
    image_version: str
    load_number: int | None = None
    snapshot_id: str | None = None
    requests_planned: int | None = None
    requests_sent: int | None = None
    requests_failed: int | None = None
    requests_pending: int | None = None
    requests_unknown: int | None = None
    answers_usable: int | None = None
    answers_unusable: int | None = None
    raw_rows: int | None = None
    clean_rows: int | None = None
    # Always null: the vendor's cost is tracked at the vendor, per API key (D16).
    vendor_cost: Decimal | None = None
    vendor_cost_unit: str | None = None
    error_class: str | None = None
    error_count: int | None = None
    failed_check: str | None = None
    @field_validator("status")
    @classmethod
    def _a_status(cls, value: str) -> str:
        if value not in RUN_STATUSES:
            raise ValueError("not a status of a run")
        return value
