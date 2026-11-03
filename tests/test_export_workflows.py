from datetime import date, timedelta
from decimal import Decimal

import pytest

from brand_presence.bigquery import Warehouse
from brand_presence.settings import Settings
from brand_presence.sources import search_console_export as gsc
from export_project import DAYS, prepared, queries
from google_project import run
from project import Killed


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
@pytest.mark.parametrize('mixed', [False, True])
def test_full_run_and_repeat(tmp_path, respx_mock, source, mixed):
    project, api = prepared(tmp_path, respx_mock, source, mixed)
    result = run(project, source)
    assert result.status == 'success', result.lines
    assert bool(api.calls) == mixed
    if source == 'ga4':
        rows = project.rows('stg.ga4_export_engagement')
        assert len(rows) == 3
        assert rows[0]['sessions'] == 120
        assert rows[0]['engagement_seconds'] == Decimal('4810.25')
        assert len(project.rows('stg.ga4_export_pages')) == 3
        assert bool(project.rows('stg.ga4_engagement')) == mixed
    else:
        for part in gsc.PARTS:
            row, = project.rows(f'stg.search_console_export_{part}')
            assert row['position_sum'] == project.results[part][0]['position_sum']
        assert project.rows('stg.search_console_export_totals')[0]['anonymized_clicks'] == 2
        assert bool(project.rows('stg.search_console_totals')) == mixed
    before = len(queries(project))
    assert run(project, source).status == 'success'
    assert len(queries(project)) == before + (source == 'search-console')
    for call in queries(project):
        assert call.detail['location'] == 'EU'
        assert call.detail['maximum_bytes_billed'] == 20 * 1024**3


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
@pytest.mark.parametrize('boundary', ['save_raw', 'load_raw', 'replace'])
def test_interrupted_export_recovers_without_duplicates(tmp_path, respx_mock, monkeypatch,
                                                        source, boundary):
    project, api = prepared(tmp_path, respx_mock, source)
    original = getattr(Warehouse, boundary)

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, boundary, killed)
        with pytest.raises(Killed):
            run(project, source)
    project.later()
    result = run(project, source)
    assert result.status == 'success', result.lines
    assert not api.calls
    if source == 'ga4':
        assert len(project.rows('stg.ga4_export_engagement')) == 3
        assert len(project.rows('stg.ga4_export_pages')) == 3
        assert len(queries(project)) == 6
    else:
        assert len(project.rows('stg.search_console_export_totals')) == 1
        assert len([c for c in queries(project) if 'SELECT data_date' in c.subject]) == 4


def test_ga4_saved_month_span_survives_new_daily_tables(tmp_path, respx_mock, monkeypatch):
    project, _ = prepared(tmp_path, respx_mock)
    original = Warehouse.save_raw

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'save_raw', killed)
        with pytest.raises(Killed):
            run(project, 'ga4')
    project.fake.add_dataset('example-export.analytics_100000001', location='EU',
                             tables=['events_20251231', *DAYS])
    project.later()
    result = run(project, 'ga4')
    assert result.status == 'success', result.lines
    assert len(project.rows('stg.ga4_export_engagement')) == 3
    assert len(project.rows('stg.ga4_export_pages')) == 4


def test_search_console_republished_week_replaces_rows(tmp_path, respx_mock):
    project, _ = prepared(tmp_path, respx_mock, 'search-console')
    assert run(project).status == 'success'
    project.epoch = 1
    project.results['totals'][0]['clicks'] = 20
    project.later()
    assert run(project).status == 'success'
    row, = project.rows('stg.search_console_export_totals')
    assert row['clicks'] == 20
    assert len(project.rows('ops.search_console_tasks')) == 2
    assert run(project).counts['requests_sent'] == 0


def test_search_console_full_export_history(tmp_path, respx_mock):
    project, _ = prepared(tmp_path, respx_mock, 'search-console')
    project.first -= timedelta(weeks=70)
    result = run(project, period=None)
    assert result.status == 'success', result.lines
    assert len(project.rows('stg.search_console_pulls')) == 71
    assert min(r['window_start'] for r in project.rows('stg.search_console_pulls')) == project.first


def test_mixed_search_console_history_keeps_api_retention(tmp_path, respx_mock):
    project, api = prepared(tmp_path, respx_mock, 'search-console', mixed=True)
    api.empty = True
    project.first -= timedelta(weeks=70)
    result = run(project, period=None)
    assert result.status == 'success', result.lines
    assert min(call['startDate'] for call in api.calls) == '2024-11-25'
    pulls = project.rows('stg.search_console_pulls')
    assert min(row['window_start'] for row in pulls
               if row['web_property_id'] == 'example-site') == project.first


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
def test_export_queries_use_configured_billing_cap(tmp_path, respx_mock, source):
    project, _ = prepared(tmp_path, respx_mock, source)
    config = project.settings.model_dump()
    config['maximum_bytes_billed'] = 1024**3
    project.settings = Settings.model_validate(config)
    project.bq.settings = project.settings
    assert run(project, source).status == 'success'
    assert queries(project)
    assert all(call.detail['maximum_bytes_billed'] == 1024**3 for call in queries(project))


