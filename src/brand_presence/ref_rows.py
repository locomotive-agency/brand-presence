"""Reference rows ported from the source contracts."""

from datetime import date
from typing import ClassVar

from pydantic import Field

from brand_presence.rows import Row


class Brand(Row):
    table: ClassVar[str] = "ref.brands"

    load_number: int
    brand_id: str
    name: str
    kind: str
    domains: list[str]
    aliases: list[str]


class Country(Row):
    table: ClassVar[str] = "ref.countries"

    load_number: int
    country_code: str
    language: str
    # Whether the configuration lists the country. False for one the loader adds and for the
    # reserved ones; null on a row written before the column, which the configuration listed.
    listed: bool | None = None


class Location(Row):
    table: ClassVar[str] = "ref.locations"

    load_number: int
    location_id: str
    country_code: str
    kind: str
    name: str
    canonical_name: str
    region_code: str | None
    sources: list[str]
    # Google's criterion ID for a city or a region, digits alone; null on any other location
    # and where the configuration gave none.
    google_criterion_id: str | None = None
    # Decimal degrees, both or neither, for a map; no source reads them.
    latitude: float | None = None
    longitude: float | None = None


class BenchmarkSetRow(Row):
    table: ClassVar[str] = "ref.benchmark_sets"

    load_number: int
    client_brand_id: str
    country_code: str
    benchmark_version: int
    content_hash: str


class CompetitorRow(Row):
    table: ClassVar[str] = "ref.benchmark_competitors"

    load_number: int
    client_brand_id: str
    country_code: str
    benchmark_version: int
    brand_id: str


class Keyword(Row):
    table: ClassVar[str] = "ref.benchmark_keywords"

    load_number: int
    client_brand_id: str
    country_code: str
    benchmark_version: int
    keyword: str
    term_type: str
    brand_id: str | None
    search_volume: bool
    search_trends: bool
    search_results: bool
    trends_anchor: bool
    # The keyword of the same set this one rolls up to, as a misspelling does; null where
    # the keyword is its own.
    canonical_term: str | None = None


class Prompt(Row):
    table: ClassVar[str] = "ref.benchmark_prompts"

    load_number: int
    client_brand_id: str
    country_code: str
    benchmark_version: int
    prompt_id: str
    prompt: str
    engines: list[str]
    location_ids: list[str]
    # The keyword the prompt stands for, its group and its intent, all three or none; null
    # when not configured. Sources do not use them to plan requests.
    keyword: str | None = None
    keyword_group: str | None = None
    intent: str | None = None


class MentionFilter(Row):
    table: ClassVar[str] = "ref.mention_filters"

    load_number: int
    tag: str
    mention_class: str
    client_brand_id: str | None
    subject_brand_id: str | None
    country_code: str | None
    # One of the prompts' keyword groups, which mentions stores on each match of the tag;
    # null where the filter names none.
    keyword_group: str | None = None

class Subreddit(Row):
    """One configured subreddit, community group and subscriber count.
    Available through ref.current_subreddits; collection does not read it."""

    table: ClassVar[str] = "ref.subreddits"

    load_number: int
    # Without `r/`, as typed. Matched to a mention's subreddit without regard to case.
    subreddit: str
    community_group: str
    # Both or neither.
    subscribers: int | None
    subscribers_as_of: date | None


class PeecProject(Row):
    table: ClassVar[str] = "ref.peec_projects"

    load_number: int
    mapping: str
    project_id: str = Field(repr=False)
    client_brand_id: str
    country_code: str
    engines: list[str]
    first_date: date

class PeecBrand(Row):
    table: ClassVar[str] = "ref.peec_brands"

    load_number: int
    mapping: str
    brand_id: str
    vendor_brand_id: str = Field(repr=False)
