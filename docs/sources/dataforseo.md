# DataForSEO search volume

| | |
| --- | --- |
| Command | `run search-volume` |
| Credentials | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` |
| Cost | US$0.06 per Standard queue task |
| Period | Last complete month, `--period YYYY-MM` |
| Reports | `reports run --source search-volume` |

## Setup

Add keywords to your `benchmark_sets`. See [configuration](../configuration.md#brands-countries-and-keywords).
Use your DataForSEO API login and API password, not your account password.

```sh
export DATAFORSEO_LOGIN='example-login'
export DATAFORSEO_PASSWORD='example-password'
uv run brand-presence init --config config.yaml
uv run brand-presence run search-volume --config config.yaml
uv run brand-presence reports run --config config.yaml
```

## Cost

DataForSEO lists Standard queue tasks at **US$0.06 per task**, with up to 1,000 keywords
per task. One HTTP request can hold several billed tasks.

| Run | Tasks | Approximate cost |
| --- | --- | --- |
| First run of `example.yaml` (2 keywords, 1 country) | 2 | US$0.12 |
| A later month | 1 | US$0.06 |

Keywords are grouped by country and language. More countries, more keyword batches,
config changes and retries of rejected tasks add cost. Prices were checked on
3 November 2026. Check the [current price](https://dataforseo.com/pricing/keywords-data/google-ads).

## What a run asks for

- The three months ending with the period.
- Once per new keyword and country, up to 48 months of history. The vendor decides which
  months are available.

Keep the original `--period` when resuming after a month boundary. Very old periods can
fall outside the vendor's history range.

## Waiting

The vendor lists a typical queue wait of one to three hours. The command polls for up
to 40 minutes. If tasks are still pending, it exits with code `3`. Run the same command
later. Known task IDs are collected without paying again.

A pending task is not a successful measurement. Read the request counts in the output.

## Reports

| Table | Contents |
| --- | --- |
| `bp_core.fct_search_volume_v1` | A new revision when the latest figure or its labels change, with its build time in `pulled_at`. |
| `bp_mart.search_volume_history_v1` | The latest revision per month, brand, country, location and keyword. |

- The fact picks the latest collection time, then the later period on a tie.
- Nulls stay null. They are not turned into zero.
- Unchanged rebuilds add no revisions.

The clean table `bp_stg.search_volume` keeps each collection period separately. Its
pulls overlap, so do not sum it. Use the history report.

Search volume is a vendor estimate. These reports do not calculate share of search,
revenue, organic clicks or ad spend. Bid and CPC fields stay in the clean table but are
left out of the reports. Use `search-demand` and `paid` for cross-source comparisons.

## Recovery

See [recovery](../recovery.md#dataforseo) for how unknown and expired tasks are handled.
