"""
SageMaker inference handler: ONE base model, several LoRA adapters.

Module A (Text-to-SQL, adapter `sql`) and Module C (Vega-Lite, adapter `chart`)
are LoRA adapters on the same base model (Qwen3.8-27B), so a single endpoint
holds the 4-bit base once (~17 GB) and switches adapters per request instead of
paying for two GPU endpoints.

model.tar.gz layout (built by ml-deploy.yml, endpoint_action=deploy):

    adapters/sql/    adapter_config.json, adapter_model.safetensors, tokenizer...
    adapters/chart/  (optional) the same, from a `--task chart` training run
    code/            inference.py, guardrail.py, common/prompts.py, requirements.txt

A tarball with a single adapter at its root (what a bare training job emits) is
served as `sql`, so older artifacts still load.

Request  (application/json):
    {"task": "ping"}
    {"task": "sql",   "schema": "...", "question": "..."}
    {"task": "chart", "question": "...", "row_count": 12,
     "columns": [{"name", "dtype", "cardinality", "label"?}], "rows": [{...}, ...]}
`task` defaults to "sql".

Response:
    ping  -> {"task": "ping", "ready": true, "adapters": [...]}   (no generation: the
             backend's proof that model_fn finished, after a scale-from-zero)
    sql   -> {"task", "sql", "valid", "error", "model_type"}
    chart -> {"task", "chart_type", "spec", "valid_json", "error", "raw", "model_type"}
The chart spec is NOT schema-validated here; the backend validates it against the
official Vega-Lite schema and the real result columns before anything is drawn.

SageMaker script mode calls: model_fn -> input_fn -> predict_fn -> output_fn.
The first request after InService can arrive while model_fn is still downloading
and loading the base (minutes, for a 27B): clients must retry (see ml-deploy.yml).
"""

import json
import os
import sys
import threading
from pathlib import Path

# code/ holds guardrail.py and common/; make them importable however the
# container sets its working directory.
_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from common.prompts import TASKS, render_inference_prompt  # noqa: E402

_LOCK = threading.Lock()  # set_adapter() mutates the shared model


def model_fn(model_dir: str) -> dict:
    """Load model at endpoint startup. Called once per container lifecycle."""
    model_dir = Path(model_dir)
    config_path = model_dir / "model_config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}

    model_type = os.environ.get("MODEL_TYPE") or config.get("model_type", "qlora")
    print(f"[inference] loading model_type={model_type!r} from {model_dir}")

    if model_type == "dummy":
        return _load_dummy(model_dir, config)
    return _load_qlora(model_dir, config)


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load_dummy(model_dir: Path, config: dict) -> dict:
    print("[inference] dummy model loaded (stdlib-only, rule-based SQL generation)")
    return {"type": "dummy", "model": None, "config": config, "adapters": ["sql"]}


def _adapter_dirs(model_dir: Path) -> dict:
    """{task: directory}. adapters/<task>/ if present, else one adapter at the root as `sql`."""
    root = model_dir / "adapters"
    if root.is_dir():
        found = {p.name: p for p in sorted(root.iterdir()) if (p / "adapter_config.json").exists()}
        unknown = set(found) - set(TASKS)
        if unknown:
            raise ValueError(f"unknown adapter(s) {sorted(unknown)}; expected a subset of {TASKS}")
        if found:
            return found
    if (model_dir / "adapter_config.json").exists():
        return {"sql": model_dir}
    raise FileNotFoundError(f"no LoRA adapter found under {model_dir}")


