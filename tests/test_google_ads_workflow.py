import pytest

from brand_presence.bigquery import Warehouse
from google_project import prepared, run
from project import Killed


def test_google_ads_full_run_and_repeat(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock, 'google-ads')
    result = run(project, 'google-ads')
    assert result.status == 'success', result.lines
    assert len(project.rows('stg.google_ads_campaigns')) == 6
    assert len(project.rows('stg.google_ads_pulls')) == 4
    assert len(project.rows('raw.google_ads')) == 8
    count = len(vendor.calls)
    assert run(project, 'google-ads').status == 'success'
    assert len(vendor.calls) == count
    assert len(project.rows('stg.google_ads_campaigns')) == 6


@pytest.mark.parametrize('boundary', ['save_raw', 'load_raw', 'replace'])
def test_google_ads_interrupted_resume(tmp_path, respx_mock, monkeypatch, boundary):
    project, vendor = prepared(tmp_path, respx_mock, 'google-ads')
    original = getattr(Warehouse, boundary)

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, boundary, killed)
        with pytest.raises(Killed):
            run(project, 'google-ads')
    project.later()
    assert run(project, 'google-ads').status == 'success'
    assert len(vendor.calls) == 5
    assert len(project.rows('stg.google_ads_campaigns')) == 6
    assert len(project.rows('ops.google_ads_tasks')) == 4


def test_google_ads_reports_known_results(tmp_path, respx_mock):
    from datetime import date

    import sqlglot

    from brand_presence.reports.definition import load
    from brand_presence.schema import MODELS
    from report_sql import _unnested, run_statement
    from reporting import evaluate

    project, _ = prepared(tmp_path, respx_mock, 'google-ads')
    project.settings.brands[0].aliases = ['aardvark']
    from brand_presence.settings import Modifier
    project.settings.modifiers = [Modifier(modifier='price', match_order=1)]
    assert run(project, 'google-ads').status == 'success'
    models = {m.table: m for m in MODELS}
    tables = {name: project.rows(name) for name in models}
    schemas = {name: {k: 'STRING' for k in model.model_fields} for name, model in models.items()}
    for name in ('brands', 'modifiers', 'ad_accounts', 'benchmark_keywords'):
        tables['ref.current_' + name] = tables['ref.' + name]
        schemas['ref.current_' + name] = schemas['ref.' + name]
    for report in load('google-ads'):
        if report.fact:
            tree = sqlglot.parse_one(report.statement, read='bigquery')
            given = {}
            for name, rows in tables.items():
                dataset, table = name.split('.')
                given.setdefault(dataset, {})[table] = rows
            tree = _unnested(tree, given)
            sqlite_tables = {f'{d}.{n}': (list(rows[0]) if rows else list(schemas[f'{d}.{n}']),
                                          rows)
                             for d, entries in given.items() for n, rows in entries.items()}
            rows = evaluate(tree.sql(dialect='bigquery'), sqlite_tables)
            rows = [dict(r, month=date.fromisoformat(r['month']), pulled_at=project.now)
                    for r in rows]
        else:
            rows = run_statement(report, tables, today=date(2026, 3, 2), schemas=schemas)
        tables[report.table] = rows
        schemas[report.table] = {c['name']: c['type'] for c in report.columns}
        from dataclasses import replace
        for _, sql in report.checks:
            check = replace(report, statement=sql)
            assert run_statement(check, tables, today=date(2026, 3, 2),
                                 pulled_at=project.now, schemas=schemas) == []
    delivery = next(r for r in tables['core.fct_ads_v1']
                    if r['country_code'] == 'NL' and r['location_type'] == 'presence')
    assert (delivery['impressions'], delivery['clicks'], delivery['cost_micros']) == (
        300, 12, 1730000)
    assert delivery['days_covered'] == 6
    boots = next(r for r in tables['core.fct_ads_keywords_v1']
                 if r['keyword_text'] == 'aardvark boots')
    assert boots['impressions'] == 129
    price = next(r for r in tables['core.fct_ads_search_terms_v1']
                 if r['search_term'] == 'sample brand aardvark price')
    assert (price['impressions'], price['clicks'], price['conversions']) == (620, 40, 3)
    auction = next(r for r in tables['core.fct_ads_auction_v1'] if r['campaign_id'] == 101)
    assert (auction['impressions'], auction['eligible_impressions']) == (3222, 7488)
    assert auction['impression_share_bounded']
    history = next(r for r in tables['mart.paid_history_v1'] if r['country_code'] == 'NL')
    assert (history['impressions'], history['cost']) == (300, 1.73)
    assert history['campaign_type'] == 'Search (unlabelled)'
    modifier = next(r for r in tables['mart.brand_modifier_monthly_v1']
                    if r['modifier'] == 'price')
    assert (modifier['search_terms'], modifier['clicks'], modifier['conversion_rate']) == (
        1, 40, 0.075)
    assert modifier['is_complete_month'] is False


