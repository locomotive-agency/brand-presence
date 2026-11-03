# API or BigQuery export

GA4 and Search Console can be read two ways. You choose per property.

| | API route | Export route |
| --- | --- | --- |
| Default | Yes | No |
| Needs | API access to the property | An existing BigQuery export |
| History | What the API still serves | Everything the export has kept since it was enabled |
| Cost | Free API, quotas | BigQuery scans, capped per query |

**Keep the API route if you have no export.** API-only setups need no export dataset,
no export permissions and no geo targets download.

Choose the export route when a property already exports to BigQuery and you need its
longer history or fuller totals. One project can mix API and export properties. There
is no automatic fallback. Reports use one route per property for all dates and never
fill export gaps with API figures.

## Set up the export in Google

- **GA4.** Follow Google's [BigQuery Export setup](https://support.google.com/analytics/answer/9823238).
  Create a BigQuery link in GA4 Admin and turn on the daily export. The kit reads
  `events_YYYYMMDD` tables, not intraday or user tables.
- **Search Console.** Follow Google's
  [bulk data export setup](https://support.google.com/webmasters/answer/12918484). The
  dataset holds `searchdata_site_impression`, `searchdata_url_impression` and `ExportLog`.

Neither export recreates data from before it was enabled.

## Configure a property

Add three keys to the `web_properties` entry:

```yaml
web_properties:
  - web_property_id: aardvark-ga4
    client_brand_id: aardvark
    source: ga4
    address: "100000001"
    route: export
    project: example-export-project
    dataset: analytics_100000001
```

- For GA4, `dataset` must be `analytics_<property ID>`, matching `address`.
- For Search Console, the dataset name is your choice.
- Keep `address`, the local ID and any `hostname`.
- An API property can switch to export with the same local ID.
- Once an export dataset is recorded, pointing at a different one needs a new local ID.
- Reports switch route after the first successful pull on the new route. With several
  properties, collect each one before rebuilding reports.

## Access

The person running the kit needs:

| Role | Where |
| --- | --- |
| BigQuery Data Viewer (`roles/bigquery.dataViewer`) | Each export dataset |
| BigQuery Job User (`roles/bigquery.jobUser`) | The kit's `project_id` |

Plus the usual write access to the kit's own datasets. An export-only property does not
use its API scopes or call its API.

Check access before the first run:

```sh
uv run brand-presence check-access --config config.yaml
```

It lists the export tables and checks required columns and types, by local property
ID. It reads no rows and runs no queries. It checks the newest GA4 daily table and all
three Search Console tables. It cannot prove Job User access or check every old daily
table.

## GA4 geo targets

GA4 export collection maps country, region and city names with Google's geo targets.
The kit does not ship that file. Pick a dated file from the
[geo targets page](https://developers.google.com/google-ads/api/data/geotargets), then:

```sh
uv run brand-presence setup-geo-targets --date YYYY-MM-DD --config config.yaml
```

- The download is checked and saved under `state_dir/<project_id>/<ops_dataset>/geo-targets/`,
  with its date, URL, row count and checksums.
- An invalid download leaves the previous copy in place.
- Collection never downloads anything. Run setup again when Google's names change.
- API-only and Search Console export setups do not need it.

## Cost

Each query runs in the export dataset's location and is billed to `project_id`. See
[BigQuery pricing](https://cloud.google.com/bigquery/pricing). `maximum_bytes_billed`
caps every export query:

```yaml
maximum_bytes_billed: 21474836480  # 20 GiB per query, the default
```

- A query over the cap fails before it runs.
- The cap is per query, not per run. History can run many queries.
- A crash before a result is saved can repeat a query and its scan. Saved pages resume
  without repeating.
- GA4 pull rows record billed bytes. Neither route reports a money cost.

Only aggregate results reach Python. They are saved locally, then written to your own
datasets.

## How each export is read

### GA4

- The first pull reads every available month, up to 120.
- Later weeks reread the current and previous month.
- The last daily table is awaited for three days. Later gaps are counted on pull rows.
- The top 10,000 page paths keep their own rows. The rest become `(other)`.
- Distinct users cannot be added across channels, places, pages or months. Use each
  place's `(all)` channel row.
- Older tables without the required nested fields can be refused.

### Search Console

- Reads complete weeks confirmed by both `ExportLog` namespaces.
- Discovers retained history, with at most 500 history requests per run.
- A week is read again when its highest epoch version rises.
- Totals include anonymized queries and show their counts. Named query rows exclude them.
- Position is the export's zero-based sum plus one, and stays null with no impressions.

## Reports

`init` creates empty export tables, so combined reports work for API-only setups too.

| `reports run --source` | Export reports |
| --- | --- |
| `search-console` | `core.fct_gsc_export_v1` |
| `ga4` | `core.fct_ga4_export_v1`, `core.fct_ga4_export_pages_v1`, `mart.engagement_monthly_v1`, `mart.page_engagement_monthly_v1` |
| `search-demand` | `mart.search_demand_monthly_v1`, `mart.growth_v1` |
| `paid` | `mart.paid_monthly_v1` |

The page report only shows paths listed in `pages`:

```yaml
pages:
  - page_path: /pricing
    page_group: Commercial
```

It is empty for API-only properties, because API landing-page sessions measure
something different from export page views. Export facts keep unlisted paths.

## Upgrading

Run `init` again after upgrading. The first run after the upgrade writes one new config
snapshot, because the snapshot now includes export settings. This is expected.

## Not yet checked live

No live export was read while building this route. Tests use invented rows and local
SQL. Check the first run against your own export.
