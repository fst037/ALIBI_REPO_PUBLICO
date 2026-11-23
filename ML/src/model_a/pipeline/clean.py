"""
Pipeline stage 3/8 — clean.

Extracts the cleaning that used to be buried inside prepare_data.write_jsonl
(a bare truthy check on schema/question/sql) into its own explicit, logged
step: drops incomplete records, de-duplicates exact (question, sql, db_id)
repeats, and normalizes whitespace. Reuses prepare_data.py's loaders for
extraction — this stage does not re-parse the raw dataset formats itself.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import mlflow

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from model_a.prepare_data import load_bird, load_spider, load_spider_hf
from model_a.pipeline._common import configure_mlflow, start_stage_run, str2bool

_WS_RE = re.compile(r"[ \t]+")


def _normalize_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def clean_records(samples: list) -> tuple:
    """Returns (cleaned, stats)."""
    stats = {"input": len(samples), "dropped_incomplete": 0, "dropped_duplicate": 0}
    seen = set()
    cleaned = []
    for s in samples:
        schema = (s.get("schema") or "").strip()
        question = (s.get("question") or "").strip()
        sql = (s.get("sql") or "").strip()
        if not schema or not question or not sql:
            stats["dropped_incomplete"] += 1
            continue

        key = (question.lower(), sql.lower(), s.get("db_id", ""))
        if key in seen:
            stats["dropped_duplicate"] += 1
            continue
        seen.add(key)

        cleaned.append({
            **s,
            "schema": _normalize_ws(schema),
            "question": _normalize_ws(question),
            "sql": _normalize_ws(sql),
        })

    stats["output"] = len(cleaned)
    return cleaned, stats


def main():
    args = parse_args()
    configure_mlflow()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with start_stage_run("clean"):
        mlflow.log_param("spider_dir", args.spider_dir or "")
        mlflow.log_param("bird_dir", args.bird_dir or "")
        mlflow.log_param("use_spider_hf", args.use_spider_hf)

        samples = []
        if str2bool(args.use_spider_hf):
            samples.extend(load_spider_hf())
        elif args.spider_dir:
            samples.extend(load_spider(Path(args.spider_dir)))
        if args.bird_dir:
            samples.extend(load_bird(Path(args.bird_dir)))

        print(f"[clean] input: {len(samples):,} raw samples")
        cleaned, stats = clean_records(samples)
        print(f"[clean] output: {stats['output']:,} "
              f"(dropped {stats['dropped_incomplete']:,} incomplete, "
              f"{stats['dropped_duplicate']:,} duplicate)")

        for k, v in stats.items():
            mlflow.log_metric(k, v)

        records_path = out / "records.jsonl"
        with open(records_path, "w", encoding="utf-8") as f:
            for r in cleaned:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        mlflow.log_artifact(str(records_path))
        print(f"[clean] wrote {records_path}")


def parse_args():
    p = argparse.ArgumentParser(description="Clean stage for Module A")
    p.add_argument("--output-dir", default="data/clean/model_a")
    p.add_argument("--spider-dir", default="")
    p.add_argument("--bird-dir", default="")
    p.add_argument("--use-spider-hf", default="true")
    return p.parse_args()


if __name__ == "__main__":
    main()
