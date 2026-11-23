"""Mercado Pago Checkout Pro — the two calls ALIBI makes, and nothing else.

* `create_preference` — a payment order. The response's `init_point` is the
  hosted page the browser is sent to; the user pays there and Mercado Pago
  sends them back to our `back_urls`.
* `get_payment` — one payment by id. Never trusted from the browser: the return
  URL carries a `payment_id`, but whether it was paid, for how much and for whom
  is read from here (`app/services/checkout.py`).

ALIBI never sees a card. With a **test** access token (a seller application's
"credenciales de prueba") this is Mercado Pago's sandbox: test cards, no money
moves. Moving to production is swapping `ALIBI_MP_ACCESS_TOKEN` for the
production one — no code changes.

Two routes, the same split as `google_identity.py` / `mailer.py`: the API Lambda
sits in the VPC with no route to api.mercadopago.com, so in the deployed
topology the bridge Lambda (`app/llm_bridge.py`, kind `mercadopago`) makes the
call; local dev and the tests run it in-process. Stdlib `urllib` only — the
deployment zip has ~7 MB of headroom.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from typing import Any

from app.core.config import get_settings

API_URL = "https://api.mercadopago.com"
_TIMEOUT_SECONDS = 15.0


class MercadoPagoUnavailable(Exception):
    """Not configured here, or Mercado Pago could not be reached / answered 5xx."""


class MercadoPagoRejected(Exception):
    """Mercado Pago refused the request (4xx): a bad token, or an unknown payment."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def _call(token: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """One request, in this process. Used directly, and by the bridge."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if method == "POST":
        # A retried POST must not create a second preference.
        headers["X-Idempotency-Key"] = secrets.token_hex(16)
    request = urllib.request.Request(
        API_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed https host
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:300]
        if 400 <= err.code < 500:
            raise MercadoPagoRejected(f"{err.code}: {detail}", err.code) from err
        raise MercadoPagoUnavailable(f"{err.code}: {detail}") from err
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        raise MercadoPagoUnavailable(f"{type(err).__name__}: {err}") from err


def run_op(op: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The bridge's entry: `op` is `create_preference` or `get_payment`."""
    if op == "create_preference":
        return _call(token, "POST", "/checkout/preferences", payload)
    if op == "get_payment":
        return _call(token, "GET", f"/v1/payments/{int(payload['id'])}")
    raise ValueError(f"unknown mercadopago op: {op!r}")


def _dispatch(op: str, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    token = settings.mp_access_token
    if not token:
        raise MercadoPagoUnavailable("ALIBI_MP_ACCESS_TOKEN is not set")

    bridge = settings.llm_bridge_function_name
    if not bridge:
        return run_op(op, token, payload)

    from app.core.llm_bridge_transport import call

    body = call(
        bridge,
        {"kind": "mercadopago", "op": op, "token": token, "payload": payload},
        read_timeout=_TIMEOUT_SECONDS,
        unavailable_cls=MercadoPagoUnavailable,
    )
    if body.get("ok"):
        return body["completion"]
    # A refusal keeps its meaning across the bridge: 4xx is ours to report, not an outage.
    if body.get("code") == "mercadopago_rejected":
        raise MercadoPagoRejected(body.get("error") or "", int(body.get("status") or 400))
    raise MercadoPagoUnavailable(body.get("error") or "bridge returned an error with no detail")


def create_preference(preference: dict[str, Any]) -> dict[str, Any]:
    return _dispatch("create_preference", preference)


def get_payment(payment_id: int) -> dict[str, Any]:
    return _dispatch("get_payment", {"id": payment_id})


__all__ = [
    "MercadoPagoRejected",
    "MercadoPagoUnavailable",
    "create_preference",
    "get_payment",
    "run_op",
]
