"""A connection failure becomes 503 `db_waking` while RDS is starting from an
idle stop, and 503 `db_unavailable` otherwise — never the generic 500."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.v1.routes import health
from app.core.config import get_settings
from app.db import waker


@pytest.fixture
def unreachable_db(monkeypatch):
    def _refuse():
        raise OperationalError("SELECT 1", {}, Exception("connection timeout expired"))

    monkeypatch.setattr(health.pool, "is_configured", lambda: True)
    monkeypatch.setattr(health, "session_scope", _refuse)


@pytest.mark.parametrize("rds_status", ["stopped", "starting", "stopping"])
def test_a_sleeping_database_answers_db_waking(client: TestClient, unreachable_db, monkeypatch, rds_status) -> None:
    monkeypatch.setattr(waker, "wake", lambda: rds_status)

    response = client.get("/api/v1/health/db")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "db_waking"
    assert response.headers["Retry-After"] == str(waker.RETRY_AFTER_SECONDS)


def test_no_capacity_is_reported_as_such(client: TestClient, unreachable_db, monkeypatch) -> None:
    monkeypatch.setattr(waker, "wake", lambda: waker.NO_CAPACITY)

    response = client.get("/api/v1/health/db")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "db_no_capacity"
    assert response.headers["Retry-After"] == str(waker.RETRY_AFTER_SECONDS)


@pytest.mark.parametrize("rds_status", ["available", None])
def test_an_unreachable_database_that_is_not_asleep_is_db_unavailable(
    client: TestClient, unreachable_db, monkeypatch, rds_status
) -> None:
    monkeypatch.setattr(waker, "wake", lambda: rds_status)

    response = client.get("/api/v1/health/db")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "db_unavailable"


def test_health_db_without_a_database_is_ok(client: TestClient) -> None:
    assert client.get("/api/v1/health/db").json() == {"status": "ok"}


def test_wake_without_a_configured_waker_does_nothing() -> None:
    assert waker.wake() is None


class _FakeLambda:
    def __init__(self, reply: dict, function_error: bool = False) -> None:
        self.reply = reply
        self.function_error = function_error
        self.calls: list[dict] = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        response = {"Payload": io.BytesIO(json.dumps(self.reply).encode())}
        if self.function_error:
            response["FunctionError"] = "Unhandled"
        return response


@pytest.fixture
def configured_waker(monkeypatch):
    monkeypatch.setattr(get_settings(), "db_waker_function_name", "alibi-db-power-test")


def test_wake_returns_the_status_the_waker_reports(configured_waker, monkeypatch) -> None:
    fake = _FakeLambda({"status": "starting"})
    monkeypatch.setattr(waker, "_client", lambda: fake)

    assert waker.wake() == "starting"
    assert fake.calls[0]["FunctionName"] == "alibi-db-power-test"
    assert json.loads(fake.calls[0]["Payload"]) == {"action": "start"}


def test_a_crashed_waker_claims_nothing(configured_waker, monkeypatch) -> None:
    monkeypatch.setattr(waker, "_client", lambda: _FakeLambda({"errorMessage": "boom"}, function_error=True))

    assert waker.wake() is None


def test_a_waker_reporting_no_capacity_is_surfaced(configured_waker, monkeypatch) -> None:
    reply = {"status": "stopped", "error": "no_capacity", "message": "no room"}
    monkeypatch.setattr(waker, "_client", lambda: _FakeLambda(reply))

    assert waker.wake() == waker.NO_CAPACITY
