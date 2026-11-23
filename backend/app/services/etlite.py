"""ETLite — light cleaning the user chooses when confirming a schema.

Optional, and deliberately small: it never changes a table's *structure* (no
new columns, no joins, no reshaping), only its values and which rows survive.

    per column   collapse whitespace · UPPER/lower · file-specific
                 placeholders → empty · round decimals ·
                 empty cells: keep, drop the row, or fill (0, mean, median,
                 most frequent, a typed value)
    per table    drop exact duplicates, or duplicates by chosen columns
                 (the first occurrence is kept)

**The cleaned table replaces the stored one.** On confirmation the recipe is
run once by DuckDB and the dataset's Parquet is overwritten with the result
(`materialise`). ALIBI never kept the uploaded document — the user has it — so
there is no "original" to preserve, and every reader (the chat, the dashboards,
the preview) simply reads the file, with no cleaning step of its own.

Until then the recipe is only a *preview*: `CleaningPlan.source_sql` is the
same SQL, put under the view (`QueryTable.cleaning`) so the confirmation screen
can show the cleaned rows before anything is written. What the preview shows is
exactly what gets written, because it is the same statement.

Fill values that depend on the data (mean, median, most frequent) are resolved
to literals before the SQL is built, so the screen can show the user the exact
value their empty cells will get.

The SQL here is built by this module from checked inputs only: column names
come from the stored schema (quoted as identifiers), fill values are quoted as
literals and test-cast against the column's real type before they are saved.
Nothing the user types is ever interpolated unquoted.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.schemas.datasets import (
    ColumnCleaning,
    ColumnKind,
    ColumnQuality,
    DuplicateStrategy,
    NullStrategy,
    ResolvedFill,
    SchemaColumn,
    TableCleaning,
    TextCase,
)
from app.services.query import QueryTable, _quote_ident, _quote_literal, run_sql

# Ordering column carried through the cleaned source so "keep the first
# duplicate" means first *in the file*, and the view still reads in file order.
ROW_COLUMN = "__alibi_row"

_SOURCE = "src"

_INTEGER = re.compile(r"^U?(TINY|SMALL|BIG|HUGE)?INT(EGER)?$")
# Anything DuckDB's DESCRIBE emits for the types we write: `BIGINT`,
# `DECIMAL(18,2)`, `TIMESTAMP WITH TIME ZONE`. Checked because it is
# interpolated into a CAST below.
_SQL_TYPE = re.compile(r"^[A-Z][A-Z0-9_ (),]*$")

_FILLS = {NullStrategy.zero, NullStrategy.mean, NullStrategy.median, NullStrategy.mode, NullStrategy.value}

_ALLOWED_NULLS: dict[ColumnKind, set[NullStrategy]] = {
    "integer": set(NullStrategy),
    "number": set(NullStrategy),
    "text": {NullStrategy.keep, NullStrategy.drop_row, NullStrategy.mode, NullStrategy.value},
    "date": {NullStrategy.keep, NullStrategy.drop_row, NullStrategy.mode, NullStrategy.value},
    "boolean": {NullStrategy.keep, NullStrategy.drop_row, NullStrategy.mode, NullStrategy.value},
    "other": {NullStrategy.keep, NullStrategy.drop_row},
}

_STRATEGY_LABEL = {
    NullStrategy.zero: "0",
    NullStrategy.mean: "la media",
    NullStrategy.median: "la mediana",
    NullStrategy.mode: "el valor más frecuente",
    NullStrategy.value: "un valor",
}

_TRUE = {"true", "verdadero", "si", "sí", "1"}
_FALSE = {"false", "falso", "no", "0"}


class CleaningError(ValueError):
    """The recipe cannot apply to this table. `message` is user-facing."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def kind_of(sql_type: str) -> ColumnKind:
    """DuckDB's physical type → the family that decides which operations apply."""
    upper = sql_type.upper().strip()
    if _INTEGER.match(upper):
        return "integer"
    if upper in {"DOUBLE", "FLOAT", "REAL"} or upper.startswith("DECIMAL"):
        return "number"
    if upper == "VARCHAR":
        return "text"
    if upper == "DATE" or upper.startswith("TIMESTAMP"):
        return "date"
    if upper == "BOOLEAN":
        return "boolean"
    return "other"


