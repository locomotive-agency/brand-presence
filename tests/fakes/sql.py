"""The small part of BigQuery's SQL that the BigQuery stand-in runs on its table store.

It is enough for run records, ledgers, the `ref` tables and migrations: one table per
statement, conditions joined by AND, values given as parameters or literals. A statement it
does not read raises `NotUnderstood`; it never guesses and never passes one over. That
error is no `Exception`, so code that closes a run on any error cannot take it for one, and
each one raised is kept for the end of the test (tests/fakes/journal.py, `Misuse`). A test
that needs more registers a handler on the stand-in (`FakeBigQuery.on`).

Understood, with keywords in any letter case and table names with or without backticks:

    BEGIN [TRANSACTION] / COMMIT [TRANSACTION] / ROLLBACK [TRANSACTION], and no word more
    CREATE TABLE [IF NOT EXISTS] t (col TYPE [NOT NULL] [OPTIONS (...)], ...)
        [PARTITION BY col | DATE(col)] [CLUSTER BY ...] [OPTIONS (name = value, ...)]
    CREATE [OR REPLACE] VIEW v AS ...            (kept as text; a view cannot be read)
    ALTER TABLE t ADD COLUMN [IF NOT EXISTS] col TYPE
    ALTER TABLE [IF EXISTS] t SET OPTIONS (name = value, ...)
    INSERT [INTO] t (cols) VALUES (...), (...)
    INSERT [INTO] t (cols) SELECT * | fields FROM UNNEST(@rows)
    SELECT * | col [AS a] | COUNT(*) | MAX(col) | MIN(col), ... FROM t
        [WHERE ...] [ORDER BY col [DESC], ...] [LIMIT n]
    UPDATE t SET col = value, ... WHERE ...
    DELETE [FROM] t WHERE ...

    WHERE: TRUE, col = | != | <> | < | <= | > | >= value, col [NOT] IN UNNEST(@p),
           col [NOT] IN (value, ...), col IS [NOT] NULL,
           STRUCT(col, ...) [NOT] IN UNNEST(@p), with @p an array of structs whose fields
           have the columns' types, in their order, joined by AND
    value: @parameter, 'text', a number, TRUE, FALSE, NULL, CURRENT_TIMESTAMP()

    TYPE:  STRING, BYTES, INT64, FLOAT64, NUMERIC, BIGNUMERIC, BOOL, DATE, DATETIME, TIME,
           TIMESTAMP, GEOGRAPHY, JSON, their other names (INT, BOOLEAN, DECIMAL, ...),
           STRUCT<name TYPE, ...> and ARRAY<TYPE>

Types are checked as BigQuery checks them. A value keeps the type it was declared with: a
parameter its own, null or not, and a literal the type of its text. It goes into a column
or is compared with one only when BigQuery would coerce it: INT64 to FLOAT64 and NUMERIC,
text given as a parameter or a literal to DATE, DATETIME, TIME and TIMESTAMP, and nothing
to INT64, BOOL or STRING but itself. A row of a load job is checked value by value, and
more strictly than BigQuery: a number is not taken as text, nor text as a number.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from functools import cache

from google.api_core.exceptions import BadRequest, Conflict, NotFound

from fakes.journal import Misuse


class NotUnderstood(Misuse):
    """A statement outside what the stand-in reads."""


# A type's name in SQL, with its other names, as the one name the stand-in keeps.
_TYPES = {
    "STRING": "STRING", "BYTES": "BYTES", "INT64": "INT64", "INT": "INT64",
    "SMALLINT": "INT64", "INTEGER": "INT64", "BIGINT": "INT64", "TINYINT": "INT64",
    "BYTEINT": "INT64", "FLOAT64": "FLOAT64", "NUMERIC": "NUMERIC", "DECIMAL": "NUMERIC",
    "BIGNUMERIC": "BIGNUMERIC", "BIGDECIMAL": "BIGNUMERIC", "BOOL": "BOOL", "BOOLEAN": "BOOL",
    "DATE": "DATE", "DATETIME": "DATETIME", "TIME": "TIME", "TIMESTAMP": "TIMESTAMP",
    "GEOGRAPHY": "GEOGRAPHY", "JSON": "JSON",
}
# What the API calls each type when it describes a table (tables.get).
API_NAMES = {
    "INT64": "INTEGER", "FLOAT64": "FLOAT", "BOOL": "BOOLEAN", "STRUCT": "RECORD",
}
# What BigQuery coerces without being asked, from a type to a wider one.
_WIDER = {
    "INT64": ("FLOAT64", "NUMERIC", "BIGNUMERIC"),
    "NUMERIC": ("BIGNUMERIC", "FLOAT64"),
    "BIGNUMERIC": ("FLOAT64",),
}
_NUMBERS = ("INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC")
_TIMES = ("DATE", "DATETIME", "TIME", "TIMESTAMP")

# Where a value in a statement came from, which decides what it may be coerced to.
LITERAL = "literal"
PARAMETER = "parameter"
FIELD = "field"          # a field of a row given as a struct, or a function's result


@dataclass(frozen=True)
class Kind:
    """A column's or a value's type. An array is its element's type with `repeated`."""

    base: str
    repeated: bool = False
    fields: tuple[tuple[str, Kind], ...] = ()

    @property
    def element(self) -> Kind:
        return replace(self, repeated=False)

    def text(self) -> str:
        """The type as SQL names it, type parameters dropped."""
        if self.repeated:
            return f"ARRAY<{self.element.text()}>"
        if self.base == "STRUCT":
            inner = ", ".join(f"{name} {kind.text()}" for name, kind in self.fields)
            return f"STRUCT<{inner}>"
        return self.base


