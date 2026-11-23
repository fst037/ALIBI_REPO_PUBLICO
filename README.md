# ALIBI — Agentic Layered Interactive Business Intelligence

A no-code Business Intelligence platform that lets non-technical users go from a raw file to an interactive visualization using plain language — no SQL, no dashboards, no data engineers required.

Built as the Proyecto Final de Ingeniería (PFI) at Universidad Argentina de la Empresa (UADE).

---

## What it does

A user opens the web app, uploads a CSV, Excel, PDF, Word or JSON file into a folder, asks a question in plain Spanish or English, and gets back an answer: a sentence with the finding, the chart and rows behind it, and the SQL that produced them. Under the hood:

1. **Module B (Zero-ETL)** detects the tables in the file, infers their schema, stores each one as Parquet, and indexes every column as a vector embedding.
2. **Module A (Text-to-SQL)** turns a question into SQL over the folder's tables, checks it against a read-only guardrail, and runs it with DuckDB.
3. **Module C (Auto-Visualization)** writes a Vega-Lite v5 specification for each result worth showing, validated against the official JSON Schema and against the result's real columns, and the browser renders it.
4. An **agent harness** ties them together: a small planner model decides which queries to run, a larger model writes the final answer from the executed results, and every figure in the answer is checked against those results.

---

## Repository structure

```
alibi/
├── frontend/          React + Vite + TypeScript web app (conversational UI, dashboards)
├── backend/           FastAPI app, deployed as AWS Lambda functions
│   ├── app/ingest/    Module B: file parsing, table detection, typing, Parquet
│   ├── app/services/  Domain services (conversations, runs, datasets, billing…)
│   ├── app/orchestrator/  Agent harness, synthesis, Module C (visualize)
│   ├── alembic/       Database migrations
│   └── terraform/     API Lambda and ingestion pipeline modules
├── contracts/         OpenAPI contracts the frontend client is generated from
├── ML/                Fine-tuning code for Modules A and C (SageMaker), eval, datasets
│   ├── src/model_a/   QLoRA fine-tune for Text-to-SQL (BIRD + Spider)
│   ├── src/model_c/   QLoRA fine-tune for Vega-Lite generation (nvBench)
│   ├── src/module_b/  Module B's prompting-based ingestion baseline
│   └── data/raw/      Raw datasets (Spider, BIRD, nvBench) — see ML/data/README.md
└── terraform/         AWS infrastructure: the unified `envs/dev` root and the `envs/database` root
```

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│               Frontend (React, CloudFront)           │
│        Conversation · Dashboards · Auth · Billing    │
└──────────────────────┬───────────────────────────────┘
                       │ HTTPS (API Gateway)
┌──────────────────────▼───────────────────────────────┐
│            Backend (FastAPI on AWS Lambda)           │
│   Agent harness: planner → queries → synthesis       │
│                                                      │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────┐   │
│  │  Module B    │ │  Module A    │ │  Module C   │   │
│  │  Zero-ETL    │ │  Text-to-SQL │ │  Auto-Vis   │   │
│  │  ingestion   │ │  + guardrail │ │  Vega-Lite  │   │
│  └──────┬───────┘ └──────┬───────┘ └──────┬──────┘   │
└─────────┼────────────────┼────────────────┼──────────┘
          │                │                │
     ┌────▼──────┐   ┌─────▼──────┐   ┌─────▼─────┐
     │ S3 Parquet│   │ PostgreSQL │   │ Vega-Lite │
     │ (DuckDB)  │   │ + pgvector │   │ JSON spec │
     └───────────┘   └────────────┘   └───────────┘
