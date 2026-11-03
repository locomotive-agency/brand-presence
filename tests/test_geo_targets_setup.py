import csv
import io
import zipfile

import pytest

from brand_presence import geo_targets
from brand_presence.errors import UserError
from brand_presence.settings import Settings
from test_export_settings import configured


def archive(rows=None):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(geo_targets.COLUMNS)
    writer.writerows(rows or [
        ['900001', 'Netherlands', 'Netherlands', '', 'NL', 'Country', 'Active'],
        ['900002', 'Example City', 'Example City,Netherlands', '900001', 'NL', 'City', 'Active'],
    ])
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as zipped:
        zipped.writestr('geotargets-2026-01-01.csv', stream.getvalue())
    return data.getvalue()


def test_setup_downloads_validates_and_dates_official_copy(tmp_path, respx_mock):
    import httpx

    settings = Settings.model_validate(configured(route='export', project='example-export',
                                                  dataset='analytics_123456'))
    settings.state_dir = tmp_path
    url = geo_targets.FILES + 'geotargets-2026-01-01.csv.zip'
    respx_mock.get(url).respond(content=archive())
    with httpx.Client() as http:
        held = geo_targets.download(settings, '2026-01-01', http)
    assert held.date == '2026-01-01'
    assert held.country('NL')[0].active
    assert geo_targets.validate(settings) == '2026-01-01'
    assert not list(tmp_path.rglob('*.zip'))


def test_api_needs_no_copy_and_export_needs_validated_names(tmp_path):
    settings = Settings.model_validate(configured())
    settings.state_dir = tmp_path
    assert geo_targets.validate(settings) is None
    settings.web_properties[0].route = 'export'
    with pytest.raises(UserError, match='setup-geo-targets'):
        geo_targets.validate(settings)


@pytest.mark.parametrize('rows', [
    [['wrong']],
    [['bad-id', 'Netherlands', 'Netherlands', '', 'NL', 'Country', 'Active']],
    [['900001', 'Netherlands', 'Netherlands', '', 'NL', 'Country', 'Active']] * 2,
])
def test_bad_download_keeps_previous_copy(tmp_path, respx_mock, rows):
    import httpx

    settings = Settings.model_validate(configured())
    settings.state_dir = tmp_path
    route = respx_mock.get(geo_targets.FILES + 'geotargets-2026-01-01.csv.zip')
    route.respond(content=archive())
    with httpx.Client() as http:
        geo_targets.download(settings, '2026-01-01', http)
        before = geo_targets.local(settings)
        route.respond(content=archive(rows))
        with pytest.raises(UserError):
            geo_targets.download(settings, '2026-01-01', http)
    assert geo_targets.local(settings).header == before.header
