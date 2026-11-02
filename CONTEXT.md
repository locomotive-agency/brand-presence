# Context

The kit collects DataForSEO search volume, Cloro AI answers, Syften mentions, and
Peec stored chats into a user's BigQuery project. Configuration is YAML. Credentials
come from environment variables. Raw storage is a local directory.

## Vocabulary

- A benchmark set is one client brand and country, its competitors, keywords and prompts.
- A request is one source's unit of collection. It has a stable request key.
- A task is one paid vendor submission. A POST may contain several tasks.
- A period is a month for search volume, an ISO week for Cloro and Peec, or a date for mentions.
- A load is an immutable configuration snapshot used to plan and parse requests.
- A window is the complete set of Peec chats for a prompt, engine and ended week.
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
