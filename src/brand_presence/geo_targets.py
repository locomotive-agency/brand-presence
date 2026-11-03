"""Dated local geo targets and canonical place validation."""
from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

COLUMNS = ("Criteria ID", "Name", "Canonical Name", "Parent ID", "Country Code",
           "Target Type", "Status")
ACTIVE = "Active"


CITY_TYPES = ("City",)
REGION_TYPES = ("Autonomous Community", "Canton", "County", "Department", "Division",
                "Governorate", "Okrug", "Prefecture", "Province", "Region", "State",
                "Territory", "Union Territory")
COUNTRY_TYPES = ("Country",)
KEPT_TYPES = (*COUNTRY_TYPES, *CITY_TYPES, *REGION_TYPES)


class GeoTargetsUnreadable(Exception):
    pass


def name_parts(canonical_name: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in canonical_name.split(","))


def country_name(canonical_name: str) -> str:
    return canonical_name.strip().lower()


@dataclass(frozen=True)
class Target:
    criterion_id: str
    name: str
    canonical_name: str
    parent_id: str
    country_code: str
    target_type: str
    status: str

    @property
    def is_country(self) -> bool:
        return self.target_type in COUNTRY_TYPES or not self.parent_id

    @property
    def active(self) -> bool:
        return self.status == ACTIVE


@dataclass(frozen=True)
class Header:
    page: str
    file: str
    date: str
    download_sha256: str
    rows: int
    sha256: str


class GeoTargets:
    def __init__(self, header: Header, targets: Iterable[Target]):
        self.header = header
        self.by_id: dict[str, Target] = {}
        self.by_name: dict[tuple[str, tuple[str, ...]], list[Target]] = {}
        self.by_country: dict[str, list[Target]] = {}
        for target in targets:
            if target.is_country:
                self.by_country.setdefault(target.country_code, []).append(target)
            self.by_id[target.criterion_id] = target
            self.by_name.setdefault(
                (target.country_code, name_parts(target.canonical_name)), []).append(target)

    @property
    def date(self) -> str:
        return self.header.date

    def named(self, country_code: str, canonical_name: str) -> list[Target]:
        return self.by_name.get((country_code, name_parts(canonical_name)), [])

    def country(self, country_code: str) -> list[Target]:
        return self.by_country.get(country_code, [])


def kept(rows: Iterable[Sequence[str]]) -> list[list[str]]:
    return [list(row) for row in rows
            if row[COLUMNS.index("Target Type")] in KEPT_TYPES
            or not row[COLUMNS.index("Parent ID")]]


def write(rows: Iterable[Sequence[str]], path: Path) -> tuple[int, str]:
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(COLUMNS)
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
    return count, hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path, header: Path) -> GeoTargets:
    try:
        said = Header(**json.loads(header.read_text(encoding="utf-8")))
        raw = path.read_bytes()
    except (OSError, ValueError, TypeError):
        raise GeoTargetsUnreadable(header.name) from None
    if hashlib.sha256(raw).hexdigest() != said.sha256:
        raise GeoTargetsUnreadable(path.name)
    reader = csv.reader(raw.decode("utf-8").splitlines())
    if tuple(next(reader, ())) != COLUMNS:
        raise GeoTargetsUnreadable(path.name)
    targets = []
    for row in reader:
        if len(row) != len(COLUMNS) or not kept([row]):
            raise GeoTargetsUnreadable(path.name)
        targets.append(Target(*row))
    try:
        _valid_rows([list(vars(t).values()) for t in targets])
    except ValueError:
        raise GeoTargetsUnreadable(path.name) from None
    if len(targets) != said.rows:
        raise GeoTargetsUnreadable(path.name)
    return GeoTargets(said, targets)


PAGE = 'https://developers.google.com/google-ads/api/data/geotargets'
FILES = 'https://developers.google.com/static/google-ads/api/data/geo/'
MAX_DOWNLOAD = 32 * 1024**2
MAX_CSV = 128 * 1024**2


