import json
from datetime import timedelta

import httpx
import pytest

from brand_presence import ledger
from brand_presence.bigquery import Warehouse
from brand_presence.client_ref import apply
from brand_presence.runs import Period, period_start
from brand_presence.sources.base import keyed, stored_request
from brand_presence.sources.registry import SOURCES
from google_project import prepared, run
from project import Killed


def interrupted(project, monkeypatch):
    original = Warehouse.save_raw

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'save_raw', killed)
        with pytest.raises(Killed):
            run(project)
    project.later()


def unknown_requests(project, source_name, period_name='2026-W09'):
    source = SOURCES[source_name]()
    ref = apply(project.bq, project.settings, project.now)
    period = Period(period_name, period_start(period_name))
    requests = keyed(source.plan(ref, period), project.settings.client_id, source_name,
                     period_name, source.identity)
    rows = [source.ledger_model(
        request_key=key, period=period_name, attempt=1, vendor_task_id=None,
        state=ledger.UNKNOWN, run_id='abandoned', created_at=project.now,
        submitted_at=None, collected_at=None, stored_run_id=None, loaded_at=None,
        written_at=None, error_class=None, vendor_status=None,
        request=stored_request(request, ref.load_number, ref.snapshot_id),
    ) for key, request in requests.items()]
    ledger.Ledger(project.bq, source.ledger_model, lambda: project.now).add(rows)
    return rows


def fail_final_reads(router, vendor, code):
    def answer(request):
        if json.loads(request.content)['dataState'] == 'all':
            return vendor.query(request)
        return httpx.Response(code, json={'error': {'status': 'PERMISSION_DENIED'}})

    return router.post(url__regex=r'https://searchconsole.googleapis.com/.*').mock(
        side_effect=answer)


def test_recovery_403_twice_releases_attempt_and_preserves_pages(
    tmp_path, respx_mock, monkeypatch,
):
    project, vendor = prepared(tmp_path, respx_mock)
    interrupted(project, monkeypatch)
    saved = {path: path.read_bytes() for path in tmp_path.rglob('*.json')}
    assert saved
    route = fail_final_reads(respx_mock, vendor, 403)
    assert run(project).status == 'error'
    assert [r['state'] for r in project.rows('ops.search_console_tasks')] == ['failed']
    project.later()
    assert run(project).status == 'error'
    assert sorted((r['attempt'], r['state']) for r in project.rows(
        'ops.search_console_tasks')) == [(1, 'failed'), (2, 'failed')]
    route.mock(side_effect=vendor.query)
    project.later()
    assert run(project).status == 'success'
    assert sorted((r['attempt'], r['state']) for r in project.rows(
        'ops.search_console_tasks')) == [(1, 'failed'), (2, 'failed'), (3, 'collected')]
    assert all(path.read_bytes() == content for path, content in saved.items())
    assert len(project.rows('stg.search_console_totals')) == 7


def test_non_transient_recovery_failure_allows_fresh_attempt(tmp_path, respx_mock, monkeypatch):
    project, vendor = prepared(tmp_path, respx_mock)
    interrupted(project, monkeypatch)
    route = fail_final_reads(respx_mock, vendor, 400)
    assert run(project).status == 'error'
    assert all(r['state'] == 'failed' for r in project.rows('ops.search_console_tasks'))
    route.mock(side_effect=vendor.query)
    project.later()
    assert run(project).status == 'success'


def test_transient_recovery_is_preserved_but_bounded(tmp_path, respx_mock, monkeypatch):
    project, vendor = prepared(tmp_path, respx_mock)
    interrupted(project, monkeypatch)
    route = fail_final_reads(respx_mock, vendor, 503)
    for _ in range(2):
        assert run(project).status == 'error'
        rows = project.rows('ops.search_console_tasks')
        assert len(rows) == 1 and rows[0]['state'] == 'unknown'
        project.later()
    assert run(project).status == 'error'
    assert sorted((r['attempt'], r['state']) for r in project.rows(
        'ops.search_console_tasks')) == [(1, 'failed'), (2, 'failed')]
    route.mock(side_effect=vendor.query)
    project.later()
    assert run(project).status == 'success'


