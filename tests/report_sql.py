"""Source SQLGlot executor, isolated from service and migration fakes."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlglot
from sqlglot import exp
from sqlglot.executor import execute
from sqlglot.executor.env import ENV
from sqlglot.executor.table import Table as ExecutorTable
from sqlglot.optimizer.annotate_types import annotate_types

from brand_presence.reports.definition import Report

ENV.setdefault("SAFEDIVIDE", lambda a, b: None if a is None or b is None or b == 0 else a / b)
ENV.setdefault("GREATEST", lambda *a: None if any(x is None for x in a) else max(a))


def _date_trunc(part: str, value: date | None) -> date | None:
    """BigQuery's DATE_TRUNC of a date to its month, quarter or year: the first day of it."""
    unit = str(part).upper()
    # sqlglot coerces DATE to midnight DATETIME for ISOWEEK in its Python executor.
    if unit == "ISOWEEK" and isinstance(value, datetime):
        if value.time() != datetime.min.time():
            raise NotImplementedError("ISOWEEK expects a calendar date")
        value = value.date()
    if unit not in ("MONTH", "QUARTER", "YEAR", "ISOWEEK") or isinstance(value, datetime):
        raise NotImplementedError(f"DATE_TRUNC to {part} of {value!r} in the stand-in")
    if value is None:
        return None
    if unit == "ISOWEEK":
        return value - timedelta(days=value.weekday())
    if unit == "MONTH":
        return value.replace(day=1)
    if unit == "QUARTER":
        return value.replace(month=value.month - (value.month - 1) % 3, day=1)
    return value.replace(month=1, day=1)


