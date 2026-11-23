"""Paying for a plan through Mercado Pago Checkout Pro.

The flow, and where each step is decided:

1. `start` — the user picks Pro. We compute the price (the plan's, from the
   database — never the browser's), create a Mercado Pago *preference* and hand
   back its hosted-page URL. The user's id, plan, cycle and amount travel in the
   preference's `external_reference`, which Mercado Pago stores and returns.
2. The user pays on Mercado Pago (in the sandbox: a test card) and is sent to
   `<origin>/pago/resultado?payment_id=…`.
3. `confirm` — the browser gives us only that `payment_id`. We read the payment
   from Mercado Pago and move the plan **only** if it is approved, belongs to
   this user, and was for the amount we asked. A forged or someone else's id
   changes nothing.

Confirming is idempotent: the invoice is keyed by the payment id, so reloading
the return page returns the same subscription.

There is no webhook: a user who pays and closes the tab before the return page
loads is upgraded when they come back and confirm. There are no automatic
renewals — a paid cycle is a single charge.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core import mercadopago as mp
from app.core.config import get_settings
from app.core.errors import ApiException
from app.db import pool
from app.db.engine import session_scope
from app.repositories import billing as repo
from app.schemas.billing import (
    CheckoutRequest,
    CheckoutSession,
    PaymentMethod,
    Subscription,
    UpgradeRequest,
)
from app.schemas.plans import BillingCycle, PlanId
from app.services import billing, plans
from app.services import billing_memory as mem

logger = logging.getLogger("alibi.checkout")

#: Argentine VAT, added to the plan's (VAT-excluded) price — the same 21 % the
#: payment screens show.
VAT = 0.21
_REFERENCE_PREFIX = "alibi"

_UNAVAILABLE = ApiException(
    503, "payments_unavailable", "Los pagos no están disponibles en este momento. Probá de nuevo más tarde."
)
_NOT_FOUND = ApiException(404, "payment_not_found", "No encontramos ese pago.")


def _total_ars(plan_id: PlanId, cycle: BillingCycle) -> float:
    plan = plans.get_plan(plan_id)
    if plan is None or plan.id is PlanId.free:
        raise ApiException(422, "plan_not_payable", "Ese plan no se paga.")
    base = plan.priceAnnual * 12 if cycle is BillingCycle.annual else plan.priceMonthly
    return float(round(base * (1 + VAT)))


def _reference(user_id: str, plan_id: PlanId, cycle: BillingCycle, amount: float) -> str:
    return "|".join([_REFERENCE_PREFIX, user_id, plan_id.value, cycle.value, str(int(amount))])


def _parse_reference(reference: str | None) -> tuple[str, PlanId, BillingCycle, float] | None:
    parts = (reference or "").split("|")
    if len(parts) != 5 or parts[0] != _REFERENCE_PREFIX:
        return None
    try:
        return parts[1], PlanId(parts[2]), BillingCycle(parts[3]), float(int(parts[4]))
    except ValueError:
        return None


def start(user_id: str, request: CheckoutRequest) -> CheckoutSession:
    origin = request.returnOrigin.rstrip("/")
    if origin not in get_settings().allowed_origins_list:
        raise ApiException(422, "invalid_return_origin", "Origen de retorno no permitido.")
    if not get_settings().mp_access_token:
        raise _UNAVAILABLE

    amount = _total_ars(request.planId, request.cycle)
    cycle_label = "anual" if request.cycle is BillingCycle.annual else "mensual"
    back = f"{origin}/pago/resultado"
    preference: dict[str, Any] = {
        "items": [
            {
                "id": request.planId.value,
                "title": f"ALIBI {request.planId.value.capitalize()} · {cycle_label}",
                "quantity": 1,
                "currency_id": "ARS",
                "unit_price": amount,
            }
        ],
        "external_reference": _reference(user_id, request.planId, request.cycle, amount),
        "back_urls": {"success": back, "failure": back, "pending": back},
        "statement_descriptor": "ALIBI",
    }
    # Mercado Pago refuses auto_return on a non-https back_url (local dev).
    if origin.startswith("https://"):
        preference["auto_return"] = "approved"

    try:
        created = mp.create_preference(preference)
    except (mp.MercadoPagoUnavailable, mp.MercadoPagoRejected) as err:
        logger.warning("could not create the Mercado Pago preference: %s", err)
        raise _UNAVAILABLE from err
    url = created.get("init_point")
    if not url:
        logger.warning("Mercado Pago answered without an init_point: %s", created)
        raise _UNAVAILABLE
    return CheckoutSession(checkoutUrl=url, amountArs=amount)


def _card_of(payment: dict[str, Any]) -> PaymentMethod | None:
    """The card a payment was made with, when it was a card (not account money)."""
    card = payment.get("card") or {}
    last4 = card.get("last_four_digits")
    if not last4:
        return None
    month, year = card.get("expiration_month"), card.get("expiration_year")
    expiry = f"{int(month):02d}/{int(year) % 100:02d}" if month and year else "—"
    brand = str(payment.get("payment_method_id") or "card").capitalize()
    return PaymentMethod(brand=brand, last4=str(last4), expiry=expiry)


def _already_recorded(payment_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.has_payment(session, payment_id)
    return mem.has_payment(payment_id)


def _record(
    user_id: str,
    payment_id: str,
    amount: float,
    method: PaymentMethod | None,
    plan_id: PlanId,
    cycle: BillingCycle,
) -> Subscription:
    request = UpgradeRequest(planId=plan_id, cycle=cycle)
    if pool.is_configured():
        with session_scope() as session:
            repo.upgrade(session, user_id, request)
            session.flush()
            repo.record_payment(session, user_id, payment_id=payment_id, amount=amount, method=method)
            session.flush()
            return repo.get_subscription(session, user_id)
    mem.upgrade(user_id, request)
    mem.record_payment(user_id, payment_id=payment_id, amount=amount, method=method)
    return mem.get_subscription(user_id)


def confirm(user_id: str, payment_id: str) -> Subscription:
    try:
        payment = mp.get_payment(int(payment_id))
    except mp.MercadoPagoRejected as err:
        # Unknown payment id, or one that belongs to another seller's account.
        raise _NOT_FOUND from err
    except mp.MercadoPagoUnavailable as err:
        logger.warning("could not read Mercado Pago payment %s: %s", payment_id, err)
        raise _UNAVAILABLE from err

    expected = _parse_reference(payment.get("external_reference"))
    if expected is None or expected[0] != user_id:
        # Someone else's payment, or one that did not start from `start`.
        raise _NOT_FOUND
    _, plan_id, cycle, amount = expected

    if _already_recorded(payment_id):
        return billing.get_subscription(user_id)

    status = payment.get("status")
    if status in ("pending", "in_process", "authorized"):
        raise ApiException(
            409, "payment_pending", "Tu pago todavía se está procesando. Tu plan se actualiza cuando se acredite."
        )
    if status != "approved":
        raise ApiException(402, "payment_rejected", "El pago no fue aprobado. No se te cobró nada.")
    if payment.get("currency_id") != "ARS" or float(payment.get("transaction_amount") or 0) != amount:
        logger.error("payment %s amount mismatch: %s vs %s", payment_id, payment.get("transaction_amount"), amount)
        raise ApiException(402, "payment_rejected", "El importe del pago no coincide con el plan elegido.")

    return _record(user_id, payment_id, amount, _card_of(payment), plan_id, cycle)
