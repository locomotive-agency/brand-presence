"""Build the real report SQL over bundled synthetic clean and reference rows."""

import csv
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from brand_presence import schema
from brand_presence.errors import UserError
from brand_presence.example._offline.fakes import FakeBigQuery
from brand_presence.example._offline.reporting import install_sources
from brand_presence.example.report import write_html
from brand_presence.reports import runner as reports_runner
from brand_presence.reports.definition import load
from brand_presence.settings import Settings
from brand_presence.warehouse import Client

BUILD_TIME = datetime(2026, 3, 2, 14, tzinfo=UTC)


def cell(value):
    if value is None:
        return ''
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if isinstance(value, float | Decimal):
        return format(value, '.6f').rstrip('0').rstrip('.') or '0'
    return str(value)


def seed(fake, settings):
    inputs = json.loads(files('brand_presence.example').joinpath('inputs.json').read_text())
    for name, rows in inputs.items():
        table = settings.table(name)
        columns = fake.tables[table.split('.', 1)[1]].columns
        for row in rows:
            for field, kind in columns.items():
                value = row.get(field)
                if value is not None:
                    if kind == 'DATE':
                        row[field] = date.fromisoformat(value)
                    elif kind == 'TIMESTAMP':
                        row[field] = datetime.fromisoformat(value)
                    elif kind == 'NUMERIC':
                        row[field] = Decimal(value)
        fake.put(table, rows)
    fake.put(settings.table('ops.config_snapshots'), [dict(
        snapshot_id='example-snapshot', load_number=1, applied_at=BUILD_TIME, payload='{}',
    )])


def run(output: Path, *, out=print):
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise UserError('Choose a new or empty output directory for the example.')
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.build-', dir=output) as state:
        settings = Settings.model_validate(dict(
            project_id='example-project', client_id='example-preview', state_dir=state,
            brands=[dict(brand_id='aardvark', name='Aardvark Sample')],
            countries=[dict(country_code='NL', language='nl', canonical_name='Netherlands')],
            benchmark_sets=[dict(client_brand_id='aardvark', country_code='NL',
                                 keywords=[dict(keyword='invented kettle', term_type='generic')])],
        ))
        fake = FakeBigQuery(project=settings.project_id, clock=lambda: BUILD_TIME)
        bq = Client(fake, settings)
        schema.initialise(bq)
        seed(fake, settings)
        install_sources(fake, settings)
        reports_runner.run(bq, settings, clock=lambda: BUILD_TIME, out=lambda _: None, source=None)
        total = 0
        for report in load():
            columns = [c['name'] for c in report.columns]
            rows = [[cell(row.get(c)) for c in columns]
                    for row in fake.rows(settings.table(report.table))]
            path = output / f'{report.name}.csv'
            with path.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream, lineterminator='\n')
                writer.writerow(columns)
                writer.writerows(sorted(rows))
            total += len(rows)
            out(f'{report.name}: {len(rows)} rows -> {path}')
        write_html(output)
    out(f'{len(load())} reports, {total} rows. HTML: {output / "report.html"}')
