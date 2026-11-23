"""
Pipeline stage 2/8 — eda.

Profiles the raw BIRD/Spider samples (reusing prepare_data.py's existing
loader functions, not re-parsing anything) and logs a summary + histograms to
MLflow. Nothing here was implemented anywhere in the codebase before this
pipeline — training previously went straight from raw files to instruction
JSONL with no visibility into dataset shape.
"""

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

import mlflow

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from model_a.prepare_data import load_bird, load_spider, load_spider_hf
from model_a.pipeline._common import configure_mlflow, log_json_artifact, start_stage_run, str2bool


def _collect(spider_dir: str, bird_dir: str, use_spider_hf: bool) -> list:
    samples = []
    if use_spider_hf:
        samples.extend(load_spider_hf())
    elif spider_dir:
        samples.extend(load_spider(Path(spider_dir)))
    if bird_dir:
        samples.extend(load_bird(Path(bird_dir)))
    return samples


def _lengths(samples: list, field: str) -> list:
    return [len(s.get(field, "").split()) for s in samples if s.get(field)]


def _histogram(values: list, title: str, xlabel: str, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.hist(values, bins=30)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("count")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def profile(samples: list) -> dict:
    by_source: dict = {}
    for s in samples:
        by_source.setdefault(s.get("source", "unknown"), 0)
        by_source[s.get("source", "unknown")] += 1

    q_lengths = _lengths(samples, "question")
    sql_lengths = _lengths(samples, "sql")
    db_ids = {s.get("db_id") for s in samples if s.get("db_id")}

    def _stats(values: list) -> dict:
        if not values:
            return {"count": 0}
        return {
            "count": len(values),
            "mean": round(statistics.mean(values), 2),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
        }

    return {
        "total_samples": len(samples),
        "by_source": by_source,
        "distinct_db_ids": len(db_ids),
        "question_length_words": _stats(q_lengths),
        "sql_length_words": _stats(sql_lengths),
    }


def main():
    args = parse_args()
    configure_mlflow()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with start_stage_run("eda"):
        mlflow.log_param("spider_dir", args.spider_dir or "")
        mlflow.log_param("bird_dir", args.bird_dir or "")
        mlflow.log_param("use_spider_hf", args.use_spider_hf)

        samples = _collect(args.spider_dir, args.bird_dir, str2bool(args.use_spider_hf))
        print(f"[eda] profiling {len(samples):,} samples ...")
        summary = profile(samples)

        mlflow.log_metric("total_samples", summary["total_samples"])
        mlflow.log_metric("distinct_db_ids", summary["distinct_db_ids"])
        for source, n in summary["by_source"].items():
            mlflow.log_metric(f"samples_{source}", n)
        if summary["question_length_words"]["count"]:
            mlflow.log_metric("question_length_words_mean", summary["question_length_words"]["mean"])
        if summary["sql_length_words"]["count"]:
            mlflow.log_metric("sql_length_words_mean", summary["sql_length_words"]["mean"])

        log_json_artifact(summary, "eda_summary.json")

        with tempfile.TemporaryDirectory() as tmp:
            q_path = Path(tmp) / "question_length_hist.png"
            sql_path = Path(tmp) / "sql_length_hist.png"
            if _lengths(samples, "question"):
                _histogram(_lengths(samples, "question"), "Question length", "words", q_path)
                mlflow.log_artifact(str(q_path))
            if _lengths(samples, "sql"):
                _histogram(_lengths(samples, "sql"), "SQL length", "words", sql_path)
                mlflow.log_artifact(str(sql_path))

        (out / "eda_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[eda] done -> {out}/eda_summary.json")
        print(f"[eda] {summary['total_samples']:,} samples across {len(summary['by_source'])} source(s), "
              f"{summary['distinct_db_ids']:,} distinct DBs")


def parse_args():
    p = argparse.ArgumentParser(description="EDA stage for Module A")
    p.add_argument("--output-dir", default="data/eda")
    p.add_argument("--spider-dir", default="")
    p.add_argument("--bird-dir", default="")
    p.add_argument("--use-spider-hf", default="true")
    return p.parse_args()


if __name__ == "__main__":
    main()
