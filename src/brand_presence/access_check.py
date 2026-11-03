"""Inspect export tables and columns without reading exported rows."""
from dataclasses import dataclass

from google.api_core.exceptions import GoogleAPICallError

from brand_presence.sources import ga4_export, search_console_export

GA4_COLUMNS = {
    'user_pseudo_id': 'STRING', 'event_date': 'STRING', 'event_name': 'STRING',
    'event_timestamp': 'INTEGER', 'event_params.key': 'STRING',
    'event_params.value.int_value': 'INTEGER', 'event_params.value.string_value': 'STRING',
    'geo.country': 'STRING', 'geo.region': 'STRING', 'geo.city': 'STRING',
    'device.web_info.hostname': 'STRING',
    'session_traffic_source_last_click.cross_channel_campaign.default_channel_group': 'STRING',
}
GSC_COMMON = {
    'data_date': 'DATE', 'site_url': 'STRING', 'country': 'STRING', 'query': 'STRING',
    'search_type': 'STRING', 'device': 'STRING', 'is_anonymized_query': 'BOOLEAN',
    'clicks': 'INTEGER', 'impressions': 'INTEGER',
}
GSC_COLUMNS = {
    search_console_export.SITE_TABLE: {**GSC_COMMON, 'sum_top_position': 'INTEGER'},
    search_console_export.URL_TABLE: {**GSC_COMMON, 'url': 'STRING', 'sum_position': 'INTEGER'},
    'ExportLog': {'agenda': 'STRING', 'namespace': 'STRING', 'data_date': 'DATE',
                  'epoch_version': 'INTEGER', 'publish_time': 'TIMESTAMP'},
}


@dataclass(frozen=True)
class Check:
    property_id: str
    table: str
    problems: tuple[str, ...]

    @property
    def ok(self):
        return not self.problems

    def line(self):
        return f'{self.property_id}: {self.table}: ' + (
            '; '.join(self.problems) if self.problems else 'columns accessible')


def columns_of(schema, prefix=''):
    columns = {}
    for field in schema:
        name = prefix + field.name
        columns[name] = field.field_type
        columns.update(columns_of(field.fields, name + '.'))
    return columns


def run(bq, settings) -> list[Check]:
    checks = []
    for prop in settings.web_properties:
        if prop.route != 'export':
            continue
        dataset = search_console_export.dataset_of(prop.bigquery_export)
        try:
            metadata = bq.get_dataset(dataset)
            if not metadata.location:
                checks.append(Check(prop.web_property_id, 'dataset', ('location missing',)))
                continue
            tables = {item.table_id for item in bq.list_tables(dataset)}
        except GoogleAPICallError as error:
            checks.append(Check(prop.web_property_id, 'dataset',
                                (ga4_export.vendor_error(error).error_class,)))
            continue
        if prop.source == 'ga4':
            daily = sorted(t for t in tables if ga4_export._DAILY_TABLE.fullmatch(t)
                           and ga4_export._table_day(t.removeprefix('events_')) is not None)
            expected = {daily[-1]: GA4_COLUMNS} if daily else {'events_YYYYMMDD': GA4_COLUMNS}
        else:
            expected = GSC_COLUMNS
        for table, required in expected.items():
            problems = []
            if table not in tables:
                problems.append('table missing')
            else:
                try:
                    fields = columns_of(bq.get_table(f'{dataset}.{table}').schema)
                    for name, kind in required.items():
                        if name not in fields:
                            problems.append(f'missing column {name}')
                        elif fields[name] != kind:
                            problems.append(f'column {name} needs {kind}')
                except GoogleAPICallError as error:
                    problems.append(ga4_export.vendor_error(error).error_class)
            checks.append(Check(prop.web_property_id, table, tuple(problems)))
    return checks
