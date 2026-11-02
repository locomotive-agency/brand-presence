"""Immutable configuration loads used to parse recovered answers under their original settings."""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from brand_presence import ref_rows
from brand_presence.bigquery import Transaction, query, rows_parameter
from brand_presence.request_keys import content_hash
from brand_presence.rows import Row
from brand_presence.settings import (
    Brand,
    Keyword,
    MentionFilter,
    PeecBrand,
    PeecProject,
    Prompt,
    Settings,
    Subreddit,
)


class InconsistentRef(RuntimeError):
    pass


class Location(BaseModel):
    location_id: str
    canonical_name: str
    country_code: str
    sources: tuple[str, ...] = ("search-volume", "ai-answers", "peec")
    kind: str = "country"
    name: str = ""
    region_code: str | None = None


class BenchmarkSet(BaseModel):
    client_brand_id: str
    country_code: str
    version: int
    language: str
    keywords: tuple[Keyword, ...]
    competitors: tuple[Brand, ...] = ()
    prompts: tuple[Prompt, ...] = ()


class ClientRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    load_number: int
    snapshot_id: str
    benchmark_sets: tuple[BenchmarkSet, ...]
    locations: tuple[Location, ...]
    brands: tuple[Brand, ...] = ()
    mention_filters: tuple[MentionFilter, ...] = ()
    subreddits: tuple[Subreddit, ...] = ()
    peec_projects: tuple[PeecProject, ...] = ()
    peec_brands: tuple[PeecBrand, ...] = ()

    def brand(self, brand_id):
        return next((b for b in self.brands if b.brand_id == brand_id), None)

    def location(self, location_id):
        return next((p for p in self.locations if p.location_id == location_id), None)


class Snapshot(Row):
    table: ClassVar[str] = "ops.config_snapshots"
    load_number: int
    snapshot_id: str
    applied_at: datetime
    payload: str


def apply(bq, settings: Settings, now: datetime) -> ClientRef:
    content = settings.model_dump(
        mode="json",
        include={
            "brands",
            "countries",
            "benchmark_sets",
            "locations",
            "mention_filters",
            "subreddits",
            "mention_text_days",
            "peec_projects",
            "peec_brands",
        },
    )
    identity = content_hash(content)
    held = query(bq, "SELECT * FROM ops.config_snapshots ORDER BY load_number DESC")
    if held and held[0]["snapshot_id"] == identity:
        return ClientRef.model_validate_json(held[0]["payload"])
    number = held[0]["load_number"] + 1 if held else 1
    before = ClientRef.model_validate_json(held[0]["payload"]) if held else None
    for snapshot in held:
        previous = ClientRef.model_validate_json(snapshot["payload"])
        for old in previous.peec_projects:
            for current in settings.peec_projects:
                if old.mapping == current.mapping and (
                    old.project_id,
                    old.client_brand_id,
                    old.country_code,
                ) != (current.project_id, current.client_brand_id, current.country_code):
                    from brand_presence.errors import UserError

                    raise UserError("Peec mapping cannot be rebound to another project or market")
    highest = {}
    for snapshot in held:
        for group in ClientRef.model_validate_json(snapshot["payload"]).benchmark_sets:
            key = (group.client_brand_id, group.country_code)
            highest[key] = max(highest.get(key, 0), group.version)
    countries = {c.country_code: c for c in settings.countries}
    brands = {b.brand_id: b for b in settings.brands}
    sets = []
    for group in settings.benchmark_sets:
        old = (
            next(
                (
                    s
                    for s in before.benchmark_sets
                    if (s.client_brand_id, s.country_code)
                    == (group.client_brand_id, group.country_code)
                ),
                None,
            )
            if before
            else None
        )
        language = countries[group.country_code].language
        same = (
            old
            and old.keywords == tuple(group.keywords)
            and old.language == language
            and (not before.brands
                 or before.brand(group.client_brand_id) == brands[group.client_brand_id])
            and old.prompts == tuple(group.prompts)
            and old.competitors == tuple(brands[b] for b in group.competitors)
        )
        sets.append(
            BenchmarkSet(
                **group.model_dump(exclude={"competitors"}),
                language=language,
                competitors=tuple(brands[b] for b in group.competitors),
                version=old.version
                if same
                else highest.get((group.client_brand_id, group.country_code), 0) + 1,
            )
        )
    ref = ClientRef(
        load_number=number,
        snapshot_id=identity,
        benchmark_sets=tuple(sets),
        brands=tuple(settings.brands),
        peec_projects=tuple(settings.peec_projects),
        peec_brands=tuple(settings.peec_brands),
        mention_filters=tuple(settings.mention_filters),
        subreddits=tuple(settings.subreddits),
        locations=tuple(
            Location(
                location_id=c.country_code.lower(),
                canonical_name=c.canonical_name,
                country_code=c.country_code,
                name=c.canonical_name,
            )
            for c in settings.countries
        )
        + tuple(Location(**p.model_dump()) for p in settings.locations),
    )
    row = Snapshot(
        load_number=number, snapshot_id=identity, applied_at=now, payload=ref.model_dump_json()
    )
    columns = ", ".join(Snapshot.model_fields)
    with Transaction(bq) as transaction:
        transaction.run(
            f"INSERT INTO {Snapshot.table} ({columns}) SELECT {columns} FROM UNNEST(@rows)",
            [rows_parameter("rows", Snapshot, [row])],
        )
        write_refs(transaction, settings, ref, before)

    return ref


