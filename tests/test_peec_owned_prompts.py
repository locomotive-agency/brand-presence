"""Peec is the sole prompt author; collection never sends a vendor write."""
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import respx

from brand_presence import http
from brand_presence.sources.base import Request
from brand_presence.sources.peec import PeecSend
from brand_presence.vendors import peec


def prompt(archived=False, country='NL'):
    return dict(id='sample-prompt', messages=[dict(content='A Peec authored question?')],
                user_location=dict(country=country), is_archived=archived,
                topic=dict(id='sample-topic'), tags=[dict(id='sample-tag')],
                created_at='2026-03-01')


@respx.mock
def test_prompt_pages_include_archived_and_use_stable_canonical_total(monkeypatch):
    monkeypatch.setattr(peec, 'PAGE_SIZE', 1)
    route = respx.get(peec.BASE + '/prompts').mock(side_effect=[
        httpx.Response(200, json=dict(data=[prompt()], total_count=2, totalCount=999)),
        httpx.Response(200, json=dict(data=[{**prompt(True), 'id': 'sample-archived'}],
                                     total_count=2, totalCount=999)),
    ])
    with httpx.Client() as client:
        rows = peec.Peec(client, 'sample-key').prompts('sample-project')
    assert len(rows) == 2 and rows[1]['is_archived']
    assert [c.request.url.params['offset'] for c in route.calls] == ['0', '1']
    assert all('is_archived' not in c.request.url.params for c in route.calls)


def test_client_has_no_prompt_write_surface():
    assert not hasattr(peec.Peec, 'create_prompt')
    assert not {'post', 'put', 'delete', 'patch'} & set(dir(peec.Peec))
    source = inspect.getsource(peec.Peec)
    assert '"POST"' not in source and '"PUT"' not in source and '"DELETE"' not in source
    assert 'method' not in inspect.signature(peec.Peec._call).parameters


def sender():
    send = PeecSend.__new__(PeecSend)
    send.api = Mock()
    send.api.prompts.return_value = [prompt()]
    send._prompts = {}
    send._save_prompts = Mock()
    send._save_discovery = Mock()
    send.clock = lambda: datetime(2026, 3, 12, tzinfo=UTC)
    send.ref = SimpleNamespace(load_number=1)
    send._prompt_metadata = Mock(return_value=dict(client_brand_id='sample',
        location_id='sample-nl', benchmark_version=1, snapshot_id='sample-snapshot',
        period='2026-W10', period_start=datetime(2026, 3, 2).date(),
        run_id='sample-run', request_key='sample-discovery'))
    return send


def request():
    return Request(dict(mapping='sample-mapping', project_id='sample-project',
                        country_code='NL', engine='chatgpt'))


def test_archived_prompt_is_saved_but_excluded_from_new_requests():
    send = sender()
    first = send.expand([request()])
    assert len(first) == 1
    original = send._save_prompts.call_args.args[0][0]
    send._prompts.clear()
    send.api.prompts.return_value = [prompt(True)]
    assert send.expand([request()]) == []
    archived = send._save_prompts.call_args.args[0][0]
    assert original.prompt_id == archived.prompt_id
    assert original.version != archived.version
    assert not original.is_archived and archived.is_archived
    assert send._save_prompts.call_count == 2


def test_prompt_country_mismatch_stops_before_saving_or_pulling():
    send = sender()
    send.api.prompts.return_value = [prompt(country='GB')]
    with pytest.raises(http.VendorError, match='peec mapping invalid'):
        send.expand([request()])
    send._save_prompts.assert_not_called()
    send.api.chats.assert_not_called()


def test_reports_read_peec_prompt_record():
    from brand_presence.reports.definition import load
    report = {r.name: r for r in load()}['peec_prompt_results_v1']
    assert 'stg.peec_prompts' in report.definition['reads']
    assert 'ref.current_benchmark_prompts' not in report.definition['reads']
    assert {'prompt_text', 'topic_id', 'is_archived'} <= {
        c['name'] for c in report.columns}


@respx.mock
@pytest.mark.parametrize('fault', ['changed_total', 'duplicate', 'early_end'])
def test_prompt_paging_refuses_incomplete_lists(monkeypatch, fault):
    monkeypatch.setattr(peec, 'PAGE_SIZE', 1)
    second = {**prompt(), 'id': 'sample-second'}
    batch, total = [second], 2
    if fault == 'changed_total':
        total = 3
    elif fault == 'duplicate':
        batch = [prompt()]
    else:
        batch = []
    respx.get(peec.BASE + '/prompts').mock(side_effect=[
        httpx.Response(200, json=dict(data=[prompt()], total_count=2)),
        httpx.Response(200, json=dict(data=batch, total_count=total)),
    ])
    with httpx.Client() as client:
        with pytest.raises(http.VendorError, match='peec pagination incomplete'):
            peec.Peec(client, 'sample-key').prompts('sample-project')






@pytest.mark.parametrize('created', [
    '2026-07-15T10:00:00Z', '2026-07-15T10:00:00', '2026-07-15T10:00:00+01:00',
    '20260715', '2026-W29-3', '2026-7-15', '2026-02-30', '', None, 123,
    '2026-07-15\n', '2026-07-15 ', ['2026-07-15'], {'date': '2026-07-15'},
])
def test_prompt_creation_requires_canonical_date(created):
    send = sender()
    send.api.prompts.return_value = [{**prompt(), 'created_at': created}]
    with pytest.raises(http.VendorError, match='peec response unreadable'):
        send.expand([request()])
    send._save_prompts.assert_not_called()


def test_prompt_creation_missing_is_unreadable():
    send = sender()
    row = prompt()
    del row['created_at']
    send.api.prompts.return_value = [row]
    with pytest.raises(http.VendorError, match='peec response unreadable'):
        send.expand([request()])
    send._save_prompts.assert_not_called()