def _valid_rows(rows):
    import re

    ids = set()
    for row in rows:
        if (len(row) != len(COLUMNS) or not re.fullmatch(r'[1-9][0-9]*', row[0])
                or row[0] in ids or not row[1] or not row[2]
                or (row[3] and not re.fullmatch(r'[1-9][0-9]*', row[3]))
                or not re.fullmatch(r'[A-Z]{2}', row[4]) or not row[5]
                or row[6] not in ('Active', 'Removal Planned')):
            raise ValueError('invalid geo target row')
        ids.add(row[0])
    if not rows:
        raise ValueError('empty geo targets')


def download(settings, dated: str, http) -> GeoTargets:
    import io
    import os
    import zipfile
    from datetime import date

    import httpx

    from brand_presence.errors import UserError
    from brand_presence.state import write_once

    try:
        if date.fromisoformat(dated).isoformat() != dated:
            raise ValueError('invalid date')
        url = FILES + f'geotargets-{dated}.csv.zip'
        raw = bytearray()
        with http.stream('GET', url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_DOWNLOAD:
                    raise ValueError('download too large')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = archive.infolist()
            if (len(files) != 1 or files[0].filename != f'geotargets-{dated}.csv'
                    or files[0].file_size > MAX_CSV):
                raise ValueError('unexpected archive contents')
            text = archive.read(files[0]).decode('utf-8')
        reader = csv.reader(io.StringIO(text, newline=''))
        if tuple(next(reader, ())) != COLUMNS:
            raise ValueError('unexpected columns')
        rows = list(reader)
        _valid_rows(rows)
        rows = kept(rows)
        _valid_rows(rows)
    except (ValueError, UnicodeError, zipfile.BadZipFile, httpx.HTTPError):
        raise UserError('Geo targets download failed validation; previous copy kept') from None
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    digest = hashlib.sha256(stream.getvalue().encode()).hexdigest()
    directory = settings.state_path / 'geo-targets'
    path = directory / f'{digest}.csv'
    write_once(path, stream.getvalue())
    header = Header(PAGE, url, dated, hashlib.sha256(raw).hexdigest(), len(rows), digest)
    manifest = directory / 'current.json'
    temporary = directory / 'current.pending'
    from dataclasses import asdict

    try:
        with temporary.open('w') as out:
            out.write(json.dumps(asdict(header), indent=2) + '\n')
            out.flush()
            os.fsync(out.fileno())
        read(path, temporary)
        os.replace(temporary, manifest)
    finally:
        temporary.unlink(missing_ok=True)
    return local(settings)


def local(settings) -> GeoTargets:
    import re

    directory = settings.state_path / 'geo-targets'
    manifest = directory / 'current.json'
    try:
        digest = json.loads(manifest.read_text())['sha256']
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('invalid digest')
        return read(directory / f'{digest}.csv', manifest)
    except (OSError, ValueError, KeyError, TypeError, GeoTargetsUnreadable):
        raise GeoTargetsUnreadable('local geo targets') from None


def validate(settings) -> str | None:
    from brand_presence.errors import UserError

    if not any(p.source == 'ga4' and p.route == 'export' for p in settings.web_properties):
        return None
    try:
        held = local(settings)
    except GeoTargetsUnreadable:
        raise UserError('GA4 exports need setup-geo-targets before collection') from None
    for country in settings.countries:
        if not any(t.active and country_name(t.canonical_name) ==
                   country_name(country.canonical_name)
                   for t in held.country(country.country_code)):
            raise UserError(f'Country {country.country_code} does not match active geo targets')
    for place in settings.locations:
        if 'ga4' not in place.sources:
            continue
        types = CITY_TYPES if place.kind == 'city' else REGION_TYPES
        fitting = [t for t in held.named(place.country_code, place.canonical_name)
                   if t.active and t.target_type in types]
        if not fitting or (place.google_criterion_id and not any(
            t.criterion_id == place.google_criterion_id for t in fitting
        )):
            raise UserError(f'Location {place.location_id} does not match active geo targets')
    return held.date
