"""
Rule-based chart-type pre-classifier for Module C.

Maps (column_profile, question) → chart_type without any trained model.
Used as:
  1. Dry-run fallback — generates Vega-Lite templates instead of LLM output.
  2. Validation layer — logs a warning if the LLM output contradicts the rule,
     but does NOT override the LLM (it has richer context from the full prompt).
"""

from dataclasses import dataclass
from typing import List

CHART_TYPES = ("bar", "line", "scatter", "pie", "histogram", "heatmap", "table")

_VL_TYPE_MAP = {
    "quantitative": "quantitative",
    "nominal": "nominal",
    "ordinal": "ordinal",
    "temporal": "temporal",
}


@dataclass
class Column:
    name: str
    vl_type: str  # "quantitative" | "nominal" | "ordinal" | "temporal"


def classify(columns: List[Column], question: str) -> str:
    """Return the most appropriate chart type given column profile and question."""
    q = question.lower()

    n_quant    = sum(1 for c in columns if c.vl_type == "quantitative")
    n_nominal  = sum(1 for c in columns if c.vl_type in ("nominal", "ordinal"))
    n_temporal = sum(1 for c in columns if c.vl_type == "temporal")

    # heatmap: 2 nominal + 1 quantitative
    if n_nominal >= 2 and n_quant >= 1 and any(kw in q for kw in ("heatmap", "matrix")):
        return "heatmap"

    # scatter: 2 quantitative, correlation/vs
    if n_quant >= 2 and any(kw in q for kw in ("correlation", " vs ", "versus", "scatter")):
        return "scatter"

    # line: temporal + quantitative, trend/over-time keywords
    if n_temporal >= 1 and n_quant >= 1 and any(
        kw in q for kw in ("trend", "over time", "evolution", "change", "over year", "over month")
    ):
        return "line"

    # pie: nominal + quantitative, proportion keywords
    if n_nominal >= 1 and n_quant >= 1 and any(
        kw in q for kw in ("proportion", "share", "percentage", "percent", "pie", "breakdown")
    ):
        return "pie"

    # histogram: quantitative only, distribution keywords
    if n_quant >= 1 and n_nominal == 0 and n_temporal == 0 and any(
        kw in q for kw in ("distribution", "frequency", "histogram", "spread")
    ):
        return "histogram"

    # bar: nominal + quantitative, comparison keywords (most common fallback)
    if n_nominal >= 1 and n_quant >= 1 and any(
        kw in q for kw in (
            "compare", " by ", " per ", "each", "group", "category", "rank",
            "top", "most", "least", "count", "total", "sum", "average", "avg", "how many",
        )
    ):
        return "bar"

    # structural defaults when no keyword match
    if n_temporal >= 1 and n_quant >= 1:
        return "line"
    if n_nominal >= 1 and n_quant >= 1:
        return "bar"
    if n_quant >= 2:
        return "scatter"

    return "table"


def infer_vl_type(dtype_str: str, n_unique: int = 999) -> str:
    """Map a pandas dtype string to a Vega-Lite data type."""
    d = dtype_str.lower()
    if "int" in d or "float" in d:
        return "quantitative"
    if "datetime" in d or "date" in d or "period" in d:
        return "temporal"
    # object/string with few unique values → nominal; many → still nominal
    return "nominal"