def test_search_console_readiness_waits_for_both_tables_each_day(tmp_path, respx_mock):
    project, _ = prepared(tmp_path, respx_mock, 'search-console')
    project.missing.add((date(2026, 2, 25), gsc.URL_NAMESPACE))
    assert run(project).status == 'error'
    assert project.rows('ops.search_console_tasks') == []
    project.missing.clear()
    assert run(project).status == 'success'


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
def test_export_dataset_cannot_rebind_a_recorded_property(tmp_path, respx_mock, source):
    project, _ = prepared(tmp_path, respx_mock, source)
    assert run(project, source).status == 'success'
    config = project.settings.model_dump()
    config['web_properties'][0].update(project='different-export')
    project.settings = Settings.model_validate(config)
    result = run(project, source)
    assert result.status == 'error'
    assert any('cannot be rebound' in line for line in result.lines)


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
def test_existing_api_property_can_enable_export(tmp_path, respx_mock, source):
    project, api = prepared(tmp_path, respx_mock, source)
    exported = project.settings
    config = exported.model_dump()
    config['web_properties'][0].update(route='api', project=None, dataset=None)
    project.settings = Settings.model_validate(config)
    project.bq.settings = project.settings
    assert run(project, source).status == 'success'
    api_count = len(api.calls)
    project.settings = exported
    project.bq.settings = exported
    project.later()
    assert run(project, source).status == 'success'
    assert len(api.calls) == api_count
    table = 'stg.ga4_export_engagement' if source == 'ga4' else 'stg.search_console_export_totals'
    assert project.rows(table)


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
@pytest.mark.parametrize('error', ['access', 'statement refused', 'bytes cap'])
def test_export_refusals_are_reported_without_exception_text(tmp_path, respx_mock, source, error):
    from google.api_core.exceptions import BadRequest, Forbidden

    project, api = prepared(tmp_path, respx_mock, source, mixed=True)
    project.export_error = (Forbidden('private error text') if error == 'access' else
                            BadRequest('private error text', errors=[dict(
                                reason='bytesBilledLimitExceeded' if error == 'bytes cap'
                                else 'invalidQuery')]))
    result = run(project, source)
    assert result.status != 'success'
    assert error in result.error_class or error in ' '.join(result.lines)
    assert 'private error text' not in str(result.lines)
    if source == 'ga4':
        assert api.calls
        assert project.rows('stg.ga4_export_engagement') == []


@pytest.mark.parametrize('ending', ['ops', 'raw', 'stg', 'core', 'mart', 'ref'])
@pytest.mark.parametrize('source', ['ga4', 'search-console'])
def test_export_statement_keeps_qualified_identifier(source, ending):
    from brand_presence.sources import ga4_export
    from brand_presence.warehouse import qualify
    from project import configuration

    dataset = f'example-{ending}.analytics_100000001'
    if source == 'ga4':
        sql = ga4_export.statement('export_pages', ga4_export.export_of(dataset),
                                   date(2026, 2, 1), date(2026, 2, 28))
    else:
        sql = gsc.statement(dataset, 'totals')
    assert qualify(sql, Settings.model_validate(configuration())) == sql


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
def test_export_binding_survives_an_intervening_api_load(tmp_path, respx_mock, source):
    from brand_presence import client_ref
    from brand_presence.errors import UserError

    project, _ = prepared(tmp_path, respx_mock, source)
    exported = project.settings
    first = client_ref.apply(project.bq, exported, project.now)
    config = exported.model_dump()
    config['web_properties'][0].update(route='api', project=None, dataset=None)
    api = Settings.model_validate(config)
    second = client_ref.apply(project.bq, api, project.now)
    assert second.load_number == first.load_number + 1
    config = exported.model_dump()
    config['web_properties'][0]['project'] = 'different-export'
    with pytest.raises(UserError, match='cannot be rebound'):
        client_ref.apply(project.bq, Settings.model_validate(config), project.now)
    assert len(project.rows('ops.config_snapshots')) == 2
    third = client_ref.apply(project.bq, exported, project.now)
    assert third.load_number == second.load_number + 1


@pytest.mark.parametrize('period', ['2026-W09', None])
def test_revoked_search_console_dataset_only_fails_export_property(tmp_path, respx_mock, period):
    project, api = prepared(tmp_path, respx_mock, 'search-console', mixed=True)
    project.fake.refuse('get_dataset')
    api.empty = True
    result = run(project, period=period)
    assert result.status == 'partial'
    assert result.error_class == 'access'
    assert api.calls
    assert project.rows('stg.search_console_totals') == []
    pulls = project.rows('stg.search_console_pulls')
    assert pulls and {row['web_property_id'] for row in pulls} == {'api-site'}
    assert project.rows('stg.search_console_export_totals') == []
