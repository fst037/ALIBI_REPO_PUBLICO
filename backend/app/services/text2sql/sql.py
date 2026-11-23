"""Validate and normalize model-generated SQL before it is ever shown or run.

Two jobs, one parse:

**1. Read-only guardrail (security).** The keyword regex over the user's question
that `conversations_engine` uses today is not a guardrail — it inspects the wrong
artifact. What has to be proven safe is the SQL the *model* produced, because a
prompt-injection payload inside an uploaded file (P2SQL) reaches the model without
passing through the user's phrasing at all. So: parse to an AST, require exactly
one statement, require it to be a SELECT, reject any table-valued or file-reading
function, and reject anything that reads or writes outside the conversation's own
dataset.

The function rules share `query.FORBIDDEN_FUNCTIONS` with the data plane's own
guard rather than restating it. This module is the *earlier* of the two checks,
not the only one — `query.validate_sql` runs again at execution — but a query
refused here is refused with a message the user sees before they confirm it,
instead of after.

**2. Dialect normalization (correctness).** The fine-tune's targets are not valid
in our execution engine. 83.3% of its 74,649 training answers use double-quoted
string literals (`WHERE director = "joyce bernal"`), which SQLite/MySQL read as
strings but DuckDB and Postgres read as *identifiers* — DuckDB would fail with
`Referenced column "joyce bernal" not found`. The training data also leaves
reserved words unquoted (`SELECT order FROM ...`).

Both are fixed by parsing in the dialect the model writes (`PromptFormat
.sql_dialect_in`, "mysql" for this fine-tune) and re-emitting as DuckDB: sqlglot
converts the literals and quotes the reserved words.

The trade-off in reading as MySQL: a genuinely double-quoted *identifier* would be
misread as a string. That is the right bet for this fine-tune — 83.3% of its
targets use double quotes as literals and none as identifiers — but it is
format-specific, which is why the dialect is a property of the format and not a
constant here.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.services.query import FORBIDDEN_FUNCTIONS, function_names

# The engine the normalized SQL will run against: DuckDB over Parquet in S3
# (the data plane, separate from the app's Postgres).
TARGET_DIALECT = "duckdb"

# Node types that mutate data or schema. Presence of any of these anywhere in the
# tree is fatal, which also covers them nested inside a CTE or subquery.
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Merge,
    exp.Grant,
    exp.Command,  # sqlglot's catch-all for unparsed statements (CALL, EXEC, SET, ...)
)


class SqlRejected(Exception):
    """The generated SQL is unsafe or unusable. `.message` is user-facing copy."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(detail or message)
        self.message = message
        self.detail = detail
        #: What the model wrote, when the rejection came from `text2sql.generate`.
        self.raw_sql: str | None = None


@dataclass(frozen=True)
class CheckedSql:
    """`sql` is normalized to `TARGET_DIALECT`; `raw` is exactly what the model emitted."""

    sql: str
    raw: str
    tables: frozenset[str]
    columns: frozenset[str]


