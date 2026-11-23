"""Render a dataset's inferred schema as the `CREATE TABLE` DDL the prompt needs.

Module B writes the inferred schema to `dataset_columns` (name + `ColumnType` +
sample values + an `included` flag the user can toggle). Module A has to hand the
model a DDL string. This module is that translation, and it deliberately mimics
the training distribution:

- **One table per statement, semicolon-separated, single line.** 99.6% of the
  fine-tune's prompts were single-table, and ALIBI already enforces one dataset
  per conversation, so in practice this emits exactly one `CREATE TABLE`.
- **No sample rows.** The training schemas never carried example values, so
  adding them (as DAIL-SQL recommends for general Text-to-SQL) would push the
  prompt out of distribution for *this* fine-tune. Revisit per format.
- **Excluded columns are omitted** — the user unchecking a column in the schema
  review screen should make it invisible to the model, not just to the UI.

Type mapping is truthful (a date column is rendered `DATE`) even though the
fine-tune only ever saw `VARCHAR` and `INTEGER`. That's a deliberate bet: date
and numeric filters are central to the questions ALIBI users actually ask, and
telling the model a date is a string invites string comparisons. It is also the
first thing worth A/B-ing once an execution-accuracy harness exists.
"""

import re
from collections.abc import Sequence

from app.schemas.datasets import ColumnType, DatasetSchema, SchemaColumn

# ColumnType is the label enum Module B produces; these are the SQL
# types the model sees. PERCENTAGE is a number with a display convention, not a
# distinct storage type.
_SQL_TYPES: dict[ColumnType, str] = {
    ColumnType.TEXT: "VARCHAR",
    ColumnType.NUMBER: "REAL",
    ColumnType.WHOLE_NUMBER: "INTEGER",
    ColumnType.DATE: "DATE",
    ColumnType.PERCENTAGE: "REAL",
    ColumnType.BOOLEAN: "BOOLEAN",
}

# The SQLite spellings BIRD's schemas use, for `render_multiline`. Measured over
# the fine-tune's 16,874 training schemas: TEXT appears in all of them, INTEGER
# in 8,278, REAL in 5,594, DATE in 4,379 — and VARCHAR in none.
_SQLITE_TYPES: dict[ColumnType, str] = {
    ColumnType.TEXT: "TEXT",
    ColumnType.NUMBER: "REAL",
    ColumnType.WHOLE_NUMBER: "INTEGER",
    ColumnType.DATE: "DATE",
    ColumnType.PERCENTAGE: "REAL",
    ColumnType.BOOLEAN: "BOOLEAN",
}

_NON_IDENT = re.compile(r"[^0-9a-zA-Z_]+")
_LEADING_DIGIT = re.compile(r"^[0-9]")


