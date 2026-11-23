"""Vega-Lite v5: the schema check, the grounding check, and the chart-type read.

This is the part of Module C that does not involve a model. The model proposes a
specification; nothing here trusts it. Three questions get asked of every
candidate, in increasing order of how much the answer costs the user to
discover:

1. **Is it a Vega-Lite specification at all?** Checked against the official
   JSON Schema, vendored in `schemas/vega-lite-v5.schema.json`. The file is not
   downloaded and not hand-written — it is copied verbatim from the
   `vega-lite` npm package the frontend renders with (`schemas/VERSION` records
   which), so the validator and the renderer can never be one minor version
   apart in what they consider legal. A spec that passes here renders there.

   The document is validated with an **empty dataset attached**, because
   Vega-Lite's `TopLevelSpec` requires `data` and the spec we ship deliberately
   has none. That checks the structure the browser will render while keeping the
   rows out of the response — see `_PLACEHOLDER_DATA`.

2. **Does it only name columns that exist?** The schema cannot know this: a
   `field` pointing at a column the query never returned is a perfectly legal
   Vega-Lite document that draws an empty chart. This is the chart's equivalent
   of `validation.check_grounding` — the model may only plot data the user
   actually got back, and inventing an axis is the same class of failure as
   inventing a figure in the narrative.

3. **Is it one of the six charts the product can show?** `ChartType` names
   six, and every renderer, saved-chart record and history icon in the product
   is written against that set. A `boxplot` is legal Vega-Lite with nowhere to
   live here, so an unsupported mark is rejected rather than shown.

Two normalisations happen before validation, and both are deliberate:

* **`data` is stripped.** The frontend injects the real result rows at render
  time. A model that supplies its own `data.values` has invented numbers, and
  those numbers would be drawn over the user's own — the single worst thing this
  system could do. Dropping the key is the safe reading of any spec that has
  one; there is nothing to salvage from it.
* **`$schema` is set, not required.** It carries no information about the chart
  — it names the validator, and we know which validator we ran. Rejecting an
  otherwise-perfect spec over a missing URL string would cost the user a chart
  for nothing, and since there is no fallback, a lost chart is simply no chart.
"""

from __future__ import annotations

import json
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Any

from app.schemas.charts import ChartType

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_SCHEMA_PATH = _SCHEMA_DIR / "vega-lite-v5.schema.json"
_VERSION_PATH = _SCHEMA_DIR / "VERSION"

#: The `$schema` every spec is stamped with. Major version only — that is what
#: the Vega-Lite runtime keys off, and pinning the patch would invalidate specs
#: on a dependency bump that changed nothing about them.
SCHEMA_URL = "https://vega.github.io/schema/vega-lite/v5.json"

#: Stands in for the rows during validation only, and never reaches the wire.
#: Vega-Lite's `TopLevelSpec` *requires* `data`, so a spec with the key stripped
#: fails the schema outright — the structure is fine, the document is simply
#: incomplete. Validating the spec as it will actually be rendered, with an
#: empty dataset attached, checks the thing that matters (the mark, the
#: encodings, the transforms) without either shipping rows twice or accepting a
#: `data` block the model wrote.
_PLACEHOLDER_DATA = {"values": []}

#: How many schema errors to collect before choosing one to report. The
#: Vega-Lite schema is 456 definitions of nested `anyOf`, so enumerating *every*
#: way an invalid document fails it can take seconds. The first handful contain
#: the useful one; the rest are the same failure re-derived down other branches.
_MAX_ERRORS = 20

#: Vega-Lite mark → the `ChartType` the product records. Marks absent from this
#: table are rejected: a chart with no `ChartType` cannot be saved to a
#: dashboard, listed in history, or exported, because all three key off it.
_MARK_TO_CHART: dict[str, ChartType] = {
    "bar": ChartType.bar,        # → histogram when an axis is binned; see below
    "line": ChartType.line,
    "area": ChartType.line,
    "trail": ChartType.line,
    "point": ChartType.scatter,
    "circle": ChartType.scatter,
    "square": ChartType.scatter,
    "arc": ChartType.pie,
    "rect": ChartType.heatmap,
}


def schema_version() -> str:
    """The `vega-lite` package version this schema was taken from."""
    try:
        return _VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover - the file ships with the package
        return "unknown"


@lru_cache(maxsize=1)
def _validator():  # noqa: ANN202 - jsonschema's validator type is not public
    """The compiled Draft-07 validator, built once per process.

    Cached rather than rebuilt: the schema is ~1.4 MB of JSON and Lambda pays
    for this on a cold start only if a chart is actually requested — the import
    of `jsonschema` is deferred for the same reason.
    """
    from jsonschema import Draft7Validator

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft7Validator(schema)


def normalize(spec: dict[str, Any]) -> dict[str, Any]:
    """A copy of `spec` with our `$schema` and no `data`. See the module docstring."""
    cleaned = {k: v for k, v in spec.items() if k != "data"}
    cleaned["$schema"] = SCHEMA_URL
    return cleaned


def _mark_type(spec: dict[str, Any]) -> str | None:
    mark = spec.get("mark")
    if isinstance(mark, str):
        return mark
    if isinstance(mark, dict):
        kind = mark.get("type")
        return kind if isinstance(kind, str) else None
    return None


