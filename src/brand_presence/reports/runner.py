"""Build the fact revisions and history table in one transaction."""

from datetime import UTC, datetime

from google.cloud import bigquery

from brand_presence import run_record
from brand_presence.bigquery import Transaction
from brand_presence.errors import UserError
from brand_presence.reports.build import fact_insert_statement, fact_rows_statement
from brand_presence.reports.definition import load
from brand_presence.runs import new_run_id
from brand_presence.state import locked


def check_rows(transaction, table, columns, key, given, *, allow_empty=True):
    key = ", ".join(key)
    if transaction.run(f"SELECT {key} FROM {table} GROUP BY {key} HAVING COUNT(*) > 1"):
        raise ValueError("report key is not unique")
    never_null = [c["name"] for c in columns if not c["nullable"]]
    if transaction.run(
        f"SELECT * FROM {table} WHERE " + " OR ".join(f"{name} IS NULL" for name in never_null),
        given,
    ):
        raise ValueError("a required report value is null")
    if not allow_empty and not transaction.run(f"SELECT * FROM {table} LIMIT 1"):
        raise ValueError("the report has no rows; run search-volume first")


def run(bq, settings, *, clock=lambda: datetime.now(UTC), out=print, source="search-volume"):
    with locked(settings.state_path):
        now = clock()
        row = run_record.Row(new_run_id(), settings.client_id, "reports", now, frequency=None)
        lines = run_record.Lines(out)
        run_record.start(bq, row)
        try:
            active, abandoned = run_record.others(bq, row.run_id, now)
            run_record.close_abandoned(bq, abandoned, now, lines)
            if active:
                raise UserError("another run is in flight; wait for it to end or expire")
            given = [
                bigquery.ScalarQueryParameter("pulled_at", "TIMESTAMP", now),
                bigquery.ScalarQueryParameter("client_time_zone", "STRING", settings.time_zone),
            ]
            reports = load(source)
            with Transaction(bq) as transaction:
                for report in reports:
                    if report.fact:
                        transaction.run("DROP TABLE IF EXISTS _SESSION._fact_rows")
                        transaction.run(fact_rows_statement(report), given)
                        check_rows(
                            transaction,
                            "_fact_rows",
                            [c for c in report.columns if c["name"] != "pulled_at"],
                            [k for k in report.definition["key"] if k != "pulled_at"],
                            given,
                            allow_empty=source != "search-volume",
                        )
                        transaction.run(fact_insert_statement(report), given)
                    else:
                        transaction.run(f"DELETE FROM {report.table} WHERE TRUE")
                        columns = ", ".join(report.returned)
                        transaction.run(
                            f"INSERT INTO {report.table} ({columns})\n" + report.statement, given
                        )
                    check_rows(
                        transaction, report.table, report.columns, report.definition["key"], given,
                        allow_empty=source != "search-volume",
                    )
                    for name, sql in report.checks:
                        if transaction.run(sql, given):
                            raise ValueError(f"report check failed: {name}")
        except Exception as error:
            run_record.end(
                bq, row, "error", clock(), lines, error_class=type(error).__name__, error_count=1
            )
            raise
        run_record.end(bq, row, "success", clock(), lines)
        return [r.table for r in reports]
