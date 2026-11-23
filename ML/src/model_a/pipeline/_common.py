"""
Shared MLflow helpers for the Module A pipeline stages
(download / eda / clean / preprocess / split / train / test_model / publish).

Every stage script calls configure_mlflow() once, then runs inside its own
mlflow.start_run(). Kept deliberately tiny: the stages themselves stay
runnable as plain Python (no MLflow) for local debugging — MLflow is layered
on top, never a hard dependency of the underlying logic.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import mlflow

EXPERIMENT_NAME = "alibi-model-a"

# Resolve code root the same way every existing training script does
# (SageMaker /opt/ml/code, Lambda /tmp/code, or local checkout) so pipeline
# stages can `from common.metrics import save_metrics` etc. regardless of
# where they run.
_CODE_CANDIDATES = ["/opt/ml/code", "/tmp/code", str(Path(__file__).parent.parent.parent)]
for _p in _CODE_CANDIDATES:
    if Path(_p).is_dir() and _p not in sys.path:
        sys.path.insert(0, _p)
        break


def _default_tracking_uri() -> str:
    """Absolute file store at ML/mlruns, independent of the working directory.

    This MUST be absolute. run_pipeline.py runs from ML/, but mlflow.projects
    executes each stage's command from inside its project directory
    (ML/pipeline-cpu-data or ML/pipeline-gpu-train). With a relative
    `file:./mlruns`, the driver and the stage resolve two different stores, so
    the stage cannot find the run id the driver created and exported to it via
    MLFLOW_RUN_ID — it fails with "Run '<id>' not found", and the tell is
    "Experiment 'alibi-model-a' does not exist. Creating a new experiment."
    logged twice in one pipeline run.
    """
    return (Path(__file__).resolve().parents[3] / "mlruns").as_uri()


def configure_mlflow() -> str:
    """
    Point MLflow at MLFLOW_TRACKING_URI if it's set (e.g. a remote server),
    otherwise fall back to a local file store under ML/mlruns so every stage
    is runnable standalone with no server at all. There is no remote server
    in this project, so the file
    store is the normal path, not a fallback for a missing one.

    Returns the tracking URI actually in effect (useful for logging/debugging).
    """
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI") or _default_tracking_uri()
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT_NAME)
    return tracking_uri


def start_stage_run(run_name: str):
    """
    Start (or attach to) this stage's MLflow run.

    When invoked via `mlflow.projects.run()` (the pipeline driver's normal
    path), MLflow has already created a run and exported MLFLOW_RUN_ID before
    this process starts — calling start_run(run_name=...) again in that case
    raises, since you can't rename a run you're resuming. When a stage script
    is run directly (standalone debugging, not through the driver), no such
    run exists yet, so it's created here with the given name.
    """
    if os.environ.get("MLFLOW_RUN_ID"):
        return mlflow.start_run()
    return mlflow.start_run(run_name=run_name)


def log_json_artifact(data: dict, filename: str, artifact_path: str = None) -> None:
    """Serialize data as pretty JSON and log it as an MLflow artifact."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        mlflow.log_artifact(str(path), artifact_path=artifact_path)


def str2bool(value: str) -> bool:
    """argparse-friendly bool parser — MLflow Project params always arrive as strings."""
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")
