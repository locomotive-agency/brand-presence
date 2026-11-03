# GA4

| | |
| --- | --- |
| Command | `run ga4` |
| Credentials | ADC with `analytics.readonly`, plus GA4 Viewer on the property |
| Cost | Free API. Token and concurrency quotas apply. |
| Period | Last ended ISO week, `--period YYYY-Www` |
| Reports | `reports run --source ga4` |

This page covers the API route, the default. To read a GA4 BigQuery export instead, see
[API or BigQuery export](../bigquery-export.md).

## Setup

```yaml
web_properties:
  - web_property_id: aardvark-ga4
    client_brand_id: aardvark
    source: ga4
    address: "100000001"
```

| Field | Rules |
| --- | --- |
| `address` | The numeric property ID, quoted. Find it in GA4 Admin, Property settings, Property details. It is not the `G-` measurement ID. |
| `hostname` | Optional. Filters a property shared by several sites. |

1. Enable the Google Analytics Data API in your Google Cloud project.
2. Give your account the **GA4 Viewer** role on the property.
3. Sign in as described in [Google sign-in](../google-auth.md).

```sh
uv run brand-presence init --config config.yaml
uv run brand-presence run ga4 --config config.yaml
uv run brand-presence reports run --source ga4 --config config.yaml
```

## What is read

- Fixed aggregate dimensions and metrics only.
- Landing pages without query strings.
- Never user IDs, event names, campaign names or custom dimensions.
- Days stay in the property's time zone.

Sampling, thresholding, data cuts, folded rows and quota tokens are recorded on each
pull summary. Sampled or limited answers are kept and flagged. They are not complete
measurements.

## Cities

Add `locations` with `kind: city` and `ga4` in `sources` to collect city rows:

```yaml
locations:
  - location_id: example-city
    country_code: NL
    kind: city
    name: Example city
    canonical_name: Example city,North Holland,Netherlands
    sources: [ga4]
```

`canonical_name` is `City,Region,Country` or `City,Country`. Unlisted or ambiguous
cities fold into `other`, with null user counts because users cannot be added across
cities.

## Periods and history

- The default is the last ended ISO week.
- A run without `--period` also reads up to 104 earlier weeks, with at most 500 history
  requests per run. History depends on what the property still keeps.
- Each report reads at most ten pages of 100,000 rows.
- Successful weeks are not refreshed, so late changes are not picked up.

## Cost and quotas

The Data API has no per-request charge. Its
[core quotas](https://developers.google.com/analytics/devguides/reporting/data/v1/quotas)
limit tokens per property and project, concurrent requests and server errors. Complex
reports use more tokens.

## Tables and reports

GA4 writes `stg.ga4_engagement`, `stg.ga4_landing_pages`, `stg.ga4_pulls` and, with
cities configured, `stg.ga4_engagement_cities`.

The report group builds `core.fct_ga4_v1`, plus the export facts,
`mart.engagement_monthly_v1` and `mart.page_engagement_monthly_v1`. Export tables may be
empty.

- Country and city figures stay separate.
- User counts are left out, because daily users cannot be added into monthly users.
- The page report only covers export properties. See [export](../bigquery-export.md#reports).

## Recovery

Same as [Search Console](search-console.md#recovery).
