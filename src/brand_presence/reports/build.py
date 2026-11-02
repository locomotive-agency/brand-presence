"""Production fact revision statements, unchanged apart from definition access."""
from brand_presence.reports.definition import Report

PULLED_AT = "pulled_at"
FACT_ROWS = "_fact_rows"

def grain(report: Report) -> list[str]:
    """A fact's key without pulled_at: the columns one row per pull is kept for."""
    return [k for k in report.definition["key"] if k != PULLED_AT]


def fact_rows_statement(report: Report) -> str:
    """The statement's rows, into the session's temporary table."""
    return f"CREATE TEMP TABLE {FACT_ROWS} AS\n{_bare(report.statement)}"


def fact_insert_statement(report: Report) -> str:
    """The rows of the temporary table that differ from the latest row of their grain in the
    fact table, each with the build's pulled_at. EXCEPT DISTINCT compares whole rows, and two
    nulls are equal."""
    returned = ", ".join(report.returned)
    return (f"INSERT INTO {report.table} ({returned}, {PULLED_AT})\n"
            f"SELECT {returned}, @{PULLED_AT} FROM (\n"
            f"  SELECT {returned} FROM {FACT_ROWS}\n"
            f"  EXCEPT DISTINCT\n"
            f"  SELECT {returned} FROM {report.table}\n"
            f"  QUALIFY ROW_NUMBER() OVER (PARTITION BY {', '.join(grain(report))} "
            f"ORDER BY {PULLED_AT} DESC) = 1\n"
            f")")


def _bare(statement: str) -> str:
    return statement.strip().rstrip(";").rstrip()
