"""Load test of the fine-tuned models' endpoint: how answer time degrades with concurrency.

    python -m eval.load_endpoint --name 2026-11-23            # levels 1,2,4,8
    python -m eval.load_endpoint --name x --levels 1,4 --per-level 16

**Why the endpoint and not the whole API.** Everything else an answer touches
scales on its own: API Gateway and the API/worker Lambdas run one request per
instance, Bedrock serves the planner and the synthesis under its account quotas.
The fine-tuned endpoint does not: it is one copy of the 27B on one GPU
(`max_instance_count = 1`, ML/terraform/modules/inference), which generates one
request at a time. That is where concurrent users queue, so that is what this
measures.

Each request is the product's own call — `app.core.sagemaker.invoke` with the
`sql` task Module A gets (`app/services/text2sql`), over the eval's sales schema
in the training layout — so the payload and the transport are what production
sends. At each concurrency level, `per-level` requests are sent by that many
threads at once; the report gives p50/p95/max latency, failures (SageMaker cuts
an invocation at 60 s) and throughput. Requests are real GPU time on a billed
endpoint; a default run is ~60 calls, a few minutes.

The endpoint must be awake (`ml-deploy.yml`, or any question in the app).
Results: `eval/results/<name>/load.jsonl` and `load.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

os.environ.setdefault("ALIBI_ENV_FILE", os.devnull)
os.environ.setdefault("ALIBI_SAGEMAKER_ENDPOINT_NAME", "model-a3-dev")
os.environ.setdefault("ALIBI_SAGEMAKER_INFERENCE_COMPONENT", "model-a3-dev-models")

from app.core import sagemaker  # noqa: E402

# The eval's sales table (eval/data.py) in the layout Module A was trained on:
# Spider/BIRD multi-line CREATE TABLE with SQLite type names.
SCHEMA = """CREATE TABLE ventas_2024 (
  region TEXT,
  product TEXT,
  channel TEXT,
  month TEXT,
  units INTEGER,
  amount REAL
)"""

# Spider-style questions, as the planner writes them for Module A.
QUESTIONS = [
    "What is the total amount for each region?",
    "Which product has the highest total units?",
    "What is the total amount for each month?",
    "What is the total units for each channel?",
    "What is the total amount for each region and product?",
    "How many rows have channel equal to 'Online'?",
    "What is the average amount for each product?",
    "What is the total units for each month for product 'Termo'?",
]


def _one(target: sagemaker.Target, question: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        reply = sagemaker.invoke(target, {"task": "sql", "schema": SCHEMA, "question": question})
        ok, error = bool(reply.get("sql")), None
    except Exception as err:  # noqa: BLE001 - every failure is a data point here
        ok, error = False, f"{type(err).__name__}: {str(err)[:200]}"
    return {"question": question, "seconds": round(time.monotonic() - started, 2), "ok": ok, "error": error}


def _level(target: sagemaker.Target, concurrency: int, per_level: int) -> dict[str, Any]:
    questions = [QUESTIONS[i % len(QUESTIONS)] for i in range(per_level)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        calls = list(pool.map(lambda q: _one(target, q), questions))
    wall = time.monotonic() - started
    ok = [c["seconds"] for c in calls if c["ok"]]
    q = statistics.quantiles(ok, n=20) if len(ok) >= 2 else ok * 19 or [0.0] * 19
    return {
        "concurrency": concurrency,
        "requests": per_level,
        "ok": len(ok),
        "failed": per_level - len(ok),
        "p50": round(statistics.median(ok), 2) if ok else None,
        "p95": round(q[18], 2) if ok else None,
        "max": round(max(ok), 2) if ok else None,
        "wall_seconds": round(wall, 2),
        "answers_per_minute": round(len(ok) / wall * 60, 2) if wall else None,
        "errors": sorted({c["error"] for c in calls if c["error"]}),
        "calls": calls,
    }


def _serving(endpoint: str) -> list[dict[str, Any]]:
    import boto3  # noqa: PLC0415

    described = boto3.client("sagemaker", region_name=os.environ.get("AWS_REGION", "us-east-1")).describe_endpoint(
        EndpointName=endpoint)
    return json.loads(json.dumps(described.get("ProductionVariants") or [], default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", required=True)
    parser.add_argument("--levels", default="1,2,4,8")
    parser.add_argument("--per-level", type=int, default=16)
    args = parser.parse_args()

    target = sagemaker.target_from_settings()
    if target is None:
        raise SystemExit("no SageMaker endpoint configured (ALIBI_SAGEMAKER_ENDPOINT_NAME)")
    out = HERE / "results" / args.name
    out.mkdir(parents=True, exist_ok=True)

    print("warming up…", flush=True)
    warm = _one(target, QUESTIONS[0])
    if not warm["ok"]:
        raise SystemExit(f"the endpoint did not answer: {warm['error']}")
    serving = _serving(target.endpoint_name)

    results = []
    for level in [int(x) for x in args.levels.split(",")]:
        result = _level(target, level, args.per_level)
        results.append(result)
        with (out / "load.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **result}, ensure_ascii=False) + "\n")
        print(f"concurrency {level}: p50 {result['p50']}s p95 {result['p95']}s max {result['max']}s, "
              f"{result['failed']} failed, {result['answers_per_minute']}/min", flush=True)

    lines = [
        f"# Endpoint load test — {args.name}",
        "",
        f"Endpoint `{target.endpoint_name}`, component `{target.inference_component}`; serving variant:",
        "",
        "```json",
        json.dumps(serving, indent=2),
        "```",
        "",
        f"`sql` task over the eval's sales schema, {args.per_level} requests per level.",
        "",
        "| concurrent | ok | failed | p50 s | p95 s | max s | answers/min |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(f"| {r['concurrency']} | {r['ok']} | {r['failed']} | {r['p50']} | {r['p95']} | {r['max']} "
                     f"| {r['answers_per_minute']} |")
    errors = sorted({e for r in results for e in r["errors"]})
    if errors:
        lines += ["", "Errors:", *[f"- `{e}`" for e in errors]]
    (out / "load.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {out / 'load.md'}")


if __name__ == "__main__":
    main()
