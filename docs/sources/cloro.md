# Cloro AI answers

| | |
| --- | --- |
| Command | `run ai-answers` |
| Credentials | `CLORO_API_KEY` |
| Cost | Cloro credits, one task per prompt, engine and location |
| Period | Current ISO week, `--period YYYY-Www` |
| Reports | `reports run --source ai-answers` |

## Setup

Add `prompts` and `competitors` to each benchmark set:

```yaml
benchmark_sets:
  - client_brand_id: aardvark
    country_code: NL
    competitors: [example-rival]
    prompts:
      - prompt_id: example-kettle
        prompt: Which invented kettle keeps tea warm?
        engines: [chatgpt, perplexity, gemini, ai-overview]
        location_ids: []
        keyword: example kettle
        keyword_group: kettles
        intent: comparison
```

| Field | Rules |
| --- | --- |
| `prompt_id`, `prompt`, `engines` | Required. Engines are `chatgpt`, `perplexity`, `gemini` and `ai-overview`. |
| `location_ids` | Empty means the set's country. Otherwise name `locations` entries. |
| `keyword`, `keyword_group`, `intent` | Optional, but give all three or none. |

For regional prompts, add a `locations` entry and name it in `location_ids`:

```yaml
locations:
  - location_id: example-region
    country_code: NL
    name: Example region
    canonical_name: Example region,Netherlands
    region_code: NL-NH
    sources: [ai-answers]
```

Brands can list `aliases` and `domains` to improve matching.

```sh
export CLORO_API_KEY='example-key'
uv run brand-presence init --config config.yaml
uv run brand-presence run ai-answers --config config.yaml
uv run brand-presence reports run --source ai-answers --config config.yaml
```

## Cost

Each distinct prompt, engine and location is one task. Identical requests shared by
several sets are sent once. Cloro charges credits. Check your account's credit price
and cap before running. The kit does not read charged credits from responses.

## Periods

The default is the current ISO week. `--period YYYY-Www` resumes a week. An older week
only collects known tasks and finishes saved answers. It never creates new paid tasks.

## Unavailable engines

If an engine is not enabled on your Cloro account, Cloro refuses the request without
creating a task, and the run ends as partial (exit code `3`). The refusal does not use
up one of the request's three attempts, so each later run asks again in case the engine
was turned on. To stop this, remove the engine from the prompt's `engines`.

## Recovery

- A timeout is retried only with the same Cloro idempotency key.
- A request interrupted mid-send becomes `unknown`. In the current week it is looked up
  by sending the same key again. If Cloro has the key but no task ID, it stays unknown.
  No new paid key is created.
- If that lookup is refused or the engine is unavailable, the error class is saved and
  later runs stop resending. Investigate, then clear the error class to allow another
  lookup in the current week.
- Ended weeks keep unknown rows without sending again.

## Reports

Cloro writes `stg.ai_answers` and `stg.ai_answer_brands`. The report group builds:

- `core.fct_ai_answers_v1`
- `core.fct_ai_cited_links_v1`
- `mart.ai_prompt_results_v2`
- `mart.ai_keyword_monthly_v2`
- `mart.ai_citations_v1`

Facts append changed revisions. Serving reports replace whole tables in the report
transaction. Empty citation results are valid. Also see
[weekly AI reports](../reports.md#weekly-ai-reports).

## How answers are read

- Brand names and aliases match whole words, ignoring case.
- Mention rank is the position in the text. It is not a recommendation or sentiment
  score, and those labels stay null.
- A results page with no AI overview is stored as `none shown`. Monthly counts leave it
  out of mention rates.
- Country and regional answers are separate samples.
- Monthly reports start in 2024.
