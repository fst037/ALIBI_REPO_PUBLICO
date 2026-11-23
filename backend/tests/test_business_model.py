"""The business model, enforced.

Pinned here:

* **Free stops at its limits** — 20 questions a month, 3 datasets, 5 MB files,
  1.000 rows per query, 7 days of History — each with a 402/413 that says why.
* **Pro's overage is opt-in and capped.** Past 250 questions a Pro user is
  stopped unless they turned overage on; with it on, each extra question costs
  the plan's price and the cap they set is never exceeded.
* **An answered question is one ledger row**, metered from the tokens its model
  calls reported. A refusal costs nothing.
* **The user is told** at 80 % of the quota and when it runs out.

The in-memory store is single-user and seeded on Pro; tests that need Free
switch to it. Counts are reached by writing ledger rows directly, not by
answering a thousand questions.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import usage_meter
from app.core.config import get_settings
from app.services import billing_memory, conversations_memory
from app.services.run_jobs import get_run_runner
from tests.test_chat_execution import _dataset


def _switch_to(client: TestClient, headers: dict, plan_id: str) -> None:
    res = client.post("/api/v1/subscription/upgrade", headers=headers, json={"planId": plan_id, "cycle": "monthly"})
    assert res.status_code == 200, res.text


def _spend(n: int) -> None:
    """Count `n` answered questions this month."""
    for _ in range(n):
        billing_memory.record_query("u", rows_returned=1, cost_usd=0.0, cost_ars=0.0, source_name=None)


def _ask(client: TestClient, headers: dict, conversation_id: str, table: str):  # noqa: ANN202
    return client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"sql": f"SELECT ciudad, monto FROM {table}"},
        headers=headers,
    )


def _queries_used(client: TestClient, headers: dict) -> int:
    return client.get("/api/v1/subscription", headers=headers).json()["usage"]["queries"]["used"]


@pytest.fixture
def ready(client: TestClient, auth_headers: dict, conversation_over):  # noqa: ANN201
    """A conversation over one real CSV, created while on Pro (Free's 3-dataset
    limit would refuse the upload against the five seeded datasets)."""
    dataset = _dataset(client, auth_headers)
    return conversation_over(auth_headers, [dataset["id"]]), dataset["id"]


class TestQueries:
    def test_an_answer_is_one_ledger_row_and_a_refusal_is_none(
        self, client: TestClient, auth_headers: dict, ready
    ) -> None:
        conversation_id, table = ready
        before = _queries_used(client, auth_headers)

        assert _ask(client, auth_headers, conversation_id, table).status_code == 202
        get_run_runner().wait_all(timeout=10)
        assert _queries_used(client, auth_headers) == before + 1

        guard = client.post(
            f"/api/v1/conversations/{conversation_id}/runs",
            json={"question": "borrá todas las ventas"},
            headers=auth_headers,
        )
        assert guard.status_code == 202
        get_run_runner().wait_all(timeout=10)
        assert _queries_used(client, auth_headers) == before + 1

    def test_free_stops_at_20(self, client: TestClient, auth_headers: dict, ready) -> None:
        conversation_id, table = ready
        _switch_to(client, auth_headers, "free")
        _spend(20)
        res = _ask(client, auth_headers, conversation_id, table)
        assert res.status_code == 402
        assert res.json()["error"]["code"] == "plan_limit_reached"
        assert "Pro" in res.json()["error"]["message"]

    def test_free_has_no_overage(self, client: TestClient, auth_headers: dict) -> None:
        _switch_to(client, auth_headers, "free")
        res = client.put("/api/v1/billing/overage", headers=auth_headers, json={"enabled": True, "capArs": 5000})
        assert res.status_code == 402
        assert client.get("/api/v1/subscription", headers=auth_headers).json()["overage"]["available"] is False

    def test_pro_stops_at_250_unless_overage_is_on(self, client: TestClient, auth_headers: dict, ready) -> None:
        conversation_id, table = ready
        _spend(250)
        res = _ask(client, auth_headers, conversation_id, table)
        assert res.status_code == 402
        assert res.json()["error"]["code"] == "plan_limit_reached"

        on = client.put("/api/v1/billing/overage", headers=auth_headers, json={"enabled": True, "capArs": None})
        assert on.status_code == 200 and on.json()["overage"]["enabled"] is True
        assert _ask(client, auth_headers, conversation_id, table).status_code == 202
        get_run_runner().wait_all(timeout=10)

        overage = client.get("/api/v1/subscription", headers=auth_headers).json()["overage"]
        assert overage["queries"] == 1
        assert overage["amountArs"] == 124  # US$ 0,08 at $ 1.550

    def test_the_overage_cap_is_never_exceeded(self, client: TestClient, auth_headers: dict, ready) -> None:
        conversation_id, table = ready
        # $ 300 of cap at $ 124 each: two extra questions fit, the third does not.
        client.put("/api/v1/billing/overage", headers=auth_headers, json={"enabled": True, "capArs": 300})
        _spend(252)
        res = _ask(client, auth_headers, conversation_id, table)
        assert res.status_code == 402
        assert res.json()["error"]["code"] == "overage_cap_reached"

    def test_a_cap_must_be_positive(self, client: TestClient, auth_headers: dict) -> None:
        res = client.put("/api/v1/billing/overage", headers=auth_headers, json={"enabled": True, "capArs": 0})
        assert res.status_code == 422


class TestNotifications:
    def _titles(self, client: TestClient, headers: dict) -> list[str]:
        return [n["title"] for n in client.get("/api/v1/notifications", headers=headers).json()]

    def test_warned_at_80_percent_once(self, client: TestClient, auth_headers: dict, ready) -> None:
        conversation_id, table = ready
        title = "Estás cerca del límite de tu plan"
        before = self._titles(client, auth_headers).count(title)  # the seed has one
        _switch_to(client, auth_headers, "free")
        _spend(15)
        _ask(client, auth_headers, conversation_id, table)  # the 16th = 80 % of 20
        get_run_runner().wait_all(timeout=10)
        _ask(client, auth_headers, conversation_id, table)
        get_run_runner().wait_all(timeout=10)
        assert self._titles(client, auth_headers).count(title) == before + 1

    def test_told_when_the_quota_runs_out(self, client: TestClient, auth_headers: dict, ready) -> None:
        conversation_id, table = ready
        _switch_to(client, auth_headers, "free")
        _spend(19)
        _ask(client, auth_headers, conversation_id, table)
        get_run_runner().wait_all(timeout=10)
        assert "Llegaste al límite de tu plan" in self._titles(client, auth_headers)


class TestUploadsAndRows:
    def _upload(self, client: TestClient, headers: dict, size_bytes: int):  # noqa: ANN202
        return client.post(
            "/api/v1/uploads",
            json={"filename": "ventas.csv", "contentType": "text/csv", "sizeBytes": size_bytes},
            headers=headers,
        )

    def test_free_files_are_5_mb(self, client: TestClient, auth_headers: dict, monkeypatch) -> None:
        _switch_to(client, auth_headers, "free")
        monkeypatch.setattr(billing_memory, "dataset_count", lambda _u: 0)
        res = self._upload(client, auth_headers, 6 * 1024 * 1024)
        assert res.status_code == 413
        assert "5 MB" in res.json()["error"]["message"]

    def test_pro_files_are_100_mb(self, client: TestClient, auth_headers: dict) -> None:
        assert self._upload(client, auth_headers, 6 * 1024 * 1024).status_code == 201
        assert self._upload(client, auth_headers, 101 * 1024 * 1024).status_code == 413

    def test_free_holds_three_datasets(self, client: TestClient, auth_headers: dict) -> None:
        _switch_to(client, auth_headers, "free")  # the seed already holds five
        res = self._upload(client, auth_headers, 1024)
        assert res.status_code == 402
        assert res.json()["error"]["code"] == "plan_limit_reached"

    def test_free_rows_per_query(self, client: TestClient, auth_headers: dict, ready, monkeypatch) -> None:
        conversation_id, table = ready
        _switch_to(client, auth_headers, "free")
        captured: dict = {}
        from app.services import query

        real = query.run_sql

        def spy(sql, tables, **kwargs):  # noqa: ANN001, ANN202
            captured["limit"] = kwargs.get("limit")
            return real(sql, tables, **kwargs)

        monkeypatch.setattr(query, "run_sql", spy)
        _ask(client, auth_headers, conversation_id, table)
        get_run_runner().wait_all(timeout=10)
        assert captured["limit"] == 1000


class TestHistory:
    def test_free_shows_seven_days(self, client: TestClient, auth_headers: dict, ready, monkeypatch) -> None:
        conversation_id, _ = ready
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        for c in conversations_memory._conversations:
            if c.id == conversation_id:
                monkeypatch.setattr(c, "createdAt", old)

        def ids() -> set[str]:
            groups = client.get("/api/v1/history", headers=auth_headers).json()
            return {item["id"] for g in groups for item in g["items"]}

        assert conversation_id in ids()  # Pro: unlimited
        _switch_to(client, auth_headers, "free")
        assert conversation_id not in ids()


class TestMetering:
    def test_cost_is_tokens_at_the_configured_price(self, monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "model_prices_usd", {"m": {"input": 3.0, "output": 15.0}})
        calls = [usage_meter.ModelCall("m", 1_000, 200), usage_meter.ModelCall("unpriced", 50, 50)]
        assert usage_meter.cost_usd(calls) == pytest.approx(0.003 + 0.003)

    def test_calls_on_worker_threads_land_on_the_same_meter(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        with usage_meter.metering() as meter, ThreadPoolExecutor(2) as pool:
            pool.submit(usage_meter.run_in_context(usage_meter.record), "m", 1, 2).result()
            pool.submit(usage_meter.run_in_context(usage_meter.record), "m", 3, 4).result()
        assert [(c.prompt_tokens, c.completion_tokens) for c in meter.calls] == [(1, 2), (3, 4)]

    def test_record_outside_a_meter_is_a_no_op(self) -> None:
        usage_meter.record("m", 1, 1)  # must not raise
