# ALIBI ML — Infrastructure & Training Runbook

Terraform infrastructure and training code for ALIBI's ML modules.

> **Run Terraform through GitHub Actions.** `ml-deploy.yml` (apply),
> `ml-train.yml` (submit a SageMaker training job), `ml-destroy.yml` (targeted
> teardown). They run on GitHub's runners, which aren't behind the Zscaler
> proxy that breaks Terraform provider mTLS on your own machine. Every step
> below also works as a local `terraform apply -target=...` if you need it —
> that's what the workflows do under the hood — and `aws` from your own
> terminal needs `--no-verify-ssl`.

---

## What's here

```
ML/
├── terraform/
│   ├── bootstrap/            # Run once: created the Terraform state bucket
│   ├── modules/
│   │   ├── iam/              # SageMaker + ingestion-Lambda execution roles
│   │   ├── storage/          # S3 buckets (datasets + model artifacts)
│   │   ├── billing/          # AWS Budget alert
│   │   ├── ingestion-lambda/ # Module B evaluation baseline (see below)
│   │   ├── training/         # SageMaker Training Job config + launch script
│   │   └── inference/        # Real-time endpoint + optional scale-to-zero
│   └── envs/dev/             # module.training + module.inference
├── pipeline-cpu-data/        # MLflow Project: download/eda/clean/preprocess/split/publish
├── pipeline-gpu-train/       # MLflow Project: train/test_model
├── environments/             # python_env.yaml per controlled environment (§5)
├── src/
│   ├── requirements.txt      # Runtime deps for the training tarball (§7)
│   ├── model_a/              # Module A: Text-to-SQL
│   │   ├── train_qlora.py    # QLoRA Qwen2.5-Coder-7B, report_to="mlflow"
│   │   ├── train_dummy.py    # stdlib-only stand-in for the $0 pipeline dry run
│   │   ├── inference.py      # SageMaker serving handler
│   │   ├── evaluate.py       # EX metric on Spider-dev + BIRD-dev
│   │   ├── guardrail.py      # SQL AST validation
│   │   ├── prepare_data.py   # BIRD + Spider loaders, reused by pipeline/
│   │   └── pipeline/         # The 8 MLflow stage scripts + run_pipeline.py
│   ├── model_c/              # Module C: Vega-Lite generation
│   ├── module_b/             # Module B ingestion Lambda handler
│   └── common/               # metrics.py, s3_utils.py
└── data/                     # download_datasets.sh + notes
```

The four modules under `terraform/modules/` that this root does **not** compose
— `iam`, `storage`, `billing`, `ingestion-lambda` — are owned by the unified
root (`terraform/envs/dev`) as `module.ml_iam`/`ml_storage`/`ml_billing`/
`ml_ingestion_lambda`. See §2.

**One training backend: SageMaker.** There used to be four (`lambda`, `ec2`,
`external_gpu`/RunPod, `sagemaker`) behind a `training_backend` selector. Three
existed only because SageMaker GPU quota was unavailable; that quota was
approved 2026-09-06 and they were deleted 2026-09-07, along with the selector,
the standalone `envs/smoke-test` root, and the parallel `ML-Azure/` project. Git history has all of it if a cheaper backend is ever
needed again.