# ---------------------------------------------------------------------------
# The compiled plan — previewed under the view, then written to the file
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CleaningPlan:
    """A recipe resolved to physical column names, ready to become SQL.

    `columns` is every physical column, in file order — excluded ones too, so
    the rewritten file keeps its shape and a column excluded today can still be
    included later. Only included columns are cleaned or decide what counts as
    a duplicate; an excluded one is carried through untouched.
    """

    columns: list[str]
    cleaning: dict[str, ColumnCleaning] = field(default_factory=dict)
    # None: keep duplicates. Otherwise the columns that identify one.
    duplicate_keys: list[str] | None = None

    def is_noop(self) -> bool:
        return self.duplicate_keys is None and all(c.is_noop() for c in self.cleaning.values())

    def source_sql(self, uri: str) -> str:
        """A SELECT over the Parquet at `uri`, cleaned, in file order."""
        values = ", ".join(
            f"{value_expr(name, self.cleaning.get(name))} AS {_quote_ident(name)}"
            for name in self.columns
        )
        inner = (
            f"SELECT {values}, file_row_number AS {_quote_ident(ROW_COLUMN)} "
            f"FROM read_parquet({_quote_literal(uri)}, file_row_number = true)"
        )
        selected = ", ".join(_quote_ident(name) for name in self.columns)
        sql = f"SELECT {selected} FROM ({inner}) AS __alibi_values"
        # On the cleaned value: a placeholder turned empty drops its row too,
        # which is what the user asked for.
        required = [
            f"{_quote_ident(name)} IS NOT NULL"
            for name, cleaning in self.cleaning.items()
            if cleaning.nulls is NullStrategy.drop_row and name in self.columns
        ]
        if required:
            sql += " WHERE " + " AND ".join(required)
        if self.duplicate_keys:
            keys = ", ".join(_quote_ident(name) for name in self.duplicate_keys)
            # After filling, so two rows that differ only in a now-filled empty
            # cell count as the duplicates they have become.
            sql += (
                f" QUALIFY row_number() OVER (PARTITION BY {keys} "
                f"ORDER BY {_quote_ident(ROW_COLUMN)}) = 1"
            )
        return sql + f" ORDER BY {_quote_ident(ROW_COLUMN)}"


def normalised_expr(name: str, cleaning: ColumnCleaning | None) -> str:
    """The column after the value-level steps, before any fill."""
    expr = _quote_ident(name)
    if cleaning is None:
        return expr
    if cleaning.trim:
        expr = f"regexp_replace(trim({expr}), '\\s+', ' ', 'g')"
    if cleaning.case is TextCase.upper:
        expr = f"upper({expr})"
    elif cleaning.case is TextCase.lower:
        expr = f"lower({expr})"
    tokens = sorted({t.strip().casefold() for t in cleaning.nullTokens if t.strip()})
    if tokens:
        # Compared trimmed and lowercased, so "Desconocido " matches
        # "desconocido" without the user having to also choose trimming.
        listed = ", ".join(_quote_literal(t) for t in tokens)
        expr = f"CASE WHEN lower(trim({expr})) IN ({listed}) THEN NULL ELSE {expr} END"
    if cleaning.decimals is not None:
        expr = f"round({expr}, {int(cleaning.decimals)})"
    return expr


def value_expr(name: str, cleaning: ColumnCleaning | None) -> str:
    expr = normalised_expr(name, cleaning)
    if cleaning is not None and cleaning.nulls in _FILLS and cleaning.fillValue is not None:
        # A string literal: DuckDB casts it to the column's own type inside
        # COALESCE, so a BIGINT column stays BIGINT. `prepare` has already
        # test-cast it, so this cannot fail at query time.
        expr = f"COALESCE({expr}, {_quote_literal(cleaning.fillValue)})"
    return expr


def plan_for(
    columns: list[SchemaColumn],
    physical: list[str],
    table: TableCleaning | None,
) -> CleaningPlan | None:
    """An already validated and resolved recipe as a plan. No I/O.

    None when there is nothing to apply.
    """
    included = [
        (ordinal, column)
        for ordinal, column in enumerate(columns)
        if column.included and ordinal < len(physical)
    ]
    plan = CleaningPlan(columns=list(physical))
    for ordinal, column in included:
        if column.cleaning is not None and not column.cleaning.is_noop():
            plan.cleaning[physical[ordinal]] = column.cleaning
    if table is not None and table.duplicates is DuplicateStrategy.exact:
        plan.duplicate_keys = [physical[ordinal] for ordinal, _ in included] or None
    elif table is not None and table.duplicates is DuplicateStrategy.by_columns:
        keys = [
            physical[ordinal]
            for ordinal in table.duplicateColumns
            if 0 <= ordinal < len(columns) and ordinal < len(physical) and columns[ordinal].included
        ]
        plan.duplicate_keys = keys or None
    return None if plan.is_noop() else plan


