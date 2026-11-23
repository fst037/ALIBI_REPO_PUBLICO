"""
Compare Module A checkpoints (and the untuned base) by Execution Accuracy, in one job.

Runs as a SageMaker training job (script mode) so it needs no endpoint: the 4-bit base is
loaded ONCE, then each LoRA checkpoint is attached as a named adapter and scored on the SAME
rows. The untuned base (adapters disabled, same prompt) is scored first as the reference.
Default is the FULL BIRD dev (1,534) and Spider dev (1,034) sets.

Channels (SM_CHANNEL_*):
  eval     eval.jsonl         rows {schema, question, sql, db_id, source}
  bird     dev_databases/     <db_id>/<db_id>.sqlite
  spider   database/          <db_id>/<db_id>.sqlite
  ckpt_<name>                 one checkpoint directory each (adapter_config.json + weights);
                              <name> becomes the variant's label, e.g. ckpt_s263 -> "s263"

Scoring (both are reported for every row; `ex` is the headline for its benchmark):
  bird_dev    ex_set     pred and gold return the same SET of rows (BIRD's official check)
  spider_dev  ex_spider  Spider's execution check: columns matched up to permutation, rows
                         compared as a multiset, order-sensitive only if gold has ORDER BY
A query that errors or runs past 30 s is a miss. A gold query that fails is counted in
`gold_failed` and stays in the denominator (a miss), never silently dropped.

Output (/opt/ml/output/data): metrics.json (per-variant scores, bootstrap 95% CIs, paired
differences against the reference variants), predictions_<variant>.jsonl.
"""

import argparse
import json
import os

# Before CUDA initialises (torch is imported lazily below): less fragmentation on a 48 GB card, as in train_qlora.py.
os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import random
import re
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from common.prompts import render_inference_prompt  # noqa: E402

QUERY_TIMEOUT_S = 30
_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)


# ── execution and result comparison ──────────────────────────────────────────

def run_sql(db_path: str, sql: str):
    """List of row tuples (query order), or None on any error / timeout."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        deadline = time.monotonic() + QUERY_TIMEOUT_S
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100000)
        try:
            return [tuple(r) for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()
    except Exception:
        return None


def same_set(pred: list, gold: list) -> bool:
    """BIRD's check: identical SETS of rows."""
    return set(pred) == set(gold)


def same_spider(pred: list, gold: list, gold_sql: str) -> bool:
    """Spider's execution check: match columns up to permutation, then compare the rows as a
    multiset, or as a list when the gold query sorts."""
    if len(pred) != len(gold):
        return False
    if not gold:
        return True
    n = len(gold[0])
    if len(pred[0]) != n:
        return False

    def canon(rows):
        keys = [tuple(sorted(repr(r[i]) for r in rows)) for i in range(n)]
        order = sorted(range(n), key=lambda i: keys[i])
        return [tuple(repr(r[i]) for i in order) for r in rows], sorted(keys)

    (p_rows, p_keys), (g_rows, g_keys) = canon(pred), canon(gold)
    if p_keys != g_keys:
        return False
    if _ORDER_BY.search(gold_sql):
        return p_rows == g_rows
    return Counter(p_rows) == Counter(g_rows)


def clean_sql(text: str) -> str:
    text = text.split("<|im_end|>")[0].strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()
    return text.split(";")[0].strip()


# ── model ────────────────────────────────────────────────────────────────────

