"""Paying for a plan through Mercado Pago (app/services/checkout.py).

The transport is stubbed — nothing here reaches api.mercadopago.com — so what
these tests pin down is what ALIBI does with Mercado Pago's answers: the plan
moves only for an approved payment that is the caller's own and for the amount
that was asked.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import mercadopago as mp
from app.core.config import get_settings

ORIGIN = "http://localhost:5173"


class FakeMercadoPago:
    """Stands in for `mercadopago.create_preference` / `get_payment`."""

    def __init__(self) -> None:
        self.preferences: list[dict[str, Any]] = []
        self.payments: dict[int, dict[str, Any]] = {}

    def create_preference(self, preference: dict[str, Any]) -> dict[str, Any]:
        self.preferences.append(preference)
        return {"id": "pref_1", "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=pref_1"}

    def get_payment(self, payment_id: int) -> dict[str, Any]:
        if payment_id not in self.payments:
            raise mp.MercadoPagoRejected("404: payment not found", 404)
        return self.payments[payment_id]

    def pay(self, payment_id: int, *, status: str = "approved", **overrides: Any) -> None:
        """A payment for the last preference created, as Mercado Pago would report it."""
        item = self.preferences[-1]["items"][0]
        self.payments[payment_id] = {
            "id": payment_id,
            "status": status,
            "currency_id": "ARS",
            "transaction_amount": item["unit_price"],
            "external_reference": self.preferences[-1]["external_reference"],
            "payment_method_id": "visa",
            "card": {"last_four_digits": "0604", "expiration_month": 11, "expiration_year": 2030},
            **overrides,
        }


@pytest.fixture
def mercadopago(monkeypatch: pytest.MonkeyPatch) -> FakeMercadoPago:
    fake = FakeMercadoPago()
    monkeypatch.setattr(get_settings(), "mp_access_token", "TEST-token")
    monkeypatch.setattr(mp, "create_preference", fake.create_preference)
    monkeypatch.setattr(mp, "get_payment", fake.get_payment)
    return fake


def _start(client: TestClient, headers: dict, cycle: str = "monthly"):  # noqa: ANN202
    return client.post(
        "/api/v1/billing/checkout",
        headers=headers,
        json={"planId": "pro", "cycle": cycle, "returnOrigin": ORIGIN},
    )


def _confirm(client: TestClient, headers: dict, payment_id: int):  # noqa: ANN202
    return client.post("/api/v1/billing/checkout/confirm", headers=headers, json={"paymentId": str(payment_id)})


def _go_free(client: TestClient, headers: dict) -> None:
    assert client.post("/api/v1/subscription/upgrade", headers=headers, json={"planId": "free", "cycle": "monthly"}).status_code == 200


def test_a_paid_plan_cannot_be_bought_through_the_plan_endpoint(client: TestClient, auth_headers: dict) -> None:
    _go_free(client, auth_headers)
    res = client.post("/api/v1/subscription/upgrade", headers=auth_headers, json={"planId": "pro", "cycle": "monthly"})
    assert res.status_code == 402
    assert res.json()["error"]["code"] == "payment_required"
    assert client.get("/api/v1/subscription", headers=auth_headers).json()["planId"] == "free"


def test_checkout_prices_the_plan_server_side_and_returns_the_hosted_page(
    client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago
) -> None:
    res = _start(client, auth_headers)
    assert res.status_code == 200, res.text
    assert res.json()["checkoutUrl"].startswith("https://www.mercadopago.com.ar/")

    plan = next(p for p in client.get("/api/v1/plans").json() if p["id"] == "pro")
    pref = mercadopago.preferences[0]
    assert pref["items"][0]["unit_price"] == round(plan["priceMonthly"] * 1.21)
    assert pref["back_urls"]["success"] == f"{ORIGIN}/pago/resultado"
    # http origin (local dev): Mercado Pago would refuse auto_return.
    assert "auto_return" not in pref


def test_checkout_refuses_a_return_origin_that_is_not_ours(
    client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago
) -> None:
    res = client.post(
        "/api/v1/billing/checkout",
        headers=auth_headers,
        json={"planId": "pro", "cycle": "monthly", "returnOrigin": "https://evil.example"},
    )
    assert res.status_code == 422
    assert mercadopago.preferences == []


def test_free_is_not_payable(client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago) -> None:
    res = client.post(
        "/api/v1/billing/checkout",
        headers=auth_headers,
        json={"planId": "free", "cycle": "monthly", "returnOrigin": ORIGIN},
    )
    assert res.status_code == 422


def test_checkout_without_a_token_is_a_503(client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "mp_access_token", None)
    res = _start(client, auth_headers)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "payments_unavailable"


def test_an_approved_payment_activates_the_plan_and_records_the_card(
    client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago
) -> None:
    _go_free(client, auth_headers)
    _start(client, auth_headers, cycle="annual")
    mercadopago.pay(1001)

    res = _confirm(client, auth_headers, 1001)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["planId"] == "pro"
    assert body["cycle"] == "annual"
    assert body["paymentMethod"] == {"brand": "Visa", "last4": "0604", "expiry": "11/30"}
    assert [i["id"] for i in body["invoices"]] == ["mp_1001"]
    assert body["invoices"][0]["status"] == "paid"


def test_confirming_twice_changes_nothing(client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago) -> None:
    _go_free(client, auth_headers)
    _start(client, auth_headers)
    mercadopago.pay(1002)

    first = _confirm(client, auth_headers, 1002)
    second = _confirm(client, auth_headers, 1002)
    assert second.status_code == 200
    assert second.json()["invoices"] == first.json()["invoices"]
    assert len(second.json()["invoices"]) == 1


@pytest.mark.parametrize("status", ["rejected", "cancelled"])
def test_a_rejected_payment_does_not_move_the_plan(
    client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago, status: str
) -> None:
    _go_free(client, auth_headers)
    _start(client, auth_headers)
    mercadopago.pay(1003, status=status)

    res = _confirm(client, auth_headers, 1003)
    assert res.status_code == 402
    assert res.json()["error"]["code"] == "payment_rejected"
    assert client.get("/api/v1/subscription", headers=auth_headers).json()["planId"] == "free"


def test_a_pending_payment_is_a_409_not_a_rejection(
    client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago
) -> None:
    _go_free(client, auth_headers)
    _start(client, auth_headers)
    mercadopago.pay(1004, status="in_process")

    res = _confirm(client, auth_headers, 1004)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "payment_pending"


def test_a_payment_for_another_amount_is_refused(
    client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago
) -> None:
    _go_free(client, auth_headers)
    _start(client, auth_headers)
    mercadopago.pay(1005, transaction_amount=1)

    assert _confirm(client, auth_headers, 1005).status_code == 402
    assert client.get("/api/v1/subscription", headers=auth_headers).json()["planId"] == "free"


def test_someone_elses_payment_is_not_found(client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago) -> None:
    _go_free(client, auth_headers)
    _start(client, auth_headers)
    mercadopago.pay(1006, external_reference="alibi|u_somebody_else|pro|monthly|99999")

    assert _confirm(client, auth_headers, 1006).status_code == 404
    assert client.get("/api/v1/subscription", headers=auth_headers).json()["planId"] == "free"


def test_a_payment_that_did_not_start_from_checkout_is_not_found(
    client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago
) -> None:
    mercadopago.payments[1007] = {"id": 1007, "status": "approved", "external_reference": None}
    assert _confirm(client, auth_headers, 1007).status_code == 404


def test_an_unknown_payment_id_is_not_found(client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago) -> None:
    assert _confirm(client, auth_headers, 424242).status_code == 404


def test_the_payment_id_must_be_numeric(client: TestClient, auth_headers: dict, mercadopago: FakeMercadoPago) -> None:
    res = client.post("/api/v1/billing/checkout/confirm", headers=auth_headers, json={"paymentId": "../../x"})
    assert res.status_code == 422


def test_the_bridge_carries_a_mercadopago_call_and_keeps_a_refusal_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm_bridge import bridge_handler

    calls: list[tuple[str, str, str]] = []

    def fake_call(token: str, method: str, path: str, body: Any = None) -> dict[str, Any]:
        calls.append((token, method, path))
        if path.endswith("/404"):
            raise mp.MercadoPagoRejected("404: nope", 404)
        return {"ok": 1}

    monkeypatch.setattr(mp, "_call", fake_call)

    ok = bridge_handler({"kind": "mercadopago", "op": "get_payment", "token": "T", "payload": {"id": 7}})
    assert ok == {"ok": True, "completion": {"ok": 1}}
    assert calls == [("T", "GET", "/v1/payments/7")]

    refused = bridge_handler({"kind": "mercadopago", "op": "get_payment", "token": "T", "payload": {"id": 404}})
    assert refused["ok"] is False
    assert refused["code"] == "mercadopago_rejected"