# ---------------------------------------------------------------------------
# Validation and resolution — when a recipe is previewed or saved
# ---------------------------------------------------------------------------


def _source(uri: str) -> QueryTable:
    return QueryTable(name=_SOURCE, uri=uri)


def physical_types(uri: str) -> dict[str, str]:
    """Column name → DuckDB type, read from the Parquet itself."""
    result = run_sql(f"DESCRIBE {_quote_ident(_SOURCE)}", [_source(uri)], validate=False, limit=10_000)
    return {str(row["column_name"]): str(row["column_type"]) for row in result.rows}


def _number_text(value: float, kind: ColumnKind) -> str:
    if kind == "integer":
        # Half away from zero, the rounding a person expects of "the mean".
        return str(int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5)))
    text = f"{round(value, 6):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _typed_value(raw: str | None, kind: ColumnKind, column: str) -> str:
    """A value the user typed, in the form DuckDB will cast."""
    text = (raw or "").strip()
    if not text:
        raise CleaningError(f"Escribí el valor con el que rellenar los vacíos de «{column}».")
    if kind in ("integer", "number"):
        number = text.replace(" ", "")
        # es-AR first: "1.234,5" is one thousand two hundred and thirty-four
        # and a half, and so is "1234,5".
        if "," in number and "." in number:
            number = number.replace(".", "").replace(",", ".")
        elif "," in number:
            number = number.replace(",", ".")
        try:
            parsed = float(number)
        except ValueError:
            raise CleaningError(f"«{text}» no es un número válido para «{column}».") from None
        if not math.isfinite(parsed):
            raise CleaningError(f"«{text}» no es un número válido para «{column}».")
        if kind == "integer" and not parsed.is_integer():
            raise CleaningError(f"«{column}» guarda números enteros: «{text}» no lo es.")
        return _number_text(parsed, kind)
    if kind == "boolean":
        folded = text.casefold()
        if folded in _TRUE:
            return "true"
        if folded in _FALSE:
            return "false"
        raise CleaningError(f"«{column}» es de Verdadero/Falso: rellenala con uno de esos dos valores.")
    return text


def _resolve_stats(uri: str, wanted: list[tuple[str, ColumnCleaning]]) -> list[object]:
    """Mean/median/mode for each (physical column, cleaning), in one query."""
    if not wanted:
        return []
    functions = {NullStrategy.mean: "avg", NullStrategy.median: "median", NullStrategy.mode: "mode"}
    selects = ", ".join(
        f"{functions[cleaning.nulls]}({normalised_expr(name, cleaning)}) AS s{index}"
        for index, (name, cleaning) in enumerate(wanted)
    )
    result = run_sql(
        f"SELECT {selects} FROM {_quote_ident(_SOURCE)}", [_source(uri)], validate=False, limit=1
    )
    row = result.rows[0] if result.rows else {}
    return [row.get(f"s{index}") for index in range(len(wanted))]


def _stat_text(value: object, kind: ColumnKind) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and kind in ("integer", "number"):
        return _number_text(float(value), kind)
    return str(value)


def _check_casts(literals: list[tuple[str, str, str]]) -> None:
    """Every (literal, DuckDB type, column label) must cast, or the save is refused."""
    if not literals:
        return
    for _, sql_type, column in literals:
        if not _SQL_TYPE.match(sql_type.upper()):
            raise CleaningError(f"No podemos rellenar «{column}»: su tipo no lo permite.")
    selects = ", ".join(
        f"TRY_CAST({_quote_literal(literal)} AS {sql_type}) IS NOT NULL AS ok{index}"
        for index, (literal, sql_type, _) in enumerate(literals)
    )
    row = run_sql(f"SELECT {selects}", [], validate=False, limit=1).rows[0]
    for index, (literal, _, column) in enumerate(literals):
        if not row.get(f"ok{index}"):
            raise CleaningError(f"«{literal}» no es un valor válido para «{column}».")


@dataclass(slots=True)
class PreparedCleaning:
    """A recipe checked against the real table, fill values resolved."""

    columns: list[SchemaColumn]
    table: TableCleaning | None
    plan: CleaningPlan | None
    fills: list[ResolvedFill] = field(default_factory=list)


