import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from brand_presence import runner
from brand_presence.bigquery import Warehouse
from brand_presence.reports.definition import load
from brand_presence.settings import MentionFilter, Settings
from brand_presence.sources.mentions import MentionsSource
from brand_presence.vendors import syften
from project import Killed, Project, configuration
from report_sql import run_statement


class Syften:
    def __init__(self, router):
        self.rows = json.loads((Path(__file__).parent / 'fixtures/syften/page_1.json').read_text())
        self.calls = 0
        self.kill = False
        router.post(f'{syften.BASE}/items/get').mock(side_effect=self.post)

    def post(self, request):
        self.calls += 1
        if self.kill:
            self.kill = False
            raise Killed()
        body = json.loads(request.content)
        after = syften.time_of(body['after'])
        before = syften.time_of(body.get('before'))
        rows = [r for r in self.rows if syften.time_of(r['matched_on']) > after and
                (before is None or syften.time_of(r['matched_on']) < before)]
        return httpx.Response(200, json=rows)


def prepared(tmp_path, router):
    project = Project(tmp_path, router)
    project.settings.mention_filters = [MentionFilter(
        tag='sample-aardvark-nl', mention_class='brand', client_brand_id='aardvark',
        subject_brand_id='aardvark', country_code='NL', keyword_group='kettles')]
    return project, Syften(router)


def run(project):
    with httpx.Client() as http:
        return runner.run(MentionsSource(), project.settings, runner.World(
            project.bq, http, {'syften-api-token': 'example-key'}, clock=lambda: project.now,
            sleep=lambda _: None, wait_seconds=60))


def test_full_mentions_run_and_known_report(tmp_path, respx_mock):
    project, _ = prepared(tmp_path, respx_mock)
    result = run(project)
    assert result.status == 'success', result.lines
    assert result.source_counts['matches_unlisted'] == 2
    assert project.rows('ops.runs')[-1]['matches_unlisted'] == 2
    assert len(project.rows('stg.mention_facts')) == 1
    assert len(project.rows('stg.mention_text')) == 1
    assert project.rows('ops.mentions_cursor')[-1]['cursor_value'].startswith('2026-03-01')
    raw = '\n'.join(p.read_text() for p in tmp_path.rglob('*.json'))
    assert 'author' not in raw
    assert 'match-0008' not in raw
    project.later()
    assert run(project).status == 'success'
    assert len(project.rows('stg.mention_facts')) == 1
    report = load('mentions')[0]
    rows = run_statement(report, {'stg.mention_facts': project.rows('stg.mention_facts')},
                         today=date(2026, 3, 2))
    assert len(rows) == 1
    assert (rows[0]['match_id'], rows[0]['month'], rows[0]['keyword_group'],
            rows[0]['accepted']) == ('match-0009', date(2026, 3, 1), 'kettles', True)


@pytest.mark.parametrize('boundary', ['post', 'save_raw', 'load_raw', 'replace'])
def test_mentions_interrupted_pages_recover(tmp_path, respx_mock, monkeypatch, boundary):
    project, vendor = prepared(tmp_path, respx_mock)
    with monkeypatch.context() as patch:
        if boundary == 'post':
            vendor.kill = True
        else:
            original = getattr(Warehouse, boundary)

            def killed(*args, **kwargs):
                original(*args, **kwargs)
                raise Killed()

            patch.setattr(Warehouse, boundary, killed)
        with pytest.raises(Killed):
            run(project)
    assert project.rows('ops.mentions_cursor') == []
    project.later()
    assert run(project).status == 'success'
    assert len(project.rows('stg.mention_facts')) == 1
    assert len(project.rows('stg.mention_text')) == 1
    assert all(r['written_at'] for r in project.rows('ops.mentions_tasks')
               if r['state'] == 'collected')


@pytest.mark.parametrize('change', ['brand', 'country', 'tag', 'class', 'retention', 'subreddit'])
def test_mentions_config_validation(change):
    config = copy.deepcopy(configuration())
    entry = dict(tag='example', mention_class='brand', client_brand_id='aardvark',
                 subject_brand_id='aardvark', country_code='NL')
    config['mention_filters'] = [entry]
    if change == 'brand':
        entry['subject_brand_id'] = 'missing'
    elif change == 'country':
        entry['country_code'] = 'XX'
    elif change == 'tag':
        entry['tag'] = '$tag:invalid'
    elif change == 'class':
        entry['mention_class'] = 'competitor'
    elif change == 'retention':
        config['mention_text_days'] = 0
    else:
        config['subreddits'] = [dict(subreddit='example', community_group='example', subscribers=1)]
    with pytest.raises(ValidationError):
        Settings.model_validate(config)


