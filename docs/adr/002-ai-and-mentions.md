# Local task, cursor and window sources

Keep the paid-task recovery rule for Cloro. A recovered submission uses its original
idempotency key. Missing task IDs remain unknown. An ended Cloro week only collects
known tasks and finishes stored answers. It never creates a new paid request.

Port Syften's cursor and partial-pull resume point. Add a local page task ledger so
raw files can recover after interruption. Advance the cursor only after durable
raw and clean writes. Deduplicate match IDs across periods. Retain the source's
text window in BigQuery and remove expired local raw files on each mentions run.
Local expiry does not run while the CLI is idle.

Port Peec's complete-window workflow and bounded weekly history. Peec GET reads
can repeat without another paid request. Unfinished reads fail and retry within
the original attempt bound. Collected raw windows recover before new reads.
Prompt discovery also saves raw before clean versions. Discovery is repeated on
a new run, including archived prompts. Successful windows are not refreshed.

Extend raw-load identity from run and request key to run, request key and stored
request text. The latter includes page and part for Peec. This refines ADR 001 for
multi-page answers, so loading one page cannot remove its siblings. Clean tables
still delete and insert transactionally. They never merge.

Apply YAML reference rows and the immutable configuration snapshot in one
transaction. Preserve historical benchmark membership and original parse settings.
A Peec mapping cannot change project, client brand or country across snapshots.

Keep the source SQL and checks. Change namespace binding and configuration prose,
not report calculations. Build each requested report group in one transaction.
The weekly AI group includes both sources' prerequisites.
