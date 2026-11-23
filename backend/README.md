# ALIBI backend

FastAPI application serving the ALIBI API: file ingestion (Module B,
`app/ingest/` + `app/services/ingestion.py`), Text-to-SQL (Module A,
`app/services/text2sql/`) and chart generation (Module C,
`app/orchestrator/nodes/visualize.py`), driven by the agent harness in
`app/orchestrator/`. Runs as a single AWS Lambda function behind an API Gateway HTTP API —
same serverless-first approach as the ML Lambda pipeline
(`ML/terraform/modules/*-lambda`).

## Live API docs

FastAPI generates OpenAPI/Swagger docs automatically from the route type
hints and Pydantic models — nothing extra to maintain:

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- Raw OpenAPI schema: `/openapi.json`

## Local development

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements-dev.txt -r requirements-ingest.txt
uvicorn app.main:app --reload
```

Both requirements files: `ALIBI_JOB_RUNNER` defaults to `thread`, so the
ingestion pipeline (Module B) runs in-process under uvicorn, and needs the
parsers (`requirements-ingest.txt`: pandas, pyarrow, openpyxl, pdfplumber,
python-docx) even for local API-only work. See that file's header comment.

Then open http://localhost:8000/docs.

With no `ALIBI_DATABASE_URL` set, this runs against the in-memory fixtures —
no database needed. To run against the real database instead (open the
SSM tunnel through the bastion first, see `db/README.md`), set `ALIBI_DATABASE_URL` in
`backend/.env` and **drop `--reload`**:

```bash
uvicorn app.main:app --port 8000
```

On Windows, `--reload` has been observed to silently fail to pick up
`ALIBI_DATABASE_URL` from `.env` (falls back to fixture mode with no error —
confirmed via A/B testing, root cause not fully isolated; likely an
interaction between the `--reload` supervisor's subprocess spawning and
`python-dotenv`'s relative-path `.env` lookup). Costs you auto-restart on
file changes; for fixture-only work `--reload` is unaffected and fine to use.

Set `ALIBI_AUTO_MIGRATE=1` too and the app runs `alembic upgrade head` on
every startup — convenient after pulling someone else's new migration, so
you don't need a separate manual step. See `db/README.md` for the full
Alembic workflow (adding tables, applying migrations against the shared DB).

## Tests

```bash
pytest
```

## Project layout

```
backend/
├── app/
│   ├── main.py            # FastAPI app, CORS, Mangum Lambda handler
│   ├── core/config.py     # pydantic-settings, env-driven
│   ├── api/v1/             # versioned routers; add new modules under routes/
│   ├── ingest/             # Module B: the parsing/typing/profiling pipeline (pure library)
│   ├── services/           # domain logic incl. ingestion.py (Module B's orchestrator), query.py (DuckDB)
│   ├── repositories/        # Postgres access per domain (uploads, folders, datasets, ...)
│   ├── schemas/             # Pydantic wire models
│   └── db/models.py         # SQLModel tables; source of truth for Alembic migrations
├── alembic/versions/        # e.g. 0003_module_b.py
├── tests/
├── Dockerfile.processor      # Module B stage-1 processor container image
└── terraform/               # module code only (api-lambda, ingest-pipeline) — composed by ../../terraform/envs/dev
```

Each new backend module gets its own router under `app/api/v1/routes/` and
its own file under `tests/`, registered in `app/api/v1/router.py`.
Domain/business logic that isn't a thin route handler belongs in a sibling
`app/services/<domain>.py` module — Module B's ingestion pipeline
(`app/ingest/`) and orchestrator (`app/services/ingestion.py`) are the
existing example to follow.

## Deploy (AWS)

The module here (`terraform/modules/api-lambda`) provisions the Lambda
function, its execution role, and an API Gateway HTTP API, but it's composed
into the **unified Terraform root** at `../terraform/envs/dev` alongside
frontend and ML-core — there is no standalone `backend/terraform/envs/dev`
anymore. Day-to-day deploy/destroy is click-button via GitHub Actions
(`infra-deploy.yml` / `infra-destroy.yml`, both `workflow_dispatch`), not
local `terraform apply`. Code-only pushes to `main`/`dev` touching
`backend/**` trigger `.github/workflows/backend-deploy.yml`, which builds the
dependency-bundled zip and updates the already-provisioned Lambda's code —
it does not touch Terraform at all.

See `terraform/envs/dev` (the unified Terraform root) for the full picture.
