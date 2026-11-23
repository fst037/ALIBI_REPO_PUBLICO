"""
QLoRA fine-tuning script for Module A (Text-to-SQL).

Base model:  --base-model-id (default Qwen/Qwen3.8-27B, Apache 2.0; the 4-bit QLoRA settings
             below are the ones validated on one 24 GB A10G. Qwen2.5-Coder-7B still works:
             pass --model-class AutoModelForCausalLM --upcast-fp32 1)
Tasks:       --task sql (Module A) | chart (Module C): same script, same base, one adapter each
Method:      QLoRA — 4-bit NF4 quantization of frozen base + LoRA adapters
Datasets:    BIRD + Spider instruction-tuning JSONL (prepared by prepare_data.py)
Instance:    ml.g5.2xlarge (A10G, 24 GB VRAM) with Managed Spot Training

References:
  - QLoRA: Dettmers et al. (2023)
  - LoRA: Hu et al. (2022)
  - DAIL-SQL prompting conventions: Gao et al. (2024)

MLflow: report_to="mlflow" below hands off to transformers' built-in
MLflowCallback, which reads MLFLOW_TRACKING_URI / MLFLOW_EXPERIMENT_NAME /
MLFLOW_RUN_ID from the environment and logs every logging_steps interval
live — whoever launches this job (SageMaker hyperparameters, EC2 user-data,
RunPod env, or ML/src/model_a/pipeline/train.py locally) is responsible for
setting those three, the same way SM_HP_* is already threaded through for
every other hyperparameter. If MLFLOW_TRACKING_URI isn't set, the callback
no-ops rather than failing the run.
"""

import argparse
import inspect
import json
import os
import sys
from pathlib import Path

# Must be set before CUDA initialises. Fragmentation matters on a 24 GB card.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
from datasets import load_dataset
from peft import LoraConfig, TaskType, prepare_model_for_kbit_training
import transformers
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)
from trl import SFTConfig, SFTTrainer

_CODE_CANDIDATES = ["/opt/ml/code", "/tmp/code", str(Path(__file__).parent.parent)]
for _p in _CODE_CANDIDATES:
    if Path(_p).is_dir() and _p not in sys.path:
        sys.path.insert(0, _p)
        break
from common.metrics import save_metrics
from common.prompts import TASKS, render_training_text


# Fixed so a smoke run is reproducible without adding another knob to a script
# whose full-length path has no seed of its own.
_SMOKE_SEED = 42

def format_sample(sample: dict, task: str) -> dict:
    """Render one row into the single `text` column SFTTrainer trains on.

    Done here with dataset.map rather than through SFTTrainer(formatting_func=):
    that hook's contract changed across trl versions (batched list[str] before
    0.12-ish, per-example str after), and a version-independent text column is
    the one thing every trl release agrees on. The prompt text itself lives in
    common/prompts.py, shared with the endpoint handler so train and serve cannot
    drift.
    """
    return {"text": render_training_text(task, sample)}