def test_google_ads_resume_after_collected_reuses_account_details(
    tmp_path, respx_mock, monkeypatch,
):
    from brand_presence import ledger

    project, vendor = prepared(tmp_path, respx_mock, 'google-ads')
    original = ledger.Ledger.rewrite

    def killed(self, rows):
        original(self, rows)
        if any(r.state == ledger.COLLECTED for r in rows):
            raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(ledger.Ledger, 'rewrite', killed)
        with pytest.raises(Killed):
            run(project, 'google-ads')
    project.later()
    assert run(project, 'google-ads').status == 'success'
    assert len(vendor.calls) == 5


@pytest.mark.parametrize('change, message', [
    ('customer', 'customer_id'), ('duplicate', 'ad_account_id'),
    ('brand', 'client brand'), ('criterion', 'criterion'), ('modifiers', 'match_order'),
])
def test_google_ads_config_validation(tmp_path, respx_mock, change, message):
    from brand_presence.settings import Settings

    project, _ = prepared(tmp_path, respx_mock, 'google-ads')
    config = project.settings.model_dump()
    if change == 'customer':
        config['ad_accounts'][0]['customer_id'] = '../example'
    elif change == 'duplicate':
        config['ad_accounts'] *= 2
    elif change == 'brand':
        config['ad_accounts'][0]['client_brand_id'] = 'not-listed'
    elif change == 'criterion':
        config['locations'] = [dict(location_id=name, kind='city', name=name,
                                    canonical_name=name + ',Netherlands', country_code='NL',
                                    sources=['google-ads'], google_criterion_id='1000000')
                               for name in ('example-one', 'example-two')]
    else:
        config['modifiers'] = [dict(modifier=m, match_order=1) for m in ('price', 'reviews')]
    with pytest.raises(ValueError, match=message):
        Settings.model_validate(config)


def test_google_ads_next_request_crash_reuses_collected_account(tmp_path, respx_mock, monkeypatch):
    from brand_presence import ledger

    project, vendor = prepared(tmp_path, respx_mock, 'google-ads')
    original = ledger.Ledger.add
    count = 0

    def killed(self, *args, **kwargs):
        nonlocal count
        result = original(self, *args, **kwargs)
        count += 1
        if count == 2:
            raise Killed()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ledger.Ledger, 'add', killed)
        with pytest.raises(Killed):
            run(project, 'google-ads')
    project.later()
    assert run(project, 'google-ads').status == 'success'
    assert len(vendor.calls) == 5


def test_google_ads_partial_page_resume(tmp_path, respx_mock, monkeypatch):
    import json

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
    project.later()
    assert run(project, 'google-ads').status == 'success'
    calls = [c for c in vendor.calls if ' FROM campaign ' in c['query']]
    assert len(calls) == 2
    assert 'pageToken' not in calls[0] and calls[1]['pageToken']
    assert len(project.rows('stg.google_ads_campaigns')) == 6