def check(
    raw_sql: str,
    *,
    read_dialect: str,
    allowed_tables: set[str] | None = None,
    allowed_columns: set[str] | None = None,
) -> CheckedSql:
    """Parse, enforce read-only, scope to the dataset, and re-emit as DuckDB.

    `allowed_tables`/`allowed_columns` come from `ddl.allowed_names`. When they are
    None the name check is skipped — used by the evaluation harness, where the SQL
    is scored against a reference database rather than a tenant dataset. Never
    skip them on a request path.
    """
    text = raw_sql.strip()
    if not text:
        raise SqlRejected("El modelo no devolvió una consulta.")

    try:
        statements = sqlglot.parse(text, read=read_dialect)
    except sqlglot.errors.ParseError as err:
        raise SqlRejected(
            "El modelo devolvió una consulta que no se pudo interpretar.",
            detail=str(err),
        ) from err

    statements = [s for s in statements if s is not None]
    if not statements:
        raise SqlRejected("El modelo no devolvió una consulta.")
    if len(statements) > 1:
        # Classic injection shape: "SELECT 1; DROP TABLE users".
        raise SqlRejected(
            "La consulta contiene más de una sentencia. ALIBI solo permite una consulta de lectura."
        )

    tree = statements[0]

    for node_type in _FORBIDDEN:
        if isinstance(tree, node_type) or tuple(tree.find_all(node_type)):
            raise SqlRejected(
                "Esta consulta se bloqueó porque modifica datos. ALIBI es de solo lectura.",
                detail=f"forbidden node {node_type.__name__}",
            )

    # `exp.Query` is the base of Select AND of every set operation (UNION,
    # INTERSECT, EXCEPT) — all read-only. Spelling out `exp.Select` here instead
    # rejected 42 legitimate INTERSECT/EXCEPT queries when this was checked
    # against the fine-tune's real training targets.
    if not isinstance(tree, (exp.Query, exp.Subquery, exp.With)):
        raise SqlRejected(
            "Esta consulta se bloqueó porque no es una consulta de lectura.",
            detail=f"root node {type(tree).__name__}",
        )

    # A table-valued function reaches data we never registered, and it does so
    # *without ever producing a table name* — `FROM read_parquet('s3://…')`
    # parses as an `exp.Table` whose `.name` is empty, wrapping the function.
    # The allow-list below only looks at named tables, so before this check
    # existed such a query passed Module A's guardrail untouched, and
    # `conversations.execute` then ran it with `validate=False`. Nothing in the
    # request path ever inspected it. That is a cross-tenant read, and it is
    # reachable without the user typing anything unusual: a prompt-injection
    # payload inside an uploaded file (P2SQL) reaches the model directly.
    #
    # Rejecting the whole category — rather than blocklisting names — is the
    # same rule `query.validate_sql` applies, and for the same reason: none of
    # these read a registered dataset, so allowing any means allowing a path to
    # somewhere we did not choose.
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func):
            name = next(iter(function_names(table.this)), "function")
            raise SqlRejected(
                "La consulta intenta leer desde fuera de este conjunto de datos.",
                detail=f"table-valued function as a data source: {name}",
            )

    # Defence in depth behind the table rule above: the same file-reaching
    # functions, wherever they appear in the tree (a subquery's SELECT list, a
    # WHERE clause), not just as a FROM source.
    for func in tree.find_all(exp.Func):
        for candidate in function_names(func):
            if candidate in FORBIDDEN_FUNCTIONS:
                raise SqlRejected(
                    "La consulta intenta leer desde fuera de este conjunto de datos.",
                    detail=f"forbidden function: {candidate}",
                )

    # CTE names are locally defined, not real tables — don't hold them to the
    # dataset's table list.
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}

    if read_dialect == "sqlite":
        tree = _sqlite_semantics(
            tree,
            text,
            known=None
            if allowed_columns is None
            else (
                {c.lower() for c in allowed_columns}
                | {t.lower() for t in allowed_tables or ()}
                | cte_names
                | {a.alias_or_name.lower() for a in tree.find_all(exp.Alias) if a.alias_or_name}
            ),
        )

    tables = frozenset(
        t.name.lower() for t in tree.find_all(exp.Table) if t.name
    )
    columns = frozenset(
        c.name.lower() for c in tree.find_all(exp.Column) if c.name and c.name != "*"
    )

    if allowed_tables is not None:
        allowed = {t.lower() for t in allowed_tables} | cte_names
        unknown = sorted(tables - allowed)
        if unknown:
            raise SqlRejected(
                "La consulta referencia tablas que no pertenecen a este conjunto de datos.",
                detail=f"unknown tables: {unknown}",
            )

    if allowed_columns is not None:
        allowed_cols = {c.lower() for c in allowed_columns}
        # Aliases introduced by the query itself are legitimate column references.
        aliases = {
            a.alias_or_name.lower() for a in tree.find_all(exp.Alias) if a.alias_or_name
        }
        unknown_cols = sorted(columns - allowed_cols - aliases)
        if unknown_cols:
            raise SqlRejected(
                "La consulta referencia columnas que no existen en este conjunto de datos.",
                detail=f"unknown columns: {unknown_cols}",
            )

    normalized = tree.sql(dialect=TARGET_DIALECT, pretty=False)
    return CheckedSql(sql=normalized, raw=text, tables=tables, columns=columns)


