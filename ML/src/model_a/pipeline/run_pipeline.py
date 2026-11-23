"""
Driver for the Module A MLflow multistep pipeline:
  download -> eda -> clean -> preprocess -> split -> train -> test_model -> publish

Each stage runs as its own `mlflow.projects.run()` call against one of two
MLflow Project directories (ML/pipeline-cpu-data, ML/pipeline-gpu-train — see
those MLproject files for why there are two: MLflow scopes one environment
per project directory, not per entry point), so each stage genuinely executes
in its own controlled environment rather than just inheriting whatever the
driver happens to be running in.

The whole pipeline is one parent MLflow run, with each stage as a child run
underneath it (MLflow's standard nested-run convention for multistep
projects). A stage whose parameters exactly match an already-FINISHED prior
run is skipped and that run's outputs are reused instead — the classic
"multistep_workflow" caching pattern — with the deliberate exception of
train/test_model/publish, which always re-run (a stale cache hit on the step
that costs real money, or on the gate that decides whether to publish, would
defeat the point of running them at all).

Usage:
  cd ML
  python -m src.model_a.pipeline.run_pipeline                       # full run, dummy model, $0
  python -m src.model_a.pipeline.run_pipeline --dummy false \\
      --run-id qlora-001 --stage-from train                         # resume from train after a change
"""

import argparse
import sys
from pathlib import Path

import mlflow
from mlflow.entities import RunStatus
from mlflow.tracking import MlflowClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from model_a.pipeline._common import EXPERIMENT_NAME, configure_mlflow

STAGES = ["download", "eda", "clean", "preprocess", "split", "train", "test_model", "publish"]
GPU_STAGES = {"train", "test_model"}
NEVER_CACHE = {"train", "test_model", "publish"}


def _project_uri(stage: str) -> str:
    return "pipeline-gpu-train" if stage in GPU_STAGES else "pipeline-cpu-data"


def _already_ran(client: MlflowClient, entry_point: str, parameters: dict, experiment_id: str):
    """Find a prior FINISHED run of this entry point with identical parameters."""
    runs = client.search_runs(
        [experiment_id],
        filter_string=f"tags.`mlflow.project.entryPoint` = '{entry_point}'",
        order_by=["start_time DESC"],
    )
    for run in runs:
        if run.info.status != RunStatus.to_string(RunStatus.FINISHED):
            continue
        if all(run.data.params.get(k) == str(v) for k, v in parameters.items()):
            return run
    return None


def run_step(client: MlflowClient, experiment_id: str, entry_point: str, parameters: dict,
             env_manager: str, cache: bool = True):
    if cache and entry_point not in NEVER_CACHE:
        cached = _already_ran(client, entry_point, parameters, experiment_id)
        if cached:
            print(f"[pipeline] {entry_point}: reusing cached run {cached.info.run_id}")
            return cached

    print(f"[pipeline] {entry_point}: running with {parameters}")
    submitted = mlflow.projects.run(
        uri=_project_uri(entry_point),
        entry_point=entry_point,
        parameters=parameters,
        experiment_id=experiment_id,
        env_manager=env_manager,
    )
    return client.get_run(submitted.run_id)


