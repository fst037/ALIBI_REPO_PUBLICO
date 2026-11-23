"""
Dummy training script for Module A (Text-to-SQL).

Validates the full pipeline (IAM, S3 I/O, artifact format, endpoint deployment)
using only Python stdlib — no scikit-learn, numpy, or joblib required.
The "model" is a JSON config; inference uses rule-based SQL generation.

Interface contract (identical to train_qlora.py):
  Input env vars: SM_CHANNEL_TRAIN, SM_CHANNEL_EVAL, SM_MODEL_DIR
  Input hyperparams: any key accepted (unknown keys silently ignored)
  Output artifacts: model_config.json, metrics.json, metrics.csv
"""

import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

# Resolve code root: SageMaker (/opt/ml/code), Lambda (/tmp/code), or local (src/)
_CODE_CANDIDATES = ["/opt/ml/code", "/tmp/code", str(Path(__file__).parent.parent)]
for _p in _CODE_CANDIDATES:
    if Path(_p).is_dir() and _p not in sys.path:
        sys.path.insert(0, _p)
        break
from common.metrics import save_metrics


def main():
    model_dir = Path(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    model_dir.mkdir(parents=True, exist_ok=True)
    job_name = os.environ.get("TRAINING_JOB_NAME", "local-dummy")

    print(f"[dummy] job={job_name}")

    t0 = time.monotonic()
    # Simulate trivial "training" — majority-class predictor on synthetic labels
    random.seed(42)
    n_train, n_eval = 500, 100
    y_train = [random.randint(0, 1) for _ in range(n_train)]
    y_eval  = [random.randint(0, 1) for _ in range(n_eval)]
    majority = max(set(y_train), key=y_train.count)
    train_acc = sum(y == majority for y in y_train) / n_train
    eval_acc  = sum(y == majority for y in y_eval)  / n_eval
    elapsed = time.monotonic() - t0

    print(f"[dummy] train_acc={train_acc:.4f}  eval_acc={eval_acc:.4f}  time={elapsed:.3f}s")

    # ── Artifacts ─────────────────────────────────────────────────────────────
    (model_dir / "model_config.json").write_text(json.dumps({
        "model_type": "dummy",
        "phase": 0,
        "description": "Stdlib-only placeholder for pipeline validation (no sklearn required)",
    }, indent=2))

    # Copy inference.py + guardrail.py so the SageMaker endpoint can serve requests.
    # In script mode SageMaker expects inference.py at model_dir/code/inference.py.
    code_dir = model_dir / "code"
    code_dir.mkdir(exist_ok=True)
    for code_root in ["/opt/ml/code", "/tmp/code"]:
        src_inference = Path(code_root) / "model_a" / "inference.py"
        src_guardrail = Path(code_root) / "model_a" / "guardrail.py"
        if src_inference.exists():
            shutil.copy(src_inference, code_dir / "inference.py")
            if src_guardrail.exists():
                shutil.copy(src_guardrail, code_dir / "guardrail.py")
            break

    save_metrics({
        "phase": 0,
        "model_type": "dummy",
        "train_accuracy": round(train_acc, 4),
        "eval_accuracy":  round(eval_acc, 4),
        "training_time_seconds": round(elapsed, 4),
        "spider_execution_accuracy": None,
        "bird_execution_accuracy":   None,
    }, str(model_dir), run_id=job_name)

    print(f"[dummy] done. artifacts → {model_dir}")


if __name__ == "__main__":
    main()
