# Search Console

| | |
| --- | --- |
| Command | `run search-console` |
| Credentials | ADC with `webmasters.readonly`, plus access to the property |
| Cost | Free API. Load and request quotas apply. |
| Period | Last ended ISO week, `--period YYYY-Www` |
| Reports | `reports run --source search-console` |

This page covers the API route, the default. To read a Search Console bulk export in
BigQuery instead, see [API or BigQuery export](../bigquery-export.md).

## Setup

```yaml
web_properties:
  - web_property_id: aardvark-site
    client_brand_id: aardvark
    source: search-console
    address: sc-domain:aardvark.example
```

| Field | Rules |
| --- | --- |
| `web_property_id` | Your stable local ID. |
| `client_brand_id` | Must appear in `benchmark_sets`. |
| `address` | The exact Search Console property, such as `sc-domain:aardvark.example` or `https://aardvark.example/`. |
| `hostname` | Optional. Reads only that host. Entries sharing an address need different hostnames. |

A local ID cannot change its source, address, hostname or client brand. Use a new ID.

1. Enable the Search Console API in your Google Cloud project.
2. Give your Google account access to the Search Console property.
3. Sign in as described in [Google sign-in](../google-auth.md).

```sh
uv run brand-presence init --config config.yaml
uv run brand-presence run search-console --config config.yaml
uv run brand-presence reports run --source search-console --config config.yaml
```

## Periods and history

- The default is the last ended ISO week. Days use Pacific time.
- A run without `--period` also reads up to 65 earlier weeks, newest first, with at most
  500 history requests per run.
- Before reading, the run asks Google for its first incomplete date. Unfinished weeks
  wait. If Google cannot name that date, the run stops.
- Each week is saved in its own BigQuery transaction, which takes a few minutes per
  week. A full first history can take several hours. To check your setup quickly, run
  one week first with `--period`, then let the history run without it.

## Cost and quotas

The [Search Console API is free](https://developers.google.com/webmaster-tools/pricing).
Its [quotas](https://developers.google.com/webmaster-tools/limits) limit load and
requests per site, user and project. Long ranges and query or page breakdowns use more
load. The kit reads up to twenty pages of 25,000 rows per query or page part.

## Tables and reports

Each site writes totals, queries, pages, devices and a pull summary to
`stg.search_console_*`. All countries are kept.

The report group builds `core.fct_gsc_v1`. It also builds `core.fct_gsc_export_v1`,
which stays empty without export data.

- `fct_gsc_v1` keeps monthly totals and named queries as separate row kinds. **Never sum
  the two kinds.**
- Position is weighted by impressions.
- Google hides some rare queries. Query and page coverage shares show how much of the
  totals they explain. A false row-limit flag does not prove completeness.
- Query brand labels use the names and aliases from the config at collection time.

`search-demand` combines Search Console with search volume. See [reports](../reports.md).

## Recovery

A read interrupted mid-send becomes `unknown`. The next run looks for its saved pages
before reading again, and resumes under the original request. If the process died
before a response reached disk, the read repeats and uses quota. A saved page is never
requested again.
