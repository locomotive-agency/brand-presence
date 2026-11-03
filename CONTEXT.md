# Context

The kit collects DataForSEO search volume, Cloro AI answers, Syften mentions, and
Peec stored chats, Search Console, GA4 and Google Ads into a user's BigQuery project. Configuration is YAML. Credentials
come from environment variables or Application Default Credentials. Raw storage is a local directory.

## Vocabulary

- A benchmark set is one client brand and country, its competitors, keywords and prompts.
- A request is one source's unit of collection. It has a stable request key.
- A task is one paid vendor submission. A POST may contain several tasks.
- A period is a month for search volume, an ISO week for Cloro, Peec and Google API sources, or a date for mentions.
- A load is an immutable configuration snapshot used to plan and parse requests.
- A window is an ended week of a source report or a set of Peec chats.
- A web property binds a local ID to a GA4 property or Search Console site.
- An ad account binds a local ID to a Google Ads customer and optional client brand.
- A mapping binds a Peec project to a benchmark set. Peec owns its prompts.
- A cursor records Syften's durable progress. A resume point continues a partial pull.
- A clean row carries its source contract and request provenance.
- A fact revision records a change observed by a report build.

## Testing decisions

Tests observe the CLI, vendor HTTP boundaries, durable ledgers and run records,
raw files, BigQuery transactional writes, and report SQL results. They use no
credentials or network. Interruption tests cover submission or read, raw storage,
raw load, clean commit, cursors and history. Configuration tests cover references
and source-specific validation. Report assertions use explicit known figures.
