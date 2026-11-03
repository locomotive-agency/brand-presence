import pytest

from brand_presence.settings import Settings
from project import configuration


def configured(source='ga4', **fields):
    config = configuration()
    config['web_properties'] = [dict(web_property_id='export-site', client_brand_id='aardvark',
                                    source=source, address='123456' if source == 'ga4' else
                                    'sc-domain:aardvark.example', **fields)]
    return config


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
def test_route_defaults_to_api(source):
    assert Settings.model_validate(configured(source)).web_properties[0].route == 'api'


@pytest.mark.parametrize('source', ['ga4', 'search-console'])
@pytest.mark.parametrize('fields', [
    {'route': 'export'}, {'route': 'export', 'project': 'example-export'},
    {'route': 'export', 'project': 'bad`;SELECT', 'dataset': 'events'},
    {'route': 'export', 'project': 'example-export', 'dataset': 'bad.name'},
    {'route': 'export', 'project': 'example-export', 'dataset': 'x' * 1025},
    {'project': 'example-export', 'dataset': 'events'}, {'route': 'unknown'},
])
def test_export_configuration_is_checked(source, fields):
    with pytest.raises(ValueError):
        Settings.model_validate(configured(source, **fields))


def test_export_and_api_can_share_a_project():
    config = configured(route='export', project='example-export', dataset='analytics_123456')
    config['web_properties'].append(dict(web_property_id='api-site', client_brand_id='aardvark',
                                         source='ga4', address='654321'))
    settings = Settings.model_validate(config)
    assert [p.route for p in settings.web_properties] == ['export', 'api']
    assert settings.web_properties[0].bigquery_export == 'example-export.analytics_123456'
    assert settings.maximum_bytes_billed == 20 * 1024**3


@pytest.mark.parametrize('dataset', ['events', 'analytics_0', 'analytics_0123456',
                                     'analytics_654321', 'analytics_123456_suffix'])
def test_ga4_export_dataset_must_belong_to_the_property(dataset):
    with pytest.raises(ValueError, match='export-site.*analytics_123456'):
        Settings.model_validate(configured(route='export', project='example-export',
                                             dataset=dataset))


def test_ga4_export_accepts_its_property_dataset():
    settings = Settings.model_validate(configured(route='export', project='example-export',
                                                  dataset='analytics_123456'))
    assert settings.web_properties[0].bigquery_export == 'example-export.analytics_123456'


@pytest.mark.parametrize('dataset', ['events', 'analytics_0', 'analytics_0123456'])
def test_ga4_export_parser_requires_a_property_dataset(dataset):
    from brand_presence.sources.ga4_export import NotAnExport, export_of

    with pytest.raises(NotAnExport):
        export_of('example-export.' + dataset)


def test_search_console_export_accepts_custom_dataset():
    settings = Settings.model_validate(configured('search-console', route='export',
                                                  project='example-export', dataset='events'))
    assert settings.web_properties[0].dataset == 'events'
