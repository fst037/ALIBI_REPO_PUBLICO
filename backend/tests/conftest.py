"""Test configuration.

The suite must run identically on every machine: in-memory fixture backends, a
throwaway object store, and **no network calls of any kind**. The tests assert
on fixture data and the permissive fixture auth, so anything a developer has
configured locally has to be kept out.

Two mechanisms, in order:

1. **The `.env` file is switched off** (`ALIBI_ENV_FILE` → a path that does not
   exist; see `app/core/config.py`). This is the one that actually holds. An
   allow-list of individually-pinned variables cannot: it only covers the
   settings that existed when it was written, and every new setting is opted
   *in* to leaking by default. That is not hypothetical — `ALIBI_JOB_RUNNER`
   was never pinned, so a developer with the deployed ingestion topology in
   their `.env` failed 450 tests on `boto3` before this. The same gap now
   covers `ALIBI_TEXT2SQL_MODEL_ID` and `ALIBI_ORCHESTRATOR_MODEL_ID`, where the
   cost of a leak (with AWS credentials on the machine) is not a failing test
   but real, billed Bedrock calls on every run.

2. **Stray `ALIBI_*` process variables are cleared**, then the handful the
   suite genuinely needs are set. Step 1 does nothing about a variable exported
   in the shell, and a real environment variable outranks the file anyway.

The object store points at a temp directory: Module B's tests genuinely write
Parquet and read it back through DuckDB — that is the point of them — and they
must not leave files in the repo to do it.

All of this runs before the app or its settings are imported.
"""

import os
import tempfile

# 1. Ignore backend/.env entirely. The name only has to not exist.
os.environ["ALIBI_ENV_FILE"] = os.path.join(
    tempfile.gettempdir(), "alibi-tests-use-no-env-file"
)

# 2. Drop anything exported in the shell, then set what the suite needs.
for _leaked in [k for k in os.environ if k.startswith("ALIBI_")]:
    if _leaked != "ALIBI_ENV_FILE":
        del os.environ[_leaked]

os.environ["ALIBI_DATABASE_URL"] = ""
os.environ["ALIBI_OBJECT_STORE"] = "local"
os.environ["ALIBI_INGEST_LOCAL_DIR"] = tempfile.mkdtemp(prefix="alibi-test-store-")
os.environ["ALIBI_SCHEMA_INFERENCE_PROVIDER"] = "none"
# Ingestion runs in-process. `lambda` would need boto3 and a deployed function.
os.environ["ALIBI_JOB_RUNNER"] = "thread"
# Answer runs too: in-process, so a test can start one and wait for it.
os.environ["ALIBI_RUN_RUNNER"] = "thread"
# Belt and braces over step 1: the three config groups that cost money if they
# ever reach a test run. Every one of these makes its owner fall back to a
# deterministic offline path — which is the path the assertions are written for.
# Model ids are the whole configuration now (Bedrock, IAM auth — no keys), so an
# empty model id is what keeps a machine with AWS credentials from billing.
os.environ["ALIBI_TEXT2SQL_MODEL_ID"] = ""
os.environ["ALIBI_ORCHESTRATOR_MODEL_ID"] = ""
os.environ["ALIBI_SCHEMA_INFERENCE_MODEL_ID"] = ""
os.environ["ALIBI_EMBEDDING_MODEL_ID"] = ""
os.environ["ALIBI_SAGEMAKER_ENDPOINT_NAME"] = ""

import pytest  # noqa: E402  (must come after the env vars are set)
from fastapi.testclient import TestClient  # noqa: E402

from app.core import mailer  # noqa: E402
from app.ingest.jobs import get_job_runner  # noqa: E402
from app.main import app  # noqa: E402
from app.services import (  # noqa: E402
    auth_memory,
    billing_memory,
    dashboards_memory,
    datasets_memory,
    ingest_memory,
    notifications_memory,
    runs_memory,
)
from app.services.run_jobs import get_run_runner  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    """Fixture state is module-level and mutable, so it is reset per test.

    Ingestion jobs run on a background thread; joining them first means a job
    from a finished test can't write into the next one's state.
    """
    yield
    get_job_runner().wait_all(timeout=10)
    get_run_runner().wait_all(timeout=10)
    runs_memory.reset()
    ingest_memory.reset()
    datasets_memory.reset()
    dashboards_memory.reset()
    billing_memory.reset()
    notifications_memory.reset()
    auth_memory.reset()


