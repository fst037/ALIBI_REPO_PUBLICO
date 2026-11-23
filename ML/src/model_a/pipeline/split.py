"""
Pipeline stage 5/8 — split.

Replaces the ad hoc "carve 5% off the training set" fallback that used to be
inlined in prepare_data.main() with an explicit, seeded, logged step. Prefers
a real Spider-dev set (with real SQLite DBs, needed for evaluate.py's
execution-accuracy metric) when --spider-dir is given; otherwise falls back to
a seeded holdout from the preprocessed pool (loss-tracking only, not an EX
eval set — same caveat prepare_data.py already documented).

Writes the exact train/train.jsonl + eval/eval.jsonl layout train_qlora.py
already expects via SM_CHANNEL_TRAIN / SM_CHANNEL_EVAL.
"""

import argparse
import json
import random
import sys
from pathlib import Path

import mlflow

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from model_a.prepare_data import _spider_ddl, write_jsonl
from model_a.pipeline._common import configure_mlflow, start_stage_run


def _load_real_spider_dev(spider_dir: str) -> list:
    spider_dev = Path(spider_dir) / "dev.json"
    tables_path = Path(spider_dir) / "tables.json"
    if not (spider_dev.exists() and tables_path.exists()):
        return []
    tables = {db["db_id"]: _spider_ddl(db) for db in json.loads(tables_path.read_text())}
    return [
        {
            "schema": tables.get(s["db_id"], ""),
            "question": s["question"],
            "sql": s["query"],
            "db_id": s["db_id"],
            "source": "spider_dev",
        }
        for s in json.loads(spider_dev.read_text())
    ]


def main():
    args = parse_args()
    configure_mlflow()
    out = Path(args.output_dir)
    seed = int(args.seed)
    eval_fraction = float(args.eval_fraction)

    records_path = Path(args.preprocessed_dir) / "records.jsonl"
    pool = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line]

    with start_stage_run("split"):
        mlflow.log_param("preprocessed_dir", args.preprocessed_dir)
        mlflow.log_param("spider_dir", args.spider_dir or "")
        mlflow.log_param("seed", seed)
        mlflow.log_param("eval_fraction", eval_fraction)

        eval_samples = _load_real_spider_dev(args.spider_dir) if args.spider_dir else []
        eval_source = "spider_dev"

        if eval_samples:
            train_samples = pool
        else:
            eval_source = "seeded_holdout"
            rng = random.Random(seed)
            shuffled = pool[:]
            rng.shuffle(shuffled)
            cut = max(1, int(len(shuffled) * eval_fraction)) if shuffled else 0
            eval_samples = shuffled[:cut]
            train_samples = shuffled[cut:]
            print(f"[split] no real Spider-dev found — using a seeded {eval_fraction:.0%} "
                  f"holdout (loss tracking only, not an EX eval set)")

        n_train = write_jsonl(train_samples, out / "train" / "train.jsonl")
        n_eval = write_jsonl(eval_samples, out / "eval" / "eval.jsonl")

        print(f"[split] train={n_train:,}  eval={n_eval:,}  (eval source: {eval_source})")
        mlflow.log_param("eval_source", eval_source)
        mlflow.log_metric("train_count", n_train)
        mlflow.log_metric("eval_count", n_eval)
        mlflow.log_artifact(str(out / "train" / "train.jsonl"), artifact_path="train")
        mlflow.log_artifact(str(out / "eval" / "eval.jsonl"), artifact_path="eval")


def parse_args():
    p = argparse.ArgumentParser(description="Split stage for Module A")
    p.add_argument("--preprocessed-dir", default="data/preprocessed/model_a")
    p.add_argument("--output-dir", default="data/processed/model_a")
    p.add_argument("--spider-dir", default="", help="If given and it contains dev.json+tables.json, used as the real eval set")
    p.add_argument("--seed", default="42")
    p.add_argument("--eval-fraction", default="0.05")
    return p.parse_args()


if __name__ == "__main__":
    main()
