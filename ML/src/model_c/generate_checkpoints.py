"""
Module C: generate the chart specs of the untuned base and each LoRA checkpoint, in one job.

Generation only — scoring is done afterwards, on a laptop, by `score_predictions.py` against the
backend's own validator (`app.orchestrator.vega`), so the GPU job does not have to carry the backend
and a scoring change never costs a re-generation.

Same shape as `model_a/eval_checkpoints.py` (4-bit base loaded once, each checkpoint a named adapter,
the same rows for every variant, token-budget batching, greedy decoding) and it imports that file's
`load_model` / `generate`, so there is one copy of the code that decides how the models are run.

Channels (SM_CHANNEL_*):
  eval       eval.jsonl   rows {prompt, target, chart_type, db_id, id}   (model_c/prepare_data.py)
  ckpt_<n>   one checkpoint directory per variant; <n> becomes the label (ckpt_s172 -> "s172")

Output (/opt/ml/output/data): predictions_<variant>.jsonl, rows {i, raw}  — i is the row's position in eval.jsonl (the nvBench `id` repeats across
paraphrases), raw the unparsed completion.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from common.prompts import CHART_SYSTEM_PROMPT, chatml  # noqa: E402
from model_a.eval_checkpoints import generate, load_model  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=int(os.environ.get("SM_HP_BATCH_SIZE", 64)))
    p.add_argument("--token-budget", type=int, default=int(os.environ.get("SM_HP_TOKEN_BUDGET", 40000)))
    p.add_argument("--max-new-tokens", type=int, default=int(os.environ.get("SM_HP_MAX_NEW_TOKENS", 768)),
                   help="same ceiling as the endpoint (inference.py, chart)")
    a, _ = p.parse_known_args()

    rows = [json.loads(l) for l in (Path(os.environ["SM_CHANNEL_EVAL"]) / "eval.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    ckpts = {k[len("SM_CHANNEL_CKPT_"):].lower(): Path(v) for k, v in sorted(os.environ.items())
             if k.startswith("SM_CHANNEL_CKPT_")}
    ckpts = {k: p for k, p in ckpts.items() if (p / "adapter_config.json").exists()}
    print(f"[gen] {len(rows)} rows; checkpoints: {list(ckpts)}", flush=True)

    model, tok = load_model(ckpts)
    prompts = [chatml(CHART_SYSTEM_PROMPT, r["prompt"]) for r in rows]  # = the training text minus the target

    out_dir = Path("/opt/ml/output/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    # The last step first, so a cut-off job still has the number that matters; the base last.
    order = sorted(ckpts, key=lambda k: -int(k[1:]) if k[1:].isdigit() else 0) + ["base"]
    unfit_by_variant: dict = {}
    for name in order:
        t0 = time.time()
        print(f"[gen] === {name} ===", flush=True)
        if name == "base":
            with model.disable_adapter():
                raw, unfit = generate(model, tok, prompts, a.batch_size, a.max_new_tokens, a.token_budget)
        else:
            model.set_adapter(name)
            raw, unfit = generate(model, tok, prompts, a.batch_size, a.max_new_tokens, a.token_budget)
        with open(out_dir / f"predictions_{name}.jsonl", "w", encoding="utf-8") as f:
            for i, text in enumerate(raw):
                f.write(json.dumps({"i": i, "raw": text}, ensure_ascii=False) + "\n")
        print(f"GENDONE {name} {len(rows)} rows, unfit {len(unfit)}, {time.time() - t0:.0f}s", flush=True)
        unfit_by_variant[name] = len(unfit)
        (out_dir / "unfit.json").write_text(json.dumps(unfit_by_variant), encoding="utf-8")
    print("[gen] done", flush=True)


if __name__ == "__main__":
    main()
