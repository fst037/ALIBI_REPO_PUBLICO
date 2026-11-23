"""Typing + profiling: a shaped grid becomes a typed frame and its statistics.

Two jobs, done together because they need the same pass over the data:

* **Coercion** — apply each column's inferred typing to every cell, producing
  the pandas frame the Parquet writer serialises.
* **Profiling** — cardinality, null rate, min/max/mean, sample values, and
  primary-key candidates.

The profile is not incidental. It is the *only* thing the semantic-inference
model ever sees: a few hundred bytes of structure per table instead of the
table itself. That is what keeps the model step cheap, bounded, and free of
the user's actual data.

Key-candidate discovery is the deterministic part of what the ML spec calls
Step 2. Full TANE functional-dependency discovery is deferred — it earns its
cost across *related* tables, and v1 profiles one table at a time — so this
implements the single-column part: unique, non-null, and not a float (nobody
keys on a price).
"""

from __future__ import annotations

from app.ingest.models import ColumnProfile, ShapedTable, TypedTable
from app.ingest.typing_rules import ColumnTyping, infer_column_typing, is_null
from app.schemas.datasets import ColumnType, InferenceConfidence

MAX_SAMPLES = 5
# Cap the values inspected when inferring a column's type. A 200k-row file
# doesn't type any more accurately than its first few thousand rows, and the
# typing pass is otherwise the slowest part of ingestion.
TYPE_SAMPLE_ROWS = 5_000


def _column_values(shaped: ShapedTable, index: int) -> list[str | None]:
    return [row[index] if index < len(row) else None for row in shaped.rows]


def _format_sample(value: object, column_type: ColumnType) -> str:
    """A sample value as a person reading en-US would write it.

    These strings are shown verbatim in the confirmation screen's "Examples"
    column, so `str(980.0)` — which is what Python gives for a coerced float —
    is the wrong answer: the trailing `.0` is noise.

    The locale has to be the frontend's. `lib/format.ts` renders the preview
    table right beside this column, and it formats en-US; emitting es-AR here
    would put `1.234,56` in "Examples" and `1,234.56` one row below it, for the
    same underlying number. Note this is display only — the *parsing* side
    (`typing_rules`) still reads es-AR input, because an uploaded file may well
    write `1.234,56` and that has to keep working.
    """
    if value is None:
        return ""
    if column_type in (ColumnType.NUMBER, ColumnType.PERCENTAGE) and isinstance(value, (int, float)):
        return f"{float(value):,.2f}"
    if column_type is ColumnType.WHOLE_NUMBER and isinstance(value, int):
        return f"{value:,}"
    if column_type is ColumnType.BOOLEAN and isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _build_profile(
    name: str,
    ordinal: int,
    raw_values: list[str | None],
    typed_values: list[object],
    typing: ColumnTyping,
) -> ColumnProfile:
    n_rows = len(raw_values)
    present = [v for v in typed_values if v is not None]
    n_null = n_rows - len(present)

    # Distinct count over the *typed* values, so "1.234,56" and "1234,56"
    # count once rather than twice.
    try:
        n_unique = len(set(present))
    except TypeError:  # unhashable — shouldn't happen, but never fail profiling
        n_unique = 0

    samples = [_format_sample(v, typing.column_type) for v in present[:MAX_SAMPLES]]

    min_value = max_value = None
    mean_value = None
    if present and typing.column_type in (
        ColumnType.NUMBER,
        ColumnType.WHOLE_NUMBER,
        ColumnType.PERCENTAGE,
        ColumnType.DATE,
    ):
        try:
            ordered = sorted(present)  # type: ignore[type-var]
            min_value, max_value = str(ordered[0]), str(ordered[-1])
        except TypeError:
            pass
        if typing.column_type is not ColumnType.DATE:
            numeric = [float(v) for v in present if isinstance(v, (int, float))]
            if numeric:
                mean_value = sum(numeric) / len(numeric)

    # A key must identify every row: unique, complete, and not a measurement.
    is_pk = (
        n_rows > 0
        and n_null == 0
        and n_unique == n_rows
        and typing.column_type in (ColumnType.WHOLE_NUMBER, ColumnType.TEXT)
    )

    return ColumnProfile(
        name=name,
        source_name=name,
        ordinal=ordinal,
        column_type=typing.column_type,
        sql_type=typing.sql_type,
        vl_type=typing.vl_type,
        confidence=typing.confidence,
        n_rows=n_rows,
        n_unique=n_unique,
        n_null=n_null,
        samples=samples,
        min_value=min_value,
        max_value=max_value,
        mean_value=mean_value,
        is_primary_key_candidate=is_pk,
    )


