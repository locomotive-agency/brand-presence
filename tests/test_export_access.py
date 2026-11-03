import pytest
from google.cloud import bigquery
from typer.testing import CliRunner

from brand_presence import access_check, cli
from brand_presence.settings import Settings
from export_project import DATASET, prepared
from project import Project, configuration


def schemas(project, source):
    expected = ({'events_20260301': access_check.GA4_COLUMNS} if source == 'ga4'
                else access_check.GSC_COLUMNS)
    for table, columns in expected.items():
        project.fake.create_table(f'{DATASET}.{table}', columns)
    return expected


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
def test_access_lists_and_checks_columns_without_queries(tmp_path, respx_mock, source):
    project, _ = prepared(tmp_path, respx_mock, source)
    expected = schemas(project, source)
    before = len(project.fake.journal.of('bigquery', 'query'))
    checks = access_check.run(project.bq, project.settings)
    assert {c.table for c in checks} == set(expected)
    assert all(c.ok for c in checks)
    assert len(project.fake.journal.of('bigquery', 'query')) == before
    assert len(project.fake.journal.of('bigquery', 'list_tables')) == 1


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
def test_access_names_missing_tables_and_columns(tmp_path, respx_mock, source):
    project, _ = prepared(tmp_path, respx_mock, source)
    expected = schemas(project, source)
    table = next(iter(expected))
    project.fake.tables['fixture_export.' + table].columns.pop('event_name' if source == 'ga4'
                                                              else 'country')
    checks = access_check.run(project.bq, project.settings)
    assert any('missing column' in c.line() for c in checks)
    project.fake.add_dataset(DATASET, location='EU', tables=[])
    checks = access_check.run(project.bq, project.settings)
    assert all('table missing' in c.line() for c in checks)
    assert all(DATASET not in c.line() for c in checks)


def test_nested_ga4_columns_are_checked():
    fields = [bigquery.SchemaField('event_params', 'RECORD', mode='REPEATED', fields=[
        bigquery.SchemaField('key', 'STRING'),
        bigquery.SchemaField('value', 'RECORD',
                             fields=[bigquery.SchemaField('int_value', 'INT64')]),
    ])]
    assert access_check.columns_of(fields)['event_params.value.int_value'] == 'INT64'


def test_access_command_api_only_never_connects(tmp_path, monkeypatch):
    import yaml

    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(configuration()))
    def denied(*_):
        raise AssertionError('no export access to check')
    monkeypatch.setattr(cli, 'connect', denied)
    result = CliRunner().invoke(cli.app, ['check-access', '--config', str(path)])
    assert result.exit_code == 0
    assert 'No export' in result.output


def test_access_command_reports_refused_dataset(tmp_path, respx_mock, monkeypatch):
    import yaml

    project, _ = prepared(tmp_path, respx_mock)
    project.fake.refuse('get_dataset')
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(project.settings.model_dump(mode='json')))
    monkeypatch.setattr(cli, 'connect', lambda _: project.bq)
    result = CliRunner().invoke(cli.app, ['check-access', '--config', str(path)])
    assert result.exit_code == 1
    assert 'example-site: dataset: access' in result.output
    assert DATASET not in result.output


def test_existing_install_gets_export_columns(tmp_path, respx_mock):
    from brand_presence import schema

    project = Project(tmp_path, respx_mock)
    for name in ('months_first', 'months_last', 'bytes_billed', 'days_without_table',
                 'listed_states_unmatched'):
        project.fake.tables['bp_stg.ga4_pulls'].columns.pop(name)
    project.fake.tables['bp_ref.web_properties'].columns.pop('route')
    schema.initialise(project.bq)
    assert 'months_first' in project.fake.tables['bp_stg.ga4_pulls'].columns
    assert 'route' in project.fake.tables['bp_ref.web_properties'].columns
    assert Settings.model_validate(configuration()).web_properties == []