@cache
def parse_type(text: str) -> Kind:
    """A type from its text in SQL. Raises NotUnderstood on anything else."""
    parser = _Parser(text, {}, {}, lambda: datetime.now(UTC))
    kind = parser.kind()
    if not parser.done():
        parser.unknown()
    return kind


def parameter_kind(described: dict) -> Kind:
    """A parameter's type, from what the client sends to the API as `parameterType`."""
    name = str(described.get("type", "")).upper()
    if name == "ARRAY":
        inner = parameter_kind(described.get("arrayType") or {})
        if inner.repeated:
            raise BadRequest("stand-in: an array of arrays is no type")
        return replace(inner, repeated=True)
    if name in ("STRUCT", "RECORD"):
        return Kind("STRUCT", fields=tuple(
            (item.get("name"), parameter_kind(item["type"]))
            for item in described.get("structTypes") or []
        ))
    base = {"FLOAT": "FLOAT64", **_TYPES}.get(name)
    if base is None:
        raise NotUnderstood(f"the stand-in does not know the parameter type {name!r}")
    return Kind(base)


def passes(declared: Kind | None, column: Kind, origin: str) -> bool:
    """Whether BigQuery puts a value declared as one type into a column of another."""
    if declared is None:            # a bare NULL takes any type
        return True
    if declared.repeated != column.repeated:
        return False
    if declared.repeated or declared.base == "STRUCT" or column.base == "STRUCT":
        # Inside an array or a struct the types must be the same, field by field.
        return (declared.base == column.base
                and [k for _, k in declared.fields] == [k for _, k in column.fields])
    if declared.base == column.base or column.base in _WIDER.get(declared.base, ()):
        return True
    if declared.base == "STRING" and column.base in _TIMES:
        return origin in (LITERAL, PARAMETER)
    return (origin == LITERAL and declared.base == "FLOAT64"
            and column.base in ("NUMERIC", "BIGNUMERIC"))


def held(value: object, kind: Kind, where: str) -> object:
    """A value as a column of this type holds it, so a time given as text compares as a
    time. Raises BadRequest when the value is not one of that type."""
    def wrong():
        return BadRequest(
            f"stand-in: {where} takes {kind.text()}, and was given {type(value).__name__}"
        )

    if kind.repeated:
        # BigQuery holds no null array: a missing one is empty.
        if value is None:
            return []
        if not isinstance(value, list | tuple) or any(item is None for item in value):
            raise wrong()
        return [held(item, kind.element, where) for item in value]
    if value is None:
        return None
    base = kind.base
    if base == "STRUCT":
        names = [name for name, _ in kind.fields]
        if not isinstance(value, dict) or any(key not in names for key in value):
            raise wrong()
        return {name: held(value.get(name), inner, f"{where}.{name}")
                for name, inner in kind.fields}
    if base == "JSON":
        return value
    if base == "BOOL":
        if not isinstance(value, bool):
            raise wrong()
        return value
    if isinstance(value, bool):
        raise wrong()
    if base in ("STRING", "GEOGRAPHY"):
        ok = isinstance(value, str)
    elif base == "BYTES":
        ok = isinstance(value, bytes | str)
    elif base == "INT64":
        ok = isinstance(value, int)
    elif base in ("FLOAT64", "NUMERIC", "BIGNUMERIC"):
        ok = isinstance(value, int | float | Decimal)
    else:
        return _a_time(value, base, wrong)
    if not ok:
        raise wrong()
    return value


