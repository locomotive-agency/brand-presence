import csv
import os
from pathlib import Path

from typer.testing import CliRunner

from brand_presence.cli import app
from brand_presence.reports.definition import load

EXPECTED = Path(__file__).parents[1] / 'src/brand_presence/example/expected'


def test_example_builds_every_report_without_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(('GOOGLE_', 'DATAFORSEO_', 'CLORO_', 'PEEC_', 'SYFTEN_')):
            monkeypatch.delenv(name)
    result = CliRunner().invoke(app, ['example', '--output', 'output'])
    assert result.exit_code == 0, result.output
    reports = load()
    assert {p.stem for p in EXPECTED.glob('*.csv')} == {r.name for r in reports}
    for report in reports:
        path = tmp_path / 'output' / f'{report.name}.csv'
        assert path.read_bytes() == (EXPECTED / path.name).read_bytes()
        with path.open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        assert rows, report.name
        assert f'{report.name}: {len(rows)} rows' in result.output
    def read(name):
        with (tmp_path / 'output' / f'{name}.csv').open(newline='') as stream:
            return list(csv.DictReader(stream))

    volume = read('search_volume_history_v1')
    assert [(r['month'], r['search_volume']) for r in volume if r['term_type'] == 'brand'] == [
        ('2025-12-01', '410'), ('2026-01-01', '290'), ('2026-02-01', '320')]
    ai = read('ai_brand_mentions_weekly_v1')
    assert [(r['brand'], r['prompts_total'], r['prompts_mentioned'], r['mention_rate'])
            for r in ai if r['source'] == 'cloro' and r['engine'] == 'chatgpt'] == [
                ('aardvark', '2', '1', '0.5'), ('capybara', '2', '1', '0.5')]
    assert [r['match_date'] for r in read('fct_mentions_v1')] == [
        '2026-02-23', '2026-03-02', '2026-03-02', '2026-03-01']
    html = (tmp_path / 'output/report.html').read_text()
    assert 'Search volume trend' in html
    assert 'AI answer brand share' in html
    assert 'Mentions by week' in html
    assert 'https://' not in html
    assert '50.0%' in html
    assert '<td>2026-02-23</td><td>brand</td><td>2</td>' in html
    assert '<td>2026-03-02</td><td>brand</td><td>2</td>' in html


def test_example_preserves_existing_output(tmp_path):
    saved = tmp_path / 'keep.csv'
    saved.write_text('keep this\n')
    result = CliRunner().invoke(app, ['example', '--output', str(tmp_path)])
    assert result.exit_code != 0
    assert 'new or empty output directory' in str(result.exception)
    assert saved.read_text() == 'keep this\n'
    assert list(tmp_path.iterdir()) == [saved]
