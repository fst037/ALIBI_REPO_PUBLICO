"""
Pipeline stage 7/8 — test_model.

Wraps evaluate.py's existing Execution-Accuracy logic unchanged (same
SPIDER_EX_TARGET/BIRD_EX_TARGET gate), logs it to MLflow, and adds a guardrail
smoke check that was previously only exercised at serving time: run
guardrail.validate_sql() over a sample of this model's own generated SQL and
log the pass rate. That check is visibility only, not a hard gate — the hard
gate stays the EX thresholds, exactly as evaluate.py already enforces via its
exit code.

Fails closed: if no real eval set is available (no Spider-dev/BIRD-dev
databases on disk), the gate is recorded as NOT passed rather than skipped,
so `publish` correctly refuses to register anything — this is what lets the
$0 dummy-model dry run exercise the same gate-refusal path a real failing
model would hit.
"""

import argparse
import json
import sys
from pathlib import Path

import mlflow

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from model_a.evaluate import evaluate_dataset, load_predict_fn, BIRD_EX_TARGET, SPIDER_EX_TARGET
from model_a.guardrail import validate_sql
from model_a.pipeline._common import configure_mlflow, log_json_artifact, start_stage_run


def _guardrail_smoke_check(predict_fn, samples: list, sample_size: int) -> dict:
    subset = samples[:sample_size]
    if not subset:
        return {"checked": 0, "passed": 0, "pct_passed": None}
    passed = 0
    for s in subset:
        try:
            result = predict_fn({"schema": s["schema"], "question": s["question"]})
            ok, _ = validate_sql(result.get("sql", ""))
            passed += int(ok)
        except Exception:
            pass
    return {"checked": len(subset), "passed": passed, "pct_passed": round(100 * passed / len(subset), 2)}


def main():
    args = parse_args()
    configure_mlflow()

    with start_stage_run(f"test_model-{args.run_id}"):
        mlflow.log_param("model_dir", args.model_dir)

        metrics: dict = {}
        have_eval_data = False

        predict_fn = load_predict_fn(args.model_dir) if args.model_dir else None

        if args.spider_eval_path and args.spider_db_dir and Path(args.spider_eval_path).exists():
            have_eval_data = True
            samples = json.loads(Path(args.spider_eval_path).read_text())
            result = evaluate_dataset(samples, predict_fn, args.spider_db_dir)
            ex = result["execution_accuracy"]
            metrics.update({
                "spider_execution_accuracy": ex,
                "spider_pass": ex >= SPIDER_EX_TARGET,
            })
            if predict_fn and int(args.guardrail_sample_size) > 0:
                gr = _guardrail_smoke_check(predict_fn, samples, int(args.guardrail_sample_size))
                metrics["guardrail_pct_passed_spider"] = gr["pct_passed"]

        if args.bird_eval_path and args.bird_db_dir and Path(args.bird_eval_path).exists():
            have_eval_data = True
            samples = json.loads(Path(args.bird_eval_path).read_text())
            result = evaluate_dataset(samples, predict_fn, args.bird_db_dir)
            ex = result["execution_accuracy"]
            metrics.update({
                "bird_execution_accuracy": ex,
                "bird_pass": ex >= BIRD_EX_TARGET,
            })

        gate_results = [v for k, v in metrics.items() if k.endswith("_pass")]
        gate_passed = have_eval_data and bool(gate_results) and all(gate_results)
        metrics["eval_gate_passed"] = gate_passed

        for k, v in metrics.items():
            if isinstance(v, (int, float)):
                mlflow.log_metric(k, v)
            else:
                mlflow.set_tag(k, str(v))
        log_json_artifact(metrics, "test_metrics.json")

        if not have_eval_data:
            print("[test_model] no eval dataset available — gate recorded as NOT PASSED "
                  "(fail-closed; publish will correctly refuse to register anything)")
        status = "PASS" if gate_passed else "FAIL"
        print(f"[test_model] gate: {status}")
        print(f"GATE_PASSED={str(gate_passed).lower()}")


def parse_args():
    p = argparse.ArgumentParser(description="Test-model stage for Module A")
    p.add_argument("--model-dir", default="")
    p.add_argument("--run-id", default="eval")
    p.add_argument("--spider-eval-path", default="")
    p.add_argument("--spider-db-dir", default="")
    p.add_argument("--bird-eval-path", default="")
    p.add_argument("--bird-db-dir", default="")
    p.add_argument("--guardrail-sample-size", default="50")
    return p.parse_args()


if __name__ == "__main__":
    main()