def prepare(
    uri: str,
    physical: list[str],
    columns: list[SchemaColumn],
    table: TableCleaning | None,
) -> PreparedCleaning:
    """Validate a recipe against the Parquet at `uri` and resolve its fill values.

    Returns the columns with each `cleaning` normalised (a no-op becomes None,
    excluded columns lose theirs) and `fillValue` set to the literal that will
    be stored. Raises `CleaningError` for anything that cannot apply.
    """
    cleaned_columns = [column.model_copy(deep=True) for column in columns]
    for column in cleaned_columns:
        if column.cleaning is not None and (column.cleaning.is_noop() or not column.included):
            column.cleaning = None
    table = None if table is None or table.is_noop() else table.model_copy(deep=True)

    active = [(ordinal, c) for ordinal, c in enumerate(cleaned_columns) if c.cleaning is not None]
    if not active and table is None:
        return PreparedCleaning(columns=cleaned_columns, table=None, plan=None)

    types = physical_types(uri)
    stats: list[tuple[int, str, ColumnCleaning]] = []
    casts: list[tuple[str, str, str]] = []
    for ordinal, column in active:
        cleaning = column.cleaning
        assert cleaning is not None
        if ordinal >= len(physical) or physical[ordinal] not in types:
            raise CleaningError(f"No encontramos «{column.name}» en la tabla guardada.")
        name = physical[ordinal]
        kind = kind_of(types[name])
        text_ops = cleaning.trim or cleaning.case is not TextCase.keep or any(
            t.strip() for t in cleaning.nullTokens
        )
        if text_ops and kind != "text":
            raise CleaningError(
                f"«{column.name}» no es de texto: no se le pueden quitar espacios, "
                "cambiar mayúsculas ni marcar valores como vacíos."
            )
        if cleaning.decimals is not None and kind != "number":
            raise CleaningError(f"«{column.name}» no tiene decimales que redondear.")
        if cleaning.nulls not in _ALLOWED_NULLS[kind]:
            raise CleaningError(
                f"No se pueden rellenar los vacíos de «{column.name}» con {_STRATEGY_LABEL[cleaning.nulls]}."
            )
        if cleaning.nulls is NullStrategy.zero:
            cleaning.fillValue = "0"
        elif cleaning.nulls is NullStrategy.value:
            cleaning.fillValue = _typed_value(cleaning.fillValue, kind, column.name)
        elif cleaning.nulls in (NullStrategy.mean, NullStrategy.median, NullStrategy.mode):
            stats.append((ordinal, name, cleaning))
        else:
            cleaning.fillValue = None
        if cleaning.nulls in (NullStrategy.zero, NullStrategy.value):
            casts.append((cleaning.fillValue or "", types[name], column.name))

    for (ordinal, name, cleaning), value in zip(
        stats, _resolve_stats(uri, [(name, cleaning) for _, name, cleaning in stats])
    ):
        column = cleaned_columns[ordinal]
        if value is None:
            raise CleaningError(
                f"«{column.name}» no tiene valores para calcular {_STRATEGY_LABEL[cleaning.nulls]}."
            )
        cleaning.fillValue = _stat_text(value, kind_of(types[name]))
        casts.append((cleaning.fillValue, types[name], column.name))
    _check_casts(casts)

    if table is not None and table.duplicates is DuplicateStrategy.by_columns:
        keys = [o for o in table.duplicateColumns if 0 <= o < len(cleaned_columns)]
        if not keys:
            raise CleaningError("Elegí al menos una columna para detectar duplicados.")
        hidden = [cleaned_columns[o].name for o in keys if not cleaned_columns[o].included]
        if hidden:
            raise CleaningError(
                f"«{hidden[0]}» está excluida: no puede decidir qué filas son duplicadas."
            )
        table.duplicateColumns = sorted(set(keys))

    fills = [
        ResolvedFill(ordinal=ordinal, value=column.cleaning.fillValue)
        for ordinal, column in enumerate(cleaned_columns)
        if column.cleaning is not None and column.cleaning.fillValue is not None
    ]
    return PreparedCleaning(
        columns=cleaned_columns,
        table=table,
        plan=plan_for(cleaned_columns, physical, table),
        fills=fills,
    )


def count_rows(table: QueryTable) -> int:
    result = run_sql(f"SELECT count(*) AS n FROM {_quote_ident(table.name)}", [table], validate=False)
    return int(result.rows[0]["n"]) if result.rows else 0


