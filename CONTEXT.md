# Context

The first workflow collects national monthly DataForSEO search volume into a user's
BigQuery project. Configuration is YAML. Raw storage is a local directory.

## Vocabulary

- A benchmark set is one client brand and country with its keywords.
- A request is a group of up to 1,000 keywords at one location and language.
- A task is one vendor submission of a request. A POST may contain several tasks.
- A period is the ended calendar month of a collection, written as YYYY-MM.
- A load is an immutable configuration snapshot used to plan and parse requests.
- A clean row is a keyword, month, location and collection period under the clean contract.
- A fact revision records a change observed by a report build.

## Testing decisions

Tests observe the CLI, DataForSEO HTTP boundary, durable ledger and run records,
raw files, BigQuery transactional writes, and report SQL results. They do not use
credentials or a network. Interruption tests cover submission, raw storage, raw load,
clean commit and history bookkeeping. Report assertions use explicit known figures.
