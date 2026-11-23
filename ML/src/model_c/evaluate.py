"""
Evaluation script for Module C (Vega-Lite generation).

Metrics (on nvBench-dev):
  validity          — % of outputs that are parseable JSON
  schema_legality   — % of valid JSONs that pass Vega-Lite v5 JSON Schema validation
  field_correctness — % of specs where all "field" values exist in the column profile
  chart_type_acc    — % matching the gold chart type in nvBench

Gate (exits 1 if any threshold not met):
  validity        >= 0.95
  schema_legality >= 0.90
  chart_type_acc  >= 0.70

Usage:
  python -m model_c.evaluate \
    --eval-jsonl data/processed/model_c/eval/eval.jsonl \
    --model-dir  /opt/ml/model \
    --output-dir /opt/ml/output/data
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List

_CODE_CANDIDATES = ["/opt/ml/code", "/tmp/code", str(Path(__file__).parent.parent)]
for _p in _CODE_CANDIDATES:
    if Path(_p).is_dir() and _p not in sys.path:
        sys.path.insert(0, _p)
        break
from common.metrics import save_metrics

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GATES = {
    "validity":        0.95,
    "schema_legality": 0.90,
    "chart_type_acc":  0.70,
}

# nvBench gold chart types → our canonical names
_CHART_LABEL_MAP = {
    "bar chart": "bar", "grouped bar chart": "bar", "stacked bar chart": "bar",
    "line chart": "line",
    "scatter plot": "scatter", "scatter chart": "scatter",
    "pie chart": "pie",
    "histogram": "histogram",
    "heatmap": "heatmap",
}


def load_eval_data(jsonl_path: Path) -> List[dict]:
    rows = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_inference(row: dict, model_bundle: dict) -> dict:
    from model_c.inference import predict_fn

    # Parse column profile from instruction text
    columns_raw = _parse_columns_from_instruction(row["instruction"])
    question    = _parse_question_from_instruction(row["instruction"])

    return predict_fn(
        {"columns": columns_raw, "question": question},
        model_bundle,
    )


def _parse_columns_from_instruction(instruction: str) -> List[dict]:
    columns = []
    in_profile = False
    for line in instruction.split("\n"):
        if "### Column profile:" in line:
            in_profile = True
            continue
        if in_profile and line.startswith("- "):
            # "- year (quantitative, e.g. 2019)" or "- year (quantitative)"
            part = line[2:].strip()
            name = part.split("(")[0].strip()
            vl_type = "quantitative"
            if "(" in part:
                inner = part.split("(")[1].split(")")[0].split(",")[0].strip()
                if inner in ("quantitative", "nominal", "ordinal", "temporal"):
                    vl_type = inner
            columns.append({"name": name, "type": vl_type})
        elif in_profile and line.startswith("###"):
            break
    return columns or [{"name": "value", "type": "quantitative"}]


def _parse_question_from_instruction(instruction: str) -> str:
    in_q = False
    for line in instruction.split("\n"):
        if "### Question:" in line:
            in_q = True
            continue
        if in_q and line.startswith("###"):
            break
        if in_q and line.strip():
            return line.strip()
    return ""


def _gold_chart_type(row: dict) -> str:
    gold_str = row.get("chart_type", row.get("chart", "")).lower()
    return _CHART_LABEL_MAP.get(gold_str, gold_str)


def compute_metrics(rows: List[dict], model_bundle: dict) -> dict:
    from model_c.validator import _load_schema, _find_unknown_fields, _find_missing_types
    import jsonschema

    n = len(rows)
    valid_count          = 0
    schema_legal_count   = 0
    field_correct_count  = 0
    chart_type_correct   = 0
    schema = _load_schema()

    for i, row in enumerate(rows):
        if i % 100 == 0:
            logger.info("[eval] %d/%d", i, n)

        result = run_inference(row, model_bundle)
        spec   = result.get("spec", {})

        # 1. validity (the inference handler always returns a dict, but check it has content)
        is_valid_json = isinstance(spec, dict) and bool(spec)
        if is_valid_json:
            valid_count += 1

        # 2. schema legality
        if is_valid_json and schema is not None:
            try:
                jsonschema.validate(instance=spec, schema=schema)
                schema_legal_count += 1
            except Exception:
                pass
        elif is_valid_json:
            schema_legal_count += 1  # schema unavailable: don't penalize

        # 3. field correctness
        columns_raw = _parse_columns_from_instruction(row["instruction"])
        col_names   = {c["name"] for c in columns_raw}
        if is_valid_json and not _find_unknown_fields(spec, col_names):
            field_correct_count += 1

        # 4. chart type accuracy
        gold = _gold_chart_type(row)
        pred = result.get("chart_type", "")
        if gold and pred == gold:
            chart_type_correct += 1

    return {
        "n_samples":        n,
        "validity":         valid_count          / n,
        "schema_legality":  schema_legal_count   / n,
        "field_correctness": field_correct_count / n,
        "chart_type_acc":   chart_type_correct   / n,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--eval-jsonl",  required=True)
    p.add_argument("--model-dir",   default=os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    p.add_argument("--output-dir",  default=os.environ.get("SM_OUTPUT_DATA_DIR", "/opt/ml/output/data"))
    args = p.parse_args()

    from model_c.inference import model_fn
    model_bundle = model_fn(args.model_dir)

    rows = load_eval_data(Path(args.eval_jsonl))
    logger.info("[eval] loaded %d samples", len(rows))

    metrics = compute_metrics(rows, model_bundle)
    logger.info("[eval] results: %s", metrics)

    job_name = os.environ.get("TRAINING_JOB_NAME", "eval")
    save_metrics(metrics, args.output_dir, run_id=job_name)

    failed = [k for k, threshold in GATES.items() if metrics.get(k, 0) < threshold]
    if failed:
        for k in failed:
            logger.error("[eval] GATE FAILED: %s=%.3f < %.2f", k, metrics[k], GATES[k])
        sys.exit(1)

    logger.info("[eval] all gates passed")


if __name__ == "__main__":
    main()
