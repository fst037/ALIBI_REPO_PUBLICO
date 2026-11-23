"""Evaluation: the one-shot pipeline vs the agent harness (Quick, Deep).

    python -m eval.run_eval --name 2026-10-09            # run, judge, report
    python -m eval.run_eval --name x --only S3,T4 --systems quick,deep
    python -m eval.run_eval --name 2026-10-09 --report   # report only
    python -m eval.run_eval --name x-en --lang en        # the English originals

**Real models, real money.** The models are the ones dev ships with
(terraform/envs/dev): Module A and Module C are the fine-tuned adapters on the
SageMaker endpoint (`model-a3-dev`, which must be deployed; it is woken if
asleep); the planner and Quick's synthesis are Haiku 4.5, Deep's synthesis
Sonnet 4.6; the judge is Opus 4.5, none of those. Every `ALIBI_*` below can be
overridden from the environment (e.g. Module A back on Bedrock with
`ALIBI_TEXT2SQL_PROVIDER=bedrock ALIBI_TEXT2SQL_PROMPT_FORMAT=generic-chat-v1`).
The full set is roughly 72 answers + 72 judgments. Results are
appended to `eval/results/<name>/` as they land and a rerun skips what is
already there, so an interruption never pays twice.

In-process and database-free, exactly like a no-database local run: the app's
own API (`TestClient`), in-memory stores, Parquet in a temp directory. The
three systems are the product's own paths, not re-implementations:

* `oneshot` — `POST …/messages/prepare` then `POST …/messages`: Module A writes
  one query, the narrative node explains it. The product before the agent harness.
* `quick` / `deep` — `POST …/runs` with that mode: the agent harness.

Questions are asked in Spanish by default (`--lang es`, `questions.SPANISH`).

Measured per answer: wall-clock latency; every fine-tuned model call with its
task (`sql` / `chart`) and seconds (by wrapping `app.core.sagemaker.invoke`);
tokens per model (by wrapping the one
Bedrock transport, `app.core.bedrock.converse`); every result any query
returned (by wrapping `query.run_sql`) — which is what "grounded" is checked
against here, independently of the product's own validator; whether a cap cut
the investigation short (by wrapping `harness.investigate`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# ---- environment: set before the app is imported -----------------------------
os.environ["ALIBI_ENV_FILE"] = os.path.join(tempfile.gettempdir(), "alibi-eval-no-env")
os.environ["ALIBI_DATABASE_URL"] = ""
os.environ["ALIBI_OBJECT_STORE"] = "local"
os.environ["ALIBI_INGEST_LOCAL_DIR"] = tempfile.mkdtemp(prefix="alibi-eval-")
os.environ["ALIBI_JOB_RUNNER"] = "thread"
os.environ["ALIBI_RUN_RUNNER"] = "thread"
os.environ["ALIBI_SCHEMA_INFERENCE_PROVIDER"] = "none"
_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# What terraform/envs/dev deploys (variables.tf defaults + terraform.tfvars).
for key, default in {
    "ALIBI_TEXT2SQL_PROVIDER": "sagemaker",
    "ALIBI_TEXT2SQL_PROMPT_FORMAT": "qwen-bird-spider-v1",
    "ALIBI_VISUALIZATION_PROVIDER": "sagemaker",
    "ALIBI_SAGEMAKER_ENDPOINT_NAME": "model-a3-dev",
    "ALIBI_SAGEMAKER_INFERENCE_COMPONENT": "model-a3-dev-models",
    "ALIBI_TEXT2SQL_MODEL_ID": _HAIKU,
    "ALIBI_ORCHESTRATOR_MODEL_ID": _HAIKU,
    "ALIBI_PLANNER_MODEL_ID": _HAIKU,
    "ALIBI_SYNTHESIS_QUICK_MODEL_ID": _HAIKU,
    "ALIBI_SYNTHESIS_DEEP_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
}.items():
    os.environ.setdefault(key, default)
JUDGE_MODEL = os.environ.get("ALIBI_EVAL_JUDGE_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0")

sys.path.insert(0, str(HERE.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.core import bedrock, sagemaker  # noqa: E402
from app.ingest.jobs import get_job_runner  # noqa: E402
from app.main import app  # noqa: E402
from app.orchestrator import harness, validation  # noqa: E402
from app.services import query  # noqa: E402
from app.services.run_jobs import get_run_runner  # noqa: E402
from eval import judge, report  # noqa: E402
from eval.data import folders, reference_connection  # noqa: E402
from eval.questions import QUESTIONS, Question, asked  # noqa: E402

SYSTEMS = ("oneshot", "quick", "deep")

# ---- instrumentation -----------------------------------------------------------
_usage: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # model -> [calls, in, out]
_executed: list[list[dict[str, Any]]] = []
_cut_short: list[str | None] = []
_model_calls: list[dict[str, Any]] = []  # fine-tuned calls: task, seconds, ok

_converse = bedrock.converse
_invoke = sagemaker.invoke
_run_sql = query.run_sql
_investigate = harness.investigate


def _counted_converse(model_id, messages, **kwargs):  # noqa: ANN001, ANN202
    reply = _converse(model_id, messages, **kwargs)
    usage = _usage[model_id]
    usage[0] += 1
    usage[1] += reply.prompt_tokens
    usage[2] += reply.completion_tokens
    return reply


def _recorded_run_sql(sql, tables, **kwargs):  # noqa: ANN001, ANN202
    result = _run_sql(sql, tables, **kwargs)
    _executed.append(result.rows)
    return result


def _timed_invoke(target, payload):  # noqa: ANN001, ANN202
    started = time.monotonic()
    ok = False
    try:
        reply = _invoke(target, payload)
        ok = True
        return reply
    finally:
        _model_calls.append({"task": payload.get("task"), "seconds": round(time.monotonic() - started, 2), "ok": ok})


def _watched_investigate(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
    found = _investigate(*args, **kwargs)
    _cut_short.append(found.cut_short)
    return found


bedrock.converse = _counted_converse
query.run_sql = _recorded_run_sql
harness.investigate = _watched_investigate
sagemaker.invoke = _timed_invoke


def _reset() -> None:
    _usage.clear()
    _executed.clear()
    _cut_short.clear()
    _model_calls.clear()


# ---- setup ----------------------------------------------------------------------


def _login(client: TestClient) -> dict[str, str]:
    # The in-memory auth store signs the fixture user straight in (there is no 2FA step any more).
    session = client.post("/api/v1/auth/login", json={"email": "ana@acme.com", "password": "pw"}).json()
    return {"Authorization": f"Bearer {session['accessToken']}"}


def _serving() -> dict[str, Any]:
    """Which instance type the fine-tuned models ran on: with InstancePools it is SageMaker's pick."""
    if os.environ.get("ALIBI_TEXT2SQL_PROVIDER") != "sagemaker":
        return {"endpoint": None}
    import boto3  # noqa: PLC0415 - only when the endpoint is in use

    name = os.environ["ALIBI_SAGEMAKER_ENDPOINT_NAME"]
    described = boto3.client("sagemaker", region_name=os.environ.get("AWS_REGION", "us-east-1")).describe_endpoint(
        EndpointName=name)
    variants = json.loads(json.dumps(described.get("ProductionVariants") or [], default=str))
    return {"endpoint": name, "variants": variants}


