from decimal import Decimal

import pytest

from brand_presence.bigquery import Warehouse
from google_project import prepared, run
from project import Killed


def test_ga4_full_run_and_repeat(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock, 'ga4')
    result = run(project, 'ga4')
    assert result.status == 'success', result.lines
    rows = project.rows('stg.ga4_engagement')
    assert len(rows) == 42
    assert rows[0]['sessions'] == Decimal('40')
    assert len(project.rows('stg.ga4_pulls')) == 2
    count = len(vendor.calls)
    assert run(project, 'ga4').status == 'success'
    assert len(vendor.calls) == count
    assert len(project.rows('stg.ga4_engagement')) == 42


@pytest.mark.parametrize('boundary', ['save_raw', 'load_raw', 'replace'])
def test_ga4_interrupted_resume(tmp_path, respx_mock, monkeypatch, boundary):
    project, vendor = prepared(tmp_path, respx_mock, 'ga4')
    original = getattr(Warehouse, boundary)

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, boundary, killed)
        with pytest.raises(Killed):
            run(project, 'ga4')
    project.later()
    assert run(project, 'ga4').status == 'success'
    assert len(vendor.calls) == 2
    assert len(project.rows('stg.ga4_engagement')) == 42
    assert len(project.rows('ops.ga4_tasks')) == 2


@pytest.mark.parametrize('name,field,expected', [
    ('sampled', 'sampled', True), ('thresholded', 'thresholded', True),
    ('other_row', 'other_row', True), ('empty_cut', 'data_cut', True),
    ('tokens_six', 'tokens_used', 6),
])
def test_ga4_pull_quality_flags(tmp_path, respx_mock, name, field, expected):
    project, vendor = prepared(tmp_path, respx_mock, 'ga4')
    vendor.answers['engagement'] = name
    assert run(project, 'ga4').status == 'success'
    pull = next(r for r in project.rows('stg.ga4_pulls') if r['report'] == 'engagement')
    assert pull[field] == expected


@pytest.mark.parametrize('name', ['restricted_metric', 'date_outside_window'])
def test_ga4_unusable_answer_stays_raw(tmp_path, respx_mock, name):
    project, vendor = prepared(tmp_path, respx_mock, 'ga4')
    vendor.answers['engagement'] = name
    assert run(project, 'ga4').status == 'partial'
    assert project.rows('stg.ga4_engagement') == []
    assert len(project.rows('raw.ga4')) == 2


def test_ga4_query_string_is_not_written_clean(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock, 'ga4')
    vendor.answers['landing_pages'] = 'landing_pages_query_string'
    assert run(project, 'ga4').status == 'partial'
    assert project.rows('stg.ga4_landing_pages') == []


@pytest.mark.parametrize('address', ['sc-domain:example.example', '123/../456', 'example', ''])
def test_ga4_validates_property_number(tmp_path, respx_mock, address):
    from brand_presence.settings import Settings

    project, _ = prepared(tmp_path, respx_mock, 'ga4')
    config = project.settings.model_dump()
    config['web_properties'][0]['address'] = address
    with pytest.raises(ValueError):
        Settings.model_validate(config)


def test_ga4_listed_city_and_other_cities(tmp_path, respx_mock):
    from brand_presence.settings import Settings

    project, vendor = prepared(tmp_path, respx_mock, 'ga4')
    config = project.settings.model_dump()
    config['countries'].append(dict(country_code='PT', language='pt', canonical_name='Portugal'))
    config['locations'] = [dict(location_id='porto', kind='city', country_code='PT',
                               name='Porto', canonical_name='Porto,Porto District,Portugal',
                               sources=['ga4'])]
    project.settings = Settings.model_validate(config)
    assert run(project, 'ga4').status == 'success'
    assert len(vendor.calls) == 3
    rows = project.rows('stg.ga4_engagement_cities')
    listed = next(r for r in rows if r['city_id'] == 'porto')
    assert listed['sessions'] == Decimal('30')
    other = next(r for r in rows if r['city_id'] != 'porto')
    assert other['sessions'] == Decimal('60')
    assert other['total_users'] is None and other['new_users'] is None


def test_ga4_report_known_result_and_checks(tmp_path, respx_mock):
    from brand_presence.reports.definition import load
    from brand_presence.schema import MODELS
    from reporting import evaluate

    project, _ = prepared(tmp_path, respx_mock, 'ga4')
    assert run(project, 'ga4').status == 'success'
    report, = load('ga4')
    models = {m.table: m for m in MODELS}
    tables = {}
    for table in report.definition['reads']:
        original = table.replace('ref.current_', 'ref.')
        tables[table] = (list(models[original].model_fields), project.rows(original))
    rows = evaluate(report.statement, tables)
    direct = next(r for r in rows if r['country_code'] == 'NL'
                  and r['channel_group'] == 'Direct' and r['month'] == '2026-02-01')
    assert direct['sessions'] == 285
    assert direct['page_views'] == 870
    assert direct['days_covered'] == 6
    assert direct['sampled'] == 0
    assert all('total_users' not in r for r in rows)
    rows = [dict(r, pulled_at=project.now) for r in rows]
    tables[report.table] = (list(rows[0]), rows)
    for _, sql in report.checks:
        assert evaluate(sql, tables, {'pulled_at': project.now}) == []


def test_ga4_partial_page_resume_uses_saved_offset_and_original_load(
    tmp_path, respx_mock, monkeypatch,
):
    from google_project import fixture

    project, vendor = prepared(tmp_path, respx_mock, 'ga4')
    page = fixture('ga4', 'engagement')
    vendor.answers['engagement'] = [
        {**page, 'rows': [page['rows'][0]], 'rowCount': 100001},
        {**page, 'rows': [page['rows'][2]], 'rowCount': 100001},
    ]
    original = Warehouse.save_raw

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'save_raw', killed)
        with pytest.raises(Killed):
            run(project, 'ga4')
    snapshot = project.rows('ops.config_snapshots')[0]['snapshot_id']
    project.settings.brands[0].name = 'Different Invented Brand'
    project.later()
    assert run(project, 'ga4').status == 'success'
    calls = [c for c in vendor.calls
             if c['dimensions'][-1]['name'] == 'sessionDefaultChannelGroup']
    assert [c['offset'] for c in calls] == [0, 100000]
    assert {r['snapshot_id'] for r in project.rows('stg.ga4_engagement')} == {snapshot}
    assert len(project.rows('stg.ga4_engagement')) == 2


@pytest.mark.parametrize('identity', ['de', 'unknown', 'all'])
def test_ga4_reserves_generated_location_ids(tmp_path, respx_mock, identity):
    from brand_presence.settings import Settings

    project, _ = prepared(tmp_path, respx_mock, 'ga4')
    config = project.settings.model_dump()
    config['locations'] = [dict(location_id=identity, kind='city', country_code='NL',
                               name='Example City', canonical_name='Example City,Netherlands',
                               sources=['ga4'])]
    with pytest.raises(ValueError, match='reserved location'):
        Settings.model_validate(config)
