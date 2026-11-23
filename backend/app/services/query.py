"""SQL over the data plane: DuckDB reading Parquet, with a read-only guard.

This is the seam Modules A and C consume. A dataset's rows live as a Parquet
object; this module registers each one as a DuckDB view and runs SQL against
them. There is no server to keep alive, no schema to register in a catalogue,
and no per-query bill — DuckDB is a library inside a process that already
exists.

Because a folder can be a conversation's source (decision D6), a query context
may hold several tables at once, and joining across them is just SQL.

## The guard

Module A generates SQL with a language model, and the UI lets the user edit it
before running. Both are untrusted input, so validation is not optional and it
is not a regex. `sqlglot` parses the statement and three rules are enforced on
the AST:

1. **Exactly one statement, and it must read.** Anything that writes, defines,
   attaches, copies, or configures is rejected outright.
2. **No file-reading functions.** `read_parquet('s3://other-tenant/...')` is a
   perfectly ordinary DuckDB expression, and it is exactly how a generated
   query could reach another user's data. The function allowlist is what stops
   it — not the S3 prefix, not the IAM policy.
3. **Every table referenced must be one we registered** (or a CTE defined in
   the same query). A name we didn't put there cannot be read even if it
   exists.

Rule 3 is the load-bearing one: it means correctness of the guard doesn't
depend on having enumerated every dangerous function name, only on knowing
which tables belong to this request.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlglot
from sqlglot import exp

from app.core.config import get_settings
from app.ingest.storage import ObjectStore, dataset_parquet_key, get_object_store

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.etlite import CleaningPlan

logger = logging.getLogger(__name__)

DIALECT = "duckdb"

# backend-deploy.yml downloads httpfs during the CI build (real internet
# there) and ships the binary at this path, sibling to `app/` in the zip —
# the backend Lambda's VPC has no NAT gateway, so `INSTALL httpfs` has no
# route to fetch it at runtime. Local dev never takes the S3 branch in
# `_connect` (ALIBI_OBJECT_STORE defaults to local), so this simply won't
# exist there, and `_connect` falls back to a normal INSTALL.
_BUNDLED_HTTPFS_EXTENSION = Path(__file__).resolve().parents[2] / "duckdb_extensions" / "httpfs.duckdb_extension"

# Statement types that are not reads. Anything not a SELECT/UNION is rejected
# anyway; this list exists to give a precise error message rather than a
# generic one.
_WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Copy,
    exp.Command,  # ATTACH, SET, PRAGMA, INSTALL, LOAD, EXPORT … all land here
    exp.Merge,
    exp.Grant,
)

# What a read-only statement may be at the top level. INTERSECT and EXCEPT are listed beside UNION
# on purpose: in sqlglot they are siblings of Union (not subclasses, before and after the
# SetOperation split in 26), so `isinstance(.., exp.Union)` alone rejected two read-only operators
# that the fine-tuned Module A emits. A write hidden inside an operand is still caught by
# `_WRITE_NODES`, which searches the whole tree.
_READ_NODES = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.With)

# Functions that reach the filesystem or another database. DuckDB exposes a lot
# of these and gains more with every release, so this is a defence-in-depth
# layer behind the table allowlist, not the primary control.
#
# Public because Module A's own guardrail (`text2sql.sql.check`) enforces the
# identical list at *prepare* time, so a bad query is refused with a useful
# message before the user is ever asked to confirm it. Two hand-kept copies
# would drift, and the drift is silent: the prepare-time check would pass
# something the execution-time check then rejects, or — as was the case before
# this was shared — the prepare-time check would pass something nothing else
# ever looked at.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "read_parquet",
        "parquet_scan",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_ndjson_auto",
        "read_text",
        "read_blob",
        "glob",
        "delta_scan",
        "iceberg_scan",
        "postgres_scan",
        "postgres_query",
        "sqlite_scan",
        "mysql_scan",
        "mysql_query",
        "duckdb_settings",
        "sniff_csv",
        "load_aws_credentials",
    }
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class QueryError(ValueError):
    """Rejected before execution. `code` maps onto the contract's error codes."""

    def __init__(self, message: str, code: str = "invalid_sql") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class QueryTable:
    """One dataset exposed to SQL."""

    name: str
    uri: str
    # Confirmed schema: effective name → the column name inside the Parquet.
    # A rename made at confirmation time is applied as a projection here rather
    # than by rewriting the file, so confirming stays cheap and reversible.
    projection: dict[str, str] = field(default_factory=dict)
    # ETLite's cleaning recipe, under the projection. Set only to preview a
    # recipe before confirming and to write the cleaned file — a confirmed
    # dataset's own view never has one, its file already holds clean data.
    cleaning: CleaningPlan | None = None

    def view_sql(self) -> str:
        if self.cleaning is not None and not self.cleaning.is_noop():
            source = f"({self.cleaning.source_sql(self.uri)}) AS __alibi_clean"
        else:
            source = f"read_parquet({_quote_literal(self.uri)})"
        if not self.projection:
            select = "*"
        else:
            select = ", ".join(
                f"{_quote_ident(physical)} AS {_quote_ident(alias)}"
                for alias, physical in self.projection.items()
            )
        return f"CREATE OR REPLACE VIEW {_quote_ident(self.name)} AS SELECT {select} FROM {source}"