def _upload(client: TestClient, headers: dict[str, str], filename: str, data: bytes) -> str:
    created = client.post("/api/v1/uploads", json={"filename": filename, "sizeBytes": len(data)},
                          headers=headers).json()
    client.put(created["uploadUrl"], content=data, headers=headers).raise_for_status()
    get_job_runner().wait_all(timeout=120)
    upload = client.get(f"/api/v1/uploads/{created['upload']['id']}", headers=headers).json()
    dataset = upload["datasets"][0]
    schema = client.get(f"/api/v1/datasets/{dataset['id']}/schema", headers=headers).json()
    client.put(f"/api/v1/datasets/{dataset['id']}/schema", json={"columns": schema["columns"]},
               headers=headers).raise_for_status()
    return dataset["id"]


def _setup(client: TestClient, headers: dict[str, str]) -> dict[str, str]:
    folder_ids = {}
    for folder in folders():
        dataset_ids = [_upload(client, headers, t.filename, t.csv.encode()) for t in folder.tables]
        created = client.post("/api/v1/folders", json={"name": folder.name, "datasetIds": dataset_ids},
                              headers=headers)
        created.raise_for_status()
        folder_ids[folder.key] = created.json()["id"]
    return folder_ids


# ---- one answer -------------------------------------------------------------------


def _answer_view(result: dict[str, Any]) -> dict[str, Any]:
    """What the user reads: the text, and the tables shown beside it."""
    findings = result.get("findings") or []
    texts = [result["naturalResponse"]] + [f["text"] for f in findings if f.get("text")]
    tables = [f["table"]["rows"] for f in findings] if findings else [result["table"]["rows"]]
    return {"text": "\n\n".join(texts), "tables": tables, "findings": max(1, len(findings)),
            "charts": sum(1 for f in findings if f.get("chart")) if findings else int(bool(result.get("chart"))),
            "trail": result.get("trail") or []}


def _grounded(text: str, question: str) -> tuple[int, int]:
    """(figures in the text, figures found in some executed result)."""
    figures = validation._numbers_in(text)  # noqa: SLF001 - the validator's own number reader
    union = [row for rows in _executed for row in rows]
    problems = validation.check_grounding(
        text, union, question=question,
        extra_values={float(len(rows)) for rows in _executed},
        counting_limit=max((len(rows) for rows in _executed), default=0),
    )
    return len(figures), len(figures) - len(problems)


