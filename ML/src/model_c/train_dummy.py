"""
Dummy training script for Module C (Vega-Lite generation).

Uses the rule-based chart classifier + fallback templates from validator.py
as a stand-in for the real QLoRA model. Validates the full SageMaker pipeline
(IAM, S3, artifact layout, endpoint) without GPU quota or real training.

Interface contract (identical to train_qlora.py):
  Input env vars : SM_CHANNEL_TRAIN, SM_CHANNEL_EVAL, SM_MODEL_DIR
  Output artifacts: model_config.json, metrics.json, metrics.csv
"""

import json
import os
import sys
import time
from pathlib import Path

from pathlib import Path as _Path
_CODE_CANDIDATES = ["/opt/ml/code", "/tmp/code", str(_Path(__file__).parent.parent)]
for _p in _CODE_CANDIDATES:
    if _Path(_p).is_dir() and _p not in sys.path:
        sys.path.insert(0, _p)
        break
from common.metrics import save_metrics


def main():
    model_dir = Path(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    train_dir = Path(os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train"))
    eval_dir  = Path(os.environ.get("SM_CHANNEL_EVAL",  "/opt/ml/input/data/eval"))
    job_name  = os.environ.get("TRAINING_JOB_NAME", "local-dummy-c")

    model_dir.mkdir(parents=True, exist_ok=True)
    print(f"[dummy-c] job={job_name}")

    t0 = time.monotonic()
    # Count eval samples to compute a proxy "accuracy" metric
    eval_samples = list(eval_dir.glob("*.jsonl")) if eval_dir.exists() else []
    n_eval = sum(1 for f in eval_samples for _ in open(f))

    # Dummy metrics: the rule-based classifier achieves ~50% chart-type accuracy
    # on nvBench-dev by design (it is the no-train baseline)
    dummy_validity        = 1.0   # templates are always valid JSON
    dummy_schema_legality = 1.0   # templates pass JSON Schema
    dummy_field_correct   = 1.0   # templates reference actual column names
    dummy_chart_type_acc  = 0.50  # rule-based baseline on nvBench
    elapsed = time.monotonic() - t0

    print(f"[dummy-c] validity={dummy_validity:.2f}  chart_type_acc={dummy_chart_type_acc:.2f}")

    (model_dir / "model_config.json").write_text(json.dumps({
        "model_type": "dummy",
        "phase": 0,
        "description": "Rule-based chart classifier + Vega-Lite template fallback. Pipeline validator.",
    }, indent=2))

    # Copy inference and support modules into code/ for SageMaker script mode
    import shutil
    code_dir = model_dir / "code"
    code_dir.mkdir(exist_ok=True)
    src_root = Path("/opt/ml/code/model_c")
    for fname in ("inference.py", "validator.py", "chart_classifier.py"):
        src = src_root / fname
        if src.exists():
            shutil.copy(src, code_dir / fname)

    save_metrics({
        "phase": 0,
        "model_type": "dummy",
        "n_eval_samples": n_eval,
        "vegalite_validity": dummy_validity,
        "schema_legality": dummy_schema_legality,
        "field_correctness": dummy_field_correct,
        "chart_type_accuracy": dummy_chart_type_acc,
        "training_time_seconds": round(elapsed, 2),
    }, str(model_dir), run_id=job_name)

    print(f"[dummy-c] done. artifacts → {model_dir}")


if __name__ == "__main__":
    main()