@dataclass(slots=True)
class QueryResultRows:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = False


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def function_names(func: exp.Expression) -> set[str]:
    """Every name a function node could be known by, lowercased.

    Necessary because sqlglot represents functions two different ways, and only
    one of them answers to `.name`: unknown functions become `exp.Anonymous`
    (where `.name` is `read_text`), while ones sqlglot models explicitly become
    a typed node — `read_parquet` is `exp.ReadParquet`, whose `.name` is the
    empty string and whose identity lives in `sql_name()`. Checking only
    `.name` silently lets the typed ones straight through, which is exactly the
    hole this closes.
    """
    names: set[str] = set()
    literal = (getattr(func, "name", "") or "").lower()
    if literal:
        names.add(literal)
    sql_name = getattr(func, "sql_name", None)
    if callable(sql_name):
        try:
            names.add(sql_name().lower())
        except Exception:  # noqa: BLE001 - defensive; sql_name is best-effort
            pass
    return names


def validate_sql(sql: str, allowed_tables: set[str]) -> exp.Expression:
    """Parse and check. Returns the AST; raises `QueryError` if not allowed."""
    if not sql or not sql.strip():
        raise QueryError("La consulta está vacía.", "empty_sql")

    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except Exception as exc:  # sqlglot raises several parse error types
        raise QueryError(f"No pudimos interpretar el SQL: {exc}", "sql_parse_error") from exc

    if len(statements) != 1:
        # Blocks the classic "; DROP TABLE" tail as a parse-level fact rather
        # than as a string search.
        raise QueryError("Enviá una sola consulta.", "multiple_statements")

    statement = statements[0]

    for node_type in _WRITE_NODES:
        if isinstance(statement, node_type) or statement.find(node_type) is not None:
            raise QueryError(
                "Esta consulta se bloqueó porque modifica datos. ALIBI es de solo lectura.",
                "write_blocked",
            )

    if not isinstance(statement, _READ_NODES):
        raise QueryError(
            "Esta consulta se bloqueó porque modifica datos. ALIBI es de solo lectura.",
            "write_blocked",
        )

    for func in statement.find_all(exp.Func):
        for candidate in function_names(func):
            if candidate in FORBIDDEN_FUNCTIONS:
                raise QueryError(
                    f"La función “{candidate}” no está permitida.", "forbidden_function"
                )

    # CTE names are legitimate table references defined by the query itself.
    cte_names = {
        (cte.alias_or_name or "").lower() for cte in statement.find_all(exp.CTE)
    }
    allowed_lower = {t.lower() for t in allowed_tables}

    for table in statement.find_all(exp.Table):
        # A table-valued function (`FROM read_parquet(...)`, `FROM glob(...)`)
        # parses as a Table with no name wrapping a Func. Rejecting the whole
        # category is the honest rule: none of them read a registered dataset,
        # so allowing any means allowing a path to somewhere we didn't choose.
        # It costs us `generate_series`-style helpers, which is a fair trade.
        if isinstance(table.this, exp.Func):
            name = next(iter(function_names(table.this)), "function")
            raise QueryError(
                f"La función “{name}” no está permitida como fuente de datos.",
                "forbidden_function",
            )
        name = (table.name or "").lower()
        if not name or name in cte_names or name in allowed_lower:
            continue
        raise QueryError(
            f"La tabla “{table.name}” no está disponible en esta fuente de datos.",
            "unknown_table",
        )

    return statement


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _connect(uris: list[str]):  # noqa: ANN202 - duckdb connection
    import duckdb

    connection = duckdb.connect(database=":memory:")
    if any(u.startswith("s3://") for u in uris):
        settings = get_settings()
        # Lambda sets no $HOME and everything but /tmp is read-only, so
        # DuckDB's default home-directory lookup (where INSTALL stores the
        # extension) comes up empty and INSTALL fails outright before it
        # ever reaches the network. /tmp is the only writable path available.
        connection.execute("SET home_directory='/tmp';")
        if _BUNDLED_HTTPFS_EXTENSION.exists():
            connection.execute(f"LOAD {_quote_literal(str(_BUNDLED_HTTPFS_EXTENSION))};")
        else:
            connection.execute("INSTALL httpfs; LOAD httpfs;")
        # PROVIDER credential_chain would resolve the Lambda execution role
        # itself, but doing so needs DuckDB's separate `aws` extension, which
        # isn't bundled and — same as httpfs above — can't auto-install with
        # no NAT gateway to reach. Resolving the same short-lived credentials
        # through boto3 (already a dependency) and handing them to the S3
        # secret directly gets the identical result — nothing is stored,
        # they're the execution role's own temporary STS credentials — while
        # only depending on the one extension actually bundled.
        import boto3

        frozen = boto3.Session().get_credentials().get_frozen_credentials()
        secret_sql = (
            "CREATE OR REPLACE SECRET alibi_s3 (TYPE S3, "
            f"KEY_ID {_quote_literal(frozen.access_key)}, "
            f"SECRET {_quote_literal(frozen.secret_key)}, "
        )
        if frozen.token:
            secret_sql += f"SESSION_TOKEN {_quote_literal(frozen.token)}, "
        secret_sql += f"REGION {_quote_literal(settings.aws_region)})"
        connection.execute(secret_sql)
    return connection


