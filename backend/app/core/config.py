import os
from enum import Enum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ObjectStoreKind(str, Enum):
    """Where the data plane (Parquet tables) lives."""

    local = "local"
    s3 = "s3"


class JobRunnerKind(str, Enum):
    """How an ingestion job is executed after the upload lands."""

    thread = "thread"
    lambda_ = "lambda"


class SchemaInferenceProvider(str, Enum):
    """Who does the *semantic* half of schema inference.

    `none` is deterministic rules only — the whole pipeline works, it just
    doesn't rename `fch_vcto` to `fecha_vencimiento` or produce embeddings.
    `bedrock` adds those, via a chat model and an embedding model on AWS Bedrock.

    A future `sagemaker` member points the same interface at Module B's trained
    QLoRA adapter once it is deployed from `ML/` — which is precisely why the
    inference step sits behind an interface instead of being called inline.
    """

    none = "none"
    bedrock = "bedrock"


class ModelProvider(str, Enum):
    """Where Module A's SQL or Module C's chart comes from.

    `bedrock` is a general chat model, prompted. `sagemaker` is the fine-tuned
    QLoRA adapter (ML/), served by the one SageMaker endpoint that carries both
    adapters on one base model — see `app/core/sagemaker.py`.
    """

    bedrock = "bedrock"
    sagemaker = "sagemaker"


