# Peec stored answers

| | |
| --- | --- |
| Command | `run peec` |
| Credentials | `PEEC_API_KEY`, sent as `x-api-key` |
| Cost | Your Peec tracking subscription. All calls are free GET reads. |
| Period | Last complete ISO week, `--period YYYY-Www` |
| Reports | `reports run --source peec` |

The kit only reads Peec. You create and manage prompts in your Peec account. The kit
never creates prompts or starts paid tracking.

## Setup

```yaml
peec_projects:
  - mapping: example-peec-nl
    project_id: example-project
    client_brand_id: aardvark
    country_code: NL
    engines: [chatgpt, perplexity, gemini, ai-overview]
    first_date: 2026-01-01
peec_brands:
  - mapping: example-peec-nl
    brand_id: aardvark
    vendor_brand_id: example-brand
```

- `mapping` is your stable local name for a Peec project.
- Each Peec project and each benchmark set can appear once. The country needs a
  matching benchmark set.
- Add a `peec_brands` entry for every brand in that set, competitors included. Vendor
  brand IDs must be distinct within a mapping.
- A mapping cannot later point to another project, brand or country.

```sh
export PEEC_API_KEY='example-key'
uv run brand-presence init --config config.yaml
uv run brand-presence run peec --config config.yaml
uv run brand-presence reports run --source peec --config config.yaml
```

## Periods and history

- The default is the last complete ISO week in `time_zone`.
- A run also fills up to 52 earlier weeks, newest first, with at most 200 history
  requests per run, and never before `first_date`. Reruns continue missing weeks.
- `--period YYYY-Www` collects or recovers one ended week, without history.
- Successful weeks are not fetched again. There is no forced refresh, so late vendor
  changes to a collected week are not picked up.

## How a run reads

- Prompt discovery includes archived prompts. Only active prompts get new chat reads.
- Each listing page and chat detail is saved before the next call.
- A week counts as collected only after all pages pass completeness checks. Missing
  pages, changing totals, duplicate chats or mismatched prompts refuse the whole week.
- A complete empty week writes a pull marker.
- Failed reads keep earlier clean answers. An interrupted read can be repeated at no cost.

## Tables and reports

Peec writes `raw.peec`, `ops.peec_tasks`, `stg.peec_prompts`, `stg.peec_pulls`,
`stg.peec_answers` and `stg.peec_answer_brands`. The report group builds:

- `core.fct_peec_answers_v1`
- `core.fct_peec_cited_links_v1`
- `mart.peec_prompt_results_v1`
- `mart.peec_keyword_monthly_v1`
- `mart.peec_citations_v1`

Notes:

- The prompt report uses the captured prompt text and topic. Archive state comes from
  the latest prompt version seen.
- Keyword, keyword group and intent stay null. Monthly totals show `(none)` for them.
- Peec's mention position is kept apart from the kit's text mention rank.
- An empty AI Overview becomes `none shown`. Unknown answer shapes fail the week.

Also see [weekly AI reports](../reports.md#weekly-ai-reports).

## Check against your account

Account access, retention, observation time zone and late weekly updates have not been
checked against a live account. Missing or inactive engine channels stop collection,
and no substitute channel is chosen.