def test_failed_raw_save_does_not_skip_page(tmp_path, respx_mock, monkeypatch):
    project, _ = prepared(tmp_path, respx_mock)

    def failed(*args, **kwargs):
        raise OSError('example disk failure')

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'save_raw', failed)
        assert run(project).status == 'error'
    assert project.rows('ops.mentions_cursor') == []
    project.later()
    assert run(project).status == 'success'
    assert len(project.rows('stg.mention_facts')) == 1


@pytest.mark.parametrize('damage', ['missing_before_load', 'missing_after_load', 'parse'])
def test_unfinishable_saved_page_fails_and_repulls(
        tmp_path, respx_mock, monkeypatch, capsys, damage):
    project, vendor = prepared(tmp_path, respx_mock)
    boundary = 'load_raw' if damage == 'missing_before_load' else 'replace'

    def killed(*args, **kwargs):
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, boundary, killed)
        with pytest.raises(Killed):
            run(project)
    assert project.rows('ops.mentions_cursor') == []
    old = project.rows('ops.mentions_tasks')[0]
    paths = [p for p in tmp_path.rglob('*.json')
             if json.loads(p.read_text())['request_key'] == old['request_key']]
    assert len(paths) == 1
    if damage.startswith('missing'):
        paths[0].unlink()
    else:
        raw = json.loads(paths[0].read_text())
        raw['response'] = json.dumps({'matches': [None]})
        paths[0].write_text(json.dumps(raw))
    calls = vendor.calls
    project.later()

    result = run(project)

    assert result.status == 'partial', result.lines
    assert vendor.calls > calls
    recovered = next(r for r in project.rows('ops.mentions_tasks')
                     if r['request_key'] == old['request_key'])
    assert recovered['state'] == 'failed'
    assert result.counts['requests_failed'] == 1
    assert project.rows('ops.runs')[-1]['requests_failed'] == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['requests_failed'] == 1
    assert len(project.rows('stg.mention_facts')) == 1
    assert project.rows('ops.mentions_cursor')


def test_local_expiry_keeps_facts_and_removes_raw(tmp_path, respx_mock):
    from datetime import timedelta

    project, _ = prepared(tmp_path, respx_mock)
    assert run(project).status == 'success'
    saved = list(tmp_path.rglob('*.json'))
    project.now += timedelta(days=367)
    assert run(project).status == 'success'
    assert all(not p.exists() for p in saved)
    assert len(project.rows('stg.mention_facts')) == 1


def test_local_expiry_batches_ledger_updates(tmp_path, respx_mock):
    from datetime import timedelta

    project, _ = prepared(tmp_path, respx_mock)
    assert run(project).status == 'success'
    saved = list(tmp_path.rglob('*.json'))
    assert len(saved) == 2
    project.now += timedelta(days=367)
    at = len(project.fake.journal.calls)

    assert run(project).status == 'success'

    rewrites = [call for call in project.fake.journal.calls[at:]
                if call.name == 'query' and 'DELETE FROM' in call.subject
                and 'mentions_tasks' in call.subject
                and any(row['state'] == 'expired'
                        for row in call.detail['parameters'].get('rows', []))]
    assert len(rewrites) == 1
    assert len(rewrites[0].detail['parameters']['rows']) == 2
    assert all(not p.exists() for p in saved)


@pytest.mark.parametrize('orphan', [False, True])
def test_unreadable_local_file_is_counted_and_does_not_block_recovery(
        tmp_path, respx_mock, monkeypatch, capsys, orphan):
    project, vendor = prepared(tmp_path, respx_mock)

    def killed(*args, **kwargs):
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'load_raw', killed)
        with pytest.raises(Killed):
            run(project)
    saved = list(tmp_path.rglob('*.json'))
    if orphan:
        broken = saved[0].parent / 'untracked.json'
    else:
        broken = saved[0]
    broken.write_text('{broken')
    project.later()
    calls = vendor.calls

    result = run(project)

    assert result.status == 'partial', result.lines
    assert vendor.calls > calls
    assert result.error_class == 'raw file unreadable'
    assert result.error_count == 1
    line = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (line['error_class'], line['error_count']) == ('raw file unreadable', 1)
    assert result.counts['requests_failed'] == (0 if orphan else 1)
    assert broken.exists()
    assert len(project.rows('stg.mention_facts')) == 1
    assert project.rows('ops.mentions_cursor')
