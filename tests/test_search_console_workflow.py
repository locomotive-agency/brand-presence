from datetime import date

import pytest

from brand_presence.bigquery import Warehouse
from brand_presence.reports.definition import load
from brand_presence.settings import Settings
from google_project import prepared, run
from project import Killed
from reporting import evaluate


def test_search_console_full_run_and_repeat(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    result = run(project)
    assert result.status == 'success', result.lines
    assert len(project.rows('raw.search_console')) == 4
    assert len(project.rows('stg.search_console_totals')) == 7
    assert len(project.rows('stg.search_console_pulls')) == 1
    assert project.rows('stg.search_console_totals')[0]['country_code'] == 'NL'
    count = len(vendor.calls)
    assert run(project).status == 'success'
    assert len(vendor.calls) == count
    assert len(project.rows('stg.search_console_totals')) == 7


@pytest.mark.parametrize('boundary', ['save_raw', 'load_raw', 'replace'])
def test_search_console_resumes_without_duplicate_reads(
    tmp_path, respx_mock, monkeypatch, boundary,
):
    project, vendor = prepared(tmp_path, respx_mock)
    original = getattr(Warehouse, boundary)

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, boundary, killed)
        with pytest.raises(Killed):
            run(project)
    project.later()
    assert run(project).status == 'success'
    parts = [b['dimensions'] for b in vendor.calls if b['dataState'] == 'final']
    assert parts == [['date', 'country'], ['date', 'country', 'query'],
                     ['date', 'country', 'page'], ['date', 'country', 'device']]
    assert len(project.rows('stg.search_console_totals')) == 7
    assert len(project.rows('ops.search_console_tasks')) == 1


@pytest.mark.parametrize('unfinished', ['2026-03-01', 'unreadable', None])
def test_search_console_holds_unfinished_or_unreadable_week(tmp_path, respx_mock, unfinished):
    project, vendor = prepared(tmp_path, respx_mock)
    vendor.unfinished = unfinished
    assert run(project).status == 'error'
    assert len(vendor.calls) == 1
    assert project.rows('ops.search_console_tasks') == []


@pytest.mark.parametrize('change', ['unknown_brand', 'bad_site', 'bad_hostname', 'duplicate'])
def test_search_console_config_validation(tmp_path, respx_mock, change):
    project, _ = prepared(tmp_path, respx_mock)
    config = project.settings.model_dump()
    prop = config['web_properties'][0]
    if change == 'unknown_brand':
        prop['client_brand_id'] = 'absent'
    elif change == 'bad_site':
        prop['address'] = 'not-a-site'
    elif change == 'bad_hostname':
        prop['hostname'] = 'https://aardvark.example/'
    else:
        config['web_properties'].append(dict(prop))
    with pytest.raises(ValueError):
        Settings.model_validate(config)


def test_gsc_report_known_result(tmp_path, respx_mock):
    project, _ = prepared(tmp_path, respx_mock)
    assert run(project).status == 'success'
    report = load('search-console')[0]
    tables = {name: (list(rows[0]), rows) for name in report.definition['reads']
              if (rows := project.rows(name))}
    rows = evaluate(report.statement, tables)
    totals = {r['month']: r for r in rows if r['row_kind'] == 'total'}
    assert totals['2026-02-01']['clicks'] == 218
    assert totals['2026-02-01']['impressions'] == 5100
    assert totals['2026-02-01']['position_weighted'] == 23177.5
    assert totals['2026-02-01']['days_covered'] == 6
    assert totals['2026-03-01']['clicks'] == 42
    assert date.fromisoformat(totals['2026-03-01']['month']).day == 1


def test_search_console_report_checks(tmp_path, respx_mock):
    project, _ = prepared(tmp_path, respx_mock)
    assert run(project).status == 'success'
    report = load('search-console')[0]
    tables = {name: (list(rows[0]), rows) for name in report.definition['reads']
              if (rows := project.rows(name))}
    rows = [dict(r, pulled_at=project.now) for r in evaluate(report.statement, tables)]
    tables[report.table] = (list(rows[0]), rows)
    for _, sql in report.checks:
        assert evaluate(sql, tables, {'pulled_at': project.now}) == []


@pytest.mark.parametrize("vendor_error", [False, True])
def test_search_console_recovery_survives_another_failed_read(
    tmp_path, respx_mock, monkeypatch, vendor_error,
):
    import httpx

    from brand_presence.vendors import search_console

    project, vendor = prepared(tmp_path, respx_mock)
    original = Warehouse.save_raw

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'save_raw', killed)
        with pytest.raises(Killed):
            run(project)
    project.later()
    def failed(*args, **kwargs):
        from brand_presence.http import VendorError

        if vendor_error:
            raise VendorError('vendor fault', 503)
        raise httpx.ReadTimeout('example failure')

    with monkeypatch.context() as patch:
        patch.setattr(search_console, 'query', failed)
        assert run(project).status == 'error'
    assert project.rows('ops.search_console_tasks')[0]['state'] == 'unknown'
    project.later()
    assert run(project).status == 'success'
    assert len([b for b in vendor.calls if b['dataState'] == 'final'
                and b['dimensions'] == ['date', 'country']]) == 1
