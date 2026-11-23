"""
Vega-Lite v5 spec validator and fallback template generator for Module C.

Validation pipeline (called by inference.py):
  1. Parse JSON — invalid JSON → fallback template
  2. Strip 'data' field if present (orchestrator injects data at render time)
  3. JSON Schema validation against Vega-Lite v5 schema
  4. Field existence check — every "field" value must exist in column profile
  5. Type completeness check — every encoding channel must have "type"
  6. If any check fails → return fallback template for the predicted chart type

The module NEVER raises to the caller: it always returns a valid spec.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Tuple

import requests

from model_c.chart_classifier import Column

logger = logging.getLogger(__name__)

VEGALITE_V5_SCHEMA_URL = "https://vega.github.io/schema/vega-lite/v5.json"
VEGALITE_V5_SCHEMA_REF = "https://vega.github.io/schema/vega-lite/v5.json"

_schema_cache: Optional[dict] = None


def _load_schema() -> Optional[dict]:
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache

    # Prefer S3-cached copy (set VEGALITE_SCHEMA_LOCAL_PATH to a pre-downloaded file)
    local_path = os.environ.get("VEGALITE_SCHEMA_LOCAL_PATH")
    if local_path and os.path.exists(local_path):
        with open(local_path) as f:
            _schema_cache = json.load(f)
        return _schema_cache

    try:
        resp = requests.get(VEGALITE_V5_SCHEMA_URL, timeout=10)
        resp.raise_for_status()
        _schema_cache = resp.json()
        return _schema_cache
    except Exception as exc:
        logger.warning("[validator] could not load Vega-Lite schema: %s — skipping schema check", exc)
        return None


def validate(
    spec: dict,
    columns: List[Column],
    chart_type: str,
) -> Tuple[bool, Optional[str], dict]:
    """
    Validate spec against Vega-Lite v5 schema and column constraints.

    Returns:
        (valid, error_reason, final_spec)
        final_spec is always a renderable spec — fallback template if validation fails.
    """
    # 1. Strip data field — orchestrator injects data at render time
    spec = {k: v for k, v in spec.items() if k != "data"}

    # 2. JSON Schema check
    schema = _load_schema()
    if schema is not None:
        try:
            import jsonschema
            jsonschema.validate(instance=spec, schema=schema)
        except jsonschema.ValidationError as exc:
            reason = f"schema: {exc.message[:120]}"
            return False, reason, get_fallback_template(chart_type, columns)
        except Exception as exc:
            logger.warning("[validator] jsonschema error: %s", exc)

    # 3. Field existence check
    col_names = {c.name for c in columns}
    bad_fields = _find_unknown_fields(spec, col_names)
    if bad_fields:
        reason = f"unknown fields: {bad_fields}"
        return False, reason, get_fallback_template(chart_type, columns)

    # 4. Type completeness check
    missing_types = _find_missing_types(spec)
    if missing_types:
        reason = f"encoding channels missing 'type': {missing_types}"
        return False, reason, get_fallback_template(chart_type, columns)

    return True, None, spec


def validate_json_string(
    raw: str,
    columns: List[Column],
    chart_type: str,
) -> Tuple[bool, Optional[str], dict]:
    """Parse raw LLM output string, then validate."""
    # Extract JSON object from raw text (LLM often wraps in markdown)
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(l for l in lines if not l.startswith("```"))

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}", get_fallback_template(chart_type, columns)

    if not isinstance(spec, dict):
        return False, "spec must be a JSON object", get_fallback_template(chart_type, columns)

    return validate(spec, columns, chart_type)


def _find_unknown_fields(spec: dict, col_names: set) -> List[str]:
    unknown = []
    encoding = spec.get("encoding", {})
    for channel, enc in encoding.items():
        if isinstance(enc, dict):
            field = enc.get("field")
            if field and field not in col_names:
                unknown.append(field)
    # Also check layer/concat/facet specs recursively
    for key in ("layer", "concat", "hconcat", "vconcat"):
        for sub in spec.get(key, []):
            unknown.extend(_find_unknown_fields(sub, col_names))
    return unknown


def _find_missing_types(spec: dict) -> List[str]:
    missing = []
    encoding = spec.get("encoding", {})
    for channel, enc in encoding.items():
        if isinstance(enc, dict) and enc.get("field") and not enc.get("type"):
            missing.append(channel)
    return missing


# ── Fallback templates ─────────────────────────────────────────────────────────

def get_fallback_template(chart_type: str, columns: List[Column]) -> dict:
    """
    Return a minimal valid Vega-Lite v5 spec for chart_type, filling
    encoding channels from columns in a best-effort way.
    """
    builders = {
        "bar":       _bar_template,
        "line":      _line_template,
        "scatter":   _scatter_template,
        "pie":       _arc_template,
        "histogram": _histogram_template,
        "heatmap":   _heatmap_template,
        "table":     _table_template,
    }
    builder = builders.get(chart_type, _table_template)
    spec = builder(columns)
    spec["$schema"] = VEGALITE_V5_SCHEMA_REF
    return spec


def _pick(columns: List[Column], vl_type: str) -> Optional[Column]:
    return next((c for c in columns if c.vl_type == vl_type), None)


def _pick_any(columns: List[Column], *vl_types: str) -> Optional[Column]:
    for t in vl_types:
        c = _pick(columns, t)
        if c:
            return c
    return columns[0] if columns else None


def _bar_template(columns: List[Column]) -> dict:
    nom   = _pick_any(columns, "nominal", "ordinal") or columns[0]
    quant = _pick(columns, "quantitative") or columns[-1]
    return {
        "mark": "bar",
        "encoding": {
            "x": {"field": nom.name,   "type": nom.vl_type},
            "y": {"field": quant.name, "type": "quantitative", "aggregate": "sum"},
        },
    }


def _line_template(columns: List[Column]) -> dict:
    temporal = _pick_any(columns, "temporal", "ordinal", "quantitative")
    quant    = _pick(columns, "quantitative") or columns[-1]
    return {
        "mark": "line",
        "encoding": {
            "x": {"field": temporal.name, "type": temporal.vl_type},
            "y": {"field": quant.name,    "type": "quantitative"},
        },
    }


def _scatter_template(columns: List[Column]) -> dict:
    quants = [c for c in columns if c.vl_type == "quantitative"]
    x = quants[0] if len(quants) >= 1 else columns[0]
    y = quants[1] if len(quants) >= 2 else columns[-1]
    return {
        "mark": "point",
        "encoding": {
            "x": {"field": x.name, "type": "quantitative"},
            "y": {"field": y.name, "type": "quantitative"},
        },
    }


def _arc_template(columns: List[Column]) -> dict:
    nom   = _pick_any(columns, "nominal", "ordinal") or columns[0]
    quant = _pick(columns, "quantitative") or columns[-1]
    return {
        "mark": "arc",
        "encoding": {
            "theta": {"field": quant.name, "type": "quantitative"},
            "color": {"field": nom.name,   "type": nom.vl_type},
        },
    }


def _histogram_template(columns: List[Column]) -> dict:
    quant = _pick(columns, "quantitative") or columns[0]
    return {
        "mark": "bar",
        "encoding": {
            "x": {"field": quant.name, "type": "quantitative", "bin": True},
            "y": {"aggregate": "count",  "type": "quantitative"},
        },
    }


def _heatmap_template(columns: List[Column]) -> dict:
    noms  = [c for c in columns if c.vl_type in ("nominal", "ordinal")]
    quant = _pick(columns, "quantitative") or columns[-1]
    x = noms[0] if len(noms) >= 1 else columns[0]
    y = noms[1] if len(noms) >= 2 else columns[-1]
    return {
        "mark": "rect",
        "encoding": {
            "x":     {"field": x.name,     "type": x.vl_type},
            "y":     {"field": y.name,     "type": y.vl_type},
            "color": {"field": quant.name, "type": "quantitative", "aggregate": "mean"},
        },
    }


def _table_template(columns: List[Column]) -> dict:
    return {
        "mark": {"type": "text"},
        "encoding": {
            ch: {"field": c.name, "type": c.vl_type}
            for ch, c in zip(
                ("x", "y", "color", "size", "text"),
                columns[:5],
            )
        },
    }
