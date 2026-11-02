import pytest

from brand_presence.reports import runner
from peec_project import prepared as peec_project
from peec_project import run as run_peec
from project import Project
from reporting import install_sources
from test_cloro_workflow import prepared as cloro_project
from test_cloro_workflow import run as run_cloro
from test_mentions_workflow import prepared as mentions_project
from test_mentions_workflow import run as run_mentions


@pytest.mark.parametrize('source', ['ai-answers', 'peec', 'mentions', 'ai-weekly'])
def test_source_report_groups_allow_empty_output(tmp_path, respx_mock, source):
    project = Project(tmp_path, respx_mock)
    install_sources(project.fake, project.settings)
    built = runner.run(project.bq, project.settings, source=source,
                       clock=lambda: project.now, out=lambda _: None)
    assert built
    assert all(project.rows(table) == [] for table in built)
    assert project.rows('ops.runs')[-1]['status'] == 'success'


@pytest.mark.parametrize('source,prepare,run', [
    ('ai-answers', cloro_project, run_cloro), ('peec', peec_project, run_peec),
    ('mentions', mentions_project, run_mentions),
])
def test_transactional_report_build_and_repeat(tmp_path, respx_mock, source, prepare, run):
    project, _ = prepare(tmp_path, respx_mock)
    assert run(project).status == 'success'
    install_sources(project.fake, project.settings)

    def build():
        return runner.run(project.bq, project.settings, source=source,
                          clock=lambda: project.now, out=lambda _: None)

    built = build()
    before = {table: project.rows(table) for table in built}
    assert all(before.values())
    project.later()
    build()
    assert {table: project.rows(table) for table in built} == before


def test_weekly_group_builds_with_one_source_empty(tmp_path, respx_mock):
    project, _ = peec_project(tmp_path, respx_mock)
    assert run_peec(project).status == 'success'
    install_sources(project.fake, project.settings)
    built = runner.run(project.bq, project.settings, source='ai-weekly',
                       clock=lambda: project.now, out=lambda _: None)
    assert len(built) == 13
    mentions = project.rows('mart.ai_brand_mentions_weekly_v1')
    assert mentions
    assert {row['source'] for row in mentions} == {'peec'}
    assert project.rows('mart.ai_prompt_results_v2') == []
