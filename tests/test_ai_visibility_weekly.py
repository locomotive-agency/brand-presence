"""Weekly statements executed over fixture facts through the existing prompt reports."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from functools import cache

import pytest

from brand_presence.reports.definition import load
from report_samples import TODAY, ai_answer, ai_brand, ai_tables
from report_sql import run_check, run_statement

MENTIONS = 'ai_brand_mentions_weekly_v1'
CITATIONS = 'ai_domain_citations_weekly_v1'
OVERVIEW = 'ai_overview_presence_weekly_v1'
WEEK = date(2025, 12, 29)


@cache
def _fixture_tables():
    """Distinct runs of one prompt, a year boundary, and a local Monday still Sunday UTC."""
    tables = ai_tables([ai_answer()], [ai_brand('sample')])
    tables['core.fct_ai_answers_v1'] = []
    tables['core.fct_ai_cited_links_v1'] = []
    tables['ref.current_locations'][0]['sources'].append('peec')
    tables['core.fct_peec_answers_v1'] = []
    tables['core.fct_peec_cited_links_v1'] = []
    tables['stg.peec_pulls'] = []
    tables['stg.peec_prompts'] = []
    moments = [datetime(2025, 12, 28, 23, 30, tzinfo=UTC),
               datetime(2026, 1, 1, 12, tzinfo=UTC),
               datetime(2026, 1, 4, 20, tzinfo=UTC),
               datetime(2026, 1, 4, 23, 30, tzinfo=UTC)]
    for index, moment in enumerate(moments):
        links = ('https://sample.example/a', 'https://sample.example/b',
                 'https://www.rival.example/page', 'https://third.example/page') if index == 0 \
            else ('https://rival.example/other',)
        answer = ai_answer(answered=moment, links=links)
        brands = [ai_brand('sample', 0 if index < 2 else None,
                           1 if index == 0 else 3 if index == 1 else None,
                           cited=index == 0, answered=moment),
                  ai_brand('rival', 2, 2, cited=True, answered=moment),
                  ai_brand('other', answered=moment)]
        for row in [answer, *brands]:
            row.update(period=f'sample-period-{index}', engine='ai-overview')
        current = ai_tables([answer], brands)
        for table in ('core.fct_ai_answers_v1', 'core.fct_ai_cited_links_v1'):
            for row in current[table]:
                row['pulled_at'] = moment + timedelta(hours=1)
            tables[table].extend(current[table])
        local_date = (moment + timedelta(hours=1)).date()
        common = dict(mapping='sample-mapping', answer_key=f'sample-answer-{index}',
                      request_key=f'sample-request-{index}', observation_date=local_date,
                      prompt_version='sample-version', citation_known=True,
                      vendor_mentioned=None, vendor_position=None)
        for old, new in [('core.fct_ai_answers_v1', 'core.fct_peec_answers_v1'),
                         ('core.fct_ai_cited_links_v1', 'core.fct_peec_cited_links_v1')]:
            tables[new].extend({**r, **common} for r in current[old])
        tables['stg.peec_pulls'].append(dict(request_key=common['request_key'],
            period=answer['period'], pulled_at=moment, complete_snapshot=True))
    tables['stg.peec_prompts'] = [dict(mapping='sample-mapping', prompt_id='sample-prompt',
        version='sample-version', prompt_text='Sample prompt', topic_id='sample-topic',
        is_archived=False, observed_at=moments[0])]
    return tables


def fixture_tables():
    return deepcopy(_fixture_tables())


@cache
def reports():
    return {r.name: r for r in load()}


def serving(tables):
    for name in ('ai_prompt_results_v2', 'peec_prompt_results_v1'):
        tables[f'mart.{name}'] = run_statement(reports()[name], tables, today=TODAY,
                                             client_time_zone='Europe/Amsterdam')
    return tables


@cache
def _sample():
    return serving(fixture_tables())


def sample():
    return deepcopy(_sample())


def weekly(name, tables, statement=None):
    report = reports()[name]
    if statement is not None:
        report = replace(report, statement=statement)
    return run_statement(report, tables, today=TODAY)


@pytest.mark.parametrize('name', [MENTIONS, CITATIONS, OVERVIEW])
def test_missing_peec_keyword_groups_pass_standard_checks_as_unknown(name):
    tables = sample()
    assert tables['mart.peec_prompt_results_v1']
    assert all(r['keyword_group'] is None for r in tables['mart.peec_prompt_results_v1'])
    report = reports()[name]
    rows = weekly(name, tables)
    assert len({tuple(r[k] for k in report.definition['key']) for r in rows}) == len(rows)
    assert all(r[c['name']] is not None for r in rows for c in report.columns
               if not c['nullable'])
    assert {r['keyword_group'] for r in rows if r['source'] == 'peec'} == {'unknown'}
    assert {r['keyword_group'] for r in rows if r['source'] == 'cloro'} == {'shoes'}
    peec = [r for r in rows if r['source'] == 'peec' and r['week_start'] == WEEK]
    if name == MENTIONS:
        own, = [r for r in peec if r['brand'] == 'sample']
        assert (own['prompts_total'], own['prompts_mentioned']) == (3, 2)
    elif name == CITATIONS:
        own, = [r for r in peec if r['domain'] == 'sample.example']
        assert (own['citation_count'], own['prompts_citing']) == (2, 1)
    else:
        own, = peec
        assert (own['queries_total'], own['queries_with_aio']) == (3, 3)


@pytest.mark.parametrize('source', ['cloro', 'peec'])
def test_mentions_run_counts_rates_positions_and_weeks(source):
    tables = sample()
    rows = weekly(MENTIONS, tables)
    own = next(r for r in rows if r['source'] == source and r['brand'] == 'sample'
               and r['week_start'] == WEEK)
    assert (own['prompts_total'], own['prompts_mentioned']) == (3, 2)
    assert own['mention_rate'] == pytest.approx(2 / 3)
    assert own['cited_rate'] == pytest.approx(1 / 3)
    assert own['avg_mention_position'] == 2
    rival = next(r for r in rows if r['source'] == source and r['brand'] == 'rival'
                 and r['week_start'] == WEEK)
    assert rival['cited_rate'] == 1  # The client's repeated mark would give one third.
    assert all(r['prompts_mentioned'] <= r['prompts_total'] for r in rows)
    assert {r['week_start'] for r in rows} == {WEEK, date(2026, 1, 5)}
    assert {r['keyword_group'] for r in rows if r['source'] == source} == (
        {'shoes'} if source == 'cloro' else {'unknown'})
    assert all(r['avg_mention_position'] is None for r in rows if r['brand'] == 'other')


@pytest.mark.parametrize('source', ['cloro', 'peec'])
def test_citations_count_pages_distinct_prompts_and_positions(source):
    rows = weekly(CITATIONS, sample())
    own = next(r for r in rows if r['source'] == source and r['domain'] == 'sample.example')
    assert (own['citation_count'], own['prompts_citing']) == (2, 1)
    assert own['avg_citation_position'] is None
    rival = next(r for r in rows if r['source'] == source and r['domain'] == 'rival.example'
                 and r['week_start'] == WEEK)
    assert (rival['citation_count'], rival['prompts_citing']) == (3, 1)
    assert all(r['avg_citation_position'] is None for r in rows if r['source'] == source)
    assert any(r['domain'] == 'third.example' for r in rows)
    assert {r['week_start'] for r in rows} == {WEEK, date(2026, 1, 5)}


@pytest.mark.parametrize('source', ['cloro', 'peec'])
def test_overview_counts_answered_only_and_each_run_once(source):
    tables = fixture_tables()
    for name in ('core.fct_ai_answers_v1', 'core.fct_peec_answers_v1'):
        for row in tables[name]:
            if row['period'] == 'sample-period-1':
                row.update(state='none shown', named=None, cited=None, mention_rank=None)
            if row['period'] == 'sample-period-3':
                row['engine'] = 'chatgpt'
    rows = weekly(OVERVIEW, serving(tables))
    row = next(r for r in rows if r['source'] == source)
    assert (row['week_start'], row['queries_total'], row['queries_with_aio']) == (WEEK, 3, 2)
    assert len(rows) == 2


def test_unknowns_stale_facts_and_peec_snapshots():
    tables = fixture_tables()
    for name in ('core.fct_ai_answers_v1', 'core.fct_peec_answers_v1'):
        for row in list(tables[name]):
            if row['subject_brand_id'] == 'sample' and row['period'] == 'sample-period-0':
                tables[name].append({**row, 'pulled_at': row['pulled_at'] - timedelta(hours=1),
                                     'cited': False, 'mention_rank': 9})
                row.update(cited=None, mention_rank=None)
    rows = weekly(MENTIONS, serving(tables))
    own = [r for r in rows if r['brand'] == 'sample' and r['week_start'] == WEEK]
    assert len(own) == 2
    assert all(r['cited_rate'] is None and r['avg_mention_position'] is None for r in own)
    for row in tables['stg.peec_pulls']:
        row['pulled_at'] += timedelta(days=1)
    serving(tables)
    assert not any(r['source'] == 'peec' for r in weekly(MENTIONS, tables))
    assert not any(r['source'] == 'peec' for r in weekly(CITATIONS, tables))


def test_checks_reject_impossible_counts_and_unknown_states():
    tables = sample()
    for name, changed in [(MENTIONS, {'prompts_mentioned': 99}),
                          (CITATIONS, {'prompts_citing': 99}),
                          (CITATIONS, {'avg_citation_position': 1.0}),
                          (OVERVIEW, {'queries_with_aio': 99})]:
        rows = weekly(name, tables)
        report = reports()[name]
        assert run_check(report, 'weekly_values', {report.table: rows}) == []
        bad = {**rows[0], **changed}
        assert run_check(report, 'weekly_values', {report.table: [bad]}) == [bad]
    report = reports()[OVERVIEW]
    assert run_check(report, 'answer_states', tables) == []
    tables['mart.ai_prompt_results_v2'][0]['answer_state'] = 'unknown'
    assert run_check(report, 'answer_states', tables) == [{'answer_state': 'unknown'}]


@pytest.mark.parametrize('name,old,new', [
    (MENTIONS, 'COUNT(*) AS prompts_total', 'COUNT(*) + 1 AS prompts_total'),
    (CITATIONS, 'CAST(NULL AS FLOAT64)', 'CAST(1 AS FLOAT64)'),
    (OVERVIEW, "answer_state = 'answered'", "answer_state = 'none shown'"),
])
def test_statement_mutations_change_fixture_results(name, old, new):
    tables = sample()
    statement = reports()[name].statement
    assert old in statement
    assert weekly(name, tables, statement.replace(old, new)) != weekly(name, tables)


def test_peec_distinct_chats_in_one_run_survive_without_multiplying_cloro():
    tables = fixture_tables()
    for name in ('core.fct_peec_answers_v1', 'core.fct_peec_cited_links_v1'):
        tables[name].extend({**r, 'answer_key': 'sample-extra-answer'}
                            for r in list(tables[name]) if r['period'] == 'sample-period-0')
    serving(tables)
    rows = weekly(MENTIONS, tables)
    own = {r['source']: r for r in rows if r['brand'] == 'sample' and r['week_start'] == WEEK}
    assert own['cloro']['prompts_total'] == 3
    assert own['peec']['prompts_total'] == 4
    assert own['peec']['prompts_mentioned'] == 3
    citations = weekly(CITATIONS, tables)
    peec = next(r for r in citations if r['source'] == 'peec'
                and r['domain'] == 'sample.example')
    assert (peec['citation_count'], peec['prompts_citing']) == (4, 1)