def _to_frame(columns: dict[str, list[object]], typings: dict[str, ColumnTyping]):  # noqa: ANN202
    import pandas as pd

    frame = pd.DataFrame()
    for name, values in columns.items():
        typing = typings[name]
        if typing.column_type is ColumnType.WHOLE_NUMBER:
            # Nullable Int64, not int64: a column with one blank cell must not
            # silently become a float and start printing 1500.0 to the user.
            series = pd.array(values, dtype="Int64")
        elif typing.column_type in (ColumnType.NUMBER, ColumnType.PERCENTAGE):
            series = pd.array(values, dtype="Float64")
        elif typing.column_type is ColumnType.BOOLEAN:
            series = pd.array(values, dtype="boolean")
        elif typing.column_type is ColumnType.DATE:
            # Kept as `date` objects, not datetime64. The inferred type is
            # "Fecha", so Parquet should hold a DATE — otherwise every value
            # round-trips as `2024-02-01T00:00:00` and the user sees a
            # timestamp they never wrote. `coerce` already produced real dates.
            series = pd.array(values, dtype="object")
        else:
            series = pd.array([None if v is None else str(v) for v in values], dtype="string")
        frame[name] = series
    return frame


def profile_table(shaped: ShapedTable) -> TypedTable:
    """Infer types, coerce values, and compute the profile for one table."""
    typings: dict[str, ColumnTyping] = {}
    coerced: dict[str, list[object]] = {}
    profiles: list[ColumnProfile] = []

    for ordinal, name in enumerate(shaped.header):
        raw_values = _column_values(shaped, ordinal)
        typing = infer_column_typing(raw_values[:TYPE_SAMPLE_ROWS])
        typed_values = [typing.coerce(v) for v in raw_values]

        # A column typed from a sample can be contradicted by the rest of the
        # file. If coercion failed for a meaningful share of non-null values,
        # the type was wrong for this data — fall back to text, which never
        # loses information, and say so via the confidence.
        non_null_raw = sum(1 for v in raw_values if not is_null(v))
        failures = sum(
            1 for raw, typed in zip(raw_values, typed_values) if not is_null(raw) and typed is None
        )
        if non_null_raw and failures / non_null_raw > 0.05 and typing.column_type is not ColumnType.TEXT:
            typing = ColumnTyping(
                ColumnType.TEXT,
                "TEXT",
                typing.vl_type,
                InferenceConfidence.LOW,
            )
            typed_values = [typing.coerce(v) for v in raw_values]

        typings[name] = typing
        coerced[name] = typed_values
        profiles.append(_build_profile(name, ordinal, raw_values, typed_values, typing))

    frame = _to_frame(coerced, typings)
    return TypedTable(
        frame=frame,
        columns=profiles,
        origin=shaped.origin,
        notes=list(shaped.notes),
        header_confidence=shaped.header_confidence,
    )


def profile_prompt(table: TypedTable, table_name: str) -> str:
    """The structured profile handed to the semantic-inference model.

    Deliberately compact and deliberately data-free: names, types, counts and
    at most three sample values per column.
    """
    lines = [f"### Table: {table_name}", f"### Rows: {table.row_count}", "### Columns:"]
    lines.extend(c.as_prompt_line() for c in table.columns)
    return "\n".join(lines)


__all__ = ["MAX_SAMPLES", "profile_prompt", "profile_table"]