def load_model(ckpts: dict):
    import torch
    import transformers
    from peft import PeftModel
    from transformers import AutoConfig, AutoTokenizer, BitsAndBytesConfig

    first = next(iter(ckpts))
    base_id = json.loads((ckpts[first] / "adapter_config.json").read_text())["base_model_name_or_path"]
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                               bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    print(f"[eval] loading {base_id} 4-bit, adapters={list(ckpts)}", flush=True)
    tok = AutoTokenizer.from_pretrained(str(ckpts[first]), trust_remote_code=True)
    tok.pad_token = tok.pad_token or tok.eos_token
    tok.padding_side = "left"  # generation needs the prompts right-aligned
    arch = (AutoConfig.from_pretrained(base_id, trust_remote_code=True).architectures or [""])[0]
    cls = "AutoModelForMultimodalLM" if "ConditionalGeneration" in arch else "AutoModelForCausalLM"
    base = getattr(transformers, cls).from_pretrained(base_id, quantization_config=quant, device_map="auto",
                                                      dtype=torch.bfloat16, trust_remote_code=True)
    model = PeftModel.from_pretrained(base, str(ckpts[first]), adapter_name=first)
    for name, path in ckpts.items():
        if name != first:
            model.load_adapter(str(path), adapter_name=name)
    model.eval()
    print(f"[eval] ready, cuda allocated {torch.cuda.memory_allocated() / 1e9:.1f} GB", flush=True)
    return model, tok


def generate(model, tok, prompts: list[str], batch_size: int, max_new: int, token_budget: int):
    """Greedy generation, longest prompts first. Returns (texts, unfit): `unfit` lists the prompts that
    never produced an output (their text is ""), and the caller must report them — a silent empty
    reply scores as a miss and would bias the comparison.

    A batch is capped by `batch_size` prompts AND by `token_budget` tokens counting the prompt plus
    the longest reply allowed (prompts run ~250-2,400 tokens). One that still runs out of memory is
    split in half and retried down to a single prompt. Two things keep that retry honest:
      * the retry runs OUTSIDE the `except` block, and the batch's tensors live in `attempt()`'s
        frame — inside the handler the exception's traceback keeps them alive, every retry then has
        less memory than the first try, and the OOM cascades to prompts that would have fit
        (this lost 16-48 prompts per variant in the first Module A comparison);
      * whatever is still unfit after the whole pass is retried once, one at a time, on a cleaned GPU.
    """
    import gc

    import torch

    stop = [tok.eos_token_id]
    im_end = tok.convert_tokens_to_ids("<|im_end|>")
    if isinstance(im_end, int) and im_end >= 0 and im_end not in stop:
        stop.append(im_end)

    lengths = [len(x) for x in tok(prompts)["input_ids"]]
    order = sorted(range(len(prompts)), key=lambda i: -lengths[i])
    out = [""] * len(prompts)

    batches, cur = [], []
    for i in order:
        if cur and (len(cur) + 1 > batch_size or (len(cur) + 1) * (lengths[cur[0]] + max_new) > token_budget):
            batches.append(cur)
            cur = []
        cur.append(i)
    if cur:
        batches.append(cur)

    def attempt(idx: list[int]) -> list[str]:
        enc = tok([prompts[i] for i in idx], return_tensors="pt", padding=True).to(model.device)
        with torch.inference_mode():
            ids = model.generate(**enc, max_new_tokens=max_new, do_sample=False,
                                 pad_token_id=tok.pad_token_id, eos_token_id=stop)
        return [tok.decode(row[enc["input_ids"].shape[1]:], skip_special_tokens=True) for row in ids]

    def run(idx: list[int], unfit: list[int]) -> None:
        try:
            texts = attempt(idx)
        except torch.cuda.OutOfMemoryError:
            texts = None
        if texts is not None:
            for i, text in zip(idx, texts):
                out[i] = text
            return
        gc.collect()
        torch.cuda.empty_cache()
        if len(idx) == 1:
            unfit.append(idx[0])
            return
        print(f"[eval]   OOM at {len(idx)} prompts (~{lengths[idx[0]]} tokens each): splitting", flush=True)
        half = len(idx) // 2
        run(idx[:half], unfit)
        run(idx[half:], unfit)

    unfit: list[int] = []
    t0, done = time.time(), 0
    print(f"[eval]   {len(batches)} batches, budget {token_budget} tokens (prompt + {max_new} new), "
          f"up to {batch_size} prompts", flush=True)
    for n, idx in enumerate(batches, 1):
        run(idx, unfit)
        done += len(idx)
        if n % 5 == 0 or n == len(batches):
            print(f"[eval]   batch {n}/{len(batches)}: {done}/{len(prompts)} generated, "
                  f"{time.time() - t0:.0f}s, unfit {len(unfit)}", flush=True)

    if unfit:
        print(f"[eval]   retrying {len(unfit)} unfit prompt(s) one at a time", flush=True)
        again, unfit = unfit, []
        for i in again:
            run([i], unfit)
    if unfit:
        print(f"[eval]   WARNING {len(unfit)} prompt(s) did not fit even alone and score as misses", flush=True)
    return out, unfit


