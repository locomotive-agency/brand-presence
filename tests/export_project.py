"""Invented export aggregates through the BigQuery stand-in and real runner."""
import copy
from datetime import date, timedelta
from decimal import Decimal

import httpx

from brand_presence import geo_targets
from brand_presence.settings import Settings
from brand_presence.sources import search_console_export as gsc
from google_project import prepared as google_project
from test_geo_targets_setup import archive

DATASET = 'example-export.fixture_export'
DAYS = ['events_20260131', 'events_20260228', 'events_20260301']


def prepared(tmp_path, router, source='ga4', mixed=False):
    project, api = google_project(tmp_path, router, source)
    config = project.settings.model_dump()
    prop = config['web_properties'][0]
    if mixed:
        config['web_properties'].append(dict(prop, web_property_id='api-site',
                                             address='100000002' if source == 'ga4' else
                                             'sc-domain:badger.example'))
    prop.update(route='export', project='example-export', dataset='fixture_export')
    config['pages'] = [dict(page_path='/example', page_group='Example')]
    project.settings = Settings.model_validate(config)
    project.bq.settings = project.settings
    if source == 'ga4':
        router.get(geo_targets.FILES + 'geotargets-2026-01-01.csv.zip').respond(content=archive())
        with httpx.Client() as http:
            geo_targets.download(project.settings, '2026-01-01', http)
    project.fake.add_dataset(DATASET, location='EU', tables=DAYS if source == 'ga4' else
                             [*gsc.TABLES, 'ExportLog'])
    project.results = {
        'export_engagement': [dict(location_id='nl', geo_level='national', channel_group='(all)',
                                   sessions=120, active_users=80, total_users=95, new_users=60,
                                   returning_users=40, page_views=240,
                                   engagement_seconds=Decimal('4810.25'), ambiguous_days=0)],
        'export_pages': [dict(page_path='/example', page_views=80, users=50,
                              engagement_seconds=Decimal('250'))],
        'totals': [dict(country='nld', clicks=12, impressions=100, position_sum=200,
                        position=3.0, anonymized_clicks=2, anonymized_impressions=20)],
        'queries': [dict(country='nld', query='Aardvark', clicks=10, impressions=80,
                         position_sum=160, position=3.0)],
        'pages': [dict(country='nld', page='https://aardvark.example/', clicks=12,
                       impressions=100, position_sum=300, position=4.0)],
        'devices': [dict(country='nld', device='DESKTOP', clicks=12, impressions=100,
                         position_sum=200, position=3.0)],
    }
    project.export_error = None

    def aggregate(name):
        if project.export_error and name == 'export_engagement':
            raise project.export_error
        return copy.deepcopy(project.results[name])

    for name, pattern in [('export_engagement', '@places'), ('export_pages', '@most_pages')]:
        project.fake.on(pattern, lambda _, __, ___, name=name: aggregate(name))
    project.epoch = 0
    project.first = date(2026, 2, 23)
    project.missing = set()

    def ready(_, __, given):
        if project.export_error:
            raise project.export_error
        first, last = (given[n] for n in ('first_date', 'last_date'))
        return [dict(agenda='SEARCHDATA', namespace=namespace, data_date=first + timedelta(days=n),
                     epoch_version=project.epoch, publish_time=project.now)
                for n in range((last - first).days + 1) for namespace in gsc.NAMESPACES
                if (first + timedelta(days=n), namespace) not in project.missing]

    project.fake.on('SELECT agenda, namespace', ready)
    project.fake.on(r'SELECT MIN\(data_date\)', lambda *_: [dict(first_date=project.first)])
    for part in gsc.PARTS:
        project.fake.on({'totals': r'country,\s+SUM', 'queries': 'country, query',
                         'pages': 'country, url AS page',
                         'devices': r'country, UPPER\(device\)'}[part],
                        lambda _, __, given, part=part: [dict(date=given['first_date'], **r)
                                                       for r in aggregate(part)])
    return project, api


def queries(project):
    return [c for c in project.fake.journal.of('bigquery', 'query') if DATASET in c.subject]