def _load_qlora(model_dir: Path, config: dict) -> dict:
    """Base model from the HuggingFace hub (named in the adapters' configs) + every
    adapter shipped in model_dir.

    The base is loaded 4-bit NF4 by default — the quantization it was trained
    under, and the only way Qwen3.8-27B (~55 GB in bf16) fits one 24 GB A10G.
    LOAD_IN_4BIT=false loads bf16 instead, for a smaller base on a bigger instance.
    """
    import torch
    import transformers
    from peft import PeftModel
    from transformers import AutoConfig, AutoTokenizer, BitsAndBytesConfig

    dirs = _adapter_dirs(model_dir)
    bases = {json.loads((d / "adapter_config.json").read_text())["base_model_name_or_path"] for d in dirs.values()}
    if len(bases) != 1:
        # An adapter only makes sense on the base it was trained on.
        raise ValueError(f"adapters were trained on different bases: {sorted(bases)}")
    base_id = bases.pop()

    quant = None
    if os.environ.get("LOAD_IN_4BIT", "true").lower() != "false":
        quant = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

    first = next(iter(dirs))
    print(f"[inference] loading base {base_id} (4bit={quant is not None}), adapters={list(dirs)} ...")
    tokenizer = AutoTokenizer.from_pretrained(str(dirs[first]), trust_remote_code=True)

    # A checkpoint that ships as a multimodal wrapper (Qwen3.8: architecture
    # Qwen3_5ForConditionalGeneration) was trained through AutoModelForMultimodalLM
    # (train_qlora.py --model-class), so its adapters' key names only line up when
    # the base is loaded the same way. Decided from the base's own config so the
    # adapter artifact needs no extra metadata.
    arch = (AutoConfig.from_pretrained(base_id, trust_remote_code=True).architectures or [""])[0]
    cls_name = "AutoModelForMultimodalLM" if "ConditionalGeneration" in arch else "AutoModelForCausalLM"
    print(f"[inference] base architecture {arch!r} -> {cls_name}")
    loader = getattr(transformers, cls_name)

    base = None
    cache = _QuantCache.for_base(base_id) if quant is not None else None
    if cache is not None:
        base = cache.load(loader, torch)
    if base is None:
        base = loader.from_pretrained(
            base_id,
            quantization_config=quant,
            device_map="auto",
            dtype=torch.bfloat16,
            trust_remote_code=True,
        )
        if cache is not None:
            cache.publish(base)

    model = PeftModel.from_pretrained(base, str(dirs[first]), adapter_name=first)
    for name, path in dirs.items():
        if name != first:
            model.load_adapter(str(path), adapter_name=name)
    model.eval()
    print(f"[inference] adapters attached: {list(dirs)} (base={base_id})")
    bundle = {"type": "qlora", "model": model, "tokenizer": tokenizer, "config": config, "adapters": list(dirs)}
    _warm_up(bundle)
    return bundle



# ── Quantized-base cache ──────────────────────────────────────────────────────
# A cold copy used to download the 27B base from HuggingFace (~55 GB bf16) and
# quantise it on the GPU: ~10 minutes of billed GPU time on every scale-from-zero
# (measured 2026-10-15). The first load now saves the quantised base (~17 GB) to
# S3; every later cold start downloads that in parallel and loads it as is.
# Strictly an accelerator: any failure here falls back to the HuggingFace path,
# and a half-written cache is never read (the marker object goes up last).

_CACHE_MARKER = "_COMPLETE"