# ── scoring ──────────────────────────────────────────────────────────────────

def score(rows: list[dict], preds: list[str], dbs: dict, gold_cache: dict, pool) -> list[dict]:
    def one(args):
        row, pred = args
        path = str(dbs[row["source"]] / row["db_id"] / f"{row['db_id']}.sqlite")
        key = (path, row["sql"])
        if key not in gold_cache:
            gold_cache[key] = run_sql(path, row["sql"])
        gold = gold_cache[key]
        got = run_sql(path, pred) if pred else None
        ok = got is not None and gold is not None
        return {"executes": got is not None, "gold_failed": gold is None,
                "ex_set": ok and same_set(got, gold),
                "ex_spider": ok and same_spider(got, gold, row["sql"])}

    results = list(pool.map(one, zip(rows, preds)))
    for r, x in zip(rows, results):  # the headline metric for each benchmark
        x["correct"] = x["ex_set"] if r["source"] == "bird_dev" else x["ex_spider"]
    return results


def summarise(rows: list[dict], results: list[dict]) -> dict:
    out = {}
    for src in sorted({r["source"] for r in rows}) + ["all"]:
        sel = [x for r, x in zip(rows, results) if src == "all" or r["source"] == src]
        n = len(sel)
        out[src] = {"n": n, "ex": round(sum(x["correct"] for x in sel) / n, 4),
                    "ex_set": round(sum(x["ex_set"] for x in sel) / n, 4),
                    "ex_spider": round(sum(x["ex_spider"] for x in sel) / n, 4),
                    "executes": round(sum(x["executes"] for x in sel) / n, 4),
                    "gold_failed": sum(x["gold_failed"] for x in sel)}
    return out


def bootstrap(rows: list[dict], correct: dict, reference: list[str], draws: int = 2000) -> dict:
    """95% CIs by resampling QUESTIONS, with the same resample for every variant so the
    differences are paired: per-variant EX per benchmark, and each variant minus each reference."""
    import numpy as np

    rng = np.random.default_rng(42)
    src = np.array([r["source"] for r in rows])
    mat = {k: np.array(v, dtype=float) for k, v in correct.items()}
    groups = {"all": np.arange(len(rows))}
    for name in sorted(set(src)):
        groups[name] = np.where(src == name)[0]

    def ci(x):
        lo, hi = np.percentile(x, [2.5, 97.5])
        return [round(float(lo), 4), round(float(hi), 4)]

    out: dict = {"draws": draws, "ex": {}, "diff": {}}
    for g, idx in groups.items():
        picks = rng.integers(0, len(idx), size=(draws, len(idx)))
        means = {k: v[idx][picks].mean(axis=1) for k, v in mat.items()}
        for k, m in means.items():
            out["ex"].setdefault(k, {})[g] = ci(m)
        for ref in reference:
            if ref not in means:
                continue
            for k, m in means.items():
                if k != ref:
                    d = m - means[ref]
                    out["diff"].setdefault(f"{k} - {ref}", {})[g] = {
                        "mean": round(float(mat[k][idx].mean() - mat[ref][idx].mean()), 4),
                        "ci95": ci(d), "p_better": round(float((d > 0).mean()), 4)}
    return out


