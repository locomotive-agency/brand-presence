"""Local configuration. Credentials are read only from the environment."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brand_presence.errors import UserError

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Text = Annotated[str, Field(min_length=1, max_length=255)]
Dataset = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, str_strip_whitespace=True)


class Datasets(Model):
    ref: Dataset = "bp_ref"
    ops: Dataset = "bp_ops"
    raw: Dataset = "bp_raw"
    stg: Dataset = "bp_stg"
    core: Dataset = "bp_core"
    mart: Dataset = "bp_mart"

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.model_dump().values())) != len(type(self).model_fields):
            raise ValueError("dataset names must be distinct")
        return self


class Brand(Model):
    brand_id: Id
    name: Text
    aliases: list[Text] = Field(default_factory=list)
    domains: list[Annotated[str, Field(pattern=(
        r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$"
    ))]] = Field(default_factory=list)


class Country(Model):
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]
    canonical_name: Text


class Keyword(Model):
    keyword: Annotated[str, Field(min_length=1, max_length=80)]
    term_type: Literal["brand", "competitor", "generic"]
    brand_id: Id | None = None
    search_volume: bool = True
    canonical_term: Text | None = None


Engine = Literal["chatgpt", "perplexity", "gemini", "ai-overview"]


class Prompt(Model):
    prompt_id: Id
    prompt: Annotated[str, Field(min_length=1, max_length=10000)]
    engines: list[Engine] = Field(min_length=1)
    location_ids: list[Id] = Field(default_factory=list)
    keyword: Text | None = None
    keyword_group: Text | None = None
    intent: Text | None = None

    @model_validator(mode="after")
    def labels(self):
        if len(set(self.engines)) != len(self.engines):
            raise ValueError("prompt engines must be unique")
        if len(set(self.location_ids)) != len(self.location_ids):
            raise ValueError("prompt locations must be unique")
        if any((self.keyword, self.keyword_group, self.intent)) and not all(
            (self.keyword, self.keyword_group, self.intent)
        ):
            raise ValueError("prompt keyword, keyword_group and intent need all three values")
        return self


class Location(Model):
    location_id: Id
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    kind: Literal["region", "city"] = "region"
    name: Text
    canonical_name: Text
    region_code: Annotated[str, Field(pattern=r"^[A-Z]{2}-[A-Z0-9]{1,3}$")] | None = None
    sources: list[Literal["ai-answers", "ga4", "google-ads"]] = Field(
        default_factory=lambda: ["ai-answers", "ga4", "google-ads"])
    google_criterion_id: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")] | None = None

    @model_validator(mode="after")
    def place(self):
        if self.kind == "region" and self.region_code is None:
            raise ValueError("region locations need region_code")
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("location sources must be unique")
        if self.kind == "city" and len(self.canonical_name.split(",")) not in (2, 3):
            raise ValueError("city canonical_name must be City,Country or City,Region,Country")
        return self


class BenchmarkSet(Model):
    client_brand_id: Id
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    keywords: list[Keyword] = Field(default_factory=list)
    competitors: list[Id] = Field(default_factory=list)
    prompts: list[Prompt] = Field(default_factory=list)


class MentionFilter(Model):
    tag: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]
    mention_class: Literal["brand", "competitor", "category", "other"]
    client_brand_id: Id | None = None
    subject_brand_id: Id | None = None
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    keyword_group: Text | None = None


class Subreddit(Model):
    subreddit: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+$")]
    community_group: Text
    subscribers: Annotated[int, Field(ge=0)] | None = None
    subscribers_as_of: date | None = None

    @model_validator(mode="after")
    def dated_count(self):
        if (self.subscribers is None) != (self.subscribers_as_of is None):
            raise ValueError("subscribers and subscribers_as_of need both values")
        return self


class PeecProject(Model):
    mapping: Id
    project_id: Text = Field(repr=False)
    client_brand_id: Id
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    engines: list[Engine] = Field(min_length=1)
    first_date: date


class PeecBrand(Model):
    mapping: Id
    brand_id: Id
    vendor_brand_id: Text = Field(repr=False)


class WebProperty(Model):
    web_property_id: Id
    client_brand_id: Id
    source: Literal["search-console", "ga4"]
    address: Text = Field(repr=False)
    hostname: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def address_for_source(self):
        from urllib.parse import urlsplit

        host_pattern = r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}"
        if self.hostname is not None and not re.fullmatch(host_pattern, self.hostname):
            raise ValueError("hostname must be a lowercase bare domain, without a path")
        if self.source == "ga4":
            if not re.fullmatch(r"[1-9][0-9]*", self.address):
                raise ValueError("GA4 address must be the numeric property ID")
        elif self.address.startswith("sc-domain:"):
            if not re.fullmatch(host_pattern, self.address.removeprefix("sc-domain:")):
                raise ValueError("Search Console domain address must be sc-domain:domain")
        else:
            site = urlsplit(self.address)
            if (site.scheme not in ("https", "http") or not site.hostname
                    or not re.fullmatch(host_pattern, site.hostname)
                    or site.username or site.password or site.query or site.fragment):
                raise ValueError("Search Console address must be a domain or URL-prefix property")
        return self


class AdAccount(Model):
    ad_account_id: Id
    client_brand_id: Id | None = None
    customer_id: Annotated[str, Field(pattern=r"^[0-9]{10}$")] = Field(repr=False)


class Modifier(Model):
    modifier: Text
    match_order: Annotated[int, Field(ge=1)]


class Settings(Model):
    project_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    client_id: Id
    location: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9-]*$")] = "US"
    time_zone: str = "UTC"
    state_dir: Path = Path(".brand-presence")
    datasets: Datasets = Field(default_factory=Datasets)
    brands: list[Brand] = Field(min_length=1)
    countries: list[Country] = Field(min_length=1)
    benchmark_sets: list[BenchmarkSet] = Field(min_length=1)
    locations: list[Location] = Field(default_factory=list)
    mention_filters: list[MentionFilter] = Field(default_factory=list)
    subreddits: list[Subreddit] = Field(default_factory=list)
    mention_text_days: Annotated[int, Field(ge=1, le=366)] = 366
    peec_projects: list[PeecProject] = Field(default_factory=list)
    peec_brands: list[PeecBrand] = Field(default_factory=list)
    web_properties: list[WebProperty] = Field(default_factory=list)
    ad_accounts: list[AdAccount] = Field(default_factory=list)
    modifiers: list[Modifier] = Field(default_factory=list)

    @field_validator("client_id")
    @classmethod
    def own_client_id(cls, value):
        if value == "example":
            raise ValueError("set client_id to your own stable identifier; example is reserved")
        return value

    @field_validator("time_zone")
    @classmethod
    def known_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("time_zone must be an IANA time zone") from None
        return value

    @model_validator(mode="after")
    def references(self):
        brands = {b.brand_id for b in self.brands}
        countries = {c.country_code for c in self.countries}
        sets = {(s.client_brand_id, s.country_code) for s in self.benchmark_sets}
        if len(brands) != len(self.brands) or len(countries) != len(self.countries):
            raise ValueError("brand IDs and country codes must be unique")
        if len(sets) != len(self.benchmark_sets):
            raise ValueError("one benchmark set is allowed per brand and country")
        if len({p.web_property_id for p in self.web_properties}) != len(self.web_properties):
            raise ValueError("web_property_id values must be unique")
        if len({a.ad_account_id for a in self.ad_accounts}) != len(self.ad_accounts):
            raise ValueError("ad_account_id values must be unique")
        if len({a.customer_id for a in self.ad_accounts}) != len(self.ad_accounts):
            raise ValueError("customer_id values must be unique")
        for account in self.ad_accounts:
            if account.client_brand_id is not None and account.client_brand_id not in {
                s.client_brand_id for s in self.benchmark_sets
            }:
                raise ValueError("ad accounts must name a client brand in benchmark_sets")
        criteria = [p.google_criterion_id for p in self.locations if p.google_criterion_id]
        if len(criteria) != len(set(criteria)):
            raise ValueError("google_criterion_id values must be unique")
        if self.ad_accounts:
            for place in self.locations:
                if "google-ads" in place.sources and not place.google_criterion_id:
                    raise ValueError(f"location {place.location_id} needs google_criterion_id "
                                     "for Google Ads; set sources without google-ads to "
                                     "exclude it from Google Ads")
        if len({m.modifier.casefold() for m in self.modifiers}) != len(self.modifiers):
            raise ValueError("modifiers must be unique ignoring case")
        if len({m.match_order for m in self.modifiers}) != len(self.modifiers):
            raise ValueError("modifier match_order values must be unique")
        for property_ in self.web_properties:
            if property_.client_brand_id not in {s.client_brand_id for s in self.benchmark_sets}:
                raise ValueError("web properties must name a client brand in benchmark_sets")
        for source, address in {(p.source, p.address) for p in self.web_properties}:
            shared = [p for p in self.web_properties if (p.source, p.address) == (source, address)]
            if len(shared) > 1 and (any(p.hostname is None for p in shared)
                                   or len({p.hostname for p in shared}) != len(shared)):
                raise ValueError("shared web properties need distinct hostname filters")
        if len({f.tag for f in self.mention_filters}) != len(self.mention_filters):
            raise ValueError("mention filter tags must be unique")
        if len({r.subreddit.casefold() for r in self.subreddits}) != len(self.subreddits):
            raise ValueError("subreddits must be unique ignoring case")
        for item in self.mention_filters:
            if any(b is not None and b not in brands
                   for b in (item.client_brand_id, item.subject_brand_id)):
                raise ValueError("mention filters must name listed brands")
            if item.country_code is not None and item.country_code not in countries:
                raise ValueError("mention filters must name a listed country")
            if item.mention_class in ("brand", "competitor"):
                if not item.client_brand_id or not item.subject_brand_id:
                    raise ValueError("brand and competitor filters need both brand IDs")
                if (item.mention_class == "brand") != (
                        item.client_brand_id == item.subject_brand_id):
                    raise ValueError("mention class must match the filter's brands")
        mappings = {p.mapping: p for p in self.peec_projects}
        if len(mappings) != len(self.peec_projects):
            raise ValueError("Peec mappings must be unique")
        if len({p.project_id for p in self.peec_projects}) != len(self.peec_projects):
            raise ValueError("each Peec project may have only one mapping")
        if len({(p.client_brand_id, p.country_code) for p in self.peec_projects}) != len(
            self.peec_projects
        ):
            raise ValueError("each benchmark set may have only one Peec mapping")
        if any(b.mapping not in mappings for b in self.peec_brands):
            raise ValueError("Peec brands must name a listed mapping")
        for project in self.peec_projects:
            benchmark = next((s for s in self.benchmark_sets if
                              (s.client_brand_id, s.country_code) ==
                              (project.client_brand_id, project.country_code)), None)
            if benchmark is None:
                raise ValueError("Peec project needs a matching benchmark set")
            if len(set(project.engines)) != len(project.engines):
                raise ValueError("Peec project engines must be unique")
            mapped = [b for b in self.peec_brands if b.mapping == project.mapping]
            expected = {benchmark.client_brand_id, *benchmark.competitors}
            if {b.brand_id for b in mapped} != expected or len(mapped) != len(expected):
                raise ValueError("Peec brands must map every benchmark brand exactly once")
            if len({b.vendor_brand_id for b in mapped}) != len(mapped):
                raise ValueError("Peec vendor brand references must be unique within a mapping")
        locations = {c.country_code.lower(): c.country_code for c in self.countries}
        not_for_prompts = {p.location_id for p in self.locations if "ai-answers" not in p.sources}
        from brand_presence.countries import TABLE

        reserved_locations = {code.lower() for code, _, _ in TABLE} | {"unknown", "all", "other"}
        for place in self.locations:
            if place.location_id in reserved_locations:
                raise ValueError("location_id must not use a reserved location ID")
            if place.location_id in locations or place.country_code not in countries:
                raise ValueError("locations need unique IDs and a listed country")
            if place.region_code and not place.region_code.startswith(place.country_code + "-"):
                raise ValueError("region_code must belong to the location country")
            locations[place.location_id] = place.country_code
        for group in self.benchmark_sets:
            if len(set(group.competitors)) != len(group.competitors):
                raise ValueError("competitors must be unique")
            if any(b not in brands or b == group.client_brand_id for b in group.competitors):
                raise ValueError("competitors must name other listed brands")
            if len({p.prompt_id for p in group.prompts}) != len(group.prompts):
                raise ValueError("prompt IDs must be unique within a benchmark set")
            for prompt in group.prompts:
                if any(locations.get(p) != group.country_code for p in prompt.location_ids):
                    raise ValueError("prompt locations must belong to the benchmark country")
                for location_id in prompt.location_ids:
                    if location_id in not_for_prompts:
                        raise ValueError(f"prompt {prompt.prompt_id} names location {location_id}, "
                                         "which does not list ai-answers among its sources")
            if group.client_brand_id not in brands or group.country_code not in countries:
                raise ValueError("a benchmark set names an unlisted brand or country")
            keywords = [k.keyword.casefold() for k in group.keywords]
            if len(keywords) != len(set(keywords)):
                raise ValueError("keywords in a set must be unique, ignoring case")
            for keyword in group.keywords:
                if keyword.canonical_term is not None and keyword.canonical_term not in {
                    k.keyword for k in group.keywords
                }:
                    raise ValueError("canonical_term must name a keyword in the same benchmark set")
                if keyword.term_type == "generic":
                    if keyword.brand_id is not None:
                        raise ValueError("a generic keyword has no brand_id")
                elif keyword.brand_id not in brands:
                    raise ValueError("a brand or competitor keyword needs a listed brand_id")
                elif (keyword.term_type == "brand") != (keyword.brand_id == group.client_brand_id):
                    raise ValueError("a keyword's term type must match its brand")
        if not (self.mention_filters or self.peec_projects or self.web_properties
                or self.ad_accounts) and not any(
            s.prompts or any(k.search_volume for k in s.keywords) for s in self.benchmark_sets
        ):
            raise ValueError("at least one benchmark set must have prompts or enabled keywords")
        return self

    def table(self, logical: str) -> str:
        dataset, name = logical.split(".")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("invalid table name")
        return f"{self.project_id}.{getattr(self.datasets, dataset)}.{name}"

    @property
    def state_path(self) -> Path:
        return self.state_dir / self.project_id / self.datasets.ops


def load(path: Path) -> Settings:
    try:
        content = yaml.safe_load(path.read_text())
    except OSError:
        raise UserError("configuration file could not be read") from None
    except yaml.YAMLError:
        raise UserError("configuration YAML is invalid") from None
    settings = Settings.model_validate(content)
    if not settings.state_dir.is_absolute():
        settings.state_dir = (path.resolve().parent / settings.state_dir).resolve()
    return settings


def credentials(source: str = "search-volume") -> dict[str, str]:
    if source == "google-ads":
        from brand_presence.vendors import google_ads

        found = {}
        for env, key in (("CLIENT_ID", google_ads.CLIENT_ID),
                         ("CLIENT_SECRET", google_ads.CLIENT_SECRET),
                         ("REFRESH_TOKEN", google_ads.REFRESH_TOKEN)):
            value = os.environ.get(f"GOOGLE_ADS_{env}", "").strip()
            if not value:
                raise UserError(f"set GOOGLE_ADS_{env}")
            found[key] = value
        developer = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
        if developer:
            found[google_ads.DEVELOPER_TOKEN] = developer
        login = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip()
        if login:
            login = google_ads.digits_of(login)
            if not re.fullmatch(r"[0-9]{10}", login):
                raise UserError("GOOGLE_ADS_LOGIN_CUSTOMER_ID must have ten digits")
            found[google_ads.LOGIN_CUSTOMER_ID] = login
        return found
    if source in ("search-console", "ga4"):
        return {}
    if source != "search-volume":
        env, key = {"ai-answers": ("CLORO_API_KEY", "cloro-api-key"),
                    "mentions": ("SYFTEN_API_KEY", "syften-api-token"),
                    "peec": ("PEEC_API_KEY", "peec-api-key")}[source]
        value = os.environ.get(env, "")
        if not value.strip():
            raise UserError(f"set {env}")
        return {key: value}
    found = {}
    for name in ("LOGIN", "PASSWORD"):
        value = os.environ.get(f"DATAFORSEO_{name}")
        if value is None or not value.strip():
            raise UserError(f"set DATAFORSEO_{name}")
        found[f"dataforseo-{name.lower()}"] = value
    return found