def main():
    args = parse_args()

    train_dir = Path(os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train"))
    eval_dir = Path(os.environ.get("SM_CHANNEL_EVAL", "/opt/ml/input/data/eval"))
    model_dir = Path(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    checkpoint_dir = Path(os.environ.get("SM_CHECKPOINT_DIR", "/opt/ml/checkpoints"))
    job_name = os.environ.get("TRAINING_JOB_NAME", "local-qlora")
    model_dir.mkdir(parents=True, exist_ok=True)

    # ── Dataset ───────────────────────────────────────────────────────────────
    train_files = [str(f) for f in sorted(train_dir.glob("*.jsonl"))]
    eval_files = [str(f) for f in sorted(eval_dir.glob("*.jsonl"))]
    print(f"[train] train files: {train_files}")
    print(f"[train] eval files:  {eval_files}")

    dataset = load_dataset(
        "json",
        data_files={"train": train_files, "eval": eval_files},
    )

    # ── Smoke-run subsampling ─────────────────────────────────────────────────
    # --sample-fraction (SM_HP_SAMPLE_FRACTION) < 1.0 trains on a seeded random
    # slice of the same data, through this same script, the same image and the
    # same backend. The point is to prove the whole path end-to-end — S3
    # channels, dependency resolution, model download, 4-bit load, LoRA attach,
    # MLflow logging, checkpointing, artifact upload — for cents and minutes
    # instead of ~$20 and 6-10h. It does NOT produce a usable model. The
    # default 1.0 leaves full-length training byte-for-byte unchanged.
    if args.sample_fraction < 1.0:
        for split in ("train", "eval"):
            full = len(dataset[split])
            keep = max(1, int(full * args.sample_fraction))
            dataset[split] = dataset[split].shuffle(seed=_SMOKE_SEED).select(range(keep))
            print(f"[train] SMOKE: {split} {full:,} -> {keep:,} ({args.sample_fraction:.4%})")

    # In-training eval is a loss curve, not the EX gate (evaluate.py owns that), and on a
    # 27B every eval pass over a few thousand rows costs tens of minutes of billed GPU.
    if args.eval_max_samples and len(dataset["eval"]) > args.eval_max_samples:
        full = len(dataset["eval"])
        dataset["eval"] = dataset["eval"].shuffle(seed=_SMOKE_SEED).select(range(args.eval_max_samples))
        print(f"[train] eval capped: {full:,} -> {args.eval_max_samples:,}")

    dataset = dataset.map(format_sample, fn_kwargs={"task": args.task}, remove_columns=dataset["train"].column_names)

    # ── Quantization ──────────────────────────────────────────────────────────
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )

    # ── Base model ────────────────────────────────────────────────────────────
    print(f"[train] loading {args.base_model_id} ...")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model_id, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    # --model-class: which transformers Auto class loads the weights. Default is
    # AutoModelForCausalLM; checkpoints that ship as a multimodal wrapper (e.g.
    # Qwen3.8-27B, architecture Qwen3_5ForConditionalGeneration) are better loaded
    # through the class their config names, so no key renaming sits between the
    # checkpoint and the model while the 4-bit quantizer runs.
    model_cls = getattr(transformers, args.model_class)
    model = model_cls.from_pretrained(
        args.base_model_id,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        dtype=torch.bfloat16,  # transformers 5 name; was torch_dtype
    )
    model.config.use_cache = False
    n4 = sum(1 for m in model.modules() if m.__class__.__name__ == "Linear4bit")
    print(f"[train] loaded {type(model).__name__}: footprint {model.get_memory_footprint() / 1e9:.1f} GB, "
          f"Linear4bit modules {n4}, cuda allocated {torch.cuda.memory_allocated() / 1e9:.1f} GB")
    if args.upcast_fp32:
        # Standard QLoRA prep, but it also upcasts every remaining bf16 parameter
        # (embeddings, lm_head) to fp32 — ~10 GB extra on a 27B with a 248k vocab.
        model = prepare_model_for_kbit_training(model)
    else:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    print(f"[train] after prep: cuda allocated {torch.cuda.memory_allocated() / 1e9:.1f} GB")

    # ── LoRA ──────────────────────────────────────────────────────────────────
    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
        # All projection matrices — empirically better for code/SQL generation
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    # ── Logging / eval / checkpoint cadence ───────────────────────────────────
    # The 50/500 defaults below are tuned for the full ~78k-sample run. On a
    # smoke slice they never fire once, so the job would "succeed" without ever
    # having exercised evaluation, checkpointing or MLflow logging — precisely
    # the paths a smoke run exists to test. Derive them from the real optimizer
    # step count instead whenever we're subsampling; leave them untouched at
    # full length.
    steps_per_epoch = max(1, len(dataset["train"]) // (args.batch_size * args.grad_accum))
    total_steps = max(1, steps_per_epoch * args.epochs)
    logging_steps = 50 if args.sample_fraction >= 1.0 else max(1, total_steps // 10)
    # Full length: at most every 500 steps, but at least 8 checkpoints per run so a cut-off
    # job loses an hour or two, not half the run (checkpoints sync to S3, see module.training).
    eval_save_steps = max(1, min(500, total_steps // 8)) if args.sample_fraction >= 1.0 else max(1, total_steps // 2)
    print(f"[train] ~{total_steps} optimizer steps; logging every {logging_steps}, "
          f"eval/save every {eval_save_steps}")

    # ── MLflow ────────────────────────────────────────────────────────────────
    # Set before Trainer construction so transformers' MLflowCallback (triggered
    # by report_to="mlflow" below) picks these up. Whoever launches this job is
    # responsible for setting them — see this module's docstring.
    os.environ.setdefault("MLFLOW_EXPERIMENT_NAME", "alibi-model-a")
    if "MLFLOW_RUN_ID" not in os.environ and "MLFLOW_RUN_NAME" not in os.environ:
        # Prefixed so a smoke run can never be mistaken for a real one in the
        # tracking UI's run list.
        prefix = "" if args.sample_fraction >= 1.0 else "smoke-"
        os.environ["MLFLOW_RUN_NAME"] = f"{prefix}train_qlora-{job_name}"

    # ── Training arguments ────────────────────────────────────────────────────
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    # SFTConfig is TrainingArguments plus the SFT-specific knobs, which moved out
    # of the SFTTrainer constructor in trl 0.12. The sequence-length knob was
    # then renamed max_seq_length -> max_length, so pick whichever this trl has.
    sft_fields = set(inspect.signature(SFTConfig).parameters)
    seq_kwarg = "max_length" if "max_length" in sft_fields else "max_seq_length"
    # warmup_ratio was removed in transformers 5.x: a float < 1 in warmup_steps
    # now means the same thing (3% of total steps).
    warmup_kwargs = {"warmup_ratio": 0.03} if "warmup_ratio" in sft_fields else {"warmup_steps": 0.03}
    training_args = SFTConfig(
        output_dir=str(checkpoint_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=True,
        learning_rate=args.learning_rate,
        lr_scheduler_type="cosine",
        **warmup_kwargs,
        bf16=use_bf16,
        fp16=(not use_bf16 and torch.cuda.is_available()),
        logging_steps=logging_steps,
        # Eval defaults to batch 8, and 8 x 2048 tokens x a 248k vocab is a 15 GiB logits
        # tensor: tp-002 trained 10 steps fine on a 48 GB L40S, then died here. Match the
        # train batch, and keep only the loss (no logits retained across the eval loop).
        per_device_eval_batch_size=args.batch_size,
        prediction_loss_only=True,
        eval_strategy="steps",
        eval_steps=eval_save_steps,
        save_strategy="steps",
        save_steps=eval_save_steps,
        load_best_model_at_end=False,
        dataloader_num_workers=2,
        report_to="mlflow",
        ddp_find_unused_parameters=False,
        dataset_text_field="text",
        **{seq_kwarg: args.max_seq_length},
    )

    # tokenizer= became processing_class= (trl 0.12 / transformers 4.46+).
    tok_kwarg = "processing_class" if "processing_class" in inspect.signature(SFTTrainer).parameters else "tokenizer"
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["eval"],
        peft_config=peft_config,
        **{tok_kwarg: tokenizer},
    )

    # Spot training resume: pick up the latest checkpoint if available
    last_checkpoint = None
    if checkpoint_dir.exists():
        checkpoints = sorted(
            checkpoint_dir.glob("checkpoint-*"),
            key=lambda p: int(p.name.split("-")[1]),
        )
        if checkpoints:
            last_checkpoint = str(checkpoints[-1])
            print(f"[train] resuming from {last_checkpoint}")

    trainer.train(resume_from_checkpoint=last_checkpoint)

    # ── Save adapter ──────────────────────────────────────────────────────────
    print(f"[train] saving adapter → {model_dir}")
    trainer.model.save_pretrained(str(model_dir))
    tokenizer.save_pretrained(str(model_dir))

    (model_dir / "model_config.json").write_text(json.dumps({
        "model_type": "qlora",
        "task": args.task,
        "base_model_id": args.base_model_id,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "lora_dropout": args.lora_dropout,
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "max_seq_length": args.max_seq_length,
        "sample_fraction": args.sample_fraction,
    }, indent=2))

    # ── Metrics ───────────────────────────────────────────────────────────────
    log_history = trainer.state.log_history
    final_eval_loss = next(
        (e["eval_loss"] for e in reversed(log_history) if "eval_loss" in e), None
    )
    save_metrics({
        "model_type": "qlora",
        "base_model_id": args.base_model_id,
        "epochs": args.epochs,
        "sample_fraction": args.sample_fraction,
        "smoke_run": args.sample_fraction < 1.0,
        "global_step": trainer.state.global_step,
        "final_eval_loss": final_eval_loss,
        # EX metrics computed separately by evaluate.py after endpoint deployment
        "spider_execution_accuracy": None,
        "bird_execution_accuracy": None,
    }, str(model_dir), run_id=job_name)

    print(f"[train] done → {model_dir}")


def _hp(name: str, default):
    """Read hyperparameter from SM_HP_<NAME> env var (set by EC2 user-data / SageMaker), fallback to default."""
    raw = os.environ.get(f"SM_HP_{name.upper()}")
    if raw is None:
        return default
    return type(default)(raw)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--task", choices=TASKS, default=_hp("task", "sql"),
                   help="sql = Module A (Text-to-SQL), chart = Module C (Vega-Lite). Selects the prompt "
                        "(common/prompts.py) and the row schema; each task trains its own adapter on the "
                        "same base model.")
    p.add_argument("--base-model-id", default=_hp("base_model_id", "Qwen/Qwen3.8-27B"))
    p.add_argument("--model-class", default=_hp("model_class", "AutoModelForMultimodalLM"),
                   help="transformers Auto class used to load the base model.")
    p.add_argument("--upcast-fp32", type=int, default=_hp("upcast_fp32", 0),
                   help="1 = prepare_model_for_kbit_training (fp32 upcast of non-quantized params); "
                        "0 = only enable gradient checkpointing. Use 0 for very large bases.")
    p.add_argument("--epochs",        type=int,   default=_hp("epochs",        3))
    p.add_argument("--batch-size",    type=int,   default=_hp("batch_size",    1))
    p.add_argument("--grad-accum",    type=int,   default=_hp("grad_accum",   16),
                   help="Gradient accumulation steps. Effective batch = batch_size x grad_accum = 16.")
    p.add_argument("--learning-rate", type=float, default=_hp("learning_rate", 2e-4))
    p.add_argument("--lora-r",        type=int,   default=_hp("lora_r",       16))
    p.add_argument("--lora-alpha",    type=int,   default=_hp("lora_alpha",  32))
    p.add_argument("--lora-dropout",  type=float, default=_hp("lora_dropout", 0.05))
    p.add_argument("--max-seq-length",type=int,   default=_hp("max_seq_length", 2048))
    p.add_argument("--eval-max-samples", type=int, default=_hp("eval_max_samples", 500),
                   help="Cap on eval rows used for the in-training loss curve (0 = no cap).")
    p.add_argument("--sample-fraction", type=float, default=_hp("sample_fraction", 1.0),
                   help="Fraction of the train/eval sets to actually train on. 1.0 (default) "
                        "is a full-length run. 0.001 is the 0.1%% smoke run: identical code, "
                        "data, image and backend, finishing in minutes for cents. Set it via "
                        "SM_HP_SAMPLE_FRACTION, i.e. training_hyperparameters in terraform.tfvars.")
    # parse_known_args, not parse_args: SageMaker script mode invokes this file
    # with the hyperparameter keys verbatim (--learning_rate, --lora_r,
    # --sample_fraction), while the options above are declared in dashed form.
    # argparse would exit(2) on the underscore spellings before training starts.
    # The values still arrive correctly: every default above reads SM_HP_<NAME>,
    # which the training toolkit sets from the same hyperparameters.
    return p.parse_known_args()[0]


if __name__ == "__main__":
    main()
