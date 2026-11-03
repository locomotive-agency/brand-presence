"""Immutable configuration loads used to parse recovered answers under their original settings."""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from brand_presence import countries, geo_targets, ref_rows
from brand_presence.bigquery import Transaction, query, rows_parameter
from brand_presence.errors import UserError
from brand_presence.request_keys import content_hash
from brand_presence.rows import Row
from brand_presence.settings import (
    AdAccount,
    Brand,
    Keyword,
    MentionFilter,
    Modifier,
    NamedPage,
    PeecBrand,
    PeecProject,
    Prompt,
    Settings,
    Subreddit,
    WebProperty,
)


class InconsistentRef(RuntimeError):
    pass


class Location(BaseModel):
    location_id: str
    canonical_name: str
    country_code: str
    sources: tuple[str, ...] = ("search-volume", "ai-answers", "peec", "search-console",
                                "ga4", "google-ads")
    kind: str = "country"
    name: str = ""
    region_code: str | None = None
    google_criterion_id: str | None = None


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
    web_properties: tuple[WebProperty, ...] = ()
    ad_accounts: tuple[AdAccount, ...] = ()
    modifiers: tuple[Modifier, ...] = ()
    pages: tuple[NamedPage, ...] = ()
    geo_targets_date: str | None = None

    def web_properties_of(self, source):
        return tuple(p for p in self.web_properties if p.source == source)

    @property
    def listed_locations(self):
        return tuple(p for p in self.locations if p.sources)

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
    targets_date = geo_targets.validate(settings)
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
            "web_properties",
            "ad_accounts",
            "modifiers",
            "pages",
        },
    )
    content["geo_targets_date"] = targets_date
    identity = content_hash(content)
    held = query(bq, "SELECT * FROM ops.config_snapshots ORDER BY load_number DESC")
    if held and held[0]["snapshot_id"] == identity:
        return ClientRef.model_validate_json(held[0]["payload"])
    number = held[0]["load_number"] + 1 if held else 1
    before = ClientRef.model_validate_json(held[0]["payload"]) if held else None
    for snapshot in held:
        previous = ClientRef.model_validate_json(snapshot["payload"])
        for collection, field, label in (("web_properties", "web_property_id", "Web property"),
                                          ("ad_accounts", "ad_account_id", "Ad account")):
            current_items = {getattr(item, field): item for item in getattr(settings, collection)}
            for old in getattr(previous, collection):
                current = current_items.get(getattr(old, field))
                if current is None:
                    continue
                if collection == "web_properties":
                    binding = ("source", "address", "hostname", "client_brand_id")
                    changed = any(getattr(current, key) != getattr(old, key) for key in binding)
                    changed |= (current.route == old.route == "export" and
                                (current.project, current.dataset) != (old.project, old.dataset))
                else:
                    changed = current != old
                if changed:
                    raise UserError(f"{label} ID cannot be rebound; use a new local ID")
        for old in previous.peec_projects:
            for current in settings.peec_projects:
                if old.mapping == current.mapping and (
                    old.project_id,
                    old.client_brand_id,
                    old.country_code,
                ) != (current.project_id, current.client_brand_id, current.country_code):
                    raise UserError("Peec mapping cannot be rebound to another project or market")
    highest = {}
    for snapshot in held:
        for group in ClientRef.model_validate_json(snapshot["payload"]).benchmark_sets:
            key = (group.client_brand_id, group.country_code)
            highest[key] = max(highest.get(key, 0), group.version)
    listed_countries = {c.country_code: c for c in settings.countries}
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
        language = listed_countries[group.country_code].language
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
        web_properties=tuple(settings.web_properties),
        ad_accounts=tuple(settings.ad_accounts),
        modifiers=tuple(settings.modifiers),
        pages=tuple(settings.pages),
        geo_targets_date=targets_date,
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
        + tuple(Location(**p.model_dump()) for p in settings.locations)
        + tuple(Location(location_id=code.lower(), country_code=code, canonical_name=code,
                         sources=())
                for code, _, _ in countries.TABLE
                if code not in listed_countries
                and (settings.web_properties or settings.ad_accounts))
        + tuple(Location(location_id=code, country_code=code, canonical_name=code, sources=())
                for code in ("unknown", "all") if settings.web_properties or settings.ad_accounts),
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
    rows += [ref_rows.WebProperty(load_number=number, **p.model_dump()) for p in ref.web_properties]
    rows += [ref_rows.AdAccount(load_number=number, **a.model_dump()) for a in ref.ad_accounts]
    rows += [ref_rows.Modifier(load_number=number, **m.model_dump()) for m in ref.modifiers]
    rows += [ref_rows.NamedPage(load_number=number, **p.model_dump()) for p in ref.pages]
    if any(a.client_brand_id is None for a in ref.ad_accounts):
        rows.append(ref_rows.Brand(load_number=number, brand_id="unknown", name="Unknown",
                                   kind="reserved", aliases=[], domains=[]))
    if ref.web_properties or ref.ad_accounts:
        listed = {c.country_code for c in settings.countries}
        codes = [code for code, _, _ in countries.TABLE] + ["unknown", "all"]
        rows += [ref_rows.Country(load_number=number, country_code=code,
                                  language="und", listed=False)
                 for code in codes if code not in listed]
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