def _name_columns_as_sqlite(tree: exp.Expression) -> None:
    """Give each unnamed computed column the name SQLite would have given it.

    SQLite names an unaliased expression column by its text — `COUNT(*)`,
    `SUM(revenue)`; DuckDB by its own rendering — `count_star()`, or a fully
    parenthesised CAST. Those names reach the table header, the narrative and,
    above all, Module C's adapter, which was trained on nvBench results executed
    in SQLite: `COUNT(*)` is a name it has seen thousands of times and
    `count_star()` one it never has. Written as an explicit alias, so the name is
    the same whichever engine runs the query. Done first, on the model's own
    expression, before the rewrites below change its text.
    """
    select = tree
    while isinstance(select, exp.SetOperation):
        select = select.this  # a UNION's columns are named by its first SELECT
    if isinstance(select, exp.Subquery):
        select = select.this
    if not isinstance(select, exp.Select):
        return
    taken = {e.alias_or_name.lower() for e in select.expressions if isinstance(e, (exp.Alias, exp.Column))}
    named: list[exp.Expression] = []
    for projection in select.expressions:
        if isinstance(projection, (exp.Alias, exp.Column, exp.Star)) or projection.find(exp.Star) is projection:
            named.append(projection)
            continue
        # Without the join's table aliases: `SUM(T1.revenue)` would otherwise
        # become the axis title "Total of t1.revenue".
        unqualified = projection.copy()
        for column in unqualified.find_all(exp.Column):
            column.set("table", None)
        name = unqualified.sql(dialect="sqlite")
        base, n = name, 1
        while name.lower() in taken:
            n += 1
            name = f"{base}_{n}"
        taken.add(name.lower())
        named.append(exp.alias_(projection, exp.to_identifier(name, quoted=True)))
    select.set("expressions", named)


def _is_inner(join: exp.Join) -> bool:
    return not join.side and not join.method and (join.kind or "INNER").upper() in ("INNER", "CROSS", "")


def _sqlite_inner_joins(select: exp.Select) -> None:
    """SQLite's reading of an inner join with no ON (see `_sqlite_semantics`).

    Only when some inner join lacks a condition, so a well-formed query is
    re-emitted untouched. When every join is inner, each ON moves into WHERE and
    the joins become cross joins — exactly SQLite's meaning, and DuckDB plans the
    equalities as hash joins all the same. Next to an outer join, whose ON cannot
    move, only the condition-less joins become CROSS JOIN.
    """
    joins: list[exp.Join] = select.args.get("joins") or []
    if not any(
        (j.kind or "").upper() == "INNER" and not j.args.get("on") and not j.args.get("using")
        for j in joins
    ):
        return
    movable = all(_is_inner(j) and not j.args.get("using") for j in joins)
    conditions: list[exp.Expression] = []
    for join in joins:
        if not _is_inner(join) or join.args.get("using"):
            continue
        on = join.args.get("on")
        if on is not None and not movable:
            continue
        if on is not None:
            conditions.append(on.copy())
            join.set("on", None)
        join.set("kind", "CROSS")
    if conditions:
        where = select.args.get("where")
        if where is not None:
            conditions.insert(0, where.this.copy())
        select.where(exp.and_(*conditions), append=False, copy=False)


#: Operators and functions SQLite applies to the *text* of their first argument.
_TEXT_OPERATORS = (
    exp.Like,
    exp.ILike,
    exp.Glob,
    exp.Substring,
    exp.StrPosition,
    exp.Length,
    exp.Upper,
    exp.Lower,
    exp.Trim,
)