def _a_time(value: object, base: str, wrong: Callable[[], Exception]) -> object:
    try:
        if base == "TIMESTAMP":
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not isinstance(value, datetime):
                raise wrong()
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if base == "DATE":
            if isinstance(value, str):
                return date.fromisoformat(value)
            # A time is no date: the client would send it whole, and BigQuery refuse it.
            if isinstance(value, datetime) or not isinstance(value, date):
                raise wrong()
            return value
        if base == "DATETIME":
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            if not isinstance(value, datetime):
                raise wrong()
            return value
        if isinstance(value, str):
            return time.fromisoformat(value)
        if not isinstance(value, time):
            raise wrong()
        return value
    except ValueError:
        raise wrong() from None


@dataclass
class FakeTable:
    name: str                                   # <dataset>.<table>
    columns: dict[str, str] = field(default_factory=dict)   # column -> type, in order
    required: set[str] = field(default_factory=set)
    partition_column: str | None = None
    options: dict[str, object] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)
    view_sql: str | None = None
    # When the table was last changed: made, given rows, rows changed or deleted, columns or
    # options changed. BigQuery gives it as the table's `lastModifiedTime`.
    last_changed_time: datetime | None = None

    def kind(self, column: str) -> Kind:
        return parse_type(self.columns[column])

    @property
    def partition_expiration_days(self) -> float | None:
        value = self.options.get("partition_expiration_days")
        return None if value is None else float(value)


@dataclass
class Outcome:
    """What one statement gave: rows for a SELECT, a count of rows for DML."""

    rows: list[dict] | None = None
    affected: int | None = None


_TOKEN = re.compile(
    r"""
      (?P<skip>\s+|--[^\n]*|/\*.*?\*/)
    | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<param>@[A-Za-z_][A-Za-z0-9_]*)
    | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<name>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
    | (?P<op><=|>=|!=|<>|=|<|>|[-+/])
    | (?P<punct>[(),;*\[\].])
    """,
    re.X | re.S,
)


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int

    @property
    def word(self) -> str:
        return self.text.upper() if self.kind == "name" else ""


def tokens(sql: str) -> list[Token]:
    found = []
    position = 0
    while position < len(sql):
        match = _TOKEN.match(sql, position)
        if match is None:
            raise NotUnderstood(f"cannot read the statement at character {position}")
        if match.lastgroup != "skip":
            if match.lastgroup == "string" and found and found[-1].kind == "string" \
                    and found[-1].end == match.start():
                # As BigQuery: '' is no quote, and two texts need a blank between them.
                raise BadRequest("stand-in: two texts with nothing between them at "
                                 f"character {position}; a quote inside text is \\'")
            found.append(Token(match.lastgroup, match.group(0), match.start(), match.end()))
        position = match.end()
    return found


def split(sql: str) -> list[str]:
    """The statements of a script, each as its own text, without the semicolons."""
    statements = []
    start = None
    end = 0
    for token in tokens(sql):
        if token.text == ";":
            if start is not None:
                statements.append(sql[start:end])
            start = None
        else:
            start = token.start if start is None else start
            end = token.end
    if start is not None:
        statements.append(sql[start:end])
    return statements


def table_name(text: str) -> str:
    """`project.dataset.table`, with or without backticks, as <dataset>.<table>."""
    parts = text.strip("`").split(".")
    if len(parts) not in (2, 3) or not all(parts):
        raise NotUnderstood(f"a table is named <dataset>.<table> or with its project: {text}")
    return ".".join(parts[-2:])


