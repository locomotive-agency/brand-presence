# Brand Presence

Collect monthly keyword search volume from DataForSEO into your own BigQuery project.
Build a history table with the latest figure for each brand, country, keyword and month.

## Setup

Use Python 3.12, uv, and macOS or Linux. Install the Google Cloud CLI separately.
Your Google Cloud project needs billing and the BigQuery API enabled.
Your signed-in user needs permission to create datasets, run query jobs, and read and
write the tables. For an initial project, BigQuery User plus BigQuery Data Editor at
project scope provide these capabilities. Dataset policies can further restrict access.
See [BigQuery roles](https://docs.cloud.google.com/bigquery/docs/access-control).

```sh
uv sync --locked
cp example.yaml config.yaml
gcloud auth application-default login
```

Edit `config.yaml`. Set `project_id` to your project and `client_id` to your own stable
identifier, such as `aardvark-research`. `client_id` is required; `example` is rejected.
It must start with a lowercase letter and contain only lowercase letters, digits,
underscores or hyphens, up to 64 characters. Request keys are
`client_id/source/period/hash`. Changing `client_id` changes every request key and
re-pays the current period, so keep it unchanged when resuming work.

Choose one BigQuery location for all five datasets. Replace the invented brands and
keywords with your own research. Use DataForSEO's country names and language codes.
Regional forms such as `zh-TW` are accepted structurally and passed through unchanged;
the vendor decides whether a language is available for the endpoint and location.
A keyword is `brand`, `competitor` or `generic`. Brand and competitor keywords need a
listed `brand_id`. Each keyword has a `search_volume` flag, defaulting to `true`.
Set it to `false` to exclude that keyword from collection. At least one keyword must
enable it.

Set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` in your shell environment. Use your
DataForSEO API login and API password. Do not put them in the YAML file.

```sh
export DATAFORSEO_LOGIN='example-login'
export DATAFORSEO_PASSWORD='example-password'
uv run brand-presence init --config config.yaml
uv run brand-presence run search-volume --config config.yaml
uv run brand-presence reports run --config config.yaml
```

`init` can be repeated. It creates missing datasets and tables without deleting data.
It does not grant permissions, enable APIs, or migrate an incompatible existing schema.
Dataset names are configurable under `datasets` in the YAML file.

The default period is the last complete month in `time_zone`. Each run asks for the
three months ending with that period. It also asks once for up to 48 months of history
for each new keyword and country. The vendor decides which months are available.
Use `--period YYYY-MM` to resume a specific ended month. Keep the original period when
resuming after a month boundary. Very old periods can exceed the vendor's history range.

## Cost and waiting

DataForSEO lists Standard queue tasks at **US$0.06 per task**, with up to 1,000 keywords
per task. This kit uses that mode. A single HTTP POST can contain several billed tasks.
The two-keyword, one-country example sends two tasks on its first run, about **US$0.12**.
A later month normally needs one task, about **US$0.06**. Shared keywords are grouped
by country and language. Additional countries, keyword batches, configuration changes,
and retries of rejected or unusable tasks can add cost. Check the
[current vendor price](https://dataforseo.com/pricing/keywords-data/google-ads).
Prices were checked on 2 November 2026. BigQuery storage and queries have separate costs.

The vendor lists a typical queue wait of one to three hours. The command polls for up
to 40 minutes. If tasks remain pending, run the same command again later. Known task
IDs are collected without another paid submission.

Exit codes are `0` for success, `3` for partial completion, `1` when nothing completed
or an error occurred, and `2` for invalid command usage. Read the request counts in
the output. A pending task is not a successful measurement.

## Reports

With the default dataset names, `reports run` builds:

| Table | Contents |
| --- | --- |
| `bp_core.fct_search_volume_v1` | A revision when the latest clean figure or its labels change. Each revision has its build time in `pulled_at`. |
| `bp_mart.search_volume_history_v1` | The latest revision per month, client brand, country, location and keyword, across all retained months. |

These are the source pipeline's SQL and report definitions. The fact chooses the
latest collection time, then the later period for a tie. A null remains null.
It is not converted to zero. Unchanged rebuilds do not add fact revisions.
The history table is replaced as a whole. A failed build rolls both tables back.

The clean table `bp_stg.search_volume` keeps each collection period separately.
Do not sum its overlapping pulls. Use the history report for analysis.
Search volume is a vendor estimate. These reports do not calculate share of search,
revenue, organic clicks, or advertising spend. Bid and CPC fields remain in clean data
but are excluded from the reports.

## Recovery and local state

Raw responses are saved under `state_dir/<project_id>/<ops_dataset>/raw/` before any
clean rows are written. The default state directory is `.brand-presence`, relative
to the config file. Back it up and keep it with the configuration.
It contains research terms and vendor responses, so treat it as private data.
Raw rows are also stored in BigQuery. Configuration snapshots in BigQuery preserve
the settings needed to parse an older task.

The BigQuery run record is also a lock. A process lock protects local state. After
an abrupt process death, the next run closes the abandoned record once it is one
hour old. Until then, another run is refused. Use one host and one state directory
for a project and dataset set. Do not delete ledger rows or state to force a rerun.

A task left `sending` becomes `unknown` and is looked up by its tag. If the ready
list does not contain it, it stays unknown. A timeout or server error on a paid POST
is never blindly retried. Unknown and expired paid tasks are not submitted again in
their original period.

Unresolved history from an earlier period reserves its keyword and location pairs
until an `unknown` or `expired` attempt is older than DataForSEO's 30-day result
retention. Age starts at submission, or creation if submission was never recorded.
After that, a later-period run can request the history again. This also releases a
request left in the ledger by a crash before the POST. **This can pay once more for
a request that the vendor may have run.** It does not resolve the old attempt's
outcome. Each run's planning output lists the reserved pair count and up to five
pairs, including zero when none remain. Stored answers awaiting clean writes keep
their reservations regardless of age.

Recover promptly and investigate unresolved tasks with the vendor. Missing local raw
files stop clean recovery rather than paying again. Requests never sent because the
run's deadline passed, or because the vendor took nothing, count as pending rather
than failed.

Clean writes delete by request key and by natural key within the period, then insert
in one transaction. If a process stops after the commit, repeating the write is safe.
Malformed rows stay raw and are reported as parse errors.

## Limits and development

This first workflow supports national monthly search volume only. It has no scheduler,
dashboard, other sources, cloud raw storage, or telemetry exporters. Country names and
languages are structurally validated offline; vendor acceptance is checked on the task.
Production APIs have not been called for this port. Tests use invented fixtures and
the source BigQuery fake. Report SQL is also executed locally through SQLGlot and SQLite.
Those tests do not replace a real BigQuery acceptance run.

```sh
uv run ruff check .
uv run pytest
```

Register future task sources in `src/brand_presence/sources/registry.py`.
Keep recovery, raw storage and clean writes in the shared runner.
The clean contract is in `src/brand_presence/contracts/clean/`.
No license has been selected yet.
