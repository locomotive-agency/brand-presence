"""Execute report SQL locally. The source fake still owns transactions and row types."""
import sqlite3
from datetime import date, datetime

import sqlglot
from sqlglot import exp


def stored(value):
    return value.isoformat() if isinstance(value, date | datetime) else value


def date_trunc(unit, value):
    if unit != "MONTH":
        raise ValueError("only MONTH is supported by these reports")
    return None if value is None else date.fromisoformat(value).replace(day=1).isoformat()


def evaluate(sql, tables, parameters=None):
    tree = sqlglot.parse_one(sql, read="bigquery")
    for table in tree.find_all(exp.Table):
        if table.name == "_fact_rows":
            table.set("this", exp.to_identifier("temp___fact_rows"))
        elif table.db:
            table.set("this", exp.to_identifier(f"{table.db}__{table.name}"))
            table.set("db", None)
            table.set("catalog", None)
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function("DATE_TRUNC", 2, date_trunc)
        for name, (columns, rows) in tables.items():
            name = name.replace(".", "__")
            fields = ", ".join(f'"{c}"' for c in columns)
            connection.execute(f'CREATE TABLE "{name}" ({fields})')
            if rows:
                placeholders = ", ".join("?" for _ in columns)
                connection.executemany(f'INSERT INTO "{name}" VALUES ({placeholders})',
                                       [[stored(r.get(c)) for c in columns] for r in rows])
        return [dict(row) for row in connection.execute(
            tree.sql(dialect="sqlite"), {k: stored(v) for k, v in (parameters or {}).items()})]


def install(fake):
    def execute(bq, sql, parameters):
        tree = sqlglot.parse_one(sql, read="bigquery")
        tables = {name: (list(t.columns), t.rows) for name, t in bq.tables.items()}
        if isinstance(tree, exp.Create):
            select = tree.expression.sql(dialect="bigquery")
            result = evaluate(select, tables, parameters)
            # The production fact SELECT has exactly the clean fields named in it.
            fields = [c.alias_or_name for c in tree.expression.selects]
            source = next(t for name, t in bq.tables.items() if name.endswith(".search_volume")
                          and "stg" in name)
            bq.create_table("temp._fact_rows", {name: source.columns[name] for name in fields})
            put(bq, "temp._fact_rows", result)
            return len(result)
        if isinstance(tree, exp.Insert):
            target = tree.this.this
            name = f"{target.db}.{target.name}"
            result = evaluate(tree.expression.sql(dialect="bigquery"), tables, parameters)
            columns = [column.name for column in tree.this.expressions]
            result = [dict(zip(columns, row.values(), strict=True)) for row in result]
            put(bq, name, result)
            return len(result)
        return evaluate(sql, tables, parameters)

    fake.on(r"^CREATE TEMP TABLE _fact_rows", execute)
    fake.on(r"^INSERT INTO `[^`]+\.(?:bp_core|bp_mart)\.", execute)
    fake.on(r"(?s)^SELECT.*(?:bp_core|bp_mart|_fact_rows)", execute)


def put(bq, name, rows):
    columns = bq.tables[name].columns
    converted = []
    for row in rows:
        row = dict(row)
        for key, kind in columns.items():
            if row.get(key) is not None and kind in ("DATE", "TIMESTAMP"):
                row[key] = (date.fromisoformat(row[key]) if kind == "DATE"
                            else datetime.fromisoformat(row[key]))
        converted.append(row)
    bq.put(name, converted)
