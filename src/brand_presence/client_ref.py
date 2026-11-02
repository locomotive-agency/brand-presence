"""Immutable configuration loads used to parse recovered answers under their original settings."""
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from brand_presence.bigquery import query, rows_parameter
from brand_presence.request_keys import content_hash
from brand_presence.rows import Row
from brand_presence.settings import Keyword, Settings


class InconsistentRef(RuntimeError):
    pass


class Location(BaseModel):
    location_id: str
    canonical_name: str
    country_code: str
    sources: tuple[str, ...] = ("search-volume",)


class BenchmarkSet(BaseModel):
    client_brand_id: str
    country_code: str
    version: int
    language: str
    keywords: tuple[Keyword, ...]


class ClientRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    load_number: int
    snapshot_id: str
    benchmark_sets: tuple[BenchmarkSet, ...]
    locations: tuple[Location, ...]


class Snapshot(Row):
    table: ClassVar[str] = "ops.config_snapshots"
    load_number: int
    snapshot_id: str
    applied_at: datetime
    payload: str


def apply(bq, settings: Settings, now: datetime) -> ClientRef:
    content = settings.model_dump(mode="json", include={"brands", "countries", "benchmark_sets"})
    identity = content_hash(content)
    held = query(bq, "SELECT * FROM ops.config_snapshots ORDER BY load_number DESC")
    if held and held[0]["snapshot_id"] == identity:
        return ClientRef.model_validate_json(held[0]["payload"])
    number = held[0]["load_number"] + 1 if held else 1
    before = ClientRef.model_validate_json(held[0]["payload"]) if held else None
    countries = {c.country_code: c for c in settings.countries}
    sets = []
    for group in settings.benchmark_sets:
        old = next((s for s in before.benchmark_sets
                    if (s.client_brand_id, s.country_code) ==
                    (group.client_brand_id, group.country_code)), None) if before else None
        language = countries[group.country_code].language
        same = old and old.keywords == tuple(group.keywords) and old.language == language
        sets.append(BenchmarkSet(
            **group.model_dump(), language=language,
            version=old.version if same else old.version + 1 if old else 1,
        ))
    ref = ClientRef(load_number=number, snapshot_id=identity, benchmark_sets=tuple(sets),
                    locations=tuple(Location(location_id=c.country_code.lower(),
                                             canonical_name=c.canonical_name,
                                             country_code=c.country_code)
                                    for c in settings.countries))
    row = Snapshot(load_number=number, snapshot_id=identity, applied_at=now,
                   payload=ref.model_dump_json())
    columns = ", ".join(Snapshot.model_fields)
    query(bq, f"INSERT INTO {Snapshot.table} ({columns}) SELECT {columns} FROM UNNEST(@rows)",
          [rows_parameter("rows", Snapshot, [row])])
    return ref


def read_at(bq, load_number: int) -> ClientRef:
    from google.cloud import bigquery

    rows = query(bq, "SELECT payload FROM ops.config_snapshots WHERE load_number = @number",
                 [bigquery.ScalarQueryParameter("number", "INT64", load_number)])
    if len(rows) != 1:
        raise InconsistentRef("the configuration load is missing or repeated")
    return ClientRef.model_validate_json(rows[0]["payload"])