class _Parser:
    def __init__(self, text: str, params: dict, kinds: dict[str, Kind],
                 clock: Callable[[], datetime]):
        self.text = text
        self.items = tokens(text)
        self.at = 0
        self.params = params
        # The declared type of each parameter, by name.
        self.kinds = kinds
        self.clock = clock

    # Reading tokens ----------------------------------------------------------------

    def peek(self, ahead: int = 0) -> Token | None:
        index = self.at + ahead
        return self.items[index] if index < len(self.items) else None

    def next(self) -> Token:
        token = self.peek()
        if token is None:
            raise NotUnderstood(f"the statement stops short: {self.text}")
        self.at += 1
        return token

    def done(self) -> bool:
        return self.at >= len(self.items)

    def word(self, *words: str) -> bool:
        """Take the next tokens when they are these keywords."""
        for offset, wanted in enumerate(words):
            token = self.peek(offset)
            if token is None or token.word != wanted:
                return False
        self.at += len(words)
        return True

    def need_word(self, *words: str) -> None:
        if not self.word(*words):
            self.unknown()

    def punct(self, text: str) -> bool:
        token = self.peek()
        if token is not None and token.kind in ("punct", "op") and token.text == text:
            self.at += 1
            return True
        return False

    def need_punct(self, text: str) -> None:
        if not self.punct(text):
            self.unknown()

    def name(self) -> str:
        token = self.next()
        if token.kind != "name":
            self.unknown()
        return token.text

    def column(self) -> str:
        """A column's name, without a table alias in front."""
        return self.name().strip("`").split(".")[-1]

    def unknown(self):
        raise NotUnderstood(f"the stand-in does not read this statement: {self.text}")

    # Values and conditions -----------------------------------------------------------

    def kind(self) -> Kind:
        """A type, as a column definition names it."""
        word = self.next().word
        if word == "ARRAY":
            self.need_punct("<")
            inner = self.kind()
            self.need_punct(">")
            if inner.repeated:
                raise BadRequest("stand-in: an array of arrays is no type")
            return replace(inner, repeated=True)
        if word == "STRUCT":
            self.need_punct("<")
            fields = []
            while True:
                fields.append((self.column(), self.kind()))
                if self.punct(">"):
                    return Kind("STRUCT", fields=tuple(fields))
                self.need_punct(",")
        if word not in _TYPES:
            self.unknown()
        if self.punct("("):
            # A length or a precision. The API's description of a table leaves it out.
            while True:
                if self.next().kind != "number":
                    self.unknown()
                if self.punct(")"):
                    break
                self.need_punct(",")
        return Kind(_TYPES[word])

    def typed(self) -> tuple[object, Kind | None, str]:
        """The next value, the type it was declared with (None for a bare NULL), and
        where it came from."""
        token = self.next()
        if token.kind == "param":
            name = token.text[1:]
            if name not in self.params:
                raise BadRequest(f"stand-in: no value for the parameter {name}")
            kind = self.kinds.get(name)
            if kind is None:
                raise NotUnderstood(f"the parameter {name} was given with no type")
            # What the client sent must be a value of the type it was declared as.
            return held(self.params[name], kind, f"the parameter {name}"), kind, PARAMETER
        if token.kind == "string":
            body = token.text[1:-1]
            body = body.replace("\\'", "'").replace('\\"', '"')
            return body, Kind("STRING"), LITERAL
        if token.kind == "number":
            if "." in token.text:
                return float(token.text), Kind("FLOAT64"), LITERAL
            return int(token.text), Kind("INT64"), LITERAL
        if token.word in ("TRUE", "FALSE"):
            return token.word == "TRUE", Kind("BOOL"), LITERAL
        if token.word == "NULL":
            return None, None, LITERAL
        if token.word == "CURRENT_TIMESTAMP":
            self.need_punct("(")
            self.need_punct(")")
            return self.clock(), Kind("TIMESTAMP"), FIELD
        self.unknown()

    def value(self) -> object:
        """The next value alone, where no column gives it a type: an option, a LIMIT."""
        return self.typed()[0]

    def stored(self, table: FakeTable, column: str) -> object:
        """The next value, as the column holds it. Refused when BigQuery would refuse to
        put a value of its declared type in that column."""
        value, declared, origin = self.typed()
        kind = table.kind(column)
        where = f"{table.name}.{column}"
        if not passes(declared, kind, origin):
            raise BadRequest(
                f"stand-in: {where} is {kind.text()}, and the value is {declared.text()}"
            )
        return held(value, kind, where)

    def compared(self, table: FakeTable, column: str, *, array: bool = False) -> object:
        """The next value, as it compares with the column. With `array`, an array
        parameter whose elements do."""
        value, declared, origin = self.typed()
        kind = table.kind(column)
        where = f"{table.name}.{column}"
        if array:
            if declared is None or not declared.repeated:
                raise BadRequest("stand-in: UNNEST needs an array parameter")
            declared, values = declared.element, value
        else:
            values = [value]
        if declared is None:
            return None
        widened = not passes(declared, kind, origin)
        if widened and not passes(kind, declared, FIELD):
            raise BadRequest(
                f"stand-in: {where} is {kind.text()} and cannot be compared with "
                f"{declared.text()}"
            )
        # A number compared with a narrower column stays the number it is.
        found = [v if widened else held(v, kind, where) for v in values]
        return found if array else found[0]

    def condition(self, table: FakeTable) -> Callable[[dict], bool]:
        terms = [self.term(table)]
        while self.word("AND"):
            terms.append(self.term(table))
        token = self.peek()
        if token is not None and token.word == "OR":
            self.unknown()
        return lambda row: all(term(row) for term in terms)

    def struct_term(self, table: FakeTable) -> Callable[[dict], bool]:
        """STRUCT(col, ...) [NOT] IN UNNEST(@p). As in SQL, a row with a null in one of
        the columns is never found, and neither is a struct with a null in a field."""
        self.need_punct("(")
        columns = [self.column()]
        while self.punct(","):
            columns.append(self.column())
        self.need_punct(")")
        for column in columns:
            if column not in table.columns:
                raise BadRequest(f"stand-in: {table.name} has no column {column}")
        negated = self.word("NOT")
        self.need_word("IN", "UNNEST")
        self.need_punct("(")
        value, declared, _ = self.typed()
        self.need_punct(")")
        if declared is None or not declared.repeated or declared.base != "STRUCT":
            raise BadRequest("stand-in: UNNEST needs an array of structs here")
        kinds = [kind for _, kind in declared.fields]
        if kinds != [table.kind(column) for column in columns]:
            raise BadRequest(
                f"stand-in: the structs' fields do not have the types of {', '.join(columns)}"
            )
        names = [name for name, _ in declared.fields]
        wanted = [tuple(item[name] for name in names) for item in value]
        wanted = [item for item in wanted if all(part is not None for part in item)]

        def keep(row: dict) -> bool:
            found = tuple(row.get(column) for column in columns)
            if any(part is None for part in found):
                return False
            return (found in wanted) != negated

        return keep

    def term(self, table: FakeTable) -> Callable[[dict], bool]:
        if self.word("TRUE"):
            return lambda row: True
        if self.word("STRUCT"):
            return self.struct_term(table)
        column = self.column()
        if column not in table.columns:
            raise BadRequest(f"stand-in: {table.name} has no column {column}")
        if self.word("IS"):
            negated = self.word("NOT")
            self.need_word("NULL")
            return lambda row: (row.get(column) is not None) == negated
        negated = self.word("NOT")
        if self.word("IN"):
            if self.word("UNNEST"):
                self.need_punct("(")
                wanted = self.compared(table, column, array=True)
                self.need_punct(")")
            else:
                self.need_punct("(")
                wanted = [self.compared(table, column)]
                while self.punct(","):
                    wanted.append(self.compared(table, column))
                self.need_punct(")")
            return lambda row: row.get(column) is not None and (
                (row.get(column) in wanted) != negated
            )
        if negated:
            self.unknown()
        token = self.next()
        if token.kind != "op":
            self.unknown()
        other = self.compared(table, column)
        return lambda row: _compare(row.get(column), token.text, other)