**No MLflow tracking server.** There used to be one here too — first an
always-on EC2, then a Lambda behind a Function URL — removed 2026-09-28. It
existed for one reason: giving a remote SageMaker job somewhere to log live
metrics. `module.training`'s `MetricDefinitions` (see `ML/terraform/modules/
training/main.tf`) do that instead, for $0, by having SageMaker scrape the
job's own stdout and graph the matches on the training job's CloudWatch
console — no server to build, deploy, or pay for. The MLflow *pipeline*
tracking (§5 below — params, artifacts, the model registry) is untouched: it
always ran against a local file store under `ML/mlruns` and never depended on
this server.

### Module B's ingestion Lambda is not the product's ingestion path

`ingestion-lambda` / `src/module_b/lambda_ingest.py` is the pre-QLoRA prompting
baseline the 2026-07-18 decision deliberately kept around to evaluate the
trained schema-inference adapter against. It genuinely calls Claude + Bedrock
Titan. The actual product's ingestion is `backend/app/ingest/` — a
deterministic-first pipeline using Azure AI Foundry, not Bedrock. Two different
systems, same module name, on purpose: don't "fix" this Lambda to match the
backend's provider.

---

## §0 — Local Python setup

The data stages (§3, §5) run locally. Everything else runs in CI.

```bash
python -m venv ML/.venv
ML/.venv/Scripts/python.exe -m pip install -r ML/src/model_a/requirements_pipeline.txt
```

Two things that will bite on this machine:

**Put the venv on `PATH`, do not just call its python by path.** `run_pipeline.py`
shells out to `python ../src/model_a/pipeline/<stage>.py` for each stage, so a
child process resolves `python` from `PATH`. Invoking `ML/.venv/Scripts/python.exe`
directly leaves `PATH` untouched and every stage runs under system Python, which
has no mlflow.

```bash
export PATH="$PWD/ML/.venv/Scripts:$PATH"    # Git Bash
# .\ML\.venv\Scripts\Activate.ps1        # PowerShell
```

**HTTPS is intercepted, so pip needs a CA bundle.** Traffic on this machine is
re-signed by *Norton Web/Mail Shield* (not Zscaler — check `openssl s_client
-connect pypi.org:443` if it ever changes). Windows trusts that root; pip does
not, because it ships its own certifi bundle and ignores the OS trust store.
The symptom is `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`
on every install.

The fix is a bundle of certifi + that root, which `ML/.venv/pip.ini` already
points at. To rebuild it:

```powershell
$c = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Thumbprint -eq '2533FE85434C4BEA09D6EF1A1468400C00F3C9F3' }
$b64 = [Convert]::ToBase64String($c.RawData, 'InsertLineBreaks')
"-----BEGIN CERTIFICATE-----`r`n$b64`r`n-----END CERTIFICATE-----" | Out-File "$HOME\.certs
orton-web-mail-shield-root.pem" -Encoding ascii
$certifi = python -c "import pip._vendor.certifi as c; print(c.where())"
Get-Content $certifi, "$HOME\.certs
orton-web-mail-shield-root.pem" | Set-Content "$HOME\.certs\corp-ca-bundle.pem" -Encoding ascii
```

Use it for other tools too via `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`. Prefer this
over `--trusted-host`, which switches verification off rather than fixing it.
(`prepare_data.load_spider_hf()` does disable verification outright for the
HuggingFace download.)

`ML/.venv/pip.ini` also sets `user = false`, because this machine's global pip
config has `install.user = yes`, which is fatal inside a venv:
*"Can not perform a '--user' install."*

---

## §1 — Terraform state

Already bootstrapped. `terraform/bootstrap/` created
`alibi-terraform-state-213598695817`, and this root's backend is configured
against `alibi/ml/dev/terraform.tfstate`. Nothing to do unless you're rebuilding
the account from scratch.

---

## §2 — Core infra (owned by the unified root)

IAM, storage, billing and the Module B ingestion Lambda are applied from
`terraform/envs/dev`, not here:

```bash
cd terraform/envs/dev
terraform apply -target=module.ml_iam -target=module.ml_storage -target=module.ml_billing -target=module.ml_ingestion_lambda
```

or `infra-deploy.yml` with `deploy_ml=true`, which also populates the repo
variables `ml-deploy.yml` reads this root's cross-root inputs from:
`AWS_ML_SAGEMAKER_ROLE_ARN`, `AWS_ML_DATASETS_BUCKET`,
`AWS_ML_ARTIFACTS_BUCKET`.

Running that at least once is a hard prerequisite for everything below.

---

## §3 — Prepare datasets

The SageMaker job reads `s3://<datasets-bucket>/model_a/{train,eval}/`.

Spider downloads automatically from HuggingFace (`b-mc2/sql-create-context`) —
the pipeline's `download` stage handles it, no manual step. **BIRD is
registration-gated** at https://bird-bench.github.io/ and must be fetched by
hand; `download.py` records it as present/missing but cannot fetch it. Spider
alone is enough for a smoke run.

Run the CPU/data half of the pipeline (§5), then upload:

```bash
aws s3 sync ML/data/processed/model_a s3://<datasets-bucket>/model_a/ \
  --profile alibi --no-verify-ssl
```

`ML/data/download_datasets.sh` also fetches nvBench for Module C.

---

## §4 — Train, then serve

```bash
# 1. Package and upload the code tarball  (ml-deploy.yml: package_and_upload_code)
cd ML/src
tar -czf C:\Temp\model_a.tar.gz model_a\ common\ requirements.txt
aws s3 cp C:\Temp\model_a.tar.gz s3://<artifacts-bucket>/code/model_a.tar.gz --profile alibi --no-verify-ssl

# 2. Submit the job  (ml-train.yml, type LAUNCH — it regenerates the job config itself)
cd ML/terraform/envs/dev
terraform apply -target=module.training     # writes training_job_<run_id>.json
.\launch_training_<run_id>.ps1

# 3. Monitor
aws sagemaker describe-training-job --training-job-name <job> --query TrainingJobStatus --profile alibi --no-verify-ssl

# 4. Serve a completed run: ml-deploy.yml
#    endpoint_action=list    -> table of completed jobs (base model, smoke/full, eval loss)
#    endpoint_action=deploy  -> training_job_name=<job from the list> (blank = latest),
#                               inference_instance_types=<priority list, default = the GPUs the 27B fits>,
#                               endpoint_confirm=DEPLOY
#    First answer comes ~8 min after InService (the model downloads + loads on first
#    use); real-time calls are cut at 60 s, so retry. Then ask it:
#      aws sagemaker-runtime invoke-endpoint --endpoint-name model-a-dev --content-type application/json #        --body fileb://req.json out.json --no-verify-ssl
```