def _is_binned(spec: dict[str, Any]) -> bool:
    """Whether a positional channel bins its field — what makes a bar a histogram.

    A histogram *is* a bar chart of a binned quantitative axis in Vega-Lite;
    there is no `"histogram"` mark. The distinction matters to the product,
    though, because it is one of the six types the product names.
    """
    encoding = spec.get("encoding")
    if not isinstance(encoding, dict):
        return False
    for channel in ("x", "y"):
        definition = encoding.get(channel)
        if isinstance(definition, dict) and definition.get("bin"):
            return True
    return False


def chart_type_of(spec: dict[str, Any]) -> ChartType | None:
    """The `ChartType` this spec corresponds to, or `None` if none covers it."""
    mark = _mark_type(spec)
    if mark is None:
        return None
    chart = _MARK_TO_CHART.get(mark)
    if chart is ChartType.bar and _is_binned(spec):
        return ChartType.histogram
    return chart


def _field_references(node: Any) -> list[str]:
    """Every data column the spec names, anywhere in it.

    Walked over the whole document rather than just `encoding`, because a
    `transform` (`aggregate`, `bin`, `filter`) can reference a column too, and a
    transform over a column that does not exist fails at render time in the
    browser — where nothing can be done about it.
    """
    found: list[str] = []
    if isinstance(node, dict):
        field = node.get("field")
        if isinstance(field, str):
            found.append(field)
        for key, value in node.items():
            if key != "field":
                found.extend(_field_references(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_field_references(item))
    return found


def _derived_names(node: Any) -> set[str]:
    """Column names the spec creates for itself, via a transform's `as`.

    `{"aggregate": [{"op": "sum", "field": "amount", "as": "total"}]}` makes
    `total` a real column downstream, and an encoding is entitled to reference
    it even though no such column came back from the query. Without this,
    every legitimate `calculate`, `aggregate`, `window` or `fold` would be
    rejected as an invented field — the check would be enforcing "no transforms"
    rather than "no invented columns".

    Order is not enforced (an `as` name counts everywhere in the document, not
    only after the transform that makes it). Getting that wrong is a spec that
    fails to draw, which the model is free to write anyway; getting the
    *invented column* case wrong is a chart of nothing, which is the one worth
    catching.
    """
    names: set[str] = set()
    if isinstance(node, dict):
        alias = node.get("as")
        if isinstance(alias, str):
            names.add(alias)
        elif isinstance(alias, list):
            names.update(a for a in alias if isinstance(a, str))
        for value in node.values():
            names |= _derived_names(value)
    elif isinstance(node, list):
        for item in node:
            names |= _derived_names(item)
    return names


def _untyped_channels(spec: dict[str, Any]) -> list[str]:
    """Encoding channels that name a field without declaring its `type`.

    Legal Vega-Lite — the runtime infers a type — but the inference is done from
    the *values*, so a year column of integers becomes a quantitative axis
    running from 2019.0 to 2024.0 with half-year gridlines. Requiring the type
    is what the Module C training spec asks of the model, and it is cheap to
    check.
    """
    encoding = spec.get("encoding")
    if not isinstance(encoding, dict):
        return []
    missing = []
    for channel, definition in encoding.items():
        if isinstance(definition, dict) and "field" in definition and "type" not in definition:
            missing.append(channel)
    return missing


def check(spec: dict[str, Any], columns: list[str]) -> list[str]:
    """Every reason this spec cannot be shown, phrased for the model's retry.

    An empty list means it renders. The strings are critiques, not log lines —
    they go straight back to the model, so each one says what was wrong *and*
    what to do about it, the same contract `validation.Critique` has for the
    narrative.
    """
    problems: list[str] = []

    encoding = spec.get("encoding")
    if not isinstance(encoding, dict) or not encoding:
        problems.append(
            "The specification has no `encoding` object, so it draws nothing. "
            "Map the result columns onto channels such as `x`, `y` and `color`."
        )

    chart = chart_type_of(spec)
    if chart is None:
        mark = _mark_type(spec)
        named = f"`{mark}`" if mark else "missing"
        problems.append(
            f"The mark is {named}. Use one of: bar (also for a histogram, with "
            "`bin` on one axis), line, area, point, arc or rect — nothing else "
            "can be displayed."
        )

    known = set(columns) | _derived_names(spec)
    for field in dict.fromkeys(_field_references(spec)):
        if field not in known:
            problems.append(
                f"The field “{field}” is not one of the columns in the data. "
                f"Use only these: {', '.join(columns)}."
            )

    for channel in _untyped_channels(spec):
        problems.append(
            f"The `{channel}` channel names a field but does not declare its "
            "`type`. Add one of quantitative, temporal, ordinal or nominal."
        )

    # The schema check runs last: it is by far the most expensive, and a spec
    # that already failed above is going to be regenerated regardless.
    if problems:
        return problems

    renderable = dict(spec, data=_PLACEHOLDER_DATA)
    validator = _validator()
    if validator.is_valid(renderable):
        return []

    from jsonschema.exceptions import best_match

    errors = list(islice(validator.iter_errors(renderable), _MAX_ERRORS))
    worst = best_match(errors)
    where = "/".join(str(p) for p in worst.absolute_path) if worst.absolute_path else "the spec"
    return [
        f"The specification is not valid Vega-Lite v5: at {where}, {worst.message}. "
        "Return a specification that validates against the v5 schema."
    ]


__all__ = ["SCHEMA_URL", "chart_type_of", "check", "normalize", "schema_version"]
