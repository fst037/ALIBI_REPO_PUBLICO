"""
Evaluation script for Module A — Text-to-SQL.

Computes Execution Accuracy (EX) on Spider-dev and BIRD-dev.
Targets: Spider EX ≥ 70%, BIRD EX ≥ 45% 

Run as a SageMaker Batch Transform job for benchmark evaluation, or locally
against a downloaded artifact. See ml/README.md §6 for Batch Transform setup.

Output: metrics.json + metrics.csv with EX results and pass/fail gates.
Exit code 1 if gate fails (prevents deployment in CI/CD pipeline).
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from common.metrics import save_metrics

SPIDER_EX_TARGET = 0.70
BIRD_EX_TARGET = 0.45


def execute_sql(db_path: str, sql: str):
    """Execute SQL on SQLite. Returns frozenset of row tuples, or None on error."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(sql)
        rows = frozenset(map(tuple, cursor.fetchall()))
        conn.close()
        return rows
    except Exception:
        return None


def execution_accuracy(pred_sql: str, gold_sql: str, db_path: str) -> bool:
    """EX: True iff pred and gold return identical result sets (order-insensitive)."""
    pred = execute_sql(db_path, pred_sql)
    gold = execute_sql(db_path, gold_sql)
    return pred is not None and gold is not None and pred == gold


def evaluate_dataset(samples: list, predict_fn, db_dir: str) -> dict:
    correct = total = errors = 0
    for sample in samples:
        db_id = sample["db_id"]
        db_path = str(Path(db_dir) / db_id / f"{db_id}.sqlite")
        try:
            result = predict_fn({"schema": sample["schema"], "question": sample["question"]})
            pred_sql = result.get("sql", "")
            if execution_accuracy(pred_sql, sample["sql"], db_path):
                correct += 1
        except Exception as exc:
            errors += 1
            print(f"[eval] error on db={db_id}: {exc}")
        total += 1

    ex = correct / total if total > 0 else 0.0
    return {"execution_accuracy": round(ex, 4), "correct": correct, "total": total, "errors": errors}


def load_predict_fn(model_dir: str):
    sys.path.insert(0, str(Path(model_dir).parent.parent / "src"))
    sys.path.insert(0, str(Path(__file__).parent))
    from inference import model_fn, predict_fn
    bundle = model_fn(model_dir)
    return lambda x: predict_fn(x, bundle)


def main():
    args = parse_args()
    predict_fn = load_predict_fn(args.model_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics: dict = {}

    if args.spider_eval_path and args.spider_db_dir:
        print("[eval] Spider-dev ...")
        samples = json.loads(Path(args.spider_eval_path).read_text())
        result = evaluate_dataset(samples, predict_fn, args.spider_db_dir)
        ex = result["execution_accuracy"]
        metrics.update({
            "spider_execution_accuracy": ex,
            "spider_correct": result["correct"],
            "spider_total": result["total"],
            "spider_errors": result["errors"],
            "spider_target": SPIDER_EX_TARGET,
            "spider_pass": ex >= SPIDER_EX_TARGET,
        })
        status = "PASS ✓" if ex >= SPIDER_EX_TARGET else "FAIL ✗"
        print(f"[eval] Spider EX = {ex:.2%}  (target ≥ {SPIDER_EX_TARGET:.0%})  {status}")

    if args.bird_eval_path and args.bird_db_dir:
        print("[eval] BIRD-dev ...")
        samples = json.loads(Path(args.bird_eval_path).read_text())
        result = evaluate_dataset(samples, predict_fn, args.bird_db_dir)
        ex = result["execution_accuracy"]
        metrics.update({
            "bird_execution_accuracy": ex,
            "bird_correct": result["correct"],
            "bird_total": result["total"],
            "bird_errors": result["errors"],
            "bird_target": BIRD_EX_TARGET,
            "bird_pass": ex >= BIRD_EX_TARGET,
        })
        status = "PASS ✓" if ex >= BIRD_EX_TARGET else "FAIL ✗"
        print(f"[eval] BIRD EX   = {ex:.2%}  (target ≥ {BIRD_EX_TARGET:.0%})  {status}")

    gate_results = [v for k, v in metrics.items() if k.endswith("_pass")]
    metrics["eval_gate_passed"] = bool(gate_results) and all(gate_results)

    save_metrics(metrics, str(output_dir), run_id=args.run_id)

    if not metrics.get("eval_gate_passed", True):
        print("\n[eval] DEPLOYMENT GATE FAILED — model does not meet minimum thresholds.")
        sys.exit(1)
    print("\n[eval] DEPLOYMENT GATE PASSED.")


def parse_args():
    p = argparse.ArgumentParser(description="Evaluate Module A Text-to-SQL model")
    p.add_argument("--model-dir", required=True, help="Path to trained model artifact")
    p.add_argument("--output-dir", default="/opt/ml/output", help="Where to write metrics files")
    p.add_argument("--run-id", default="eval")
    p.add_argument("--spider-eval-path", help="Path to Spider dev.json")
    p.add_argument("--spider-db-dir", help="Path to Spider database/ dir")
    p.add_argument("--bird-eval-path", help="Path to BIRD dev/dev.json")
    p.add_argument("--bird-db-dir", help="Path to BIRD dev/dev_databases/ dir")
    return p.parse_args()


if __name__ == "__main__":
    main()
