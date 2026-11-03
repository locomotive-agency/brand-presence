import pytest
import sqlglot
from sqlglot import exp

from brand_presence.reports.definition import DIRECTORY, load
from brand_presence.settings import Settings
from brand_presence.warehouse import qualify
from project import Project, configuration
from reporting import install_sources


@pytest.mark.parametrize('path', sorted(DIRECTORY.rglob('*.sql')),
                         ids=lambda p: str(p.relative_to(DIRECTORY)))
def test_every_report_and_check_uses_configured_datasets(path):
    settings = Settings.model_validate(configuration())
    original = sqlglot.parse_one(path.read_text(), read='bigquery')
    qualified = sqlglot.parse_one(qualify(path.read_text(), settings), read='bigquery')
    expected = [(settings.project_id, getattr(settings.datasets, t.db), t.name)
                for t in original.find_all(exp.Table) if t.db]
    actual = [(t.catalog, t.db, t.name) for t in qualified.find_all(exp.Table) if t.db]
    assert actual == expected


@pytest.mark.parametrize('comment', ["-- the month's ref.brands\n",
                                     "/* the month's\nref.brands */\n"])
def test_comments_and_strings_are_preserved_without_hiding_tables(comment):
    sql = comment + "SELECT 'ref.brands' FROM ref.brands"
    assert qualify(sql, Settings.model_validate(configuration())) == (
        comment + "SELECT 'ref.brands' FROM `example-project.bp_ref.brands`")


@pytest.mark.parametrize('name', ['fct_ai_answers_v1', 'ai_citations_v1'])
def test_offline_executor_rejects_unqualified_report_references(tmp_path, respx_mock, name):
    project = Project(tmp_path, respx_mock)
    install_sources(project.fake, project.settings)
    report = next(r for r in load() if r.name == name)
    sql = (f'CREATE TEMP TABLE _fact_rows AS\n{report.statement}' if report.fact else
           f'INSERT INTO `{project.settings.table(report.table)}` '
           f'({", ".join(report.returned)})\n{report.statement}')
    with pytest.raises(ValueError, match='unqualified report table'):
        project.fake.query(sql).result()
