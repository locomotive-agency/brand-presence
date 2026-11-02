import pytest

from brand_presence.bigquery import Warehouse
from peec_project import prepared, run
from project import Killed


def test_full_peec_run_and_rerun(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    result = run(project)
    assert result.status == 'success', result.lines
    assert len(project.rows('stg.peec_answers')) == 1
    assert len(project.rows('stg.peec_answer_brands')) == 1
    assert len(project.rows('raw.peec')) == 3
    assert result.counts['raw_rows'] == 3
    assert result.counts['clean_rows'] == 4
    assert len(project.rows('stg.peec_prompts')) == 1
    assert project.rows('stg.peec_pulls')[0]['complete_snapshot'] is True
    chats = sum(p == '/chats' for p, _ in vendor.calls)
    assert run(project).status == 'success'
    assert sum(p == '/chats' for p, _ in vendor.calls) == chats
    assert len(project.rows('stg.peec_answers')) == 1


@pytest.mark.parametrize('boundary', ['get', 'save_raw', 'load_raw', 'replace'])
def test_peec_interrupted_resume(tmp_path, respx_mock, monkeypatch, boundary):
    project, vendor = prepared(tmp_path, respx_mock)
    with monkeypatch.context() as patch:
        if boundary == 'get':
            vendor.kill_path = '/chats/example-chat/content'
        else:
            original = getattr(Warehouse, boundary)

            calls = 0

            def killed(*args, **kwargs):
                nonlocal calls
                value = original(*args, **kwargs)
                calls += 1
                if boundary == 'replace' or calls == 2:
                    raise Killed()
                return value

            patch.setattr(Warehouse, boundary, killed)
        with pytest.raises(Killed):
            run(project)
    project.later()
    assert run(project).status == 'success'
    assert len(project.rows('stg.peec_answers')) == 1
    assert len(project.rows('stg.peec_answer_brands')) == 1
    assert all(r['written_at'] for r in project.rows('ops.peec_tasks')
               if r['state'] == 'collected')


def test_peec_unusable_retry_then_complete_empty(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    vendor.bad_detail = True
    assert run(project).status == 'error'
    assert project.rows('stg.peec_answers') == []
    vendor.bad_detail = False
    vendor.empty = True
    assert run(project).status == 'success'
    assert project.rows('stg.peec_answers') == []
    assert project.rows('stg.peec_pulls')[0]['chats_observed'] == 0
    assert len(project.rows('ops.peec_tasks')) == 2


def test_archived_prompts_are_recorded_without_new_chats(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    vendor.archived = True
    assert run(project).status == 'error'
    assert project.rows('stg.peec_prompts')[0]['is_archived'] is True
    assert not any(p == '/chats' for p, _ in vendor.calls)


@pytest.mark.parametrize('change, message', [
    ('missing_brand', 'map every benchmark brand'),
    ('duplicate_mapping', 'mappings must be unique'),
    ('duplicate_project', 'project may have only one mapping'),
    ('unknown_mapping', 'listed mapping'),
    ('unknown_country', 'matching benchmark set'),
    ('duplicate_engine', 'engines must be unique'),
    ('bad_date', 'valid date'),
])
def test_peec_config_validation(tmp_path, respx_mock, change, message):
    from pydantic import ValidationError

    from brand_presence.settings import Settings

    project, _ = prepared(tmp_path, respx_mock)
    config = project.settings.model_dump(mode='json')
    mapping = config['peec_projects'][0]
    if change == 'missing_brand':
        config['peec_brands'] = []
    elif change == 'duplicate_mapping':
        config['peec_projects'].append(dict(mapping))
    elif change == 'duplicate_project':
        config['peec_projects'].append({**mapping, 'mapping': 'second'})
    elif change == 'unknown_mapping':
        config['peec_brands'][0]['mapping'] = 'unknown'
    elif change == 'unknown_country':
        mapping['country_code'] = 'GB'
    elif change == 'duplicate_engine':
        mapping['engines'] *= 2
    else:
        mapping['first_date'] = 'not-a-date'
    with pytest.raises(ValidationError, match=message):
        Settings.model_validate(config)


def test_peec_mapping_cannot_be_rebound(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    assert run(project).status == 'success'
    before = len(vendor.calls)
    project.settings.peec_projects[0].project_id = 'example-other-project'
    result = run(project)
    assert result.status == 'error'
    assert any('mapping' in line for line in result.lines)
    assert len(vendor.calls) == before


def test_peec_history_stops_at_configured_first_date(tmp_path, respx_mock):
    from datetime import date

    project, vendor = prepared(tmp_path, respx_mock)
    project.settings.peec_projects[0].first_date = date(2026, 2, 16)
    result = run(project, period=None)
    assert result.status == 'success', result.lines
    assert {r['period'] for r in project.rows('stg.peec_pulls')} == {'2026-W09', '2026-W08'}
    assert {p['start_date'] for path, p in vendor.calls if path == '/chats'} == {
        '2026-02-16', '2026-02-23'}
    count = sum(path == '/chats' for path, _ in vendor.calls)
    assert run(project, period=None).status == 'success'
    assert sum(path == '/chats' for path, _ in vendor.calls) == count


def test_peec_discovery_is_raw_before_prompt_versions(tmp_path, respx_mock, monkeypatch):
    import json

    from brand_presence.sources.peec import PeecSend

    project, _ = prepared(tmp_path, respx_mock)
    original = PeecSend._save_prompts

    def save(sender, rows):
        raw = project.rows('raw.peec')
        assert len(raw) == 1
        assert json.loads(raw[0]['response'])['prompts'][0]['id'] == 'example-prompt'
        assert list(project.settings.state_path.rglob('*.json'))
        return original(sender, rows)

    monkeypatch.setattr(PeecSend, '_save_prompts', save)
    assert run(project).status == 'success'


def test_peec_recovery_uses_captured_brand_config(tmp_path, respx_mock, monkeypatch):
    project, vendor = prepared(tmp_path, respx_mock)

    def killed(*args, **kwargs):
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, 'replace', killed)
        with pytest.raises(Killed):
            run(project)
    project.settings.brands[0].name = 'Different Invented Brand'
    project.settings.brands[0].domains = ['different.example']
    project.later()
    before = sum(path == '/chats' for path, _ in vendor.calls)
    assert run(project).status == 'success'
    marks = project.rows('stg.peec_answer_brands')
    assert len(marks) == 1 and marks[0]['named'] is True and marks[0]['cited'] is True
    assert sum(path == '/chats' for path, _ in vendor.calls) == before


@pytest.mark.parametrize('empty', [False, True])
def test_peec_reread_preserves_or_replaces_whole_snapshot(tmp_path, respx_mock, empty):
    project, vendor = prepared(tmp_path, respx_mock)
    assert run(project).status == 'success'
    before = project.rows('stg.peec_answers')
    held = project.rows('ops.peec_tasks')[-1]
    project.fake.put(project.settings.table('ops.peec_tasks'), [{
        **held, 'attempt': 2, 'state': 'unusable', 'written_at': None,
        'error_class': 'unusable',
    }])
    vendor.empty = empty
    vendor.bad_detail = not empty
    result = run(project)
    if empty:
        assert result.status == 'success'
        assert project.rows('stg.peec_answers') == []
        assert project.rows('stg.peec_answer_brands') == []
        assert len(project.rows('stg.peec_pulls')) == 1
        assert project.rows('stg.peec_pulls')[0]['chats_observed'] == 0
    else:
        assert result.status == 'error'
        assert project.rows('stg.peec_answers') == before