**Watching a run.** The SageMaker console's training-job page graphs
`train:loss`, `eval:loss`, `train:learning_rate`, `train:grad_norm` and
`train:epoch` live, scraped straight from the job's own stdout by the
`MetricDefinitions` in `ML/terraform/modules/training/main.tf` — no server, no
setup, $0 beyond the training job itself. CloudWatch Logs
(`/aws/sagemaker/TrainingJobs`) has the raw output, including the
`SMOKE: train N -> M` line a smoke run prints.

`module.training` creates **no AWS resources** — SageMaker Training Jobs have no
Terraform resource type, so it only writes `training_job_<run_id>.json` and a
launch script. Applying it can never start or bill anything; submitting the job
is a separate, explicit act.

Always bump `training_run_id` between runs: SageMaker rejects duplicate job
names, and the artifact S3 path is keyed on it.

**Destroy the endpoint when you're done** — it bills ~$1.41/hr while up with no
idle scale-down (`ml-destroy.yml` defaults `destroy_inference` on for exactly
this reason):

```bash
ml-destroy.yml (destroy_inference)  # or: terraform destroy -target=module.inference
```

### Cost reference

| Job | Compute | Est. time | Est. cost |
|-----|---------|-----------|-----------|
| Smoke run (§6) | `ml.g5.2xlarge` on-demand | ~15-20 min | ~$0.50-1.50 |
| Full QLoRA run | `ml.g5.2xlarge` on-demand | 6-10 h | ~$14-24 |
| Inference endpoint (idle) | `ml.g5.xlarge` | — | ~$1.41/hr |
| Module B ingestion Lambda | Lambda 1 GB | <30 s/file | <$0.001/file |

Budget alert is set at $200/month.

---

## §5 — MLflow pipeline (Download → EDA → Clean → Preprocess → Split → Train → Test → Publish)

The scripts above (`prepare_data.py`, `train_qlora.py`, `evaluate.py`) are also wired into an
MLflow multistep pipeline, so every run is tracked (params/metrics/artifacts) instead of just
writing `metrics.json`. Two MLflow Project directories, one per controlled environment
(MLflow scopes one environment per project directory, not per entry point):

- `ml/pipeline-cpu-data/` — download, eda, clean, preprocess, split, publish
  (`ml/environments/cpu-data-env.yaml` → `requirements_pipeline.txt`)
- `ml/pipeline-gpu-train/` — train, test_model
  (`ml/environments/gpu-train-env.yaml` → `requirements_qlora.txt`)

Driven together by `ml/src/model_a/pipeline/run_pipeline.py`.

**Validate the whole shape for $0 first** (dummy model, seconds, no cloud dispatch at all):

```bash
cd ml
python -m src.model_a.pipeline.run_pipeline
```

This exercises every stage including the evaluation gate — with no real eval data available
locally, the gate fails closed and `publish` correctly refuses to register anything. That
refusal is the expected, correct behavior of a $0 dry run, not a bug.

**Run a single stage on its own** (useful while iterating on one stage):

```bash
cd ml/pipeline-cpu-data
mlflow run . --entry-point eda -P use_spider_hf=true
```

**Resume from a specific stage** (e.g. after changing only the training script):

```bash
python -m src.model_a.pipeline.run_pipeline --stage-from train --dummy false \
  --run-id qlora-001 --model-dir data/processed/model_a
```