def test_recovery_stops_after_group_refusal(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    unknown_requests(project, 'search-console', '2026-W08')
    unknown_requests(project, 'search-console')
    route = fail_final_reads(respx_mock, vendor, 403)
    assert run(project, period=None).status == 'error'
    final = [call for call in route.calls
             if json.loads(call.request.content)['dataState'] == 'final']
    assert len(final) == 1
    assert sorted((r['period'], r['state']) for r in project.rows(
        'ops.search_console_tasks')) == [('2026-W08', 'failed'), ('2026-W09', 'unknown')]


def test_recovery_honours_run_ending_check(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    prop = project.settings.web_properties[0]
    project.settings.web_properties.append(prop.model_copy(update={
        'web_property_id': 'second-site', 'address': 'sc-domain:second.example'}))
    unknown_requests(project, 'search-console')
    vendor.unfinished = 'invalid'
    assert run(project).status == 'error'
    assert len(vendor.calls) == 1
    assert vendor.calls[0]['dataState'] == 'all'
    assert sorted(r['state'] for r in project.rows('ops.search_console_tasks')) == [
        'failed', 'unknown']


@pytest.mark.parametrize('during_check', [False, True])
def test_recovery_honours_deadline(tmp_path, respx_mock, during_check):
    project, vendor = prepared(tmp_path, respx_mock)
    unknown_requests(project, 'search-console')

    def answer(request):
        reply = vendor.query(request)
        project.now += timedelta(hours=1)
        return reply

    respx_mock.post(url__regex=r'https://searchconsole.googleapis.com/.*').mock(
        side_effect=answer)
    assert run(project, wait_seconds=1 if during_check else 0).status == 'error'
    assert len(vendor.calls) == int(during_check)
    assert all(c['dataState'] == 'all' for c in vendor.calls)
    row, = project.rows('ops.search_console_tasks')
    assert row['state'] == 'unknown'
    assert 'recovery_failures' not in json.loads(row['request'])


@pytest.mark.parametrize('invalid', ['customer', 'currency'])
def test_recovery_checks_ads_account_before_reports(tmp_path, respx_mock, invalid):
    from google_project import fixture

    project, vendor = prepared(tmp_path, respx_mock, 'google-ads')
    unknown_requests(project, 'google-ads')
    details = fixture('google_ads', 'details_first')
    if invalid == 'customer':
        details['results'][0]['customer']['id'] = '1000000002'
    else:
        details['results'][0]['customer'].pop('currencyCode')
    vendor.answers['details_first'] = details
    assert run(project, 'google-ads').status == 'error'
    assert len(vendor.calls) == 1
    assert project.rows('stg.google_ads_pulls') == []


def test_recovery_reuses_sender_per_old_snapshot(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock, 'google-ads')
    unknown_requests(project, 'google-ads')
    project.settings.brands[0].name = 'Changed brand name'
    assert run(project, 'google-ads').status == 'success'
    assert len(vendor.calls) == 5
    assert len(project.rows('stg.google_ads_pulls')) == 4


def test_removed_property_is_not_recovered_forever(tmp_path, respx_mock):
    project, _ = prepared(tmp_path, respx_mock)
    unknown_requests(project, 'search-console')
    project.settings.web_properties = []
    route = respx_mock.post(url__regex=r'https://searchconsole.googleapis.com/.*').respond(
        403, json={'error': {'status': 'PERMISSION_DENIED'}})
    assert run(project).status == 'error'
    row, = project.rows('ops.search_console_tasks')
    assert row['state'] == 'failed'
    project.later()
    assert run(project).status == 'error'
    assert route.call_count == 1


def test_recovery_check_transient_failure_can_resume(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    unknown_requests(project, 'search-console')
    route = respx_mock.post(url__regex=r'https://searchconsole.googleapis.com/.*').respond(503)
    assert run(project).status == 'error'
    row, = project.rows('ops.search_console_tasks')
    assert row['state'] == 'unknown'
    route.mock(side_effect=vendor.query)
    project.later()
    assert run(project).status == 'success'
    row, = project.rows('ops.search_console_tasks')
    assert row['attempt'] == 1 and row['state'] == 'collected'


def test_local_recovery_failures_are_bounded(tmp_path, respx_mock, monkeypatch):
    project, _ = prepared(tmp_path, respx_mock)
    interrupted(project, monkeypatch)

    def failed(*args, **kwargs):
        raise OSError('disk unavailable')

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'save_raw', failed)
        for expected in ('unknown', 'unknown', 'failed'):
            assert run(project).status == 'error'
            row = next(r for r in project.rows('ops.search_console_tasks') if r['attempt'] == 1)
            assert row['state'] == expected
            project.later()
    assert run(project).status == 'success'


def test_dead_ads_page_token_releases_attempt(tmp_path, respx_mock, monkeypatch):
    project, vendor = prepared(tmp_path, respx_mock, 'google-ads')
    vendor.answers['campaigns'] = 'campaigns_two_pages'
    original = Warehouse.save_raw

    def killed(self, source, raw, *args, **kwargs):
        result = original(self, source, raw, *args, **kwargs)
        held = json.loads(raw.request)
        if held['part'] == 'rows' and held['page'] == 1:
            raise Killed()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'save_raw', killed)
        with pytest.raises(Killed):
            run(project, 'google-ads')
    saved = {path: path.read_bytes() for path in tmp_path.rglob('*.json')}

    def answer(request):
        if json.loads(request.content).get('pageToken'):
            return httpx.Response(400, json={'error': {'status': 'INVALID_ARGUMENT'}})
        return vendor.query(request)

    route = respx_mock.post(url__regex=r'https://googleads.googleapis.com/.*').mock(
        side_effect=answer)
    project.later()
    assert run(project, 'google-ads').status != 'success'
    campaign = [r for r in project.rows('ops.google_ads_tasks')
                if json.loads(r['request'])['body']['report'] == 'campaigns']
    assert sorted((r['attempt'], r['state']) for r in campaign) == [
        (1, 'failed'), (2, 'failed')]
    assert all(path.read_bytes() == content for path, content in saved.items())
    vendor.answers['campaigns'] = 'campaigns'
    route.mock(side_effect=vendor.query)
    project.later()
    assert run(project, 'google-ads').status == 'success'