@pytest.fixture(autouse=True)
def outbox(monkeypatch) -> dict[str, str]:
    """Catch every verification code instead of emailing it: email -> last code.

    Autouse for the same reason as `_no_provider_calls`: no test may open an
    SMTP connection, whether or not it remembered to ask for this.
    """
    sent: dict[str, str] = {}

    def _send(to: str, code: str, _minutes: int) -> None:
        sent[to] = code

    monkeypatch.setattr(mailer, "send_code", _send)
    return sent


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """A bearer-token header for an authenticated request.

    Uses the permissive fixture auth (any credentials log straight in), so
    protected endpoints can be exercised without a real user.
    """
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"email": "ana@acme.com", "password": "pw"})
    return {"Authorization": f"Bearer {login.json()['accessToken']}"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_provider_calls(monkeypatch):
    """Substitute the transport for both models, for every test.

    ALIBI has no offline mode. There is no template narrative and no canned
    result — an answer is a real query narrated by a real model, or it is a 503
    — so a test cannot get a deterministic answer by letting the product
    degrade. It has to supply the model instead.

    Autouse rather than opt-in, and deliberately so: an opt-in stub protects the
    tests that remember to ask for it, while the ones that forget make a real
    billed call and pass. This makes reaching a provider from the suite
    impossible rather than discouraged. `conftest`'s env blanking is the belt;
    this is the braces.

    Tests that care about narrative *content* override this by patching
    `explain_result.client.complete` themselves — a later monkeypatch on the same
    attribute simply wins.

    **Two nodes share this transport**, so the stub dispatches on the system
    prompt: `explain_result` and `visualize` both call `client.complete`, and a
    narrative-shaped reply handed to Module C would be rejected, retried and
    rejected again — two wasted round trips per execute and a misleading
    "chart attempt rejected" in every test log. Module C's default answer is
    `chart_type: "none"`, which is a *correct* Module C answer (prompt rule 7),
    so the baseline behaviour of the suite is an answer with no chart. Tests
    that want a chart supply one by patching `visualize.client.complete`.
    """
    import json as _json

    from app.orchestrator import client as orchestrator_client

    def _reply(config, messages, **kwargs):
        system = messages[0].get("content", "") if messages else ""
        if "You are the planner of ALIBI" in system:
            return orchestrator_client.Completion(
                text=_json.dumps(default_planner_action(messages)),
                model="stub",
                prompt_tokens=0,
                completion_tokens=0,
            )
        if "## What you are given" in system:
            return orchestrator_client.Completion(
                text=_json.dumps(default_synthesis(messages)),
                model="stub",
                prompt_tokens=0,
                completion_tokens=0,
            )
        if "Vega-Lite" in system:
            return orchestrator_client.Completion(
                text=_json.dumps({"chart_type": "none", "spec": None, "reason": "stub"}),
                model="stub",
                prompt_tokens=0,
                completion_tokens=0,
            )
        # Grounded by construction: it cites nothing, so `validation.review`
        # passes it whatever the rows are. A stub that invented a figure would
        # fail its own product's checks.
        return orchestrator_client.Completion(
            text=_json.dumps(
                {
                    "narrative": "Here is what the information shows.",
                    "key_figures": [],
                    "confidence_score": 0.9,
                }
            ),
            model="stub",
            prompt_tokens=0,
            completion_tokens=0,
        )

    monkeypatch.setattr(orchestrator_client, "complete", _reply)

    # The fine-tuned models' endpoint: no stub answer, just no way through. A
    # test that wants the SageMaker path patches `sagemaker.invoke` (or the
    # clients below, for the transport's own tests) itself.
    from app.core import sagemaker

    def _no_sagemaker(*_a, **_k):
        raise AssertionError("a test reached the real SageMaker client")

    monkeypatch.setattr(sagemaker, "_runtime", _no_sagemaker)
    monkeypatch.setattr(sagemaker, "_control", _no_sagemaker)
    for name in ("config_from_settings", "planner_config", "synthesis_config"):
        monkeypatch.setattr(
            orchestrator_client,
            name,
            lambda *_a, **_k: orchestrator_client.LlmConfig(model_id="stub"),
        )


def default_planner_action(messages: list[dict]) -> dict:
    """The suite's planner: ask the user's question once, then finish.

    The shape of the simplest real investigation — one insight query — so every
    test that asks a question in plain language goes through the whole harness
    (planner, Module A, DuckDB, synthesis, Module C) without scripting it.
    Tests about the loop itself script their own planner.
    """
    import re as _re

    if any(m["role"] == "assistant" for m in messages):
        return {"thought": "stub", "action": "finish", "findings": ["r1"], "unreached": None}
    asked = _re.search(r"The user asked: (.*)", messages[1]["content"])
    return {
        "thought": "stub",
        "action": "ask_sql",
        "question": asked.group(1) if asked else "everything",
        "purpose": "insight",
        "label": "Consultando los datos",
    }


def default_synthesis(messages: list[dict]) -> dict:
    """A synthesis that cites no figure, with a sentence for every finding shown.

    Grounded by construction, like the narrative stub: it passes the validator
    whatever the rows are.
    """
    import re as _re

    block = messages[1]["content"].split("BACKGROUND:")[0]
    handles = _re.findall(r"^(r\d+) — about:", block, _re.MULTILINE)
    return {
        "summary": "Here is what the information shows.",
        "findings": [{"result": h, "text": "This part shows another side of it."} for h in handles]
        if len(handles) > 1
        else [],
        "key_figures": [],
        "confidence_score": 0.9,
    }


@pytest.fixture
def folder_of(client: TestClient):
    """Open a folder holding the given datasets — the only way to start a chat.

    A conversation runs over a folder and nothing else, so almost every chat
    test needs one. Returns the folder id.
    """

    def _make(headers: dict, dataset_ids: list[str], name: str = "Working set") -> str:
        created = client.post(
            "/api/v1/folders",
            json={"name": name, "datasetIds": dataset_ids},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        return created.json()["id"]

    return _make


@pytest.fixture
def conversation_over(client: TestClient, folder_of):
    """A conversation over a folder containing `dataset_ids`. Returns its id."""

    def _make(headers: dict, dataset_ids: list[str], name: str = "Working set") -> str:
        folder_id = folder_of(headers, dataset_ids, name)
        created = client.post(
            "/api/v1/conversations", json={"folderId": folder_id}, headers=headers
        )
        assert created.status_code == 201, created.text
        return created.json()["id"]

    return _make
