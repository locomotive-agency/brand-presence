# Syften mentions

| | |
| --- | --- |
| Command | `run mentions` |
| Credentials | `SYFTEN_API_KEY`, your Syften API bearer token |
| Cost | Your Syften subscription. Reads create no paid tasks. |
| Period | Current local date. Reads from the saved cursor. `--period` is rejected. |
| Reports | `reports run --source mentions` |

## Setup

Create filters in your Syften account. Give each one a tag such as `$tag:example-tag`,
then map that tag in YAML:

```yaml
mention_filters:
  - tag: example-aardvark-nl
    mention_class: brand
    client_brand_id: aardvark
    subject_brand_id: aardvark
    country_code: NL
    keyword_group: kettles
mention_text_days: 366
```

| Field | Rules |
| --- | --- |
| `mention_class` | `brand`, `competitor`, `category` or `other`. |
| `client_brand_id`, `subject_brand_id` | Both required for `brand` and `competitor`. |
| `country_code`, `client_brand_id` | Stored as `unknown` when omitted. |
| `mention_text_days` | 1 to 366 days of post text retention. Default 366. |

Matches with an unlisted tag are counted and dropped before storage. Author fields are
always removed.

Optional `subreddits` write a reference table:

```yaml
subreddits:
  - subreddit: example_kettles
    community_group: kettles
    subscribers: null
    subscribers_as_of: null
```

```sh
export SYFTEN_API_KEY='example-token'
uv run brand-presence init --config config.yaml
uv run brand-presence run mentions --config config.yaml
uv run brand-presence reports run --source mentions --config config.yaml
```

## How a run reads

- The API returns matches for every filter in your Syften account, not only the mapped
  tags. The first run therefore pages through the whole account history. On an account
  with many filters this can take most of an hour and end as partial when Syften rate
  limits it. Run the command again to continue. Unmapped matches are only counted.
- Reads use POST, up to 100 matches per page. They create no paid tasks.
- Each page is saved before the next. Saved pages recover before a new pull.
- A match ID is written once across runs.
- The cursor moves forward only after clean rows are written. A partial pull keeps its
  oldest and newest timestamps, so the next run continues from there.
- If a saved page cannot finish, for example its raw file is missing, it is marked
  `failed` and the pull restarts from the unmoved cursor to cover it again.
- Rate-limit waits stop at the run deadline.

## Text retention

`init` sets partition expiry on raw mentions and clean text. Each run deletes expired
local raw files. Files only expire when a run happens. Unreadable files are kept,
skipped and counted as `raw file unreadable`. Counts remain after the text expires.
Apply the same retention to your backups.

## Tables and reports

Syften writes `raw.mentions`, `stg.mention_facts`, `stg.mention_text`,
`ops.mentions_cursor` and `ops.mentions_tasks`.

`core.fct_mentions_v1` keeps the latest labels and the vendor acceptance flag. It holds
no post text. No labels or sentiment are inferred.

## Not included

- The Syften archive backfill.
- `reddit_mentions_v1` and `reddit_mentions_v2`, which need an external labels table.