def main():
    a = parse_args()
    ch = lambda n: Path(os.environ[f"SM_CHANNEL_{n.upper()}"])  # noqa: E731
    rows = [json.loads(l) for l in (ch("eval") / "eval.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.per_source:
        rng = random.Random(42)
        keep = []
        for src in sorted({r["source"] for r in rows}):
            pool = [r for r in rows if r["source"] == src]
            keep += rng.sample(pool, min(a.per_source, len(pool)))
        rows = keep
    print(f"[eval] {len(rows)} rows: { {s: sum(r['source'] == s for r in rows) for s in {r['source'] for r in rows}} }", flush=True)

    dbs = {"bird_dev": ch("bird"), "spider_dev": ch("spider")}
    ckpts = {k[len("SM_CHANNEL_CKPT_"):].lower(): Path(v) for k, v in sorted(os.environ.items())
             if k.startswith("SM_CHANNEL_CKPT_")}
    ckpts = {k: p for k, p in ckpts.items() if (p / "adapter_config.json").exists()}
    print(f"[eval] checkpoints: {list(ckpts)}", flush=True)

    model, tok = load_model(ckpts)
    prompts = [render_inference_prompt("sql", {"schema": r["schema"], "question": r["question"]}) for r in rows]

    try:
        from model_a.guardrail import validate_sql
    except Exception:  # sqlglot missing: skip the guardrail column rather than fail the comparison
        validate_sql = None

    out_dir = Path("/opt/ml/output/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics: dict = {"rows": len(rows), "per_source": a.per_source, "variants": {}}
    correct: dict = {}
    gold_cache: dict = {}
    pool = ThreadPoolExecutor(max_workers=4)

    # The shipped candidate (the last step) first, so a cut-off job still has the number that matters.
    order = ["base"] + sorted(ckpts, key=lambda k: (k != "s2110", int(k[1:]) if k[1:].isdigit() else 0))
    for name in order:
        t0 = time.time()
        print(f"[eval] === {name} ===", flush=True)
        if name == "base":
            with model.disable_adapter():
                raw, unfit = generate(model, tok, prompts, a.batch_size, a.max_new_tokens, a.token_budget)
        else:
            model.set_adapter(name)
            raw, unfit = generate(model, tok, prompts, a.batch_size, a.max_new_tokens, a.token_budget)
        preds = [clean_sql(t) for t in raw]
        results = score(rows, preds, dbs, gold_cache, pool)
        summ = summarise(rows, results)
        if validate_sql:
            summ["guardrail_pass"] = round(sum(validate_sql(p)[0] for p in preds if p) / len(preds), 4)
        summ["unfit"] = len(unfit)  # prompts with no output at all, scored as misses; must be 0 for a clean comparison
        summ["seconds"] = round(time.time() - t0)
        metrics["variants"][name] = summ
        correct[name] = [x["correct"] for x in results]
        with open(out_dir / f"predictions_{name}.jsonl", "w", encoding="utf-8") as f:
            for r, p, x in zip(rows, preds, results):
                f.write(json.dumps({"source": r["source"], "db_id": r["db_id"], "question": r["question"],
                                    "gold": r["sql"], "pred": p, **x}, ensure_ascii=False) + "\n")
        if len(correct) > 1:  # recomputed as variants arrive, so a cut-off job keeps its intervals
            metrics["bootstrap"] = bootstrap(rows, correct, [v for v in ("base", "s2110") if v in correct])
        (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(f"EXRESULT {name} " + json.dumps(summ), flush=True)

    print("[eval] done", flush=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--per-source", type=int, default=int(os.environ.get("SM_HP_PER_SOURCE", 0)),
                   help="rows per benchmark (seeded sample); 0 = all of BIRD dev and Spider dev (default)")
    p.add_argument("--batch-size", type=int, default=int(os.environ.get("SM_HP_BATCH_SIZE", 16)))
    p.add_argument("--token-budget", type=int, default=int(os.environ.get("SM_HP_TOKEN_BUDGET", 40000)),
                   help="max padded prompt tokens per batch (16 x 2,400 ran fine at batch 16 on 48 GB)")
    p.add_argument("--max-new-tokens", type=int, default=int(os.environ.get("SM_HP_MAX_NEW_TOKENS", 400)))
    a, _ = p.parse_known_args()
    return a


if __name__ == "__main__":
    main()