@pytest.mark.parametrize('part, answer', [
    ('campaigns', 'other_currency'), ('campaigns', 'other_customer_row'),
    ('campaigns', 'partial_failure'), ('campaigns', 'date_outside_window'),
    ('details_first', 'details_other_customer'),
])
def test_google_ads_rejects_unusable_answers(tmp_path, respx_mock, part, answer):
    project, vendor = prepared(tmp_path, respx_mock, 'google-ads')
    vendor.answers[part] = answer
    assert run(project, 'google-ads').status != 'success'
    assert project.rows('stg.google_ads_campaigns') == []


def test_google_ads_environment_and_optional_login(monkeypatch):
    from brand_presence.errors import UserError
    from brand_presence.settings import credentials
    from brand_presence.vendors import google_ads

    names = ('DEVELOPER_TOKEN', 'CLIENT_ID', 'CLIENT_SECRET', 'REFRESH_TOKEN')
    for name in names:
        monkeypatch.delenv('GOOGLE_ADS_' + name, raising=False)
    monkeypatch.delenv('GOOGLE_ADS_LOGIN_CUSTOMER_ID', raising=False)
    with pytest.raises(UserError, match='GOOGLE_ADS_CLIENT_ID'):
        credentials('google-ads')
    for name in names:
        monkeypatch.setenv('GOOGLE_ADS_' + name, 'example-value')
    assert google_ads.LOGIN_CUSTOMER_ID not in credentials('google-ads')
    monkeypatch.delenv('GOOGLE_ADS_DEVELOPER_TOKEN')
    assert google_ads.DEVELOPER_TOKEN not in credentials('google-ads')
    monkeypatch.setenv('GOOGLE_ADS_LOGIN_CUSTOMER_ID', '100-000-0001')
    assert credentials('google-ads')[google_ads.LOGIN_CUSTOMER_ID] == '1000000001'
    monkeypatch.setenv('GOOGLE_ADS_LOGIN_CUSTOMER_ID', 'invalid')
    with pytest.raises(UserError, match='ten digits'):
        credentials('google-ads')


def test_google_ads_listed_and_other_geographies(tmp_path, respx_mock):
    from brand_presence.settings import Settings

    project, _ = prepared(tmp_path, respx_mock, 'google-ads')
    config = project.settings.model_dump()
    config['countries'].append(dict(country_code='PT', language='pt', canonical_name='Portugal'))
    config['locations'] = [
        dict(location_id='example-city', kind='city', country_code='PT', name='Example City',
             canonical_name='Example City,Portugal', google_criterion_id='1011759',
             sources=['google-ads']),
        dict(location_id='example-region', kind='region', country_code='NL',
             name='Example Region', canonical_name='Example Region,Netherlands',
             region_code='NL-NH', google_criterion_id='20766', sources=['google-ads']),
    ]
    project.settings = Settings.model_validate(config)
    result = run(project, 'google-ads')
    assert result.status == 'success', result.lines
    cities = project.rows('stg.google_ads_geo_cities')
    listed = next(r for r in cities if r['city_id'] == 'example-city')
    other = next(r for r in cities if r['city_id'] == 'other')
    assert (listed['impressions'], other['impressions']) == (120, 75)
    regions = project.rows('stg.google_ads_geo_regions')
    listed = next(r for r in regions if r['region_id'] == 'example-region'
                  and r['location_type'] == 'presence')
    other = next(r for r in regions if r['region_id'] == 'other')
    assert (listed['impressions'], other['impressions']) == (200, 75)


@pytest.mark.parametrize('source', ['search-console', 'ga4', 'google-ads'])
def test_google_identity_cannot_be_rebound(tmp_path, respx_mock, source):
    from brand_presence.client_ref import apply
    from brand_presence.errors import UserError

    project, _ = prepared(tmp_path, respx_mock, source)
    apply(project.bq, project.settings, project.now)
    if source == 'google-ads':
        project.settings.ad_accounts[0].customer_id = '1000000002'
    else:
        project.settings.web_properties[0].address = (
            '100000002' if source == 'ga4' else 'sc-domain:another.example')
    with pytest.raises(UserError, match='cannot be rebound'):
        apply(project.bq, project.settings, project.now)