def main():
    args = parse_args()
    configure_mlflow()
    client = MlflowClient()
    experiment = client.get_experiment_by_name(EXPERIMENT_NAME)
    experiment_id = experiment.experiment_id if experiment else client.create_experiment(EXPERIMENT_NAME)

    stages = STAGES[STAGES.index(args.stage_from): STAGES.index(args.stage_to) + 1]
    print(f"[pipeline] running stages: {stages}")

    with mlflow.start_run(run_name=f"pipeline-{args.run_id}"):
        mlflow.set_tag("pipeline_run_id", args.run_id)
        model_dir = args.model_dir  # may be pre-supplied when resuming from test_model/publish
        gate_passed = False

        if "download" in stages:
            run_step(client, experiment_id, "download",
                     {"bird_dir": args.bird_dir}, args.env_manager)

        if "eda" in stages:
            run_step(client, experiment_id, "eda",
                     {"spider_dir": args.spider_dir, "bird_dir": args.bird_dir,
                      "use_spider_hf": str(args.use_spider_hf).lower()}, args.env_manager)

        if "clean" in stages:
            run_step(client, experiment_id, "clean",
                     {"spider_dir": args.spider_dir, "bird_dir": args.bird_dir,
                      "use_spider_hf": str(args.use_spider_hf).lower()}, args.env_manager)

        if "preprocess" in stages:
            run_step(client, experiment_id, "preprocess", {}, args.env_manager)

        if "split" in stages:
            run_step(client, experiment_id, "split",
                     {"spider_dir": args.spider_dir, "seed": args.seed,
                      "eval_fraction": args.eval_fraction}, args.env_manager)

        if "train" in stages:
            train_run = run_step(
                client, experiment_id, "train",
                {"run_id": args.run_id, "dummy": str(args.dummy).lower(),
                 "artifact_s3_uri": args.artifact_s3_uri, "base_model_id": args.base_model_id,
                 "sample_fraction": args.sample_fraction},
                args.env_manager, cache=False,
            )
            model_dir = train_run.data.params.get("model_dir", model_dir)
            if not model_dir:
                print("[pipeline] train dispatched to a remote backend and is not finished yet — "
                      "stopping here. Re-run with --stage-from test_model --model-dir <path> "
                      "once the remote job's artifact has been pulled back (see train.py's printed instructions).")
                return

        if "test_model" in stages:
            test_run = run_step(
                client, experiment_id, "test_model",
                {"model_dir": model_dir, "run_id": args.run_id,
                 "spider_eval_path": args.spider_eval_path, "spider_db_dir": args.spider_db_dir,
                 "bird_eval_path": args.bird_eval_path, "bird_db_dir": args.bird_db_dir},
                args.env_manager, cache=False,
            )
            gate_passed = test_run.data.metrics.get("eval_gate_passed", 0.0) >= 1.0

        if "publish" in stages:
            run_step(
                client, experiment_id, "publish",
                {"model_dir": model_dir, "run_id": args.run_id,
                 "artifact_s3_uri": args.artifact_s3_uri, "gate_passed": str(gate_passed).lower()},
                args.env_manager, cache=False,
            )

    print("[pipeline] done.")


def parse_args():
    p = argparse.ArgumentParser(description="Run the Module A MLflow multistep pipeline")
    p.add_argument("--stage-from", default=STAGES[0], choices=STAGES)
    p.add_argument("--stage-to", default=STAGES[-1], choices=STAGES)
    p.add_argument("--run-id", default="local")
    p.add_argument("--env-manager", default="local", choices=["local", "virtualenv"],
                    help="'local' runs in the current interpreter (fast local dev); "
                         "'virtualenv' provisions each stage's own python_env for true "
                         "environment isolation (needs the `virtualenv` package installed)")
    p.add_argument("--dummy", default="true")
    p.add_argument("--model-dir", default="", help="Pre-supply when resuming from test_model/publish")
    p.add_argument("--artifact-s3-uri", default="")
    p.add_argument("--base-model-id", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    p.add_argument("--sample-fraction", default="1.0",
                   help="Fraction of train/eval to train on. 1.0 = full run; 0.001 = 0.1%% "
                        "smoke run through the identical code path. Remote backends read it "
                        "from SM_HP_SAMPLE_FRACTION (training_hyperparameters in tfvars).")
    p.add_argument("--spider-dir", default="")
    p.add_argument("--bird-dir", default="")
    p.add_argument("--use-spider-hf", default="true")
    p.add_argument("--seed", default="42")
    p.add_argument("--eval-fraction", default="0.05")
    p.add_argument("--spider-eval-path", default="")
    p.add_argument("--spider-db-dir", default="")
    p.add_argument("--bird-eval-path", default="")
    p.add_argument("--bird-db-dir", default="")
    args = p.parse_args()
    if STAGES.index(args.stage_from) > STAGES.index(args.stage_to):
        p.error("--stage-from must come before --stage-to")
    return args


if __name__ == "__main__":
    main()