class _QuantCache:
    def __init__(self, bucket: str, prefix: str, local: Path) -> None:
        self.bucket, self.prefix, self.local = bucket, prefix, local

    @classmethod
    def for_base(cls, base_id: str) -> "_QuantCache | None":
        uri = os.environ.get("QUANT_CACHE_S3_URI", "").strip()
        if not uri.startswith("s3://"):
            return None
        import bitsandbytes
        import transformers

        bucket, _, root = uri[5:].partition("/")
        # The serialised layout depends on these versions (requirements.txt lets
        # pip pick them), so a new version is a new cache entry, not a bad read.
        key = f"{base_id.replace('/', '--')}/tf{transformers.__version__}-bnb{bitsandbytes.__version__}-nf4"
        return cls(bucket, f"{root.strip('/')}/{key}".lstrip("/"), Path("/tmp/quantized-base"))

    def _s3(self):  # noqa: ANN202 - boto3 type
        import boto3
        from botocore.config import Config

        return boto3.client("s3", config=Config(max_pool_connections=64, retries={"max_attempts": 5}))

    def load(self, loader, torch):  # noqa: ANN001, ANN201
        """The cached base, or None (nothing cached yet, or it would not load)."""
        import shutil
        import time

        try:
            s3 = self._s3()
            s3.head_object(Bucket=self.bucket, Key=f"{self.prefix}/{_CACHE_MARKER}")
        except Exception:  # noqa: BLE001 - absent is the normal first run
            print(f"[inference] no quantised-base cache at s3://{self.bucket}/{self.prefix}; loading from HuggingFace")
            return None
        try:
            started = time.time()
            shutil.rmtree(self.local, ignore_errors=True)
            self.local.mkdir(parents=True)
            self._download(s3)
            print(f"[inference] quantised base downloaded in {time.time() - started:.0f}s")
            base = loader.from_pretrained(
                str(self.local), device_map="auto", dtype=torch.bfloat16, trust_remote_code=True
            )
            print(f"[inference] quantised base loaded from cache in {time.time() - started:.0f}s")
            return base
        except Exception as err:  # noqa: BLE001 - never let the accelerator break the endpoint
            print(f"[inference] quantised-base cache unusable ({type(err).__name__}: {err}); loading from HuggingFace")
            return None
        finally:
            shutil.rmtree(self.local, ignore_errors=True)

    def _download(self, s3) -> None:  # noqa: ANN001
        from concurrent.futures import ThreadPoolExecutor

        keys = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix + "/"):
            keys += [o["Key"] for o in page.get("Contents", []) if not o["Key"].endswith("/" + _CACHE_MARKER)]
        if not keys:
            raise FileNotFoundError("cache marker present but no files")

        def fetch(key: str) -> None:
            dest = self.local / key[len(self.prefix) + 1:]
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(self.bucket, key, str(dest))

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(fetch, keys))  # list() re-raises the first failure

    def publish(self, base) -> None:  # noqa: ANN001
        """Save the freshly quantised base so the next cold start can skip the hub."""
        import shutil
        import time

        try:
            started = time.time()
            shutil.rmtree(self.local, ignore_errors=True)
            base.save_pretrained(str(self.local), safe_serialization=True)
            s3 = self._s3()
            from concurrent.futures import ThreadPoolExecutor

            files = [p for p in self.local.rglob("*") if p.is_file()]
            with ThreadPoolExecutor(max_workers=16) as pool:
                list(pool.map(
                    lambda p: s3.upload_file(str(p), self.bucket, f"{self.prefix}/{p.relative_to(self.local).as_posix()}"),
                    files,
                ))
            s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}/{_CACHE_MARKER}", Body=b"")
            print(f"[inference] quantised base cached at s3://{self.bucket}/{self.prefix} in {time.time() - started:.0f}s")
        except Exception as err:  # noqa: BLE001 - the endpoint still serves; the next cold start just pays again
            print(f"[inference] could not cache the quantised base ({type(err).__name__}: {err})")
        finally:
            shutil.rmtree(self.local, ignore_errors=True)


# One realistic request per adapter, run before model_fn returns.
_WARM_UP = {
    "sql": {"schema": "CREATE TABLE sales (\n  region TEXT,\n  revenue REAL\n);",
            "question": "What is the total revenue by region?"},
    "chart": {"question": "Total revenue by region", "row_count": 2,
              "columns": [{"name": "region", "dtype": "nominal", "cardinality": 2},
                          {"name": "SUM(revenue)", "dtype": "quantitative", "cardinality": 2}],
              "rows": [{"region": "North", "SUM(revenue)": 10.5}, {"region": "South", "SUM(revenue)": 7.0}]},
}


