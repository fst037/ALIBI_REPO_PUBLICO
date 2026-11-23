"""
Structured metrics output for ALIBI training runs.
Writes metrics.json (machine-readable) and appends to metrics.csv (thesis-friendly flat table).
Both are saved to the same output_dir, which SageMaker copies to S3 at job end.
"""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def save_metrics(metrics: dict, output_dir: str, run_id: str = None) -> dict:
    """
    Save metrics as JSON and append to CSV.

    Args:
        metrics:    Dict of metric name → value (any JSON-serializable type).
        output_dir: Directory to write into. SageMaker will copy /opt/ml/model to S3.
        run_id:     Optional run identifier. Falls back to TRAINING_JOB_NAME env var.

    Returns:
        The enriched metrics dict (with run_id and timestamp added).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    enriched = {
        "run_id": run_id or os.environ.get("TRAINING_JOB_NAME", "local"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **metrics,
    }

    # JSON — structured, one file per run
    json_path = output_dir / "metrics.json"
    json_path.write_text(json.dumps(enriched, indent=2, default=str))

    # CSV — one row appended per run; all runs accumulate in the same file
    csv_path = output_dir / "metrics.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(enriched.keys()), extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(enriched)

    print(f"[metrics] → {json_path}")
    print(f"[metrics] → {csv_path}")
    return enriched
