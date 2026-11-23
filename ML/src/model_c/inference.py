"""
SageMaker inference handler for Module C (Vega-Lite generation).

Expected request body (application/json):
{
  "columns": [{"name": "year", "type": "quantitative"}, ...],
  "question": "Show revenue by year",
  "chart_hint": "line"   // optional — ignored by dummy, used as hint by QLoRA
}

Response body (application/json):
{
  "spec":       {...},    // Vega-Lite v5 JSON spec (always valid)
  "chart_type": "line",
  "valid":      true,
  "error":      null      // validation error if spec was replaced by fallback
}
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def model_fn(model_dir: str) -> Dict[str, Any]:
    config_path = Path(model_dir) / "model_config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    model_type = config.get("model_type", os.environ.get("MODEL_TYPE", "dummy"))

    logger.info("[inference-c] loading model_type=%r from %s", model_type, model_dir)

    if model_type == "dummy":
        return {"model_type": "dummy", "config": config}

    # QLoRA path: not served from here any more. Module C is a LoRA adapter on the
    # same base model as Module A and is served by model_a/inference.py with
    # {"task": "chart", ...} (prompts shared via common/prompts.py). This handler
    # only implements the rule-based dummy path above.
    raise NotImplementedError(
        "Module C QLoRA is served by model_a/inference.py (task='chart'); "
        "model_c/inference.py only handles model_type='dummy'."
    )


def input_fn(body: str, content_type: str) -> Dict:
    if content_type == "application/json":
        return json.loads(body)
    raise ValueError(f"unsupported content type: {content_type}")


def output_fn(prediction: Dict, accept: str) -> str:
    return json.dumps(prediction)


def _extract_mark(generated: str) -> Optional[str]:
    """Best-effort: parse the 'mark' field from generated JSON."""
    try:
        spec = json.loads(generated.strip())
        mark = spec.get("mark")
        if isinstance(mark, dict):
            mark = mark.get("type")
        return str(mark) if mark else None
    except Exception:
        return None
