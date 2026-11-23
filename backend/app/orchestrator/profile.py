"""The column profile: how a result table is described to Module C.

The model is not shown the whole table. It is shown, per column, a type and a
cardinality plus a small sample of rows — which is everything a chart choice
actually depends on. Twelve months against one revenue column is a line chart
whether the revenues are pesos or units, and whether there are twelve rows or
twelve thousand.

The same profile travels back to the browser on the response. That is not
duplication for its own sake: switching chart type is a client-side re-encode
(no model call), and a re-encode needs to know which
column is the quantity and which is the label. Deriving it a second time in
TypeScript from the formatted table would be a second opinion about the same
data, and the two would drift.

`ordinal` is deliberately never inferred, although Vega-Lite has it. An ordinal
column is a nominal one whose values happen to have a meaningful order, and
nothing in a result table carries that fact — "Small/Medium/Large" and
"Cordoba/Mendoza" are indistinguishable here. The model may still write
`"type": "ordinal"` in the specification, which is exactly where that judgement
belongs: it can read the labels.
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas.charts import ChartColumn

#: ISO-8601 dates and timestamps. `query._json_safe` renders every DuckDB date,
#: time and timestamp through `.isoformat()`, so this is the shape a temporal
#: column arrives in — there is no `date` object left to type-check by then.
_ISO_TEMPORAL = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
#: A month written as text, `2024-01` — what a query bucketing by month returns
#: when it formats the date (`strftime('%Y-%m', d)`, the SQLite idiom the
#: fine-tuned Module A writes). It is a period, not a category: profiled as
#: nominal, a monthly series reached Module C as twelve labels and came back a
#: bar chart. ISO 8601 allows the form, and the browser's `Date` parses it.
_ISO_MONTH = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


def _values(rows: list[dict[str, Any]], column: str) -> list[Any]:
    return [row.get(column) for row in rows if row.get(column) is not None]


def _dtype(values: list[Any]) -> str:
    """The Vega-Lite type for a column, read from its values.

    From the values rather than a declared schema, for the same reason
    `query.is_numeric_column` does it that way: the result of a query is
    expressions and aliases, not the stored columns, so there is frequently no
    declared type to consult. `SUM(amount) / COUNT(*)` has no entry in any
    schema Module B wrote.

    Booleans are nominal, not quantitative. They are `int` in Python, and
    plotting yes/no on a continuous axis from 0 to 1 is not a chart anyone reads.
    """
    if not values:
        # No non-null value to judge by. Nominal is the safe default: it renders
        # as a labelled axis whatever the underlying data turns out to be,
        # whereas guessing quantitative on an empty column produces an axis with
        # no ticks.
        return "nominal"
    if all(isinstance(v, bool) for v in values):
        return "nominal"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "quantitative"
    if all(isinstance(v, str) and (_ISO_TEMPORAL.match(v) or _ISO_MONTH.match(v)) for v in values):
        return "temporal"
    return "nominal"


def _cardinality(values: list[Any]) -> int:
    """Distinct non-null values, as far as they are hashable.

    Cardinality is what separates a bar chart from a scatter plot and a legend
    from an unreadable smear of 400 colours, so it is the one statistic worth
    computing over the whole result rather than the prompt sample.
    """
    try:
        return len(set(values))
    except TypeError:  # pragma: no cover - DuckDB returns JSON-safe scalars
        return len({str(v) for v in values})


def column_profile(rows: list[dict[str, Any]], columns: list[str]) -> list[ChartColumn]:
    """Type and cardinality for each result column, in the order the query returned them."""
    profile = []
    for name in columns:
        values = _values(rows, name)
        profile.append(
            ChartColumn(
                name=name,
                dtype=_dtype(values),  # type: ignore[arg-type]
                cardinality=_cardinality(values),
            )
        )
    return profile


__all__ = ["column_profile"]
