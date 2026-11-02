# Local recovery for the first workflow

Keep the source task ledger, run lock, request keys and clean replacement rule.
Replace cloud raw objects with atomic local files. Keep operational tables and raw
rows in the configured BigQuery project. Parse recovered tasks with their original
configuration load.

A local file is named by the submitting run and attempt. Its contents still name the
collecting run. This lets a later run find an answer even when collection was not
recorded before interruption. Raw-table loads replace their own run and request pair.

An absent ready-list entry does not prove that the vendor took no paid request.
Keep such attempts unknown. Do not retry a paid POST on an HTTP 5xx. This intentionally
narrows the source's retry behaviour. Unresolved outcomes may require vendor support.
An ambiguous per-task submission status also stays unknown. Expired paid tasks do not
become new paid attempts. Unresolved history reserves its keyword and location pairs
across period boundaries, so a new month cannot pay for the same history again.

Keep the fact SQL, history SQL and report definitions unchanged. Build both in one
transaction. Fact revisions append only changed rows; the history table is replaced.
Use a namespace adapter to bind logical datasets to configured project and dataset names.

Support one host with a persistent state directory. The local process lock and the
source's expiring BigQuery run record coordinate commands within this scope. Distributed
execution and automatic resolution of unknown vendor tasks are outside this slice.
