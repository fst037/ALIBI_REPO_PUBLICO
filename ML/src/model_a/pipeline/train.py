"""
Pipeline stage 6/8 — train.

Two modes:

  --dummy true (default) — runs train_dummy.py in-process, no cloud
  dispatch at all. $0, seconds, used to validate the full 8-stage pipeline
  shape before spending anything on a real GPU run.

  --dummy false — real QLoRA training. This stage does NOT call AWS itself:
  Zscaler blocks Terraform/boto3 network calls from this environment the same
  way it blocks every other terraform/aws command in this repo (see
  ML/README.md), so dispatch stays exactly what it already is — a Terraform
  apply + a generated launch script (or common.launcher.launch() for
  SageMaker) run from your own terminal. What this stage does is:
    1. package the code tarball (pure local tar, no network — safe to run here)
    2. print the exact next commands for whichever backend is configured
    3. once you pass back --artifact-s3-uri (after the remote job finishes),
       download the artifact + its metrics.json and log everything into
       MLflow as this step's run, so the pipeline has one place recording
       every run regardless of which of the 4 backends produced it.
"""

import argparse
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

import mlflow

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from common import s3_utils
from model_a.pipeline._common import configure_mlflow, start_stage_run, str2bool


def _package_code(src_dir: Path, dest_tar: Path) -> None:
    with tarfile.open(dest_tar, "w:gz") as tf:
        tf.add(src_dir / "model_a", arcname="model_a")
        tf.add(src_dir / "common", arcname="common")
        # Tarball root, where SageMaker script mode looks for it.
        tf.add(src_dir / "requirements.txt", arcname="requirements.txt")
    print(f"[train] packaged code -> {dest_tar} ({dest_tar.stat().st_size / 1_048_576:.1f} MB)")


def run_dummy(data_dir: str, run_id: str) -> dict:
    """In-process dummy training — no SageMaker/EC2/RunPod dispatch, no network."""
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "model"
        os.environ["SM_MODEL_DIR"] = str(model_dir)
        os.environ["SM_CHANNEL_TRAIN"] = str(Path(data_dir) / "train")
        os.environ["SM_CHANNEL_EVAL"] = str(Path(data_dir) / "eval")
        os.environ["TRAINING_JOB_NAME"] = run_id

        from model_a.train_dummy import main as train_dummy_main
        train_dummy_main()

        metrics = json.loads((model_dir / "metrics.json").read_text())
        # Copy the artifact out of the tempdir before it's cleaned up.
        persisted_dir = Path("data/models/model_a") / run_id
        persisted_dir.mkdir(parents=True, exist_ok=True)
        for item in model_dir.iterdir():
            target = persisted_dir / item.name
            if item.is_dir():
                import shutil
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                import shutil
                shutil.copy(item, target)
        return {"metrics": metrics, "model_dir": str(persisted_dir)}


def run_remote_dispatch_notice(run_id: str, sample_fraction: float = 1.0) -> None:
    src_dir = Path(__file__).parent.parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "model_a.tar.gz"
        _package_code(src_dir, tar_path)
        persisted_tar = Path("data/code") / f"model_a-{run_id}.tar.gz"
        persisted_tar.parent.mkdir(parents=True, exist_ok=True)
        persisted_tar.write_bytes(tar_path.read_bytes())

    print(
        "\n[train] Real training needs your own terminal (Zscaler blocks AWS/Terraform "
        "calls from this environment) — see ML/README.md §7/§8:\n"
        f"  1. Upload the code package built at {persisted_tar}:\n"
        "       aws s3 cp " + str(persisted_tar) + " s3://<artifacts-bucket>/code/model_a.tar.gz "
        "--profile alibi --no-verify-ssl\n"
        "  2. terraform apply -target=module.training_sagemaker  (or training_ec2_gpu / training_external_gpu)\n"
        "  3. Run the generated launch script from your terminal.\n"
        "  4. Once the job completes, re-run this stage with "
        "--dummy false --artifact-s3-uri s3://<bucket>/model_a/runs/<run_id>/<job>/output/model.tar.gz "
        "to pull the artifact and metrics into MLflow.\n"
    )
    if sample_fraction < 1.0:
        print(
            f"[train] NOTE: this is a SMOKE dispatch (sample_fraction={sample_fraction}). "
            "The remote job only honours it if terraform.tfvars' training_hyperparameters "
            f"carries sample_fraction = {sample_fraction!r} — this stage cannot set a "
            "hyperparameter on the remote job itself."
        )


def run_remote_ingest(artifact_s3_uri: str, run_id: str) -> dict:
    """Second half of the real-training path: pull back an already-finished
    remote job's artifact + metrics.json and fold it into this MLflow run."""
    bucket, key = artifact_s3_uri[len("s3://"):].split("/", 1)
    with tempfile.TemporaryDirectory() as tmp:
        local_tar = Path(tmp) / "model.tar.gz"
        s3_utils.download_file(bucket, key, str(local_tar))
        model_dir = Path("data/models/model_a") / run_id
        model_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(local_tar, "r:gz") as tf:
            tf.extractall(model_dir)

    metrics_path = model_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    return {"metrics": metrics, "model_dir": str(model_dir)}


def main():
    args = parse_args()
    configure_mlflow()

    with start_stage_run(f"train-{args.run_id}"):
        mlflow.log_params({
            "dummy": args.dummy,
            "run_id": args.run_id,
            "base_model_id": args.base_model_id,
            "epochs": args.epochs,
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "sample_fraction": args.sample_fraction,
        })

        if str2bool(args.dummy):
            result = run_dummy(args.data_dir, args.run_id)
        elif args.artifact_s3_uri:
            result = run_remote_ingest(args.artifact_s3_uri, args.run_id)
        else:
            run_remote_dispatch_notice(args.run_id, float(args.sample_fraction))
            mlflow.set_tag("status", "dispatched_pending_manual_launch")
            print("[train] stopping here — nothing to log yet until the remote job finishes.")
            return

        for k, v in result["metrics"].items():
            if isinstance(v, (int, float)):
                mlflow.log_metric(k, v)
        mlflow.log_param("model_dir", result["model_dir"])
        mlflow.log_artifacts(result["model_dir"], artifact_path="model")
        print(f"[train] model_dir={result['model_dir']}")
        # Emit the path on stdout in a greppable form for run_pipeline.py to thread forward.
        print(f"MODEL_DIR={result['model_dir']}")


def parse_args():
    p = argparse.ArgumentParser(description="Train stage for Module A")
    p.add_argument("--data-dir", default="data/processed/model_a")
    p.add_argument("--run-id", default="local")
    p.add_argument("--dummy", default="true")
    p.add_argument("--artifact-s3-uri", default="", help="Set once a real remote job has finished, to pull its artifact back")
    p.add_argument("--base-model-id", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    p.add_argument("--epochs", default="3")
    p.add_argument("--lora-r", default="64")
    p.add_argument("--lora-alpha", default="128")
    p.add_argument("--sample-fraction", default="1.0",
                   help="Fraction of train/eval the real training script trains on. 1.0 = "
                        "full run; 0.001 = 0.1%% smoke run. Recorded here so the MLflow run "
                        "says which one it was; the remote job reads it from "
                        "SM_HP_SAMPLE_FRACTION.")
    return p.parse_args()


if __name__ == "__main__":
    main()