def run_sql(
    sql: str,
    tables: list[QueryTable],
    *,
    limit: int | None = None,
    validate: bool = True,
) -> QueryResultRows:
    """Validate, register the views, run, and return rows.

    A row limit is always applied. It's enforced by fetching `limit + 1` rows
    and reporting `truncated`, rather than by rewriting the user's SQL: editing
    someone's query to bolt on a LIMIT would change the meaning of anything
    already using one.
    """
    row_limit = limit or get_settings().query_row_limit
    if validate:
        validate_sql(sql, {t.name for t in tables})

    connection = None
    try:
        # Inside the try: a connect-time failure (e.g. the extension load)
        # must become a QueryError like any other execution failure, not an
        # unhandled 500 — the route only translates QueryError into a 409.
        connection = _connect([t.uri for t in tables])
        for table in tables:
            connection.execute(table.view_sql())
        cursor = connection.execute(sql)
        fetched = cursor.fetchmany(row_limit + 1)
        columns = [d[0] for d in (cursor.description or [])]
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f"The query failed: {exc}", "execution_failed") from exc
    finally:
        if connection is not None:
            connection.close()

    truncated = len(fetched) > row_limit
    rows = [dict(zip(columns, _json_safe(row))) for row in fetched[:row_limit]]
    return QueryResultRows(columns=columns, rows=rows, truncated=truncated)


def is_numeric_column(rows: list[dict], key: str) -> bool:
    """Whether a result column should render right-aligned.

    Decided from the values rather than a declared type, so a table whose schema
    isn't confirmed yet still lines up. Booleans are excluded on purpose: they are
    `int` subclasses in Python but read as labels, not quantities.

    Lives here rather than next to a route because both the ingestion preview and
    the chat's result table need the identical rule — two copies would eventually
    disagree and the same column would align differently on two screens.
    """
    values = [row.get(key) for row in rows if row.get(key) is not None]
    return bool(values) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in values
    )


def _json_safe(row: tuple) -> list[Any]:
    """Coerce DuckDB values into things `json.dumps` accepts.

    Dates and Decimals are the two that matter: both are common in ingested
    data and neither is JSON-serialisable, so leaving them would fail at
    response-encoding time — long after the useful stack trace.
    """
    import datetime
    import decimal

    out: list[Any] = []
    for value in row:
        if value is None or isinstance(value, (str, int, float, bool)):
            out.append(value)
        elif isinstance(value, decimal.Decimal):
            out.append(float(value))
        elif isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            out.append(value.isoformat())
        elif isinstance(value, (bytes, bytearray)):
            out.append(value.decode("utf-8", errors="replace"))
        else:
            out.append(str(value))
    return out


# ---------------------------------------------------------------------------
# Building a context from stored metadata
# ---------------------------------------------------------------------------


def table_for_dataset(
    owner_id: str,
    dataset_id: str,
    table_name: str,
    *,
    columns: dict[str, str] | None = None,
    store: ObjectStore | None = None,
) -> QueryTable:
    store = store or get_object_store()
    if not _IDENTIFIER.match(table_name):
        # Table names are generated by `pipeline.slugify`, so a failure here
        # means a bug upstream, not user input — but this string is
        # interpolated into DDL, so it is checked anyway.
        raise QueryError(f"Invalid table name: {table_name!r}", "invalid_table_name")
    return QueryTable(
        name=table_name,
        uri=store.uri_for(dataset_parquet_key(owner_id, dataset_id)),
        projection=columns or {},
    )


def preview(table: QueryTable, limit: int = 20) -> QueryResultRows:
    """First N rows — the confirmation screen and the Biblioteca drawer.

    Not routed through `validate_sql`: this SQL is built here from a checked
    identifier, not supplied by anyone.
    """
    return run_sql(
        f"SELECT * FROM {_quote_ident(table.name)}", [table], limit=limit, validate=False
    )


__all__ = [
    "FORBIDDEN_FUNCTIONS",
    "QueryError",
    "QueryResultRows",
    "QueryTable",
    "function_names",
    "preview",
    "run_sql",
    "table_for_dataset",
    "validate_sql",
]