```

Row data lives as Parquet in S3 and is queried with DuckDB; PostgreSQL (RDS) holds everything else — users, folders and dataset metadata, conversations, dashboards, and the column embeddings in `pgvector`.

The fine-tuned adapters for Modules A and C are served from a SageMaker real-time endpoint that scales to zero. Every other model call — the planner, the synthesis, Module B's semantic naming, the embeddings — goes to **Amazon Bedrock** with IAM auth. Model ids are configuration, not code.

A question runs as an **asynchronous run**: the API records it, a worker Lambda answers it, and the frontend polls for the result, so a multi-step answer is not bound by API Gateway's 29-second limit.

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Frontend | React · Vite · TypeScript · React Router · vega-embed · Phosphor Icons |
| Backend | Python 3.11 · FastAPI · Mangum · AWS Lambda behind API Gateway |
| Agent harness | Bedrock (Converse API) — a small planner model; Claude Haiku (Quick) or Sonnet (Deep) for the synthesis |
| Module A model | Qwen3.8-27B + QLoRA adapter (BIRD + Spider) on SageMaker |
| Module C model | Same base, separate QLoRA adapter (nvBench), on the same endpoint |
| Module B | Deterministic parsers and rules + Bedrock (Claude Haiku + Titan embeddings) for column naming and layout arbitration only |
| Data plane | Parquet in S3, queried with DuckDB |
| Database | RDS PostgreSQL (`db.t4g.micro`) + pgvector |
| ML training | SageMaker Training Jobs · Hugging Face Transformers · PEFT · MLflow (local file store) |
| Payments | Mercado Pago Checkout Pro |
| Infrastructure | Terraform · GitHub Actions (OIDC) |

---

## Modules

### Module A — Text-to-SQL

QLoRA adapter on Qwen3.8-27B, trained on the [BIRD](https://bird-bench.github.io/) and [Spider](https://yale-nlp.github.io/spider/) benchmarks.

- Input: a natural-language sub-question from the planner, the folder's schema, and optional BIRD-style hints
- Output: one read-only SQL query, executed with DuckDB over the folder's Parquet
- Guardrail: `sqlglot`-based parser that rejects anything that is not a pure `SELECT` before it runs
- Target: ≥ 60% Execution Accuracy on BIRD-dev, ≥ 80% on Spider-dev

### Module B — Zero-ETL Ingestion

Deterministic-first parsing pipeline with LLM-assisted semantic enrichment.

- Input: CSV, TSV, Excel (including several tables in one sheet), PDF (including embedded tables), Word, JSON
- Output: **one detected table = one dataset** (an upload can yield several), stored as Parquet with its inferred schema and a per-column embedding
- Deterministic rules handle encoding, delimiters, header detection, multi-table sheets, es-AR numbers and dates, and typing; a model is consulted only for column names and descriptions, and to arbitrate an ambiguous header row — never to override what the rules decided
- Asynchronous: a background worker parses each upload, and the raw document is deleted once its Parquet exists
- A dataset can be updated with a new file (append or replace) as long as its structure matches

### Module C — Auto-Visualization

QLoRA adapter trained on [nvBench](https://github.com/TsinghuaDatabaseGroup/nvBench).

- Input: the result's column profile, the question, and the chart form the planner asked for (line, bar, pie, scatter)
- Output: a Vega-Lite v5 JSON specification without data (never executable code)
- Validated against the official Vega-Lite JSON Schema and the result's real columns; if no spec validates, the answer has no chart and shows the table instead
- Target: ≥ 90% schema validity, ≥ 70% chart-type accuracy on nvBench-dev

### Agent harness

A planner model works in a loop, one action per turn: ask Module A a question, look at a result it already holds, or finish with the findings worth showing. Results are held server-side and passed to models by reference (a digest, a few sample rows and a handle), under per-mode caps on queries, turns and tokens. Each query is labeled *context* (to understand the data) or *insight* (a finding); only insights are shown with their chart, and the full trail is available under "Cómo llegué a esto". **Quick** answers use fewer steps; **Deep**, a Pro feature, uses more steps and a larger synthesis model.

---

## ML training

See [`ML/README.md`](ML/README.md) for the training pipeline and [`ML/data/README.md`](ML/data/README.md) for the datasets.

| Job | Dataset | Instance |
|-----|---------|----------|
| Module A fine-tune | BIRD + Spider | ml.g5.2xlarge |
| Module C fine-tune | nvBench | ml.g5.2xlarge |

---

## Infrastructure

All AWS resources are managed with Terraform and deployed from GitHub Actions (`workflow_dispatch`):

- `terraform/envs/dev` — the unified root: backend, frontend and ML core (storage, IAM, billing, ingestion), applied by `infra-deploy.yml`
- `terraform/envs/database` — RDS PostgreSQL, the SSM bastion and the RDS auto-stop, applied by `db-deploy.yml`
- `ML/terraform/envs/dev` — SageMaker training and inference, driven by `ml-train.yml` / `ml-deploy.yml` / `ml-destroy.yml`

Code-only changes are shipped by `backend-deploy.yml` and `frontend-deploy.yml`. The database stops itself after an hour idle and wakes on the next request.

---

## Security model

ALIBI treats every user query as potentially adversarial:

1. **SQL guardrail** — generated SQL is parsed with `sqlglot` before execution. Any statement that is not a pure `SELECT` (no `DROP`, `DELETE`, `INSERT`, `UPDATE`, `CREATE`, etc.) is rejected and never runs.
2. **Isolated data plane** — queries run in DuckDB over the user's own Parquet files only, never against the application database.
3. **Quota controls** — a row limit and an execution timeout on every query, and per-plan monthly query quotas.

The visualization module returns only a Vega-Lite JSON object — no code generated by a model is ever executed.

---

## Scope

In scope:
- CSV, Excel, PDF, Word and JSON ingestion
- Natural-language questions in Spanish and English
- Conversations and dashboards over a folder of datasets
- Web interface on desktop and mobile browsers

Out of scope:
- Real-time / streaming data (Kafka, CDC pipelines)
- NoSQL databases (MongoDB, Cassandra, graph engines)
- Native mobile apps or offline mode
- ERP/CRM connectors (SAP, Salesforce, Power BI)
- AutoML
- Formal compliance certifications (SOC 2, ISO 27001, GDPR)

---

## Academic context

This project is the Proyecto Final de Ingeniería (PFI) submitted to UADE's School of Engineering.

Key benchmarks:
- **BIRD** (Li et al., 2023) — Text-to-SQL with real-world dirty data and external knowledge
- **Spider** (Yu et al., 2018) — Cross-domain Text-to-SQL generalization
- **nvBench** (Luo et al., 2021) — Natural language to Vega-Lite specification
