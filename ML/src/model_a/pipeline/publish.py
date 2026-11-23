"""
Pipeline stage 8/8 — publish.

Registers the trained adapter in the MLflow Model Registry, tagged with its
run id and artifact location, and moves it to the "staging" alias — but only
if test_model's gate passed. This is the literal enforcement of the project's
guiding principle: "no model moves to the endpoint deployment phase without
hitting the minimum metric threshold defined per module." It does not touch
the SageMaker endpoint Terraform (ML/terraform/modules/inference) — that
stays a deliberate, separate manual step, unchanged.
"""

import argparse
import sys
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from model_a.pipeline._common import configure_mlflow, start_stage_run, str2bool

MODEL_NAME = "alibi-model-a"


def main():
    args = parse_args()
    configure_mlflow()

    with start_stage_run(f"publish-{args.run_id}"):
        mlflow.log_param("model_dir", args.model_dir)
        mlflow.log_param("run_id", args.run_id)
        mlflow.log_param("artifact_s3_uri", args.artifact_s3_uri or "")

        if not str2bool(args.gate_passed):
            mlflow.set_tag("status", "refused_gate_not_passed")
            print("[publish] REFUSED — test_model's evaluation gate did not pass. "
                  "Nothing registered. This is the intended behavior, not a bug.")
            return

        source = args.artifact_s3_uri or args.model_dir
        if not source:
            raise SystemExit("[publish] gate passed but neither --artifact-s3-uri nor --model-dir was given")

        result = mlflow.register_model(model_uri=source, name=MODEL_NAME)
        client = MlflowClient()
        client.set_model_version_tag(MODEL_NAME, result.version, "training_run_id", args.run_id)
        client.set_registered_model_alias(MODEL_NAME, "staging", result.version)

        mlflow.set_tag("status", "registered")
        mlflow.log_param("registered_version", result.version)
        print(f"[publish] registered {MODEL_NAME} v{result.version} (alias: staging) <- {source}")


def parse_args():
    p = argparse.ArgumentParser(description="Publish stage for Module A")
    p.add_argument("--model-dir", default="")
    p.add_argument("--run-id", default="")
    p.add_argument("--artifact-s3-uri", default="")
    p.add_argument("--gate-passed", default="false")
    return p.parse_args()


if __name__ == "__main__":
    main()
