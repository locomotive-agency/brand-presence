"""Local configuration. Credentials are read only from the environment."""
from __future__ import annotations

import os
import re
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


class Country(Model):
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}$")]
    canonical_name: Text


class Keyword(Model):
    keyword: Annotated[str, Field(min_length=1, max_length=80)]
    term_type: Literal["brand", "competitor", "generic"]
    brand_id: Id | None = None
    search_volume: bool = True


class BenchmarkSet(Model):
    client_brand_id: Id
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    keywords: list[Keyword] = Field(min_length=1)


class Settings(Model):
    project_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    client_id: Id = "example"
    location: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9-]*$")] = "US"
    time_zone: str = "UTC"
    state_dir: Path = Path(".brand-presence")
    datasets: Datasets = Field(default_factory=Datasets)
    brands: list[Brand] = Field(min_length=1)
    countries: list[Country] = Field(min_length=1)
    benchmark_sets: list[BenchmarkSet] = Field(min_length=1)

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
        for group in self.benchmark_sets:
            if group.client_brand_id not in brands or group.country_code not in countries:
                raise ValueError("a benchmark set names an unlisted brand or country")
            keywords = [k.keyword.casefold() for k in group.keywords]
            if len(keywords) != len(set(keywords)):
                raise ValueError("keywords in a set must be unique, ignoring case")
            for keyword in group.keywords:
                if keyword.term_type == "generic":
                    if keyword.brand_id is not None:
                        raise ValueError("a generic keyword has no brand_id")
                elif keyword.brand_id not in brands:
                    raise ValueError("a brand or competitor keyword needs a listed brand_id")
                elif (keyword.term_type == "brand") != (
                        keyword.brand_id == group.client_brand_id):
                    raise ValueError("a keyword's term type must match its brand")
        if not any(k.search_volume for s in self.benchmark_sets for k in s.keywords):
            raise ValueError("at least one keyword must enable search_volume")
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


def credentials() -> dict[str, str]:
    found = {}
    for name in ("LOGIN", "PASSWORD"):
        value = os.environ.get(f"DATAFORSEO_{name}")
        if value is None or not value.strip():
            raise UserError(f"set DATAFORSEO_{name}")
        found[f"dataforseo-{name.lower()}"] = value
    return found