def read_at(bq, load_number: int) -> ClientRef:
    from google.cloud import bigquery

    rows = query(
        bq,
        "SELECT payload FROM ops.config_snapshots WHERE load_number = @number",
        [bigquery.ScalarQueryParameter("number", "INT64", load_number)],
    )
    if len(rows) != 1:
        raise InconsistentRef("the configuration load is missing or repeated")
    return ClientRef.model_validate_json(rows[0]["payload"])


def write_refs(transaction, settings, ref, before):
    number = ref.load_number
    own = {s.client_brand_id for s in settings.benchmark_sets}
    rows = [
        ref_rows.Brand(
            load_number=number,
            kind="client" if b.brand_id in own else "competitor",
            **b.model_dump(),
        )
        for b in settings.brands
    ]
    rows += [
        ref_rows.Country(
            load_number=number, country_code=c.country_code, language=c.language, listed=True
        )
        for c in settings.countries
    ]
    rows += [
        ref_rows.Location(load_number=number, **p.model_dump(mode="json")) for p in ref.locations
    ]
    rows += [
        ref_rows.MentionFilter(load_number=number, **f.model_dump()) for f in ref.mention_filters
    ]
    rows += [ref_rows.Subreddit(load_number=number, **r.model_dump()) for r in ref.subreddits]
    rows += [ref_rows.PeecProject(load_number=number, **p.model_dump()) for p in ref.peec_projects]
    rows += [ref_rows.PeecBrand(load_number=number, **b.model_dump()) for b in ref.peec_brands]
    for group in ref.benchmark_sets:
        standard = dict(
            load_number=number,
            client_brand_id=group.client_brand_id,
            country_code=group.country_code,
            benchmark_version=group.version,
        )
        rows.append(
            ref_rows.BenchmarkSetRow(**standard, content_hash=content_hash(group.model_dump()))
        )
        if before and any(
            (s.client_brand_id, s.country_code, s.version)
            == (group.client_brand_id, group.country_code, group.version)
            for s in before.benchmark_sets
        ):
            continue
        rows += [ref_rows.CompetitorRow(**standard, brand_id=b.brand_id) for b in group.competitors]
        rows += [ref_rows.Prompt(**standard, **p.model_dump()) for p in group.prompts]
        rows += [
            ref_rows.Keyword(
                **standard,
                **k.model_dump(),
                search_trends=False,
                search_results=False,
                trends_anchor=False,
            )
            for k in group.keywords
        ]
    for model in dict.fromkeys(type(row) for row in rows):
        columns = ", ".join(model.model_fields)
        transaction.run(
            f"INSERT INTO {model.table} ({columns}) SELECT {columns} FROM UNNEST(@rows)",
            [rows_parameter("rows", model, [r for r in rows if type(r) is model])],
        )