def _date_add(value: date | None, amount, unit) -> date | None:
    """BigQuery's DATE_ADD of days or months to a date. Months keep the day, or the month's
    last day where it has fewer. The executor hands the unit as its own function of that
    name."""
    if isinstance(value, datetime) or unit not in (ENV["DAY"], ENV["MONTH"]):
        raise NotImplementedError(f"DATE_ADD of {amount!r} {unit!r} to {value!r} in the stand-in")
    if value is None or amount is None:
        return None
    if unit is ENV["DAY"]:
        return value + timedelta(days=int(amount))
    months = value.year * 12 + value.month - 1 + int(amount)
    year, month = divmod(months, 12)
    first_of_next = date(year + (month + 1) // 12, (month + 1) % 12 + 1, 1)
    return date(year, month + 1, min(value.day, (first_of_next - timedelta(days=1)).day))


def _format_date(value: date | None, form: str) -> str | None:
    """BigQuery's FORMAT_DATE, for %Y, %b, %m and %Q, the quarter's number."""
    if value is None:
        return None
    if re.search(r"%[^YbmQ]", form):
        raise NotImplementedError(f"FORMAT_DATE with {form!r} in the stand-in")
    return value.strftime(form.replace("%Q", str((value.month - 1) // 3 + 1)))


def _all(values) -> bool | None:
    held = [v for v in values if v is not None]
    return all(held) if held else None


def _any(values) -> bool | None:
    held = [v for v in values if v is not None]
    return any(held) if held else None


ENV.setdefault("DATETRUNC", _date_trunc)
ENV.setdefault("DATEADD", _date_add)
ENV.setdefault(
    "DATESUB",
    lambda value, amount, unit: _date_add(value, None if amount is None else -int(amount), unit),
)
ENV.setdefault("TIMETOSTR", _format_date)
ENV.setdefault("LOGICALAND", _all)
ENV.setdefault("LOGICALOR", _any)

# RE2's class of what is neither a letter nor a digit, and Python's of the same.
_RE2_NOT_LETTER_OR_DIGIT = (r"[^\p{L}\p{N}]", r"[\W_]")


def _sha256(value: str | None, length=256) -> bytes | None:
    """BigQuery's SHA256 of a string: the digest of its UTF-8 bytes."""
    if int(length) != 256 or not isinstance(value, (str, type(None))):
        raise NotImplementedError(f"SHA{length} of {value!r} in the stand-in")
    return None if value is None else hashlib.sha256(value.encode("utf-8")).digest()


def _regexp_replace(value: str | None, pattern: str, replacement: str) -> str | None:
    """BigQuery's REGEXP_REPLACE, for patterns Python's re reads as RE2 does."""
    pattern = pattern.replace(*_RE2_NOT_LETTER_OR_DIGIT)
    if "\\p" in pattern or "\\" in replacement:
        raise NotImplementedError(f"REGEXP_REPLACE with {pattern!r} in the stand-in")
    return None if value is None else re.sub(pattern, replacement, value)


def _date(value, zone: str | None = None) -> date | None:
    """BigQuery's DATE of a timestamp, in a time zone where one is given."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value.astimezone(ZoneInfo(zone)) if zone else value.astimezone(UTC)).date()
    raise NotImplementedError(f"DATE of {value!r} in the stand-in")


def _corr(ys, xs) -> float | None:
    """BigQuery's CORR: Pearson's coefficient over the pairs where neither is null."""
    pairs = [
        (float(y), float(x)) for y, x in zip(ys, xs, strict=True) if y is not None and x is not None
    ]
    if len(pairs) < 2:
        return None
    mean_y = sum(y for y, _ in pairs) / len(pairs)
    mean_x = sum(x for _, x in pairs) / len(pairs)
    top = sum((y - mean_y) * (x - mean_x) for y, x in pairs)
    spread_y = sum((y - mean_y) ** 2 for y, _ in pairs)
    spread_x = sum((x - mean_x) ** 2 for _, x in pairs)
    if spread_y == 0 or spread_x == 0:
        return None
    return top / math.sqrt(spread_y * spread_x)


def _number(function):
    return lambda value: None if value is None else function(float(value))


ENV.setdefault("CORR", _corr)
ENV.setdefault("ATANH", _number(math.atanh))
ENV.setdefault("TANH", _number(math.tanh))
ENV.setdefault("SQRT", _number(math.sqrt))
ENV.setdefault("SHA2DIGEST", _sha256)
ENV.setdefault("LOWERHEX", lambda value: None if value is None else value.hex())
ENV.setdefault("REGEXPREPLACE", _regexp_replace)
ENV.setdefault("DATE", _date)
ENV.setdefault(
    "ENDSWITH", lambda value, end: None if value is None or end is None else value.endswith(end)
)
ENV.setdefault("SORTEDARRAY", lambda values: None if values is None else sorted(values))
ENV.setdefault(
    "SORTEDARRAYDESC", lambda values: None if values is None else sorted(values, reverse=True)
)


ZONE = "UTC"


def run_statement(
    report_: Report,
    tables: dict[str, list[dict]],
    *,
    today: date,
    client_id: str = "sample",
    client_time_zone: str = ZONE,
    pulled_at=None,
    schemas=None,
) -> list[dict]:
    """The rows a report's statement returns, run as written by sqlglot's executor over the
    rows given for each table, named as <dataset>.<table>, with the runner's parameters bound
    and CURRENT_DATE given `today`. A fact's pulled_at is not
    added: the test gives what it needs."""
    bound = {
        "pulled_at": exp.cast(
            exp.Literal.string((pulled_at or datetime(2026, 3, 2, 12, tzinfo=UTC)).isoformat()),
            "TIMESTAMP",
        ),
        "client_id": exp.Literal.string(client_id),
        "client_time_zone": exp.Literal.string(client_time_zone),
    }

    def bind(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Parameter):
            return bound[node.name].copy()
        if isinstance(node, exp.CurrentDate):
            return exp.cast(exp.Literal.string(today.isoformat()), "DATE")
        return node

    tree = sqlglot.parse_one(report_.statement, dialect="bigquery").transform(bind)
    given: dict[str, dict[str, list[dict]]] = {}
    for table, rows in tables.items():
        dataset, name = table.split(".")
        columns = (schemas or {}).get(table)
        given.setdefault(dataset, {})[name] = (
            ExecutorTable(list(columns), []) if columns and not rows else [dict(r) for r in rows]
        )
    tree = _unnested(tree, given).transform(_sorted_array_agg).transform(_null_list).transform(lazy)
    # The executor infers columns from row zero. Empty external label tables still
    # have their migrated schema, so supply it explicitly alongside the sample rows.
    inferred = {}
    for dataset, entries in given.items():
        for name, values in entries.items():
            columns = (schemas or {}).get(f"{dataset}.{name}")
            if columns and isinstance(values, ExecutorTable):
                inferred.setdefault(dataset, {})[name] = columns
                continue
            if not values:
                continue
            inferred.setdefault(dataset, {})
            inferred[dataset][name] = {
                c: annotate_types(exp.convert(v), dialect="bigquery").type
                for c, v in (values[0].items() if values else [])
            }
    found = execute(tree, schema=inferred, dialect="bigquery", tables=given)
    return [dict(zip(found.columns, values, strict=True)) for values in found.rows]


def _null_list(node: exp.Expression) -> exp.Expression:
    """A null cast to a list, which the executor cannot cast: a plain null. BigQuery stores
    it as an empty list."""
    if (
        isinstance(node, exp.Cast)
        and isinstance(node.this, exp.Null)
        and node.to.is_type(exp.DataType.Type.ARRAY)
    ):
        return exp.Null()
    return node


def lazy(node: exp.Expression) -> exp.Expression:
    """IF as the CASE that means the same: only the result it returns is evaluated. A
    branch of a CASE is an If of its own, and is left as it is."""
    if (
        not isinstance(node, exp.If)
        or isinstance(node.parent, exp.Case)
        or "false" not in node.args
        and node.args.get("true") is None
    ):
        return node
    case = exp.Case(ifs=[exp.If(this=node.this.copy(), true=node.args["true"].copy())])
    if node.args.get("false") is not None:
        case.set("default", node.args["false"].copy())
    return case


def _sorted_array_agg(node: exp.Expression) -> exp.Expression:
    """ARRAY_AGG ordered by the value it gathers, up or down, which the executor cannot
    order: the unordered aggregate, sorted after."""
    if not isinstance(node, exp.ArrayAgg) or not isinstance(node.this, exp.Order):
        return node
    order = node.this
    gathered = order.this.expressions[0] if isinstance(order.this, exp.Distinct) else order.this
    (by,) = order.expressions
    if by.this != gathered:
        raise NotImplementedError(f"{node.sql(dialect='bigquery')} in the stand-in")
    return exp.Anonymous(
        this="SORTEDARRAYDESC" if by.args.get("desc") else "SORTEDARRAY",
        expressions=[exp.ArrayAgg(this=order.this.copy())],
    )


def _unnested(tree: exp.Expression, given: dict[str, dict[str, list[dict]]]) -> exp.Expression:
    """Each select that reads one given table and joins UNNEST of one of its arrays, read
    instead from a table the stand-in makes: one row per element, with the element as a
    column named by the UNNEST's alias."""
    for select in list(tree.find_all(exp.Select)):
        joins = select.args.get("joins") or []
        unnests = [j for j in joins if isinstance(j.this, exp.Unnest)]
        if not unnests:
            continue
        from_ = select.args.get("from_") or select.args.get("from")
        table = from_.this if from_ is not None else None
        (join,) = joins
        unnest = join.this
        (array,) = unnest.expressions
        alias = table.alias_or_name if isinstance(table, exp.Table) else None
        columns = (
            [array]
            if isinstance(array, exp.Column)
            else [array.args.get("start"), array.args.get("end")]
            if isinstance(array, exp.GenerateDateArray) and _one_day(array.args.get("step"))
            else []
        )
        if (
            not isinstance(table, exp.Table)
            or not table.db
            or len(joins) != 1
            or not columns
            or any(not isinstance(c, exp.Column) or c.table != alias for c in columns)
            or join.args.get("on") not in (None, exp.true())
        ):
            raise NotImplementedError(f"{select.sql(dialect='bigquery')} in the stand-in")
        (element,) = [c.name for c in unnest.args["alias"].columns]
        rows = given[table.db][table.name]
        assert all(element not in r for r in rows), element
        name = f"unnest_{len(given.get('standin', {}))}"
        given.setdefault("standin", {})[name] = [
            {
                **r,
                element: value,
                **({unnest.args["offset"].name: index} if unnest.args.get("offset") else {}),
            }
            for r in rows
            for index, value in enumerate(_elements(r, columns))
        ]
        table.replace(
            exp.Table(
                this=exp.to_identifier(name),
                db=exp.to_identifier("standin"),
                alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name)),
            )
        )
        # The expanded element belongs to the synthetic table. Qualify it explicitly
        # even when that table is empty and the executor cannot infer its columns.
        for column in select.find_all(exp.Column):
            if not column.table and column.name in {
                element,
                unnest.args["offset"].name if unnest.args.get("offset") else None,
            }:
                column.set("table", exp.to_identifier(alias))
        join.pop()
    return tree


def _one_day(step: exp.Expression | None) -> bool:
    """GENERATE_DATE_ARRAY's step, which the stand-in takes only as one day."""
    return step is None or (
        isinstance(step, exp.Interval)
        and step.this.name == "1"
        and step.unit is not None
        and step.unit.name.upper() == "DAY"
    )


def _elements(row: dict, columns: list[exp.Column]) -> list:
    """The elements UNNEST gives for one row: its array, or each date from the first column's
    to the second's, both included."""
    if len(columns) == 1:
        return row[columns[0].name] or []
    start, end = (row[c.name] for c in columns)
    if start is None or end is None:
        return []
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def run_check(report, check, tables):
    text = dict(report.checks)[check]
    return run_statement(replace(report, statement=text), tables, today=date(2026, 3, 2))
