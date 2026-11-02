from brand_presence.reports.definition import load
from report_samples import TODAY, ai_answer, ai_brand, ai_tables, citation_tables
from report_sql import run_statement


def test_all_cloro_reports_known_results():
    reports = {r.name: r for r in load('ai-answers')}
    tables = ai_tables([ai_answer(text='Rival first.', links=('https://sample.example/',))],
                      [ai_brand('sample', cited=True), ai_brand('rival', 0, 1), ai_brand('other')])
    fact = tables['core.fct_ai_answers_v1']
    assert {r['subject_brand_id']: r['named'] for r in fact} == {
        'sample': False, 'rival': True, 'other': False}
    assert [r['link'] for r in tables['core.fct_ai_cited_links_v1']] == [
        'https://sample.example/']
    shown = run_statement(reports['ai_prompt_results_v2'], tables, today=TODAY)
    assert {r['brand']: r['brand_mentioned'] for r in shown} == {
        'sample': False, 'rival': True, 'other': False}
    monthly = run_statement(reports['ai_keyword_monthly_v2'], {
        'mart.ai_prompt_results_v2': shown}, today=TODAY)
    own = next(r for r in monthly if r['brand'] == 'sample')
    assert (own['prompts_run'], own['share_of_voice'], own['gap_count']) == (1, 0, 1)
    cited = run_statement(reports['ai_citations_v1'], citation_tables(shown), today=TODAY)
    assert len(cited) == 2
    assert {r["level"] for r in cited} == {"domain", "page"}
    assert all(r["citations"] == 1 for r in cited)
    assert cited[0]['domain'] == 'sample.example'
