# Brand Presence

Collect search volume, AI answers, brand mentions and Google marketing data into your
own BigQuery project. The kit provides clean tables and 32 versioned reports for
analysts and marketing teams who manage their own data.

Use the BigQuery tables in Looker Studio or give Claude access through your chosen
BigQuery connection. You manage that connection and its permissions. The kit collects
and prepares data; it does not include a dashboard service or a Claude integration.
Configuration is YAML. Raw responses stay on your disk and in your BigQuery project.

## Try it without credentials

From the repository directory, after the one-time install below:

```sh
uv run brand-presence example --output example-output
```

On a clean macOS or Linux machine, install [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Git. Clone this public repository, or download and unpack its ZIP from GitHub.
Open a terminal in the unpacked directory, then run:

```sh
uv python install 3.12
uv sync --locked
uv run brand-presence example --output example-output
```

Installation downloads Python and packages. Once installed, the example itself runs
without network access, Google Cloud, credentials, configuration or vendor accounts.
Open `example-output/report.html` in your browser. It reads the generated CSVs at build
time and contains three views: search volume trend, AI answer brand share and mentions
by week. It has no scripts, external assets or browser server requirement.

The example runs every ported report against bundled invented observations for
Aardvark Sample and Capybara Sample. It prints each report's name, row count and CSV
path. Choose a new or empty output directory; existing files are never overwritten.
This is a preview of the BigQuery data before paying for collection.

[Example inputs and expected outputs](src/brand_presence/example/README.md) describe
the fixed dates, known figures and CSV rounding. Tests compare every report CSV exactly.
The offline SQL support is shared with the tests. It covers these reports, not arbitrary
BigQuery SQL, and does not replace a live BigQuery acceptance check.

## Run it on your own project

After trying the example, install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
Your Google Cloud project needs billing and the BigQuery API enabled.
Your signed-in user needs permission to create datasets, run query jobs, and read and
write the tables. For an initial project, BigQuery User plus BigQuery Data Editor at
project scope provide these capabilities. Dataset policies can further restrict access.
See [BigQuery roles](https://docs.cloud.google.com/bigquery/docs/access-control).

```sh
cp example.yaml config.yaml
gcloud auth application-default login
```

Edit `config.yaml`. Set `project_id` to your project and `client_id` to your own stable
identifier, such as `aardvark-research`. `client_id` is required; `example` is rejected.
It must start with a lowercase letter and contain only lowercase letters, digits,
underscores or hyphens, up to 64 characters. Request keys are
`client_id/source/period/hash`. Changing `client_id` changes every request key and
re-pays the current period, so keep it unchanged when resuming work.

Choose one BigQuery location for all six datasets. Replace the invented brands and
keywords with your own research. Use DataForSEO's country names and language codes.
Regional forms such as `zh-TW` are accepted structurally and passed through unchanged;
the vendor decides whether a language is available for the endpoint and location.
A keyword is `brand`, `competitor` or `generic`. Brand and competitor keywords need a
listed `brand_id`. Each keyword has a `search_volume` flag, defaulting to `true`.
Set it to `false` to exclude that keyword from collection. The search-volume command
needs at least one enabled keyword. Other sources can use benchmark sets without keywords.

Start with one source. For DataForSEO, set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD`
in your shell environment. Use your DataForSEO API login and API password. Do not put them in the YAML file.

```sh
export DATAFORSEO_LOGIN='example-login'
export DATAFORSEO_PASSWORD='example-password'
uv run brand-presence init --config config.yaml
uv run brand-presence run search-volume --config config.yaml
uv run brand-presence reports run --config config.yaml
```

`init` can be repeated. It creates missing datasets and tables without deleting data.
It also adds supported upgrade columns to run, property and GA4 pull tables, and sets
mention text expiry. It does not grant permissions, enable APIs, or repair other
incompatible existing schemas.
Dataset names are configurable under `datasets` in the YAML file.

For search volume, the default period is the last complete month in `time_zone`.
Each run asks for the three months ending with that period. It also asks once for up to 48 months of history
for each new keyword and country. The vendor decides which months are available.
Use `--period YYYY-MM` to resume a specific ended month. Keep the original period when
resuming after a month boundary. Very old periods can exceed the vendor's history range.

For another source, use its `run` command from the table below after configuring it.
Then build its report group with `reports run --source SOURCE --config config.yaml`.
Collection and report builds are separate commands. Neither command schedules future runs.
API sources need the extra scopes and account access described in their sections.

## Sources

Commands below follow `uv run brand-presence`. All real source runs also need
BigQuery Application Default Credentials (ADC) and can incur BigQuery storage and
query costs. The detailed sections explain configuration, access and recovery.
Supported means implemented and tested offline. It does not mean verified against
every live account. `reports run` defaults to `search-volume`.

| Source | Collection command | Credentials | Cost or quota | Reports (`reports run --source`) | Status |
| --- | --- | --- | --- | --- | --- |
| DataForSEO | `run search-volume` | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | Paid Standard queue tasks; see costs below | `search-volume`: monthly volume facts and history | Supported |
| Cloro | `run ai-answers` | `CLORO_API_KEY` | Paid credits per task; account limits | `ai-answers`: prompt results, monthly brand counts and citations | Supported |
| Syften | `run mentions` | `SYFTEN_API_KEY` | Subscription and account limits | `mentions`: mention facts | Supported |
| Peec | `run peec` | `PEEC_API_KEY` | Tracking subscription; GET reads create no paid tasks | `peec`: stored prompt results, monthly counts and citations | Supported |
| Search Console API | `run search-console` | ADC with `webmasters.readonly` and site access | Free API; load and request quotas | `search-console`: monthly totals and query facts | Supported |
| Search Console export | `run search-console` with `route: export` | ADC with export dataset read access | BigQuery scans, capped per query | `search-console`: export facts; `search-demand`: search demand and growth | Supported |
| GA4 API | `run ga4` | ADC with `analytics.readonly` and property access | Token and concurrency quotas | `ga4`: monthly facts and engagement | Supported |
| GA4 export | `run ga4` with `route: export` | ADC with export dataset read access | BigQuery scans, capped per query | `ga4`: export facts, engagement and configured page reports | Supported |
| Google Ads | `run google-ads` | `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`; optional manager ID and legacy developer token | Free API; project operation quotas | `google-ads`: delivery, keywords, terms, auction, history and modifiers; `paid`: monthly comparisons | Supported |
| Cloro + Peec | Collect either source above | Their credentials | Report queries only | `ai-weekly`: weekly brand mentions, domain citations and AI overview presence | Supported |
| CRM | None | Not applicable | Not applicable | CRM facts, pipeline and correlation | Deferred |
| Syften archive backfill | None | Not applicable | Not applicable | Historical archive collection | Deferred |
| Reddit reports | None | External mention labels missing | Not applicable | `reddit_mentions_v1`, `reddit_mentions_v2` | Deferred |
| Ads Transparency, search results and Trends | None | Not applicable | Not applicable | Reports listed below, including freshness | Deferred |

`search-demand` combines search volume and Search Console. `paid` adds Google Ads.
These groups include prerequisite report builds. The API and export routes use the
same collection command, selected separately for each configured property.

Reports left out of this kit are listed below. Their statements were not split or
rewritten to hide missing dependencies.

| Reports | Missing dependency |
| --- | --- |
| `fct_crm_leads_v1`, `fct_crm_deals_v1`, `pipeline_monthly_v1` | CRM |
| `correlation_v1` | CRM pipeline reports |
| `competitor_ads_history_v1` | Ads Transparency |
| `fct_serp_v1`, `serp_history_v1`, `serp_presence_monthly_v1` | Search results and paid search-result ads |
| `competitor_ads_v1` | Search-result facts and ad labels |
| `fct_trends_v1`, `search_trends_history_v1` | Trends |
| `freshness_v2` | CRM, Trends, search results and source-freshness data |
| `reddit_mentions_v1`, `reddit_mentions_v2` | External mention labels, as before this port |

## DataForSEO costs and waiting

DataForSEO lists Standard queue tasks at **US$0.06 per task**, with up to 1,000 keywords
per task. This kit uses that mode. A single HTTP POST can contain several billed tasks.
A live `run search-volume` with the two keywords and one country in `example.yaml`
sends two tasks on its first run, about **US$0.12**. The offline `example` command is free.
A later month normally needs one task, about **US$0.06**. Shared keywords are grouped
by country and language. Additional countries, keyword batches, configuration changes,
and retries of rejected or unusable tasks can add cost. Check the
[current vendor price](https://dataforseo.com/pricing/keywords-data/google-ads).
Prices were checked on 3 November 2026. BigQuery storage and queries have separate costs.

The vendor lists a typical queue wait of one to three hours. The command polls for up
to 40 minutes. If tasks remain pending, run the same command again later. Known task
IDs are collected without another paid submission.

Exit codes are `0` for success, `3` for partial completion, `1` when nothing completed
or an error occurred, and `2` for invalid command usage. Read the request counts in
the output. A pending task is not a successful measurement.

## Search volume reports

With the default dataset names, `reports run --source search-volume` builds:

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
Search volume is a vendor estimate. These two reports do not calculate share of search,
revenue, organic clicks, or advertising spend. Use `search-demand` and `paid` for
cross-source comparisons once their inputs have been collected. Bid and CPC fields remain in clean data
but are excluded from the reports.

## Cloro AI answers

Add `prompts` and `competitors` to each benchmark set in YAML. Each prompt needs
`prompt_id`, `prompt`, and `engines`. Engines are `chatgpt`, `perplexity`, `gemini`,
and `ai-overview`. Empty `location_ids` selects the set's country. Regional prompts
name a configured `locations` entry with its country, name, canonical name and ISO
region code. Brands may list `aliases` and lowercase bare `domains`, without a scheme or path.
Prompt keyword, keyword_group
and intent must be supplied together, or omitted together.

```sh
export CLORO_API_KEY='example-key'
uv run brand-presence init --config config.yaml
uv run brand-presence run ai-answers --config config.yaml
uv run brand-presence reports run --source ai-answers --config config.yaml
```

Cloro measures the current ISO week. `--period YYYY-Www` resumes a week. An older
week only collects known tasks and finishes saved answers. It never creates new
paid tasks. Each distinct prompt, engine and location creates one task. Identical
requests shared by sets are sent once. Cloro uses credits. The source documentation
does not give a monetary price per request. Check your account's credit price and
cap before running. The kit does not read charged credits from responses.

A timeout is retried only under the same Cloro idempotency key. A recovered `sending`
row becomes `unknown`. In the current week it is looked up by submitting that same
key. If Cloro reports an existing key without a task ID, it stays unknown. No new
paid key is created for that unknown outcome.
If that lookup is refused or the engine is unavailable, the class is saved on the
unknown row and later runs stop resending it. The row and original key remain for
recovery. Investigate the outcome before clearing its error class to permit another
lookup in the current week. Ended weeks keep unknown rows without another POST.

Cloro writes `stg.ai_answers` and `stg.ai_answer_brands`. The five reports are
`core.fct_ai_answers_v1`, `core.fct_ai_cited_links_v1`, `mart.ai_prompt_results_v2`,
`mart.ai_keyword_monthly_v2`, and `mart.ai_citations_v1`. The report definitions and
SQL retain the source's names and logic. Each collection run applies YAML reference rows. `init` creates the current views
they need. Facts append changed revisions. Serving reports replace
whole tables within the report transaction. Empty citation results are valid.

Brand names and aliases match whole words without case sensitivity. Mention rank
is deterministic text position. It is not a recommendation or sentiment score.
Those labels remain null. A results page with no AI overview has `none shown`,
with unknown brand marks. Monthly counts exclude it from mention rates. Country
and regional responses are independent samples. Monthly reports start in 2024.

## Syften mentions

Create filters in your own Syften account. Give each filter a `$tag:example-tag`
and map that tag in YAML `mention_filters`. Each mapping holds `mention_class`
(`brand`, `competitor`, `category` or `other`), optional client and subject brand IDs,
country and keyword group. Brand and competitor classes need both brand IDs.
An omitted country or client brand is stored as `unknown`. Unlisted tags are
counted and discarded before raw storage. Every author field is removed.

```sh
export SYFTEN_API_KEY='example-token'
uv run brand-presence init --config config.yaml
uv run brand-presence run mentions --config config.yaml
uv run brand-presence reports run --source mentions --config config.yaml
```

`SYFTEN_API_KEY` holds Syften's API bearer token. The endpoint uses POST to read
matches, up to 100 per page. It does not create paid tasks. The source docs give
no per-request monetary price. Your subscription and account limits still apply.
The archive backfill is excluded.

The source retains `raw.mentions`, `stg.mention_facts`, `stg.mention_text`, and
`ops.mentions_cursor`. `ops.mentions_tasks` adds a durable local page ledger.
Saved pages recover before another pull. A match ID is written once across runs.
If a saved page cannot finish, for example because its raw file is missing or parsing
fails, its ledger row becomes `failed` and increments `requests_failed` on the run
line. The pull restarts from the unmoved cursor or saved resume point to cover it again.
The cursor advances after clean writes. A partial pull retains its oldest and
newest timestamps so the next run can continue. Rate-limit waits stop at the
run deadline. Mentions use the current local date as their collection period and
reject `--period`; their saved cursor determines what to read.

`mention_text_days` is 1 to 366, default 366. `init` sets partition expiry for
raw mentions and clean text. Each mentions run deletes expired local raw files.
Expiry batches ledger updates. Unreadable files are kept and skipped, counted as
`raw file unreadable` in the run's error fields, so other pages can recover.
Local expiry needs a run; files do not disappear while the CLI is idle. Backups
need the same retention policy. Countable facts remain after text expires.

`core.fct_mentions_v1` retains the latest source labels and the vendor acceptance
flag. It contains no post text. `reddit_mentions_v1` and `reddit_mentions_v2` are
excluded because they require the unported `labels.mention_labels` source.
YAML `subreddits` still writes `ref.subreddits` and its current view. Each entry
has a subreddit name without `r/`, community group, and optional subscriber count
with its observation date. No labels or sentiment are inferred.

## Peec stored answers

Peec owns the prompts. Create and manage them in your Peec account. The kit only
reads them. YAML `peec_projects` maps a stable `mapping` name to `project_id`,
`client_brand_id`, `country_code`, `engines`, and `first_date`. Each project and each
benchmark set can appear once. The country needs a matching benchmark set.
`peec_brands` maps every brand in that set, including competitors, to a
`vendor_brand_id`. Vendor brand IDs must be distinct within the mapping.
A mapping cannot later point to another project, brand or country.

```sh
export PEEC_API_KEY='example-key'
uv run brand-presence init --config config.yaml
uv run brand-presence run peec --config config.yaml
uv run brand-presence reports run --source peec --config config.yaml
```

The key is sent as `x-api-key`. All Peec calls are GET reads. The source docs state
that these reads are not paid. Your Peec tracking subscription and BigQuery costs
still apply. The kit never creates prompts or starts paid tracking.

The default period is the last complete ISO week in `time_zone`. History covers
at most 52 earlier weeks and 200 historical prompt-engine requests per run, newest
first, bounded by `first_date`. Reruns continue missing eligible weeks. Use
`--period YYYY-Www` to collect or recover one ended week without history.
Successful windows are not fetched again. There is no forced refresh command.
Late vendor changes to an already collected week are therefore not picked up.

Prompt discovery includes archived prompts. Raw discovery is saved before prompt
versions. Only active prompts receive new chat requests. Each chat listing page
and detail is saved before the next call. The task becomes collected only after
all pages pass completeness checks. Missing pages, changing totals, duplicate
chats and mismatched prompts or channels refuse the whole window. A complete
empty window writes a pull marker. Failed reads preserve earlier clean answers.
An interrupted GET may be repeated. It creates no paid task. Completed saved
windows recover with their original config and captured prompt version.

Peec writes `raw.peec`, `ops.peec_tasks`, `stg.peec_prompts`, `stg.peec_pulls`,
`stg.peec_answers`, and `stg.peec_answer_brands`. The five reports are
`core.fct_peec_answers_v1`, `core.fct_peec_cited_links_v1`,
`mart.peec_prompt_results_v1`, `mart.peec_keyword_monthly_v1`, and
`mart.peec_citations_v1`. Each included source check runs in the report transaction.

The prompt report uses the captured prompt text and topic. Archive state comes
from the latest observed prompt version. Keyword, keyword group and intent remain
null. Monthly totals use `(none)` for those labels. Prompt creation dates are
stored at midnight UTC as a storage convention. Chat dates remain vendor dates.
Vendor mention position is separate from the deterministic text mention rank.
The verified empty AI Overview shape becomes `none shown`. Empty text on other
surfaces is omitted only for the exact documented shape. Unknown states fail.

Account access, retention, observation time zone, and late weekly updates need
confirmation against your account. This port has not verified
those assumptions against a live account. Missing or inactive engine channels
stop collection. No substitute channel is chosen.

## Weekly AI reports

After collecting Cloro or Peec, run:

```sh
uv run brand-presence reports run --source ai-weekly --config config.yaml
```

This builds both sources' five reports, then `mart.ai_brand_mentions_weekly_v1`,
`mart.ai_domain_citations_weekly_v1`, and `mart.ai_overview_presence_weekly_v1`.
All 13 builds share one transaction. Empty source tables are allowed. Source and
engine stay separate. Countries and locations are combined within the project.
Only observed weeks appear. Keyword groups without a label are `unknown`.
No-overview observations are counted separately from answered prompts.

`freshness_v2` is excluded. It requires unported Trends, search results,
CRM and source-freshness tables. The two Reddit reports and the Syften archive backfill
are also excluded, as described above.

## Search Console API

Add a `web_properties` entry with `source: search-console`, a stable `web_property_id`,
`client_brand_id`, and `address`. The address must match the Search Console property,
such as `sc-domain:aardvark.example` or `https://aardvark.example/`.
An optional `hostname` reads only that host. Entries sharing an address need distinct
hostname filters. The client brand must appear in `benchmark_sets`.
A local property ID cannot change source, address, hostname or client brand across loads.
Use a new local ID when one of those bindings changes.

Enable the Search Console API in your own project. Give your Google account access to
that Search Console property. Create an OAuth desktop client in your own Google Cloud
project and download its client JSON outside this repository. Configure its consent
screen and add your account as a test user if the app is in testing. Sign in with
Application Default Credentials (ADC):

```sh
gcloud auth application-default login --client-id-file=/path/to/example-oauth-client.json --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/webmasters.readonly
gcloud auth application-default set-quota-project example-project
uv run brand-presence init --config config.yaml
uv run brand-presence run search-console --config config.yaml --period 2026-W09
uv run brand-presence reports run --source search-console --config config.yaml
```

The cloud-platform scope lets the same ADC credentials write to BigQuery. The
webmasters.readonly scope reads Search Console. Google documents the custom OAuth client
requirement for [ADC scopes outside Google Cloud](https://docs.cloud.google.com/docs/authentication/application-default-credentials).
Alternatively, set `GOOGLE_APPLICATION_CREDENTIALS` to a service account JSON file outside
this repository. Give that account access to the property and to your BigQuery project.
No credential belongs in YAML.

The default period is the last ended ISO week. A run without `--period` also reads up to
65 earlier weeks, newest first, with a cap of 500 history requests per run.
A finished-week check asks Google for its first incomplete date. Unfinished weeks wait.
A check that cannot name that date stops the run. Days use Pacific time.

Each site writes totals, queries, pages, devices and a pull summary to
`stg.search_console_*`. All countries are retained. Country reference rows are added
from the shared country table. Query labels use the names and aliases in the original
configuration snapshot. Rare queries may be hidden. Query and page coverage shares
show how much of the totals they explain. A false row-limit flag does not prove completeness.
The report group builds `core.fct_gsc_v1`. It keeps monthly totals and named queries as
separate row kinds. Never sum those two kinds. Position is weighted by impressions.

The [Search Console API is free](https://developers.google.com/webmaster-tools/pricing).
Its [quotas](https://developers.google.com/webmaster-tools/limits) cover short and long
term load, requests per site, per user and per project. Large date ranges and page or
query dimensions consume more load. This kit reads up to twenty 25,000-row pages per
query or page part. BigQuery storage and queries have separate costs.

For Google window sources, an interrupted `sending` attempt becomes `unknown`.
The next run looks for its local raw pages before repeating a read. Saved pages resume
under the original request and configuration. If the process died before a response
reached disk, that read may repeat and consume quota. A saved page is never requested
again during crash recovery. Clean rows are replaced transactionally by request key.

`fct_gsc_export_v1` is built alongside the API fact. It is empty without export data.
The `search-demand` report group combines recorded API and export routes.

## GA4 API

Add a `web_properties` entry with `source: ga4`, a stable `web_property_id`,
`client_brand_id`, and the numeric property ID as a quoted `address`.
Find that number in GA4 Admin, Property settings, Property details. It is a property
ID, not a `G-` measurement ID. An optional `hostname` filters a shared property.
Give your account the GA4 Viewer role on the property. Enable the Google Analytics
Data API in your own Google Cloud project.

Use the OAuth desktop client setup described above. This command grants both property
read scopes so Search Console and GA4 work with the same ADC file:

```sh
gcloud auth application-default login --client-id-file=/path/to/example-oauth-client.json --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/webmasters.readonly
gcloud auth application-default set-quota-project example-project
uv run brand-presence init --config config.yaml
uv run brand-presence run ga4 --config config.yaml --period 2026-W09
uv run brand-presence reports run --source ga4 --config config.yaml
```

A service account selected by `GOOGLE_APPLICATION_CREDENTIALS` also works when it has
GA4 Viewer access and the BigQuery permissions from Run it on your own project. ADC quota projects need
Service Usage Consumer permission. The client sends ADC's quota project with API calls.

GA4 writes `stg.ga4_engagement`, `stg.ga4_landing_pages` and `stg.ga4_pulls`.
It reads fixed aggregate dimensions and metrics. Landing pages have no query strings.
User IDs, event names, campaign names and custom dimensions are never requested.
Days remain in the property's time zone. An empty report writes a pull summary.
Sampling, thresholding, data cuts, folded rows and quota tokens remain on that summary.
Sampled or limited answers are retained and flagged. They are not complete measurements.

Optional `locations` of `kind: city` with `ga4` in `sources` enable
`stg.ga4_engagement_cities`. Give each city its country, name and canonical name as
`City,Region,Country`, or `City,Country`. Unlisted or ambiguous cities fold into `other`.
The folded user counts are null because they cannot be added across cities.
The report group builds `core.fct_ga4_v1`. It keeps country and city figures separate
and excludes user counts because daily users cannot be added into monthly users.

Without `--period`, collection starts with the last ended ISO week and then up to 104
previous weeks, with at most 500 history requests per run. Each report reads at most ten
100,000-row pages. History depends on the data the property still makes available.
Successful windows are not refreshed automatically, so late changes need a future
explicit refresh feature.

The Data API has no per-request charge. Its [Core quotas](https://developers.google.com/analytics/devguides/reporting/data/v1/quotas)
cover tokens per property per day and hour, tokens per project and property per hour,
concurrent requests, server errors, and potentially thresholded requests. Complex reports
consume more tokens. BigQuery storage and queries are billed separately.

The `ga4` report group also builds both export facts, `engagement_monthly_v1`
and `page_engagement_monthly_v1`. These builds accept empty export tables.
No pages reference table is needed by the API-only report group.

## Google Ads API

Add `ad_accounts` with your stable `ad_account_id` and a quoted ten-digit `customer_id`.
Find the customer ID in the Google Ads account selector and remove its hyphens.
An optional `client_brand_id` must name a client brand in `benchmark_sets`.
An account without a brand keeps `unknown`. Customer IDs stay in configuration and
raw responses. Reports use your local account ID. A local account ID cannot change customer or client brand across loads.
Use a new local ID when either binding changes.

Enable the Google Ads API in the project that owns your OAuth client. Apply for
production access on that project's Google Ads API Overview page. Give the Google user
access to each ad account, directly or through a manager account. Create an OAuth desktop
client and configure its consent screen with the `adwords` scope.

Google retired developer tokens on September 9, 2026. New users apply through Google
Cloud. Existing tokens remain available in the manager account's API Center for
historical reference. `GOOGLE_ADS_DEVELOPER_TOKEN` remains supported but is optional;
The client sends the header only when set. Google currently ignores it and will reject
it in a future major API version. Do not apply for a new token through API Center.
See Google's [developer-token migration](https://developers.google.com/google-ads/api/docs/api-policy/developer-token).

Generate your refresh token with your desktop OAuth client's downloaded JSON. Include
all three API scopes when the same ADC file serves the other Google sources:

```sh
gcloud auth application-default login --client-id-file=/path/to/example-oauth-client.json --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/webmasters.readonly
gcloud auth application-default set-quota-project example-project
```

Follow Google's [single-user authentication steps](https://developers.google.com/google-ads/api/docs/oauth/single-user-authentication).
Google currently requires passkey authentication when issuing Ads refresh tokens.
The resulting ADC JSON contains `client_id`, `client_secret`, and `refresh_token`.
Transfer those values into the matching environment variables below. Ads reads these
variables, not the ADC file. BigQuery, GA4 and Search Console still use ADC.
Keep the JSON outside the repository and do not paste its contents into logs.

```sh
export GOOGLE_ADS_CLIENT_ID='example-client-id'
export GOOGLE_ADS_CLIENT_SECRET='example-client-secret'
export GOOGLE_ADS_REFRESH_TOKEN='example-refresh-token'
# Optional legacy value from your existing manager account's API Center:
# export GOOGLE_ADS_DEVELOPER_TOKEN='example-developer-token'
# Required only when accessing accounts through this manager:
# export GOOGLE_ADS_LOGIN_CUSTOMER_ID='1000000002'
uv run brand-presence init --config config.yaml
uv run brand-presence run google-ads --config config.yaml --period 2026-W09
uv run brand-presence reports run --source google-ads --config config.yaml
```

The login customer ID identifies the manager account, not the reporting account.
Direct account access needs no login customer ID. The client only reads fixed reports.
It checks account identity, currency and time zone before collecting. Tokens stay in
memory and never enter raw responses. An expired access token is exchanged once more.
An unclassified authorization refusal stops the run so an inaccessible week is not lost.

Each account writes campaigns, geographic delivery, keywords, search terms and pull
summaries to `stg.google_ads_*`. Optional city and region reports use `locations` with
`google-ads` in `sources` and a unique `google_criterion_id` from Google's
[geographic targets](https://developers.google.com/google-ads/api/reference/data/geotargets).
These are public location criteria, not account IDs. Regions also need `region_code`.
Unlisted places fold into `other`; unmatched criteria are counted on the pull summary.
Keep country, region and city totals separate. Presence and interest also stay separate.

The default reads the last ended ISO week and then up to 52 earlier weeks, with at most
60 history requests per run. Pagination follows every page token. Days use the account's
time zone. Google can revise conversions after a week ends. Successful windows are not
refreshed automatically. Search terms are privacy-filtered and do not explain all spend.
Costs stay in the account's currency, in micros before reporting. No currency conversion
is performed. Auction shares at Google's bounds are flagged; inferred counts are estimates.

The report group builds these six tables with their original SQL and available checks:

- `core.fct_ads_v1`: monthly delivery by account, campaign and geography.
- `core.fct_ads_keywords_v1`: keyword impressions for campaign labels.
- `core.fct_ads_search_terms_v1`: named search terms and coverage.
- `core.fct_ads_auction_v1`: auction counts and bounded-share flags.
- `mart.paid_history_v1`: retained national presence history and campaign labels.
- `mart.brand_modifier_monthly_v1`: counts grouped by the first matching modifier.

Optional YAML `modifiers` entries have `modifier` and a unique positive `match_order`.
Keywords may name a `canonical_term` from the same benchmark set. These populate the
current modifier and keyword references used by reports. No channel-map reference is
needed by these six reports.

The [Google Ads API has no usage fee](https://developers.google.com/google-ads/api/docs/productionize/access-levels).
Daily operation quotas depend on [project access level](https://developers.google.com/google-ads/api/docs/api-policy/access-levels):
Explorer allows 2,880 operations against production accounts, Basic allows 15,000,
and Standard has no daily operation cap. Test access only reaches test accounts.
[Service limits and throttling](https://developers.google.com/google-ads/api/docs/best-practices/quotas)
still apply. BigQuery storage and queries cost separately. These reads do not buy ads.

The `paid` report group builds `paid_monthly_v1` after its search-demand and Ads inputs.
It works without either export configured. Missing source data stays empty or null.

## API or export

Keep the API route if you have no export. It is the default for every web property.
Existing API configuration, collection and reports keep working. You do not need an
export dataset, export permissions or a geo targets download for an API-only setup.
`init` creates empty clean export tables so combined reports can read them safely.
Run `init` again when upgrading an existing installation. The first run after this
upgrade writes one new configuration snapshot because the snapshot includes the new
export settings. This is harmless, including for API-only installations.

Choose the export route when the property already exports to BigQuery and you need
its retained history or fuller aggregates. Choose separately for each property.
A project can contain API and export properties. There is no automatic fallback.
Reports select one recorded route per property for all dates. They never fill gaps
in an export with API figures.

For GA4, follow Google's [BigQuery Export setup](https://support.google.com/analytics/answer/9823238).
Create a BigQuery link in GA4 Admin and enable daily event export. The kit reads
`events_YYYYMMDD` tables. It does not read intraday or user export tables.
For Search Console, follow Google's
[bulk data export setup](https://support.google.com/webmasters/answer/12918484).
Use the dataset containing `searchdata_site_impression`, `searchdata_url_impression`
and `ExportLog`. These exports do not recreate data from before export was enabled.

Add these keys to the relevant `web_properties` entry. Use your own project and dataset:

```yaml
route: export
project: example-export-project
dataset: analytics_100000001
```

For GA4, the dataset must be `analytics_<property ID>`, with the number matching
the property's `address`. Search Console can use a custom dataset name.

Keep the property's `address`, local ID and optional `hostname`. Project and dataset
names are validated before SQL is built. Domain-scoped project IDs such as
`example.com:proj` are not supported for `project_id` or export projects. Omitting
`route` means `api`; an API property cannot specify export project or dataset. An existing API property may switch to export
with the same local ID. Its source, address, hostname and client brand remain fixed.
Once an export dataset has been recorded, changing its binding needs a new local ID.
Reports change route after a successful pull records the new route. With several
properties, collect each configured route before rebuilding the reports.

The person running the kit uses Application Default Credentials. Grant that principal
BigQuery Data Viewer (`roles/bigquery.dataViewer`) on each export dataset and BigQuery
Job User (`roles/bigquery.jobUser`) on the kit's billing project, `project_id`.
The normal write permissions on the kit's own datasets still apply.
The export service's write permissions from Google's setup are separate from these read grants.
An export-only property does not use its API OAuth scopes or call its API.

```sh
uv run brand-presence check-access --config config.yaml
```

This command lists export tables and checks required columns and types. It reads no
export rows and submits no queries. It checks the newest GA4 daily table and all three
Search Console tables. Missing tables, columns and access are reported by local property
ID. It cannot prove Job User access or validate every historical daily schema.

Each query runs in the export dataset's location and is billed to `project_id`.
Only aggregate results are read into Python, saved locally, then written through the
shared framework to your own datasets. The locations may differ. Export storage and
query scans have BigQuery costs. See [BigQuery pricing](https://cloud.google.com/bigquery/pricing).
The top-level YAML setting `maximum_bytes_billed` limits each export query:

```yaml
maximum_bytes_billed: 21474836480  # 20 GiB per statement, the default.
```

A query exceeding the cap fails before execution. The cap is per statement, not per run.
History can issue many statements. A crash before a result is saved may repeat a query
and incur another scan. Saved pages resume without repeating their queries.
GA4 pull rows record billed bytes. Neither route reports a monetary cost.

GA4 export collection needs Google's geo targets for the listed country names and
GA4 region and city mappings. This kit does not ship Google's CSV. Choose a dated file
listed on the [official geo targets page](https://developers.google.com/google-ads/api/data/geotargets),
then run this setup command with that file's date:

```sh
uv run brand-presence setup-geo-targets --date YYYY-MM-DD --config config.yaml
```

Setup downloads the official archive into memory, validates its columns and target rows,
and saves a filtered CSV under `state_dir/<project_id>/<ops_dataset>/geo-targets/`.
Its metadata records the file date, URL, row count and download and CSV checksums.
An invalid download leaves the previous copy active. Refresh explicitly when Google's
names change. Collection does not download anything. Each configuration load records
the date used for validation. API-only and Search Console export setups need no copy.

The following report groups include their prerequisites and checks in one transaction:

| Command suffix after `reports run` | New reports |
| --- | --- |
| `--source search-console` | `core.fct_gsc_export_v1`, beside the API fact |
| `--source ga4` | `core.fct_ga4_export_v1`, `core.fct_ga4_export_pages_v1`, `mart.engagement_monthly_v1`, `mart.page_engagement_monthly_v1` |
| `--source search-demand` | `mart.search_demand_monthly_v1`, `mart.growth_v1`, after search volume and both Search Console facts |
| `--source paid` | `mart.paid_monthly_v1`, after search-demand and Google Ads reports |

Search demand starts from configured keyword volume. Collect search volume to give it
rows. Growth aggregates those months. Paid media can build with no search-demand rows;
its volume comparisons then remain null. Engagement combines property routes.
Its session and page-view columns use INT64, matching the report's casts.
SQL calculations and the source report checks are unchanged.
The page report only shows export pages listed in optional YAML `pages`:

```yaml
pages:
  - page_path: /pricing
    page_group: Commercial
```

The page report is empty for API-only properties. API landing-page sessions are a
different measure from export page views and users, so they are not substituted.
An empty page list also gives an empty page report. Export facts retain unlisted paths.

GA4's first export pull reads all available months, up to 120. Later weeks reread the
current and previous month, or from the latest collected month if older. Each month
is read through its latest daily table up to the requested week's end. The last daily
table is awaited for three days. Later gaps are counted on pull rows. A span with no
daily table is unusable. Distinct users cannot be summed across channels, places,
pages or months. Use each place's `(all)` channel row. The top 10,000 page paths retain
separate rows; the rest form `(other)`. Channel grouping depends on export schema
availability. Older tables without the required nested fields can be refused.

Search Console export reads complete weeks confirmed by both `ExportLog` namespaces.
It discovers retained history, with at most 500 historical requests per run. The API
route keeps its 65-week history. When the highest epoch version in a week rises, that
week is collected again. An unchanged maximum skips a completed week. An incomplete
first week remains unavailable. Totals include anonymized queries and expose their
counts; named query rows exclude them. Position uses the export's zero-based sum plus
one, and stays null when impressions are zero.

No live export was accessed during this port. Tests use invented rows, the BigQuery
stand-in and local SQL execution. They do not replace acceptance checks on your own export.

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

For DataForSEO, a task left `sending` becomes `unknown` and is looked up by its tag. If the ready
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

Clean writes delete by request key and by natural key, then insert
in one transaction. If a process stops after the commit, repeating the write is safe.
Task and window natural keys include their period. Mention IDs are unique across
periods. Malformed rows stay raw and are reported as parse errors.

## Limitations

The CLI has no scheduler or dashboard. Search volume is national and monthly.
Cloro supports countries and configured regions. Peec maps one project to one
country. Mentions use the country assigned to each filter. Country names and
languages are structurally validated offline; vendor acceptance is checked on the task.
Production APIs have not been called for this port. Tests use invented fixtures and
the source BigQuery fake. Report SQL is also executed locally through SQLGlot and SQLite.
Those tests do not replace a real BigQuery acceptance run.

Successful weekly API and Peec windows are not automatically refreshed. Export routes
have their own refresh rules in the API or export section. Missing data can produce empty tables or nulls.
An empty report does not prove that a brand had no activity. Sentiment and recommendation
labels are not inferred. Distinct users and ratios cannot be added across report rows.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests and style.

## Licence and support

Licensed under [Apache-2.0](LICENSE). Report bugs and request features through this
repository's GitHub Issues tab. Include the command, package version and a redacted
error. Do not attach credentials, account IDs, raw responses or private configuration.
For vulnerabilities, follow [SECURITY.md](SECURITY.md).
