"""Rendering values and column names for a human reader.

This was `templates.py`, which also held a full deterministic narrative — a
shape-aware sentence per `insight_type`, used whenever no model was configured
or every model attempt failed. That fallback is gone. ALIBI now answers with a
real model or reports that it could not: a canned sentence beneath a real
results table is indistinguishable from a real answer, and the whole point of
the grounding validator is that the user should never have to wonder which one
they are reading.

What survives is the part that was never a fallback — turning a stored value or
a column name into something a person reads. Used to build the prompt (the model
is shown business labels, never raw identifiers) and by the results table.
"""

from __future__ import annotations

import re
from typing import Any

# `query._json_safe` renders dates as ISO strings; showing the user a bare
# `2024-03-01` is fine, but the trailing `T00:00:00` on a midnight timestamp is
# noise.
_MIDNIGHT = re.compile(r"T00:00:00(\.0+)?$")


def format_number(value: float | int, locale: str = "en-US") -> str:
    """A quantity as a reader of `locale` expects it.

    en-US only today, which is the whole supported set. Answers are written in
    Spanish but keep this number format: the
    results table and the charts beside them show `1,234.56`, and the grounding
    check in `validation.py` parses exactly that. This function is the one place
    a per-tenant locale would land, which is why it takes the parameter it
    currently ignores rather than hard-coding the format at each call site.

    Integers keep no decimal point, and fractions are shown to at most two
    places — a total of `1,234.5678` is precision the sentence cannot use.
    """
    if isinstance(value, bool):
        return str(value)
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def humanize(name: str, labels: dict[str, str] | None = None) -> str:
    """A column name as a phrase, preferring the business label when one exists.

    `labels` comes from Module B's semantic enrichment, which is where a real
    name lives (`fch_vcto` → "Due date"). Without it, the fallback is mechanical:
    split on underscores and sentence-case. That turns `total_revenue` into
    "Total revenue", which is honest, and leaves `fch_vcto` as "Fch vcto", which
    is at least not `fch_vcto`.
    """
    if labels and name in labels and labels[name].strip():
        return labels[name].strip()
    words = re.sub(r"[_\-]+", " ", name).strip()
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", words)
    return words[:1].upper() + words[1:] if words else name


def display(value: Any, locale: str = "en-US") -> str:
    # The words are Spanish because the model writes its answer in Spanish and
    # tends to copy them verbatim from the data it is shown.
    if value is None:
        return "sin dato"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, (int, float)):
        return format_number(value, locale)
    text = str(value)
    return _MIDNIGHT.sub("", text)


__all__ = ["display", "format_number", "humanize"]
