"""Offline Google API windows through the real runner and local raw store."""
import json
from pathlib import Path

import httpx

from brand_presence import runner
from brand_presence.runs import Period, period_start
from brand_presence.settings import Settings
from brand_presence.sources.registry import SOURCES
from project import Project

FIXTURES = Path(__file__).parent / 'fixtures'


def fixture(source, name):
    return json.loads((FIXTURES / source / f'{name}.json').read_text())


class SearchConsole:
    def __init__(self, router):
        self.calls = []
        self.unfinished = '2026-03-02'
        self.empty = False
        router.post(url__regex=r'https://searchconsole.googleapis.com/.*').mock(
            side_effect=self.query)

    def query(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        assert request.headers['authorization'] == 'Bearer example-token'
        if body['dataState'] == 'all':
            answer = {'responseAggregationType': 'byProperty',
                      'metadata': {'firstIncompleteDate': self.unfinished}}
        else:
            part = {'query': 'queries', 'page': 'pages', 'device': 'devices'}.get(
                body['dimensions'][-1], 'totals')
            answer = fixture('search_console', part)
            if self.empty:
                answer['rows'] = []
        return httpx.Response(200, json=answer)


def prepared(tmp_path, router, source='search-console'):
    project = Project(tmp_path, router)
    config = project.settings.model_dump()
    config['web_properties'] = [dict(web_property_id='example-site', client_brand_id='aardvark',
                                     source=source, address=('100000001' if source == 'ga4'
                                                            else 'sc-domain:aardvark.example'))]
    if source == 'google-ads':
        config['web_properties'] = []
        config['ad_accounts'] = [dict(ad_account_id='example-ads', client_brand_id='aardvark',
                                     customer_id='1000000001')]
    project.settings = Settings.model_validate(config)
    project.bq.settings = project.settings
    vendor = (GoogleAds(router) if source == 'google-ads' else
              Ga4(router) if source == 'ga4' else SearchConsole(router))
    return project, vendor


def run(project, source='search-console', period='2026-W09', wait_seconds=2400):
    with httpx.Client() as client:
        world = runner.World(project.bq, client, ads_keys() if source == "google-ads" else {},
                             clock=lambda: project.now,
                             sleep=lambda _: None, status_out=project.lines.append,
                             tokens=lambda scope: 'example-token', wait_seconds=wait_seconds)
        named = Period(period, period_start(period)) if period else None
        return runner.run(SOURCES[source](), project.settings, world, period=named)


class FakeTokens:
    def __init__(self, value='example-token'):
        self.value = value
        self.scopes = []

    def __call__(self, scope):
        self.scopes.append(scope)
        return self.value

    def asked(self):
        return self.scopes


class Ga4:
    def __init__(self, router):
        self.calls = []
        self.answers = {}
        router.post(url__regex=r'https://analyticsdata.googleapis.com/.*').mock(
            side_effect=self.query)

    def query(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        assert request.headers['authorization'] == 'Bearer example-token'
        part = {'sessionDefaultChannelGroup': 'engagement', 'landingPage': 'landing_pages',
                'city': 'engagement_cities'}[body['dimensions'][-1]['name']]
        answer = self.answers.get(part, part)
        if isinstance(answer, list):
            answer = answer[body['offset'] // 100_000]
        if isinstance(answer, str):
            answer = fixture('ga4', answer)
        return httpx.Response(200, json=answer)


def ads_keys():
    from brand_presence.vendors.google_ads import SECRETS
    return {name: 'example-secret' for name in SECRETS[:-1]}


class GoogleAds:
    def __init__(self, router):
        from brand_presence.vendors.google_ads import TOKEN_URL
        self.calls = []
        self.answers = {}
        router.post(TOKEN_URL).respond(200, json={'access_token': 'example-token'})
        router.post(url__regex=r'https://googleads.googleapis.com/.*').mock(
            side_effect=self.query)

    def query(self, request):
        from brand_presence.sources.google_ads_reports import DETAILS, REPORTS, query_of
        body = json.loads(request.content)
        self.calls.append(body)
        assert request.headers['authorization'] == 'Bearer example-token'
        if body['query'] == query_of(DETAILS):
            part = 'details_first'
        else:
            part = next(name for name, report in REPORTS.items()
                        if body['query'].split(' FROM ')[0] == query_of(report).split(' FROM ')[0])
        answer = self.answers.get(part, 'keywords_three' if part == 'keywords' else part)
        if isinstance(answer, str):
            answer = fixture('google_ads', answer)
        if isinstance(answer, list):
            token = body.get('pageToken')
            index = 0 if token is None else next(i + 1 for i, p in enumerate(answer)
                                                 if p.get('nextPageToken') == token)
            answer = answer[index]
        return httpx.Response(200, json=answer)