class Settings(BaseSettings):
    # Reads real environment variables first, then backend/.env (relative to
    # the working dir uvicorn runs from). Extra keys in .env are ignored, so it
    # can hold non-ALIBI vars too. In Lambda there is no .env — config comes
    # from real env vars — and a missing file here is simply skipped.
    #
    # `ALIBI_ENV_FILE` moves or disables that file. The test suite sets it to a
    # path that does not exist, which is the only reliable way to keep a
    # developer's real `.env` out of a test run: `tests/conftest.py` can pin the
    # handful of settings it knows about, but it cannot pin the ones added
    # later — and the ones added later are exactly the dangerous ones. With
    # `ALIBI_TEXT2SQL_MODEL_ID` or `ALIBI_ORCHESTRATOR_MODEL_ID` in a local `.env`
    # and AWS credentials on the machine, `pytest` would otherwise make real,
    # billed model calls on every run.
    model_config = SettingsConfigDict(
        env_prefix="ALIBI_",
        env_file=os.getenv("ALIBI_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ALIBI API"
    environment: str = "dev"
    # Comma-separated list of allowed CORS origins, e.g.
    # "https://d123.cloudfront.net,http://localhost:5173"
    allowed_origins: str = "http://localhost:5173"
    # Postgres DSN, e.g.
    # "host=127.0.0.1 port=5433 dbname=alibi user=alibi_admin password=... sslmode=require"
    # When unset, the domain services use the in-memory fixtures instead of the
    # database — so tests and a no-database local run keep working unchanged.
    # For local dev it points at the SSM tunnel on 127.0.0.1:5433 (use the IPv4
    # literal, not localhost); the deployed
    # Lambda will point at the RDS endpoint once it's VPC-attached.
    database_url: str | None = None
    # Local-dev-only convenience: run `alembic upgrade head` on app startup
    # (only when database_url is also set — no DB, nothing to migrate).
    # Defaults off everywhere, including the deployed Lambda (which migrates
    # via an explicit invoke from backend-deploy.yml instead — see
    # app/main.py — never on cold start, to avoid concurrent Lambda
    # invocations racing to run DDL against the shared RDS instance).
    auto_migrate: bool = False

    # ------------------------------------------------------------------
    # Module B — Zero-ETL ingestion
    #
    # Every default here is the LOCAL one: files on disk, jobs on a thread,
    # no model calls. A developer with no AWS access at all can run the full
    # upload → parse → confirm → query flow. Flipping to the deployed
    # topology is a matter of env vars only — see backend/.env.example.
    # ------------------------------------------------------------------

    # Data plane. `local` writes Parquet under `ingest_local_dir`; `s3` writes
    # to `ingest_bucket`. DuckDB reads either transparently.
    object_store: ObjectStoreKind = ObjectStoreKind.local
    ingest_local_dir: str = ".data"
    ingest_bucket: str | None = None
    aws_region: str = "us-east-1"

    # Execution. `thread` runs both stages in-process (uvicorn dev); `lambda`
    # is the deployed S3→SQS→processor→committer chain.
    #
    # Two function names because the chain has two hops, and each is invoked by
    # a different party: the API re-invokes the PROCESSOR when a user retries a
    # failed upload, and the processor invokes the COMMITTER when its stage-1
    # work is done. (In the normal path the API invokes nothing at all — the S3
    # ObjectCreated event starts the processor.)
    job_runner: JobRunnerKind = JobRunnerKind.thread
    processor_function_name: str | None = None
    committer_function_name: str | None = None

    # Semantic inference. Left at `none`, no model is ever called — which is
    # also what keeps the test suite offline and free.
    #
    # On AWS Bedrock (ALIBI is AWS-only), called
    # with the processor Lambda's own IAM role: no key, no endpoint URL. The
    # stage-1 processor runs outside the VPC, so it reaches Bedrock directly.
    schema_inference_provider: SchemaInferenceProvider = SchemaInferenceProvider.none
    # A Bedrock model or cross-region inference-profile id, e.g.
    # "us.anthropic.claude-haiku-4-5-20251001-v1:0". No default in code on
    # purpose: a default would let any machine with AWS credentials make billed
    # calls. Terraform sets it in the deployed topology.
    schema_inference_model_id: str | None = None
    # Titan Text Embeddings V2 emits 256/512/1024 natively, which is what keeps
    # the pgvector column at `embedding_dimensions` without a migration.
    embedding_model_id: str | None = None
    # Seconds. A schema-inference call is small; a slow one should fail fast and
    # fall back to the deterministic names rather than hold up the upload.
    schema_inference_timeout_seconds: float = 30.0
    # Must match the pgvector column width declared in the 0003_module_b
    # migration; changing it is a migration, not a config edit.
    embedding_dimensions: int = 512

    # ---- Answer runs (app/services/runs.py) ----
    # Where a run executes. `thread` (default) runs it in-process — local dev and
    # tests; `lambda` async-invokes the run-worker Lambda (`app/run_worker.py`),
    # which has 15 minutes where API Gateway allows 29 seconds.
    run_runner: JobRunnerKind = JobRunnerKind.thread
    run_worker_function_name: str | None = None
    # A run still `queued`/`running` this long after its last update is reported
    # as failed: the worker that owned it was killed (Lambda timeout) and nothing
    # else will ever finish it. A little above the worker Lambda's 900 s timeout.
    run_stale_after_seconds: int = 960

    # Limits, applied before a presigned upload URL is handed out. The per-plan
    # file size (`plans.limits.maxFileMb`: Free 5, Pro 100) is the one a user
    # meets; this is the platform's ceiling above every plan.
    max_upload_mb: int = 100
    max_rows_per_table: int = 1_000_000
    query_row_limit: int = 5_000

    # ---- Cost metering (cost_ledger) ----
    # USD per 1M tokens, per model id: `{"<model id>": {"input": 3.0, "output": 15.0}}`
    # (as JSON in ALIBI_MODEL_PRICES_USD). Every answered question is written to
    # the ledger with the tokens its model calls actually reported, priced here.
    # No prices in code, for the same reason there are no model ids in code: a
    # model change is configuration. A model with no entry is metered at 0 and
    # logged, never guessed.
    model_prices_usd: dict[str, dict[str, float]] = {}
    # ARS per USD: the official dollar rate. Two uses — the ledger's `cost_ars`
    # and, above all, the plan prices: every price has a USD reference value
    # (`plans.*_usd`) and is shown and charged in pesos at this rate.
    #The default is the business model's
    # reference rate (BCRA REM, Q4 2026). The API Lambda has no internet egress
    # (no NAT), so the rate is configuration: when the official rate moves,
    # update ALIBI_USD_ARS_RATE and ALIBI_USD_ARS_RATE_AS_OF and every price in
    # pesos follows — no migration, no notice period (the terms say so).
    usd_ars_rate: float = 1550.0
    #: ISO date of `usd_ars_rate`, shown next to the prices.
    usd_ars_rate_as_of: str = "2026-10-01"

    # ---- Module A (Text-to-SQL) ----
    # The model that writes SQL when `text2sql_provider` is bedrock: a Bedrock
    # model or inference-profile id (with `text2sql_prompt_format =
    # generic-chat-v1`). When unset, a conversation answers 503 — there is no canned fallback.
    text2sql_model_id: str | None = None
    # Set only in the deployed VPC topology. The API Lambda is in the VPC to reach
    # RDS, and with no NAT gateway or VPC endpoint it has no route to Bedrock; the
    # bridge is a small Lambda outside the VPC that makes the model call for it
    # (see `app/llm_bridge.py`). When set, `text2sql.client` and
    # `orchestrator.client` invoke this function instead of calling Bedrock
    # themselves. Unset (local dev, tests, CI), both call Bedrock directly.
    llm_bridge_function_name: str | None = None
    # Set only in the deployed topology: the Lambda that starts an RDS instance
    # which stopped itself while idle (terraform/modules/rds-autostop). A failed
    # connection invokes it and answers 503 `db_waking`; unset, a failed
    # connection is a plain 503 `db_unavailable`. See `app/db/waker.py`.
    db_waker_function_name: str | None = None
    # Outgoing mail for the 6-digit verification code (`app/core/mailer.py`):
    # plain SMTP from the project's own mailbox — no mail service, no AWS. With
    # Gmail that is smtp.gmail.com:587 + an *app password* (the account needs
    # 2-Step Verification on). Unset password = dev mode: the code is written to
    # the log instead of sent, so local runs and tests need no mailbox.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    #: The From address; defaults to `smtp_user` (Gmail rewrites it to that anyway).
    mail_from: str | None = None
    # "Continuar con Google" (`app/core/google_identity.py`): the OAuth client id
    # from the Google Cloud console — the `aud` every ID token must carry. Not a
    # secret (the browser holds the same value as VITE_GOOGLE_CLIENT_ID). Unset =
    # no Google sign-in: the endpoint answers 503 `google_unavailable` and the
    # email-and-password path is unaffected.
    google_client_id: str | None = None
    # Mercado Pago Checkout Pro (`app/core/mercadopago.py`). The access token of a
    # seller application: a TEST one (credenciales de prueba) is the sandbox, where
    # no money moves; the production one charges for real. A secret. Unset = paying
    # for a plan is unavailable (503 `payments_unavailable`); Free is unaffected.
    mp_access_token: str | None = None
    # A stopped RDS instance doesn't refuse connections, it lets them hang —
    # without a bound the request would sit until the Lambda is killed at 29s
    # and the browser would get an opaque 502 instead of `db_waking`.
    db_connect_timeout_seconds: int = 5
    # Timeout for the single model call a `prepare` makes. Must stay under the
    # Lambda's own timeout (`lambda_timeout_seconds`, 29s — API Gateway HTTP
    # API's hard request ceiling): a larger value is unreachable, because the
    # function is killed first and the caller gets an opaque 502 instead of our
    # 503. 20s leaves ~9s of headroom for request handling and the DB reads that
    # load the dataset schema. Raise only alongside the Lambda/API Gateway limits.
    text2sql_timeout_seconds: float = 20.0
    # Which prompt shape to send — MUST match the model pointed at above. See
    # app/services/text2sql/formats.py; changing the model means changing this,
    # and nothing else.
    #
    #   sql-create-context-v1  the first qwen3 fine-tune's prompt shape (trained
    #                          on Azure before 2026-10-05, retired with it). Reads
    #                          the model's SQL as MySQL, because that fine-tune
    #                          writes double-quoted string literals.
    #   generic-chat-v1        any general chat model used as Module A —
    #                          gpt-4o-mini, gpt-5.4-mini, Claude, a local Llama.
    #                          Reads the SQL as DuckDB.
    #
    # Pointing a general chat model at `sql-create-context-v1` is silently
    # wrong rather than broken: its MySQL reading rewrites `SELECT "Total
    # Revenue"` into `SELECT 'Total Revenue'`, a constant string that executes
    # cleanly and returns the same text on every row. Set this when you set
    # `text2sql_model_id`.
    #
    #   qwen-bird-spider-v1    the fine-tuned Qwen3.8-27B SQL adapter (job
    #                          model-a-dev-qlora-001), served on SageMaker. Only
    #                          valid with `text2sql_provider = sagemaker`.
    text2sql_prompt_format: str = "sql-create-context-v1"
    # `bedrock` calls `text2sql_model_id` with the format's chat messages;
    # `sagemaker` sends the schema and question to the fine-tuned adapter on
    # `sagemaker_endpoint_name`, which renders its own training prompt.
    text2sql_provider: ModelProvider = ModelProvider.bedrock

    # ---- Fine-tuned models (app/core/sagemaker.py) ----
    # ONE real-time endpoint serves both adapters (task `sql` = Module A, `chart`
    # = Module C) on one base model. No default, same reason as the model ids:
    # a default would let any machine with AWS credentials make billed calls.
    sagemaker_endpoint_name: str | None = None
    # The endpoint scales to zero, which SageMaker supports only for endpoints
    # that host inference components — so every invocation names this one, and
    # waking the model means setting its copy count back to 1.
    sagemaker_inference_component: str | None = None
    # SageMaker cuts a real-time invocation off at 60 s whatever this says; the
    # slack is for the bridge hop. The calls run in the answer-run worker
    # (15 minutes), never inside a 29 s API Gateway request.
    sagemaker_timeout_seconds: float = 65.0
    # How long a run waits for an asleep model to come back before it gives up
    # with 503 `model_waking`. The worker Lambda has 900 s and still has to
    # answer afterwards.
    model_wake_wait_seconds: int = 600

    # ---- Orchestrator (app/orchestrator) ----
    # The general chat model the orchestration layer uses to *write* — today the
    # `explain_result` node, which turns an executed result into a business-language
    # sentence. Deliberately a different model from Module A's: that one is
    # fine-tuned on SQL and is not the thing you want composing
    # English prose.
    #
    # A Bedrock model or inference-profile id, called through
    # `app/core/bedrock.py`. No default in code, for the same reason as
    # `text2sql_model_id`.
    #
    # There is NO fallback if this is unset. A conversation needs a model to
    # answer, so an unconfigured or unreachable one is a 503, not a canned
    # sentence — see `app/services/conversations.py`.
    orchestrator_model_id: str | None = None
    # Shorter than text2sql's 20s: this call happens during `execute`, *after* the
    # query has already run, so its budget is whatever is left of the API
    # Gateway's 29s ceiling. A narrative that hasn't arrived in 15s is one the
    # user is better off not waiting for — the template is already correct.
    orchestrator_timeout_seconds: float = 15.0
    # Not 0.0, unlike SQL generation. Deterministic prose repeats the same three
    # sentence skeletons across every result; the grounding validator, not the
    # sampler, is what keeps the figures honest here.
    orchestrator_temperature: float = 0.2

    @property
    def orchestrator_configured(self) -> bool:
        """True only when a narrative call has a model to go to."""
        return bool(self.orchestrator_model_id)

    # How many result rows reach the narrative prompt. Rule 6 of the spec forbids
    # enumerating rows anyway, so a large sample buys nothing but tokens — the
    # model needs enough to see the leaders and the spread, not the whole table.
    narrative_max_prompt_rows: int = 30
    # 1–3 sentences plus the key-figure list. A cap this low is also a guardrail:
    # a model that ignores the length rule gets cut off rather than billed for a
    # paragraph the validator will reject.
    narrative_max_tokens: int = 400
    # Below this self-reported score the Reflection layer logs the narrative as
    # weak. It is not a discard threshold: grounding is verified on every answer
    # regardless, so a low score on a verified sentence is information, not a veto.
    narrative_confidence_threshold: float = 0.6

    # ---- Module C (app/orchestrator/nodes/visualize.py) ----
    # Module C runs on the same model as the narrative — one general chat
    # model, prompted two different ways. It gets its own budgets because the two
    # calls are not alike: a chart needs to *see* more of the table than a
    # sentence does (cardinality and spread are the whole basis of the choice)
    # and produces a longer, more structured answer.
    #
    # No separate model id, deliberately: Module C is prompted here until its
    # trained adapter (ML/, served next to Module A's on SageMaker) is wired in,
    # and that gets its own route then, exactly as Module A has one.
    visualization_max_prompt_rows: int = 40
    # A Vega-Lite spec is 100–300 tokens; the ceiling leaves room for titles and
    # a transform without letting a runaway generation bill for a config block
    # the validator would strip anyway.
    visualization_max_tokens: int = 900
    # Tighter than the narrative's 15s and for a different reason: `execute`
    # already spent its budget running the query, and the chart is the optional
    # half of the answer. If it has not arrived by now the user gets the sentence
    # and the table, which is a complete answer — waiting longer trades something
    # they need for something they might like.
    visualization_timeout_seconds: float = 12.0
    # Off switches the node out entirely (no model call, `chart: null`). For
    # cost control and for reproducing the pre-Module-C behaviour without
    # unsetting the credentials the narrative also needs.
    visualization_enabled: bool = True
    # `bedrock`: the prompted path above. `sagemaker`: the fine-tuned nvBench
    # adapter on `sagemaker_endpoint_name`, one attempt (it was never trained on
    # a critique turn, and it decodes greedily, so a retry would repeat itself).
    visualization_provider: ModelProvider = ModelProvider.bedrock

    # ---- The agent harness (app/orchestrator/harness.py) ----
    # Two collaborating roles: a small fast
    # *planner* drives the loop — which questions to ask Module A, which results
    # to look at more closely, when to stop — and a bigger *synthesiser* writes
    # the final answer across every step's result. Bedrock model or
    # inference-profile ids, no defaults in code (same reason as above); Terraform
    # supplies Claude Haiku 4.5 (planner, Quick synthesis) / Sonnet 4.6 (Deep).
    #
    # Unset planner = a natural-language question is a 503 `planner_unavailable`,
    # exactly as an unset Module A is a 503. There is no fallback to the
    # one-query pipeline: it would be a second, quieter product behind the same
    # button.
    planner_model_id: str | None = None
    synthesis_quick_model_id: str | None = None
    synthesis_deep_model_id: str | None = None
    # Per call. The planner's turns are short; a synthesis by the Deep model over
    # several results is one long call, and the worker has 15 minutes, not 29 s.
    planner_timeout_seconds: float = 30.0
    synthesis_timeout_seconds: float = 120.0
    planner_max_tokens: int = 600
    synthesis_max_tokens: int = 1_200

    # Hard caps per mode. `max_sql` counts Module A calls the planner asked for
    # (the one automatic retry of a failed query is not counted); `max_turns`
    # counts planner calls of any kind; `max_tokens` is every model token the run
    # spends (planner + Module A), checked before each turn. Starting values from
    # the approved plan, to be tuned against the evaluation set.
    harness_quick_max_sql: int = 2
    harness_quick_max_turns: int = 4
    harness_quick_max_tokens: int = 40_000
    harness_deep_max_sql: int = 8
    harness_deep_max_turns: int = 18
    harness_deep_max_tokens: int = 160_000
    # What one observation (a digest, a peek, a describe) may cost the planner,
    # in approximate tokens (4 characters each). A bigger view is cut and says so.
    harness_observation_tokens: int = 1_500
    # Rows of each finding the synthesiser sees, beside every step's digest.
    harness_synthesis_rows: int = 30

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def schema_inference_configured(self) -> bool:
        """True when semantic inference has a chat model to call.

        Checked before use so a half-filled `.env` degrades to the
        deterministic path with a log line, instead of failing every upload.
        Embeddings are independently optional (`embedding_model_id`).
        """
        return bool(self.schema_inference_model_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