def _compare(left: object, op: str, right: object) -> bool:
    # As in SQL, a comparison with NULL is never true.
    if left is None or right is None:
        return False
    if op == "=":
        return left == right
    if op in ("!=", "<>"):
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    return left >= right


class Engine:
    """Runs statements on a table store. One per stand-in."""

    def __init__(self, tables: dict[str, FakeTable], clock: Callable[[], datetime]):
        self.tables = tables
        self.clock = clock

    def table(self, name: str) -> FakeTable:
        if name not in self.tables:
            raise NotFound(f"stand-in: no table {name}")
        return self.tables[name]

    def changed(self, table: FakeTable, rows: int) -> None:
        """Rows were written, changed or deleted. A statement that touched no row leaves
        the table's last changed time as it was."""
        if rows:
            table.last_changed_time = self.clock()

    def data_table(self, name: str) -> FakeTable:
        table = self.table(name)
        if table.view_sql is not None:
            raise NotUnderstood(
                f"{name} is a view, and the stand-in runs no view; register a handler for it"
            )
        return table

    def run(self, statement: str, params: dict, kinds: dict[str, Kind] | None = None, *,
            dry: bool = False) -> Outcome:
        """Run one statement. With `dry`, read it and check its tables, and change nothing."""
        parser = _Parser(statement, params, kinds or {}, self.clock)
        first = parser.next().word
        if first == "CREATE":
            return self._create(parser, dry)
        if first == "ALTER":
            return self._alter(parser, dry)
        if first == "INSERT":
            return self._insert(parser, dry)
        if first == "SELECT":
            return self._select(parser, dry)
        if first == "UPDATE":
            return self._update(parser, dry)
        if first == "DELETE":
            return self._delete(parser, dry)
        parser.unknown()

    # Tables and views -------------------------------------------------------------------

    def _create(self, parser: _Parser, dry: bool) -> Outcome:
        replace = parser.word("OR", "REPLACE")
        if parser.word("VIEW"):
            name = table_name(parser.name())
            parser.need_word("AS")
            if parser.done():
                parser.unknown()
            body = parser.text[parser.peek().start:]
            existing = self.tables.get(name)
            if existing is not None and (existing.view_sql is None or not replace):
                raise Conflict(f"stand-in: {name} already exists")
            if not dry:
                self.tables[name] = FakeTable(name, view_sql=body,
                                              last_changed_time=self.clock())
            return Outcome()
        if replace:
            parser.unknown()
        parser.need_word("TABLE")
        if_missing = parser.word("IF", "NOT", "EXISTS")
        name = table_name(parser.name())
        table = FakeTable(name)
        parser.need_punct("(")
        while True:
            column = parser.column()
            kind, required = self._column_type(parser)
            if column in table.columns:
                raise BadRequest(f"stand-in: {name} names the column {column} twice")
            table.columns[column] = kind
            if required:
                table.required.add(column)
            if parser.punct(")"):
                break
            parser.need_punct(",")
        if parser.word("PARTITION", "BY"):
            first = parser.name()
            if parser.punct("("):
                table.partition_column = parser.column()
                depth = 1
                while depth:
                    text = parser.next().text
                    depth += (text == "(") - (text == ")")
            elif not first.startswith("_"):
                table.partition_column = first.strip("`").split(".")[-1]
            if table.partition_column and table.partition_column not in table.columns:
                raise BadRequest(f"stand-in: {name} is partitioned by a column it lacks")
        if parser.word("CLUSTER", "BY"):
            parser.column()
            while parser.punct(","):
                parser.column()
        if parser.word("OPTIONS"):
            table.options = self._options(parser)
        if not parser.done():
            parser.unknown()
        if name in self.tables:
            if if_missing:
                return Outcome()
            raise Conflict(f"stand-in: {name} already exists")
        if not dry:
            table.last_changed_time = self.clock()
            self.tables[name] = table
        return Outcome()

    def _column_type(self, parser: _Parser) -> tuple[str, bool]:
        """The type of a column, and whether it is required. After the type come NOT NULL
        and OPTIONS and nothing else: a DEFAULT, a key or a collation is not read."""
        kind = parser.kind()
        required = False
        while True:
            if parser.word("NOT", "NULL"):
                required = True
            elif parser.word("OPTIONS"):
                self._options(parser)
            else:
                break
        if required and kind.repeated:
            raise BadRequest("stand-in: an array column cannot be required")
        return kind.text(), required

    def _options(self, parser: _Parser) -> dict[str, object]:
        options: dict[str, object] = {}
        parser.need_punct("(")
        while True:
            key = parser.name().lower()
            parser.need_punct("=")
            options[key] = parser.value()
            if parser.punct(")"):
                return options
            parser.need_punct(",")

    def _alter(self, parser: _Parser, dry: bool) -> Outcome:
        parser.need_word("TABLE")
        if_there = parser.word("IF", "EXISTS")
        name = table_name(parser.name())
        if if_there and name not in self.tables:
            # Passed over. Only the one form is read: there is no table to read columns
            # against.
            if not parser.word("SET", "OPTIONS"):
                parser.unknown()
            self._options(parser)
            if not parser.done():
                parser.unknown()
            return Outcome()
        table = self.data_table(name)
        if parser.word("SET", "OPTIONS"):
            options = self._options(parser)
            if not parser.done():
                parser.unknown()
            if "partition_expiration_days" in options and table.partition_column is None:
                raise BadRequest(f"stand-in: {table.name} is not partitioned")
            if not dry:
                table.options.update(options)
                table.last_changed_time = self.clock()
            return Outcome()
        added: dict[str, str] = {}
        while True:
            parser.need_word("ADD", "COLUMN")
            if_missing = parser.word("IF", "NOT", "EXISTS")
            column = parser.column()
            kind, required = self._column_type(parser)
            if required:
                raise BadRequest("stand-in: a column added to a table cannot be required")
            if column in table.columns or column in added:
                if not if_missing:
                    raise BadRequest(f"stand-in: {table.name} already has {column}")
            else:
                added[column] = kind
            if not parser.punct(","):
                break
        if not parser.done():
            parser.unknown()
        if not dry and added:
            table.last_changed_time = self.clock()
        if not dry:
            table.columns.update(added)
            # A row that was there before the column reads NULL in it, an array as empty.
            for row in table.rows:
                for column in added:
                    row[column] = held(None, table.kind(column), column)
        return Outcome()

    # Rows ----------------------------------------------------------------------------------

    def check_row(self, table: FakeTable, row: dict) -> dict:
        """A row as the table holds it: every column, typed, and nothing else."""
        for column in row:
            if column not in table.columns:
                raise BadRequest(f"stand-in: {table.name} has no column {column}")
        full = {c: held(row.get(c), table.kind(c), f"{table.name}.{c}")
                for c in table.columns}
        for column in table.required:
            if full[column] is None:
                raise BadRequest(f"stand-in: {table.name}.{column} is required")
        return full

    def _insert(self, parser: _Parser, dry: bool) -> Outcome:
        parser.word("INTO")
        table = self.data_table(table_name(parser.name()))
        parser.need_punct("(")
        columns = [parser.column()]
        while parser.punct(","):
            columns.append(parser.column())
        parser.need_punct(")")
        for column in columns:
            if column not in table.columns:
                raise BadRequest(f"stand-in: {table.name} has no column {column}")
        if len(set(columns)) != len(columns):
            raise BadRequest("stand-in: the insert names a column twice")
        rows: list[dict] = []
        if parser.word("VALUES"):
            while True:
                parser.need_punct("(")
                row: dict = {}
                at = 0
                while True:
                    if at >= len(columns):
                        raise BadRequest("stand-in: a row has another number of values")
                    row[columns[at]] = parser.stored(table, columns[at])
                    at += 1
                    if not parser.punct(","):
                        break
                parser.need_punct(")")
                if at != len(columns):
                    raise BadRequest("stand-in: a row has another number of values")
                rows.append(row)
                if not parser.punct(","):
                    break
        elif parser.word("SELECT"):
            chosen: list[str] | None = None
            if not parser.punct("*"):
                chosen = [parser.column()]
                while parser.punct(","):
                    chosen.append(parser.column())
            parser.need_word("FROM", "UNNEST")
            parser.need_punct("(")
            given, declared, _ = parser.typed()
            parser.need_punct(")")
            if declared is None or not declared.repeated or declared.base != "STRUCT":
                raise BadRequest("stand-in: UNNEST needs an array of rows, each a struct")
            fields = dict(declared.fields)
            # A star takes the struct's fields in their own order, as BigQuery does.
            chosen = list(fields) if chosen is None else chosen
            if len(chosen) != len(columns):
                raise BadRequest("stand-in: the rows do not fit the columns")
            for column, source in zip(columns, chosen, strict=True):
                if source not in fields:
                    raise BadRequest(f"stand-in: the rows have no field {source}")
                # A field is no parameter: text in it is not coerced to a time.
                if not passes(fields[source], table.kind(column), FIELD):
                    raise BadRequest(
                        f"stand-in: {table.name}.{column} is {table.columns[column]}, and "
                        f"the field {source} is {fields[source].text()}"
                    )
            for item in given:
                rows.append({c: item[source] for c, source in zip(columns, chosen, strict=True)})
        else:
            parser.unknown()
        if not parser.done():
            parser.unknown()
        checked = [self.check_row(table, row) for row in rows]
        if not dry:
            table.rows.extend(checked)
            self.changed(table, len(checked))
        return Outcome(affected=len(checked))

    def _select(self, parser: _Parser, dry: bool) -> Outcome:
        items: list[tuple[str, str | None, str]] = []   # (function, column, output name)
        star = False
        while True:
            if parser.punct("*"):
                star = True
            else:
                name = parser.name()
                function = name.upper()
                if function in ("COUNT", "MAX", "MIN") and parser.punct("("):
                    column = None if parser.punct("*") else parser.column()
                    parser.need_punct(")")
                    if column is None and function != "COUNT":
                        parser.unknown()
                    output = f"f{len(items)}_"
                else:
                    function, column = "", name.strip("`").split(".")[-1]
                    output = column
                if parser.word("AS"):
                    output = parser.column()
                items.append((function, column, output))
            if not parser.punct(","):
                break
        parser.need_word("FROM")
        table = self.data_table(table_name(parser.name()))
        for _, column, _ in items:
            if column is not None and column not in table.columns:
                raise BadRequest(f"stand-in: {table.name} has no column {column}")
        keep = parser.condition(table) if parser.word("WHERE") else (lambda row: True)
        order: list[tuple[str, bool]] = []
        if parser.word("ORDER", "BY"):
            while True:
                column = parser.column()
                if column not in table.columns:
                    raise BadRequest(f"stand-in: {table.name} has no column {column}")
                descending = parser.word("DESC")
                if not descending:
                    parser.word("ASC")
                order.append((column, descending))
                if not parser.punct(","):
                    break
        limit = None
        if parser.word("LIMIT"):
            limit = parser.value()
        if not parser.done():
            parser.unknown()
        if dry:
            return Outcome(rows=[])
        rows = [row for row in table.rows if keep(row)]
        for column, descending in reversed(order):
            rows.sort(key=lambda r, c=column: (r[c] is not None, r[c]), reverse=descending)
        if any(function for function, _, _ in items):
            if star or not all(function for function, _, _ in items):
                parser.unknown()
            out = {}
            for function, column, output in items:
                values = [r[column] for r in rows if r[column] is not None] if column else rows
                if function == "COUNT":
                    out[output] = len(values)
                elif function == "MAX":
                    out[output] = max(values) if values else None
                else:
                    out[output] = min(values) if values else None
            return Outcome(rows=[out])
        if limit is not None:
            rows = rows[: int(limit)]
        if star and not items:
            return Outcome(rows=[copy.deepcopy(row) for row in rows])
        if star:
            parser.unknown()
        return Outcome(
            rows=[{output: copy.deepcopy(row[column]) for _, column, output in items}
                  for row in rows]
        )

    def _update(self, parser: _Parser, dry: bool) -> Outcome:
        table = self.data_table(table_name(parser.name()))
        parser.need_word("SET")
        changes: dict[str, object] = {}
        while True:
            column = parser.column()
            if column not in table.columns:
                raise BadRequest(f"stand-in: {table.name} has no column {column}")
            parser.need_punct("=")
            changes[column] = parser.stored(table, column)
            if not parser.punct(","):
                break
        # BigQuery refuses an UPDATE or a DELETE with no WHERE.
        parser.need_word("WHERE")
        keep = parser.condition(table)
        if not parser.done():
            parser.unknown()
        hit = [row for row in table.rows if keep(row)]
        for column in table.required & set(changes):
            if changes[column] is None:
                raise BadRequest(f"stand-in: {table.name}.{column} is required")
        if not dry:
            for row in hit:
                row.update(copy.deepcopy(changes))
            self.changed(table, len(hit))
        return Outcome(affected=len(hit))

    def _delete(self, parser: _Parser, dry: bool) -> Outcome:
        parser.word("FROM")
        table = self.data_table(table_name(parser.name()))
        parser.need_word("WHERE")
        keep = parser.condition(table)
        if not parser.done():
            parser.unknown()
        hit = [row for row in table.rows if keep(row)]
        if not dry:
            table.rows[:] = [row for row in table.rows if not keep(row)]
            self.changed(table, len(hit))
        return Outcome(affected=len(hit))
