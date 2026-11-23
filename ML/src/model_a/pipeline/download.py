"""
Pipeline stage 1/8 — download.

Fetches Spider (auto, via HuggingFace's b-mc2/sql-create-context — reuses
prepare_data.load_spider_hf's Zscaler-safe SSL bypass, no manual download or
Google Drive step needed) and checks for a local BIRD raw directory (BIRD
itself stays manual/registration-gated at https://bird-bench.github.io/, same
as ML/data/download_datasets.sh already documents).

This stage does not parse/clean anything — it only gets bytes onto disk (or
confirms they're already there) and records what it found. Parsing into
{schema, question, sql, db_id, source} records happens in the `clean` stage,
which reuses the exact same loader functions from prepare_data.py.
"""

import argparse
import json
import sys
from pathlib import Path

import mlflow

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from model_a.prepare_data import load_spider_hf
from model_a.pipeline._common import configure_mlflow, start_stage_run


def run(output_dir: str, bird_dir: str) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    manifest = {"sources": {}}

    print("[download] fetching Spider via HuggingFace (b-mc2/sql-create-context) ...")
    spider_hf_path = out / "spider_hf.jsonl"
    count = 0
    with open(spider_hf_path, "w", encoding="utf-8") as f:
        for record in load_spider_hf():
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    manifest["sources"]["spider_hf"] = {
        "path": str(spider_hf_path),
        "record_count": count,
        "status": "downloaded" if count > 0 else "empty",
    }
    print(f"[download] spider_hf -> {spider_hf_path} ({count:,} records)")

    bird_path = Path(bird_dir) if bird_dir else None
    if bird_path and (bird_path / "train" / "train.json").exists():
        manifest["sources"]["bird"] = {"path": str(bird_path), "status": "present"}
        print(f"[download] BIRD found at {bird_path}")
    else:
        manifest["sources"]["bird"] = {"path": bird_dir, "status": "missing"}
        print(
            "[download] BIRD not found — manual download required from "
            "https://bird-bench.github.io/ (registration required), then pass "
            "--bird-dir pointing at the extracted folder."
        )

    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    args = parse_args()
    configure_mlflow()
    with start_stage_run("download"):
        mlflow.log_param("output_dir", args.output_dir)
        mlflow.log_param("bird_dir", args.bird_dir or "")
        manifest = run(args.output_dir, args.bird_dir)
        for source, info in manifest["sources"].items():
            if "record_count" in info:
                mlflow.log_metric(f"{source}_record_count", info["record_count"])
            mlflow.set_tag(f"{source}_status", info["status"])
        mlflow.log_artifact(str(Path(args.output_dir) / "manifest.json"))


def parse_args():
    p = argparse.ArgumentParser(description="Download stage for Module A")
    p.add_argument("--output-dir", default="data/raw")
    p.add_argument("--bird-dir", default="", help="Path to an already-downloaded BIRD root, if any")
    return p.parse_args()


if __name__ == "__main__":
    main()