def sql_identifier(raw: str, fallback: str = "dataset") -> str:
    """Fold an arbitrary label (a file name, a spreadsheet header) into a bare identifier.

    Accents are stripped rather than transliterated (`año` -> `ao` would be wrong,
    so `ñ` maps through NFKD to `n`). The result is always a valid unquoted
    identifier, which keeps the DDL in the shape the model was trained on — the
    training schemas contained no quoted identifiers at all.
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = _NON_IDENT.sub("_", ascii_only).strip("_")
    if not cleaned:
        return fallback
    if _LEADING_DIGIT.match(cleaned):
        cleaned = f"t_{cleaned}"
    return cleaned.lower()


def table_name(dataset_id: str) -> str:
    """The identifier the generated SQL will reference for this dataset.

    Derived from the dataset **id**, not its display name. The name is
    user-editable (`PATCH /datasets/{id}`), so deriving the table from it would
    silently invalidate every saved query and chart the moment someone renames a
    file. The id is stable for the dataset's lifetime.

    The cost — an opaque table name carries no semantics for the model — is close
    to zero here: 71.6% of the current fine-tune's training schemas used
    meaningless names like `table_name_47`, so it is squarely in distribution.
    Column names, which stay semantic, are what the model actually links against.

    The data plane (DuckDB over Parquet in S3 — roadmap front #3) must expose the
    dataset's Parquet file under this same name.
    """
    return sql_identifier(dataset_id)


def fold_names(names: Sequence[str]) -> list[str]:
    """Stored column names → the identifiers SQL actually uses, order preserved.

    **The single source of truth for column naming**, used by the DDL the model
    is shown, by the guardrail's allow-list, *and* by the DuckDB view's aliases
    (`services.datasets.query_table`). Three copies of this rule would drift, and
    the failure is nasty: the guardrail validates the model's SQL against the
    folded names and passes it, then DuckDB rejects the query for naming a column
    the view doesn't expose — a confirmed query failing with an opaque engine
    error rather than anything the user can act on.

    That is not hypothetical. A user renaming a column to `Monto Total` in the
    Ingesta screen — which the UI invites — is stored verbatim as the display
    name, so the view used to alias `"Monto Total"` while the model was told
    `monto_total`.

    Collisions are suffixed rather than dropped: `Monto Total` and `monto_total`
    both fold to the same identifier, and a view cannot expose two columns under
    one name.
    """
    used: dict[str, int] = {}
    folded: list[str] = []
    for index, raw in enumerate(names):
        base = sql_identifier(raw, fallback=f"col_{index + 1}")
        count = used.get(base, 0) + 1
        used[base] = count
        folded.append(base if count == 1 else f"{base}_{count}")
    return folded


def column_ddl(column: SchemaColumn, name: str) -> str:
    sql_type = _SQL_TYPES.get(column.type, "VARCHAR")
    return f"{name} {sql_type}"


def render_one(dataset_id: str, schema: DatasetSchema) -> str:
    """`CREATE TABLE <dataset> (<col> <TYPE>, ...)` — one line, no trailing semicolon."""
    included = [c for c in schema.columns if c.included]
    if not included:
        raise ValueError("dataset schema has no included columns to query")
    names = fold_names([c.name for c in included])
    columns = ", ".join(column_ddl(c, name) for c, name in zip(included, names))
    return f"CREATE TABLE {table_name(dataset_id)} ({columns})"


def render(context: Sequence[tuple[str, DatasetSchema]]) -> str:
    """The schema block for the prompt: one `CREATE TABLE` per dataset.

    A conversation over several files (an explicit selection, or a folder) has to
    show the model *all* of them or it cannot write a JOIN — and if it guessed a
    second table anyway, the guardrail would reject the name.

    **Semicolon-separated on one line, not newline-separated.** `formats.py`
    records that 0 of the fine-tune's 74,649 training schemas contained a
    newline; a multi-line schema block is a shape this model has never seen. The
    separator is therefore part of the prompt format's contract, not a cosmetic
    choice — see `PromptFormat` if a future fine-tune wants a different one.

    Datasets whose schema has no included columns are skipped rather than raising,
    so one unusable file in a folder doesn't take the rest down with it. Raises
    only when that leaves nothing at all.
    """
    blocks: list[str] = []
    for dataset_id, schema in context:
        try:
            blocks.append(render_one(dataset_id, schema))
        except ValueError:
            continue
    if not blocks:
        raise ValueError("no dataset in this conversation has queryable columns")
    return "; ".join(blocks)


def render_multiline(context: Sequence[tuple[str, DatasetSchema]]) -> str:
    """The schema block in the shape the fine-tuned Qwen adapter was trained on.

    Format `qwen-bird-spider-v1` (job model-a-dev-qlora-001) trained on 16,874
    rows: 8,577 Spider, whose schemas are one `CREATE TABLE t (` per table with
    one indented column per line and `);`, tables separated by a blank line; and
    8,297 BIRD, whose schemas are the raw SQLite DDL with the same per-line
    layout and upper-case SQLite types. This is that layout with those types —
    the single-line shape `render` produces is one this model never saw.

    Same table and column naming, same skipping of empty schemas, as `render`.
    """
    blocks: list[str] = []
    for dataset_id, schema in context:
        included = [c for c in schema.columns if c.included]
        if not included:
            continue
        names = fold_names([c.name for c in included])
        columns = ",\n".join(
            f"  {name} {_SQLITE_TYPES.get(c.type, 'TEXT')}" for c, name in zip(included, names)
        )
        blocks.append(f"CREATE TABLE {table_name(dataset_id)} (\n{columns}\n);")
    if not blocks:
        raise ValueError("no dataset in this conversation has queryable columns")
    return "\n\n".join(blocks)


def allowed_names(
    context: Sequence[tuple[str, DatasetSchema]],
) -> tuple[set[str], set[str]]:
    """(tables, columns) the generated SQL is allowed to touch, lowercased.

    Used by the guardrail: a query naming anything outside this set is either a
    hallucination or an attempt to reach beyond the conversation's data, and both
    are refused.

    The column set is the **union** across datasets, not a per-table mapping. That
    is deliberately loose: it accepts `a.col_of_b`, which is wrong SQL but not
    unsafe, because the tables are exactly the ones this conversation may read.
    DuckDB rejects the mismatch at execution anyway, and the alternative —
    qualifying every column — would reject legitimate unqualified references in a
    JOIN, which the model emits constantly.
    """
    tables: set[str] = set()
    columns: set[str] = set()
    for dataset_id, schema in context:
        included = [c for c in schema.columns if c.included]
        if not included:
            continue
        tables.add(table_name(dataset_id))
        columns.update(fold_names([c.name for c in included]))
    return tables, columns
