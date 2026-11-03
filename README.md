# Brand Presence

Collect search volume, AI answers, brand mentions and Google marketing data into your
own BigQuery project.

Brand Presence is a command-line kit for analysts and marketing teams. It pulls data
from seven sources, cleans it, and builds 32 versioned reports in BigQuery. You own the
project, the data and the credentials.

- **Your project.** Everything lands in datasets in your own Google Cloud project.
- **Pay once.** Raw responses are saved before anything else, so an interrupted run
  resumes without paying twice for the same request.
- **Plain setup.** Configuration is one YAML file. Credentials come only from
  environment variables.
- **Try first.** A built-in example runs every report on invented data, offline.

Use the tables in Looker Studio, or give Claude access through your own BigQuery
connection. The kit does not include a dashboard or a scheduler.

## Contents

- [Try it without credentials](#try-it-without-credentials)
- [Run it on your own project](#run-it-on-your-own-project)
- [Sources](#sources)
- [Reports](#reports)
- [How a run works](#how-a-run-works)
- [Limitations](#limitations)
- [Documentation](#documentation)

## Try it without credentials

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, on macOS
or Linux. Clone the repository, then run:

```sh
uv python install 3.12
uv sync --locked
uv run brand-presence example --output example-output
```

Open `example-output/report.html` in your browser. It shows three views: search volume
trend, AI answer brand share and mentions by week.

The example needs no network, Google Cloud project, credentials or vendor account. It
runs every report on invented data for two sample brands and writes one CSV per report.
It never overwrites existing files, so use a new or empty output directory.

The [example inputs and expected outputs](src/brand_presence/example/README.md) list the
fixed dates and known figures. Tests compare every report CSV exactly.

## Run it on your own project

### 1. Prepare Google Cloud

- Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
- Use a project with billing and the BigQuery API enabled.
- Give your user **BigQuery User** and **BigQuery Data Editor** on the project. See
  [BigQuery roles](https://docs.cloud.google.com/bigquery/docs/access-control).

```sh
gcloud auth application-default login
```

### 2. Write your config

```sh
cp example.yaml config.yaml
```

In `config.yaml`, set at least:

| Key | Value |
| --- | --- |
| `project_id` | Your Google Cloud project. |
| `client_id` | A stable name of your own, such as `aardvark-research`. `example` is rejected. |
| `location` | One BigQuery location for all datasets, such as `EU`. |
| `brands`, `countries`, `benchmark_sets` | Your brands, countries and keywords instead of the invented ones. |

Keep `client_id` unchanged once you start. It is part of every request key, so changing
it pays again for the current period. All keys are described in
[configuration](docs/configuration.md).

### 3. Create the datasets

```sh
uv run brand-presence init --config config.yaml
```

`init` is safe to repeat. It creates missing datasets and tables and never deletes data.

### 4. Collect and build reports

Start with one source. For DataForSEO:

```sh
export DATAFORSEO_LOGIN='example-login'
export DATAFORSEO_PASSWORD='example-password'
uv run brand-presence run search-volume --config config.yaml
uv run brand-presence reports run --config config.yaml
```

Collection and reports are separate commands. Neither schedules future runs. For other
sources, follow their guide in the table below.

## Sources

Every command starts with `uv run brand-presence`. Every live run also needs BigQuery
access through Application Default Credentials (ADC), and BigQuery bills storage and
queries separately.

| Source | Collect | Credentials | Cost | Guide |
| --- | --- | --- | --- | --- |
| DataForSEO | `run search-volume` | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | US$0.06 per task | [DataForSEO](docs/sources/dataforseo.md) |
| Cloro | `run ai-answers` | `CLORO_API_KEY` | Credits per task | [Cloro](docs/sources/cloro.md) |
| Syften | `run mentions` | `SYFTEN_API_KEY` | Subscription | [Syften](docs/sources/syften.md) |
| Peec | `run peec` | `PEEC_API_KEY` | Subscription, reads are free | [Peec](docs/sources/peec.md) |
| Search Console | `run search-console` | ADC | Free API, quotas apply | [Search Console](docs/sources/search-console.md) |
| GA4 | `run ga4` | ADC | Free API, quotas apply | [GA4](docs/sources/ga4.md) |
| Google Ads | `run google-ads` | `GOOGLE_ADS_*` variables | Free API, quotas apply | [Google Ads](docs/sources/google-ads.md) |

Search Console and GA4 read their APIs by default. If a property already exports to
BigQuery, you can read that export instead, per property. See
[API or BigQuery export](docs/bigquery-export.md).

"Supported" means implemented and tested offline with invented data. Each source still
needs a first check against your own account.

**Not included yet:** CRM sources, the Syften archive backfill, and reports that depend
on Ads Transparency, search results, Trends or Reddit labels. The full list is in
[reports](docs/reports.md#reports-not-included).

## Reports

Build a report group after collecting its source:

```sh
uv run brand-presence reports run --source ai-answers --config config.yaml
```

| `--source` | Builds |
| --- | --- |
| `search-volume` (default) | Monthly search volume facts and history |
| `ai-answers` | Cloro prompt results, monthly brand counts and citations |
| `mentions` | Syften mention facts |
| `peec` | Peec prompt results, monthly brand counts and citations |
| `ai-weekly` | Weekly brand mentions, domain citations and AI overview presence, from Cloro and Peec |
| `search-console` | Search Console monthly totals and query facts |
| `ga4` | GA4 monthly facts, engagement and page reports |
| `google-ads` | Delivery, keywords, search terms, auction, history and modifiers |
| `search-demand` | Search demand and growth, from search volume and Search Console |
| `paid` | Monthly paid comparisons, adding Google Ads |

Each group builds its prerequisites and checks in one transaction. A failed build rolls
back. Table names and caveats are in [reports](docs/reports.md).

## How a run works

1. **Plan.** The run reads your config and works out which requests the period needs.
2. **Save raw.** Each vendor response is saved to local disk and BigQuery before parsing.
3. **Write clean rows.** Rows are replaced by request key in one transaction, never merged.
4. **Recover.** A request interrupted mid-send is looked up before it is paid for again.

Raw files live under `.brand-presence/` next to your config. They contain your research
terms and vendor responses, so treat the folder as private data and back it up. Use one
machine and one state folder per project. Details are in [recovery](docs/recovery.md).

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Nothing completed, or an error |
| `2` | Invalid command usage |
| `3` | Partial completion. Run the same command again later. |

## Limitations

- No scheduler and no dashboard.
- Search volume is national and monthly.
- Weekly API and Peec windows are not refreshed after they succeed, so late vendor
  changes are not picked up.
- An empty report does not prove a brand had no activity.
- Sentiment and recommendation labels are not inferred.
- Distinct users and ratios cannot be added across report rows.
- Tests run report SQL locally. They do not replace a first live BigQuery check.

## Documentation

| Page | What it covers |
| --- | --- |
| [Configuration](docs/configuration.md) | Every YAML key and its rules |
| [Sources](docs/sources/) | Setup, cost and behaviour per source |
| [API or BigQuery export](docs/bigquery-export.md) | Reading GA4 and Search Console exports |
| [Reports](docs/reports.md) | Report tables, caveats and what is left out |
| [Recovery](docs/recovery.md) | Local state, locks and paid-request recovery |
| [Decisions](docs/adr/) | Why the kit works the way it does |

## Contributing and support

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and tests. Report bugs and
request features in GitHub Issues. Include the command, package version and a redacted
error. Never attach credentials, account IDs, raw responses or private configuration.
For vulnerabilities, follow [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