# Columns whose existing values a step rewrites, so their example values in the
# schema are stale afterwards. A fill only adds values; drops only remove rows.
def _rewrites_values(cleaning: ColumnCleaning) -> bool:
    return bool(
        cleaning.trim
        or cleaning.case is not TextCase.keep
        or any(t.strip() for t in cleaning.nullTokens)
        or cleaning.decimals is not None
    )


@dataclass(slots=True)
class Materialised:
    """The cleaned table, written: Parquet bytes plus what the schema must say now."""

    parquet: bytes
    row_count: int
    # Ordinal → fresh example values, for columns whose values were rewritten —
    # Module A is shown these, and "Córdoba" must not be what it sees when the
    # data now says "CÓRDOBA".
    samples: dict[int, str] = field(default_factory=dict)


def materialise(uri: str, physical: list[str], prepared: PreparedCleaning) -> Materialised:
    """Run the plan over the Parquet at `uri` and return the cleaned file.

    DuckDB writes the Parquet itself (`COPY … TO`), so this needs neither pandas
    nor pyarrow — the API Lambda, which runs it, has neither. Written to a
    temporary file (`/tmp` on Lambda, the only writable path) and read back, so
    the caller stores it through the `ObjectStore` like every other object.
    """
    import os
    import tempfile

    assert prepared.plan is not None
    cleaned = QueryTable(name="cleaned", uri=uri, cleaning=prepared.plan)
    handle, path = tempfile.mkstemp(suffix=".parquet")
    os.close(handle)
    try:
        run_sql(
            f"COPY (SELECT * FROM {_quote_ident(cleaned.name)}) TO {_quote_literal(path)} "
            "(FORMAT parquet, COMPRESSION snappy)",
            [cleaned],
            validate=False,
        )
        written = QueryTable(name="written", uri=path)
        rows = count_rows(written)
        samples: dict[int, str] = {}
        for ordinal, column in enumerate(prepared.columns):
            if column.cleaning is None or ordinal >= len(physical):
                continue
            if not _rewrites_values(column.cleaning):
                continue
            name = _quote_ident(physical[ordinal])
            # First three distinct values in file order — the same idea as the
            # ingestion samples, recomputed over what is now stored.
            found = run_sql(
                f"SELECT {name} AS v FROM (SELECT {name}, min(file_row_number) AS r "
                f"FROM read_parquet({_quote_literal(path)}, file_row_number = true) "
                f"WHERE {name} IS NOT NULL GROUP BY {name}) ORDER BY r",
                [],
                validate=False,
                limit=3,
            )
            samples[ordinal] = ", ".join(str(row["v"]) for row in found.rows)
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return Materialised(parquet=data, row_count=rows, samples=samples)


# ---------------------------------------------------------------------------
# What the confirmation screen shows before anything is chosen
# ---------------------------------------------------------------------------


def quality(uri: str, physical: list[str]) -> tuple[int, int, list[ColumnQuality]]:
    """(rows, duplicate rows, per-column findings) for the stored Parquet, raw.

    One aggregate query plus the duplicate count, however many columns there
    are: this runs when the confirmation screen opens, and the user is waiting.
    """
    types = physical_types(uri)
    present = [(ordinal, name) for ordinal, name in enumerate(physical) if name in types]
    selects = ["count(*) AS n"]
    for ordinal, name in present:
        column = _quote_ident(name)
        selects.append(f"count(*) - count({column}) AS n{ordinal}")
        if kind_of(types[name]) == "text":
            selects.append(
                f"count_if({column} <> regexp_replace(trim({column}), '\\s+', ' ', 'g')) AS w{ordinal}"
            )
    source = _quote_ident(_SOURCE)
    selects.append(f"(SELECT count(*) FROM (SELECT DISTINCT * FROM {source})) AS d")
    row = run_sql(f"SELECT {', '.join(selects)} FROM {source}", [_source(uri)], validate=False, limit=1).rows[0]

    rows = int(row["n"] or 0)
    columns = [
        ColumnQuality(
            ordinal=ordinal,
            kind=kind_of(types[name]),
            nullCount=int(row.get(f"n{ordinal}") or 0),
            whitespaceCount=int(row.get(f"w{ordinal}") or 0),
        )
        for ordinal, name in present
    ]
    return rows, rows - int(row["d"] or 0), columns


__all__ = [
    "CleaningError",
    "CleaningPlan",
    "Materialised",
    "PreparedCleaning",
    "count_rows",
    "kind_of",
    "materialise",
    "plan_for",
    "prepare",
    "quality",
]
