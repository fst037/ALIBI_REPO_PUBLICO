# Harness evaluation

**Audience:** whoever reruns or extends the comparison between the one-shot
pipeline and the agent harness, including for the thesis write-up.

> `run_eval.py` uses Haiku 4.5 for Module A by default. To measure the harness
> on the fine-tuned Qwen3.8-27B adapters, point its Module A setting at the
> SageMaker path (and its prompt format).

Compares three ways of answering the same 24 questions, end to end through
the app's own API:

| system | what it is |
|---|---|
| `oneshot` | `prepare` + `execute`: Module A writes one query, the narrative node explains it (the product before the agent harness) |
| `quick` | an answer run in Quick mode: the planner loop, ≤ 2 queries, Sonnet 4.6 synthesis |
| `deep` | an answer run in Deep mode: ≤ 6 queries, Opus 4.6 synthesis |

All three use the same Module A (Haiku 4.5 by default), so the
difference measured is the harness, not the SQL model.

## Files

- `data.py` — three seeded synthetic folders (sales; support tickets; employees
  + reviews, two tables) with planted findings.
- `questions.py` — 24 questions (`lookup` / `multi` / `explore`), each with
  reference SQL over the raw CSVs and a rubric.
- `judge.py` — blind, pointwise grading against the reference results
  (Claude Opus 4.5 — none of the compared models).
- `report.py` — the Markdown report; also holds the **assumed** token prices.
- `run_eval.py` — runs, judges, reports. Resumable.
- `results/<name>/` — `runs.jsonl`, `judgments.jsonl`, `report.md`.

`tests/test_eval_set.py` keeps the set honest offline: deterministic data,
every reference query runs, and the planted findings are really in the data.

## Running it

Real Bedrock calls: the full set costs a few US dollars and takes ~30–40 min.
From `backend/`, with AWS credentials (and, on the dev machine behind the TLS
interceptor, the CA bundle):

```bash
AWS_CA_BUNDLE=~/.certs/corp-ca-bundle.pem python -m eval.run_eval --name 2026-10-09
python -m eval.run_eval --name try --only S3,T4 --systems quick,deep   # a subset
python -m eval.run_eval --name 2026-10-09 --report                     # re-render only
```

A rerun with the same `--name` skips answers and judgments already recorded.
To test another budget or model, set the usual `ALIBI_*` variables (e.g.
`ALIBI_HARNESS_QUICK_MAX_SQL=3`, `ALIBI_SYNTHESIS_QUICK_MODEL_ID=…`) and use a
new `--name`.

## Metrics

- **Rubric score** — share of rubric points the judge marked correct (partial =
  ½), over answered questions and over all (a failure scores 0).
- **Usefulness** — the judge's 1–5 rating.
- **Wrong claims** — statements in the answer text the reference contradicts.
  This is what catches a wrong comparison whose figures are all real, which the
  product's grounding validator cannot.
- **Grounded figures** — share of numbers in the answer text found in some
  result a query actually returned (measured by wrapping `query.run_sql`,
  independently of the validator).
- **Latency, queries executed, findings and charts shown, caps hit, cost** —
  cost from measured tokens × the assumed prices in `report.py`.