def _sqlite_semantics(tree: exp.Expression, text: str, *, known: set[str] | None) -> exp.Expression:
    """Make a query written for SQLite mean in DuckDB what it meant in SQLite.

    The fine-tuned Module A (format `qwen-bird-spider-v1`) learned from BIRD and
    Spider, whose answers are SQLite and whose accuracy was measured by running
    them in SQLite. sqlglot translates the syntax; the meanings it leaves
    behind are restored here, each one SQLite's own rule:

    * **An unresolved double-quoted name is a string.** SQLite reads `"France"`
      as an identifier and, when no column has that name, falls back to the
      string 'France'. A third of Spider's answers lean on that
      (`WHERE country = "France"`), and DuckDB would report a missing column.
      Only double quotes get the fallback — `` `x` `` and `[x]` are always
      identifiers in SQLite too — and only names that match nothing the query
      can see (`known`: the dataset's columns and tables, plus the query's own
      aliases and CTEs). Skipped when `known` is None (no allow-list to decide by).
    * **REAL is 8 bytes.** `CAST(x AS REAL)` is a double in SQLite and a 4-byte
      float in DuckDB, which turns BIRD's `CAST(... AS REAL) * 100 / COUNT(*)`
      percentages into 33.333332.
    * **JULIANDAY** does not exist in DuckDB; `julian(timestamp)` is the same day
      number.
    * **Text operators see text.** LIKE, GLOB and the string functions read
      their operand as a string, which is how SQLite — where a date *is* a
      string — filters dates by `LIKE '2023%'` or `SUBSTR(d, 1, 4)`.
    * **LIKE ignores case**, as SQLite's does for ASCII.
    * **An inner join may have no ON.** SQLite reads `JOIN t AS T3` with no
      condition as a cross join and treats every inner join's ON as one more
      WHERE term — so an ON may even name a table joined after it. The adapter
      writes both (`… ON T1.medico_id = T3.medico_id INNER JOIN c AS T3 WHERE …`,
      seen live 2026-10-13/16, a parser error in DuckDB on every retry).

    Integer division is deliberately *not* restored: SQLite's `7 / 2 = 3` is the
    kind of truncation a business user reads as a wrong number, and DuckDB's 3.5
    is the honest answer to the question they asked.
    """
    _name_columns_as_sqlite(tree)
    for select in list(tree.find_all(exp.Select)):
        _sqlite_inner_joins(select)

    if known is not None:
        from sqlglot.tokens import TokenType

        double_quoted = {
            token.text.lower()
            for token in sqlglot.Dialect.get_or_raise("sqlite").tokenize(text)
            if token.token_type == TokenType.IDENTIFIER and text[token.start : token.start + 1] == '"'
        }
        if double_quoted:
            for column in list(tree.find_all(exp.Column)):
                ident = column.this
                name = column.name
                if (
                    isinstance(ident, exp.Identifier)
                    and ident.quoted
                    and not column.table
                    and name.lower() in double_quoted
                    and name.lower() not in known
                ):
                    column.replace(exp.Literal.string(name))

    # Text operators read their operand as text. SQLite stores a date as a
    # string, so `order_date LIKE '2023%'` and `SUBSTR(order_date, 1, 4)` are how
    # BIRD's answers filter by year — and DuckDB has no LIKE or SUBSTRING over a
    # DATE (measured: 2 of 16 live questions failed on exactly this). Casting
    # the operand restores SQLite's reading; on a text column it is a no-op.
    for node in list(tree.find_all(*_TEXT_OPERATORS)):
        operand = node.this
        if isinstance(operand, exp.Expression) and not (
            isinstance(operand, exp.Literal) and operand.is_string
        ) and not (isinstance(operand, exp.Cast) and operand.to.is_type(exp.DataType.Type.VARCHAR, exp.DataType.Type.TEXT)):
            operand.replace(exp.cast(operand.copy(), "VARCHAR"))

    # SQLite's LIKE ignores ASCII case ('north' matches 'North'); DuckDB's does
    # not. ILIKE is the same match.
    for like in list(tree.find_all(exp.Like)):
        like.replace(exp.ILike(this=like.this, expression=like.expression))

    for data_type in list(tree.find_all(exp.DataType)):
        if data_type.this == exp.DataType.Type.FLOAT:
            data_type.replace(exp.DataType.build("DOUBLE"))

    for func in list(tree.find_all(exp.Anonymous)):
        if func.name.lower() == "julianday" and len(func.expressions) == 1:
            func.replace(
                exp.Anonymous(this="julian", expressions=[exp.cast(func.expressions[0], "TIMESTAMP")])
            )
    return tree