def _error(response) -> dict[str, Any]:  # noqa: ANN001
    """The API's error envelope, or the raw body when it is not one."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    return error or {"code": f"http_{response.status_code}", "message": response.text[:300]}


def _ask(client: TestClient, headers: dict[str, str], system: str, q: Question, folder_id: str,
         lang: str) -> dict[str, Any]:
    conversation = client.post("/api/v1/conversations", json={"folderId": folder_id}, headers=headers).json()["id"]
    _reset()
    text = asked(q, lang)
    record: dict[str, Any] = {"question": q.id, "system": system, "kind": q.kind, "lang": lang, "asked": text}
    started = time.monotonic()
    if system == "oneshot":
        prepared = client.post(f"/api/v1/conversations/{conversation}/messages/prepare",
                               json={"question": text}, headers=headers)
        body = prepared.json()
        if not prepared.is_success:
            record.update(status="failed", error=_error(prepared))
        elif body.get("status") == "blocked":
            record.update(status="guard", error={"code": "guard", "message": body.get("message")})
        else:
            executed = client.post(f"/api/v1/conversations/{conversation}/messages",
                                   json={"draftId": body["draftId"]}, headers=headers)
            if not executed.is_success:
                record.update(status="failed", error=_error(executed))
            else:
                record.update(status="done", answer=_answer_view(executed.json()["message"]["result"]))
    else:
        started_run = client.post(f"/api/v1/conversations/{conversation}/runs",
                                  json={"question": text, "mode": system}, headers=headers).json()
        get_run_runner().wait_all(timeout=900)
        run = client.get(f"/api/v1/conversations/{conversation}/runs/{started_run['id']}", headers=headers).json()
        if run["status"] == "done" and run.get("message"):
            record.update(status="done", answer=_answer_view(run["message"]["result"]))
        elif run["status"] == "done":
            record.update(status="guard", error={"code": "guard", "message": (run.get("guard") or {}).get("message")})
        else:
            record.update(status="failed", error=run.get("error"))
        record["cut_short"] = _cut_short[-1] if _cut_short else None
    record["seconds"] = round(time.monotonic() - started, 2)
    record["usage"] = {model: list(u) for model, u in _usage.items()}
    record["queries_executed"] = len(_executed)
    record["model_calls"] = list(_model_calls)
    if record["status"] == "done":
        figures, grounded = _grounded(record["answer"]["text"], text)
        record["figures"], record["grounded_figures"] = figures, grounded
    return record


# ---- main ---------------------------------------------------------------------------


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", required=True, help="results directory under eval/results/")
    parser.add_argument("--only", default="", help="comma-separated question ids")
    parser.add_argument("--systems", default=",".join(SYSTEMS))
    parser.add_argument("--report", action="store_true", help="only write the report")
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--lang", default="es", choices=("es", "en"), help="language the questions are asked in")
    args = parser.parse_args()

    out = HERE / "results" / args.name
    out.mkdir(parents=True, exist_ok=True)
    runs_path, judgments_path = out / "runs.jsonl", out / "judgments.jsonl"
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    questions = [q for q in QUESTIONS if not only or q.id in only]

    if not args.report:
        done = {(r["question"], r["system"]) for r in _load(runs_path)}
        todo = [(q, s) for q in questions for s in systems if (q.id, s) not in done]
        if todo:
            client = TestClient(app)
            headers = _login(client)
            folder_ids = _setup(client, headers)
            for i, (q, system) in enumerate(todo, 1):
                record = _ask(client, headers, system, q, folder_ids[q.folder], args.lang)
                if i == 1 or i % 10 == 0:
                    # The serving instance can change if the endpoint scales to zero and back.
                    _append(out / "serving.jsonl", {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **_serving()})
                _append(runs_path, record)
                print(f"[{i}/{len(todo)}] {q.id} {system}: {record['status']} in {record['seconds']}s"
                      + (f" ({(record.get('error') or {}).get('code')})" if record["status"] != "done" else ""),
                      flush=True)

        if not args.skip_judge:
            truth_db = reference_connection()
            by_id = {q.id: q for q in QUESTIONS}
            judged = {(j["question"], j["system"]) for j in _load(judgments_path)}
            for record in _load(runs_path):
                key = (record["question"], record["system"])
                if record["status"] != "done" or key in judged or record["question"] not in by_id:
                    continue
                q = by_id[record["question"]]
                truth = [truth_db.execute(sql).fetchdf().to_dict("records") for sql in q.reference_sql]
                try:
                    verdict = judge.grade(JUDGE_MODEL, record.get("asked", q.text), truth, q.rubric, record["answer"])
                except Exception as err:  # noqa: BLE001 - a failed judgment is retried next run
                    print(f"judge failed for {key}: {err}", flush=True)
                    continue
                _append(judgments_path, {"question": key[0], "system": key[1], **verdict})
                print(f"judged {key[0]} {key[1]}: {verdict['rubric_score']:.2f} / usefulness "
                      f"{verdict.get('usefulness')}", flush=True)

    report_path = out / "report.md"
    report_path.write_text(report.render(_load(runs_path), _load(judgments_path), args.name), encoding="utf-8")
    print(f"report: {report_path}")


if __name__ == "__main__":
    main()