**Real training**, once real data is prepared (§3) and the SageMaker job has been launched
(§4 — this pipeline does not, and cannot, run Terraform or dispatch a paid job itself; see
`train.py`'s own docstring):

```bash
python -m src.model_a.pipeline.run_pipeline --dummy false --run-id qlora-001 \
  --artifact-s3-uri s3://<artifacts-bucket>/model_a/runs/qlora-001/<job-name>/output/model.tar.gz \
  --stage-from train --spider-eval-path data/raw/spider/dev.json --spider-db-dir data/raw/spider/database
```

By default `--env-manager local` runs every stage in whatever Python environment you already
have active (fast for local dev). Pass `--env-manager virtualenv` to have MLflow actually
provision each stage's own environment from its `python_env.yaml` — needs `pip install
virtualenv` first.

---

## §6 — Smoke run: the real pipeline at 0.1% length

`sample_fraction` is a hyperparameter on `train_qlora.py` (default `1.0`). Below
1.0 it trains on a seeded random slice of the same data, through the same
script, the same container image, the same S3 channels and the same backend —
so a smoke run proves the whole path (dependency resolution, base-model
download, 4-bit load, LoRA attach, MLflow logging, checkpointing, artifact
upload to S3) without paying for a full run. `0.001` is the 0.1% setting:
minutes and cents instead of 6-10h and ~$14-24.

It is not a separate code path and does not produce a usable model. Two
behaviours change, both deliberately:

- The train/eval splits are subsampled (`shuffle(seed=42).select(...)`).
- `logging_steps`/`eval_steps`/`save_steps` are derived from the real optimizer
  step count instead of the full-run constants `50`/`500`. At 0.1% those
  constants would never fire once, and the job would "succeed" without ever
  having evaluated, checkpointed, or logged a metric — exactly the paths the
  smoke run exists to test. At `sample_fraction = 1.0` they stay `50`/`500`,
  unchanged.

**On SageMaker** it arrives as `SM_HP_SAMPLE_FRACTION`, i.e. through
`training_hyperparameters` in `terraform.tfvars` — the same seam `epochs`,
`learning_rate` and `lora_r` already use. `terraform.tfvars` currently ships
the smoke settings:

```hcl
training_run_id              = "smoke-001"
training_hyperparameters     = { epochs = "3", learning_rate = "2e-4", lora_r = "64", sample_fraction = "0.001" }
training_max_runtime_seconds = 5400   # 90m ceiling instead of 12h
```

To promote to the real run: set `training_run_id = "qlora-001"`, drop
`sample_fraction` (or set `"1.0"`), restore `training_max_runtime_seconds =
43200`, then run `ml-train.yml`, which regenerates the job config from those
values before submitting. Always bump `training_run_id` —
SageMaker refuses a duplicate job name, and the artifact path is keyed on it.

**Locally / through the pipeline driver**, `--sample-fraction` is threaded
through `run_pipeline.py` → the `train` entry point → `pipeline/train.py`, where
it is logged as an MLflow param so a run's own record says which kind it was.
A smoke run's MLflow run name is prefixed `smoke-`:

```bash
cd ML
python -m src.model_a.pipeline.run_pipeline --dummy false --run-id smoke-001 \
    --sample-fraction 0.001 --stage-from train
```

Note that the pipeline's `train` stage cannot set a hyperparameter on a remote
job — it packages code and prints the dispatch instructions (see `train.py`'s
docstring). `terraform.tfvars` is what the remote job actually reads.

---

## §7 — Training dependencies come from the tarball, not the image

`ML/src/requirements.txt` sits at the **root of the code tarball** and is the
single dependency list all three GPU backends resolve from.

SageMaker script mode pip-installs a `requirements.txt` found at the code root
before running `sagemaker_program`, automatically. Without one, a job runs on
whatever the Deep Learning Container happens to ship — for the configured
`huggingface-pytorch-training:2.1.0-transformers4.36.0-gpu-py310-cu121` that is
transformers 4.36 and no `trl` at all, and `train_qlora.py` fails on both:

- `TrainingArguments(eval_strategy=...)` — the argument was `evaluation_strategy`
  before transformers 4.41.
- `SFTTrainer(max_seq_length=..., tokenizer=...)` — both moved into `SFTConfig`
  in trl 0.12 and were removed in 0.13, so an unpinned `pip install trl` breaks
  it from the other direction.

Those two bounds are the load-bearing ones and are commented as such in the file.
This was also the fix for the EC2-spot and RunPod backends, whose user-data
`pip install`ed an unpinned list — they were deleted 2026-09-07, but the file
they shared is the one that survives.

**`torch` is deliberately not in it.** The SageMaker DLC already ships a
CUDA-matched build, and reinstalling it there is both slow and a good way to
break the CUDA pairing.

If you package the tarball by hand, include it:

```powershell
cd ML\src
tar -czf C:\Temp\model_a.tar.gz model_a\ common\ lambda_handlers\ requirements.txt
```

`ml-deploy.yml`'s `package_and_upload_code` step and `pipeline/train.py`'s
`_package_code()` both already do.

These pins were validated by the first real smoke run (`smoke-002`, 2026-10-04):
the job resolved them on the DLC, loaded Qwen2.5-Coder-7B in 4-bit, trained,
evaluated, and uploaded a valid adapter. (`smoke-001` got as far as `SFTTrainer`
before failing on the `formatting_func` batch contract.)
