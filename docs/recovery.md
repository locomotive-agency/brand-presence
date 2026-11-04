# Recovery and local state

The kit is built so that a crash, a timeout or a closed laptop never pays twice for the
same request and never leaves half-written rows.

## The rules

1. **Raw first.** Every vendor response is saved to disk before any clean row is written.
2. **Unknown is looked up.** A request left mid-send becomes `unknown` and is looked up
   before anything is paid for again.
3. **Replace, never merge.** Clean rows are deleted by request key and natural key, then
   inserted, in one transaction. Repeating a write is safe.
4. **Bounded retries.** A rejected, failed or unusable request gets at most three
   attempts. Expired paid tasks are not retried.

Malformed rows stay raw and are reported as parse errors.

## Local state

| Path | Holds |
| --- | --- |
| `state_dir/<project_id>/<ops_dataset>/raw/` | Raw responses |
| `state_dir/<project_id>/<ops_dataset>/geo-targets/` | GA4 geo targets, when set up |

`state_dir` defaults to `.brand-presence`, next to the config file.

- **It is private.** It holds your research terms and vendor responses.
- **Back it up** with your config. Raw rows and config snapshots are also kept in
  BigQuery, so older tasks can still be parsed.
- **Use one machine and one state folder** per project and dataset set.
- **Never delete ledger rows or state** to force a rerun.

## Locks

The BigQuery run record is a lock, and a local process lock protects the state folder.
Only one collection or report build runs at a time per project and dataset set.

After a process dies, the next run closes the abandoned record once it is one hour old.
Until then, every collection and report build is refused with "another run is in
flight". Wait, then run the same command. Saved pages are not requested again.

Ctrl-C and `SIGTERM` stop a run cleanly: an open BigQuery transaction is rolled back
and its session closed. A hard kill (`SIGKILL`, a power cut) cannot do that. BigQuery
then keeps the transaction open until its session expires, up to 24 hours, and writes to
the same tables fail with "Transaction is aborted due to concurrent update". To end it
sooner, find the session and abort it:

```sql
SELECT session_id, creation_time
FROM `region-us`.INFORMATION_SCHEMA.SESSIONS_BY_USER
WHERE is_active
ORDER BY creation_time;

CALL BQ.ABORT_SESSION('<session_id>');
```

Use your dataset location's region. Only abort sessions from the dead run.

## Pending is not failed

Requests not sent because the run's deadline passed, or because the vendor took
nothing, count as pending. Run the same command again. A missing raw file stops clean
recovery instead of paying again.

## DataForSEO

- A task left `sending` becomes `unknown` and is looked up by its tag. If the ready list
  does not have it, it stays unknown.
- A timeout or server error on a paid request is never blindly retried.
- Unknown and expired tasks are not sent again in their original period.

### Reserved history

Unresolved history from an earlier period holds its keyword and location pairs until
the `unknown` or `expired` attempt is older than DataForSEO's 30-day result retention.
Age counts from submission, or from creation if submission was never recorded.

After that, a later period can ask for the history again. **This can pay once more for
a request the vendor may have run.** It does not resolve the old attempt.

Each run's planning output lists the reserved pair count and up to five pairs. Stored
answers still waiting for clean writes keep their reservation regardless of age.

Recover promptly, and check unresolved tasks with the vendor.

## Other sources

| Source | Details |
| --- | --- |
| Cloro | [Cloro recovery](sources/cloro.md#recovery) |
| Syften | [How a run reads](sources/syften.md#how-a-run-reads) |
| Peec | [How a run reads](sources/peec.md#how-a-run-reads) |
| Search Console, GA4, Google Ads | [Search Console recovery](sources/search-console.md#recovery) |
| BigQuery exports | [Export cost](bigquery-export.md#cost) |

Design reasons are in the [decision records](adr/).
