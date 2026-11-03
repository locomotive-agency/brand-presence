"""Execute report SQL locally. The source fake still owns transactions and row types."""
import json
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal

import sqlglot
from sqlglot import exp


def stored(value):
    return (value.isoformat() if isinstance(value, date | datetime) else
            json.dumps(value) if isinstance(value, list) else
            str(value) if isinstance(value, Decimal) else value)


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
            if not columns:
                continue
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

    def drop(bq, sql, parameters):
        bq.tables.pop("temp._fact_rows", None)
        return 0

    fake.on(r"^DROP TABLE IF EXISTS _SESSION\._fact_rows$", drop)
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


def install_sources(fake, settings):
    from dataclasses import replace

    from brand_presence.example._offline.report_sql import run_statement
    from brand_presence.reports.definition import load
    from brand_presence.schema import REF_MODELS

    reports = load()
    references = tuple(m.table.split('.')[1] for m in REF_MODELS)

    def tables_of(bq):
        tables = {}
        for logical, physical in settings.datasets.model_dump().items():
            for name, table in bq.tables.items():
                if name.startswith(physical + '.'):
                    tables[logical + '.' + name.split('.', 1)[1]] = table.rows
        snapshots = tables.get('ops.config_snapshots', [])
        if snapshots:
            latest = max(r['load_number'] for r in snapshots)
            for table in references:
                tables[f'ref.current_{table}'] = [r for r in tables['ref.' + table]
                                                   if r['load_number'] == latest]
            versions = {(r['client_brand_id'], r['country_code'], r['benchmark_version'])
                        for r in tables['ref.current_benchmark_sets']}
            for table in ('benchmark_prompts', 'benchmark_keywords', 'benchmark_competitors'):
                tables[f'ref.current_{table}'] = [r for r in tables['ref.' + table]
                    if (r['client_brand_id'], r['country_code'],
                        r['benchmark_version']) in versions]
        else:
            tables.update({f'ref.current_{table}': [] for table in references})
        if 'temp._fact_rows' in bq.tables:
            tables['_temp._fact_rows'] = bq.tables['temp._fact_rows'].rows
        return tables

    def schemas_of(bq):
        schemas = {logical + '.' + name.split('.', 1)[1]: dict(table.columns)
                for logical, physical in settings.datasets.model_dump().items()
                for name, table in bq.tables.items() if name.startswith(physical + '.')
                and table.columns}
        if 'temp._fact_rows' in bq.tables:
            schemas['_temp._fact_rows'] = dict(bq.tables['temp._fact_rows'].columns)
        for table in references:
            schemas[f'ref.current_{table}'] = schemas['ref.' + table]
        return schemas

    def logical_sql(sql):
        for logical, physical in settings.datasets.model_dump().items():
            sql = re.sub(r'`' + re.escape(f'{settings.project_id}.{physical}.')
                         + r'([A-Za-z_][A-Za-z0-9_]*)`', rf'{logical}.\1', sql)
        return sql.replace('_fact_rows', '_temp._fact_rows')

    def execute(bq, sql, parameters):
        tree = sqlglot.parse_one(sql, read='bigquery')
        for table in tree.find_all(exp.Table):
            if table.db and table.db != '_SESSION' and (table.catalog != settings.project_id
                             or table.db not in settings.datasets.model_dump().values()):
                raise ValueError(f'unqualified report table: {table.sql()}')
        if isinstance(tree, exp.Drop):
            bq.tables.pop('temp._fact_rows', None)
            return 0
        if isinstance(tree, exp.Create):
            assert 'temp._fact_rows' not in bq.tables, 'fact temporary table already exists'
            statement = sql.split(' AS\n', 1)[1].strip()
            report = next(r for r in reports if r.statement.strip() == logical_sql(statement))
            try:
                if report.name.startswith(('fct_gsc', 'fct_ga4', 'fct_ads')):
                    rows = google_fact(report, tables_of(bq), schemas_of(bq))
                else:
                    rows = run_statement(report, tables_of(bq), today=date(2026, 3, 2),
                                         client_time_zone=settings.time_zone,
                                         schemas=schemas_of(bq))
            except Exception as error:
                raise RuntimeError(report.name) from error
            columns = {c['name']: c['type'] for c in report.columns if c['name'] != 'pulled_at'}
            bq.create_table('temp._fact_rows', columns)
            bq.put('temp._fact_rows', rows)
            return len(rows)
        if isinstance(tree, exp.Insert):
            target = tree.this.this
            report = next(r for r in reports if r.name == target.name)
            target_name = settings.table(report.table)
            if report.fact:
                grain = [k for k in report.definition['key'] if k != 'pulled_at']
                latest = {}
                for row in sorted(bq.rows(target_name), key=lambda r: r['pulled_at']):
                    latest[tuple(row[k] for k in grain)] = row
                rows = []
                for row in bq.tables['temp._fact_rows'].rows:
                    old = latest.get(tuple(row[k] for k in grain))
                    if old is None or any(old[k] != row[k] for k in report.returned):
                        rows.append(dict(row, pulled_at=parameters['pulled_at']))
            else:
                rows = run_statement(report, tables_of(bq), today=date(2026, 3, 2),
                                     client_time_zone=settings.time_zone,
                                         schemas=schemas_of(bq))
            bq.put(target_name, rows)
            return len(rows)
        report = replace(reports[0], statement=logical_sql(sql))
        return run_statement(report, tables_of(bq), today=date(2026, 3, 2),
                             pulled_at=parameters.get('pulled_at'),
                             client_time_zone=settings.time_zone,
                                         schemas=schemas_of(bq))

    fake.on(r'^DROP TABLE IF EXISTS _SESSION\._fact_rows$', execute)
    fake.on(r'^CREATE TEMP TABLE _fact_rows', execute)
    fake.on(r'^INSERT INTO `[^`]+\.(?:bp_core|bp_mart)\.', execute)
    fake.on(r'(?s)^(?:SELECT|WITH).*?(?:bp_core|bp_mart|_fact_rows)', execute)


def google_fact(report, tables, schemas):
    from sqlglot.executor.table import Table as ExecutorTable

    from brand_presence.example._offline.report_sql import _unnested

    given = {}
    for name, rows in tables.items():
        dataset, table = name.split('.')
        given.setdefault(dataset, {})[table] = rows
    schemas = dict(schemas)
    tree = _unnested(sqlglot.parse_one(report.statement, read='bigquery'), given, schemas)
    sqlite_tables = {f'{d}.{n}': (list(rows[0]) if rows and not isinstance(rows, ExecutorTable)
                                 else list(schemas[f'{d}.{n}']),
                                 [] if isinstance(rows, ExecutorTable) else rows)
                     for d, entries in given.items() for n, rows in entries.items()}
    rows = evaluate(tree.sql(dialect='bigquery'), sqlite_tables)
    for row in rows:
        for column in report.columns:
            name, kind = column['name'], column['type']
            if row.get(name) is not None:
                if kind == 'DATE':
                    row[name] = date.fromisoformat(row[name])
                elif kind == 'NUMERIC':
                    row[name] = Decimal(str(row[name]))
                elif kind == 'BOOL':
                    row[name] = bool(row[name])
    return rows