def _warm_up(bundle: dict) -> None:
    """Pay the first-generation cost here, not on a user's request.

    Measured 2026-10-12: the first request after loading took 31 s, every later
    one ~2 s (the linear-attention kernels compile on first use). A user's first
    chart would have been ~40 s against SageMaker's 60 s invocation limit. The
    backend treats the endpoint as ready once `ping` answers — i.e. once this
    has run — so nobody waits on the compile again.
    """
    import time

    for task in bundle["adapters"]:
        started = time.time()
        try:
            _generate(bundle, task, _WARM_UP[task], max_new_tokens=8)
            print(f"[inference] warm-up {task}: {time.time() - started:.1f}s")
        except Exception as err:  # noqa: BLE001 - a failed warm-up must not stop the endpoint loading
            print(f"[inference] warm-up {task} failed ({err}); the first request will pay for it")


# ── Predict ───────────────────────────────────────────────────────────────────

def predict_fn(input_data: dict, model_bundle: dict) -> dict:
    task = input_data.get("task", "sql")
    if task == "ping":
        return {"task": "ping", "ready": True, "adapters": model_bundle["adapters"]}
    if task not in TASKS:
        return {"task": task, "error": f"unknown task {task!r}; expected one of {list(TASKS)}"}
    if task not in model_bundle["adapters"]:
        return {"task": task, "error": f"no {task!r} adapter loaded; this endpoint serves {model_bundle['adapters']}"}
    if model_bundle["type"] == "dummy":
        return _predict_dummy(input_data, task)
    return _predict_qlora(input_data, model_bundle, task)


def _predict_dummy(input_data: dict, task: str) -> dict:
    """Trivially valid output — enough to test the endpoint plumbing."""
    if task == "chart":
        return {"task": "chart", "chart_type": "none", "spec": None, "valid_json": True, "error": None,
                "raw": "", "model_type": "dummy"}
    return {"task": "sql", "sql": "SELECT 1 AS alibi_pipeline_ok -- phase 0 dummy model",
            "model_type": "dummy", "valid": True, "error": None}


# New tokens per task: a SQL query is ~30-100 tokens, a data-less spec ~150-400.
_MAX_NEW_TOKENS = {"sql": 256, "chart": 768}


def _generate(bundle: dict, task: str, payload: dict, max_new_tokens: int | None = None) -> str:
    import torch

    model, tokenizer = bundle["model"], bundle["tokenizer"]
    prompt = render_inference_prompt(task, payload)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with _LOCK:
        model.set_adapter(task)
        with torch.inference_mode():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens or _MAX_NEW_TOKENS[task],
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
    new_tokens = output_ids[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).split("<|im_end|>")[0].strip()


def _predict_qlora(input_data: dict, bundle: dict, task: str) -> dict:
    text = _generate(bundle, task, input_data)

    if task == "sql":
        from guardrail import validate_sql  # packaged alongside, in code/

        valid, error = validate_sql(text)
        return {"task": "sql", "sql": text, "model_type": "qlora", "valid": valid, "error": error}

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict) or "chart_type" not in parsed:
            raise ValueError("reply is JSON but has no chart_type")
        return {"task": "chart", "chart_type": parsed["chart_type"], "spec": parsed.get("spec"),
                "valid_json": True, "error": None, "raw": text, "model_type": "qlora"}
    except (ValueError, json.JSONDecodeError) as err:
        return {"task": "chart", "chart_type": None, "spec": None, "valid_json": False,
                "error": str(err), "raw": text, "model_type": "qlora"}


# ── SageMaker I/O hooks ───────────────────────────────────────────────────────

def input_fn(request_body: str, content_type: str) -> dict:
    if content_type == "application/json":
        return json.loads(request_body)
    raise ValueError(f"Unsupported content type: {content_type!r}")


def output_fn(prediction: dict, accept: str) -> str:
    if accept in ("application/json", "*/*"):
        return json.dumps(prediction)
    raise ValueError(f"Unsupported accept type: {accept!r}")
