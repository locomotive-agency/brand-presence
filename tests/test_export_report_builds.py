from datetime import date

import pytest

from brand_presence.reports import runner
from brand_presence.settings import Settings
from export_project import prepared
from google_project import SearchConsole, run
from google_project import prepared as api_prepared
from reporting import install_sources


@pytest.mark.parametrize('route', ['api', 'export', 'mixed'])
def test_combined_reports_build_for_every_route_and_repeat(tmp_path, respx_mock, route):
    if route == 'api':
        project, _ = api_prepared(tmp_path, respx_mock, 'ga4')
    else:
        project, _ = prepared(tmp_path, respx_mock, 'ga4', mixed=route == 'mixed')
    config = project.settings.model_dump()
    for p in list(config['web_properties']):
        config['web_properties'].append(dict(p, web_property_id=p['web_property_id'] + '-gsc',
                                             source='search-console',
                                             address=f"sc-domain:{p['web_property_id']}.example"))
    project.settings = Settings.model_validate(config)
    project.bq.settings = project.settings
    SearchConsole(respx_mock)
    assert project.run().status == 'success'
    for source in ('ga4', 'search-console'):
        result = run(project, source)
        assert result.status == 'success', result.lines
    install_sources(project.fake, project.settings)
    built = []
    for group in ('ga4', 'paid'):
        built += runner.run(project.bq, project.settings, source=group,
                            clock=lambda: project.now, out=lambda _: None)
    before = {table: project.rows(table) for table in built}
    assert before['mart.engagement_monthly_v1']
    assert before['mart.search_demand_monthly_v1']
    assert before['mart.growth_v1']
    if route == 'api':
        assert before['core.fct_ga4_export_v1'] == []
        assert before['core.fct_gsc_export_v1'] == []
        assert before['mart.page_engagement_monthly_v1'] == []
    else:
        page = next(r for r in before['core.fct_ga4_export_pages_v1']
                    if r['month'] == date(2026, 2, 1))
        assert (page['page_views'], page['users'], page['engagement_seconds']) == (80, 50, 250)
        gsc = next(r for r in before['core.fct_gsc_export_v1'] if r['row_kind'] == 'total')
        assert (gsc['clicks'], gsc['anonymized_clicks'], gsc['position_weighted']) == (12, 2, 300)
        assert before['mart.page_engagement_monthly_v1'][0]['avg_engagement_sec'] == 5
    project.later()
    for group in ('ga4', 'paid'):
        runner.run(project.bq, project.settings, source=group,
                   clock=lambda: project.now, out=lambda _: None)
    assert {table: project.rows(table) for table in built} == before
